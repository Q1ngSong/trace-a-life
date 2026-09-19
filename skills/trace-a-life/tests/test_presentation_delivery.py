from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_DIR = REPO_ROOT / "examples" / "fictional-founder"
sys.path.insert(0, str(SKILL_ROOT))

from xray_person.delivery import (  # noqa: E402
    MANIFEST_SCHEMA_VERSION,
    build_delivery_manifest,
    run_delivery_qa,
    verify_delivery_manifest,
)
from xray_person.presentation import (  # noqa: E402
    attest_html,
    build_view_model,
    write_view_model,
)


class PresentationDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case_data = json.loads((EXAMPLE_DIR / "case.json").read_text(encoding="utf-8"))
        example_html = (EXAMPLE_DIR / "index.html").read_text(encoding="utf-8")
        self.raw_html = re.sub(
            r'<meta name="xray-(?:attestation-version|case-sha256|view-model-sha256)" content="[^"]*">',
            "",
            example_html,
        )

    def _attested(self, case_data=None, view_model=None, html=None) -> tuple[dict, str]:
        case_data = case_data or self.case_data
        view_model = view_model or build_view_model(case_data)
        return view_model, attest_html(html or self.raw_html, case_data, view_model)

    def test_complete_case_projects_without_losing_references(self) -> None:
        view_model, html = self._attested()

        self.assertEqual(
            ["origins", "ascent", "turning-point", "legacy"],
            [chapter["id"] for chapter in view_model["fact"]["life_stages"]],
        )
        self.assertEqual(
            ["src-003", "src-004"],
            view_model["traceability"]["content_refs"]["events:event-004"]["source_ids"],
        )
        self.assertEqual(
            ["claim-003"],
            view_model["traceability"]["content_refs"]["events:event-004"]["claim_ids"],
        )
        self.assertEqual(1, view_model["traceability"]["simulation"]["scenario_count"])
        report = run_delivery_qa(case_data=self.case_data, view_model=view_model, html=html)
        self.assertTrue(report.ok, report.to_dict())

    def test_oral_history_projects_with_traceable_interview_and_disclosure(self) -> None:
        changed = copy.deepcopy(self.case_data)
        changed["oral_history"] = {
            "summary": "两档长访谈中的口述史。",
            "interviews": [
                {
                    "id": "interview-001",
                    "title": "一场长访谈",
                    "published_at": "2026-08-31",
                    "source_ids": ["src-001"],
                }
            ],
            "disclosures": [
                {
                    "id": "oral-001",
                    "interview_id": "interview-001",
                    "timecode": "00:10:00",
                    "summary": "当事人披露的早期失败。",
                    "source_ids": ["src-001"],
                }
            ],
        }

        view_model = build_view_model(changed)
        self.assertEqual("interview-001", view_model["fact"]["oral_history"]["interviews"][0]["id"])
        self.assertEqual(
            ["src-001"],
            view_model["traceability"]["content_refs"]["oral_disclosures:oral-001"]["source_ids"],
        )

    def test_unattested_html_is_rejected(self) -> None:
        report = run_delivery_qa(
            case_data=self.case_data,
            view_model=build_view_model(self.case_data),
            html=self.raw_html,
        )
        self.assertIn(
            "html_attestation_missing_or_duplicate", {issue.code for issue in report.errors}
        )

    def test_stale_html_cannot_be_re_attested_to_bypass_semantic_sync(self) -> None:
        changed = copy.deepcopy(self.case_data)
        changed["subject"]["name"] = "完全不同的人物"
        changed["claims"][0]["text"] = "完全不同且尚未渲染的新主张。"
        view_model = build_view_model(changed)
        stale_but_attested = attest_html(self.raw_html, changed, view_model)
        report = run_delivery_qa(
            case_data=changed,
            view_model=view_model,
            html=stale_but_attested,
        )
        self.assertIn(
            "html_projection_stale_or_incomplete", {issue.code for issue in report.errors}
        )

    def test_evidence_gap_is_a_structured_error(self) -> None:
        broken = copy.deepcopy(self.case_data)
        broken["claims"][0]["evidence"] = []
        view_model, html = self._attested(broken)
        report = run_delivery_qa(case_data=broken, view_model=view_model, html=html)
        self.assertIn("claim_evidence_missing", {issue.code for issue in report.errors})

    def test_html_shaped_evidence_metadata_is_rejected(self) -> None:
        malicious = copy.deepcopy(self.case_data)
        malicious["sources"][0]["publisher"] = '<img src=x onerror="alert(1)">'
        view_model, html = self._attested(malicious)
        report = run_delivery_qa(case_data=malicious, view_model=view_model, html=html)
        self.assertIn("unsafe_evidence_text", {issue.code for issue in report.errors})

    def test_simulation_cannot_be_mixed_into_fact_timeline(self) -> None:
        view_model = build_view_model(self.case_data)
        view_model["fact"]["timeline"].append(copy.deepcopy(self.case_data["scenarios"][0]))
        html = attest_html(self.raw_html, self.case_data, view_model)
        report = run_delivery_qa(case_data=self.case_data, view_model=view_model, html=html)
        codes = {issue.code for issue in report.errors}
        self.assertIn("simulation_in_fact_layer", codes)
        self.assertIn("simulation_payload_in_fact_layer", codes)

    def test_scenario_summary_cannot_leak_into_fact_html(self) -> None:
        summary = self.case_data["scenarios"][0]["summary"]
        leaked = self.raw_html.replace(
            '<section class="section story" id="story">',
            f'<section class="section story" id="story"><p>{summary}</p>',
            1,
        )
        view_model, html = self._attested(html=leaked)
        report = run_delivery_qa(case_data=self.case_data, view_model=view_model, html=html)
        self.assertIn("simulation_in_fact_html", {issue.code for issue in report.errors})

    def test_scenario_projection_loss_is_rejected(self) -> None:
        view_model = build_view_model(self.case_data)
        view_model["simulation"]["scenarios"] = []
        html = attest_html(self.raw_html, self.case_data, view_model)
        report = run_delivery_qa(case_data=self.case_data, view_model=view_model, html=html)
        self.assertIn(
            "simulation_projection_set_mismatch", {issue.code for issue in report.errors}
        )

    def test_manifest_detects_html_drift(self) -> None:
        view_model, html = self._attested()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_path = root / "case.json"
            view_model_path = root / "view-model.json"
            html_path = root / "index.html"
            simulation_path = root / "simulation"
            simulation_path.mkdir()
            case_path.write_text(json.dumps(self.case_data, ensure_ascii=False), encoding="utf-8")
            write_view_model(view_model_path, view_model)
            html_path.write_text(html, encoding="utf-8")
            (simulation_path / "seed.md").write_text("simulation seed", encoding="utf-8")
            manifest = build_delivery_manifest(
                case_path=case_path,
                view_model_path=view_model_path,
                html_path=html_path,
                simulation_path=simulation_path,
                base_dir=root,
            )
            self.assertEqual("xray-delivery-manifest/1", MANIFEST_SCHEMA_VERSION)
            clean = run_delivery_qa(
                case_data=self.case_data,
                view_model=view_model,
                html=html,
                manifest=manifest,
                manifest_base_dir=root,
            )
            self.assertTrue(clean.ok, clean.to_dict())

            html_path.write_text(html + "<!-- drift -->", encoding="utf-8")
            drifted = run_delivery_qa(
                case_data=self.case_data,
                view_model=view_model,
                html=html,
                manifest=manifest,
                manifest_base_dir=root,
            )
            self.assertIn("manifest_digest_mismatch", {issue.code for issue in drifted.errors})

    def test_empty_manifest_and_invalid_algorithm_are_rejected(self) -> None:
        drifts = verify_delivery_manifest(
            {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "hash_algorithm": "md5",
                "artifacts": {},
            },
            ".",
        )
        codes = {drift.code for drift in drifts}
        self.assertIn("manifest_hash_algorithm_invalid", codes)
        self.assertIn("manifest_required_artifact_missing", codes)

    def test_manifest_rejects_empty_artifact_and_empty_simulation_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_path = root / "case.json"
            view_model_path = root / "view-model.json"
            html_path = root / "index.html"
            simulation_path = root / "simulation"
            simulation_path.mkdir()
            case_path.write_text("{}", encoding="utf-8")
            view_model_path.write_text("{}", encoding="utf-8")
            html_path.write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_delivery_manifest(
                    case_path=case_path,
                    view_model_path=view_model_path,
                    html_path=html_path,
                    base_dir=root,
                )
            html_path.write_text("<html></html>", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_delivery_manifest(
                    case_path=case_path,
                    view_model_path=view_model_path,
                    html_path=html_path,
                    simulation_path=simulation_path,
                    base_dir=root,
                )

    def test_manifest_rejects_root_path_alias_and_malformed_member_count(self) -> None:
        invalid = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "hash_algorithm": "sha256",
            "artifacts": {
                "case": {"path": ".", "kind": "file", "sha256": "0" * 64, "bytes": 1},
                "view_model": {
                    "path": "same.json",
                    "kind": "file",
                    "sha256": "0" * 64,
                    "bytes": 1,
                },
                "html": {
                    "path": "same.json",
                    "kind": "file",
                    "sha256": "0" * 64,
                    "bytes": 1,
                },
                "simulation": {
                    "path": "simulation",
                    "kind": "directory",
                    "sha256": "0" * 64,
                    "member_count": "oops",
                    "members": [],
                },
            },
        }
        drifts = verify_delivery_manifest(invalid, ".")
        codes = {drift.code for drift in drifts}
        self.assertIn("manifest_path_invalid", codes)
        self.assertIn("manifest_artifact_path_alias", codes)
        self.assertIn("manifest_empty_or_invalid_directory", codes)

    def test_top_level_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real.html"
            linked = root / "index.html"
            case_path = root / "case.json"
            view_model_path = root / "view-model.json"
            real.write_text("<html></html>", encoding="utf-8")
            linked.symlink_to(real.name)
            case_path.write_text("{}", encoding="utf-8")
            view_model_path.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_delivery_manifest(
                    case_path=case_path,
                    view_model_path=view_model_path,
                    html_path=linked,
                    base_dir=root,
                )


if __name__ == "__main__":
    unittest.main()
