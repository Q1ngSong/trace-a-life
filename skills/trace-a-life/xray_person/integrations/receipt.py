"""Generic receipts for results returned by the host collection boundary.

The Skill does not ship a crawler, MCP client, or provider adapter. A host may
execute a web-search, browser-capture, or local-material call and hand the
result back as :class:`ToolResult`. This module records what was actually
returned without claiming that the payload is historically true.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .contracts import (
    Receipt,
    ToolCall,
    ToolResult,
    assert_result_matches,
    payload_sha256,
)


def _decode_json_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _records(value: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, Mapping):
        records: list[Mapping[str, Any]] = [value]
        for key in ("items", "results", "sources", "documents", "records", "data", "hits"):
            child = value.get(key)
            if isinstance(child, (Mapping, list, tuple)):
                records.extend(_records(child))
        return tuple(records)
    if isinstance(value, (list, tuple)):
        records: list[Mapping[str, Any]] = []
        for child in value:
            records.extend(_records(child))
        return tuple(records)
    return ()


def _identifiers(value: Any) -> tuple[tuple[str, ...], str | None]:
    resources: list[str] = []
    run_id: str | None = None
    for record in _records(value):
        if run_id is None:
            for key in ("run_id", "execution_id", "job_id"):
                if record.get(key) not in (None, ""):
                    run_id = str(record[key])
                    break
        for key in ("source_id", "document_id", "resource_id", "id"):
            item = record.get(key)
            if item not in (None, "") and not isinstance(item, (Mapping, list, tuple)):
                resources.append(str(item))
                break
    return tuple(dict.fromkeys(resources)), run_id


def normalize_receipt(call: ToolCall, result: ToolResult) -> Receipt:
    """Turn one host result into a generic auditable collection receipt."""

    assert_result_matches(call, result)
    digest = payload_sha256(result.payload)
    notes: list[str] = []

    if not result.ok:
        status = "failed"
        epistemic_status = "operational-error"
        notes.append(result.error or "executor reported failure")
    elif result.attestation is None:
        status = "reported-only"
        epistemic_status = "reported-only"
        notes.append("no executor attestation; local result cannot prove execution")
    elif not 200 <= result.attestation.status_code < 300:
        status = "failed"
        epistemic_status = "operational-error"
        notes.append(
            f"executor transport status was {result.attestation.status_code}, not success"
        )
    else:
        status = "completed"
        epistemic_status = "evidence-candidate"

    if status == "completed":
        resource_ids, run_id = _identifiers(_decode_json_text(result.payload))
    else:
        resource_ids, run_id = (), None

    return Receipt.create(
        call=call,
        status=status,
        epistemic_status=epistemic_status,
        payload_digest=digest,
        resource_ids=resource_ids,
        run_id=run_id,
        notes=tuple(notes),
        attestation=result.attestation,
    )


__all__ = ["normalize_receipt"]
