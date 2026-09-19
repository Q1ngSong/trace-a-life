#!/usr/bin/env python3
"""Export a frozen Trace a Life case as a MiroFish seed document."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ALLOWED_CLAIMS = {"verified", "credible", "partial"}


def bullet(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values) if values else "- 无"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path)
    parser.add_argument("--question", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    data = json.loads(args.case.read_text(encoding="utf-8"))
    freeze = data.get("fact_freeze", {})
    if freeze.get("status") != "frozen" or not freeze.get("frozen_at"):
        raise SystemExit("MiroFish export requires fact_freeze.status=frozen and frozen_at")

    args.output.mkdir(parents=True, exist_ok=True)
    source_map = {source["id"]: source for source in data.get("sources", [])}
    node_map = {node["id"]: node for node in data.get("relationships", {}).get("nodes", [])}

    claims = [claim for claim in data.get("claims", []) if claim.get("status") in ALLOWED_CLAIMS]
    claim_lines = []
    for claim in claims:
        refs = [source_map[item["source_id"]]["title"] for item in claim.get("evidence", []) if item.get("source_id") in source_map]
        claim_lines.append(f"[{claim['status']}] {claim['text']}（来源：{'；'.join(refs) or '未列出'}）")

    relation_lines = []
    for edge in data.get("relationships", {}).get("edges", []):
        if edge.get("epistemic_status") != "fact":
            continue
        source = node_map.get(edge.get("source"), {}).get("label", edge.get("source", "?"))
        target = node_map.get(edge.get("target"), {}).get("label", edge.get("target", "?"))
        relation_lines.append(f"{source} —{edge.get('label', '关联')}→ {target}（{edge.get('period', '时期未明')}）")

    decision_lines = []
    for decision in data.get("decisions", []):
        known = "；".join(decision.get("known_at_time", [])) or "未记录"
        alternatives = "；".join(decision.get("alternatives", []))
        decision_lines.append(
            f"{decision.get('date', '')}｜{decision.get('title', '')}：{decision.get('decision', '')}\n"
            f"  当时已知：{known}\n  可选路径：{alternatives}\n  解释状态：{decision.get('interpretation_status', '')}"
        )

    context_lines = [
        f"{item.get('period', '')}｜{item.get('title', '')}：{item.get('summary', '')}"
        for item in data.get("contexts", [])
    ]
    gaps = [item.get("question", "") if isinstance(item, dict) else str(item) for item in data.get("unresolved_questions", [])]

    subject = data["subject"]
    scope = data.get("scope", {})
    content = f"""# MiroFish Seed: {subject['name']}

> 本文档是事实冻结包，不是完整传记。模拟结果不得反向用作历史证据。

## 元数据

- 研究对象：{subject['name']}
- 身份锚点：{subject.get('anchor', '')}
- 事实冻结时间：{freeze['frozen_at']}
- 导出时间：{datetime.now(timezone.utc).isoformat()}
- 调查目标：{scope.get('objective', '')}
- 推演问题：{args.question}

## 已核验或基本可信的原子事实

{bullet(claim_lines)}

## 有证据的关系

{bullet(relation_lines)}

## 关键决策与当时信息集

{bullet(decision_lines)}

## 社会、政策、行业与资本环境

{bullet(context_lines)}

## 已知缺口与禁止补全项

{bullet(gaps)}

不得把缺口推断为事实；不得生成私人住址、未成年子女、医疗或非公开家庭信息。

## 观察目标

请围绕以下内容输出情景，而非“真实预测概率”：

1. 不同参与者在既有利益与信息约束下可能采取的行动；
2. 一阶与二阶影响；
3. 哪些结论高度依赖输入假设；
4. 哪些现实材料或可观察信号能支持或推翻该情景；
5. 至少一个与主情景相反但同样合理的替代情景。
"""
    seed_path = args.output / "mirofish-seed.md"
    seed_path.write_text(content, encoding="utf-8")
    case_sha256 = hashlib.sha256(args.case.read_bytes()).hexdigest()
    seed_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    manifest = {
        "schema_version": "x-ray-mirofish-seed/1",
        "subject": subject["name"],
        "question": args.question,
        "fact_freeze_at": freeze["frozen_at"],
        "source_case": args.case.name,
        "source_case_sha256": case_sha256,
        "seed_file": seed_path.name,
        "seed_sha256": seed_sha256,
        "epistemic_status": "simulation-input",
        "allowed_claim_statuses": sorted(ALLOWED_CLAIMS),
    }
    (args.output / "simulation-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(seed_path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
