#!/usr/bin/env python3
"""Render a validated trace-a-life case into a self-contained HTML page."""

from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path
import sys

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from validate_case import validate
from xray_person.presentation import attest_html, build_view_model, write_view_model


STATUS_LABELS = {
    "verified": "坐实",
    "credible": "基本可信",
    "partial": "部分真实",
    "self_reported": "当事人自述",
    "unverified": "未证实",
    "contradicted": "证据冲突",
    "fact": "事实",
    "self_report": "自述",
    "inference": "推断",
    "dispute": "争议",
    "simulation": "沙盘",
}

TRANSCRIPT_LABELS = {
    "full": "完整逐字稿",
    "partial": "部分逐字稿",
    "shownotes_only": "仅节目笔记",
    "pending": "待本地转写",
    "unavailable": "暂无文稿",
}


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def load_case_payload(path: str | Path) -> tuple[dict, bool]:
    """Load a private case or a public ``results.json`` snapshot.

    Public snapshots intentionally contain a ``public-research/1`` envelope.
    Unwrapping that envelope here lets the same local renderer be used for
    saved case files and for the exact JSON that is published beside a page.
    The boolean tells the CLI which validation contract applies; it never
    upgrades the public snapshot into a private case.
    """

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        isinstance(payload, dict)
        and payload.get("schema_version") == "public-research/1"
        and isinstance(payload.get("data"), dict)
    ):
        return dict(payload["data"]), True
    if not isinstance(payload, dict):
        raise ValueError("case payload must be a JSON object")
    return payload, False


def join_text(values: list[str], sep: str = " · ") -> str:
    return sep.join(esc(value) for value in values if value)


def evidence_button(source_ids: list[str], label: str = "查看证据") -> str:
    unique = list(dict.fromkeys(source_ids))
    if not unique:
        return '<span class="no-source">未附来源</span>'
    return f'<button class="evidence-trigger" type="button" data-sources="{esc(",".join(unique))}">{esc(label)} <span>↗</span></button>'


def safe_image(value: str) -> str:
    value = (value or "").strip()
    if value.startswith(("https://", "data:image/")):
        return value
    if (
        value
        and ":" not in value
        and not value.startswith(("/", "//", "\\"))
        and "\\" not in value
        and not any(part == ".." for part in value.split("/"))
        and not any(ord(character) < 32 for character in value)
    ):
        return value
    return ""


def duration_label(value: object) -> str:
    try:
        seconds = max(0, int(float(value)))
    except (TypeError, ValueError):
        return "时长未知"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours}小时{minutes:02d}分" if hours else f"{minutes}分钟"


def relation_svg(nodes: list[dict], edges: list[dict]) -> str:
    if not nodes:
        return '<div class="empty-state">尚未建立可验证的关系网络。</div>'
    width, height = 920, 520
    cx, cy = width / 2, height / 2
    radius_x, radius_y = 335, 185
    positions: dict[str, tuple[float, float]] = {}
    for index, node in enumerate(nodes):
        angle = -math.pi / 2 + 2 * math.pi * index / max(1, len(nodes))
        positions[node["id"]] = (cx + radius_x * math.cos(angle), cy + radius_y * math.sin(angle))
    lines = []
    for edge in edges:
        if edge.get("source") not in positions or edge.get("target") not in positions:
            continue
        x1, y1 = positions[edge["source"]]
        x2, y2 = positions[edge["target"]]
        lines.append(
            f'<line class="network-edge {esc(edge.get("epistemic_status", "fact"))}" '
            f'data-source-node="{esc(edge["source"])}" data-target-node="{esc(edge["target"])}" '
            f'x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"><title>{esc(edge.get("label"))}</title></line>'
        )
    node_groups = []
    for node in nodes:
        x, y = positions[node["id"]]
        label = str(node.get("label", ""))
        short = label if len(label) <= 9 else label[:8] + "…"
        node_groups.append(
            f'<g class="network-node type-{esc(node.get("type", "other"))}" role="button" tabindex="0" data-node="{esc(node["id"])}" transform="translate({x:.1f} {y:.1f})">'
            '<circle r="43"></circle>'
            f'<text text-anchor="middle" dy="4">{esc(short)}</text><title>{esc(label)}：{esc(node.get("description"))}</title></g>'
        )
    return (
        f'<svg class="network-svg" viewBox="0 0 {width} {height}" role="img" aria-label="人物商业关系网络">'
        '<defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="7" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z"></path></marker></defs>'
        + "".join(lines)
        + "".join(node_groups)
        + "</svg>"
    )


def render(data: dict) -> str:
    subject = data["subject"]
    report = data.get("report", {})
    chapters = data.get("chapters", [])
    claims = data.get("claims", [])
    sources = data.get("sources", [])
    events = data.get("events", [])
    decisions = data.get("decisions", [])
    contexts = data.get("contexts", [])
    oral_history = data.get("oral_history", {})
    oral_analysis = oral_history.get("analysis", {})
    interviews = oral_history.get("interviews", [])
    disclosures = oral_history.get("disclosures", [])
    relationships = data.get("relationships", {})
    nodes = relationships.get("nodes", [])
    edges = relationships.get("edges", [])
    wealth = data.get("wealth", {})
    scenarios = data.get("scenarios", [])
    gaps = data.get("unresolved_questions", [])
    fact_freeze = data.get("fact_freeze", {})

    quote_map: dict[str, list[str]] = {}
    for claim in claims:
        for item in claim.get("evidence", []):
            if item.get("quote"):
                quote_map.setdefault(item["source_id"], []).append(item["quote"])
    source_payload = []
    for source in sources:
        enriched = dict(source)
        enriched["quotes"] = list(dict.fromkeys(quote_map.get(source["id"], [])))
        referenced = any(
            source["id"] == item.get("source_id")
            for claim in claims
            for item in claim.get("evidence", [])
        )
        if enriched["quotes"]:
            enriched["citation_state"] = "cited"
            enriched["citation_note"] = "此来源附有案件中的逐字引文。"
        elif referenced:
            enriched["citation_state"] = "source-only"
            enriched["citation_note"] = "当前快照只保留来源登记，未附逐字引文。"
        else:
            enriched["citation_state"] = "uncited"
            enriched["citation_note"] = "当前没有主张引用此来源。"
        source_payload.append(enriched)
    source_json = json.dumps(source_payload, ensure_ascii=False).replace("<", "\\u003c")

    portrait = safe_image(subject.get("portrait", ""))
    monogram = "".join(list(subject.get("name", "人物"))[:2])
    hero_visual = (
        f'<img class="portrait-image" src="{esc(portrait)}" alt="{esc(subject.get("name"))}">'
        if portrait
        else f'<div class="portrait-abstract" aria-hidden="true"><span>{esc(monogram)}</span><i></i><b></b></div>'
    )

    verified_count = sum(claim.get("status") in {"verified", "credible"} for claim in claims)
    contested_count = sum(claim.get("status") in {"partial", "unverified", "contradicted"} for claim in claims)
    independent_sources = len({source.get("origin_cluster") for source in sources if source.get("origin_cluster")})
    cited_claim_count = sum(bool(claim.get("evidence")) for claim in claims)
    quoted_claim_count = sum(
        any(item.get("quote") for item in claim.get("evidence", []))
        for claim in claims
    )
    citation_state = (
        "引文已随案件保存"
        if quoted_claim_count
        else "只保留来源编号"
        if cited_claim_count
        else "待补来源"
    )
    is_frozen = fact_freeze.get("status") == "frozen"
    research_state = "事实包已冻结" if is_frozen else "研究草稿 · 尚未事实冻结"
    research_note = (
        f"冻结于 {fact_freeze.get('frozen_at')}"
        if is_frozen
        else fact_freeze.get("notes") or "关键证据缺口仍需解决。"
    )

    full_interview_count = oral_analysis.get(
        "full_interview_count",
        sum(interview.get("transcript_status") == "full" for interview in interviews),
    )
    segment_count = oral_analysis.get(
        "segment_count",
        sum(int(interview.get("segment_count") or 0) for interview in interviews),
    )
    analysis_status = oral_analysis.get("status", "pending")
    analysis_status_label = "五道门禁通过" if analysis_status == "complete" else "访谈分析未完成"
    oral_audit = (
        '<div class="oral-audit reveal">'
        f'<div class="oral-audit-lead"><p class="kicker">TRANSCRIPT AUDIT / {esc(analysis_status_label)}</p>'
        '<h3>每条口述先回到说话人、时间码和第三轮核验。</h3>'
        '<p>事件对齐 → 说话人假设 → 连续章节 → 秘辛候选 → 外部交叉验证</p></div>'
        f'<dl><div><dt>{esc(full_interview_count)}</dt><dd>完整访谈</dd></div>'
        f'<div><dt>{esc(segment_count)}</dt><dd>ASR 句段</dd></div>'
        f'<div><dt>{esc(oral_analysis.get("chapter_count", "—"))}</dt><dd>自动章节</dd></div>'
        f'<div><dt>{esc(oral_analysis.get("candidate_count", "—"))}</dt><dd>秘辛候选</dd></div>'
        f'<div><dt>{esc(oral_analysis.get("writeback_count", "—"))}</dt><dd>本轮写回</dd></div></dl></div>'
    ) if interviews else ""

    chapter_sections = []
    for index, chapter in enumerate(chapters, 1):
        chapter_events = [event for event in events if event.get("chapter_id") == chapter.get("id")]
        beats = "".join(
            f'<li><time>{esc(event.get("date_label") or event.get("date"))}</time><span>{esc(event.get("title"))}</span></li>'
            for event in chapter_events[:4]
        )
        chapter_sections.append(
            f'<article class="chapter reveal" id="chapter-{esc(chapter.get("id"))}">'
            f'<div class="chapter-number">0{index}</div><div class="chapter-copy"><p class="kicker">{esc(chapter.get("label"))} · {esc(chapter.get("years"))}</p>'
            f'<h3>{esc(chapter.get("title") or chapter.get("label"))}</h3><p>{esc(chapter.get("summary") or "本阶段尚待补充。")}</p>'
            f'<ol class="chapter-beats">{beats}</ol></div></article>'
        )

    timeline_items = []
    for event in sorted(events, key=lambda item: item.get("date", "")):
        status = event.get("epistemic_status", "fact")
        timeline_items.append(
            f'<article class="timeline-item reveal" data-status="{esc(status)}">'
            f'<div class="timeline-date">{esc(event.get("date_label") or event.get("date"))}</div>'
            f'<div class="timeline-mark"><span></span></div><div class="timeline-copy"><div class="item-top"><span class="status {esc(status)}">{esc(STATUS_LABELS.get(status, status))}</span>'
            f'{evidence_button(event.get("source_ids", []))}</div><h3>{esc(event.get("title"))}</h3><p>{esc(event.get("summary"))}</p></div></article>'
        )

    post_exit_items = []
    post_exit_events = sorted(
        (event for event in events if event.get("post_exit")),
        key=lambda item: item.get("date", ""),
    )
    for event in post_exit_events:
        status = event.get("epistemic_status", "fact")
        post_exit_items.append(
            '<article class="post-exit-event reveal">'
            f'<div class="post-exit-date"><strong>{esc(event.get("date_label") or event.get("date"))}</strong>'
            f'<span>{esc(event.get("post_exit_phase") or "阶段")}</span></div>'
            f'<div class="post-exit-copy"><div class="item-top"><span class="status {esc(status)}">{esc(STATUS_LABELS.get(status, status))}</span>'
            f'{evidence_button(event.get("source_ids", []))}</div><h3>{esc(event.get("title"))}</h3>'
            f'<p>{esc(event.get("summary"))}</p></div></article>'
        )
    post_exit_section = (
        '<section class="section post-exit-section" id="post-exit"><div class="section-head">'
        '<p class="kicker">II / 消失的十一年</p><h2>经营退出，<br>不等于停止行动。</h2></div>'
        '<div class="post-exit-intro"><p class="kicker">2015 → 2026 / 公开轨迹重建</p><div>'
        f'<p class="post-exit-thesis">{esc(report.get("post_exit_thesis") or "退出后的公开轨迹尚待重建。")}</p>'
        f'<p class="post-exit-question"><strong>证据边界：</strong>{esc(report.get("post_exit_limits") or "连续外部记录仍不足。")}</p>'
        '</div></div><div class="post-exit-layout"><aside class="post-exit-axis" aria-label="2015年至2026年">'
        '<span>2015</span><b>11</b><small>years outside operations</small><span>2026</span></aside>'
        f'<div class="post-exit-list">{"".join(post_exit_items)}</div></div></section>'
        if post_exit_events
        else ""
    )
    section_numbers = (
        {"timeline": "III", "decisions": "IV", "network": "V", "wealth": "VI", "context": "VII", "claims": "VIII", "gaps": "IX", "sources": "X"}
        if post_exit_events
        else {"timeline": "II", "decisions": "III", "network": "IV", "wealth": "V", "context": "VI", "claims": "VII", "gaps": "VIII", "sources": "IX"}
    )

    decision_items = []
    for index, decision in enumerate(decisions, 1):
        known = "".join(f"<li>{esc(item)}</li>" for item in decision.get("known_at_time", []))
        alternatives = "".join(f"<li>{esc(item)}</li>" for item in decision.get("alternatives", []))
        constraints = join_text(decision.get("constraints", []))
        alt_explanations = "".join(f"<li>{esc(item)}</li>" for item in decision.get("alternative_explanations", []))
        decision_items.append(
            f'<article class="decision reveal"><div class="decision-index">D{index:02d}</div><div class="decision-main">'
            f'<div class="item-top"><p class="kicker">{esc(decision.get("date"))} · {esc(STATUS_LABELS.get(decision.get("interpretation_status"), decision.get("interpretation_status")))}</p>{evidence_button(decision.get("source_ids", []))}</div>'
            f'<h3>{esc(decision.get("title"))}</h3><p class="decision-act">{esc(decision.get("decision"))}</p>'
            f'<div class="decision-grid"><div><h4>当时已知</h4><ul>{known}</ul></div><div><h4>现实选项</h4><ul>{alternatives}</ul></div></div>'
            f'<p class="constraint"><span>约束</span>{constraints or "未记录"}</p><p><strong>结果：</strong>{esc(decision.get("outcome"))}</p>'
            f'<p><strong>解释：</strong>{esc(decision.get("interpretation"))}</p>'
            f'<details><summary>替代解释与证伪条件</summary><ul>{alt_explanations}</ul><p><strong>可推翻当前解释的证据：</strong>{esc(decision.get("falsifier"))}</p></details>'
            '</div></article>'
        )

    relation_list = []
    node_map = {node.get("id"): node.get("label") for node in nodes}
    for edge in edges:
        relation_list.append(
            f'<li data-relation-source="{esc(edge.get("source"))}" data-relation-target="{esc(edge.get("target"))}">'
            f'<span>{esc(node_map.get(edge.get("source"), edge.get("source")))}</span><b>{esc(edge.get("label"))}</b><span>{esc(node_map.get(edge.get("target"), edge.get("target")))}</span>'
            f'<small>{esc(edge.get("period"))} · {esc(STATUS_LABELS.get(edge.get("epistemic_status"), edge.get("epistemic_status")))}</small>{evidence_button(edge.get("source_ids", []), "依据")}</li>'
        )

    wealth_stages = []
    for stage in wealth.get("stages", []):
        wealth_stages.append(
            f'<article class="wealth-stage reveal"><p class="kicker">{esc(stage.get("period"))}</p><h3>{esc(stage.get("title"))}</h3>'
            f'<p>{esc(stage.get("summary"))}</p><div class="item-top"><span class="wealth-mechanism">{esc(stage.get("mechanism"))}</span>{evidence_button(stage.get("source_ids", []))}</div></article>'
        )
    transfer_items = []
    for transfer in wealth.get("transfers", []):
        transfer_items.append(
            f'<li><div><span>{esc(transfer.get("period"))}</span><h4>{esc(transfer.get("title"))}</h4><p>{esc(transfer.get("summary"))}</p></div>{evidence_button(transfer.get("source_ids", []))}</li>'
        )

    claim_items = []
    for claim in claims:
        status = claim.get("status", "unverified")
        clusters = {
            next((source.get("origin_cluster") for source in sources if source.get("id") == item.get("source_id")), "")
            for item in claim.get("evidence", [])
        }
        source_ids = [item.get("source_id") for item in claim.get("evidence", []) if item.get("source_id")]
        quote_count = sum(bool(item.get("quote")) for item in claim.get("evidence", []))
        citation_label = (
            "已引用引文"
            if quote_count
            else "已登记来源，未附逐字引文"
            if source_ids
            else "待补来源与引文"
        )
        evidence_label = "查看来源与引文" if quote_count else "查看来源登记"
        claim_items.append(
            f'<article class="claim-row reveal" data-citation-state="{esc("cited" if quote_count else "source-only" if source_ids else "uncited")}"><div><span class="status {esc(status)}">{esc(STATUS_LABELS.get(status, status))}</span><small>{esc(claim.get("importance"))}</small><small class="citation-state">{esc(citation_label)}</small></div>'
            f'<div><h3>{esc(claim.get("text"))}</h3><p>{esc(claim.get("explanation"))}</p>'
            f'<details><summary>反证检查</summary><p>{esc(claim.get("counterevidence") or "未记录反证检查。")}</p></details></div>'
            f'<div class="claim-proof"><strong>{len({item for item in clusters if item})}</strong><span>来源谱系</span>{evidence_button(source_ids, evidence_label)}</div></article>'
        )

    context_items = "".join(
        f'<article class="context-line reveal"><p class="kicker">{esc(item.get("period"))} · {esc(item.get("type"))}</p><h3>{esc(item.get("title"))}</h3><p>{esc(item.get("summary"))}</p>{evidence_button(item.get("source_ids", []))}</article>'
        for item in contexts
    )
    interview_map = {item.get("id"): item for item in interviews}
    interview_items = []
    for interview in interviews:
        audio_path = str(interview.get("audio_path", "")).strip()
        audio = (
            f'<audio controls preload="metadata" src="../{esc(audio_path)}"></audio>'
            if audio_path and not audio_path.startswith(("/", "\\")) and ".." not in audio_path.split("/")
            else ""
        )
        digest = str(interview.get("audio_sha256", ""))
        digest_label = f"SHA-256 {digest[:12]}…" if digest else "未保存音频哈希"
        interview_items.append(
            f'<article class="interview-card reveal"><div class="item-top"><p class="kicker">{esc(interview.get("published_at"))} · {esc(interview.get("publisher"))}</p>'
            f'{evidence_button(interview.get("source_ids", []), "节目来源")}</div><h3>{esc(interview.get("title"))}</h3>'
            f'<p>{esc(duration_label(interview.get("duration_seconds")))} · {esc(TRANSCRIPT_LABELS.get(interview.get("transcript_status"), interview.get("transcript_status")))} <code>{esc(interview.get("transcript_status"))}</code></p>'
            f'{audio}<small>{esc(digest_label)} · {esc(interview.get("notes"))}</small></article>'
        )
    disclosure_items = []
    for disclosure in disclosures:
        interview = interview_map.get(disclosure.get("interview_id"), {})
        status = disclosure.get("epistemic_status", "self_report")
        verification_ref = disclosure.get("third_round_verification_id")
        verification_line = (
            f'<small class="verification-line"><b>第三轮：</b>已登记 <code>{esc(verification_ref)}</code></small>'
            if verification_ref
            else '<small class="verification-line pending"><b>第三轮：</b>未登记，仅保留原始披露</small>'
        )
        disclosure_items.append(
            f'<article class="oral-disclosure reveal"><div class="oral-time"><strong>{esc(disclosure.get("timecode"))}</strong><span class="status {esc(status)}">{esc(STATUS_LABELS.get(status, status))}</span></div>'
            f'<div><p class="kicker">{esc(interview.get("title"))}</p><h3>{esc(disclosure.get("title"))}</h3><p>{esc(disclosure.get("summary"))}</p>'
            f'<small><b>交叉核对：</b>{esc(disclosure.get("cross_check") or "尚无独立旁证")}</small>'
            f'<small><b>敏感性：</b>{esc(disclosure.get("sensitivity") or "公开职业经历")}</small>{verification_line}</div>'
            f'{evidence_button(disclosure.get("source_ids", []))}</article>'
        )
    claim_labels = {claim.get("id"): claim.get("text") for claim in claims}
    gap_fragments = []
    for item in gaps:
        record = item if isinstance(item, dict) else {}
        question = item if not isinstance(item, dict) else item.get("question")
        claim_ids = record.get("claim_ids", [])
        claim_text = "；".join(
            esc(claim_labels.get(claim_id, claim_id))
            for claim_id in claim_ids
            if claim_id
        )
        claims_fragment = (
            f'<p class="gap-claims"><strong>关联主张：</strong>{claim_text}</p>'
            if claim_text
            else ""
        )
        gap_fragments.append(
            f'<details class="gap reveal"><summary><span class="gap-mark">?</span><span>{esc(question)}</span></summary>'
            f'<div class="gap-body"><p><strong>已经查过：</strong>{esc(record.get("searched"))}</p>'
            f'<p><strong>下一步：</strong>{esc(record.get("next_query"))}</p>'
            f'<p><strong>影响：</strong>{esc(record.get("impact"))}</p>{claims_fragment}</div></details>'
        )
    gap_items = "".join(gap_fragments)
    scenario_items = "".join(
        f'<article class="scenario reveal"><div class="scenario-tag">SIMULATION / 非史实</div><h3>{esc(item.get("title"))}</h3><p>{esc(item.get("summary"))}</p>'
        f'<h4>观察信号</h4><ul>{"".join(f"<li>{esc(signal)}</li>" for signal in item.get("signals", []))}</ul>'
        f'<small>{join_text(item.get("limitations", []))}</small></article>'
        for item in scenarios
    )
    source_rows_fragments = []
    citation_state_labels = {
        "cited": "已引用引文",
        "source-only": "已登记来源",
        "uncited": "未被主张引用",
    }
    for source in source_payload:
        quotes = "".join(
            f"<blockquote>{esc(quote)}</blockquote>"
            for quote in source.get("quotes", [])
        )
        citation_body = quotes or (
            f'<p class="citation-note">{esc(source.get("citation_note"))}</p>'
        )
        source_rows_fragments.append(
            f'<li id="source-{esc(source.get("id"))}"><span>{esc(source.get("id"))}</span><div><details class="source-detail"><summary>{esc(source.get("title"))}<span class="source-citation-state">{esc(citation_state_labels.get(source.get("citation_state"), "状态未知"))}</span></summary>'
            f'<small>{esc(source.get("publisher"))} · {esc(source.get("published_at"))} · {esc(source.get("role"))} · 来源簇：{esc(source.get("origin_cluster"))}</small>'
            f'{citation_body}'
            f'<a href="{esc(source.get("url"))}" target="_blank" rel="noreferrer noopener">打开原始来源 ↗</a></details></div></li>'
        )
    source_rows = "".join(source_rows_fragments)
    scenario_section = (
        f'<section class="section simulation-section" id="simulation"><div class="section-head"><p class="kicker">MiroFish / 反事实沙盘</p><h2>不是过去的真相，<br>是替代路径的压力测试。</h2></div>{scenario_items}</section>'
        if scenarios
        else ""
    )

    nav_links = [
        ("story", "四幕"), ("timeline", "时间线"), ("decisions", "决策"),
        ("network", "关系"), ("claims", "核验"), ("sources", "来源"),
    ]
    if post_exit_events:
        nav_links.insert(1, ("post-exit", "十一年"))
    if interviews or disclosures:
        timeline_index = next(index for index, item in enumerate(nav_links) if item[0] == "timeline")
        nav_links.insert(timeline_index + 1, ("interviews", "访谈"))
    nav = "".join(f'<a href="#{target}">{label}</a>' for target, label in nav_links)

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="{esc(report.get('dek'))}"><title>{esc(report.get('title') or subject.get('name'))} · 商业生平调查</title>
<style>
:root{{--ink:#111a18;--ink-2:#24302d;--paper:#f2ede2;--paper-2:#e9e1d2;--rust:#b84a2b;--gold:#b99861;--muted:#6f746e;--line:rgba(17,26,24,.18);--serif:"Songti SC","STSong","Noto Serif CJK SC",Georgia,serif;--sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);overflow-x:hidden}}body:before{{content:"";position:fixed;inset:0;pointer-events:none;opacity:.22;z-index:20;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 180 180' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.12'/%3E%3C/svg%3E")}}
a{{color:inherit}}button{{font:inherit}}.topbar{{position:fixed;inset:0 0 auto;z-index:15;height:66px;display:flex;align-items:center;justify-content:space-between;padding:0 4vw;color:#fff;border-bottom:1px solid rgba(255,255,255,.15);background:rgba(17,26,24,.74);backdrop-filter:blur(14px)}}.brand{{font:700 12px/1 var(--sans);letter-spacing:.18em}}.nav{{display:flex;gap:24px}}.nav a{{font-size:12px;text-decoration:none;opacity:.66;white-space:nowrap;display:flex;align-items:center;min-height:44px}}.nav a.active,.nav a:hover{{opacity:1;color:#e6b39c}}.mode-toggle{{border:1px solid rgba(255,255,255,.25);border-radius:999px;background:transparent;color:#fff;padding:8px 13px;cursor:pointer}}
.hero{{min-height:100svh;background:var(--ink);color:var(--paper);display:grid;grid-template-columns:1.14fr .86fr;position:relative;overflow:hidden}}.hero-copy{{padding:clamp(104px,14vh,150px) 5vw 68px;display:flex;flex-direction:column;justify-content:space-between;position:relative;z-index:2}}.hero-copy:after{{content:"";position:absolute;right:0;top:13%;bottom:13%;width:1px;background:rgba(255,255,255,.18)}}.eyebrow,.kicker{{font-size:11px;text-transform:uppercase;letter-spacing:.19em;font-weight:750;color:var(--rust)}}.research-state{{display:flex;align-items:flex-start;gap:18px;margin:20px 0 0;padding:13px 0;border-top:1px solid rgba(255,255,255,.2);border-bottom:1px solid rgba(255,255,255,.2);max-width:760px}}.research-state strong{{font-size:11px;letter-spacing:.13em;text-transform:uppercase;color:#f0b59f;white-space:nowrap}}.research-state span{{font-size:11px;line-height:1.55;color:rgba(255,255,255,.58)}}.hero h1{{font:400 clamp(70px,11vw,170px)/.84 var(--serif);letter-spacing:-.08em;margin:22px 0;max-width:860px}}.hero-dek{{font:400 clamp(18px,1.8vw,28px)/1.45 var(--serif);max-width:760px;color:rgba(242,237,226,.78)}}.hero-meta{{display:flex;gap:36px;flex-wrap:wrap;margin-top:38px}}.hero-meta div{{border-top:1px solid rgba(255,255,255,.2);padding-top:12px;min-width:120px}}.hero-meta strong{{font:400 28px/1 var(--serif);display:block}}.hero-meta span{{font-size:11px;color:rgba(255,255,255,.55);letter-spacing:.08em}}.hero-thesis{{margin-top:36px;max-width:680px;border-left:2px solid var(--rust);padding-left:22px;font-size:14px;line-height:1.8;color:rgba(255,255,255,.68)}}
.hero-visual{{position:relative;display:grid;place-items:center;overflow:hidden;background:radial-gradient(circle at 50% 35%,#35433f 0,#1c2825 45%,#111a18 75%)}}.hero-visual:before{{content:"";position:absolute;inset:9%;border:1px solid rgba(255,255,255,.12);border-radius:50%}}.portrait-image{{width:100%;height:100%;object-fit:cover;filter:grayscale(1) contrast(1.08);mix-blend-mode:luminosity;opacity:.82}}.portrait-abstract{{position:relative;width:min(58vh,72%);aspect-ratio:3/4;border:1px solid rgba(255,255,255,.16);display:grid;place-items:center;transform:rotate(2deg);background:linear-gradient(145deg,rgba(255,255,255,.09),rgba(255,255,255,.01))}}.portrait-abstract span{{font:400 clamp(84px,12vw,170px)/1 var(--serif);color:rgba(242,237,226,.9);writing-mode:vertical-rl;letter-spacing:.12em}}.portrait-abstract i,.portrait-abstract b{{position:absolute;border:1px solid rgba(184,74,43,.55);border-radius:50%;inset:9% 12%;transform:rotate(-12deg)}}.portrait-abstract b{{inset:20% -18%;transform:rotate(31deg)}}.visual-caption{{position:absolute;bottom:30px;right:34px;font-size:10px;letter-spacing:.16em;color:rgba(255,255,255,.48)}}
.section{{padding:clamp(90px,12vw,170px) max(5vw,24px);border-bottom:1px solid var(--line)}}.section-head{{display:grid;grid-template-columns:1fr 2fr;align-items:start;margin-bottom:70px}}.section-head h2{{font:400 clamp(42px,6.5vw,94px)/.96 var(--serif);letter-spacing:-.055em;margin:0;max-width:1000px}}.section-head .kicker{{padding-top:10px}}.lede{{font:400 22px/1.7 var(--serif);max-width:900px;margin:0 0 60px auto;color:var(--ink-2)}}
.story{{background:var(--paper)}}.chapter{{display:grid;grid-template-columns:.55fr 2fr;gap:5vw;min-height:52vh;padding:72px 0;border-top:1px solid var(--line);align-items:center}}.chapter-number{{font:italic 400 clamp(80px,15vw,230px)/1 var(--serif);color:transparent;-webkit-text-stroke:1px rgba(17,26,24,.22)}}.chapter-copy h3{{font:400 clamp(38px,5vw,76px)/1.02 var(--serif);margin:14px 0 24px;max-width:880px}}.chapter-copy>p:not(.kicker){{font:400 20px/1.8 var(--serif);max-width:760px;color:var(--ink-2)}}.chapter-beats{{list-style:none;margin:35px 0 0;padding:0;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 28px}}.chapter-beats li{{display:flex;gap:14px;padding:13px 0;border-top:1px solid var(--line);font-size:13px}}.chapter-beats time{{color:var(--rust);min-width:54px}}
.post-exit-section{{position:relative;overflow:clip;background:#d7cbb7}}.post-exit-section:before{{content:"2015—2026";position:absolute;right:-.025em;top:.35em;font:italic 400 clamp(90px,18vw,270px)/1 var(--serif);letter-spacing:-.08em;color:rgba(17,26,24,.055);white-space:nowrap}}.post-exit-intro{{display:grid;grid-template-columns:1fr 2fr;gap:5vw;margin-bottom:70px;position:relative}}.post-exit-thesis{{font:400 clamp(24px,3vw,42px)/1.45 var(--serif);margin:0;max-width:930px}}.post-exit-question{{align-self:end;margin:0;padding:18px 0 0;border-top:1px solid var(--line);font-size:12px;line-height:1.7;color:var(--muted)}}.post-exit-layout{{display:grid;grid-template-columns:minmax(150px,.55fr) 2fr;gap:5vw;position:relative}}.post-exit-axis{{position:sticky;top:100px;height:calc(100vh - 130px);min-height:540px;display:flex;flex-direction:column;justify-content:space-between;border-left:1px solid var(--ink);padding:0 0 0 24px}}.post-exit-axis span{{font:400 clamp(36px,5vw,72px)/1 var(--serif)}}.post-exit-axis b{{font:italic 400 clamp(95px,13vw,190px)/.8 var(--serif);color:var(--rust)}}.post-exit-axis small{{font-size:10px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);writing-mode:vertical-rl;position:absolute;right:0;top:42%}}.post-exit-event{{display:grid;grid-template-columns:150px 1fr;gap:35px;padding:34px 0 52px;border-top:1px solid var(--line)}}.post-exit-date strong{{display:block;font:400 27px/1.1 var(--serif)}}.post-exit-date span{{display:block;margin-top:12px;font-size:10px;letter-spacing:.13em;text-transform:uppercase;color:var(--rust)}}.post-exit-copy h3{{font:400 clamp(28px,3vw,46px)/1.15 var(--serif);margin:16px 0}}.post-exit-copy p{{max-width:760px;margin:0;color:var(--ink-2);line-height:1.8}}
.timeline-section{{background:var(--ink);color:var(--paper)}}.timeline-section .section-head h2{{color:var(--paper)}}.filters{{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 50px calc(33.333% + 2vw)}}.filter{{border:1px solid rgba(255,255,255,.2);background:transparent;color:#fff;border-radius:999px;padding:9px 15px;cursor:pointer}}.filter.active{{background:var(--paper);color:var(--ink)}}.timeline-item{{display:grid;grid-template-columns:1fr 70px 2fr;max-width:1200px;margin:auto;min-height:180px}}.timeline-date{{font:400 30px/1 var(--serif);padding:24px 25px 0 0;text-align:right;color:#d8c6ad}}.timeline-mark{{position:relative;display:flex;justify-content:center}}.timeline-mark:before{{content:"";width:1px;background:rgba(255,255,255,.18);position:absolute;inset:0 auto}}.timeline-mark span{{position:relative;margin-top:27px;width:9px;height:9px;border-radius:50%;background:var(--rust);box-shadow:0 0 0 7px var(--ink)}}.timeline-copy{{padding:20px 0 65px;border-top:1px solid rgba(255,255,255,.16)}}.timeline-copy h3{{font:400 30px/1.2 var(--serif);margin:14px 0}}.timeline-copy p{{line-height:1.75;color:rgba(255,255,255,.68);max-width:720px}}.item-top{{display:flex;align-items:center;justify-content:space-between;gap:18px}}.status{{display:inline-block;font-size:10px;letter-spacing:.12em;border-radius:999px;padding:5px 8px;background:#d9e2dc;color:#1d4d3a}}.status.self_report,.status.self_reported{{background:#e7d9bd;color:#765d2a}}.status.inference,.status.partial{{background:#eed1bf;color:#7b3b26}}.status.dispute,.status.contradicted{{background:#e5bdb8;color:#78251f}}.status.unverified{{background:#ddd;color:#555}}.status.credible{{background:#cfe0d3;color:#265c3b}}.evidence-trigger{{border:0;border-bottom:1px solid currentColor;background:transparent;color:inherit;padding:4px 0;cursor:pointer;font-size:11px;letter-spacing:.07em;opacity:.72}}.evidence-trigger:hover{{opacity:1}}.no-source{{font-size:10px;color:var(--muted)}}
.decision{{display:grid;grid-template-columns:110px 1fr;gap:28px;border-top:1px solid var(--line);padding:55px 0}}.decision-index{{font:italic 42px/1 var(--serif);color:var(--rust)}}.decision-main h3{{font:400 clamp(30px,4vw,58px)/1.08 var(--serif);margin:14px 0}}.decision-act{{font:400 21px/1.7 var(--serif);max-width:850px}}.decision-grid{{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin:28px 0}}.decision-grid>div{{border-top:1px solid var(--line);padding-top:16px}}.decision h4,.scenario h4{{font-size:11px;letter-spacing:.14em;text-transform:uppercase}}.decision ul,.scenario ul{{padding-left:18px;line-height:1.7}}.constraint{{display:flex;gap:18px;padding:14px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}}.constraint span{{color:var(--rust);font-weight:700}}details{{margin-top:16px}}summary{{cursor:pointer;color:var(--rust);font-size:12px;font-weight:700}}
.network-section{{background:#e3dccf}}.network-wrap{{display:grid;grid-template-columns:1.6fr .8fr;gap:45px;align-items:start}}.network-svg{{width:100%;height:auto;min-height:420px}}.network-edge{{stroke:#6f746e;stroke-width:1.5;opacity:.6;marker-end:url(#arrow)}}.network-edge.inference{{stroke:var(--rust);stroke-dasharray:7 5}}.network-edge.dispute{{stroke:#8b3026;stroke-dasharray:2 5}}#arrow path{{fill:#6f746e}}.network-node{{cursor:pointer}}.network-node circle{{fill:var(--paper);stroke:#596560;stroke-width:1.5;transition:.2s}}.network-node text{{font:600 12px var(--sans);fill:var(--ink);pointer-events:none}}.network-node.type-person circle{{fill:var(--ink)}}.network-node.type-person text{{fill:var(--paper)}}.network-node.active circle{{stroke:var(--rust);stroke-width:5}}.network-edge.dim{{opacity:.08}}.relationship-list{{list-style:none;padding:0;margin:20px 0}}.relationship-list li{{display:grid;grid-template-columns:1fr auto 1fr;gap:8px;padding:17px 0;border-top:1px solid var(--line);align-items:center;font-size:12px}}.relationship-list b{{color:var(--rust);font-size:10px;text-transform:uppercase}}.relationship-list small{{grid-column:1/3;color:var(--muted)}}.relationship-list .evidence-trigger{{justify-self:end}}
.wealth-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);border:1px solid var(--line)}}.wealth-stage{{background:var(--paper);padding:34px;min-height:290px;display:flex;flex-direction:column}}.wealth-stage h3{{font:400 30px/1.15 var(--serif);margin:15px 0}}.wealth-stage p:not(.kicker){{line-height:1.7;color:var(--muted)}}.wealth-stage .item-top{{margin-top:auto}}.wealth-mechanism{{font-size:11px;color:var(--rust);font-weight:700}}.transfer-list{{list-style:none;margin:65px 0 0;padding:0}}.transfer-list li{{display:flex;justify-content:space-between;gap:30px;padding:25px 0;border-top:1px solid var(--line)}}.transfer-list span{{font-size:11px;color:var(--rust)}}.transfer-list h4{{font:400 25px/1.2 var(--serif);margin:5px 0}}
.context-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:0 60px}}.context-line{{padding:35px 0;border-top:1px solid var(--line)}}.context-line h3{{font:400 28px/1.2 var(--serif)}}.context-line p{{line-height:1.7;color:var(--muted)}}
.interviews-section{{background:#ded4c3}}.oral-audit{{display:grid;grid-template-columns:1.15fr 1fr;gap:50px;padding:34px 0;margin:0 0 54px;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}}.oral-audit-lead h3{{font:400 clamp(28px,3vw,46px)/1.15 var(--serif);margin:12px 0}}.oral-audit-lead>p:last-child{{color:var(--muted);font-size:12px;line-height:1.7}}.oral-audit dl{{display:grid;grid-template-columns:repeat(5,1fr);gap:16px;margin:0;align-self:center}}.oral-audit dl div{{border-left:1px solid var(--line);padding-left:12px}}.oral-audit dt{{font:400 clamp(25px,3vw,42px)/1 var(--serif)}}.oral-audit dd{{margin:7px 0 0;font-size:10px;color:var(--muted);letter-spacing:.08em}}.interview-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line);margin-bottom:70px}}.interview-card{{background:var(--paper);padding:30px;display:flex;flex-direction:column;min-height:290px}}.interview-card h3{{font:400 27px/1.25 var(--serif);margin:16px 0}}.interview-card>p{{color:var(--muted)}}.interview-card code{{font-size:10px;border:1px solid var(--line);border-radius:999px;padding:3px 6px;color:var(--rust)}}.interview-card audio{{width:100%;margin:22px 0 16px}}.interview-card>small{{display:block;margin-top:auto;color:var(--muted);line-height:1.5;overflow-wrap:anywhere}}.oral-disclosure{{display:grid;grid-template-columns:110px 1fr 90px;gap:30px;padding:30px 0;border-top:1px solid var(--line);align-items:start}}.oral-time strong{{font:400 25px/1 var(--serif);display:block;margin-bottom:12px}}.oral-disclosure h3{{font:400 27px/1.3 var(--serif);margin:9px 0}}.oral-disclosure p{{line-height:1.7}}.oral-disclosure small{{display:block;color:var(--muted);line-height:1.6;margin-top:7px}}.verification-line code{{font-size:9px;color:var(--rust);overflow-wrap:anywhere}}.verification-line.pending{{color:#8b3026}}.oral-disclosure .evidence-trigger{{justify-self:end}}
.claims-section{{background:#f7f3ea}}.claim-row{{display:grid;grid-template-columns:150px 1fr 120px;gap:35px;padding:32px 0;border-top:1px solid var(--line);align-items:start}}.claim-row h3{{font:400 23px/1.4 var(--serif);margin:0 0 10px}}.claim-row p{{color:var(--muted);line-height:1.6}}.claim-row small{{display:block;margin-top:8px;color:var(--muted)}}.claim-row .citation-state{{color:var(--rust);font-weight:700}}.claim-proof{{text-align:right}}.claim-proof strong{{display:block;font:400 38px/1 var(--serif)}}.claim-proof span{{display:block;font-size:10px;color:var(--muted);margin:5px 0 15px}}
.gaps{{background:var(--ink);color:var(--paper)}}.gap{{padding:27px 0;border-top:1px solid rgba(255,255,255,.18)}}.gap summary{{display:flex;align-items:flex-start;gap:20px;color:var(--paper);font:400 25px/1.3 var(--serif);list-style:none}}.gap summary::-webkit-details-marker{{display:none}}.gap-mark{{font:italic 64px/1 var(--serif);color:var(--rust);min-width:70px}}.gap-body{{padding:18px 0 0 90px;max-width:900px}}.gap p,.gap small{{color:rgba(255,255,255,.6);line-height:1.7}}.gap-body strong{{color:var(--paper)}}.gap-claims{{border-top:1px solid rgba(255,255,255,.18);padding-top:12px}}
.simulation-section{{background:#522a22;color:#f5e8d6}}.scenario{{border:1px solid rgba(255,255,255,.25);padding:42px;max-width:920px;margin-left:auto}}.scenario-tag{{font:700 10px/1 var(--sans);letter-spacing:.18em;color:#f1a07d}}.scenario h3{{font:400 36px/1.2 var(--serif)}}.scenario small{{color:rgba(255,255,255,.6)}}
.sources-section{{padding-bottom:130px}}.source-list{{list-style:none;padding:0;margin:0}}.source-list li{{display:grid;grid-template-columns:80px 1fr;gap:20px;padding:18px 0;border-top:1px solid var(--line)}}.source-list>li>span{{font:400 17px var(--serif);color:var(--rust)}}.source-detail summary{{display:flex;justify-content:space-between;gap:18px;align-items:baseline;font:600 14px/1.4 var(--sans);cursor:pointer}}.source-detail summary::-webkit-details-marker{{color:var(--rust)}}.source-citation-state{{font-size:10px;color:var(--rust);font-weight:500;white-space:nowrap}}.source-list a{{display:inline-block;margin-top:12px;font:600 12px/1.4 var(--sans);text-decoration-thickness:1px;text-underline-offset:3px;color:var(--rust)}}.source-list small{{display:block;color:var(--muted);margin-top:9px;line-height:1.55}}.source-list blockquote{{margin:15px 0;padding:13px 17px;border-left:2px solid var(--rust);background:var(--paper-2);font:400 15px/1.7 var(--serif)}}.citation-note{{margin:15px 0;color:var(--muted);font-size:12px;line-height:1.7}}.footer{{background:var(--ink);color:rgba(255,255,255,.58);padding:35px 5vw;display:flex;justify-content:space-between;gap:20px;font-size:11px}}
.drawer{{position:fixed;inset:0;z-index:30;display:none}}.drawer.open{{display:block}}.drawer-backdrop{{position:absolute;inset:0;background:rgba(0,0,0,.56)}}.drawer-panel{{position:absolute;right:0;top:0;bottom:0;width:min(590px,100%);background:var(--paper);padding:28px clamp(22px,5vw,58px);overflow:auto;box-shadow:-20px 0 80px rgba(0,0,0,.3);animation:slide .24s ease-out}}@keyframes slide{{from{{transform:translateX(35px);opacity:0}}}}.drawer-close{{border:1px solid var(--line);background:transparent;border-radius:50%;width:42px;height:42px;float:right;cursor:pointer}}.drawer h2{{font:400 43px/1.05 var(--serif);margin:55px 0 25px}}.drawer-source{{padding:25px 0;border-top:1px solid var(--line)}}.drawer-source h3{{font:400 22px/1.3 var(--serif)}}.drawer-source blockquote{{margin:16px 0;padding:15px 18px;border-left:2px solid var(--rust);background:var(--paper-2);font:400 15px/1.7 var(--serif)}}.drawer-source small{{display:block;color:var(--muted);line-height:1.6}}.drawer-source a{{display:inline-block;margin-top:12px;color:var(--rust);font-size:12px}}.empty-state{{padding:45px;border:1px dashed var(--line);color:var(--muted)}}
.evidence-mode .evidence-trigger{{background:var(--rust);color:white;border:0;padding:7px 10px;border-radius:2px;opacity:1}}.reveal{{opacity:0;transform:translateY(22px);transition:opacity .65s ease,transform .65s ease}}.reveal.visible{{opacity:1;transform:none}}
@media(max-width:850px){{.topbar{{padding:0 18px}}.nav{{max-width:58vw;overflow:auto;gap:17px}}.mode-toggle{{display:none}}.evidence-trigger,.filter{{min-height:44px}}.hero{{grid-template-columns:1fr;min-height:auto}}.hero-copy{{min-height:78svh;padding:96px 22px 48px}}.hero-copy:after{{display:none}}.research-state{{display:block}}.research-state span{{display:block;margin-top:7px}}.hero h1{{font-size:25vw}}.hero-visual{{height:62svh}}.section{{padding:85px 22px}}.section-head{{grid-template-columns:1fr;gap:24px;margin-bottom:48px}}.section-head h2{{font-size:13vw}}.chapter{{grid-template-columns:74px 1fr;gap:18px;min-height:0;padding:50px 0;align-items:start}}.chapter-number{{font-size:62px}}.chapter-beats{{grid-template-columns:1fr}}.post-exit-intro,.post-exit-layout{{grid-template-columns:1fr}}.post-exit-axis{{position:relative;top:auto;height:auto;min-height:0;flex-direction:row;align-items:end;padding:18px 0 0;border-left:0;border-top:1px solid var(--ink)}}.post-exit-axis b{{font-size:92px}}.post-exit-axis small{{position:static;writing-mode:horizontal-tb;align-self:center}}.post-exit-event{{grid-template-columns:92px 1fr;gap:20px}}.post-exit-date strong{{font-size:20px}}.timeline-item{{grid-template-columns:70px 28px 1fr}}.timeline-date{{font-size:16px;padding-right:8px}}.filters{{margin-left:0}}.decision{{grid-template-columns:1fr}}.decision-grid,.context-grid,.interview-grid,.oral-audit{{grid-template-columns:1fr}}.oral-audit{{gap:24px}}.oral-audit dl{{grid-template-columns:repeat(3,1fr);row-gap:20px}}.oral-disclosure{{grid-template-columns:1fr}}.oral-disclosure .evidence-trigger{{justify-self:start}}.network-wrap{{grid-template-columns:1fr}}.network-svg{{min-height:260px}}.wealth-grid{{grid-template-columns:1fr}}.claim-row{{grid-template-columns:1fr}}.claim-proof{{text-align:left;display:flex;gap:10px;align-items:center}}.claim-proof strong{{font-size:28px}}.relationship-list li{{grid-template-columns:1fr auto 1fr}}.gap summary{{font-size:21px}}.gap-body{{padding-left:0}}.source-detail summary{{display:block}}.source-citation-state{{display:block;margin-top:7px}}.footer{{flex-direction:column}}}}
@media(prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}.reveal{{opacity:1;transform:none;transition:none}}.drawer-panel{{animation:none}}}}
</style></head><body>
<header class="topbar"><div class="brand">X—RAY / {esc(subject.get('name'))}</div><nav class="nav">{nav}</nav><button class="mode-toggle" type="button">证据模式</button></header>
<main>
<section class="hero" id="top"><div class="hero-copy"><div><p class="eyebrow">{esc(report.get('eyebrow') or '商业生平调查')} · 截至 {esc(report.get('as_of'))}</p><div class="research-state"><strong>{esc(research_state)}</strong><span>{esc(research_note)}</span></div><h1>{esc(subject.get('name'))}</h1><p class="hero-dek">{esc(report.get('dek') or subject.get('summary'))}</p>
<div class="hero-meta"><div><strong>{verified_count}</strong><span>主张坐实或基本可信</span></div><div><strong>{independent_sources}</strong><span>来源谱系</span></div><div><strong>{contested_count}</strong><span>仍有争议或缺口</span></div><div><strong>{cited_claim_count}/{len(claims)}</strong><span>{esc(citation_state)}</span></div></div></div>
<p class="hero-thesis"><strong>核心判断</strong><br>{esc(report.get('thesis') or '尚未形成结论。')}</p></div><div class="hero-visual">{hero_visual}<span class="visual-caption">IDENTITY ANCHOR / {esc(subject.get('anchor'))}</span></div></section>
<section class="section story" id="story"><div class="section-head"><p class="kicker">I / 四幕人生</p><h2>财富从哪里来，<br>又去了哪里。</h2></div><p class="lede">{esc(subject.get('summary'))}</p>{''.join(chapter_sections)}</section>
{post_exit_section}
<section class="section timeline-section" id="timeline"><div class="section-head"><p class="kicker">{section_numbers['timeline']} / 可核验时间线</p><h2>先把发生过的事，<br>从后来写成的故事里剥出来。</h2></div><div class="filters"><button class="filter active" data-filter="all">全部</button><button class="filter" data-filter="fact">事实</button><button class="filter" data-filter="self_report">自述</button><button class="filter" data-filter="inference">推断</button><button class="filter" data-filter="dispute">争议</button></div>{''.join(timeline_items) or '<div class="empty-state">时间线尚未建立。</div>'}</section>
{f'<section class="section interviews-section" id="interviews"><div class="section-head"><p class="kicker">口述史 / 访谈秘辛</p><h2>先听他怎么说，<br>再问能否被旁证。</h2></div><p class="lede">{esc(oral_history.get("summary"))}</p>{oral_audit}<div class="interview-grid">{"".join(interview_items)}</div><div class="oral-disclosures">{"".join(disclosure_items)}</div></section>' if interviews or disclosures else ''}
<section class="section" id="decisions"><div class="section-head"><p class="kicker">{section_numbers['decisions']} / 关键决策</p><h2>当时看见了什么，<br>又放弃了哪些路。</h2></div>{''.join(decision_items) or '<div class="empty-state">尚未完成决策层分析。</div>'}</section>
<section class="section network-section" id="network"><div class="section-head"><p class="kicker">{section_numbers['network']} / 关系与结构</p><h2>个人选择，发生在<br>资本、组织与时代之间。</h2></div><div class="network-wrap"><div>{relation_svg(nodes, edges)}</div><ol class="relationship-list">{''.join(relation_list) or '<li>尚无可验证关系边。</li>'}</ol></div></section>
<section class="section" id="wealth"><div class="section-head"><p class="kicker">{section_numbers['wealth']} / 财富路径</p><h2>收入、所有权、控制权，<br>不是同一件事。</h2></div><p class="lede">{esc(wealth.get('summary'))}</p><div class="wealth-grid">{''.join(wealth_stages) or '<div class="empty-state">财富阶段尚未建立。</div>'}</div><ul class="transfer-list">{''.join(transfer_items)}</ul></section>
<section class="section" id="context"><div class="section-head"><p class="kicker">{section_numbers['context']} / 社会环境</p><h2>所谓天才决策，<br>也有它的风向与地基。</h2></div><div class="context-grid">{context_items or '<div class="empty-state">背景层尚未补全。</div>'}</div></section>
<section class="section claims-section" id="claims"><div class="section-head"><p class="kicker">{section_numbers['claims']} / 主张核验</p><h2>每一句漂亮故事，<br>都回到证据本身。</h2></div>{''.join(claim_items) or '<div class="empty-state">尚无原子主张。</div>'}</section>
<section class="section gaps" id="gaps"><div class="section-head"><p class="kicker">{section_numbers['gaps']} / 未解决</p><h2>不知道的部分，<br>不替它编完。</h2></div>{gap_items or '<div class="empty-state">暂无已记录缺口。</div>'}</section>
{scenario_section}
<section class="section sources-section" id="sources"><div class="section-head"><p class="kicker">{section_numbers['sources']} / 来源台账</p><h2>材料的出处，<br>也是结论的边界。</h2></div><ol class="source-list">{source_rows or '<li>尚无来源。</li>'}</ol></section>
</main><footer class="footer"><span>由 Trace a Life 从结构化案件生成 · {esc(research_state)} · 单文件，无 CDN</span><span>事实 ≠ 自述 ≠ 推断 ≠ 争议 ≠ 模拟</span></footer>
<aside class="drawer" aria-hidden="true"><div class="drawer-backdrop"></div><div class="drawer-panel" role="dialog" aria-modal="true" aria-labelledby="drawer-title"><button class="drawer-close" type="button" aria-label="关闭">×</button><h2 id="drawer-title">证据与出处</h2><div class="drawer-content"></div></div></aside>
<script>
var sourceData={source_json};var sourceMap={{}};sourceData.forEach(function(s){{sourceMap[s.id]=s}});var drawer=document.querySelector('.drawer');var drawerContent=document.querySelector('.drawer-content');var lastTrigger=null;
function text(v){{return v==null?'':String(v)}}function addTextNode(parent,tag,className,value){{var node=document.createElement(tag);if(className)node.className=className;node.textContent=text(value);parent.appendChild(node);return node}}function safeHttpUrl(value){{try{{var parsed=new URL(text(value));return parsed.protocol==='http:'||parsed.protocol==='https:'?parsed.href:''}}catch(error){{return ''}}}}function openDrawer(ids,trigger){{lastTrigger=trigger;drawerContent.replaceChildren();ids.forEach(function(id){{var s=sourceMap[id];if(!s)return;var article=document.createElement('article');article.className='drawer-source';addTextNode(article,'small','',text(s.id)+' · '+text(s.type)+' · '+text(s.role));addTextNode(article,'h3','',s.title||id);(s.quotes||[]).forEach(function(q){{addTextNode(article,'blockquote','',q)}});if(!(s.quotes||[]).length){{addTextNode(article,'p','citation-note',s.citation_note||'当前快照未附逐字引文。')}}var metadata=addTextNode(article,'small','',text(s.publisher)+' · '+text(s.published_at));metadata.appendChild(document.createElement('br'));metadata.appendChild(document.createTextNode('来源簇：'+text(s.origin_cluster)));var href=safeHttpUrl(s.url);if(href){{var a=addTextNode(article,'a','','打开原始来源 ↗');a.target='_blank';a.rel='noreferrer noopener';a.href=href}}drawerContent.appendChild(article)}});drawer.classList.add('open');drawer.setAttribute('aria-hidden','false');document.querySelector('.drawer-close').focus()}}function closeDrawer(){{drawer.classList.remove('open');drawer.setAttribute('aria-hidden','true');if(lastTrigger)lastTrigger.focus()}}
document.querySelectorAll('.evidence-trigger').forEach(function(btn){{btn.addEventListener('click',function(){{openDrawer((btn.dataset.sources||'').split(',').filter(Boolean),btn)}})}});document.querySelector('.drawer-close').addEventListener('click',closeDrawer);document.querySelector('.drawer-backdrop').addEventListener('click',closeDrawer);document.addEventListener('keydown',function(e){{if(e.key==='Escape'&&drawer.classList.contains('open'))closeDrawer()}});
document.querySelectorAll('.filter').forEach(function(btn){{btn.addEventListener('click',function(){{document.querySelectorAll('.filter').forEach(function(b){{b.classList.remove('active')}});btn.classList.add('active');var f=btn.dataset.filter;document.querySelectorAll('.timeline-item').forEach(function(item){{item.hidden=f!=='all'&&item.dataset.status!==f}})}})}});
document.querySelector('.mode-toggle').addEventListener('click',function(e){{document.body.classList.toggle('evidence-mode');e.currentTarget.textContent=document.body.classList.contains('evidence-mode')?'阅读模式':'证据模式'}});
var revealObserver=new IntersectionObserver(function(entries){{entries.forEach(function(entry){{if(entry.isIntersecting)entry.target.classList.add('visible')}})}},{{threshold:.08}});document.querySelectorAll('.reveal').forEach(function(el){{revealObserver.observe(el)}});
var navLinks=Array.from(document.querySelectorAll('.nav a'));var sectionObserver=new IntersectionObserver(function(entries){{entries.forEach(function(entry){{if(entry.isIntersecting){{navLinks.forEach(function(a){{a.classList.toggle('active',a.getAttribute('href')==='#'+entry.target.id)}})}}}})}},{{rootMargin:'-25% 0px -65% 0px'}});navLinks.forEach(function(a){{var section=document.querySelector(a.getAttribute('href'));if(section)sectionObserver.observe(section)}});
document.querySelectorAll('.network-node').forEach(function(node){{function focusNode(){{var id=node.dataset.node;document.querySelectorAll('.network-node').forEach(function(n){{n.classList.toggle('active',n===node)}});document.querySelectorAll('.network-edge').forEach(function(edge){{edge.classList.toggle('dim',edge.dataset.sourceNode!==id&&edge.dataset.targetNode!==id)}})}}node.addEventListener('click',focusNode);node.addEventListener('keydown',function(e){{if(e.key==='Enter'||e.key===' '){{e.preventDefault();focusNode()}}}})}});
</script></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--view-model",
        type=Path,
        help="write the deterministic presentation projection beside the HTML",
    )
    parser.add_argument(
        "--attest",
        action="store_true",
        help="bind the HTML to the current case and presentation projection",
    )
    parser.add_argument("--allow-invalid", action="store_true", help="render despite validation errors")
    args = parser.parse_args()
    try:
        data, is_public_snapshot = load_case_payload(args.case)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"Case payload could not be loaded: {error}") from error
    result = validate(data, strict=False)
    if is_public_snapshot:
        # The publication allowlist intentionally omits the private schema
        # marker. Require only the public fields needed by this renderer.
        required = ("subject", "report", "sources", "claims")
        public_errors = [f"public results missing {key}" for key in required if key not in data]
        if public_errors and not args.allow_invalid:
            raise SystemExit("Public results validation failed:\n- " + "\n- ".join(public_errors))
    elif result.errors and not args.allow_invalid:
        raise SystemExit("Case validation failed:\n- " + "\n- ".join(result.errors))
    page = render(data)
    if args.view_model or args.attest:
        view_model = build_view_model(data)
        if args.view_model:
            write_view_model(args.view_model, view_model)
        if args.attest:
            page = attest_html(page, data, view_model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(page, encoding="utf-8")
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
