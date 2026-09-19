"""Evidence independence, claim aggregation, and auditable stop gates.

The verifier is intentionally non-generative: it reports what the case says,
counts only explicit source links, and never mutates or upgrades claim status.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from ..domain import CaseDocument
from .query_plan import SearchRound, build_query_plan


CLAIM_STATUSES = frozenset(
    {"verified", "credible", "partial", "self_reported", "unverified", "contradicted"}
)
COUNTER_STANCES = frozenset({"counter", "contradict", "contradicts", "refute", "refutes"})
SUPPORT_STANCES = frozenset({"", "support", "supports"})

# P4 self-reports and P5 discovery leads can remain attached to a claim, but
# cannot by themselves establish a factual status. ``verified`` needs a direct
# record; ``credible`` may additionally use independent retrospective analysis.
VERIFIED_SUPPORT_ROLES = frozenset({"primary", "independent"})
CREDIBLE_SUPPORT_ROLES = frozenset({"primary", "independent", "analysis"})


@dataclass(frozen=True)
class SourceCluster:
    id: str
    source_ids: tuple[str, ...]
    roles: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "source_ids": list(self.source_ids), "roles": list(self.roles)}

    def to_dict(self) -> dict[str, Any]:
        return self.as_dict()


@dataclass(frozen=True)
class RoundCompletion:
    """Auditable evidence that a verification round actually checked queries."""

    round: SearchRound
    queries_checked: tuple[str, ...]
    completed_at: str = ""
    receipt_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.round, SearchRound):
            raise TypeError("round must be a SearchRound")
        if not isinstance(self.queries_checked, tuple) or not all(
            isinstance(query_id, str) and query_id for query_id in self.queries_checked
        ):
            raise TypeError("queries_checked must be a tuple of non-empty query IDs")
        if not isinstance(self.completed_at, str):
            raise TypeError("completed_at must be text")
        if not isinstance(self.receipt_ids, tuple) or not all(
            isinstance(receipt_id, str) and receipt_id for receipt_id in self.receipt_ids
        ):
            raise TypeError("receipt_ids must be a tuple of non-empty IDs")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RoundCompletion":
        raw_round = value.get("round")
        try:
            round_ = raw_round if isinstance(raw_round, SearchRound) else SearchRound(raw_round)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid verification round {raw_round!r}") from exc
        raw_queries = value.get("queries_checked", [])
        if not isinstance(raw_queries, list) or not all(
            isinstance(query_id, str) and query_id for query_id in raw_queries
        ):
            raise ValueError("queries_checked must be an array of non-empty query IDs")
        raw_receipts = value.get("receipt_ids", [])
        if not isinstance(raw_receipts, list) or not all(
            isinstance(receipt_id, str) and receipt_id for receipt_id in raw_receipts
        ):
            raise ValueError("receipt_ids must be an array of non-empty IDs")
        completed_at = value.get("completed_at", "")
        if not isinstance(completed_at, str):
            raise ValueError("completed_at must be text")
        return cls(round_, tuple(raw_queries), completed_at, tuple(raw_receipts))

    def as_dict(self) -> dict[str, Any]:
        return {
            "round": self.round.value,
            "queries_checked": list(self.queries_checked),
            "completed_at": self.completed_at,
            "receipt_ids": list(self.receipt_ids),
        }

    def to_dict(self) -> dict[str, Any]:
        return self.as_dict()


@dataclass(frozen=True)
class ClaimAssessment:
    claim_id: str
    importance: str
    declared_status: str
    support_source_ids: tuple[str, ...]
    counter_source_ids: tuple[str, ...]
    unclassified_source_ids: tuple[str, ...]
    missing_source_ids: tuple[str, ...]
    support_origin_clusters: tuple[str, ...]
    counter_origin_clusters: tuple[str, ...]
    support_lineages: tuple[tuple[str, ...], ...]
    counter_lineages: tuple[tuple[str, ...], ...]
    strong_support_lineage_count: int
    quoted_support_count: int
    counterevidence_note: str
    has_conflict: bool
    lineage_conflicts: tuple[str, ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def independent_support_count(self) -> int:
        return len(self.support_lineages)

    @property
    def independent_counter_count(self) -> int:
        return len(self.counter_lineages)

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "importance": self.importance,
            "declared_status": self.declared_status,
            "support_source_ids": list(self.support_source_ids),
            "counter_source_ids": list(self.counter_source_ids),
            "unclassified_source_ids": list(self.unclassified_source_ids),
            "missing_source_ids": list(self.missing_source_ids),
            "support_origin_clusters": list(self.support_origin_clusters),
            "counter_origin_clusters": list(self.counter_origin_clusters),
            "support_lineages": [list(lineage) for lineage in self.support_lineages],
            "counter_lineages": [list(lineage) for lineage in self.counter_lineages],
            "independent_support_count": self.independent_support_count,
            "independent_counter_count": self.independent_counter_count,
            "strong_support_lineage_count": self.strong_support_lineage_count,
            "quoted_support_count": self.quoted_support_count,
            "counterevidence_note": self.counterevidence_note,
            "has_conflict": self.has_conflict,
            "lineage_conflicts": list(self.lineage_conflicts),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }

    def to_dict(self) -> dict[str, Any]:
        return self.as_dict()


@dataclass(frozen=True)
class StopGate:
    ready: bool
    completed_rounds: tuple[str, ...]
    round_completions: tuple[RoundCompletion, ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "completed_rounds": list(self.completed_rounds),
            "round_completions": [completion.as_dict() for completion in self.round_completions],
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }

    def to_dict(self) -> dict[str, Any]:
        return self.as_dict()


@dataclass(frozen=True)
class VerificationReport:
    source_clusters: Mapping[str, SourceCluster]
    claims: tuple[ClaimAssessment, ...]
    stop_gate: StopGate

    def claim(self, claim_id: str) -> ClaimAssessment | None:
        return next((claim for claim in self.claims if claim.claim_id == claim_id), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_clusters": {
                cluster_id: cluster.as_dict()
                for cluster_id, cluster in self.source_clusters.items()
            },
            "claims": [claim.as_dict() for claim in self.claims],
            "stop_gate": self.stop_gate.as_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return self.as_dict()


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _as_document(case: CaseDocument | Mapping[str, Any]) -> CaseDocument:
    return case if isinstance(case, CaseDocument) else CaseDocument.from_mapping(case)


def _canonical_url(source: Mapping[str, Any]) -> str:
    value = source.get("canonical_url") or source.get("url")
    if not isinstance(value, str) or not value:
        return ""
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    if scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def _lineage_components(
    source_ids: Iterable[str],
    sources: Mapping[str, Mapping[str, Any]],
) -> tuple[tuple[str, ...], ...]:
    ordered_ids = _unique(source_id for source_id in source_ids if source_id in sources)
    parents = {source_id: source_id for source_id in ordered_ids}

    def find(source_id: str) -> str:
        while parents[source_id] != source_id:
            parents[source_id] = parents[parents[source_id]]
            source_id = parents[source_id]
        return source_id

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    owners: dict[tuple[str, str], str] = {}
    for source_id in ordered_ids:
        source = sources[source_id]
        keys: list[tuple[str, str]] = []
        cluster = source.get("origin_cluster")
        if isinstance(cluster, str) and cluster.strip():
            keys.append(("cluster", cluster.strip()))
        canonical_url = _canonical_url(source)
        if canonical_url:
            keys.append(("canonical_url", canonical_url))
        document_id = source.get("source_document_id")
        if isinstance(document_id, str) and document_id.strip():
            keys.append(("document", document_id.strip()))
        for key in keys:
            if key in owners:
                union(owners[key], source_id)
            else:
                owners[key] = source_id

    grouped: dict[str, list[str]] = {}
    for source_id in ordered_ids:
        grouped.setdefault(find(source_id), []).append(source_id)
    return tuple(tuple(group) for group in grouped.values())


def _lineage_conflicts(
    source_ids: Iterable[str],
    sources: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...]:
    ids = _unique(source_id for source_id in source_ids if source_id in sources)
    conflicts: list[str] = []
    for label, key_getter in (
        ("canonical URL", _canonical_url),
        (
            "source document",
            lambda source: str(source.get("source_document_id", "")).strip(),
        ),
    ):
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for source_id in ids:
            key = key_getter(sources[source_id])
            if key:
                grouped.setdefault(key, []).append(sources[source_id])
        for key, records in grouped.items():
            clusters = _unique(
                str(record.get("origin_cluster", "")).strip() for record in records
            )
            if len(clusters) > 1:
                record_ids = _unique(str(record.get("id", "?")) for record in records)
                conflicts.append(
                    f"{label} {key!r} is assigned to multiple origin clusters "
                    f"({', '.join(clusters)}) via {', '.join(record_ids)}"
                )
    return _unique(conflicts)


def build_source_clusters(
    case: CaseDocument | Mapping[str, Any],
) -> Mapping[str, SourceCluster]:
    """Group sources by explicit origin lineage; missing clusters are omitted."""

    document = _as_document(case)
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for source in document.sources:
        cluster = source.get("origin_cluster")
        if isinstance(cluster, str) and cluster.strip():
            grouped.setdefault(cluster.strip(), []).append(source)
    result = {
        cluster_id: SourceCluster(
            id=cluster_id,
            source_ids=_unique(
                str(source.get("id", ""))
                for source in records
                if isinstance(source.get("id"), str)
            ),
            roles=_unique(
                str(source.get("role", ""))
                for source in records
                if isinstance(source.get("role"), str)
            ),
        )
        for cluster_id, records in sorted(grouped.items())
    }
    return MappingProxyType(result)


def _stance(item: Mapping[str, Any]) -> str:
    value = item.get("stance", "support")
    return value.strip().lower() if isinstance(value, str) else "unknown"


def _strong_lineage_count(
    lineages: Iterable[tuple[str, ...]],
    sources: Mapping[str, Mapping[str, Any]],
    allowed_roles: frozenset[str],
) -> int:
    return sum(
        1
        for lineage in lineages
        if any(sources[source_id].get("role") in allowed_roles for source_id in lineage)
    )


def assess_claim(
    claim: Mapping[str, Any],
    sources: Mapping[str, Mapping[str, Any]],
) -> ClaimAssessment:
    """Summarize explicit support/counter links while preserving declared status."""

    claim_id = str(claim.get("id", "?"))
    status = str(claim.get("status", ""))
    evidence = claim.get("evidence", [])
    blockers: list[str] = []
    warnings: list[str] = []
    support_ids: list[str] = []
    counter_ids: list[str] = []
    unclassified_ids: list[str] = []
    missing_ids: list[str] = []
    quoted_support_count = 0
    missing_quote_count = 0

    if not isinstance(evidence, list):
        blockers.append("evidence must be an array")
        evidence = []
    for position, item in enumerate(evidence):
        if not isinstance(item, Mapping):
            blockers.append(f"evidence[{position}] must be an object")
            continue
        source_id = item.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            blockers.append(f"evidence[{position}] has no source_id")
            continue
        if source_id not in sources:
            missing_ids.append(source_id)
        has_quote = isinstance(item.get("quote"), str) and bool(item.get("quote", "").strip())
        if not has_quote:
            missing_quote_count += 1
        stance = _stance(item)
        if stance in SUPPORT_STANCES:
            support_ids.append(source_id)
            if has_quote:
                quoted_support_count += 1
        elif stance in COUNTER_STANCES:
            counter_ids.append(source_id)
        else:
            unclassified_ids.append(source_id)
            warnings.append(f"evidence[{position}] has unknown stance {stance!r}")

    def clusters_for(source_ids: Iterable[str]) -> tuple[str, ...]:
        values: list[str] = []
        for source_id in source_ids:
            source = sources.get(source_id)
            if not source:
                continue
            cluster = source.get("origin_cluster")
            if isinstance(cluster, str) and cluster.strip():
                values.append(cluster.strip())
            else:
                warnings.append(f"source {source_id} has no origin_cluster")
        return _unique(values)

    support_ids_tuple = _unique(support_ids)
    counter_ids_tuple = _unique(counter_ids)
    support_clusters = clusters_for(support_ids_tuple)
    counter_clusters = clusters_for(counter_ids_tuple)
    support_lineages = _lineage_components(support_ids_tuple, sources)
    counter_lineages = _lineage_components(counter_ids_tuple, sources)
    lineage_conflicts = _lineage_conflicts(
        (*support_ids_tuple, *counter_ids_tuple), sources
    )
    has_conflict = bool(support_ids_tuple and counter_ids_tuple)

    allowed_roles = (
        VERIFIED_SUPPORT_ROLES if status == "verified" else CREDIBLE_SUPPORT_ROLES
    )
    strong_support_count = _strong_lineage_count(
        support_lineages, sources, allowed_roles
    )

    if status not in CLAIM_STATUSES:
        blockers.append(f"invalid declared status {status!r}")
    if missing_ids:
        blockers.append("evidence references unknown sources")
    if status in {"verified", "credible", "partial", "self_reported", "contradicted"} and not evidence:
        blockers.append(f"{status} requires attached evidence")
    if status in {"verified", "credible"}:
        if not support_ids_tuple:
            blockers.append(f"{status} requires supporting evidence")
        if missing_quote_count:
            blockers.append(f"{status} evidence requires verbatim quotes")
    if status == "verified" and strong_support_count < 1:
        blockers.append(
            "verified requires at least one primary or independent support lineage; "
            "self_report/discovery cannot establish the fact"
        )
    if status == "credible" and strong_support_count < 2:
        blockers.append(
            "credible requires at least two independent strong support lineages; "
            "self_report/discovery do not count"
        )
    if status == "self_reported":
        roles = {
            sources[source_id].get("role")
            for source_id in support_ids_tuple
            if source_id in sources
        }
        if "self_report" not in roles:
            blockers.append("self_reported requires an attached self_report source")
    if status == "contradicted" and not counter_ids_tuple:
        warnings.append("contradicted has no evidence item with an explicit counter stance")
    if has_conflict and status in {"verified", "credible", "self_reported"}:
        blockers.append(f"{status} leaves explicit support/counter conflict unresolved")
    if lineage_conflicts:
        blockers.extend(f"lineage conflict: {conflict}" for conflict in lineage_conflicts)
    if status == "unverified" and evidence:
        warnings.append("unverified retains attached leads; no status upgrade was inferred")
    if claim.get("importance") == "critical" and status == "unverified":
        warnings.append("critical claim remains unverified and must remain visible as a gap")

    counter_note = claim.get("counterevidence", "")
    if not isinstance(counter_note, str):
        warnings.append("counterevidence note is not text")
        counter_note = ""
    if claim.get("importance") == "critical" and not counter_note.strip():
        blockers.append("critical claim lacks a Round B counterevidence search note")

    return ClaimAssessment(
        claim_id=claim_id,
        importance=str(claim.get("importance", "")),
        declared_status=status,
        support_source_ids=support_ids_tuple,
        counter_source_ids=counter_ids_tuple,
        unclassified_source_ids=_unique(unclassified_ids),
        missing_source_ids=_unique(missing_ids),
        support_origin_clusters=support_clusters,
        counter_origin_clusters=counter_clusters,
        support_lineages=support_lineages,
        counter_lineages=counter_lineages,
        strong_support_lineage_count=strong_support_count,
        quoted_support_count=quoted_support_count,
        counterevidence_note=counter_note,
        has_conflict=has_conflict,
        lineage_conflicts=lineage_conflicts,
        blockers=_unique(blockers),
        warnings=_unique(warnings),
    )


def _is_accessible_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _valid_iso_datetime(value: str) -> bool:
    if not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _parse_completions(
    values: Iterable[RoundCompletion | Mapping[str, Any] | SearchRound | str],
) -> tuple[tuple[RoundCompletion, ...], tuple[str, ...]]:
    completions: list[RoundCompletion] = []
    blockers: list[str] = []
    for position, value in enumerate(values):
        if isinstance(value, RoundCompletion):
            completions.append(value)
        elif isinstance(value, Mapping):
            try:
                completions.append(RoundCompletion.from_mapping(value))
            except ValueError as exc:
                blockers.append(f"round completion[{position}] is invalid: {exc}")
        else:
            blockers.append(
                f"round completion[{position}] is an unaudited declaration; "
                "provide RoundCompletion with queries_checked and completed_at or receipt_ids"
            )
    return tuple(completions), _unique(blockers)


def _audit_round_completions(
    document: CaseDocument,
    values: Iterable[RoundCompletion | Mapping[str, Any] | SearchRound | str],
) -> tuple[tuple[RoundCompletion, ...], tuple[str, ...], tuple[str, ...]]:
    completions, parse_blockers = _parse_completions(values)
    blockers = list(parse_blockers)
    valid_query_ids: dict[SearchRound, set[str]] = {round_: set() for round_ in SearchRound}

    try:
        plan = build_query_plan(document)
        expected = {
            round_: {query.id for query in plan.for_round(round_)} for round_ in SearchRound
        }
    except ValueError as exc:
        blockers.append(f"cannot audit verification rounds: {exc}")
        expected = {round_: set() for round_ in SearchRound}

    for position, completion in enumerate(completions):
        queries = _unique(completion.queries_checked)
        auditable = True
        if not queries:
            blockers.append(f"round completion[{position}] has no queries_checked")
            auditable = False
        if completion.completed_at and not _valid_iso_datetime(completion.completed_at):
            blockers.append(f"round completion[{position}] has invalid completed_at")
            auditable = False
        if not completion.completed_at and not _unique(completion.receipt_ids):
            blockers.append(
                f"round completion[{position}] needs completed_at or at least one receipt_id"
            )
            auditable = False
        unknown = sorted(set(queries) - expected[completion.round])
        if unknown:
            blockers.append(
                f"round completion[{position}] has query IDs outside {completion.round.value}: "
                + ", ".join(unknown)
            )
            auditable = False
        if auditable:
            valid_query_ids[completion.round].update(queries)

    completed_rounds: list[str] = []
    for round_ in SearchRound:
        missing = sorted(expected[round_] - valid_query_ids[round_])
        if missing or not expected[round_]:
            blockers.append(
                f"verification round {round_.value} lacks audited query coverage: "
                + (", ".join(missing) if missing else "query plan unavailable")
            )
        else:
            completed_rounds.append(round_.value)
    return completions, tuple(completed_rounds), _unique(blockers)


def evaluate_stop_gate(
    case: CaseDocument | Mapping[str, Any],
    assessments: Iterable[ClaimAssessment] | None = None,
    completed_rounds: Iterable[
        RoundCompletion | Mapping[str, Any] | SearchRound | str
    ] | None = None,
) -> StopGate:
    """Apply the protocol stop conditions without requiring uncertainty to vanish."""

    document = _as_document(case)
    sources = document.index.sources
    claim_assessments = tuple(
        assessments
        if assessments is not None
        else (assess_claim(claim, sources) for claim in document.claims)
    )
    if completed_rounds is None:
        provenance_rounds = document.provenance.get("verification_rounds", [])
        completed_rounds = provenance_rounds if isinstance(provenance_rounds, list) else []
    completions, rounds, round_blockers = _audit_round_completions(
        document, completed_rounds
    )
    blockers: list[str] = list(round_blockers)
    warnings: list[str] = []

    for collection, duplicate_ids in document.index.duplicate_ids.items():
        blockers.append(f"duplicate IDs in {collection}: {', '.join(duplicate_ids)}")
    for source in document.sources:
        source_id = str(source.get("id", "?"))
        if not source.get("origin_cluster"):
            blockers.append(f"source {source_id} has no origin_cluster")
        if not _is_accessible_url(source.get("url")):
            blockers.append(f"source {source_id} has no accessible http(s) URL")
    global_lineage_conflicts = _lineage_conflicts(tuple(sources), sources)
    blockers.extend(f"source ledger lineage conflict: {item}" for item in global_lineage_conflicts)

    for assessment in claim_assessments:
        blockers.extend(
            f"claim {assessment.claim_id}: {message}" for message in assessment.blockers
        )
        warnings.extend(
            f"claim {assessment.claim_id}: {message}" for message in assessment.warnings
        )

    claim_ids = set(document.index.claims)
    linked_complete_gaps: dict[str, list[str]] = {}
    for gap in document.unresolved_questions:
        gap_id = str(gap.get("id", "?"))
        required = ("question", "searched", "next_query", "impact")
        missing = [field for field in required if not gap.get(field)]
        if missing:
            blockers.append(f"gap {gap_id} lacks audit fields: {', '.join(missing)}")
        raw_claim_ids = gap.get("claim_ids", [])
        if raw_claim_ids is None:
            raw_claim_ids = []
        if not isinstance(raw_claim_ids, list) or not all(
            isinstance(claim_id, str) and claim_id for claim_id in raw_claim_ids
        ):
            blockers.append(f"gap {gap_id}: claim_ids must be an array of non-empty IDs")
            raw_claim_ids = []
        unknown_claim_ids = sorted(set(raw_claim_ids) - claim_ids)
        if unknown_claim_ids:
            blockers.append(
                f"gap {gap_id} references unknown claims: {', '.join(unknown_claim_ids)}"
            )
        if not missing:
            for claim_id in _unique(raw_claim_ids):
                if claim_id in claim_ids:
                    linked_complete_gaps.setdefault(claim_id, []).append(gap_id)

    for assessment in claim_assessments:
        if (
            assessment.importance == "critical"
            and assessment.declared_status == "unverified"
            and not linked_complete_gaps.get(assessment.claim_id)
        ):
            blockers.append(
                f"critical unverified claim {assessment.claim_id} requires a complete gap "
                "whose claim_ids explicitly references it"
            )
    if document.unresolved_questions and not document.provenance.get("researched_at"):
        blockers.append("provenance.researched_at is required when unresolved gaps exist")

    for decision in document.decisions:
        decision_id = str(decision.get("id", "?"))
        if not decision.get("alternatives"):
            blockers.append(f"decision {decision_id} has no contemporaneous alternatives")
        if not decision.get("alternative_explanations"):
            blockers.append(f"decision {decision_id} has no alternative explanations")
        if not decision.get("falsifier"):
            blockers.append(f"decision {decision_id} has no falsifier")

    freeze = document.as_dict().get("fact_freeze", {})
    if isinstance(freeze, Mapping) and freeze.get("status") == "frozen" and blockers:
        warnings.append("fact pack is marked frozen although the evidence stop gate is not ready")

    return StopGate(
        ready=not blockers,
        completed_rounds=rounds,
        round_completions=completions,
        blockers=_unique(blockers),
        warnings=_unique(warnings),
    )


def verify_case(
    case: CaseDocument | Mapping[str, Any],
    completed_rounds: Iterable[
        RoundCompletion | Mapping[str, Any] | SearchRound | str
    ] | None = None,
) -> VerificationReport:
    """Run source clustering, claim assessment, and the research stop gate."""

    document = _as_document(case)
    source_clusters = build_source_clusters(document)
    claims = tuple(assess_claim(claim, document.index.sources) for claim in document.claims)
    stop_gate = evaluate_stop_gate(document, claims, completed_rounds)
    return VerificationReport(source_clusters, claims, stop_gate)
