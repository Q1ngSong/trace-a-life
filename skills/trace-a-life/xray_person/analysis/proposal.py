"""Build a source-bounded business-analysis proposal from an existing case.

This module is deliberately extractive.  It groups records that already exist
in ``case.json`` and carries their declared epistemic status forward.  It does
not infer a cause, merge entities, call a model, or write back to the case.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping


ANALYSIS_SCHEMA_VERSION = "xray-business-analysis/1"
LAYERS = ("verified_facts", "self_reports", "inferences", "unknowns")

THEMES: tuple[tuple[str, str], ...] = (
    ("origins", "起家"),
    ("expansion", "扩张"),
    ("decisions", "关键决策"),
    ("contexts", "社会环境"),
    ("relationships", "人际关系"),
    ("wealth", "财富变化与传承"),
)

# Keep the mapping explicit: these are case chapter identifiers, not keyword
# guesses.  The last two chapters are deliberately surfaced in the decision
# and wealth themes so a turning point or a legacy arrangement cannot silently
# disappear from the proposal.
_EVENT_THEME = {
    "origins": "origins",
    "ascent": "expansion",
    "turning-point": "decisions",
    "legacy": "wealth",
}


def _clean_ids(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted({value for value in values if isinstance(value, str) and value})


def _record_refs(record: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    source_ids = _clean_ids(record.get("source_ids"))
    claim_ids = _clean_ids(record.get("claim_ids"))
    evidence = record.get("evidence")
    if isinstance(evidence, list):
        source_ids = sorted(
            set(source_ids)
            | {
                item.get("source_id")
                for item in evidence
                if isinstance(item, Mapping)
                and isinstance(item.get("source_id"), str)
                and item.get("source_id")
            }
        )
    return source_ids, claim_ids


def _layer_for(record: Mapping[str, Any], record_type: str) -> tuple[str, str, str]:
    """Return layer, normalized epistemic status, and an audit reason."""

    explicit = record.get("epistemic_status")
    if explicit == "fact":
        return "verified_facts", "fact", "record.epistemic_status=fact"
    if explicit == "self_report":
        return "self_reports", "self_report", "record.epistemic_status=self_report"
    if explicit == "inference":
        return "inferences", "inference", "record.epistemic_status=inference"
    if explicit == "dispute":
        return "unknowns", "dispute", "record.epistemic_status=dispute"

    claim_status = record.get("status")
    if record_type == "claim":
        if claim_status in {"verified", "credible"}:
            return "verified_facts", str(claim_status), f"claim.status={claim_status}"
        if claim_status == "self_reported":
            return "self_reports", "self_report", "claim.status=self_reported"
        if claim_status in {"partial", "unverified", "contradicted"}:
            return "unknowns", str(claim_status), f"claim.status={claim_status}"

    interpretation = record.get("interpretation_status")
    if record_type == "decision" and interpretation in {"fact", "inference"}:
        layer = "verified_facts" if interpretation == "fact" else "inferences"
        return layer, interpretation, f"decision.interpretation_status={interpretation}"

    return "unknowns", "unknown", "no explicit epistemic status; held as unknown"


def _title(record: Mapping[str, Any], record_type: str) -> str:
    for field in ("title", "text", "question", "label", "summary"):
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"{record_type}:{record.get('id', 'unknown')}"


def _item(record: Mapping[str, Any], record_type: str) -> dict[str, Any]:
    source_ids, claim_ids = _record_refs(record)
    layer, epistemic_status, reason = _layer_for(record, record_type)
    identifier = str(record.get("id") or record.get("candidate_id") or "unknown")
    return {
        "id": f"{record_type}:{identifier}",
        "record_type": record_type,
        "title": _title(record, record_type),
        "epistemic_status": epistemic_status,
        "classification_reason": reason,
        "source_ids": source_ids,
        "claim_ids": claim_ids,
        "record": deepcopy(dict(record)),
        "_layer": layer,
    }


def _append(theme: dict[str, Any], record: Mapping[str, Any], record_type: str) -> None:
    item = _item(record, record_type)
    layer = item.pop("_layer")
    theme[layer].append(item)


def _theme_template(theme_id: str, label: str) -> dict[str, Any]:
    return {
        "id": theme_id,
        "label": label,
        "verified_facts": [],
        "self_reports": [],
        "inferences": [],
        "unknowns": [],
        "unresolved_question_ids": [],
        "coverage": {layer: 0 for layer in LAYERS},
    }


def _ordered(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: (item.get("record", {}).get("date", ""), item["id"]))


def build_analysis_proposal(case: Mapping[str, Any]) -> dict[str, Any]:
    """Create a deterministic proposal without changing ``case``.

    Records without an explicit epistemic status are retained under
    ``unknowns``.  This is intentional: a source reference alone is not
    converted into a causal or historical conclusion.
    """

    if not isinstance(case, Mapping):
        raise TypeError("case must be a mapping")
    subject = case.get("subject", {})
    if not isinstance(subject, Mapping) or not subject.get("name"):
        raise ValueError("case.subject.name is required")

    themes = {theme_id: _theme_template(theme_id, label) for theme_id, label in THEMES}
    events = case.get("events", []) if isinstance(case.get("events", []), list) else []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        theme_id = _EVENT_THEME.get(str(event.get("chapter_id")))
        if theme_id:
            _append(themes[theme_id], event, "event")

    decisions = case.get("decisions", []) if isinstance(case.get("decisions", []), list) else []
    for decision in decisions:
        if isinstance(decision, Mapping):
            _append(themes["decisions"], decision, "decision")

    contexts = case.get("contexts", []) if isinstance(case.get("contexts", []), list) else []
    for context in contexts:
        if isinstance(context, Mapping):
            _append(themes["contexts"], context, "context")

    relationships = case.get("relationships", {})
    relationships = relationships if isinstance(relationships, Mapping) else {}
    for edge in relationships.get("edges", []) if isinstance(relationships.get("edges", []), list) else []:
        if isinstance(edge, Mapping):
            _append(themes["relationships"], edge, "relationship")

    wealth = case.get("wealth", {})
    wealth = wealth if isinstance(wealth, Mapping) else {}
    for record_type in ("stages", "transfers"):
        records = wealth.get(record_type, [])
        for record in records if isinstance(records, list) else []:
            if isinstance(record, Mapping):
                _append(themes["wealth"], record, f"wealth_{record_type[:-1]}")

    claims = case.get("claims", []) if isinstance(case.get("claims", []), list) else []
    claims_by_id = {
        str(claim.get("id")): claim
        for claim in claims
        if isinstance(claim, Mapping) and claim.get("id")
    }
    # Claims are attached to the themes through explicit event/decision links.
    # Claims not linked to one of those records remain in a separate evidence
    # section rather than being assigned to a topic by keyword guessing.
    linked_claim_ids: set[str] = set()
    for theme in themes.values():
        for layer in LAYERS:
            for item in theme[layer]:
                linked_claim_ids.update(item.get("claim_ids", []))
    claim_evidence = {
        "verified_facts": [],
        "self_reports": [],
        "inferences": [],
        "unknowns": [],
    }
    for claim in claims:
        if not isinstance(claim, Mapping):
            continue
        item = _item(claim, "claim")
        layer = item.pop("_layer")
        claim_evidence[layer].append(item)

    gaps = case.get("unresolved_questions", [])
    gaps = gaps if isinstance(gaps, list) else []
    unresolved = []
    for gap in gaps:
        if not isinstance(gap, Mapping):
            continue
        item = _item(gap, "unresolved_question")
        item.pop("_layer")
        item["epistemic_status"] = "unknown"
        unresolved.append(item)
        gap_claim_ids = set(item.get("record", {}).get("claim_ids", []))
        for theme in themes.values():
            theme_claim_ids = {
                claim_id
                for layer in LAYERS
                for record in theme[layer]
                for claim_id in record.get("claim_ids", [])
            }
            if gap_claim_ids & theme_claim_ids:
                theme["unknowns"].append(deepcopy(item))
                theme["unresolved_question_ids"].append(item["id"])

    for theme in themes.values():
        for layer in LAYERS:
            theme[layer] = _ordered(theme[layer])
            theme["coverage"][layer] = len(theme[layer])
        theme["unresolved_question_ids"] = sorted(set(theme["unresolved_question_ids"]))

    proposal = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "subject": {"name": str(subject.get("name")), "anchor": str(subject.get("anchor", ""))},
        "generated_from": {
            "case_schema_version": case.get("schema_version", ""),
            "record_counts": {
                "events": len(events),
                "decisions": len(decisions),
                "contexts": len(contexts),
                "relationship_edges": len(relationships.get("edges", [])) if isinstance(relationships.get("edges", []), list) else 0,
                "wealth_stages": len(wealth.get("stages", [])) if isinstance(wealth.get("stages", []), list) else 0,
                "wealth_transfers": len(wealth.get("transfers", [])) if isinstance(wealth.get("transfers", []), list) else 0,
                "claims": len(claims),
                "unresolved_questions": len(unresolved),
            },
        },
        "guardrails": [
            "仅复制已有 case 字段和显式 epistemic/status 标签。",
            "没有来源或显式状态的记录保留为 unknowns，不升级为事实。",
            "提案不生成因果关系，不替代 verification 或 fact_freeze。",
        ],
        "themes": themes,
        "claim_evidence": claim_evidence,
        "unresolved_questions": _ordered(unresolved),
        "summary": {
            "theme_count": len(themes),
            "verified_fact_count": sum(len(theme["verified_facts"]) for theme in themes.values()) + len(claim_evidence["verified_facts"]),
            "self_report_count": sum(len(theme["self_reports"]) for theme in themes.values()) + len(claim_evidence["self_reports"]),
            "inference_count": sum(len(theme["inferences"]) for theme in themes.values()) + len(claim_evidence["inferences"]),
            "unknown_count": sum(
                sum(item.get("record_type") != "unresolved_question" for item in theme["unknowns"])
                for theme in themes.values()
            )
            + len(claim_evidence["unknowns"])
            + len(unresolved),
        },
    }
    return proposal


def load_analysis_input(path: str | Path) -> dict[str, Any]:
    """Load a plain case or a public results envelope without mutation."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        isinstance(payload, Mapping)
        and payload.get("schema_version") == "public-research/1"
        and isinstance(payload.get("data"), Mapping)
    ):
        return deepcopy(dict(payload["data"]))
    if not isinstance(payload, Mapping):
        raise ValueError("analysis input must be a JSON object")
    return deepcopy(dict(payload))


def write_analysis_proposal(path: str | Path, proposal: Mapping[str, Any]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(proposal, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination
