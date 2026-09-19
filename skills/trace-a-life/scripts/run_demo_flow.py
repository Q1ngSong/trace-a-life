#!/usr/bin/env python3
"""Run the local demo chain from a saved ToolResult to a Chinese HTML page.

This is a thin integration boundary, not a new research engine.  The host (or
an offline fixture) supplies the ToolCall/ToolResult; the script persists the
collection result, runs the existing local factcheck and deterministic
business-analysis builders, then renders a copy of the case.  The input case
is never modified.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from ingest_collection import _call, _read, _result  # noqa: E402
from render_case import render  # noqa: E402
from xray_person.analysis import build_analysis_proposal, write_analysis_proposal  # noqa: E402
from xray_person.collection import ingest_tool_result  # noqa: E402
from xray_person.research import evaluate_collection_coverage, write_factcheck_report  # noqa: E402


class DemoFlowBlocked(RuntimeError):
    """The demo has evidence gaps and may only continue as an explicit draft."""


def _source_projection(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project a collection candidate into the renderer's source index."""

    source_id = str(record.get("source_id", ""))
    return {
        "id": source_id,
        "url": record.get("url") or "",
        "title": record.get("title") or source_id,
        "publisher": record.get("provider") or "host collection",
        "published_at": record.get("accessed_at") or "",
        "role": "discovery",
        "origin_cluster": f"collection:{record.get('provider') or 'host'}",
        "collection_status": record.get("collection_status") or "reported-only",
        "epistemic_status": "evidence-candidate",
    }


def _merge_sources(case: Mapping[str, Any], records: list[Mapping[str, Any]]) -> dict[str, Any]:
    data = copy.deepcopy(dict(case))
    existing = data.get("sources", [])
    if not isinstance(existing, list):
        existing = []
    by_id = {
        item.get("id"): item
        for item in existing
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    for record in records:
        projected = _source_projection(record)
        if projected["id"] and projected["id"] not in by_id:
            existing.append(projected)
            by_id[projected["id"]] = projected
    data["sources"] = existing
    return data


def run_demo_flow(
    case_path: str | Path,
    call_path: str | Path,
    result_path: str | Path,
    *,
    mode: str,
    output_dir: str | Path | None = None,
    accessed_at: str | None = None,
    allow_draft: bool = False,
) -> dict[str, Any]:
    source_case = Path(case_path).resolve()
    if source_case.name != "case.json":
        source_case = source_case / "case.json"
    original = json.loads(source_case.read_text(encoding="utf-8"))
    if not isinstance(original, Mapping):
        raise ValueError("case must contain a JSON object")

    destination = Path(output_dir or source_case.parent / "runs" / "demo-integration").resolve()
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    call = _call(_read(Path(call_path)))
    result = _result(_read(Path(result_path)), call)
    collection = ingest_tool_result(
        destination,
        call,
        result,
        mode=mode,
        accessed_at=accessed_at,
    )
    collection_records = [record.to_dict() for record in collection.records]
    case_data = _merge_sources(original, collection_records)
    output_case = destination / "case.json"
    output_case.write_text(
        json.dumps(case_data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    factcheck_path = write_factcheck_report(
        output_case,
        collection_dir=destination / "research" / "collection",
    )
    factcheck = json.loads(factcheck_path.read_text(encoding="utf-8"))
    coverage = evaluate_collection_coverage(
        case_data,
        collection_dir=destination / "research" / "collection",
    )
    coverage_path = destination / "research" / "collection-coverage.json"
    coverage_path.write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not coverage["ready"] and not allow_draft:
        manifest_path = destination / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "xray-demo-flow/2",
                    "result_status": "blocked",
                    "mode": collection.mode.value,
                    "input_case": str(source_case),
                    "output_case": str(output_case),
                    "collection_coverage": str(coverage_path),
                    "factcheck": {"path": str(factcheck_path), "summary": factcheck.get("summary", {})},
                    "analysis": None,
                    "page": None,
                    "blockers": coverage["blockers"],
                    "notes": ["formal analysis and page generation stopped before evidence coverage completed"],
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        raise DemoFlowBlocked("; ".join(coverage["blockers"]))
    proposal = build_analysis_proposal(case_data)
    proposal_path = write_analysis_proposal(destination / "analysis" / "proposal.json", proposal)
    page_path = destination / "site" / "index.html"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(render(case_data), encoding="utf-8")

    manifest = {
        "schema_version": "xray-demo-flow/2",
        "result_status": "ready" if coverage["ready"] else "draft",
        "mode": collection.mode.value,
        "input_case": str(source_case),
        "output_case": str(output_case),
        "collection": {
            "receipt_status": collection.receipt.status,
            "receipt_id": collection.receipt.receipt_id,
            "source_ids": [record.source_id for record in collection.records],
            "source_statuses": sorted({record.collection_status for record in collection.records}),
            "raw_path": str(collection.raw_path),
            "sources_path": str(collection.sources_path),
        },
        "factcheck": {
            "path": str(factcheck_path),
            "summary": factcheck.get("summary", {}),
        },
        "collection_coverage": {
            "path": str(coverage_path),
            "ready": coverage["ready"],
            "blockers": coverage["blockers"],
        },
        "analysis": {"path": str(proposal_path), "summary": proposal.get("summary", {})},
        "page": str(page_path),
        "notes": [
            "collection candidates are not factual claims until existing verification accepts them",
            "live still requires an executor-attested ToolResult; this command never performs network I/O",
            "result_status=draft is preview-only and was allowed explicitly with --allow-draft",
        ],
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path, help="case.json or case directory")
    parser.add_argument("--call", required=True, type=Path, help="saved ToolCall JSON")
    parser.add_argument("--result", required=True, type=Path, help="saved ToolResult JSON")
    parser.add_argument("--mode", choices=("live", "replay"), required=True)
    parser.add_argument("--output", type=Path, help="isolated output directory")
    parser.add_argument("--accessed-at", help="override source access time")
    parser.add_argument(
        "--allow-draft",
        action="store_true",
        help="continue to a clearly labeled preview when collection coverage is incomplete",
    )
    args = parser.parse_args()
    try:
        manifest = run_demo_flow(
            args.case,
            args.call,
            args.result,
            mode=args.mode,
            output_dir=args.output,
            accessed_at=args.accessed_at,
            allow_draft=args.allow_draft,
        )
    except DemoFlowBlocked as error:
        raise SystemExit(f"Demo flow blocked: {error}") from error
    except (OSError, ValueError, TypeError) as error:
        raise SystemExit(f"Demo flow failed: {error}") from error
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
