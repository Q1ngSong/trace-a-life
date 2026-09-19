#!/usr/bin/env python3
"""Validate the five-gate interview analysis package for an x-ray case."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA = "xray-interview-analysis/1"
STAGES = (
    "event_alignment",
    "speaker_hypotheses",
    "topic_segmentation",
    "disclosure_extraction",
    "third_cross_verification",
)
FINAL_STATUSES = {
    "verified",
    "credible",
    "partial",
    "self_reported",
    "unverified",
    "contradicted",
    "excluded",
}
IDENTITY_EVIDENCE = {
    "self_identification",
    "host_identification",
    "official_roster_turn_order",
    "voice_reference",
}


@dataclass
class AnalysisValidation:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.errors

    def require(self, condition: bool, message: str) -> None:
        if not condition:
            self.errors.append(message)

    def warn(self, condition: bool, message: str) -> None:
        if not condition:
            self.warnings.append(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_case_path(case_dir: Path, value: Any, label: str, result: AnalysisValidation) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        result.errors.append(f"{label}: path is required")
        return None
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        result.errors.append(f"{label}: path must stay inside the case directory")
        return None
    return case_dir / relative


def _read_json(path: Path, label: str, result: AnalysisValidation) -> dict[str, Any] | None:
    if not path.is_file():
        result.errors.append(f"{label}: missing file {path.name}")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        result.errors.append(f"{label}: invalid JSON: {error}")
        return None
    if not isinstance(value, dict):
        result.errors.append(f"{label}: root must be an object")
        return None
    return value


def _index_by_id(items: Any, key: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        return {}
    return {
        str(item.get(key)): item
        for item in items
        if isinstance(item, dict) and item.get(key)
    }


def validate_interview_analysis(case_location: Path) -> AnalysisValidation:
    result = AnalysisValidation()
    case_path = case_location if case_location.name == "case.json" else case_location / "case.json"
    case_path = case_path.resolve()
    case_dir = case_path.parent
    case_data = _read_json(case_path, "case", result)
    if case_data is None:
        return result

    oral_history = case_data.get("oral_history", {})
    interviews = oral_history.get("interviews", []) if isinstance(oral_history, dict) else []
    full = {
        str(item.get("id")): item
        for item in interviews
        if isinstance(item, dict) and item.get("transcript_status") == "full" and item.get("id")
    }
    if not full:
        return result

    analysis = oral_history.get("analysis", {}) if isinstance(oral_history, dict) else {}
    result.require(isinstance(analysis, dict), "oral_history.analysis must be an object")
    if not isinstance(analysis, dict):
        return result
    result.require(analysis.get("schema_version") == SCHEMA, f"analysis schema must be {SCHEMA}")
    result.require(analysis.get("status") == "complete", "interview analysis status must be complete")
    manifest_path = _safe_case_path(case_dir, analysis.get("manifest_path"), "analysis manifest", result)
    if manifest_path is None:
        return result
    manifest = _read_json(manifest_path, "analysis manifest", result)
    if manifest is None:
        return result
    expected_manifest_hash = str(analysis.get("manifest_sha256", ""))
    result.require(_sha256(manifest_path) == expected_manifest_hash, "analysis manifest SHA-256 mismatch")
    result.require(manifest.get("schema_version") == SCHEMA, f"manifest schema must be {SCHEMA}")

    manifest_transcripts = _index_by_id(manifest.get("transcripts"), "interview_id")
    result.require(set(manifest_transcripts) == set(full), "manifest must include every and only full transcript")
    transcript_data: dict[str, dict[str, Any]] = {}
    for interview_id, interview in full.items():
        entry = manifest_transcripts.get(interview_id, {})
        result.require(entry.get("path") == interview.get("transcript_path"), f"{interview_id}: transcript path drift")
        result.require(entry.get("sha256") == interview.get("transcript_sha256"), f"{interview_id}: transcript hash drift")
        result.require(entry.get("segment_count") == interview.get("segment_count"), f"{interview_id}: segment count drift")
        transcript_path = _safe_case_path(case_dir, entry.get("path"), f"{interview_id} transcript", result)
        if transcript_path is None:
            continue
        transcript = _read_json(transcript_path, f"{interview_id} transcript", result)
        if transcript is None:
            continue
        result.require(_sha256(transcript_path) == str(entry.get("sha256", "")), f"{interview_id}: transcript file SHA-256 mismatch")
        segments = transcript.get("segments", [])
        result.require(isinstance(segments, list), f"{interview_id}: transcript segments must be an array")
        if isinstance(segments, list):
            result.require(len(segments) == entry.get("segment_count"), f"{interview_id}: transcript segment count mismatch")
            transcript_data[interview_id] = transcript

    artifact_entries = _index_by_id(manifest.get("artifacts"), "stage")
    result.require(set(artifact_entries) == set(STAGES), "manifest must contain exactly the five required stages")
    artifacts: dict[str, dict[str, Any]] = {}
    for stage in STAGES:
        entry = artifact_entries.get(stage, {})
        result.require(entry.get("status") == "complete", f"{stage}: status must be complete")
        artifact_path = _safe_case_path(case_dir, entry.get("path"), stage, result)
        if artifact_path is None:
            continue
        artifact = _read_json(artifact_path, stage, result)
        if artifact is None:
            continue
        result.require(_sha256(artifact_path) == str(entry.get("sha256", "")), f"{stage}: artifact SHA-256 mismatch")
        result.require(artifact.get("schema_version") == SCHEMA, f"{stage}: invalid schema")
        result.require(artifact.get("stage") == stage, f"{stage}: stage label mismatch")
        result.require(isinstance(artifact.get("input_artifacts"), list) and artifact.get("input_artifacts"), f"{stage}: input_artifacts are required")
        result.require(not artifact.get("blockers"), f"{stage}: unresolved blockers remain")
        artifacts[stage] = artifact

    speakers = artifacts.get("speaker_hypotheses", {})
    for interview in speakers.get("interviews", []) if isinstance(speakers.get("interviews"), list) else []:
        for speaker in interview.get("speakers", []) if isinstance(interview, dict) else []:
            result.require(speaker.get("status") == "hypothesis", "speaker identity status must remain hypothesis")
            if speaker.get("name_hypothesis"):
                evidence = speaker.get("evidence", [])
                strong = any(
                    isinstance(item, dict)
                    and item.get("segment_id")
                    and item.get("evidence_type") in IDENTITY_EVIDENCE
                    for item in evidence if isinstance(evidence, list)
                )
                result.require(strong, f"{interview.get('interview_id')} {speaker.get('speaker_id')}: named speaker lacks locatable identity evidence")

    topics = artifacts.get("topic_segmentation", {})
    topic_interviews = _index_by_id(topics.get("interviews"), "interview_id")
    for interview_id, transcript in transcript_data.items():
        segments = transcript.get("segments", [])
        segment_ids = [str(item.get("segment_id")) for item in segments]
        index = {segment_id: position for position, segment_id in enumerate(segment_ids)}
        chapters = topic_interviews.get(interview_id, {}).get("chapters", [])
        result.require(bool(chapters), f"{interview_id}: topic chapters are required")
        expected_start = 0
        for chapter in chapters if isinstance(chapters, list) else []:
            start_id = str(chapter.get("start_segment_id"))
            end_id = str(chapter.get("end_segment_id"))
            result.require(start_id in index and end_id in index, f"{interview_id}: chapter references unknown segment")
            if start_id not in index or end_id not in index:
                continue
            start = index[start_id]
            end = index[end_id]
            result.require(start == expected_start and end >= start, f"{interview_id}: chapters must be contiguous and ordered")
            result.require(chapter.get("start_ms") == segments[start].get("start_ms"), f"{interview_id}: chapter start_ms is not an ASR boundary")
            result.require(chapter.get("end_ms") == segments[end].get("end_ms"), f"{interview_id}: chapter end_ms is not an ASR boundary")
            expected_start = end + 1
        result.require(expected_start == len(segments), f"{interview_id}: chapters must cover every segment exactly once")

    disclosures = artifacts.get("disclosure_extraction", {})
    candidates = _index_by_id(disclosures.get("candidates"), "candidate_id")
    verifications = artifacts.get("third_cross_verification", {})
    checks = _index_by_id(verifications.get("verifications"), "candidate_id")
    result.require(set(checks) == set(candidates), "every disclosure candidate needs a third-round verification")
    for candidate_id, check in checks.items():
        layers = check.get("checks", {})
        result.require(isinstance(layers, dict), f"{candidate_id}: checks must be an object")
        if isinstance(layers, dict):
            result.require(bool(layers.get("transcript_grounding")), f"{candidate_id}: transcript grounding is missing")
            result.require(bool(layers.get("case_cross_check")), f"{candidate_id}: case cross-check is missing")
            result.require(bool(layers.get("independent_external_check")), f"{candidate_id}: independent external check is missing")
        result.require(check.get("final_status") in FINAL_STATUSES, f"{candidate_id}: invalid final status")

    verification_by_id = {
        str(item.get("verification_id")): item
        for item in checks.values()
        if item.get("verification_id")
    }
    verification_ids = set(verification_by_id)
    for disclosure in oral_history.get("disclosures", []):
        if not isinstance(disclosure, dict):
            continue
        if str(disclosure.get("interview_id")) not in full:
            continue
        ref = str(disclosure.get("third_round_verification_id", ""))
        result.require(bool(ref), f"{disclosure.get('id')}: third_round_verification_id is required")
        if ref:
            result.require(ref in verification_ids, f"{disclosure.get('id')}: unknown third-round verification")
            verification = verification_by_id.get(ref, {})
            result.require(
                verification.get("writeback_eligible") is True,
                f"{disclosure.get('id')}: third-round result is not eligible for writeback",
            )
            result.require(
                verification.get("final_status") not in {"excluded", "contradicted"},
                f"{disclosure.get('id')}: excluded or contradicted disclosure reached the case layer",
            )

    writeback = manifest.get("writeback", {})
    if writeback:
        case_ids = {
            "claim_ids": {str(item.get("id")) for item in case_data.get("claims", [])},
            "event_ids": {str(item.get("id")) for item in case_data.get("events", [])},
            "disclosure_ids": {
                str(item.get("id"))
                for item in oral_history.get("disclosures", [])
                if isinstance(item, dict)
            },
        }
        for label, known in case_ids.items():
            declared = writeback.get(label, [])
            result.require(isinstance(declared, list), f"writeback.{label} must be an array")
            if isinstance(declared, list):
                result.require(
                    all(str(item) in known for item in declared),
                    f"writeback.{label} references an object missing from case.json",
                )

    release_gate = manifest.get("release_gate", {})
    result.require(release_gate.get("status") == "passed", "Leader release gate must be passed")
    result.require(not release_gate.get("blockers"), "Leader release gate has blockers")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path)
    args = parser.parse_args()
    result = validate_interview_analysis(args.case)
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
