# `case.json` 数据契约

Schema version: `trace-a-life/1`。

## 顶层

| 字段 | 作用 |
|---|---|
| `subject` | 姓名、身份锚点、别名、画像与一句话定位 |
| `scope` | 目的、时间/司法辖区、纳入与排除范围 |
| `provenance` | 采集后端、可选外部 workspace 和研究时间 |
| `report` | 网页标题、摘要、主结论与截至时间 |
| `chapters` | 固定四幕：`origins`、`ascent`、`turning-point`、`legacy` |
| `sources` | 来源台账与来源谱系 |
| `claims` | 原子主张与六级核验状态 |
| `events` | 可阅读时间线 |
| `decisions` | 关键决策、当时信息、选项、约束与替代解释 |
| `contexts` | 社会、政策、行业、资本和技术环境 |
| `relationships` | 有向关系节点与边 |
| `wealth` | 财富来源阶段与传承动作 |
| `oral_history` | 访谈音频、逐字稿状态与带时间戳的当事人披露 |
| `unresolved_questions` | 已检查范围、证据缺口和下一轮搜索 |
| `scenarios` | MiroFish 或其他明确标注的模拟结果 |
| `fact_freeze` | 事实包是否可进入模拟 |

`provenance.verification_rounds` 记录三轮核验的可审计完成情况。每项至少包含 `round`、该轮全部 `queries_checked`，以及 `completed_at` 或外部执行返回的 `receipt_ids`；单独写“已完成三轮”不算完成凭据。

## 来源

```json
{
  "id": "src-001",
  "title": "来源标题",
  "url": "https://...",
  "publisher": "发布者",
  "published_at": "YYYY-MM-DD",
  "accessed_at": "YYYY-MM-DD",
  "type": "registry|filing|court|news|interview|book|archive|other",
  "role": "primary|independent|analysis|self_report|discovery",
  "origin_cluster": "同源材料共享的稳定名称",
  "source_document_id": "可空；由外部采集器提供时用于去重",
  "notes": "",
  "collection_progress": "not_attempted|attempted_failed|blocked_access|captured_needs_review|completed"
}
```

`collection_progress` 是采集过程状态，不是主张的真实性结论。没有本地来源记录时只能写 `not_attempted`；`attempted_failed` 与 `blocked_access` 必须有上游回执或明确失败记录支撑。主张是否被来源支持仍由 `claims[].evidence[].review` 判定。

## 主张

```json
{
  "id": "claim-001",
  "text": "最小可核验陈述",
  "importance": "critical|supporting|context",
  "status": "verified|credible|partial|self_reported|unverified|contradicted",
  "explanation": "为何得到该判定",
  "counterevidence": "主动反证结果",
  "evidence": [
    {
      "source_id": "src-001",
      "quote": "来源中的短摘录或回查锚点",
      "review": {
        "alignment_status": "direct_support|semantic_support|partial_support|context_insufficient|contradicted",
        "context_reviewed": true,
        "reviewed_by": "leader-agent|research-agent|human",
        "reviewed_at": "带时区 ISO-8601",
        "note": "主体、时间、范围、归因与前后文的核验说明"
      }
    }
  ]
}
```

`credible` 应覆盖至少两个不同 `origin_cluster`。`verified`、`credible` 的 evidence 必须有可回查的 `quote`，并且每条 evidence 都要有完成的 `review`。自动文本命中不能代替 `review`；没有 `context_reviewed=true` 的证据不能进入正式结果。

## 事件

```json
{
  "id": "event-001",
  "date": "1998-06",
  "date_label": "1998",
  "chapter_id": "origins",
  "title": "事件标题",
  "summary": "发生了什么",
  "epistemic_status": "fact|self_report|inference|dispute",
  "claim_ids": ["claim-001"],
  "source_ids": ["src-001"]
}
```

## 决策

```json
{
  "id": "decision-001",
  "date": "2003",
  "title": "决策标题",
  "decision": "可观察行动",
  "known_at_time": ["当时已知信息"],
  "alternatives": ["现实选项 A", "现实选项 B"],
  "constraints": ["资金", "政策", "关系"],
  "outcome": "之后发生的结果",
  "interpretation": "为何作出选择",
  "interpretation_status": "fact|inference",
  "alternative_explanations": ["替代解释"],
  "falsifier": "什么证据会推翻当前解释",
  "source_ids": ["src-001"]
}
```

## 访谈口述史

```json
{
  "oral_history": {
    "summary": "访谈覆盖范围与证据边界",
    "interviews": [
      {
        "id": "interview-001",
        "title": "节目标题",
        "publisher": "播客或媒体",
        "published_at": "YYYY-MM-DD",
        "duration_seconds": 5400,
        "audio_path": "raw/interviews/audio/episode.m4a",
        "audio_sha256": "64位小写SHA-256",
        "transcript_status": "full|partial|shownotes_only|pending|unavailable",
        "transcript_url": "https://...",
        "normalized_audio_path": "raw/interviews/audio/normalized/episode.16k-mono.wav",
        "normalized_audio_sha256": "64位小写SHA-256",
        "extraction_receipt_path": "raw/interviews/audio/normalized/episode.audio.json",
        "transcript_path": "raw/interviews/transcripts/tencent/episode.tencent-asr.json",
        "transcript_provider": "tencent-cloud-asr",
        "transcript_sha256": "64位小写SHA-256",
        "segment_count": 3000,
        "speaker_count": 2,
        "source_ids": ["src-001"],
        "notes": "取得方式与限制"
      }
    ],
    "disclosures": [
      {
        "id": "oral-001",
        "interview_id": "interview-001",
        "timecode": "00:15:54",
        "title": "披露标题",
        "summary": "准确转述，不写成已独立证实事实",
        "epistemic_status": "self_report|dispute",
        "cross_check": "其他来源如何支持、冲突或尚无旁证",
        "sensitivity": "公开职业经历|未具名第三人|家庭边界",
        "source_ids": ["src-001"]
      }
    ],
    "analysis": {
      "schema_version": "xray-interview-analysis/1",
      "status": "pending|complete|blocked",
      "manifest_path": "research/interviews/analysis-manifest.json",
      "manifest_sha256": "64位小写SHA-256",
      "completed_at": "带时区 ISO-8601 或 null",
      "blockers": []
    }
  }
}
```

只有使用公开下载路径并实际保存的音频才能填写 `audio_path` 与哈希。`shownotes_only` 明确表示只有节目笔记或 AI 摘要；不得将其当作完整逐字稿。只有完整 ASR 结果实际落盘后才能填写 `transcript_path`、哈希和计数，并把 `transcript_status` 设为 `full`。匿名 `speaker_count` 只表示声学聚类数量，不证明人物身份。

只要存在一个 `full` 访谈，严格校验与事实冻结就要求 `oral_history.analysis.status=complete`，且 manifest 通过 [interview-analysis-protocol.md](interview-analysis-protocol.md) 的五道门禁。分析尚未完成时应明确写 `pending` 或 `blocked`；不得省略字段来规避门禁。

## 关系

节点至少包含 `id`、`label`、`type`、`description`。边至少包含 `id`、`source`、`target`、`label`、`period`、`epistemic_status`、`source_ids`。同框、同姓或同地址不能直接生成事实边。

## 缺口

推荐对象格式：

```json
{
  "id": "gap-001",
  "claim_ids": ["claim-001"],
  "question": "仍未解决的问题",
  "searched": "查过哪些数据库、关键词或档案",
  "next_query": "下一轮最有效的搜索方向",
  "impact": "该缺口会改变什么结论"
}
```

关键未证实主张必须由 `claim_ids` 显式关联到对应缺口；一个无关缺口不能替另一条主张通过停止门。
