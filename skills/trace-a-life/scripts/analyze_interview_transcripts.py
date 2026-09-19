#!/usr/bin/env python3
"""Build the five auditable interview-analysis artifacts for an x-ray case."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable


SCHEMA = "xray-interview-analysis/1"
STAGE_FILES = {
    "event_alignment": "event-alignments.json",
    "speaker_hypotheses": "speaker-hypotheses.json",
    "topic_segmentation": "topic-chapters.json",
    "disclosure_extraction": "disclosure-candidates.json",
    "third_cross_verification": "third-round-verification.json",
}

TOPICS = {
    "origins": ("起点、求学与职业转向", ("小时候", "高中", "大学", "留学", "签证", "乐手", "夜总会", "崔健", "第一份工作")),
    "first_capital": ("第一桶金与早期失败", ("第一桶金", "库存", "牛仔靴", "皮夹克", "亏光", "赔光", "本金", "二十万", "3万", "失败")),
    "design_business": ("设计创业与客户网络", ("设计", "广告", "早晨", "客户", "公司", "创业", "电影", "华谊", "合伙人", "工作室", "房地产")),
    "investment": ("投资、泡沫与退出", ("股票", "投资", "清仓", "泡沫", "一亿", "现金", "关闭公司", "退出", "2015", "账户")),
    "munger": ("芒格影响与人生减法", ("芒格", "穷查理", "李录", "道德", "消费", "普通", "读书", "反思")),
    "wealth": ("财富观、守成与返贫", ("财富", "金钱", "返贫", "守成", "复利", "被动收入", "消费", "自由", "破产", "有钱人")),
    "family_legacy": ("家庭、教育与代际传承", ("孩子", "子女", "家庭", "教育", "传承", "下一代", "父母", "家族", "遗产")),
    "relationships": ("关系、团队与社会环境", ("朋友", "合伙人", "员工", "老板", "关系", "时代", "政策", "周期", "社会", "团队")),
    "book": ("写作与《第二天》", ("第二天", "金钱进化论", "写书", "作者", "出版", "书里", "读者")),
    "general": ("财富与人生经验", ()),
}

DISCLOSURE_SIGNALS = (
    "我当时", "我后来", "我第一次", "我决定", "我发现", "我就把", "我关闭", "我清仓",
    "我赚", "我赔", "我亏", "我认识", "我朋友", "我孩子", "我父亲", "我母亲", "我们公司",
    "万", "亿", "199", "200", "201", "202", "入狱", "破产", "失败", "内幕", "没说过",
)

SENSITIVE = {
    "illegal_allegation": ("入狱", "犯罪", "违法", "判刑", "坐牢", "被抓"),
    "health": ("癌", "病", "自杀", "抑郁", "死亡"),
    "family_boundary": ("孩子", "儿子", "女儿", "妻子", "家庭", "父亲", "母亲"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def timecode(milliseconds: int) -> str:
    seconds = max(0, int(milliseconds) // 1000)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def char_ngrams(text: str, size: int = 2) -> Counter[str]:
    clean = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text.lower())
    return Counter(clean[index:index + size] for index in range(max(0, len(clean) - size + 1)))


def cosine(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    numerator = sum(value * right.get(key, 0) for key, value in left.items())
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def topic_for(text: str) -> tuple[str, str, list[str]]:
    scored = []
    for topic_id, (label, terms) in TOPICS.items():
        hits = [term for term in terms if term.lower() in text.lower()]
        scored.append((len(hits), topic_id, label, hits))
    score, topic_id, label, hits = max(scored, key=lambda item: (item[0], item[1] != "general"))
    if score == 0:
        return "general", TOPICS["general"][0], []
    return topic_id, label, hits[:8]


def segment_blocks(segments: list[dict[str, Any]], seconds: int = 90) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    start = 0
    for index, segment in enumerate(segments):
        if int(segment.get("end_ms", 0)) - int(segments[start].get("start_ms", 0)) >= seconds * 1000:
            blocks.append((start, index))
            start = index + 1
    if start < len(segments):
        blocks.append((start, len(segments) - 1))
    return blocks


def distinctive_terms(case: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    documents: list[tuple[str, str, str]] = []
    for event in case.get("events", []):
        documents.append(("event", str(event.get("id")), f"{event.get('title', '')}{event.get('summary', '')}"))
    for claim in case.get("claims", []):
        documents.append(("claim", str(claim.get("id")), str(claim.get("text", ""))))
    grams_by_doc = [set(char_ngrams(text, 3)) for _, _, text in documents]
    frequency = Counter(gram for grams in grams_by_doc for gram in grams)
    output: dict[str, dict[str, list[str]]] = {"event": {}, "claim": {}}
    for (kind, item_id, text), grams in zip(documents, grams_by_doc):
        literal = re.findall(r"(?:19|20)\d{2}|\d+(?:\.\d+)?(?:万|亿)|《[^》]{2,20}》|[A-Za-z]{3,}", text)
        ranked = sorted(grams, key=lambda gram: (frequency[gram], -text.find(gram), gram))
        output[kind][item_id] = list(dict.fromkeys(literal + ranked[:14]))
    return output


def build_alignments(case: dict[str, Any], transcripts: dict[str, dict[str, Any]], generated_at: str, inputs: list[dict[str, Any]]) -> dict[str, Any]:
    terms = distinctive_terms(case)
    interviews = []
    for interview_id, transcript in transcripts.items():
        segments = transcript["segments"]
        alignments = []
        matched_count = 0
        for number, (start, end) in enumerate(segment_blocks(segments), 1):
            text = "".join(str(item.get("text", "")) for item in segments[start:end + 1])
            event_matches: dict[str, list[str]] = {}
            claim_matches: dict[str, list[str]] = {}
            for item_id, candidates in terms["event"].items():
                hits = [term for term in candidates if term and term.lower() in text.lower()]
                if len(hits) >= 2 or any(re.fullmatch(r"(?:19|20)\d{2}|\d+(?:\.\d+)?(?:万|亿)", hit) for hit in hits):
                    event_matches[item_id] = hits[:6]
            for item_id, candidates in terms["claim"].items():
                hits = [term for term in candidates if term and term.lower() in text.lower()]
                if len(hits) >= 2 or any(len(hit) >= 5 for hit in hits):
                    claim_matches[item_id] = hits[:6]
            signals = sorted(set(term for hits in list(event_matches.values()) + list(claim_matches.values()) for term in hits))
            if event_matches or claim_matches:
                matched_count += end - start + 1
            alignments.append({
                "alignment_id": f"{interview_id}-align-{number:03d}",
                "segment_ids": [segments[start]["segment_id"], segments[end]["segment_id"]],
                "start_ms": segments[start]["start_ms"],
                "end_ms": segments[end]["end_ms"],
                "event_ids": sorted(event_matches),
                "claim_ids": sorted(claim_matches),
                "match_signals": signals[:12],
                "confidence": "medium" if len(signals) >= 3 else "low",
                "contradictions": [],
            })
        interviews.append({
            "interview_id": interview_id,
            "segment_count": len(segments),
            "matched_segment_count": matched_count,
            "unmatched_segment_count": len(segments) - matched_count,
            "alignments": alignments,
        })
    return {"schema_version": SCHEMA, "stage": "event_alignment", "generated_at": generated_at, "input_artifacts": inputs, "interviews": interviews, "blockers": []}


def speaker_hypotheses(subject_name: str, transcripts: dict[str, dict[str, Any]], generated_at: str, inputs: list[dict[str, Any]]) -> dict[str, Any]:
    output = []
    for interview_id, transcript in transcripts.items():
        segments = transcript["segments"]
        by_speaker: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for segment in segments:
            by_speaker[str(segment.get("speaker_id"))].append(segment)
        evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
        names: dict[str, str] = {}
        for index, segment in enumerate(segments[:220]):
            text = str(segment.get("text", ""))
            speaker_id = str(segment.get("speaker_id"))
            match = re.fullmatch(
                r"(?:呃|诶|嗯)?(?:我是|我叫)(?:主持人)?([\u4e00-\u9fffA-Za-z]{2,6})(?:[，。！啊哈呃]*)",
                text.strip(),
            )
            if match:
                name = match.group(1)
                if 1 < len(name) <= 6 and speaker_id not in names:
                    names[speaker_id] = name
                    evidence[speaker_id].append({"segment_id": segment["segment_id"], "timecode": timecode(segment["start_ms"]), "evidence_type": "self_identification", "text": text})
            if subject_name in text and any(marker in text for marker in ("欢迎", "邀请", "作者", "嘉宾", "老师")):
                for following in segments[index + 1:index + 8]:
                    target = str(following.get("speaker_id"))
                    if target != speaker_id:
                        evidence[target].append({"segment_id": segment["segment_id"], "timecode": timecode(segment["start_ms"]), "evidence_type": "host_identification", "text": text})
                        if any(marker in str(following.get("text", "")) for marker in ("你好", "听众", "大家好", "很高兴")):
                            names.setdefault(target, subject_name)
                            break
        if subject_name not in names.values():
            scored = []
            for speaker_id, items in by_speaker.items():
                speech = "".join(str(item.get("text", "")) for item in items)
                biography = sum(speech.count(term) for term in ("我当时", "我后来", "我公司", "我做", "我赚", "我读", "我决定"))
                questions = speech.count("？") + speech.count("您")
                scored.append((biography * 20 + len(speech) / 200 - questions * 2, speaker_id))
            if scored:
                candidate = max(scored)[1]
                host_mentions = [
                    segment for segment in segments[:260]
                    if subject_name in str(segment.get("text", "")) and str(segment.get("speaker_id")) != candidate
                ]
                if host_mentions:
                    mention = host_mentions[0]
                    names[candidate] = subject_name
                    evidence[candidate].append({"segment_id": mention["segment_id"], "timecode": timecode(mention["start_ms"]), "evidence_type": "host_identification", "text": mention["text"]})
        speakers = []
        for speaker_id, items in sorted(by_speaker.items()):
            speech = "".join(str(item.get("text", "")) for item in items)
            name = names.get(speaker_id)
            role = "subject" if name == subject_name else ("host" if name else "unknown")
            if not name and (speech.count("您") + speech.count("请问") + speech.count("欢迎")) >= 4:
                role = "host"
            speakers.append({
                "speaker_id": speaker_id,
                "role_hypothesis": role,
                "name_hypothesis": name,
                "status": "hypothesis",
                "confidence": "high" if evidence[speaker_id] else ("medium" if role != "unknown" else "low"),
                "evidence": evidence[speaker_id][:6],
                "alternatives": ["cohost", "guest", "unknown"] if role != "subject" else ["guest misidentified by diarization"],
                "limitations": ["ASR speaker_id is an acoustic cluster; no voiceprint comparison was performed."],
                "segment_count": len(items),
            })
        output.append({"interview_id": interview_id, "speakers": speakers})
    return {"schema_version": SCHEMA, "stage": "speaker_hypotheses", "generated_at": generated_at, "input_artifacts": inputs, "interviews": output, "blockers": []}


def choose_chapters(segments: list[dict[str, Any]]) -> list[tuple[int, int]]:
    if not segments:
        return []
    blocks = segment_blocks(segments, seconds=75)
    vectors = [char_ngrams("".join(str(item.get("text", "")) for item in segments[start:end + 1])) for start, end in blocks]
    bounds = []
    start_block = 0
    while start_block < len(blocks):
        start_ms = int(segments[blocks[start_block][0]].get("start_ms", 0))
        candidates = []
        forced = None
        for boundary in range(start_block + 1, len(blocks)):
            boundary_ms = int(segments[blocks[boundary][0]].get("start_ms", 0))
            elapsed = boundary_ms - start_ms
            if elapsed >= 8 * 60 * 1000:
                forced = boundary
                break
            if elapsed >= 3 * 60 * 1000:
                transition_text = str(segments[blocks[boundary][0]].get("text", ""))
                bonus = 0.18 if any(term in transition_text for term in ("接下来", "再聊", "换个", "那您", "另一个", "最后")) else 0.0
                candidates.append((cosine(vectors[boundary - 1], vectors[boundary]) - bonus, boundary))
        if forced is None and not candidates:
            bounds.append((blocks[start_block][0], blocks[-1][1]))
            break
        boundary = min(candidates)[1] if candidates else forced
        if forced is not None and candidates:
            near = [item for item in candidates if item[1] >= max(start_block + 1, forced - 2)]
            boundary = min(near or candidates)[1]
        bounds.append((blocks[start_block][0], blocks[boundary - 1][1]))
        start_block = boundary
    return bounds


def topic_chapters(transcripts: dict[str, dict[str, Any]], speakers_artifact: dict[str, Any], alignment_artifact: dict[str, Any], generated_at: str, inputs: list[dict[str, Any]]) -> dict[str, Any]:
    speaker_index = {item["interview_id"]: item for item in speakers_artifact["interviews"]}
    alignment_index = {item["interview_id"]: item for item in alignment_artifact["interviews"]}
    interviews = []
    for interview_id, transcript in transcripts.items():
        segments = transcript["segments"]
        chapters = []
        for number, (start, end) in enumerate(choose_chapters(segments), 1):
            text = "".join(str(item.get("text", "")) for item in segments[start:end + 1])
            topic_id, label, hits = topic_for(text)
            speaker_counts = Counter(str(item.get("speaker_id")) for item in segments[start:end + 1])
            alignment_refs = []
            for item in alignment_index[interview_id]["alignments"]:
                if int(item["end_ms"]) >= int(segments[start]["start_ms"]) and int(item["start_ms"]) <= int(segments[end]["end_ms"]):
                    alignment_refs.append(item["alignment_id"])
            chapters.append({
                "chapter_id": f"{interview_id}-chapter-{number:02d}",
                "title": label,
                "summary": f"自动归类为“{label}”；命中线索：{'、'.join(hits) if hits else '无稳定主题词，需人工复核'}。",
                "topic_id": topic_id,
                "topic_tags": hits,
                "start_segment_id": segments[start]["segment_id"],
                "end_segment_id": segments[end]["segment_id"],
                "start_ms": segments[start]["start_ms"],
                "end_ms": segments[end]["end_ms"],
                "start_timecode": timecode(segments[start]["start_ms"]),
                "end_timecode": timecode(segments[end]["end_ms"]),
                "dominant_speaker_ids": [speaker for speaker, _ in speaker_counts.most_common(3)],
                "alignment_refs": alignment_refs,
            })
        interviews.append({"interview_id": interview_id, "segment_count": len(segments), "chapters": chapters, "speaker_hypotheses_ref": speaker_index[interview_id]["interview_id"]})
    return {"schema_version": SCHEMA, "stage": "topic_segmentation", "generated_at": generated_at, "input_artifacts": inputs, "interviews": interviews, "blockers": []}


def nearest_segments(segments: list[dict[str, Any]], target_ms: int, radius: int = 45000) -> list[dict[str, Any]]:
    selected = [item for item in segments if abs(int(item.get("start_ms", 0)) - target_ms) <= radius]
    return selected or [min(segments, key=lambda item: abs(int(item.get("start_ms", 0)) - target_ms))]


def disclosure_grounding_window(
    segments: list[dict[str, Any]], target_ms: int, disclosure_text: str
) -> list[dict[str, Any]]:
    candidates = [
        item for item in segments
        if abs(int(item.get("start_ms", 0)) - target_ms) <= 180000
    ] or segments
    reference = char_ngrams(disclosure_text, 2)
    best = max(
        candidates,
        key=lambda item: (
            cosine(reference, char_ngrams(str(item.get("text", "")), 2))
            - abs(int(item.get("start_ms", 0)) - target_ms) / 3_600_000
        ),
    )
    center = int(best.get("start_ms", 0))
    return [item for item in segments if center - 12000 <= int(item.get("start_ms", 0)) <= center + 22000]


def parse_timecode(value: str) -> int | None:
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})", value.strip())
    if not match:
        return None
    hours, minutes, seconds = map(int, match.groups())
    return (hours * 3600 + minutes * 60 + seconds) * 1000


def sensitivity(text: str) -> str:
    for label, terms in SENSITIVE.items():
        if any(term in text for term in terms):
            return label
    return "public_business"


def disclosure_candidates(case: dict[str, Any], transcripts: dict[str, dict[str, Any]], speakers_artifact: dict[str, Any], generated_at: str, inputs: list[dict[str, Any]]) -> dict[str, Any]:
    speaker_index = {item["interview_id"]: item for item in speakers_artifact["interviews"]}
    existing = case.get("oral_history", {}).get("disclosures", [])
    candidates = []
    occupied: dict[str, list[int]] = defaultdict(list)
    for disclosure in existing:
        interview_id = str(disclosure.get("interview_id"))
        if interview_id not in transcripts:
            continue
        target = parse_timecode(str(disclosure.get("timecode", "")))
        if target is None:
            continue
        segments = transcripts[interview_id]["segments"]
        window = disclosure_grounding_window(
            segments,
            target,
            f"{disclosure.get('title', '')}{disclosure.get('summary', '')}",
        )
        subject_ids = {item["speaker_id"] for item in speaker_index[interview_id]["speakers"] if item.get("role_hypothesis") == "subject"}
        subject_window = [item for item in window if item.get("speaker_id") in subject_ids] or window
        start, end = subject_window[0], subject_window[-1]
        quote = "".join(str(item.get("text", "")) for item in subject_window)[:240]
        candidate_id = f"candidate-{disclosure['id']}"
        candidates.append({
            "candidate_id": candidate_id,
            "existing_disclosure_id": disclosure["id"],
            "interview_id": interview_id,
            "segment_ids": [start["segment_id"], end["segment_id"]],
            "start_ms": start["start_ms"], "end_ms": end["end_ms"],
            "timecode": timecode(start["start_ms"]),
            "title": disclosure.get("title"),
            "quote": quote,
            "summary": disclosure.get("summary"),
            "speaker_hypothesis_ref": f"{interview_id}:{start.get('speaker_id')}",
            "novelty": "Existing oral-history disclosure re-grounded against the full local transcript.",
            "sensitivity": (
                "public_business"
                if str(disclosure.get("sensitivity", "")).startswith("公开")
                else sensitivity(f"{disclosure.get('sensitivity', '')}{quote}")
            ),
            "epistemic_status": "candidate",
            "support_query": str(disclosure.get("title", "")),
            "counter_query": f"{case.get('subject', {}).get('name', '')} {disclosure.get('title', '')} 质疑 OR 争议",
        })
        occupied[interview_id].append(target)

    for interview_id, transcript in transcripts.items():
        segments = transcript["segments"]
        subject_ids = {item["speaker_id"] for item in speaker_index[interview_id]["speakers"] if item.get("role_hypothesis") == "subject"}
        ranked = []
        start = 0
        while start < len(segments):
            speaker_id = segments[start].get("speaker_id")
            end = start
            while end + 1 < len(segments) and segments[end + 1].get("speaker_id") == speaker_id and int(segments[end + 1]["end_ms"]) - int(segments[start]["start_ms"]) <= 70000:
                end += 1
            text = "".join(str(item.get("text", "")) for item in segments[start:end + 1])
            if speaker_id in subject_ids and len(text) >= 45:
                hits = [term for term in DISCLOSURE_SIGNALS if term in text]
                if hits:
                    middle = (int(segments[start]["start_ms"]) + int(segments[end]["end_ms"])) // 2
                    if not any(abs(middle - value) < 120000 for value in occupied[interview_id]):
                        novelty_base = "".join(str(item.get("text", "")) for item in case.get("claims", []))
                        novelty = 1.0 - cosine(char_ngrams(text), char_ngrams(novelty_base))
                        score = len(set(hits)) * 2 + novelty + min(len(text), 300) / 300
                        ranked.append((score, start, end, text, hits, novelty))
            start = end + 1
        for rank, (_, start, end, text, hits, novelty) in enumerate(sorted(ranked, reverse=True)[:2], 1):
            topic_id, label, _ = topic_for(text)
            candidate_id = f"candidate-{interview_id}-auto-{rank:02d}"
            candidates.append({
                "candidate_id": candidate_id,
                "existing_disclosure_id": None,
                "interview_id": interview_id,
                "segment_ids": [segments[start]["segment_id"], segments[end]["segment_id"]],
                "start_ms": segments[start]["start_ms"], "end_ms": segments[end]["end_ms"],
                "timecode": timecode(segments[start]["start_ms"]),
                "title": f"{label}：高信号自述候选",
                "quote": text[:240],
                "summary": f"自动命中披露线索：{'、'.join(sorted(set(hits)))}；需人工确认语义与上下文。",
                "speaker_hypothesis_ref": f"{interview_id}:{segments[start].get('speaker_id')}",
                "novelty": f"Compared with current claims by character-bigram distance; novelty_score={novelty:.3f}.",
                "sensitivity": sensitivity(text),
                "epistemic_status": "candidate",
                "topic_id": topic_id,
                "support_query": f"{case.get('subject', {}).get('name', '')} {label} {' '.join(sorted(set(hits))[:3])}",
                "counter_query": f"{case.get('subject', {}).get('name', '')} {label} 质疑 OR 争议",
            })
    return {"schema_version": SCHEMA, "stage": "disclosure_extraction", "generated_at": generated_at, "input_artifacts": inputs, "candidates": candidates, "blockers": []}


def third_verification(candidates_artifact: dict[str, Any], external: dict[str, Any], generated_at: str, inputs: list[dict[str, Any]]) -> dict[str, Any]:
    records = {str(item.get("candidate_id")): item for item in external.get("records", []) if isinstance(item, dict)}
    verifications = []
    blockers = []
    for candidate in candidates_artifact["candidates"]:
        candidate_id = candidate["candidate_id"]
        record = records.get(candidate_id)
        if record is None:
            blockers.append(f"{candidate_id}: independent external check has not been executed")
            external_check = {"status": "pending", "queries": [candidate["support_query"], candidate["counter_query"]], "sources": [], "counterevidence": ""}
            final_status = "unverified"
        else:
            external_check = {
                "status": record.get("status", "completed"),
                "searched_at": record.get("searched_at"),
                "queries": record.get("queries", []),
                "sources": record.get("sources", []),
                "counterevidence": record.get("counterevidence", ""),
                "lineage_note": record.get("lineage_note", ""),
            }
            final_status = record.get("final_status", "unverified")
            if external_check["status"] != "completed":
                blockers.append(f"{candidate_id}: independent external check is not complete")
        if candidate.get("sensitivity") in {"illegal_allegation", "health"} and final_status not in {"excluded", "unverified"}:
            blockers.append(f"{candidate_id}: high-sensitivity candidate cannot be promoted without explicit exclusion review")
        verifications.append({
            "verification_id": f"verification-{candidate_id}",
            "candidate_id": candidate_id,
            "checks": {
                "transcript_grounding": {"status": "completed", "interview_id": candidate["interview_id"], "segment_ids": candidate["segment_ids"], "timecode": candidate["timecode"], "audio_spot_check": record.get("audio_spot_check", "not_required") if record else "pending_for_high-risk"},
                "case_cross_check": {"status": "completed", "existing_disclosure_id": candidate.get("existing_disclosure_id"), "novelty": candidate["novelty"], "same_subject_repetitions_are_independent": False},
                "independent_external_check": external_check,
            },
            "final_status": final_status,
            "writeback_eligible": bool(record and record.get("writeback_eligible", False) and final_status not in {"excluded", "contradicted"}),
            "notes": record.get("notes", "") if record else "Awaiting an executed external support and counterevidence search.",
        })
    return {"schema_version": SCHEMA, "stage": "third_cross_verification", "generated_at": generated_at, "input_artifacts": inputs, "verifications": verifications, "blockers": blockers}


def artifact_ref(case_dir: Path, path: Path) -> dict[str, Any]:
    return {"path": path.relative_to(case_dir).as_posix(), "sha256": sha256(path)}


def run(case_path: Path, output_dir: Path, external_path: Path | None, update_case: bool, generated_at: str) -> int:
    case_path = case_path.resolve()
    case_dir = case_path.parent
    output_dir = output_dir.resolve()
    case = json.loads(case_path.read_text(encoding="utf-8"))
    full = [item for item in case.get("oral_history", {}).get("interviews", []) if item.get("transcript_status") == "full"]
    transcripts: dict[str, dict[str, Any]] = {}
    transcript_refs = []
    for interview in full:
        path = case_dir / interview["transcript_path"]
        transcript = json.loads(path.read_text(encoding="utf-8"))
        transcripts[interview["id"]] = transcript
        transcript_refs.append({"interview_id": interview["id"], "path": interview["transcript_path"], "sha256": sha256(path), "segment_count": len(transcript.get("segments", []))})
    base_inputs = [{"path": item["path"], "sha256": item["sha256"]} for item in transcript_refs]
    output_dir.mkdir(parents=True, exist_ok=True)

    artifacts: dict[str, dict[str, Any]] = {}
    paths: dict[str, Path] = {}
    artifacts["event_alignment"] = build_alignments(case, transcripts, generated_at, base_inputs)
    paths["event_alignment"] = output_dir / STAGE_FILES["event_alignment"]
    write_json(paths["event_alignment"], artifacts["event_alignment"])

    inputs = base_inputs + [artifact_ref(case_dir, paths["event_alignment"])]
    artifacts["speaker_hypotheses"] = speaker_hypotheses(str(case.get("subject", {}).get("name", "")), transcripts, generated_at, inputs)
    paths["speaker_hypotheses"] = output_dir / STAGE_FILES["speaker_hypotheses"]
    write_json(paths["speaker_hypotheses"], artifacts["speaker_hypotheses"])

    inputs = [artifact_ref(case_dir, paths["event_alignment"]), artifact_ref(case_dir, paths["speaker_hypotheses"])]
    artifacts["topic_segmentation"] = topic_chapters(transcripts, artifacts["speaker_hypotheses"], artifacts["event_alignment"], generated_at, inputs)
    paths["topic_segmentation"] = output_dir / STAGE_FILES["topic_segmentation"]
    write_json(paths["topic_segmentation"], artifacts["topic_segmentation"])

    inputs = [artifact_ref(case_dir, paths["speaker_hypotheses"]), artifact_ref(case_dir, paths["topic_segmentation"])]
    artifacts["disclosure_extraction"] = disclosure_candidates(case, transcripts, artifacts["speaker_hypotheses"], generated_at, inputs)
    paths["disclosure_extraction"] = output_dir / STAGE_FILES["disclosure_extraction"]
    write_json(paths["disclosure_extraction"], artifacts["disclosure_extraction"])

    external = json.loads(external_path.read_text(encoding="utf-8")) if external_path else {"records": []}
    inputs = [artifact_ref(case_dir, paths["disclosure_extraction"])]
    if external_path:
        inputs.append(artifact_ref(case_dir, external_path.resolve()))
    artifacts["third_cross_verification"] = third_verification(artifacts["disclosure_extraction"], external, generated_at, inputs)
    paths["third_cross_verification"] = output_dir / STAGE_FILES["third_cross_verification"]
    write_json(paths["third_cross_verification"], artifacts["third_cross_verification"])

    stage_entries = []
    blockers = []
    for stage in STAGE_FILES:
        stage_blockers = artifacts[stage].get("blockers", [])
        blockers.extend(stage_blockers)
        stage_entries.append({"stage": stage, "path": paths[stage].relative_to(case_dir).as_posix(), "sha256": sha256(paths[stage]), "status": "blocked" if stage_blockers else "complete"})
    manifest = {
        "schema_version": SCHEMA,
        "generated_at": generated_at,
        "case_path": case_path.relative_to(case_dir).as_posix(),
        "case_sha256_before_analysis": sha256(case_path),
        "transcripts": transcript_refs,
        "artifacts": stage_entries,
        "release_gate": {
            "status": "blocked" if blockers else "passed",
            "checks": [
                {"name": "all-full-transcripts-included", "passed": len(transcript_refs) == len(full)},
                {"name": "five-stage-artifacts-complete", "passed": not blockers},
                {"name": "same-subject-repetition-not-independent", "passed": True},
            ],
            "blockers": blockers,
        },
    }
    manifest_path = output_dir / "analysis-manifest.json"
    write_json(manifest_path, manifest)

    if update_case:
        verification_by_candidate = {item["candidate_id"]: item for item in artifacts["third_cross_verification"]["verifications"]}
        for disclosure in case.get("oral_history", {}).get("disclosures", []):
            candidate_id = f"candidate-{disclosure.get('id')}"
            if candidate_id in verification_by_candidate:
                disclosure["third_round_verification_id"] = verification_by_candidate[candidate_id]["verification_id"]
        case["oral_history"]["analysis"] = {
            "schema_version": SCHEMA,
            "status": "blocked" if blockers else "complete",
            "manifest_path": manifest_path.relative_to(case_dir).as_posix(),
            "manifest_sha256": sha256(manifest_path),
            "completed_at": None if blockers else generated_at,
            "blockers": blockers,
        }
        write_json(case_path, case)
    print(f"interviews={len(full)} candidates={len(artifacts['disclosure_extraction']['candidates'])} blockers={len(blockers)}")
    print(manifest_path)
    return 2 if blockers else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--external-evidence", type=Path)
    parser.add_argument("--update-case", action="store_true")
    parser.add_argument("--generated-at", default=datetime.now().astimezone().isoformat(timespec="seconds"))
    args = parser.parse_args()
    case_path = args.case if args.case.name == "case.json" else args.case / "case.json"
    output = args.output or case_path.parent / "research" / "interviews"
    return run(case_path, output, args.external_evidence, args.update_case, args.generated_at)


if __name__ == "__main__":
    raise SystemExit(main())
