"""Static acceptance checks for webpage delivery artifacts."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from ..presentation.attestation import attestation_values, json_sha256
from ..presentation.view_model import build_view_model, canonical_json_bytes
from .manifest import verify_delivery_manifest


_FACT_SECTIONS = {
    "top",
    "story",
    "timeline",
    "decisions",
    "network",
    "wealth",
    "context",
    "claims",
    "gaps",
    "sources",
}
_DANGEROUS_HTML = re.compile(
    r"<\s*(?:/\s*|!|\?|[a-z])|javascript\s*:|(?:^|[\s\"'])on[a-z]+\s*=",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class QAIssue:
    severity: str
    code: str
    message: str
    location: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QAReport:
    issues: tuple[QAIssue, ...]

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def errors(self) -> tuple[QAIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[QAIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": {"errors": len(self.errors), "warnings": len(self.warnings)},
            "issues": [issue.to_dict() for issue in self.issues],
        }


class _SectionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sections: set[str] = set()
        self.section_text: dict[str, list[str]] = {}
        self.meta: dict[str, list[str]] = {}
        self.document_text: list[str] = []
        self._section_stack: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "section":
            section_id = attributes.get("id")
            self._section_stack.append(section_id)
            if section_id:
                self.sections.add(section_id)
                self.section_text.setdefault(section_id, [])
        elif tag == "meta" and attributes.get("name"):
            self.meta.setdefault(str(attributes["name"]), []).append(
                str(attributes.get("content", ""))
            )

    def handle_endtag(self, tag: str) -> None:
        if tag == "section" and self._section_stack:
            self._section_stack.pop()

    def handle_data(self, data: str) -> None:
        self.document_text.append(data)
        if self._section_stack and self._section_stack[-1]:
            self.section_text[self._section_stack[-1]].append(data)


def _walk(value: Any, path: str = "") -> Iterable[tuple[str, Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        yield path, value
        for key, child in value.items():
            next_path = f"{path}.{key}" if path else str(key)
            yield from _walk(child, next_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def _scalar_strings(value: Any, path: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            next_path = f"{path}.{key}" if path else str(key)
            yield from _scalar_strings(child, next_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _scalar_strings(child, f"{path}[{index}]")
    elif isinstance(value, str) and value.strip():
        yield path, value


def _normalize(value: str) -> str:
    return " ".join(value.split())


def _known_ids(view_model: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    indexes = view_model.get("indexes", {})
    sources = indexes.get("sources", {}) if isinstance(indexes, Mapping) else {}
    claims = indexes.get("claims", {}) if isinstance(indexes, Mapping) else {}
    return (
        {str(value) for value in sources} if isinstance(sources, Mapping) else set(),
        {str(value) for value in claims} if isinstance(claims, Mapping) else set(),
    )


def _attestation_issues(
    case_data: Mapping[str, Any], view_model: Mapping[str, Any], parser: _SectionParser
) -> list[QAIssue]:
    issues: list[QAIssue] = []
    for name, expected in attestation_values(case_data, view_model).items():
        actual = parser.meta.get(name, [])
        if len(actual) != 1:
            issues.append(
                QAIssue(
                    "error",
                    "html_attestation_missing_or_duplicate",
                    f"HTML must contain exactly one {name} meta tag",
                    "html.head",
                    {"meta": name, "count": len(actual)},
                )
            )
        elif actual[0] != expected:
            issues.append(
                QAIssue(
                    "error",
                    "html_attestation_mismatch",
                    f"HTML {name} does not match current delivery input",
                    "html.head",
                    {"meta": name, "expected": expected, "actual": actual[0]},
                )
            )
    expected_view_model = build_view_model(case_data)
    if canonical_json_bytes(expected_view_model) != canonical_json_bytes(view_model):
        issues.append(
            QAIssue(
                "error",
                "view_model_case_mismatch",
                "view model is not the deterministic projection of the current case",
                "view_model",
                {
                    "expected_sha256": json_sha256(expected_view_model),
                    "actual_sha256": json_sha256(view_model),
                },
            )
        )
    return issues


def _expected_section_values(case_data: Mapping[str, Any]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {section: [] for section in _FACT_SECTIONS}
    oral_history = case_data.get("oral_history", {})
    if isinstance(oral_history, Mapping) and (
        oral_history.get("summary") or oral_history.get("interviews") or oral_history.get("disclosures")
    ):
        values["interviews"] = []

    def add(section: str, item: Mapping[str, Any], fields: Iterable[str]) -> None:
        for field_name in fields:
            value = item.get(field_name)
            if isinstance(value, str) and value.strip():
                values[section].append(value)
            elif isinstance(value, list):
                values[section].extend(
                    str(member) for member in value if isinstance(member, str) and member.strip()
                )

    subject = case_data.get("subject", {})
    report = case_data.get("report", {})
    if isinstance(subject, Mapping):
        add("top", subject, ("name", "anchor"))
        add("story", subject, ("summary",))
    if isinstance(report, Mapping):
        add("top", report, ("eyebrow", "dek", "thesis", "as_of"))
    for chapter in case_data.get("chapters", []):
        if isinstance(chapter, Mapping):
            add("story", chapter, ("label", "years", "title", "summary"))
    for event in case_data.get("events", []):
        if isinstance(event, Mapping):
            add("timeline", event, ("date_label", "title", "summary"))
    for decision in case_data.get("decisions", []):
        if isinstance(decision, Mapping):
            add(
                "decisions",
                decision,
                (
                    "date",
                    "title",
                    "decision",
                    "known_at_time",
                    "alternatives",
                    "constraints",
                    "outcome",
                    "interpretation",
                    "alternative_explanations",
                    "falsifier",
                ),
            )
    relationships = case_data.get("relationships", {})
    if isinstance(relationships, Mapping):
        for node in relationships.get("nodes", []):
            if isinstance(node, Mapping):
                add("network", node, ("label", "description"))
        for edge in relationships.get("edges", []):
            if isinstance(edge, Mapping):
                add("network", edge, ("label", "period"))
    wealth = case_data.get("wealth", {})
    if isinstance(wealth, Mapping):
        add("wealth", wealth, ("summary",))
        for item in list(wealth.get("stages", [])) + list(wealth.get("transfers", [])):
            if isinstance(item, Mapping):
                add("wealth", item, ("period", "title", "summary", "mechanism"))
    for context in case_data.get("contexts", []):
        if isinstance(context, Mapping):
            add("context", context, ("period", "type", "title", "summary"))
    if "interviews" in values and isinstance(oral_history, Mapping):
        add("interviews", oral_history, ("summary",))
        for interview in oral_history.get("interviews", []):
            if isinstance(interview, Mapping):
                add(
                    "interviews",
                    interview,
                    ("title", "publisher", "published_at", "transcript_status"),
                )
        for disclosure in oral_history.get("disclosures", []):
            if isinstance(disclosure, Mapping):
                add(
                    "interviews",
                    disclosure,
                    ("title", "summary", "timecode", "cross_check", "sensitivity"),
                )
    for claim in case_data.get("claims", []):
        if isinstance(claim, Mapping):
            add("claims", claim, ("text", "importance", "explanation", "counterevidence"))
    for gap in case_data.get("unresolved_questions", []):
        if isinstance(gap, Mapping):
            add("gaps", gap, ("question", "searched", "next_query"))
    for source in case_data.get("sources", []):
        if isinstance(source, Mapping):
            add(
                "sources",
                source,
                ("id", "title", "publisher", "published_at", "role", "origin_cluster"),
            )
    return values


def _html_projection_issues(
    case_data: Mapping[str, Any], parser: _SectionParser
) -> list[QAIssue]:
    issues: list[QAIssue] = []
    for section, values in _expected_section_values(case_data).items():
        rendered = _normalize(" ".join(parser.section_text.get(section, [])))
        missing = sorted(
            {
                value
                for value in values
                if _normalize(value) and _normalize(value) not in rendered
            }
        )
        if missing:
            issues.append(
                QAIssue(
                    "error",
                    "html_projection_stale_or_incomplete",
                    f"#{section} is not synchronized with the current case",
                    f"html#{section}",
                    {"missing_values": missing[:20], "missing_count": len(missing)},
                )
            )
    return issues


def _security_issues(case_data: Mapping[str, Any]) -> list[QAIssue]:
    issues: list[QAIssue] = []
    for index, source in enumerate(case_data.get("sources", [])):
        if not isinstance(source, Mapping):
            continue
        source_id = str(source.get("id", index))
        for field_name, value in source.items():
            if isinstance(value, str) and _DANGEROUS_HTML.search(value):
                issues.append(
                    QAIssue(
                        "error",
                        "unsafe_evidence_text",
                        f"source {source_id}.{field_name} contains HTML-shaped executable input",
                        f"case.sources.{source_id}.{field_name}",
                    )
                )
        url = str(source.get("url", ""))
        if urlsplit(url).scheme not in {"http", "https"}:
            issues.append(
                QAIssue(
                    "error",
                    "unsafe_source_url",
                    f"source {source_id} URL must be http(s)",
                    f"case.sources.{source_id}.url",
                )
            )
    for claim in case_data.get("claims", []):
        if not isinstance(claim, Mapping):
            continue
        claim_id = str(claim.get("id", "?"))
        for index, proof in enumerate(claim.get("evidence", [])):
            quote = proof.get("quote") if isinstance(proof, Mapping) else None
            if isinstance(quote, str) and _DANGEROUS_HTML.search(quote):
                issues.append(
                    QAIssue(
                        "error",
                        "unsafe_evidence_text",
                        f"claim {claim_id} quote contains HTML-shaped executable input",
                        f"case.claims.{claim_id}.evidence[{index}].quote",
                    )
                )

    portrait = str(case_data.get("subject", {}).get("portrait", "")).strip()
    if portrait and not portrait.startswith(("https://", "data:image/")):
        local = PurePosixPath(portrait)
        unsafe = (
            local.is_absolute()
            or "\\" in portrait
            or ":" in portrait
            or any(part == ".." for part in local.parts)
            or any(ord(character) < 32 for character in portrait)
        )
        if unsafe:
            issues.append(
                QAIssue(
                    "error",
                    "unsafe_portrait_path",
                    "portrait must be a site-local normalized relative path, https URL, or image data URI",
                    "case.subject.portrait",
                )
            )
    return issues


def _reference_issues(view_model: Mapping[str, Any]) -> list[QAIssue]:
    issues: list[QAIssue] = []
    source_ids, claim_ids = _known_ids(view_model)
    fact = view_model.get("fact", {})
    for path, item in _walk(fact, "fact"):
        for source_id in item.get("source_ids", []) if isinstance(item.get("source_ids"), list) else []:
            if source_id not in source_ids:
                issues.append(QAIssue("error", "missing_source_reference", f"unknown source {source_id}", path))
        for claim_id in item.get("claim_ids", []) if isinstance(item.get("claim_ids"), list) else []:
            if claim_id not in claim_ids:
                issues.append(QAIssue("error", "missing_claim_reference", f"unknown claim {claim_id}", path))
        source_id = item.get("source_id")
        if source_id is not None and source_id not in source_ids:
            issues.append(QAIssue("error", "missing_source_reference", f"unknown source {source_id}", path))

    claims = fact.get("evidence", {}).get("claims", []) if isinstance(fact, Mapping) else []
    for claim in claims if isinstance(claims, list) else []:
        if not isinstance(claim, Mapping):
            continue
        claim_id = str(claim.get("id", "?"))
        evidence = claim.get("evidence", [])
        if claim.get("status") in {"verified", "credible"} and not evidence:
            issues.append(
                QAIssue(
                    "error",
                    "claim_evidence_missing",
                    f"{claim_id} is {claim.get('status')} but has no evidence",
                    f"fact.evidence.claims.{claim_id}",
                )
            )
        for index, proof in enumerate(evidence if isinstance(evidence, list) else []):
            if isinstance(proof, Mapping) and claim.get("status") in {"verified", "credible"}:
                if not str(proof.get("quote", "")).strip():
                    issues.append(
                        QAIssue(
                            "error",
                            "claim_quote_missing",
                            f"{claim_id} evidence {index} has no verbatim quote",
                            f"fact.evidence.claims.{claim_id}.evidence[{index}]",
                        )
                    )
    timeline = fact.get("timeline", []) if isinstance(fact, Mapping) else []
    for event in timeline if isinstance(timeline, list) else []:
        if isinstance(event, Mapping) and not event.get("source_ids") and not event.get("claim_ids"):
            event_id = str(event.get("id", "?"))
            issues.append(
                QAIssue(
                    "error",
                    "event_evidence_missing",
                    f"{event_id} has neither source nor claim references",
                    f"fact.timeline.{event_id}",
                )
            )
    return issues


def _traceability_issues(
    case_data: Mapping[str, Any], view_model: Mapping[str, Any]
) -> list[QAIssue]:
    issues: list[QAIssue] = []
    expected = build_view_model(case_data).get("traceability", {}).get("content_refs", {})
    actual = view_model.get("traceability", {}).get("content_refs", {})
    if not isinstance(actual, Mapping):
        return [QAIssue("error", "traceability_missing", "content reference ledger is missing")]
    expected_keys = set(expected)
    actual_keys = set(actual)
    if expected_keys != actual_keys:
        issues.append(
            QAIssue(
                "error",
                "traceability_entity_set_mismatch",
                "traceability entity count or IDs differ from the current case",
                "view_model.traceability.content_refs",
                {"missing": sorted(expected_keys - actual_keys), "extra": sorted(actual_keys - expected_keys)},
            )
        )
    for key in sorted(expected_keys & actual_keys):
        if canonical_json_bytes(expected[key]) != canonical_json_bytes(actual[key]):
            issues.append(
                QAIssue(
                    "error",
                    "traceability_reference_mismatch",
                    f"projection references differ for {key}",
                    key,
                    {"expected": expected[key], "actual": actual[key]},
                )
            )
    return issues


def _simulation_payloads(
    case_data: Mapping[str, Any], *, rendered_only: bool = False
) -> set[str]:
    fact_case = {key: value for key, value in case_data.items() if key != "scenarios"}
    fact_values = {_normalize(value) for _, value in _scalar_strings(fact_case)}
    payloads: set[str] = set()
    for scenario in case_data.get("scenarios", []):
        if not isinstance(scenario, Mapping):
            continue
        fields = (
            ("title", "summary", "signals", "limitations")
            if rendered_only
            else ("id", "title", "question", "summary", "signals", "limitations")
        )
        for field_name in fields:
            value = scenario.get(field_name)
            candidates = value if isinstance(value, list) else [value]
            for candidate in candidates:
                if isinstance(candidate, str) and len(_normalize(candidate)) >= 8:
                    if _normalize(candidate) not in fact_values:
                        payloads.add(_normalize(candidate))
    return payloads


def _simulation_issues(
    case_data: Mapping[str, Any], view_model: Mapping[str, Any], parser: _SectionParser
) -> list[QAIssue]:
    issues: list[QAIssue] = []
    fact = view_model.get("fact", {})
    for path, item in _walk(fact, "fact"):
        if item.get("epistemic_status") == "simulation" or item.get("layer") == "simulation":
            issues.append(
                QAIssue(
                    "error",
                    "simulation_in_fact_layer",
                    "simulation content appears in the historical fact layer",
                    path,
                )
            )

    case_scenarios = {
        str(item.get("id")): item
        for item in case_data.get("scenarios", [])
        if isinstance(item, Mapping) and item.get("id")
    }
    simulation = view_model.get("simulation", {})
    projected_list = simulation.get("scenarios", []) if isinstance(simulation, Mapping) else []
    projected = {
        str(item.get("id")): item
        for item in projected_list
        if isinstance(item, Mapping) and item.get("id")
    }
    if set(case_scenarios) != set(projected):
        issues.append(
            QAIssue(
                "error",
                "simulation_projection_set_mismatch",
                "scenario count or IDs differ between case and view model",
                "view_model.simulation.scenarios",
                {
                    "missing": sorted(set(case_scenarios) - set(projected)),
                    "extra": sorted(set(projected) - set(case_scenarios)),
                },
            )
        )
    for scenario_id in sorted(set(case_scenarios) & set(projected)):
        if canonical_json_bytes(case_scenarios[scenario_id]) != canonical_json_bytes(projected[scenario_id]):
            issues.append(
                QAIssue(
                    "error",
                    "simulation_projection_payload_mismatch",
                    f"scenario {scenario_id} payload changed during projection",
                    f"view_model.simulation.scenarios.{scenario_id}",
                )
            )
    expected_trace = build_view_model(case_data).get("traceability", {}).get("simulation", {})
    actual_trace = view_model.get("traceability", {}).get("simulation", {})
    if canonical_json_bytes(expected_trace) != canonical_json_bytes(actual_trace):
        issues.append(
            QAIssue(
                "error",
                "simulation_traceability_mismatch",
                "scenario traceability count, IDs, or digests differ from the current case",
                "view_model.traceability.simulation",
            )
        )

    payloads = _simulation_payloads(case_data)
    for path, value in _scalar_strings(fact, "fact"):
        normalized = _normalize(value)
        if any(payload in normalized for payload in payloads):
            issues.append(
                QAIssue(
                    "error",
                    "simulation_payload_in_fact_layer",
                    "scenario-specific payload appears in the historical fact projection",
                    path,
                )
            )

    if case_scenarios:
        if "simulation" not in parser.sections:
            issues.append(QAIssue("error", "html_section_missing", "missing #simulation section", "html"))
        simulation_text = _normalize(" ".join(parser.section_text.get("simulation", [])))
        if "SIMULATION" not in simulation_text or "非史实" not in simulation_text:
            issues.append(
                QAIssue(
                    "error",
                    "simulation_disclaimer_missing",
                    "simulation section must show SIMULATION and 非史实",
                    "html#simulation",
                )
            )
        rendered_payloads = _simulation_payloads(case_data, rendered_only=True)
        for payload in sorted(rendered_payloads):
            if payload not in simulation_text:
                issues.append(
                    QAIssue(
                        "error",
                        "simulation_html_payload_missing",
                        "scenario payload is missing from #simulation",
                        "html#simulation",
                        {"payload": payload},
                    )
                )
        for section_id in _FACT_SECTIONS | {"interviews"}:
            text = _normalize(" ".join(parser.section_text.get(section_id, [])))
            leaked = sorted(payload for payload in payloads if payload in text)
            if leaked:
                issues.append(
                    QAIssue(
                        "error",
                        "simulation_in_fact_html",
                        f"scenario payload appears in fact section #{section_id}",
                        f"html#{section_id}",
                        {"payloads": leaked[:10]},
                    )
                )
    return issues


def run_delivery_qa(
    *,
    case_data: Mapping[str, Any],
    view_model: Mapping[str, Any],
    html: str,
    manifest: Mapping[str, Any] | None = None,
    manifest_base_dir: str | Path | None = None,
) -> QAReport:
    """Run all static delivery checks and return a serializable report."""

    parser = _SectionParser()
    parser.feed(html)
    issues: list[QAIssue] = []
    for section_id in sorted(_FACT_SECTIONS):
        if section_id not in parser.sections:
            issues.append(QAIssue("error", "html_section_missing", f"missing #{section_id} section", "html"))

    issues.extend(_attestation_issues(case_data, view_model, parser))
    issues.extend(_html_projection_issues(case_data, parser))
    issues.extend(_security_issues(case_data))
    issues.extend(_reference_issues(view_model))
    issues.extend(_traceability_issues(case_data, view_model))
    issues.extend(_simulation_issues(case_data, view_model, parser))

    if manifest is not None:
        if manifest_base_dir is None:
            issues.append(
                QAIssue(
                    "error",
                    "manifest_base_dir_missing",
                    "manifest verification requires manifest_base_dir",
                )
            )
        else:
            for drift in verify_delivery_manifest(manifest, manifest_base_dir):
                issues.append(
                    QAIssue(
                        "error",
                        drift.code,
                        f"delivery artifact drift: {drift.artifact}",
                        drift.path,
                        drift.to_dict(),
                    )
                )
    return QAReport(tuple(issues))
