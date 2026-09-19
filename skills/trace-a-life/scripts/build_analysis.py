#!/usr/bin/env python3
"""Build a deterministic, source-bounded business-analysis proposal."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from xray_person.analysis import build_analysis_proposal, load_analysis_input, write_analysis_proposal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path, help="case.json or public results.json")
    parser.add_argument(
        "--output",
        type=Path,
        help="proposal path (defaults to <case-dir>/analysis/proposal.json)",
    )
    args = parser.parse_args()
    try:
        data = load_analysis_input(args.case)
        proposal = build_analysis_proposal(data)
    except (OSError, ValueError, TypeError) as error:
        raise SystemExit(f"Analysis proposal failed: {error}") from error
    output = args.output or args.case.parent / "analysis" / "proposal.json"
    write_analysis_proposal(output, proposal)
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
