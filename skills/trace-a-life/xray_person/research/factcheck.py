"""Local evidence lookup, review coverage, and identity-candidate review.

The existing :mod:`verification` module decides what source roles and claim
statuses mean. String lookup only locates candidate passages; it never decides
whether a full claim is supported. Formal delivery also needs an explicit
Agent/human review of the current claims and source context. This module never
merges people or upgrades a claim status.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable, Mapping

from ..domain import CaseDocument
from .verification import COUNTER_STANCES, SUPPORT_STANCES, assess_claim


QUOTE_STATUSES = frozenset(
    {
        "matched",  # text located, NOT direct_support of the claim
        "candidate_match",
        "not_found",
        "missing_source",
        "empty_quote",
        "context_insufficient",
    }
)
IDENTITY_STATUSES = frozenset({"pending", "accepted", "rejected"})
COMPLETE_COLLECTION_STATUSES = frozenset({"completed", "accepted"})
COLLECTION_PROGRESS_STATUSES = frozenset(
    {
        "not_attempted",
        "attempted_failed",
        "blocked_access",
        "captured_needs_review",
        "completed",
    }
)
CLAIM_REVIEW_STATUSES = frozenset(
    {
        "direct_support",
        "semantic_support",
        "partial_support",
        "context_insufficient",
        "contradicted",
    }
)


class FactcheckError(ValueError):
    """Raised when local factcheck input is malformed."""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).replace("\u200b", "")
    return re.sub(r"\s+", " ", value).strip()


def _contains_quote(quote: str, text: str) -> bool:
    normalized_quote = _normalize_text(quote)
    normalized_text = _normalize_text(text)
    if normalized_quote in normalized_text:
        return True
    # Chinese transcription and browser extraction often insert spaces around
    # digits or punctuation. Keep the fallback deliberately conservative: it
    # only removes whitespace, never punctuation or characters.
    return normalized_quote.replace(" ", "") in normalized_text.replace(" ", "")


def _ellipsis_fragments_match(quote: str, text: str) -> bool:
    """Match a shortened/paraphrased anchor without treating the ellipsis as text.

    A quote such as ``实际控制人……变更为俞浩`` is not expected to occur
    literally in a source.  Every retained fragment still has to occur in the
    saved text. Omissions may hide negation or change meaning: only a reviewer
    can turn this candidate into claim support.
    """

    if not re.search(r"(?:…|⋯|\.{2,})", quote):
        return False
    normalized_text = _normalize_text(text).replace(" ", "")
    fragments = [
        re.sub(r"^[\s，,、；;：:。.!！？?]+|[\s，,、；;：:。.!！？?]+$", "", part)
        for part in re.split(r"(?:…|⋯|\.{2,})+", quote)
    ]
    fragments = [fragment.replace(" ", "") for fragment in fragments if fragment.strip()]
    return len(fragments) >= 2 and all(fragment in normalized_text for fragment in fragments)


def _char_ngrams(value: str, size: int = 3) -> set[str]:
    compact = re.sub(r"[^0-9A-Za-z\u3400-\u9fff]+", "", _normalize_text(value))
    return {compact[index : index + size] for index in range(max(0, len(compact) - size + 1))}


def _semantic_overlap(quote: str, text: str) -> bool:
    """Use a conservative lexical fallback for wording differences.

    This is a candidate detector, not an LLM truth judgment. Numeric tokens in
    the evidence anchor must still occur in the source, and at least four
    three-character chunks must overlap. The report labels the result as
    ``candidate_match``; neither this nor an exact hit establishes support.
    """

    quote_ngrams = _char_ngrams(quote)
    text_ngrams = _char_ngrams(text)
    if len(quote_ngrams) < 4:
        return False
    numeric_tokens = re.findall(r"\d+(?:\.\d+)?", quote)
    normalized_text = _normalize_text(text)
    if any(token not in normalized_text for token in numeric_tokens):
        return False
    overlap = len(quote_ngrams & text_ngrams)
    return overlap >= 4 and overlap / len(quote_ngrams) >= 0.45


def _context_status(source: Mapping[str, Any]) -> str:
    explicit = _text(source.get("context_status"))
    if explicit in {"sufficient", "limited", "insufficient", "unknown"}:
        return explicit
    return "unknown"


def _has_source_material(source: Mapping[str, Any]) -> bool:
    return any(_text(source.get(field)) for field in ("content", "snippet", "local_body"))


def _collection_progress(
    source: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Describe collection progress without confusing it with claim support.

    A missing local record is ``not_attempted`` because this module cannot
    infer whether a network request was made. ``attempted_failed`` and
    ``blocked_access`` therefore require an explicit upstream progress value;
    they are never guessed from absence alone.
    """

    if source is None:
        return {
            "progress": "not_attempted",
            "collection_status": None,
            "context_status": "unknown",
            "has_material": False,
            "reason": "no local source record",
        }
    explicit = _text(source.get("collection_progress"))
    if explicit in {"attempted_failed", "blocked_access"}:
        return {
            "progress": explicit,
            "collection_status": _text(source.get("collection_status")) or None,
            "context_status": _context_status(source),
            "has_material": _has_source_material(source),
            "reason": "explicit upstream collection progress",
        }
    collection_status = _text(source.get("collection_status"))
    if collection_status in {"failed", "attempted_failed"}:
        return {
            "progress": "attempted_failed",
            "collection_status": collection_status,
            "context_status": _context_status(source),
            "has_material": _has_source_material(source),
            "reason": "source receipt reports a failed attempt",
        }
    if collection_status in {"blocked_access", "access_blocked"}:
        return {
            "progress": "blocked_access",
            "collection_status": collection_status,
            "context_status": _context_status(source),
            "has_material": _has_source_material(source),
            "reason": "source receipt reports blocked access",
        }
    if (
        collection_status in COMPLETE_COLLECTION_STATUSES
        and _has_source_material(source)
        and _context_status(source) == "sufficient"
    ):
        progress = "completed"
        reason = "completed/accepted capture with material and sufficient context"
    else:
        progress = "captured_needs_review"
        reason = "local record exists but capture or context is incomplete"
    return {
        "progress": progress,
        "collection_status": collection_status or None,
        "context_status": _context_status(source),
        "has_material": _has_source_material(source),
        "reason": reason,
    }


def _safe_source_filename(source_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", source_id).strip(".-")[:80] or "source"


def _load_jsonl(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    records: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FactcheckError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(value, Mapping) or not _text(value.get("source_id")):
            raise FactcheckError(f"source record at {path}:{line_number} needs source_id")
        records[str(value["source_id"])] = dict(value)
    return records


def load_local_sources(collection_dir: str | Path) -> dict[str, dict[str, Any]]:
    """Load normalized collection records and fill missing content from Markdown."""

    root = Path(collection_dir)
    sources = _load_jsonl(root / "sources.jsonl")
    source_dir = root / "sources"
    for source_id, source in sources.items():
        if _text(source.get("content")):
            continue
        markdown = source_dir / f"{_safe_source_filename(source_id)}.md"
        if not markdown.is_file():
            continue
        text = markdown.read_text(encoding="utf-8")
        body = text.split("\n\n", 1)[1] if "\n\n" in text else text
        source["local_body"] = body.strip()
    return sources


@dataclass(frozen=True)
class QuoteCheck:
    claim_id: str
    source_id: str
    quote: str
    status: str
    matched_in: str | None = None
    source_path: str | None = None
    title: str | None = None
    url: str | None = None
    file_path: str | None = None
    alignment_method: str | None = None
    context_status: str = "unknown"
    alignment_status: str = "unresolved"
    evidence_index: int | None = None

    def __post_init__(self) -> None:
        if self.status not in QUOTE_STATUSES:
            raise FactcheckError(f"invalid quote check status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "source_id": self.source_id,
            "quote": self.quote,
            "status": self.status,
            "matched_in": self.matched_in,
            "source_path": self.source_path,
            "title": self.title,
            "url": self.url,
            "file_path": self.file_path,
            "alignment_method": self.alignment_method,
            "context_status": self.context_status,
            "alignment_status": self.alignment_status,
            "evidence_index": self.evidence_index,
        }


def check_quote(
    *,
    claim_id: str,
    source_id: str,
    quote: Any,
    sources: Mapping[str, Mapping[str, Any]],
    collection_dir: Path | None = None,
) -> QuoteCheck:
    quote_text = _text(quote)
    source = sources.get(source_id)
    if not source:
        return QuoteCheck(
            claim_id,
            source_id,
            quote_text,
            "missing_source",
            alignment_status="source_missing",
        )
    if not quote_text:
        return QuoteCheck(
            claim_id,
            source_id,
            quote_text,
            "empty_quote",
            title=_text(source.get("title")) or None,
            url=_text(source.get("url")) or None,
            file_path=_text(source.get("file_path")) or None,
            context_status=_context_status(source),
            alignment_status="context_insufficient",
        )

    candidates: list[tuple[str, str, str | None]] = []
    content = _text(source.get("content"))
    snippet = _text(source.get("snippet"))
    local_body = _text(source.get("local_body"))
    if content:
        candidates.append(("content", content, None))
    if snippet:
        candidates.append(("snippet", snippet, None))
    if local_body:
        candidates.append(("markdown", local_body, str(collection_dir / "sources")))
    matched_in = next(
        (kind for kind, value, _ in candidates if _contains_quote(quote_text, value)),
        None,
    )
    context_status = _context_status(source)
    if matched_in:
        status = "matched"
        alignment_method = "normalized_substring"
        alignment_status = "pending_review"
    else:
        semantic_pair = next(
            (
                (kind, value)
                for kind, value, _ in candidates
                if _ellipsis_fragments_match(quote_text, value) or _semantic_overlap(quote_text, value)
            ),
            None,
        )
        semantic_in = semantic_pair[0] if semantic_pair else None
        semantic_value = semantic_pair[1] if semantic_pair else ""
        matched_in = semantic_in
        status = "candidate_match" if semantic_in else "not_found"
        alignment_status = "pending_review"
        alignment_method = (
            "ellipsis_fragments"
            if semantic_in and _ellipsis_fragments_match(quote_text, semantic_value)
            else "conservative_char_ngram_overlap"
            if semantic_in
            else None
        )
    return QuoteCheck(
        claim_id,
        source_id,
        quote_text,
        status,
        matched_in=matched_in,
        source_path=(
            str(collection_dir / "sources" / f"{_safe_source_filename(source_id)}.md")
            if matched_in == "markdown" and collection_dir
            else None
        ),
        title=_text(source.get("title")) or None,
        url=_text(source.get("url")) or None,
        file_path=_text(source.get("file_path")) or None,
        alignment_method=alignment_method,
        context_status=context_status,
        alignment_status=alignment_status,
    )


def build_identity_candidates(case: CaseDocument | Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Build explicit identity candidates without merging same-name records."""

    data = case.as_dict() if isinstance(case, CaseDocument) else dict(case)
    subject = data.get("subject", {})
    if not isinstance(subject, Mapping):
        raise FactcheckError("subject must be an object")
    explicit = data.get("identity_candidates", [])
    if explicit is None:
        explicit = []
    if not isinstance(explicit, list):
        raise FactcheckError("identity_candidates must be an array")
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(explicit):
        if not isinstance(raw, Mapping):
            raise FactcheckError(f"identity_candidates[{index}] must be an object")
        name = _text(raw.get("name"))
        if not name:
            raise FactcheckError(f"identity_candidates[{index}].name is required")
        status = _text(raw.get("status")) or "pending"
        if status not in IDENTITY_STATUSES:
            raise FactcheckError(f"identity candidate {name}: invalid status {status}")
        candidate_id = _text(raw.get("candidate_id"))
        if not candidate_id:
            digest = hashlib.sha256(f"{index}:{name}".encode("utf-8")).hexdigest()[:12]
            candidate_id = f"identity-{digest}"
        result.append(
            {
                "candidate_id": candidate_id,
                "name": name,
                "kind": _text(raw.get("kind")) or "name",
                "status": status,
                "basis": _text(raw.get("basis")) or "explicit case candidate",
                "source_ids": list(raw.get("source_ids", []))
                if isinstance(raw.get("source_ids", []), list)
                else [],
            }
        )

    aliases = subject.get("aliases", [])
    if aliases is None:
        aliases = []
    if not isinstance(aliases, list):
        raise FactcheckError("subject.aliases must be an array")
    names = [_text(subject.get("name")), *[_text(item) for item in aliases]]
    for index, name in enumerate(name for name in names if name):
        if any(candidate["name"] == name for candidate in result):
            continue
        result.append(
            {
                "candidate_id": f"subject-name-{index}",
                "name": name,
                "kind": "subject-name" if index == 0 else "alias",
                "status": "pending",
                "basis": "subject name or alias; explicit review required",
                "source_ids": [],
            }
        )
    return tuple(result)


def build_factcheck_report(
    case: CaseDocument | Mapping[str, Any],
    *,
    collection_dir: str | Path,
) -> dict[str, Any]:
    """Check attached quotes and preserve existing claim-status visibility."""

    document = case if isinstance(case, CaseDocument) else CaseDocument.from_mapping(case)
    root = Path(collection_dir)
    sources = load_local_sources(root)
    quote_checks: list[QuoteCheck] = []
    visibility: list[dict[str, Any]] = []
    case_sources = document.index.sources
    for claim in document.claims:
        claim_id = str(claim.get("id", "?"))
        for evidence_index, evidence in enumerate(claim.get("evidence", [])):
            if not isinstance(evidence, Mapping):
                continue
            quote_checks.append(
                replace(
                    check_quote(
                        claim_id=claim_id,
                        source_id=_text(evidence.get("source_id")),
                        quote=evidence.get("quote"),
                        sources=sources,
                        collection_dir=root,
                    ),
                    evidence_index=evidence_index,
                )
            )
        assessment = assess_claim(claim, case_sources)
        visibility.append(
            {
                "claim_id": claim_id,
                "declared_status": assessment.declared_status,
                "has_conflict": assessment.has_conflict,
                "support_source_ids": list(assessment.support_source_ids),
                "counter_source_ids": list(assessment.counter_source_ids),
                "blockers": list(assessment.blockers),
                "warnings": list(assessment.warnings),
                "status_is_preserved": True,
            }
        )
    return {
        "schema_version": "xray-factcheck-report/2",
        "scope": "passage lookup only; not a semantic verdict or delivery approval",
        "quote_checks": [item.to_dict() for item in quote_checks],
        "identity_candidates": list(build_identity_candidates(document)),
        "claim_visibility": visibility,
        "summary": {
            "quote_count": len(quote_checks),
            "matched_quotes": sum(item.status == "matched" for item in quote_checks),
            "candidate_quotes": sum(item.status == "candidate_match" for item in quote_checks),
            "unmatched_quotes": sum(item.status != "matched" for item in quote_checks),
            "missing_source_evidence_count": sum(item.status == "missing_source" for item in quote_checks),
            "missing_source_count": len({item.source_id for item in quote_checks if item.status == "missing_source"}),
            "claim_count": len(document.claims),
            "identity_candidate_count": len(build_identity_candidates(document)),
        },
    }


def _referenced_source_ids(case: CaseDocument | Mapping[str, Any]) -> tuple[str, ...]:
    """Collect sources that must be present before a formal result is built."""

    data = case.as_dict() if isinstance(case, CaseDocument) else dict(case)
    values: list[str] = []
    for claim in data.get("claims", []) if isinstance(data.get("claims", []), list) else []:
        if not isinstance(claim, Mapping):
            continue
        evidence = claim.get("evidence", [])
        if isinstance(evidence, list):
            values.extend(
                str(item.get("source_id"))
                for item in evidence
                if isinstance(item, Mapping) and _text(item.get("source_id"))
            )
    for collection_name in ("events", "decisions", "contexts"):
        records = data.get(collection_name, [])
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, Mapping):
                continue
            source_ids = record.get("source_ids", [])
            if isinstance(source_ids, list):
                values.extend(str(item) for item in source_ids if _text(item))
    relationships = data.get("relationships", {})
    edges = relationships.get("edges", []) if isinstance(relationships, Mapping) else []
    if isinstance(edges, list):
        for edge in edges:
            if isinstance(edge, Mapping):
                source_ids = edge.get("source_ids", [])
                if isinstance(source_ids, list):
                    values.extend(str(item) for item in source_ids if _text(item))
    return tuple(dict.fromkeys(values))


def _claim_review_blockers(case: CaseDocument | Mapping[str, Any]) -> list[str]:
    """Require a human/Agent review record for every evidence item."""

    data = case.as_dict() if isinstance(case, CaseDocument) else dict(case)
    blockers: list[str] = []
    claims = data.get("claims", [])
    if not isinstance(claims, list):
        return ["claims must be an array before evidence review"]
    for claim in claims:
        if not isinstance(claim, Mapping):
            continue
        claim_id = _text(claim.get("id")) or "?"
        evidence = claim.get("evidence", [])
        if not isinstance(evidence, list):
            blockers.append(f"claim {claim_id} evidence is not an array")
            continue
        for position, item in enumerate(evidence):
            if not isinstance(item, Mapping):
                blockers.append(f"claim {claim_id} evidence[{position}] is not an object")
                continue
            source_id = _text(item.get("source_id")) or "?"
            review = item.get("review")
            if not isinstance(review, Mapping):
                blockers.append(f"claim {claim_id}:{source_id} has no claim-level review")
                continue
            alignment = _text(review.get("alignment_status"))
            if alignment not in CLAIM_REVIEW_STATUSES:
                blockers.append(f"claim {claim_id}:{source_id} has invalid alignment review")
            if review.get("context_reviewed") is not True:
                blockers.append(f"claim {claim_id}:{source_id} lacks context_reviewed=true")
            if not _text(review.get("reviewed_by")) or not _text(review.get("reviewed_at")):
                blockers.append(f"claim {claim_id}:{source_id} lacks reviewer and timestamp")
            if not _text(review.get("note")):
                blockers.append(f"claim {claim_id}:{source_id} lacks review note")
    return list(dict.fromkeys(blockers))


def _review_for_quote(
    case: CaseDocument | Mapping[str, Any],
    check: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    data = case.as_dict() if isinstance(case, CaseDocument) else dict(case)
    for claim in data.get("claims", []) if isinstance(data.get("claims", []), list) else []:
        if not isinstance(claim, Mapping) or _text(claim.get("id")) != _text(check.get("claim_id")):
            continue
        evidence = claim.get("evidence", [])
        if not isinstance(evidence, list):
            return None
        position = check.get("evidence_index")
        if not isinstance(position, int) or position < 0 or position >= len(evidence):
            return None
        item = evidence[position]
        return item.get("review") if isinstance(item, Mapping) and isinstance(item.get("review"), Mapping) else None
    return None


def _quote_requires_review(case: CaseDocument | Mapping[str, Any], check: Mapping[str, Any]) -> bool:
    """Return whether a located anchor still lacks an explicit claim review."""

    if check.get("status") not in {"matched", "candidate_match"}:
        return True
    review = _review_for_quote(case, check)
    return not (
        isinstance(review, Mapping)
        and _text(review.get("alignment_status")) in CLAIM_REVIEW_STATUSES
        and review.get("context_reviewed") is True
        and _text(review.get("reviewed_by"))
        and _text(review.get("reviewed_at"))
        and _text(review.get("note"))
    )


def evaluate_collection_coverage(
    case: CaseDocument | Mapping[str, Any],
    *,
    collection_dir: str | Path,
) -> dict[str, Any]:
    """Return the pre-result collection gate.

    The gate is intentionally stricter than a draft-page preview: every source
    referenced by claims/events/decisions must have a completed or accepted
    local capture, non-empty material, sufficient context, and an aligned
    evidence anchor. This prevents analysis from silently running on only a
    subset of the case.
    """

    document = case if isinstance(case, CaseDocument) else CaseDocument.from_mapping(case)
    root = Path(collection_dir)
    sources = load_local_sources(root)
    referenced_ids = _referenced_source_ids(document)
    missing_ids = sorted(source_id for source_id in referenced_ids if source_id not in sources)
    incomplete_ids = sorted(
        source_id
        for source_id in referenced_ids
        if source_id in sources
        and _text(sources[source_id].get("collection_status")) not in COMPLETE_COLLECTION_STATUSES
    )
    empty_ids = sorted(
        source_id
        for source_id in referenced_ids
        if source_id in sources
        and not any(_text(sources[source_id].get(field)) for field in ("content", "snippet", "local_body"))
    )
    context_ids = sorted(
        source_id
        for source_id in referenced_ids
        if source_id in sources and _context_status(sources[source_id]) != "sufficient"
    )
    report = build_factcheck_report(document, collection_dir=root)
    source_progress = {
        source_id: _collection_progress(sources.get(source_id))
        for source_id in referenced_ids
    }
    progress_counts = {
        status: sum(item["progress"] == status for item in source_progress.values())
        for status in sorted(COLLECTION_PROGRESS_STATUSES)
    }
    unresolved_checks = [
        item
        for item in report["quote_checks"]
        if _quote_requires_review(document, item)
    ]
    review_blockers = _claim_review_blockers(document)
    blockers: list[str] = []
    if missing_ids:
        blockers.append("referenced sources are not collected: " + ", ".join(missing_ids))
    if incomplete_ids:
        blockers.append("referenced sources are not completed/accepted: " + ", ".join(incomplete_ids))
    if empty_ids:
        blockers.append("referenced sources have no saved content: " + ", ".join(empty_ids))
    if context_ids:
        blockers.append("referenced sources lack sufficient context review: " + ", ".join(context_ids))
    if unresolved_checks:
        blockers.append(
            "evidence anchors still require claim-level review: "
            + ", ".join(f"{item['claim_id']}:{item['source_id']}" for item in unresolved_checks)
        )
    blockers.extend(review_blockers)
    return {
        "schema_version": "xray-collection-coverage/1",
        "ready": not blockers,
        "referenced_source_ids": list(referenced_ids),
        "captured_source_ids": sorted(sources),
        "missing_source_ids": missing_ids,
        "incomplete_source_ids": incomplete_ids,
        "empty_source_ids": empty_ids,
        "insufficient_context_source_ids": context_ids,
        "source_progress": source_progress,
        "source_progress_counts": progress_counts,
        "unresolved_evidence": unresolved_checks,
        "claim_review_blockers": review_blockers,
        "factcheck_summary": report["summary"],
        "blockers": blockers,
    }


def write_factcheck_report(
    case_path: str | Path,
    *,
    collection_dir: str | Path | None = None,
    output_path: str | Path | None = None,
) -> Path:
    case_file = Path(case_path)
    document = CaseDocument.from_path(case_file)
    case_dir = case_file.parent
    report = build_factcheck_report(
        document,
        collection_dir=collection_dir or case_dir / "research" / "collection",
    )
    destination = Path(output_path or case_dir / "research" / "factcheck" / "report.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    identity_path = destination.parent / "identity-candidates.json"
    identity_path.write_text(
        json.dumps(report["identity_candidates"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


__all__ = [
    "FactcheckError",
    "COLLECTION_PROGRESS_STATUSES",
    "IDENTITY_STATUSES",
    "QUOTE_STATUSES",
    "QuoteCheck",
    "build_factcheck_report",
    "build_identity_candidates",
    "check_quote",
    "evaluate_collection_coverage",
    "load_local_sources",
    "write_factcheck_report",
]
