"""Project a research case into a deterministic, traceable page view model.

The projection is deliberately renderer-agnostic.  It keeps the historical
fact layer and simulation layer in separate top-level objects, while retaining
all source and claim references needed by an HTML renderer or evidence drawer.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping


VIEW_MODEL_SCHEMA_VERSION = "trace-a-life/view-model/1"
_CHAPTER_ORDER = ("origins", "ascent", "turning-point", "legacy")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON data deterministically for storage or hashing."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _ordered(items: Iterable[Mapping[str, Any]], *keys: str) -> list[dict[str, Any]]:
    """Deep-copy mappings and sort by stable textual keys."""

    copied = [copy.deepcopy(dict(item)) for item in items if isinstance(item, Mapping)]
    return sorted(copied, key=lambda item: tuple(str(item.get(key, "")) for key in keys))


def _refs(item: Mapping[str, Any]) -> dict[str, list[str]]:
    source_ids = {
        value
        for value in item.get("source_ids", [])
        if isinstance(value, str) and value
    }
    claim_ids = {
        value
        for value in item.get("claim_ids", [])
        if isinstance(value, str) and value
    }
    for evidence in item.get("evidence", []):
        if isinstance(evidence, Mapping):
            source_id = evidence.get("source_id")
            if isinstance(source_id, str) and source_id:
                source_ids.add(source_id)
    return {"source_ids": sorted(source_ids), "claim_ids": sorted(claim_ids)}


def _entity_refs(case_data: Mapping[str, Any]) -> dict[str, dict[str, list[str]]]:
    """Create one stable reference record per fact-layer entity."""

    records: dict[str, dict[str, list[str]]] = {}
    collection_paths = (
        ("claims", case_data.get("claims", [])),
        ("events", case_data.get("events", [])),
        ("decisions", case_data.get("decisions", [])),
        ("contexts", case_data.get("contexts", [])),
        ("interviews", case_data.get("oral_history", {}).get("interviews", [])),
        ("oral_disclosures", case_data.get("oral_history", {}).get("disclosures", [])),
        ("relationship_edges", case_data.get("relationships", {}).get("edges", [])),
        ("wealth_stages", case_data.get("wealth", {}).get("stages", [])),
        ("wealth_transfers", case_data.get("wealth", {}).get("transfers", [])),
    )
    for collection, items in collection_paths:
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, Mapping):
                continue
            identifier = item.get("id")
            if isinstance(identifier, str) and identifier:
                records[f"{collection}:{identifier}"] = _refs(item)
    return dict(sorted(records.items()))


def _claim_projection(
    claims: Iterable[Mapping[str, Any]],
    source_index: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for claim in _ordered(claims, "id"):
        source_ids = _refs(claim)["source_ids"]
        clusters = {
            source_index[source_id].get("origin_cluster")
            for source_id in source_ids
            if source_id in source_index
        }
        claim["source_ids"] = source_ids
        claim["independent_origin_clusters"] = len({value for value in clusters if value})
        projected.append(claim)
    return projected


def build_view_model(case_data: Mapping[str, Any]) -> dict[str, Any]:
    """Build the complete renderer-neutral view model for one case.

    No current time or random value is introduced, so identical case data
    produces byte-identical canonical JSON.  Historical facts live under
    ``fact``; MiroFish and other counterfactual outputs live under
    ``simulation`` and are never copied into that fact object.
    """

    sources = _ordered(case_data.get("sources", []), "id")
    source_index = {
        source["id"]: source
        for source in sources
        if isinstance(source.get("id"), str) and source.get("id")
    }
    claims = _claim_projection(case_data.get("claims", []), source_index)
    claim_index = {
        claim["id"]: claim
        for claim in claims
        if isinstance(claim.get("id"), str) and claim.get("id")
    }

    events = _ordered(case_data.get("events", []), "date", "id")
    chapters_by_id = {
        chapter.get("id"): copy.deepcopy(dict(chapter))
        for chapter in case_data.get("chapters", [])
        if isinstance(chapter, Mapping) and chapter.get("id")
    }
    life_stages: list[dict[str, Any]] = []
    for chapter_id in _CHAPTER_ORDER:
        chapter = chapters_by_id.pop(chapter_id, None)
        if chapter is None:
            continue
        chapter["events"] = [
            copy.deepcopy(event) for event in events if event.get("chapter_id") == chapter_id
        ]
        life_stages.append(chapter)
    for chapter_id in sorted(chapters_by_id):
        chapter = chapters_by_id[chapter_id]
        chapter["events"] = [
            copy.deepcopy(event) for event in events if event.get("chapter_id") == chapter_id
        ]
        life_stages.append(chapter)

    status_counts = Counter(str(claim.get("status", "unknown")) for claim in claims)
    importance_counts = Counter(str(claim.get("importance", "unknown")) for claim in claims)
    scenarios = _ordered(case_data.get("scenarios", []), "id")

    fact = {
        "life_stages": life_stages,
        "timeline": events,
        "decisions": _ordered(case_data.get("decisions", []), "date", "id"),
        "relationships": {
            "nodes": _ordered(case_data.get("relationships", {}).get("nodes", []), "id"),
            "edges": _ordered(case_data.get("relationships", {}).get("edges", []), "id"),
        },
        "contexts": _ordered(case_data.get("contexts", []), "period", "id"),
        "oral_history": {
            "summary": copy.deepcopy(case_data.get("oral_history", {}).get("summary", "")),
            "interviews": _ordered(
                case_data.get("oral_history", {}).get("interviews", []), "published_at", "id"
            ),
            "disclosures": _ordered(
                case_data.get("oral_history", {}).get("disclosures", []), "interview_id", "timecode", "id"
            ),
        },
        "wealth": {
            "summary": copy.deepcopy(case_data.get("wealth", {}).get("summary", "")),
            "stages": _ordered(case_data.get("wealth", {}).get("stages", []), "period", "id"),
            "transfers": _ordered(case_data.get("wealth", {}).get("transfers", []), "period", "id"),
        },
        "evidence": {
            "claims": claims,
            "sources": sources,
            "unresolved_questions": _ordered(case_data.get("unresolved_questions", []), "id"),
            "status_counts": dict(sorted(status_counts.items())),
            "importance_counts": dict(sorted(importance_counts.items())),
        },
    }

    return {
        "schema_version": VIEW_MODEL_SCHEMA_VERSION,
        "case_schema_version": copy.deepcopy(case_data.get("schema_version", "")),
        "cover": {
            "subject": copy.deepcopy(case_data.get("subject", {})),
            "report": copy.deepcopy(case_data.get("report", {})),
            "scope": copy.deepcopy(case_data.get("scope", {})),
            "provenance": copy.deepcopy(case_data.get("provenance", {})),
        },
        "fact": fact,
        "simulation": {
            "layer": "simulation",
            "label": "SIMULATION / 非史实",
            "fact_freeze": copy.deepcopy(case_data.get("fact_freeze", {})),
            "scenarios": scenarios,
        },
        "indexes": {
            "sources": copy.deepcopy(source_index),
            "claims": copy.deepcopy(claim_index),
        },
        "traceability": {
            "content_refs": _entity_refs(case_data),
            "simulation": {
                "scenario_count": len(scenarios),
                "scenario_ids": [
                    scenario.get("id")
                    for scenario in scenarios
                    if isinstance(scenario.get("id"), str) and scenario.get("id")
                ],
                "scenario_sha256": {
                    str(scenario.get("id", "")): hashlib.sha256(
                        canonical_json_bytes(scenario)
                    ).hexdigest()
                    for scenario in scenarios
                    if isinstance(scenario.get("id"), str) and scenario.get("id")
                },
            },
        },
    }


def write_view_model(path: str | Path, view_model: Mapping[str, Any]) -> Path:
    """Write a view model as stable, human-readable JSON."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(view_model, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination
