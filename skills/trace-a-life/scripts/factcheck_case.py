#!/usr/bin/env python3
"""Check claim quotes against case-local collection material."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from xray_person.research import write_factcheck_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path, help="case directory or case.json")
    parser.add_argument("--collection-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    case_path = args.case if args.case.name == "case.json" else args.case / "case.json"
    report_path = write_factcheck_report(
        case_path,
        collection_dir=args.collection_dir,
        output_path=args.output,
    )
    print(report_path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
