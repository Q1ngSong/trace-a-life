from __future__ import annotations

from copy import deepcopy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = ROOT / "skills" / "trace-a-life"
EXAMPLE = ROOT / "examples" / "fictional-founder" / "case.json"
sys.path.insert(0, str(SKILL_ROOT))

from xray_person.analysis import build_analysis_proposal, load_analysis_input  # noqa: E402


class BusinessAnalysisProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def test_proposal_is_deterministic_and_does_not_mutate_case(self) -> None:
        original = deepcopy(self.case)
        first = build_analysis_proposal(self.case)
        second = build_analysis_proposal(self.case)
        self.assertEqual(first, second)
        self.assertEqual(original, self.case)
        self.assertEqual("xray-business-analysis/1", first["schema_version"])

    def test_six_themes_preserve_explicit_epistemic_layers(self) -> None:
        proposal = build_analysis_proposal(self.case)

        self.assertEqual(
            {"origins", "expansion", "decisions", "contexts", "relationships", "wealth"},
            set(proposal["themes"]),
        )
        origins = proposal["themes"]["origins"]
        self.assertEqual({"event:event-003"}, {item["id"] for item in origins["verified_facts"]})
        self.assertEqual(
            {"event:event-001", "event:event-002"},
            {item["id"] for item in origins["self_reports"]},
        )
        expansion = proposal["themes"]["expansion"]
        self.assertEqual(
            {"event:event-004", "event:event-005"},
            {item["id"] for item in expansion["verified_facts"]},
        )
        decisions = proposal["themes"]["decisions"]
        self.assertEqual(
            {"decision:decision-002", "event:event-007"},
            {item["id"] for item in decisions["verified_facts"]},
        )
        self.assertEqual(
            {"decision:decision-001", "decision:decision-003", "event:event-006"},
            {item["id"] for item in decisions["inferences"]},
        )
        self.assertIn("event:event-007", {item["id"] for item in decisions["verified_facts"]})
        self.assertIn("event:event-006", {item["id"] for item in decisions["inferences"]})
        self.assertIn("event:event-008", {item["id"] for item in proposal["themes"]["wealth"]["verified_facts"]})

    def test_unlabelled_context_and_wealth_records_stay_unknown(self) -> None:
        proposal = build_analysis_proposal(self.case)
        self.assertEqual(3, len(proposal["themes"]["contexts"]["unknowns"]))
        self.assertEqual(5, len(proposal["themes"]["wealth"]["unknowns"]))
        self.assertTrue(
            all(
                item["classification_reason"].startswith("no explicit")
                for item in proposal["themes"]["wealth"]["unknowns"]
            )
        )

    def test_claim_evidence_and_unresolved_questions_remain_traceable(self) -> None:
        proposal = build_analysis_proposal(self.case)
        self.assertEqual(3, len(proposal["claim_evidence"]["verified_facts"]))
        self.assertEqual(1, len(proposal["claim_evidence"]["self_reports"]))
        self.assertEqual(1, len(proposal["claim_evidence"]["unknowns"]))
        self.assertEqual("unresolved_question:gap-001", proposal["unresolved_questions"][0]["id"])
        self.assertIn(
            "unresolved_question:gap-001",
            proposal["themes"]["origins"]["unresolved_question_ids"],
        )
        self.assertEqual(["src-001"], proposal["claim_evidence"]["verified_facts"][0]["source_ids"])

    def test_public_results_envelope_is_supported_without_promoting_missing_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.json"
            path.write_text(
                json.dumps(
                    {"schema_version": "public-research/1", "kind": "人物", "data": self.case},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            proposal = build_analysis_proposal(load_analysis_input(path))
        self.assertEqual("林深", proposal["subject"]["name"])
        self.assertEqual(6, proposal["summary"]["theme_count"])

    def test_cli_writes_proposal_to_requested_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "analysis" / "proposal.json"
            subprocess.run(
                [
                    sys.executable,
                    str(SKILL_ROOT / "scripts" / "build_analysis.py"),
                    str(EXAMPLE),
                    "--output",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            written = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual("xray-business-analysis/1", written["schema_version"])
        self.assertEqual(6, written["summary"]["theme_count"])


if __name__ == "__main__":
    unittest.main()
