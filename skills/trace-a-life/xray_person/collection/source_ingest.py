"""Persist host-returned collection results as case-local research material.

This module intentionally does not perform HTTP, MCP, or browser I/O. The host
executes a :class:`~xray_person.integrations.ToolCall` and passes its
``ToolResult`` here. The generic receipt normalizer records that handoff; this
module only stores the receipt and the material carried by the result.

``live`` means an executor-attested result was returned. ``replay`` means a
previously saved result is being ingested offline. Neither mode promotes a
source to a verified claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from xray_person.integrations import (
    IntegrationContractError,
    Receipt,
    ToolCall,
    ToolResult,
    canonical_json,
    normalize_receipt,
)


class IngestError(ValueError):
    """Raised when a returned source cannot be persisted as a source record."""


class CollectionMode(str, Enum):
    LIVE = "live"
    REPLAY = "replay"


_URL_FIELDS = ("url", "source_url", "canonical_url", "link", "href", "uri")
_FILE_FIELDS = ("file_path", "path", "file", "filename")
_TITLE_FIELDS = ("title", "name", "document_title", "headline")
_TIME_FIELDS = ("accessed_at", "retrieved_at", "fetched_at", "collected_at", "timestamp")
_CONTENT_FIELDS = ("content", "body", "text", "full_text", "document_text")
_SNIPPET_FIELDS = ("snippet", "excerpt", "summary", "description")
_ID_FIELDS = ("source_id", "id", "document_id", "resource_id")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _iso(value: str, field: str) -> str:
    value = _text(value)
    if not value:
        raise IngestError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IngestError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IngestError(f"{field} must include a timezone")
    return value


def _first_text(record: Mapping[str, Any], fields: Iterable[str]) -> str:
    for field in fields:
        value = _text(record.get(field))
        if value:
            return value
    return ""


def _url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise IngestError("source url must be an absolute http(s) URL")
    return value


def _file_path(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise IngestError("file_path must be case-relative and cannot contain ..")
    return value


def _slug(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return safe[:80] or "source"


def _source_id(record: Mapping[str, Any], *, url: str, file_path: str, title: str, content: str) -> str:
    explicit = _first_text(record, _ID_FIELDS)
    if explicit:
        return explicit
    identity = {"url": url, "file_path": file_path, "title": title, "content": content}
    return f"src-{hashlib.sha256(canonical_json(identity).encode('utf-8')).hexdigest()[:20]}"


@dataclass(frozen=True)
class SourceRecord:
    """A case-local source candidate, not a verified historical fact."""

    source_id: str
    title: str
    accessed_at: str
    content: str
    content_kind: str
    collection_status: str
    url: str | None = None
    file_path: str | None = None
    snippet: str | None = None
    provider: str = "web"
    receipt_id: str = ""
    query: str | None = None

    def __post_init__(self) -> None:
        validate_source_record(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "url": self.url,
            "file_path": self.file_path,
            "title": self.title,
            "accessed_at": self.accessed_at,
            "content": self.content,
            "snippet": self.snippet,
            "content_kind": self.content_kind,
            "collection_status": self.collection_status,
            "provider": self.provider,
            "receipt_id": self.receipt_id,
            "query": self.query,
            "epistemic_status": "evidence-candidate",
        }


def validate_source_record(record: Mapping[str, Any]) -> None:
    """Validate the minimum fields required before a source enters a case."""

    if not _text(record.get("source_id")):
        raise IngestError("source_id is required")
    if not _text(record.get("title")):
        raise IngestError("title is required")
    url = _text(record.get("url"))
    file_path = _text(record.get("file_path"))
    if bool(url) == bool(file_path):
        raise IngestError("exactly one of url or file_path is required")
    if url:
        _url(url)
    if file_path:
        _file_path(file_path)
    _iso(_text(record.get("accessed_at")), "accessed_at")
    if not _text(record.get("content")) and not _text(record.get("snippet")):
        raise IngestError("content or snippet is required")
    if _text(record.get("collection_status")) not in {"completed", "reported-only", "accepted"}:
        raise IngestError("collection_status must be completed, accepted, or reported-only")


def _decode_payload(payload: Any) -> Any:
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return payload
    return payload


def _candidate_records(value: Any) -> list[Mapping[str, Any]]:
    """Find source-like mappings in common host result envelopes."""

    if isinstance(value, Mapping):
        has_source_fields = any(
            key in value for key in (*_URL_FIELDS, *_FILE_FIELDS, *_CONTENT_FIELDS, *_SNIPPET_FIELDS)
        )
        records: list[Mapping[str, Any]] = [value] if has_source_fields else []
        for key, child in value.items():
            if key in {"items", "results", "documents", "sources", "records", "data", "hits"}:
                records.extend(_candidate_records(child))
        return records
    if isinstance(value, (list, tuple)):
        records: list[Mapping[str, Any]] = []
        for child in value:
            records.extend(_candidate_records(child))
        return records
    return []


def _fallback_record(payload: Any, call: ToolCall) -> Mapping[str, Any]:
    text = payload if isinstance(payload, str) else canonical_json(payload)
    arguments = dict(call.arguments)
    urls = arguments.get("start_urls") or arguments.get("urls") or []
    return {
        "url": urls[0] if len(urls) == 1 else None,
        "title": f"{call.action} result",
        "content": text,
        "snippet": text[:500],
        "query": ", ".join(str(item) for item in arguments.get("queries", []) if item),
    }


def _record_from_mapping(
    raw: Mapping[str, Any],
    *,
    call: ToolCall,
    receipt: Receipt,
    accessed_at: str,
) -> SourceRecord:
    url = _first_text(raw, _URL_FIELDS)
    file_path = _first_text(raw, _FILE_FIELDS)
    title = _first_text(raw, _TITLE_FIELDS)
    content = _first_text(raw, _CONTENT_FIELDS)
    snippet = _first_text(raw, _SNIPPET_FIELDS)
    if not title:
        raise IngestError("title is required for every returned source")
    if not url and not file_path:
        raise IngestError("every returned source needs a URL or file_path")
    if not content and not snippet:
        raise IngestError("every returned source needs content or snippet")
    source_id = _source_id(raw, url=url, file_path=file_path, title=title, content=content or snippet)
    query = _first_text(raw, ("query", "search_query")) or _first_text(dict(call.arguments), ("query",))
    return SourceRecord(
        source_id=source_id,
        url=url or None,
        file_path=file_path or None,
        title=title,
        accessed_at=_iso(_first_text(raw, _TIME_FIELDS) or accessed_at, "accessed_at"),
        content=content,
        snippet=snippet or None,
        content_kind="body" if content else "snippet",
        collection_status=receipt.status,
        provider=receipt.provider,
        receipt_id=receipt.receipt_id,
        query=query or None,
    )


@dataclass(frozen=True)
class IngestReport:
    mode: CollectionMode
    receipt: Receipt
    records: tuple[SourceRecord, ...]
    raw_path: Path
    sources_path: Path
    receipt_path: Path


def _append_jsonl(path: Path, records: Iterable[Mapping[str, Any]], *, key: str) -> None:
    existing: dict[str, dict[str, Any]] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                if isinstance(item, Mapping) and _text(item.get(key)):
                    existing[str(item[key])] = dict(item)
    for record in records:
        existing[str(record[key])] = dict(record)
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in existing.values()),
        encoding="utf-8",
    )


def ingest_tool_result(
    case_dir: str | Path,
    call: ToolCall,
    result: ToolResult,
    *,
    mode: CollectionMode | str,
    accessed_at: str | None = None,
) -> IngestReport:
    """Persist one host result and its normalized collection receipt."""

    try:
        selected_mode = mode if isinstance(mode, CollectionMode) else CollectionMode(mode)
    except ValueError as exc:
        raise IngestError("mode must be live or replay") from exc
    try:
        receipt = normalize_receipt(call, result)
    except IntegrationContractError as exc:
        raise IngestError(str(exc)) from exc
    if selected_mode is CollectionMode.LIVE and receipt.status != "completed":
        raise IngestError("live ingestion requires an attested completed collection result")
    # Replay may carry an attested receipt from the original live run.  The
    # mode records how this invocation happened; it does not erase the original
    # receipt's execution status or pretend that replay performed network I/O.

    when = accessed_at or (
        receipt.attestation.executed_at
        if receipt.attestation
        else datetime.now(timezone.utc).isoformat()
    )
    payload = _decode_payload(result.payload)
    raw_records = _candidate_records(payload) or [_fallback_record(payload, call)]
    records = tuple(
        _record_from_mapping(item, call=call, receipt=receipt, accessed_at=when)
        for item in raw_records
    )

    collection_dir = Path(case_dir) / "research" / "collection"
    sources_dir = collection_dir / "sources"
    collection_dir.mkdir(parents=True, exist_ok=True)
    sources_dir.mkdir(parents=True, exist_ok=True)
    raw_path = collection_dir / "raw" / f"{_slug(receipt.receipt_id)}.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        json.dumps(
            {
                "schema_version": "xray-collection-raw/1",
                "mode": selected_mode.value,
                "call": call.to_dict(),
                "result": result.to_dict(),
                "receipt": receipt.to_dict(),
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    sources_path = collection_dir / "sources.jsonl"
    _append_jsonl(sources_path, (record.to_dict() for record in records), key="source_id")
    for record in records:
        body = record.content or record.snippet or ""
        (sources_dir / f"{_slug(record.source_id)}.md").write_text(
            f"# {record.title}\n\n"
            f"- source_id: `{record.source_id}`\n"
            f"- url: {record.url or ''}\n"
            f"- file_path: {record.file_path or ''}\n"
            f"- accessed_at: `{record.accessed_at}`\n"
            f"- collection_status: `{record.collection_status}`\n"
            f"- receipt_id: `{record.receipt_id}`\n\n"
            f"{body}\n",
            encoding="utf-8",
        )
    receipt_path = collection_dir / "receipts.jsonl"
    _append_jsonl(receipt_path, [{**receipt.to_dict(), "mode": selected_mode.value}], key="receipt_id")
    return IngestReport(selected_mode, receipt, records, raw_path, sources_path, receipt_path)


__all__ = [
    "CollectionMode",
    "IngestError",
    "IngestReport",
    "SourceRecord",
    "ingest_tool_result",
    "validate_source_record",
]
