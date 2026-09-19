# 多说话人访谈识别 API

核查日期：2026-08-31。供应商能力、价格和限制会变化；接入前重新检查官方文档，并先用同一段 10 分钟中文访谈做 A/B 测试。

## 选择结论

| API | 中文多人识别 | 时间戳 | 自动语义章节 | 适合当前项目的角色 |
|---|---|---|---|---|
| [Gladia](https://docs.gladia.io/chapters/audio-intelligence/chapterization) | 支持中文和 speaker diarization | 词、发言段、章节 | 原生；标题、摘要、关键词、起止时间 | 功能最接近通义；章节仍是 Alpha，必须实测与保留替代路径 |
| [Speechmatics](https://www.speechmatics.com/product/chapters) | 普通话简繁、粤语；支持 diarization | 发言段、章节 | 原生；自然话题切分、标题、摘要、时间戳 | Gladia 的成熟备用源；章节为付费附加项 |
| [腾讯会议开放平台](https://cloud.tencent.com/document/product/1095/105658) | 上传时指定或自动识别多人 | 发言与章节 | 原生智能章节，返回章节名和开始时间 | 大陆网络与中文访谈优先；智能章节需要相应企业套餐 |
| [腾讯云 ASR](https://cloud.tencent.com/document/product/1093/37823) | 盲分最多 20 人，也可接声纹 | 句级、词级 | 无 | 普通云账号最易落地；章节交给受约束的文本模型 |
| [讯飞录音文件转写大模型](https://www.xfyun.cn/doc/spark/asr_llm/Ifasr_llm.html) | 盲分 0–10 人；声纹角色分离 | 句级、词级 | 无稳定原生时间章节 | 方言、中英混说或需要注册声纹时优先 |
| [OpenAI GPT-4o Transcribe Diarize](https://developers.openai.com/api/docs/models/gpt-4o-transcribe-diarize) | 内置 diarization；可提供已知说话人参考音频 | 发言段 | 无 | 需要确认“哪位是方言”时有价值；章节另用 Structured Outputs |
| [AssemblyAI](https://www.assemblyai.com/docs/pre-recorded-audio/label-speakers) | Universal-2 支持中文 diarization | 词、发言段 | 旧 Auto Chapters 已弃用，现走 LLM Gateway | 低成本、同一供应商的两阶段组合 |
| [Deepgram](https://developers.deepgram.com/docs/diarization/) | 中文转写和 diarization | 词、发言段 | 中文无；其音频摘要目前限英语 | 便宜的第二路 ASR，不负责中文章节 |
| [ElevenLabs Scribe v2](https://elevenlabs.io/docs/overview/capabilities/speech-to-text) | 普通话、粤语；最多 32 人 | 词级 | 无 | 多人长文件的交叉转写或说话人库匹配 |

## 免费优先路由

“免费”分为会按月刷新的额度、一次性试用金和本地部署三种，不要混在一起比较：

| 方案 | 免费性质 | 能否多人分离 | 能否自动章节 | 结论 |
|---|---|---|---|---|
| [腾讯云录音文件识别](https://cloud.tencent.com/document/api/1093/35693) | 官方 FAQ 当前仍列每月 10 小时免费额度 | 是，自动分离最多 20 人 | 否 | 大陆环境下长期免费首选；关闭后付费或设置预算告警，章节接本地模型 |
| [Speechmatics](https://www.speechmatics.com/pricing) | 新账号一次性 $100 credit，无需信用卡 | 是 | 是，Chapters 按 $0.40/小时另计 | 一站式免费 PoC 首选；额度用完后不是永久免费 |
| [Gladia](https://www.gladia.io/pricing) | 一次性 50€ credit，不会每月重置 | 是 | 是，但仍为 Alpha | 功能最贴合；适合与 Speechmatics 做相同样本 A/B |
| [AssemblyAI](https://www.assemblyai.com/pricing/) | 一次性免费 credit | 是 | 现为 LLM Gateway 两阶段 | 可测试，不再把它当成熟原生章节 API |
| [Deepgram](https://deepgram.com/pricing) | 新账号一次性 $200 credit，官方称不设到期时间 | 是 | 中文无 | 免费转写额度很大，适合第二路 ASR；章节仍需本地模型 |
| 本地 FunASR + CAM++ + 本地 Qwen | 软件永久免费；消耗本机计算和存储 | 是 | 两阶段生成 | 唯一不依赖供应商试用政策的长期零 API 账单方案，可封装成本地 HTTP API |

当前案件的零成本组合建议：

```text
FFmpeg 提取 16 kHz 单声道 WAV
  -> 腾讯云 ASR 免费额度：转写 + Speaker A/B + 时间戳
  -> 本地 Qwen：只引用现有 segment_id 生成章节
  -> 关键姓名/金额片段：Deepgram 免费 credit 做第二路复核
```

腾讯云开通时默认可能进入后付费链路。即使计划只用免费额度，也应在费用中心设置预算告警或关闭超额调用，避免免费资源包耗尽后自动计费。一次性 credit 只用于 PoC，不应写进“永久免费”的运行预算。

Gladia 的预录单请求通常最长 135 分钟，企业方案可更长；章节响应包含 `start`、`end`、`headline`、`summary`、`gist` 与 `keywords`。语言表明确列出 `zh`，但没有单列“中文章节质量”，不能仅凭功能开关判定可用。详见[支持语言](https://docs.gladia.io/chapters/language/supported-languages)、[说话人分离](https://docs.gladia.io/chapters/audio-intelligence/speaker-diarization)和[文件限制](https://docs.gladia.io/chapters/limits-and-specifications/supported-formats)。

腾讯会议最接近大陆版一站式替代：上传完成时可开启智能录制并指定发言人数，之后通过智能章节接口读取章节。其门槛是产品套餐与企业鉴权，而不是普通腾讯云 ASR 的一个附加参数。详见[上传录制文件](https://cloud.tencent.com/document/product/1095/106694)、[完成上传](https://cloud.tencent.com/document/product/1095/106697)和[智能章节](https://cloud.tencent.com/document/product/1095/105658)。

## 推荐路由

1. 先用 **Gladia** 和 **腾讯会议** 对同一段中文多人访谈做 PoC；若没有腾讯会议企业权限，用 **腾讯云 ASR** 代替。
2. 说话人实名比章节更重要时，加跑 **OpenAI diarize** 或讯飞声纹分离；普通 diarization 只能证明“Speaker A/B”，不能证明身份。
3. 涉及姓名、年份、金额、公司、争议或所谓“秘辛”的片段，用第二家 ASR 复核；推荐 Deepgram、ElevenLabs 或讯飞，不对整段音频无差别重复付费。
4. 一站式章节接口不稳定时，把带时间戳的 ASR 发言段交给文本模型。模型只能返回已有 `segment_id`，不能自由生成时间。

## 统一交付契约

所有供应商结果先归一化为：

```json
{
  "segment_id": "seg-000123",
  "start_ms": 123000,
  "end_ms": 128400,
  "speaker_id": "speaker-02",
  "speaker_name": null,
  "text": "原始逐字稿",
  "confidence": 0.91,
  "provider": "gladia",
  "provider_segment_id": "..."
}
```

章节模型只允许输出：

```json
{
  "title": "创业前的职业选择",
  "start_segment_id": "seg-000123",
  "end_segment_id": "seg-000188",
  "summary": "基于所列片段的准确概括",
  "claim_segment_ids": ["seg-000131", "seg-000155"]
}
```

确定性验证器应拒绝：不存在的 segment、时间倒序、章节重叠、没有片段支撑的摘要、逐字稿中未出现的姓名/日期/金额，以及模型自行生成的秒数。

## 成本比较方法

不要只比较宣传页的每小时价格。统一计算：

```text
总成本 = 音频分钟 × 转写单价
       + diarization 附加费
       + 章节模型输入/输出 token
       + 长音频重叠切块与失败重试
       + 关键片段第二路 ASR
```

PoC 至少记录中文字符错误率、专名/数字准确率、说话人混淆次数、章节边界偏差、处理耗时和真实账单。供应商声称的“摘要”不等同于带起止时间的语义章节。
