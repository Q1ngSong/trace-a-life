from __future__ import annotations

from copy import deepcopy
import json
import sys
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT))

from xray_person.domain import CaseDocument  # noqa: E402
from xray_person.research import (  # noqa: E402
    RESEARCH_LANES,
    RoundCompletion,
    SearchRound,
    build_query_plan,
    verify_case,
)


def base_case() -> dict:
    return {
        "schema_version": "trace-a-life/1",
        "subject": {
            "name": "测试人物",
            "aliases": ["Test Person", "测试人物"],
            "anchor": "测试公司 1998–2024",
        },
        "scope": {
            "time_range": "1987–2024",
            "jurisdictions": ["中国", "中国"],
        },
        "provenance": {"researched_at": "2026-08-31"},
        "sources": [
            {
                "id": "src-1",
                "url": "https://registry.example/source-1",
                "origin_cluster": "registry-1",
                "role": "primary",
            },
            {
                "id": "src-2",
                "url": "https://news.example/source-2",
                "origin_cluster": "news-2",
                "role": "independent",
            },
        ],
        "claims": [
            {
                "id": "claim-1",
                "text": "一个可单独核验的陈述。",
                "importance": "critical",
                "status": "credible",
                "counterevidence": "已完成反向检索，细节见查询记录。",
                "evidence": [
                    {"source_id": "src-1", "quote": "登记原文。"},
                    {"source_id": "src-2", "quote": "独立报道原文。"},
                ],
            }
        ],
        "events": [
            {
                "id": "event-1",
                "date": "1998-04-18",
                "source_ids": ["src-1"],
                "claim_ids": ["claim-1"],
            }
        ],
        "decisions": [
            {
                "id": "decision-1",
                "source_ids": ["src-1"],
                "alternatives": ["维持原方案"],
                "alternative_explanations": ["融资约束"],
                "falsifier": "若同期文件显示没有融资约束，则重估解释。",
            }
        ],
        "contexts": [{"id": "context-1", "source_ids": ["src-2"]}],
        "relationships": {
            "nodes": [{"id": "person-1", "label": "测试人物"}],
            "edges": [
                {
                    "id": "edge-1",
                    "source": "person-1",
                    "target": "person-1",
                    "source_ids": ["src-1"],
                }
            ],
        },
        "unresolved_questions": [],
        "fact_freeze": {"status": "draft"},
    }


def round_completions(data: dict) -> tuple[RoundCompletion, ...]:
    plan = build_query_plan(data)
    return tuple(
        RoundCompletion(
            round=round_,
            queries_checked=tuple(query.id for query in plan.for_round(round_)),
            completed_at="2026-08-31T12:00:00+08:00",
        )
        for round_ in SearchRound
    )


class DomainAndResearchTests(unittest.TestCase):
    def test_domain_access_and_query_plan_are_stable(self) -> None:
        raw = base_case()
        document = CaseDocument.from_mapping(raw)
        raw["subject"]["name"] = "被外部修改"

        self.assertEqual("测试人物", document.subject["name"])
        document.subject["aliases"].append("外部尝试修改")
        self.assertNotIn("外部尝试修改", document.subject["aliases"])
        self.assertEqual("src-1", document.sources[0]["id"])
        self.assertEqual("claim-1", document.claims[0]["id"])
        self.assertEqual("event-1", document.events[0]["id"])
        self.assertEqual("decision-1", document.decisions[0]["id"])
        self.assertEqual("context-1", document.contexts[0]["id"])
        self.assertEqual("person-1", document.relationship_nodes[0]["id"])
        self.assertEqual("edge-1", document.relationship_edges[0]["id"])
        self.assertEqual("src-1", document.index.sources["src-1"]["id"])
        self.assertEqual((), document.interviews)
        self.assertEqual((), document.oral_disclosures)

        first = build_query_plan(document)
        second = build_query_plan(document)
        self.assertEqual(first, second)
        self.assertEqual(7, len(RESEARCH_LANES))
        self.assertEqual(21, len(first.queries))
        self.assertEqual(7, len(first.for_round(SearchRound.COUNTER)))
        self.assertEqual(3, len(first.for_lane("decisions")))
        self.assertEqual(("1987", "1998", "2024"), first.time_anchors)
        self.assertIn('"Test Person"', first.queries[0].query)
        self.assertEqual(("测试公司",), first.identity_anchors)
        self.assertTrue(all('"测试公司"' in query.query for query in first.queries))

        parenthesized = base_case()
        parenthesized["subject"]["anchor"] = "南浦制造（虚构）· 1998–2024"
        parenthesized_plan = build_query_plan(parenthesized)
        self.assertEqual(("南浦制造（虚构）",), parenthesized_plan.identity_anchors)
        self.assertTrue(
            all('"南浦制造（虚构）"' in query.query for query in parenthesized_plan.queries)
        )

    def test_oral_history_is_read_only_and_indexed(self) -> None:
        data = base_case()
        data["oral_history"] = {
            "summary": "两档访谈。",
            "interviews": [{"id": "interview-1", "title": "长访谈"}],
            "disclosures": [{"id": "oral-1", "interview_id": "interview-1"}],
        }
        document = CaseDocument.from_mapping(data)

        self.assertEqual("interview-1", document.interviews[0]["id"])
        self.assertEqual("oral-1", document.oral_disclosures[0]["id"])
        self.assertEqual("interview-1", document.index.interviews["interview-1"]["id"])
        self.assertEqual("oral-1", document.index.oral_disclosures["oral-1"]["id"])

    def test_normal_case_reaches_stop_gate_without_changing_status(self) -> None:
        data = base_case()
        report = verify_case(data, completed_rounds=round_completions(data))
        assessment = report.claim("claim-1")

        self.assertIsNotNone(assessment)
        assert assessment is not None
        self.assertEqual("credible", assessment.declared_status)
        self.assertEqual(2, assessment.independent_support_count)
        self.assertEqual(2, len(report.source_clusters))
        self.assertTrue(report.stop_gate.ready, report.stop_gate.blockers)
        self.assertEqual(report.as_dict(), report.to_dict())
        self.assertEqual(assessment.as_dict(), assessment.to_dict())
        json.dumps(report.as_dict(), ensure_ascii=False)

    def test_explicit_support_counter_conflict_blocks_overconfident_status(self) -> None:
        data = base_case()
        data["sources"].append(
            {
                "id": "src-3",
                "url": "https://court.example/source-3",
                "origin_cluster": "court-3",
                "role": "primary",
            }
        )
        data["claims"][0]["evidence"].append(
            {"source_id": "src-3", "quote": "相反记录。", "stance": "counter"}
        )

        report = verify_case(data, completed_rounds=round_completions(data))
        assessment = report.claim("claim-1")

        assert assessment is not None
        self.assertTrue(assessment.has_conflict)
        self.assertEqual(1, assessment.independent_counter_count)
        self.assertEqual("credible", assessment.declared_status)
        self.assertFalse(report.stop_gate.ready)
        self.assertTrue(any("conflict unresolved" in item for item in report.stop_gate.blockers))

    def test_same_origin_reposts_count_as_one_independent_source(self) -> None:
        data = base_case()
        data["sources"][1]["origin_cluster"] = "registry-1"

        report = verify_case(data, completed_rounds=round_completions(data))
        assessment = report.claim("claim-1")

        assert assessment is not None
        self.assertEqual(1, assessment.independent_support_count)
        self.assertEqual(1, len(report.source_clusters))
        self.assertFalse(report.stop_gate.ready)
        self.assertTrue(any("strong support" in item for item in report.stop_gate.blockers))

    def test_same_canonical_url_cannot_fake_two_independent_sources(self) -> None:
        data = base_case()
        data["sources"][1]["url"] = data["sources"][0]["url"]

        report = verify_case(data, completed_rounds=round_completions(data))
        assessment = report.claim("claim-1")

        assert assessment is not None
        self.assertEqual(1, assessment.independent_support_count)
        self.assertTrue(assessment.lineage_conflicts)
        self.assertFalse(report.stop_gate.ready)
        self.assertTrue(any("lineage conflict" in item for item in report.stop_gate.blockers))

    def test_self_report_and_discovery_cannot_be_declared_factual(self) -> None:
        for role in ("self_report", "discovery"):
            for status in ("verified", "credible"):
                with self.subTest(role=role, status=status):
                    data = base_case()
                    for source in data["sources"]:
                        source["role"] = role
                    data["claims"][0]["status"] = status
                    if status == "verified":
                        data["claims"][0]["evidence"] = data["claims"][0]["evidence"][:1]

                    report = verify_case(data, completed_rounds=round_completions(data))
                    assessment = report.claim("claim-1")

                    assert assessment is not None
                    self.assertEqual(0, assessment.strong_support_lineage_count)
                    self.assertEqual(status, assessment.declared_status)
                    self.assertFalse(report.stop_gate.ready)
                    self.assertTrue(
                        any("self_report/discovery" in item for item in report.stop_gate.blockers)
                    )

    def test_round_enums_without_query_receipts_do_not_complete_rounds(self) -> None:
        data = base_case()
        report = verify_case(data, completed_rounds=tuple(SearchRound))

        self.assertFalse(report.stop_gate.ready)
        self.assertEqual((), report.stop_gate.completed_rounds)
        self.assertTrue(any("unaudited declaration" in item for item in report.stop_gate.blockers))

    def test_structured_round_audit_can_be_loaded_from_provenance(self) -> None:
        data = base_case()
        audit = [
            completion.as_dict() for completion in round_completions(data)
        ]
        audit[-1]["completed_at"] = ""
        audit[-1]["receipt_ids"] = ["receipt-causal-001"]
        data["provenance"]["verification_rounds"] = audit

        report = verify_case(data)

        self.assertTrue(report.stop_gate.ready, report.stop_gate.blockers)
        self.assertEqual(3, len(report.stop_gate.completed_rounds))

    def test_insufficient_evidence_stays_unverified_and_requires_audited_gap(self) -> None:
        data = base_case()
        data["claims"][0].update(status="unverified", evidence=[])

        completions = round_completions(data)
        missing_gap = verify_case(data, completed_rounds=completions)
        assessment = missing_gap.claim("claim-1")
        assert assessment is not None
        self.assertEqual("unverified", assessment.declared_status)
        self.assertFalse(missing_gap.stop_gate.ready)
        self.assertTrue(any("claim_ids explicitly" in item for item in missing_gap.stop_gate.blockers))

        with_gap = deepcopy(data)
        with_gap["unresolved_questions"] = [
            {
                "id": "gap-1",
                "question": "缺失的原始记录在哪里？",
                "searched": "已查登记库与报纸索引。",
                "next_query": "查找同期合同目录。",
                "impact": "影响起家叙事，不改变已登记身份。",
            }
        ]
        unrelated_gap = verify_case(with_gap, completed_rounds=completions)
        self.assertFalse(unrelated_gap.stop_gate.ready)
        self.assertTrue(any("claim_ids explicitly" in item for item in unrelated_gap.stop_gate.blockers))

        with_gap["unresolved_questions"][0]["claim_ids"] = ["claim-1"]
        audited_gap = verify_case(with_gap, completed_rounds=completions)
        self.assertTrue(audited_gap.stop_gate.ready, audited_gap.stop_gate.blockers)
        self.assertEqual("unverified", audited_gap.claim("claim-1").declared_status)


if __name__ == "__main__":
    unittest.main()
