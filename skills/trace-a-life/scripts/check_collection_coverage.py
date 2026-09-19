#!/usr/bin/env python3
"""Block formal results until referenced evidence has been collected and reviewed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from xray_person.domain import CaseDocument  # noqa: E402
from xray_person.research import evaluate_collection_coverage  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path, help="case.json or case directory")
    parser.add_argument("--collection-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    case_path = args.case if args.case.name == "case.json" else args.case / "case.json"
    case = CaseDocument.from_path(case_path)
    coverage = evaluate_collection_coverage(case, collection_dir=args.collection_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(coverage, ensure_ascii=False, indent=2))
    return 0 if coverage["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
