from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT))

from xray_person.collection import CollectionMode, IngestError, ingest_tool_result  # noqa: E402
from xray_person.integrations import (  # noqa: E402
    ExecutorAttestation,
    ToolCall,
    ToolResult,
)


def host_call(action: str, **arguments):
    return ToolCall.create(
        provider="host-web",
        action=action,
        tool_name=f"host_web_{action}",
        arguments=arguments,
    )


class CollectionIngestTests(unittest.TestCase):
    @staticmethod
    def attested_result(call, payload):
        attestation = ExecutorAttestation.create(
            call,
            payload,
            executor="test-mcp-host",
            attempt_id="attempt-1",
            transport="mcp",
            executed_at="2026-09-19T10:00:00+08:00",
            status_code=200,
            server_version="host-web-test",
        )
        return ToolResult.attested_success(call, payload, attestation)

    def test_replay_search_result_is_persisted_as_reported_only(self) -> None:
        call = host_call("search", queries=["测试人物 公司"])
        result = ToolResult.success(
            call,
            {
                "results": [
                    {
                        "id": "doc-1",
                        "url": "https://example.com/article",
                        "title": "测试人物访谈",
                        "snippet": "一段已保存的搜索摘要。",
                    }
                ]
            },
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            report = ingest_tool_result(
                temp_dir,
                call,
                result,
                mode=CollectionMode.REPLAY,
                accessed_at="2026-09-19T10:00:00+08:00",
            )
            self.assertEqual("reported-only", report.receipt.status)
            self.assertEqual("doc-1", report.records[0].source_id)
            self.assertTrue(report.sources_path.is_file())
            saved = report.sources_path.read_text(encoding="utf-8")
            self.assertIn("测试人物访谈", saved)
            self.assertIn("reported-only", saved)
            self.assertTrue(report.raw_path.is_file())

    def test_live_web_result_persists_body_and_attested_receipt(self) -> None:
        call = host_call(
            "capture", start_urls=["https://example.com/article"], workspace="case"
        )
        result = self.attested_result(
            call,
            {"url": "https://example.com/article", "title": "正文", "content": "完整正文。"},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            report = ingest_tool_result(temp_dir, call, result, mode="live")
            self.assertEqual("completed", report.receipt.status)
            self.assertEqual("正文", report.records[0].title)
            body_path = (
                Path(temp_dir)
                / "research"
                / "collection"
                / "sources"
                / "src-"
            )
            source_files = list(body_path.parent.glob("*.md"))
            self.assertEqual(1, len(source_files))
            self.assertIn("完整正文。", source_files[0].read_text(encoding="utf-8"))

    def test_invalid_source_is_rejected_before_writing(self) -> None:
        call = host_call("search", queries=["x"])
        result = ToolResult.success(call, {"results": [{"title": "无出处"}]})
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(IngestError):
                ingest_tool_result(
                    temp_dir,
                    call,
                    result,
                    mode="replay",
                    accessed_at="2026-09-19T10:00:00+08:00",
                )

    def test_live_mode_does_not_accept_unattested_result(self) -> None:
        call = host_call("search", queries=["x"])
        result = ToolResult.success(
            call,
            {"results": [{"url": "https://example.com", "title": "结果", "snippet": "摘要"}]},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(IngestError):
                ingest_tool_result(temp_dir, call, result, mode=CollectionMode.LIVE)

    def test_replay_can_rebuild_from_an_attested_previous_result(self) -> None:
        call = host_call(
            "capture", start_urls=["https://example.com/old"], workspace="case"
        )
        result = self.attested_result(
            call,
            {"url": "https://example.com/old", "title": "旧正文", "content": "已保存正文。"},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            report = ingest_tool_result(temp_dir, call, result, mode=CollectionMode.REPLAY)
            self.assertEqual("replay", report.mode.value)
            self.assertEqual("completed", report.receipt.status)


if __name__ == "__main__":
    unittest.main()
