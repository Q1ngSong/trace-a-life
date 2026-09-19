#!/usr/bin/env python3
"""Ingest a saved host collection ToolResult into a case directory.

The script is deliberately a file boundary: it does not execute a network
client.  Use ``--mode live`` only for a result that carries an executor
attestation; use ``--mode replay`` for an offline fixture or a previous run.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from xray_person.collection import ingest_tool_result  # noqa: E402
from xray_person.integrations import (  # noqa: E402
    ExecutorAttestation,
    ToolCall,
    ToolResult,
)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _call(data: dict[str, Any]) -> ToolCall:
    return ToolCall.create(
        provider=str(data["provider"]),
        action=str(data["action"]),
        tool_name=str(data["tool_name"]),
        arguments=data.get("arguments", {}),
    )


def _result(data: dict[str, Any], call: ToolCall) -> ToolResult:
    attestation_data = data.get("attestation")
    attestation = None
    if isinstance(attestation_data, dict):
        attestation = ExecutorAttestation(
            executor=str(attestation_data["executor"]),
            attempt_id=str(attestation_data["attempt_id"]),
            transport=str(attestation_data["transport"]),
            executed_at=str(attestation_data["executed_at"]),
            request_sha256=str(attestation_data["request_sha256"]),
            response_sha256=str(attestation_data["response_sha256"]),
            status_code=int(attestation_data["status_code"]),
            server_version=str(attestation_data["server_version"]),
        )
    if bool(data.get("ok")):
        if attestation is not None:
            return ToolResult.attested_success(call, data.get("payload"), attestation)
        return ToolResult.success(call, data.get("payload"))
    return ToolResult.failure(
        call,
        str(data.get("error") or "host returned failure"),
        data.get("payload"),
        attestation=attestation,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path, help="case directory")
    parser.add_argument("--call", required=True, type=Path, help="ToolCall JSON")
    parser.add_argument("--result", required=True, type=Path, help="ToolResult JSON")
    parser.add_argument("--mode", choices=("live", "replay"), required=True)
    parser.add_argument("--accessed-at", help="override source access time (ISO-8601 with timezone)")
    args = parser.parse_args()
    call = _call(_read(args.call))
    result = _result(_read(args.result), call)
    report = ingest_tool_result(
        args.case,
        call,
        result,
        mode=args.mode,
        accessed_at=args.accessed_at,
    )
    print(
        json.dumps(
            {
                "mode": report.mode.value,
                "receipt": report.receipt.to_dict(),
                "source_ids": [record.source_id for record in report.records],
                "raw_path": str(report.raw_path),
                "sources_path": str(report.sources_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
