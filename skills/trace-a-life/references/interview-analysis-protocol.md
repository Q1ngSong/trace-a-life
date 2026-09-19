# 访谈分析强制协议

Schema version: `xray-interview-analysis/1`。

本协议适用于所有 `transcript_status=full` 的本地访谈。目标不是把 ASR 文本快速总结成传记，而是留下可重放、可反证、不会把自述伪装成事实的证据链。

## 固定执行顺序

```text
ASR transcript
  -> 事件/主张对齐
  -> 说话人身份假设
  -> 连续话题章节
  -> 秘辛候选
  -> 第三次交叉验证
  -> Leader release gate
```

后一步必须引用前一步的 artifact SHA-256。上游变化会使全部下游结果失效，必须重跑，不能只改 manifest 哈希。

## 固定目录与制品

```text
cases/subject-slug/research/interviews/
├── event-alignments.json
├── speaker-hypotheses.json
├── topic-chapters.json
├── disclosure-candidates.json
├── third-round-verification.json
└── analysis-manifest.json
```

五个阶段制品均包含：`schema_version`、`generated_at`、`input_artifacts[]`、`interviews[]`、`blockers[]`。输入制品必须记录案件相对路径和 SHA-256。

`analysis-manifest.json` 至少包含：

```json
{
  "schema_version": "xray-interview-analysis/1",
  "generated_at": "带时区 ISO-8601",
  "case_path": "case.json",
  "case_sha256_before_analysis": "64位小写SHA-256",
  "transcripts": [
    {
      "interview_id": "interview-001",
      "path": "raw/interviews/transcripts/provider/episode.json",
      "sha256": "64位小写SHA-256",
      "segment_count": 3000
    }
  ],
  "artifacts": [
    {
      "stage": "event_alignment|speaker_hypotheses|topic_segmentation|disclosure_extraction|third_cross_verification",
      "path": "research/interviews/event-alignments.json",
      "sha256": "64位小写SHA-256",
      "status": "complete|blocked"
    }
  ],
  "release_gate": {
    "status": "passed|blocked",
    "checks": [],
    "blockers": []
  }
}
```

## Gate 1：事件与真实内容对齐

每个对齐项必须给出 `interview_id`、`segment_ids`、真实 `start_ms/end_ms`、`event_ids`、`claim_ids`、`match_signals`、`confidence` 和 `contradictions[]`。自动匹配只产生候选关系；没有人工或规则证据时不得改变 `case.json` 中事件与主张的状态。

必须报告：segment 总数、已进入章节的 segment 数、至少一个事件/主张匹配的 segment 数、未匹配范围和冲突数。低匹配率不是失败；隐瞒未匹配或冲突才是失败。

## Gate 2：说话人身份假设

每个 ASR `speaker_id` 都必须保留，并记录：

- `role_hypothesis`: `subject|host|cohost|guest|unknown`；
- `name_hypothesis`: 姓名或 `null`；
- `status`: 固定为 `hypothesis`，除非有人工提供的可靠声纹参考；
- `confidence`: `high|medium|low`；
- `evidence[]`: `segment_id`、时间码、证据类型与原文；
- `alternatives[]` 和 `limitations[]`。

证据强度从高到低：本人自报姓名；主持人点名后紧邻的回答；官方节目名单与可确认的发言顺序；语言/问答特征。仅凭“说得更多”“使用第一人称”不能实名，只能推断角色。

## Gate 3：自动话题章节

每章至少包含 `chapter_id`、标题、摘要、`start_segment_id/end_segment_id`、`start_ms/end_ms`、主说话人、主题标签和 Gate 1 对齐引用。

强校验：

- 章节按时间严格递增；
- 相邻章节首尾连接；
- 每个原始 segment 恰好属于一个章节；
- 起止毫秒等于原始 segment 边界；
- 不允许空章、重叠、遗漏或虚构时间。

## Gate 4：秘辛候选

“秘辛”只是研究标签，不是事实等级。每项必须包含：

- 原始 `segment_ids`、时间码和短引文；
- `speaker_hypothesis_ref`；
- 相对既有事件、主张和披露的 `novelty` 说明；
- `sensitivity`: `public_business|unnamed_third_party|family_boundary|illegal_allegation|health|exclude`；
- `epistemic_status`: 此阶段固定为 `candidate`；
- 支持、反证和最有效下一查询。

不得根据片段推断未公开第三人身份。`exclude` 不得进入页面或案件主张。

## Gate 5：第三次交叉验证

每个拟写回案件的候选必须有三层检查：

1. `transcript_grounding`：segment 存在，时间码来自 ASR；高风险或关键金额应回听音频抽查。
2. `case_cross_check`：与既有事件、主张、来源和矛盾记录对齐；记录它是新增细节、重复自述还是冲突。
3. `independent_external_check`：主动寻找同期文件、工商/法院/监管记录、独立报道或可确认的第三方记录，并记录反证搜索。节目简介、书摘、转载和当事人跨节目复述属于同一来源谱系。

最终结论只允许：`verified|credible|partial|self_reported|unverified|contradicted|excluded`。`verified/credible` 必须有独立 `origin_cluster` 和逐字证据；只有访谈证据时最高为 `self_reported`。

## Leader release gate

以下任一情况必须 `blocked`：

- 完整转写没有进入 manifest，或 path/hash/segment count 不一致；
- 五个制品缺失、哈希不匹配或状态不是 `complete`；
- 章节未完整、连续、唯一覆盖原始 segment；
- 说话人实名缺少可定位证据；
- 秘辛写回前没有三层检查和最终结论；
- 同源自述被误算为独立旁证；
- 敏感或排除内容进入事实层；
- 上游转写或案件事实发生漂移但下游未重跑。

阻断必须写入 `release_gate.blockers` 和 Leader handoff；第三轮仍无旁证是允许的研究结论，但只能保留低等级状态，不能通过改写措辞绕过。
