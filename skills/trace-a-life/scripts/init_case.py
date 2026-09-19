#!/usr/bin/env python3
"""Initialize a Trace a Life case without overwriting existing work."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", value)
    return value.strip("-") or "person-case"


def template(name: str, anchor: str) -> dict:
    today = date.today().isoformat()
    return {
        "schema_version": "trace-a-life/1",
        "subject": {
            "name": name,
            "anchor": anchor,
            "aliases": [],
            "lifespan": "",
            "known_for": "",
            "summary": "",
            "portrait": "",
        },
        "scope": {
            "objective": "重建公开商业生平并核验关键叙事",
            "time_range": "",
            "jurisdictions": [],
            "in_scope": ["公开商业活动", "公司与资本关系", "关键决策", "财富传承"],
            "out_of_scope": ["私人住址", "日常行踪", "未成年子女", "医疗与非公开隐私"],
        },
        "provenance": {
            "collection_backend": "unconfigured",
            "researched_at": today,
            "verification_rounds": [],
            "notes": "",
        },
        "report": {
            "title": name,
            "eyebrow": "商业生平调查",
            "dek": "",
            "thesis": "",
            "as_of": today,
        },
        "chapters": [
            {"id": "origins", "label": "起家", "years": "", "title": "", "summary": ""},
            {"id": "ascent", "label": "登顶", "years": "", "title": "", "summary": ""},
            {"id": "turning-point", "label": "转折", "years": "", "title": "", "summary": ""},
            {"id": "legacy", "label": "退出 / 传承", "years": "", "title": "", "summary": ""},
        ],
        "sources": [],
        "claims": [],
        "events": [],
        "decisions": [],
        "contexts": [],
        "relationships": {"nodes": [], "edges": []},
        "wealth": {"summary": "", "stages": [], "transfers": []},
        "oral_history": {
            "summary": "",
            "interviews": [],
            "disclosures": [],
            "analysis": {
                "schema_version": "xray-interview-analysis/1",
                "status": "pending",
                "manifest_path": "research/interviews/analysis-manifest.json",
                "manifest_sha256": "",
                "completed_at": None,
                "blockers": [],
            },
        },
        "unresolved_questions": [],
        "scenarios": [],
        "fact_freeze": {"status": "draft", "frozen_at": None, "notes": ""},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--anchor", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    output = args.output or Path("cases") / slugify(args.name)
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"Refusing to overwrite non-empty case directory: {output}")

    for child in (
        "evidence",
        "raw",
        "research",
        "analysis",
        "simulation",
        "site",
        "delivery",
        "pipeline/handoffs",
    ):
        (output / child).mkdir(parents=True, exist_ok=True)

    (output / "case.json").write_text(
        json.dumps(template(args.name, args.anchor), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "evidence" / "ledger.json").write_text(
        json.dumps({"version": 1, "sources": []}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "source-narrative.md").write_text(
        f"# 起始叙事：{args.name}\n\n> 在这里粘贴书中原文、页码或用户提供的准确摘要。\n",
        encoding="utf-8",
    )
    (output / "research-log.md").write_text(
        "# Research log\n\n"
        "| 日期 | 后端 | 查询 | 采用 | 排除与原因 | 下一步 |\n"
        "|---|---|---|---|---|---|\n",
        encoding="utf-8",
    )
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
