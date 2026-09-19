#!/usr/bin/env python3
"""Validate a Trace a Life case and its internal references."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse


CLAIM_STATUSES = {"verified", "credible", "partial", "self_reported", "unverified", "contradicted"}
EPISTEMIC = {"fact", "self_report", "inference", "dispute", "simulation"}
IMPORTANCE = {"critical", "supporting", "context"}
TRANSCRIPT_STATUSES = {"full", "partial", "shownotes_only", "pending", "unavailable"}
INTERVIEW_ANALYSIS_STATUSES = {"pending", "complete", "blocked"}


class Validation:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def require(self, condition: bool, message: str) -> None:
        if not condition:
            self.errors.append(message)

    def warn(self, condition: bool, message: str) -> None:
        if not condition:
            self.warnings.append(message)


def ids(items: list[dict], label: str, result: Validation) -> set[str]:
    values = [item.get("id") for item in items]
    result.require(all(isinstance(value, str) and value for value in values), f"{label}: every item needs a non-empty id")
    clean = {value for value in values if isinstance(value, str) and value}
    result.require(len(clean) == len(values), f"{label}: ids must be unique")
    return clean


def valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate(data: dict, strict: bool = False) -> Validation:
    result = Validation()
    result.require(data.get("schema_version") == "trace-a-life/1", "schema_version must be trace-a-life/1")

    subject = data.get("subject", {})
    result.require(bool(subject.get("name")), "subject.name is required")
    result.require(bool(subject.get("anchor")), "subject.anchor is required")

    chapters = data.get("chapters", [])
    chapter_ids = ids(chapters, "chapters", result)
    result.require({"origins", "ascent", "turning-point", "legacy"}.issubset(chapter_ids), "chapters must include origins, ascent, turning-point, and legacy")

    sources = data.get("sources", [])
    source_ids = ids(sources, "sources", result)
    for source in sources:
        sid = source.get("id", "?")
        result.require(bool(source.get("title")), f"source {sid}: title is required")
        result.require(valid_url(source.get("url", "")), f"source {sid}: url must be http(s)")
        result.require(bool(source.get("origin_cluster")), f"source {sid}: origin_cluster is required")
        result.warn(bool(source.get("accessed_at")), f"source {sid}: accessed_at is missing")

    claims = data.get("claims", [])
    claim_ids = ids(claims, "claims", result)
    for claim in claims:
        cid = claim.get("id", "?")
        status = claim.get("status")
        result.require(status in CLAIM_STATUSES, f"claim {cid}: invalid status {status!r}")
        result.require(claim.get("importance") in IMPORTANCE, f"claim {cid}: invalid importance")
        evidence = claim.get("evidence", [])
        for item in evidence:
            result.require(item.get("source_id") in source_ids, f"claim {cid}: unknown source {item.get('source_id')}")
            if status in {"verified", "credible"}:
                result.require(bool(item.get("quote")), f"claim {cid}: verified/credible evidence needs a quote")
        if status in {"verified", "credible", "partial", "self_reported", "contradicted"}:
            result.require(bool(evidence), f"claim {cid}: status {status} requires evidence")
        if status == "credible":
            clusters = {
                next((source.get("origin_cluster") for source in sources if source.get("id") == item.get("source_id")), None)
                for item in evidence
            }
            result.warn(len({cluster for cluster in clusters if cluster}) >= 2, f"claim {cid}: credible should have two independent origin clusters")

    events = data.get("events", [])
    ids(events, "events", result)
    for event in events:
        eid = event.get("id", "?")
        result.require(event.get("chapter_id") in chapter_ids, f"event {eid}: unknown chapter_id")
        result.require(event.get("epistemic_status") in EPISTEMIC - {"simulation"}, f"event {eid}: invalid epistemic_status")
        for sid in event.get("source_ids", []):
            result.require(sid in source_ids, f"event {eid}: unknown source {sid}")
        for cid in event.get("claim_ids", []):
            result.require(cid in claim_ids, f"event {eid}: unknown claim {cid}")

    decisions = data.get("decisions", [])
    ids(decisions, "decisions", result)
    for decision in decisions:
        did = decision.get("id", "?")
        result.require(decision.get("interpretation_status") in {"fact", "inference"}, f"decision {did}: interpretation_status must be fact or inference")
        result.require(bool(decision.get("alternatives")), f"decision {did}: alternatives must not be empty")
        result.require(bool(decision.get("falsifier")), f"decision {did}: falsifier is required")
        for sid in decision.get("source_ids", []):
            result.require(sid in source_ids, f"decision {did}: unknown source {sid}")

    contexts = data.get("contexts", [])
    ids(contexts, "contexts", result)
    for context in contexts:
        for sid in context.get("source_ids", []):
            result.require(sid in source_ids, f"context {context.get('id')}: unknown source {sid}")

    relationships = data.get("relationships", {})
    nodes = relationships.get("nodes", [])
    node_ids = ids(nodes, "relationship nodes", result)
    edges = relationships.get("edges", [])
    ids(edges, "relationship edges", result)
    for edge in edges:
        eid = edge.get("id", "?")
        result.require(edge.get("source") in node_ids, f"relationship {eid}: unknown source node")
        result.require(edge.get("target") in node_ids, f"relationship {eid}: unknown target node")
        result.require(edge.get("epistemic_status") in {"fact", "inference", "dispute"}, f"relationship {eid}: invalid epistemic_status")
        for sid in edge.get("source_ids", []):
            result.require(sid in source_ids, f"relationship {eid}: unknown evidence source {sid}")

    wealth = data.get("wealth", {})
    for label in ("stages", "transfers"):
        items = wealth.get(label, [])
        ids(items, f"wealth.{label}", result)
        for item in items:
            for sid in item.get("source_ids", []):
                result.require(sid in source_ids, f"wealth {item.get('id')}: unknown source {sid}")

    oral_history = data.get("oral_history", {})
    interviews = oral_history.get("interviews", [])
    interview_ids = ids(interviews, "oral_history.interviews", result)
    for interview in interviews:
        iid = interview.get("id", "?")
        result.require(bool(interview.get("title")), f"interview {iid}: title is required")
        result.require(
            interview.get("transcript_status") in TRANSCRIPT_STATUSES,
            f"interview {iid}: invalid transcript_status",
        )
        audio_path = str(interview.get("audio_path", "")).strip()
        if audio_path:
            result.require(not audio_path.startswith(("/", "\\")), f"interview {iid}: audio_path must be case-relative")
            result.require(".." not in Path(audio_path).parts, f"interview {iid}: audio_path cannot escape the case")
            digest = str(interview.get("audio_sha256", ""))
            result.require(len(digest) == 64 and all(char in "0123456789abcdef" for char in digest), f"interview {iid}: audio_sha256 must be lowercase SHA-256")
            result.require(isinstance(interview.get("duration_seconds"), (int, float)) and interview.get("duration_seconds", 0) > 0, f"interview {iid}: downloaded audio needs duration_seconds")
        for field in ("normalized_audio_path", "extraction_receipt_path", "transcript_path"):
            local_path = str(interview.get(field, "")).strip()
            if local_path:
                result.require(not local_path.startswith(("/", "\\")), f"interview {iid}: {field} must be case-relative")
                result.require(".." not in Path(local_path).parts, f"interview {iid}: {field} cannot escape the case")
        transcript_path = str(interview.get("transcript_path", "")).strip()
        if transcript_path:
            result.require(interview.get("transcript_status") == "full", f"interview {iid}: local transcript requires full status")
            for digest_field in ("normalized_audio_sha256", "transcript_sha256"):
                digest = str(interview.get(digest_field, ""))
                result.require(len(digest) == 64 and all(char in "0123456789abcdef" for char in digest), f"interview {iid}: {digest_field} must be lowercase SHA-256")
            result.require(bool(interview.get("transcript_provider")), f"interview {iid}: transcript_provider is required")
            result.require(isinstance(interview.get("segment_count"), int) and interview.get("segment_count", 0) > 0, f"interview {iid}: segment_count must be positive")
            result.require(isinstance(interview.get("speaker_count"), int) and interview.get("speaker_count", 0) > 0, f"interview {iid}: speaker_count must be positive")
        for sid in interview.get("source_ids", []):
            result.require(sid in source_ids, f"interview {iid}: unknown source {sid}")

    disclosures = oral_history.get("disclosures", [])
    ids(disclosures, "oral_history.disclosures", result)
    for disclosure in disclosures:
        did = disclosure.get("id", "?")
        result.require(disclosure.get("interview_id") in interview_ids, f"disclosure {did}: unknown interview_id")
        result.require(disclosure.get("epistemic_status") in {"self_report", "dispute"}, f"disclosure {did}: oral disclosure must be self_report or dispute")
        result.require(bool(disclosure.get("timecode")), f"disclosure {did}: timecode is required")
        result.require(bool(disclosure.get("summary")), f"disclosure {did}: summary is required")
        for sid in disclosure.get("source_ids", []):
            result.require(sid in source_ids, f"disclosure {did}: unknown source {sid}")

    full_interviews = [
        interview for interview in interviews if interview.get("transcript_status") == "full"
    ]
    interview_analysis = oral_history.get("analysis", {})
    if interview_analysis:
        result.require(
            interview_analysis.get("schema_version") == "xray-interview-analysis/1",
            "oral_history.analysis.schema_version must be xray-interview-analysis/1",
        )
        result.require(
            interview_analysis.get("status") in INTERVIEW_ANALYSIS_STATUSES,
            "oral_history.analysis.status must be pending, complete, or blocked",
        )
        manifest_path = str(interview_analysis.get("manifest_path", "")).strip()
        if manifest_path:
            result.require(
                not manifest_path.startswith(("/", "\\")),
                "oral_history.analysis.manifest_path must be case-relative",
            )
            result.require(
                ".." not in Path(manifest_path).parts,
                "oral_history.analysis.manifest_path cannot escape the case",
            )
        if interview_analysis.get("status") == "complete":
            result.require(bool(manifest_path), "complete interview analysis requires manifest_path")
            digest = str(interview_analysis.get("manifest_sha256", ""))
            result.require(
                len(digest) == 64 and all(char in "0123456789abcdef" for char in digest),
                "complete interview analysis requires lowercase manifest_sha256",
            )
            result.require(
                bool(interview_analysis.get("completed_at")),
                "complete interview analysis requires completed_at",
            )

    analysis_is_required = bool(full_interviews) and (
        strict or data.get("fact_freeze", {}).get("status") == "frozen"
    )
    if analysis_is_required:
        result.require(
            interview_analysis.get("status") == "complete",
            "strict: full transcripts require oral_history.analysis.status=complete",
        )
    elif full_interviews:
        result.warn(
            interview_analysis.get("status") == "complete",
            "full transcripts are pending the five-gate interview analysis",
        )

    scenarios = data.get("scenarios", [])
    ids(scenarios, "scenarios", result)
    for scenario in scenarios:
        result.require(scenario.get("epistemic_status") == "simulation", f"scenario {scenario.get('id')}: epistemic_status must be simulation")
        result.require(bool(scenario.get("limitations")), f"scenario {scenario.get('id')}: limitations are required")

    freeze = data.get("fact_freeze", {})
    result.require(freeze.get("status") in {"draft", "frozen"}, "fact_freeze.status must be draft or frozen")
    if freeze.get("status") == "frozen":
        result.require(bool(freeze.get("frozen_at")), "frozen fact pack needs fact_freeze.frozen_at")

    if strict:
        result.require(bool(data.get("report", {}).get("thesis")), "strict: report.thesis is required")
        result.require(bool(events), "strict: at least one event is required")
        result.require(bool(claims), "strict: at least one claim is required")
        result.require(bool(sources), "strict: at least one source is required")
        critical = [claim for claim in claims if claim.get("importance") == "critical"]
        result.require(bool(critical), "strict: at least one critical claim is required")
        result.require(all(claim.get("status") != "unverified" for claim in critical), "strict: critical claims cannot remain unverified")

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    data = json.loads(args.case.read_text(encoding="utf-8"))
    result = validate(data, strict=args.strict)
    for warning in result.warnings:
        print(f"warning: {warning}")
    for error in result.errors:
        print(f"error: {error}")
    if result.errors:
        print(f"FAILED: {len(result.errors)} error(s), {len(result.warnings)} warning(s)")
        return 1
    print(f"OK: 0 errors, {len(result.warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
