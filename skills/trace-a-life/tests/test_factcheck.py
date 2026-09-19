from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT))

from xray_person.research import (  # noqa: E402
    build_factcheck_report,
    build_identity_candidates,
    evaluate_collection_coverage,
)


def case_data() -> dict:
    return {
        "schema_version": "trace-a-life/1",
        "subject": {"name": "张三", "aliases": ["张三（企业家）", "张三"]},
        "scope": {},
        "provenance": {},
        "chapters": [
            {"id": "origins"},
            {"id": "ascent"},
            {"id": "turning-point"},
            {"id": "legacy"},
        ],
        "sources": [
            {
                "id": "src-1",
                "url": "https://example.com/article",
                "title": "报道",
                "origin_cluster": "example",
                "role": "independent",
            }
        ],
        "claims": [
            {
                "id": "claim-1",
                "status": "self_reported",
                "importance": "supporting",
                "evidence": [
                    {"source_id": "src-1", "quote": "张三在1998年创办公司。"},
                ],
            },
            {
                "id": "claim-2",
                "status": "unverified",
                "importance": "critical",
                "counterevidence": "尚未找到同期登记。",
                "evidence": [
                    {"source_id": "src-1", "quote": "这段文字不存在。"},
                ],
            },
        ],
        "events": [],
        "decisions": [],
        "contexts": [],
        "relationships": {"nodes": [], "edges": []},
        "unresolved_questions": [],
        "fact_freeze": {"status": "draft"},
    }


class FactcheckTests(unittest.TestCase):
    def test_quote_is_checked_against_saved_body_and_missing_quote_is_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "sources.jsonl").write_text(
                json.dumps(
                    {
                        "source_id": "src-1",
                        "url": "https://example.com/article",
                        "title": "报道",
                        "accessed_at": "2026-09-19T10:00:00+08:00",
                        "content": "张三在 1998 年创办公司。",
                        "snippet": "张三在 1998 年创办公司。",
                        "collection_status": "reported-only",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            report = build_factcheck_report(case_data(), collection_dir=root)
            statuses = [item["status"] for item in report["quote_checks"]]
            self.assertEqual(["matched", "not_found"], statuses)
            self.assertEqual(1, report["summary"]["matched_quotes"])
            visibility = {item["claim_id"]: item for item in report["claim_visibility"]}
            self.assertEqual("self_reported", visibility["claim-1"]["declared_status"])
            self.assertTrue(visibility["claim-1"]["status_is_preserved"])
            self.assertEqual("unverified", visibility["claim-2"]["declared_status"])

    def test_missing_source_and_empty_quote_are_distinct(self) -> None:
        data = case_data()
        data["claims"][0]["evidence"] = [
            {"source_id": "missing", "quote": "未知来源"},
            {"source_id": "src-1", "quote": ""},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "sources.jsonl").write_text(
                json.dumps({"source_id": "src-1", "title": "报道"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            report = build_factcheck_report(data, collection_dir=root)
            self.assertEqual(
                ["missing_source", "empty_quote"],
                [item["status"] for item in report["quote_checks"][:2]],
            )

    def test_semantic_anchor_accepts_ellipsis_without_calling_it_a_direct_quote(self) -> None:
        data = case_data()
        data["claims"][0]["evidence"] = [
            {"source_id": "src-1", "quote": "实际控制人……变更为俞浩"},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "sources.jsonl").write_text(
                json.dumps(
                    {
                        "source_id": "src-1",
                        "title": "公告",
                        "content": "公告称实际控制人由原股东变更为俞浩。",
                        "context_status": "sufficient",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            report = build_factcheck_report(data, collection_dir=root)
            check = report["quote_checks"][0]
            self.assertEqual("candidate_match", check["status"])
            self.assertEqual("pending_review", check["alignment_status"])
            self.assertEqual("ellipsis_fragments", check["alignment_method"])
            self.assertEqual("sufficient", check["context_status"])
            self.assertEqual(1, report["summary"]["candidate_quotes"])

    def test_collection_coverage_blocks_before_formal_result_when_source_is_missing(self) -> None:
        data = case_data()
        for claim in data["claims"]:
            for evidence in claim["evidence"]:
                evidence["review"] = {
                    "alignment_status": "direct_support",
                    "context_reviewed": True,
                    "reviewed_by": "research-agent",
                    "reviewed_at": "2026-09-19T10:00:00+08:00",
                    "note": "主体、时间与上下文一致。",
                }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "sources.jsonl").write_text(
                json.dumps(
                    {
                        "source_id": "src-1",
                        "title": "报道",
                        "collection_status": "reported-only",
                        "context_status": "sufficient",
                        "content": "张三在1998年创办公司。这段文字不存在。",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            coverage = evaluate_collection_coverage(data, collection_dir=root)
            self.assertFalse(coverage["ready"])
            self.assertTrue(any("completed/accepted" in blocker for blocker in coverage["blockers"]))
            self.assertEqual("captured_needs_review", coverage["source_progress"]["src-1"]["progress"])
            self.assertEqual(1, coverage["source_progress_counts"]["captured_needs_review"])

            source = json.loads((root / "sources.jsonl").read_text(encoding="utf-8"))
            source["collection_status"] = "completed"
            (root / "sources.jsonl").write_text(
                json.dumps(source, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            ready = evaluate_collection_coverage(data, collection_dir=root)
            self.assertTrue(ready["ready"], ready)
            self.assertEqual("completed", ready["source_progress"]["src-1"]["progress"])

            (root / "sources.jsonl").write_text("", encoding="utf-8")
            missing = evaluate_collection_coverage(data, collection_dir=root)
            self.assertFalse(missing["ready"])
            self.assertEqual(["src-1"], missing["missing_source_ids"])
            self.assertEqual("not_attempted", missing["source_progress"]["src-1"]["progress"])

    def test_collection_progress_preserves_explicit_failure_and_access_block(self) -> None:
        data = case_data()
        data["claims"] = [data["claims"][0]]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "sources.jsonl").write_text(
                "\n".join(
                    json.dumps(
                        {
                            "source_id": source_id,
                            "collection_progress": progress,
                            "collection_status": "reported-only",
                        },
                        ensure_ascii=False,
                    )
                    for source_id, progress in (("src-1", "attempted_failed"), ("src-2", "blocked_access"))
                )
                + "\n",
                encoding="utf-8",
            )
            data["claims"][0]["evidence"] = [
                {"source_id": "src-1", "quote": "失败"},
                {"source_id": "src-2", "quote": "被阻断"},
            ]
            coverage = evaluate_collection_coverage(data, collection_dir=root)
            self.assertEqual("attempted_failed", coverage["source_progress"]["src-1"]["progress"])
            self.assertEqual("blocked_access", coverage["source_progress"]["src-2"]["progress"])

    def test_reviewed_candidate_anchor_can_pass_without_verbatim_copy(self) -> None:
        data = case_data()
        data["claims"] = [data["claims"][0]]
        data["claims"][0]["evidence"] = [
            {
                "source_id": "src-1",
                "quote": "实际控制人……变更为俞浩",
                "review": {
                    "alignment_status": "semantic_support",
                    "context_reviewed": True,
                    "reviewed_by": "research-agent",
                    "reviewed_at": "2026-09-19T10:00:00+08:00",
                    "note": "省略号只压缩了原句，主体、控制权变更动作和对象一致。",
                },
            }
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "sources.jsonl").write_text(
                json.dumps(
                    {
                        "source_id": "src-1",
                        "title": "公告",
                        "collection_status": "completed",
                        "context_status": "sufficient",
                        "content": "公告称实际控制人由原股东变更为俞浩。",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            coverage = evaluate_collection_coverage(data, collection_dir=root)
            self.assertTrue(coverage["ready"], coverage)
            self.assertEqual(1, coverage["factcheck_summary"]["candidate_quotes"])

    def test_identity_candidates_remain_explicit_and_unmerged(self) -> None:
        data = case_data()
        data["identity_candidates"] = [
            {
                "candidate_id": "cn-1",
                "name": "张三",
                "status": "rejected",
                "basis": "同名但城市不一致",
            },
            {
                "candidate_id": "cn-2",
                "name": "张三",
                "status": "accepted",
                "basis": "公司官网职位和年份一致",
            },
        ]
        candidates = build_identity_candidates(data)
        self.assertEqual(["rejected", "accepted"], [item["status"] for item in candidates[:2]])
        self.assertEqual(["cn-1", "cn-2"], [item["candidate_id"] for item in candidates[:2]])
        self.assertEqual(2, sum(item["name"] == "张三" for item in candidates))


if __name__ == "__main__":
    unittest.main()
