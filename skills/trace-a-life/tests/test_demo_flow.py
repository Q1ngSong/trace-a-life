from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = ROOT / "skills" / "trace-a-life"
sys.path.insert(0, str(SKILL_ROOT))
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from ingest_collection import _read  # noqa: E402
from run_demo_flow import run_demo_flow  # noqa: E402


class DemoFlowTests(unittest.TestCase):
    def test_replay_runs_collection_factcheck_analysis_and_page_without_mutating_case(self) -> None:
        case_path = ROOT / "examples" / "fictional-founder" / "case.json"
        original = case_path.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            call_path = root / "call.json"
            result_path = root / "result.json"
            call_path.write_text(
                json.dumps(
                    {
                        "provider": "host-web",
                        "action": "web_crawl",
                        "tool_name": "host_web_capture",
                        "arguments": {"start_urls": ["https://example.com/founder"]},
                    }
                ),
                encoding="utf-8",
            )
            result_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "payload": {
                            "url": "https://example.com/founder",
                            "title": "Founder profile",
                            "content": "A saved source body for replay.",
                        },
                    }
                ),
                encoding="utf-8",
            )
            manifest = run_demo_flow(
                case_path,
                call_path,
                result_path,
                mode="replay",
                output_dir=root / "run",
                accessed_at="2026-09-19T10:00:00+08:00",
                allow_draft=True,
            )
            output = Path(manifest["page"])
            self.assertTrue(output.is_file())
            self.assertTrue(Path(manifest["collection"]["sources_path"]).is_file())
            self.assertTrue(Path(manifest["factcheck"]["path"]).is_file())
            self.assertTrue(Path(manifest["analysis"]["path"]).is_file())
            self.assertEqual("replay", manifest["mode"])
            self.assertEqual("draft", manifest["result_status"])
            self.assertFalse(manifest["collection_coverage"]["ready"])
            self.assertIn("Founder profile", output.read_text(encoding="utf-8"))
        self.assertEqual(original, case_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
