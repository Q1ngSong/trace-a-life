"""Build a deterministic, three-round search plan across seven research lanes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping

from ..domain import CaseDocument


class SearchRound(str, Enum):
    SUPPORT = "A_support_chain"
    COUNTER = "B_counterevidence"
    CAUSAL = "C_causal_attack"


@dataclass(frozen=True)
class ResearchLane:
    id: str
    label: str
    support_terms: tuple[str, ...]
    counter_terms: tuple[str, ...]
    causal_terms: tuple[str, ...]


RESEARCH_LANES: tuple[ResearchLane, ...] = (
    ResearchLane(
        "identity",
        "身份与实体",
        ("工商登记", "任职", "股东", "法律实体"),
        ("同名", "曾用名", "更正", "注销"),
        ("身份排除", "登记号", "司法辖区", "控制人"),
    ),
    ResearchLane(
        "origins",
        "起家与资本来源",
        ("早期职业", "第一笔资本", "创办", "融资"),
        ("金额不符", "年份不符", "家庭资本", "交易对手"),
        ("政策窗口", "合作伙伴", "组织平台", "幸存者偏差"),
    ),
    ResearchLane(
        "ascent",
        "扩张与辉煌",
        ("收入", "利润", "并购", "市场份额"),
        ("修订", "亏损", "债务", "竞争者"),
        ("行业周期", "团队", "资本方", "渠道"),
    ),
    ResearchLane(
        "decisions",
        "关键决策",
        ("董事会", "交易", "战略", "当时"),
        ("反对", "备选方案", "推迟", "失败"),
        ("约束", "替代解释", "融资条件", "后见之明"),
    ),
    ResearchLane(
        "contexts",
        "社会与制度环境",
        ("政策", "监管", "宏观周期", "技术"),
        ("政策变化", "监管处罚", "市场下行", "舆论"),
        ("必要条件", "同期对照", "地域网络", "资本市场"),
    ),
    ResearchLane(
        "relationships",
        "人际与组织关系",
        ("共同任职", "共同投资", "合同", "董事"),
        ("冲突", "离职", "诉讼", "终止合作"),
        ("资本支持", "信息渠道", "执行团队", "控制关系"),
    ),
    ResearchLane(
        "decline_legacy",
        "危机、退出与财富传承",
        ("危机", "退出", "股权转让", "信托"),
        ("债务重组", "控制权变更", "争产", "监管安排"),
        ("主动变现", "代际交接", "受益权", "经营权"),
    ),
)


@dataclass(frozen=True)
class QuerySpec:
    id: str
    round: SearchRound
    lane_id: str
    lane_label: str
    query: str
    purpose: str

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "round": self.round.value,
            "lane_id": self.lane_id,
            "lane_label": self.lane_label,
            "query": self.query,
            "purpose": self.purpose,
        }


@dataclass(frozen=True)
class QueryPlan:
    subject_terms: tuple[str, ...]
    identity_anchors: tuple[str, ...]
    time_anchors: tuple[str, ...]
    jurisdictions: tuple[str, ...]
    queries: tuple[QuerySpec, ...]

    def for_round(self, round_: SearchRound) -> tuple[QuerySpec, ...]:
        return tuple(query for query in self.queries if query.round == round_)

    def for_lane(self, lane_id: str) -> tuple[QuerySpec, ...]:
        return tuple(query for query in self.queries if query.lane_id == lane_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject_terms": list(self.subject_terms),
            "identity_anchors": list(self.identity_anchors),
            "time_anchors": list(self.time_anchors),
            "jurisdictions": list(self.jurisdictions),
            "queries": [query.as_dict() for query in self.queries],
        }


def _unique_strings(values: list[Any]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = " ".join(value.split())
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return tuple(result)


def _quoted(value: str) -> str:
    return f'"{value.replace(chr(34), " ")}"'


def _year_anchors(case: CaseDocument) -> tuple[str, ...]:
    candidates: list[str] = []
    values: list[Any] = [
        case.subject.get("anchor"),
        case.scope.get("time_range"),
    ]
    values.extend(event.get("date") for event in case.events)
    for value in values:
        if isinstance(value, str):
            candidates.extend(re.findall(r"(?<!\d)(?:18|19|20)\d{2}(?!\d)", value))
    return tuple(sorted(set(candidates), key=int))


def _identity_anchors(case: CaseDocument) -> tuple[str, ...]:
    """Extract the entity/disambiguation part of the supplied identity anchor."""

    subject = case.subject
    values: list[Any] = []
    explicit = subject.get("identity_anchors", [])
    if isinstance(explicit, list):
        values.extend(explicit)
    anchor = subject.get("anchor")
    if isinstance(anchor, str):
        without_years = re.sub(r"(?<!\d)(?:18|19|20)\d{2}(?!\d)", " ", anchor)
        without_separators = re.sub(r"[\s·•|,，;/、:：–—-]+", " ", without_years)
        values.append(without_separators.strip(" .。"))
    return _unique_strings(values)


def _base_expression(values: tuple[str, ...]) -> str:
    return "(" + " OR ".join(_quoted(value) for value in values) + ")"


def build_query_plan(case: CaseDocument | Mapping[str, Any]) -> QueryPlan:
    """Create 21 stable queries: seven lanes for each evidence round.

    These are search instructions, not claims.  The function therefore uses
    only identity/scope anchors already supplied by the researcher and never
    turns search results into facts.
    """

    document = case if isinstance(case, CaseDocument) else CaseDocument.from_mapping(case)
    subject = document.subject
    scope = document.scope
    aliases = subject.get("aliases", [])
    aliases = aliases if isinstance(aliases, list) else []
    subject_terms = _unique_strings([subject.get("name"), *aliases])
    if not subject_terms:
        raise ValueError("at least one subject name is required")
    raw_jurisdictions = scope.get("jurisdictions", [])
    raw_jurisdictions = raw_jurisdictions if isinstance(raw_jurisdictions, list) else []
    jurisdictions = _unique_strings(raw_jurisdictions)
    identity_anchors = _identity_anchors(document)
    if not identity_anchors:
        raise ValueError("subject.anchor must contain a non-year identity entity")
    time_anchors = _year_anchors(document)

    subject_expression = _base_expression(subject_terms)
    identity_expression = _base_expression(identity_anchors)
    time_expression = _base_expression(time_anchors) if time_anchors else ""
    jurisdiction_expression = _base_expression(jurisdictions) if jurisdictions else ""
    context_parts = [part for part in (time_expression, jurisdiction_expression) if part]

    round_terms = (
        (SearchRound.SUPPORT, "建立最直接且接近事件时间的支持链", "support_terms"),
        (SearchRound.COUNTER, "主动查找日期、金额、角色或结果的反证", "counter_terms"),
        (SearchRound.CAUSAL, "攻击单因果叙事并检索替代解释", "causal_terms"),
    )
    queries: list[QuerySpec] = []
    for round_number, (round_, purpose, terms_attr) in enumerate(round_terms, start=1):
        for lane_number, lane in enumerate(RESEARCH_LANES, start=1):
            lane_terms = getattr(lane, terms_attr)
            parts = [
                subject_expression,
                identity_expression,
                _base_expression(lane_terms),
                *context_parts,
            ]
            queries.append(
                QuerySpec(
                    id=f"query-{round_number:02d}-{lane_number:02d}",
                    round=round_,
                    lane_id=lane.id,
                    lane_label=lane.label,
                    query=" ".join(parts),
                    purpose=purpose,
                )
            )
    return QueryPlan(subject_terms, identity_anchors, time_anchors, jurisdictions, tuple(queries))
