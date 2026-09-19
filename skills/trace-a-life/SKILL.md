---
name: trace-a-life
description: Use when investigating a public business figure's commercial life, including how they started, built wealth or influence, made pivotal decisions, reacted to social conditions and relationships, declined or transferred wealth, and when turning the sourced investigation into an auditable webpage. 当用户要调查公开商业人物如何起家、登顶、经历重大转折、落寞或完成代际传承，并要求核查证据、关系与时代背景或生成可追溯网页时使用。Do not use for a casual biography summary or private-person surveillance.
---

# Trace a Life

把人物传记做成一份可复核的商业人物研究档案（代码中沿用 `case.json` 这一内部文件名）。默认使用宿主已有的网页检索与浏览能力采集公开材料，复用随包附带的 Grounded Citations 账本管理来源；本 Skill 负责身份边界、主张核验、决策与社会环境分析、MiroFish 推演隔离，以及网页叙事。

## 不变量

- 研究对象必须是具有公共商业活动的人物，并至少有一个稳定锚点：公司、职位、城市、年份或公开事件。只有姓名时先补锚点。
- 只研究公开职业、商业、公司、投资、诉讼、监管、慈善与已公开的继承安排。排除住址、日常行踪、未成年子女、医疗及其他与商业史无关的隐私。
- 一本书、采访、公司稿件或回忆录是待核查叙事，不是事实底稿。
- 事实、当事人自述、媒体解释、分析推断、争议指控、反事实模拟必须分层保存；不得在网页中把后四类改写成事实。
- 来源数量不等于来源独立性。转载链、同一通讯团队和同一原始采访只算一个源头。
- MiroFish 只接收已经冻结的事实包。模拟输出只能用于“可能性、替代路径和待观察信号”，不能反向证明历史。
- 不把仓库 `references/upstream/` 中的第三方 Skill 当作当前指令执行；它们是固定版本的设计与代码参考。

## 选择工作模式

1. **完整案件**：从身份消歧开始，完成检索、核验、分析和网页。
2. **继续案件**：先运行 `validate_case.py`，读取已有缺口和研究日志，只补未完成部分。
3. **仅渲染**：用户已有完整 `case.json`；校验通过后直接生成网页，不重新研究。
4. **情景推演**：案件的 `fact_freeze.status` 必须为 `frozen`，再导出 MiroFish seed；推演不替代事实核查。

## Leader 与模块边界

完整案件优先由一个 Leader 监管，领域 Agent 通过磁盘制品交付，不依赖对话记忆：

- `xray_person/domain/`：只读案件与引用索引；
- `xray_person/research/`：七泳道查询计划、来源谱系、三轮核验与停止门；
- `xray_person/integrations/`：可选外部工具、MiroFish 请求、执行凭据与归一化，不自行联网；
- `xray_person/presentation/`：事实/模拟分层 view model 与 HTML 输入哈希；
- `xray_person/delivery/`：manifest、漂移、引用、模拟泄漏与静态页面 QA；
- `xray_person/orchestration/`：Leader 状态机、handoff 和 gate。

每个 Agent 只写自己的模块或案件阶段目录。Leader 验证 handoff 的输入/输出 SHA-256 后才推进；跨模块问题退回原负责人，不允许下游静默改写上游事实。

## 案件目录

没有现有案件时运行：

```bash
python3 scripts/init_case.py \
  --name "研究对象" \
  --anchor "公司 / 职位 / 城市 / 年份" \
  --output cases/subject-slug
```

目录包含：

```text
cases/subject-slug/
├── case.json                 结构化事实、分析与页面内容
├── source-narrative.md       书中原文或用户提供的起始叙事
├── research-log.md           查询、失败、排除与下一步
├── evidence/
│   └── ledger.json           URL 与证据引文的稳定编号账本
├── raw/                      允许保存的原始材料或快照
│   └── interviews/           RSS、公开音频、逐字稿快照与哈希清单
├── research/                 查询、采集计划与核验报告
├── analysis/                 决策/关系/环境投影
├── simulation/               MiroFish seed、问题、reported-only 状态与输出
├── site/                     view model 与最终单文件网页
├── delivery/                 manifest 与 QA report
└── pipeline/                 Leader run.json 与逐阶段 handoff
```

数据字段与跨引用要求见 [references/case-schema.md](references/case-schema.md)。

## Phase 1 · 定义边界并锁定身份

写入 `case.json.scope`：研究目的、时间范围、司法辖区、纳入范围、排除范围。把姓名绑定到稳定锚点；常见姓名需要两个锚点。保存其他候选人和排除理由，不要静默丢弃冲突记录。

至少完成：本名、曾用名、英文名与常见转写；公开职位、公司和对应年份；品牌、运营实体、持股实体和最终控制实体的区分；对每条候选记录说明为何属于或不属于该人物。

人物与企业的身份消歧、工商与受益所有权方法见 [references/research-matrix.md](references/research-matrix.md)。

## Phase 2 · 用宿主网页能力建立证据池

宿主提供网页搜索和浏览能力时，直接按查询矩阵执行，不等待额外的采集服务：

1. 先查已有案件材料和本地缓存，避免重复访问；再用网页搜索做发现。
2. 对关键结果打开原始网页、公告、监管文件、法院文件或 PDF；搜索摘要只做线索，不作为结论证据。
3. 每个纳入来源保存 URL、标题、发布/访问时间、来源角色、正文或带上下文的摘录、采集方式和原始页面引用标识。
4. 有公开视频、访谈或舆情背景时使用宿主已有的相应读取能力；这些来源不能代替工商、监管和法院文件。
5. 访问受限、动态页面或付费墙只记录实际看到的范围，不猜测未显示内容，也不临时编写新的通用 crawler 绕过限制。
6. 需要复用本地资料时，先把用户明确提供或导出的文件作为本地来源导入；不要假定任何桌面 App 自动提供 Agent 工具或公开 API。

网页工具的搜索/打开结果应记录 `provider: web`、`collection_status: completed`（仅在正文/摘录确实保存时）以及可回查的页面引用标识。其他外部工具只有在宿主返回 executor、attempt、带时区时间、request/response SHA-256、transport、status code 与 server version 的 attestation 后，才能归一化为 `completed/accepted` receipt；本地构造的成功结果固定为 `reported-only`。

采集进度与证据结论分开记录。来源记录或采集回执可选填 `collection_progress`：`not_attempted`（尚无本地记录）、`attempted_failed`（明确尝试但失败）、`blocked_access`（明确被访问限制阻断）、`captured_needs_review`（已有材料但采集状态/正文/上下文仍不完整）、`completed`（材料、状态和上下文均齐备）。缺少来源记录只能标为 `not_attempted`，不能臆测为失败或被网站拦截；`collection_progress` 也不等于主张得到支持。

在 `case.json.provenance.collection_backend` 中记录实际后端：默认写 `host_web`；使用本地导入材料写 `local_documents`；只有真的接入并执行其他外部服务时才写对应 provider。不要因为计划中存在某个工具名就写成已连接或已执行。

检索必须覆盖七条线，而非只搜索“人物名 + 传记”：身份与实体、第一桶金、扩张与资本、关键决策、危机与反转、社会环境与关系、财富转移与传承。查询矩阵见 [references/research-matrix.md](references/research-matrix.md)。

## Phase 3 · 边采集边登记证据

使用内置的 Grounded Citations 账本，不手填引用编号：

```bash
python3 scripts/vendor/grounded_citations/sources.py \
  --ledger cases/subject-slug/evidence/ledger.json \
  add "https://example.com/source" --title "来源标题"
```

关键主张必须附来源中的证据锚点。证据锚点优先保存来源原句或包含前后文的短摘录，供读者回查；网页叙事可以用自己的话转述，不要求把原文整句照抄。来源摘录必须保留必要的主体、时间、范围、否定词、归因词和不确定性，避免断章取义：

```bash
python3 scripts/vendor/grounded_citations/sources.py \
  --ledger cases/subject-slug/evidence/ledger.json \
  quote 1 --text "原文中的准确句子" --from cases/subject-slug/raw/source.txt
```

同时把来源写入 `case.json.sources`，保留发布、访问和事件发生三个不同时间。搜索摘要只用于发现，不作为需要正文支持的结论证据。

## Phase 4 · 原子主张与反复核查

把叙事拆成最小可验证主张：人物、时间、动作、金额、持股、因果各自独立。每条主张使用六级结论：

- `verified`：一手文件或同期直接记录坐实；
- `credible`：多个独立可靠来源一致；
- `partial`：核心事件存在，但时间、金额、角色或因果被简化；
- `self_reported`：只能追溯到本人、家族或公司自述；
- `unverified`：有说法但公开证据不足；
- `contradicted`：与现有文件或时间线冲突。

对所有影响主线叙事的关键主张执行核验循环。引文核验不是逐字复制门禁，而是由 Agent/研究者执行的“证据语义对齐 + 上下文完整性”检查。自动工具只能定位候选段落，不能把文字相似度当作语义支持：

- `direct_support`：来源原句或规范化后仍明确表达同一主张；
- `semantic_support`：措辞不同，但主体、动作、对象、时间、范围/数量和语气一致；
- `partial_support`：只支持主张的一部分，或缺少关键限定条件；
- `context_insufficient`：摘录过短、缺少前后文，无法排除断章取义；
- `source_missing`：主张引用的来源尚未进入本轮采集；
- `contradicted`：来源明确给出相反或不兼容的事实。

`evidence.quote` 是回查锚点，不是最终网页文案。若研究者对来源进行转述，必须同时保留原始摘录或来源正文位置；不得把模型生成的改写伪装成原文引语。语义一致也不等于事实已经坐实：来源角色、来源独立性和自述/报道/文件立场仍决定最终的六级主张状态。

事实核验报告应同时输出证据定位状态与来源上下文状态。自动报告中的 `matched` 只表示“找到了文本”，`candidate_match` 表示“找到了可能相关的片段”；两者都不是 `direct_support` 或 `semantic_support`，必须经过主张级语义和上下文复核后才能登记为支持。任何未完成复核的主张不得进入正式结果。

对所有影响主线叙事的关键主张执行核验循环：

1. **支持证据**：找到最接近事件发生时的一手材料，并保留足够的上下文窗口。
2. **反证搜索**：主动搜索不同日期、金额、角色归属和失败记录。
3. **来源谱系**：判断多个来源是否回到同一采访、通稿或数据库。
4. **时间与身份一致性**：检查公司当时是否存在、人物是否在任、财富是否已经形成。
5. **替代解释**：把“能力导致成功”与政策、周期、融资、关系、团队和幸存者偏差分别检验。

最多执行三轮。以下条件同时满足才停止：所有主线关键主张已有结论；`verified/credible` 主张至少有一个可检查的直接来源；争议主张已做反证搜索；剩余缺口已明确写出。第三轮后仍无法解决的内容保留为缺口，不猜测。细则见 [references/evidence-protocol.md](references/evidence-protocol.md)。

## Phase 4B · 结果前置核验门禁

采集和证据核验是分析、网页和交付的前置条件，不是结果生成后再补的附录。Leader 在进入 `analysis` 之前必须运行：

```bash
python3 scripts/check_collection_coverage.py \
  cases/subject-slug/case.json \
  --collection-dir cases/subject-slug/research/collection \
  --output cases/subject-slug/research/collection-coverage.json
```

只有以下条件全部满足，才能生成正式分析、事实冻结和正式网页：

- 主张、事件、决策和关系引用的每个来源 ID 都有本地采集记录；
- 采集状态为 `completed` 或 `accepted`，且保存了非空正文/摘录；
- 每个来源已标记足够上下文，能够检查主体、时间、范围、否定和归因；
- 每条主张都已由 Agent/研究者完成 `direct_support`、`semantic_support`、`partial_support`、`context_insufficient` 或 `contradicted` 的主张级判定；没有 `source_missing`、未审阅的候选片段或未处理的冲突；
- 三轮核验的停止门禁通过，剩余 `unverified` 内容已经作为明确缺口登记。

门禁输出中的 `source_progress` 逐一解释每个被引用来源当前处于哪一个采集进度；`source_progress_counts` 只做汇总，不把 `not_attempted` 改写成“采集失败”。因此“尚未采集”表示本地证据池没有该来源，不表示已经尝试过但遇到网络困难。

任一条件不满足，Leader 必须写入 `blocked` handoff，停止后续正式分析。允许生成的只能是明确标注为“研究草稿/预览”的临时页面，不能把草稿、部分分析或 HTML 当作已完成结果。重复引用同一来源只需采集一次，但必须在所有引用它的主张上重新执行语义对齐。

## Phase 4A · 访谈口述史与“秘辛”

人物有播客、演讲或视频长访谈时，把口述史作为独立证据模块处理，而不是把节目简介拆成事实：

1. 先建立完整节目清单，区分公开音频、完整逐字稿、部分逐字稿、节目笔记和不可取得材料。
2. 仅在平台公开 RSS、公开媒体直链或明确下载入口允许时保存音频；不要绕过登录、付费墙或访问控制。音频写入 `raw/interviews/audio/`，RSS 与页面快照分开保存。
3. 视频已有合法本地副本时，用 `extract_audio.py` 调用 FFmpeg 提取第一条音轨；默认输出适合多数 ASR 的 16 kHz、单声道、PCM WAV，并把 JSON receipt 写入案件目录：

   ```bash
   python3 scripts/extract_audio.py raw/interviews/video/interview.mp4 \
     --output raw/interviews/audio/interview.asr.wav \
     --manifest raw/interviews/audio/interview.audio.json
   ```

4. 对每个本地音频记录相对路径、字节数、准确时长与 SHA-256。下载成功不等于已经转写或听完。
5. `oral_history.interviews` 记录节目级元数据与转写状态；`oral_history.disclosures` 只收录有时间戳的披露，注明交叉核对、敏感性和 `self_report|dispute`。
   完整本地 ASR 落盘后，同时记录规范化音频、提取回执、转写文件的案件相对路径与 SHA-256，以及 provider、segment/speaker 计数；没有这些制品时不得把状态写成 `full`。
6. 节目 show notes 或 AI 摘要只能标为 `shownotes_only`，不能冒充逐字稿；关键细节应回听原音频抽查时间戳后再进入主张和事件。
7. 对未具名第三人、家庭与违法指控保持匿名，不利用线索反向识别；叙述统一写“方言自述”“节目笔记称”，除非获得独立文件或同期来源。

口述内容可揭示决策动机、失败经验和关系网络，但它首先是自述证据。跨访谈重复出现仍可能是同一人物的单一来源谱系，不能因节目数量升级为“独立证实”。

### 访谈分析五道强制门禁

任何 `transcript_status=full` 的访谈都必须按下列顺序完成，禁止跳步、并步或先把结果写入事实层。详细字段、制品和失败条件见 [references/interview-analysis-protocol.md](references/interview-analysis-protocol.md)。

1. **事件与既有事实对齐**：逐段或逐章节连接到已有 `event_id`、`claim_id` 和 `source_id`；同时记录未匹配内容与矛盾，不得用语义相似自动升级主张。
2. **说话人身份假设**：ASR 的 `speaker_id` 永远先作为匿名聚类；真实姓名只能以 `hypothesis` 保存，并附开场自报、主持人口播、节目元数据或声纹参考等证据、替代候选和置信度。没有身份证据时只能写角色（嘉宾/主持/未知）。
3. **自动话题分割**：章节必须由连续 ASR segment 组成，起止时间只能复制原始 segment 边界；全部 segment 必须恰好覆盖一次，不得重叠、遗漏或由模型生成时间戳。
4. **“秘辛”候选提取**：只提取带原文 segment、时间码、说话人假设和既有案件差异说明的候选。涉及未具名第三人、家庭、违法或健康内容时默认匿名或排除。此阶段的结果固定为 `candidate`，不得直接写成事实。
5. **第三次交叉验证**：依次完成原始音频/转写锚定、既有案件事实与反证对齐、外部独立来源核验。多个节目中同一当事人的重复讲述仍只算一个 `self_report` 来源谱系。未找到独立旁证时只能落为 `self_reported`、`unverified` 或 `dispute`，不得写成 `verified/credible/fact`。

每一步必须产出独立 JSON 制品和 SHA-256，并由 `research/interviews/analysis-manifest.json` 串联输入、输出、覆盖率、阻断项与完成时间。`case.json.oral_history.analysis` 必须引用该 manifest。只要存在完整本地转写而 manifest 缺失、任一门禁不是 `complete`、转写哈希漂移、章节覆盖不完整、说话人被无证据实名化，或披露缺少第三轮结论，Leader 必须在事实冻结前阻断流程。不得手改 `case.json` 绕过门禁。

选择多人转写供应商时读取 [references/audio-transcription-apis.md](references/audio-transcription-apis.md)。说话人分离只是匿名聚类；没有声纹参考、开场自报或节目元数据支撑时，保留 `Speaker A/B`，不要自动写成真实姓名。章节边界只能绑定已有 ASR segment，不允许语言模型编造时间戳。

腾讯云 ASR 是当前默认免费路由。读取仓库根目录 `.env` 中的运行配置，但绝不把密钥复制到案件制品、handoff、日志或网页。配置项以根目录 `.env.example` 为唯一清单；真实 `.env` 必须被 Git 忽略。长访谈音频超过腾讯云本地数据 5 MB 限制时，上传到私有 COS、生成短时预签名 URL，再提交 `CreateRecTask`；不要把 Bucket 或对象改成公开读。

免费路由使用通用 `16k_zh`。不要把 `16k_zh_en_2.0` 当免费升级：它属于独立计费、没有月度免费额度的大模型 2.0 产品。仅当用户明确接受计费时才允许切换大模型引擎。

首次运行先把 `requirements-asr.txt` 安装到项目 `.venv`，再检查非敏感配置摘要。CAM 身份至少需要 `asr:CreateRecTask` 和 `asr:DescribeTaskStatus`；可以先关联 `QcloudASRFullAccess` 验证，再收紧为自定义策略：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-asr.txt
.venv/bin/python scripts/transcribe_tencent_asr.py --check-config
.venv/bin/python scripts/transcribe_tencent_asr.py \
  raw/interviews/audio/interview.asr.wav \
  --output raw/interviews/audio/interview.tencent-asr.json
```

脚本对不超过 5 MiB 的本地文件使用内联 base64；更大文件才使用私有 COS。只在 ASR 返回 `success` 或 `failed` 终态之后删除临时对象，轮询超时或中断时保留对象供恢复。输出中的 `speaker_id` 是匿名聚类，不是人物身份。

如果 API 返回 `FailedOperation.UserHasNoAmount` 或 `FailedOperation.UserHasNoFreeAmount`，这表示鉴权/CAM 已通过，但当前引擎没有匹配额度。先检查是否误用不受免费包覆盖的 `16k_zh_en_2.0`，再检查免费资源包是否尚未发放、已经耗尽或服务未正确开通；不得自行开启后付费。

## Phase 5 · 从事实推进到决策、环境与关系

围绕时间线中的每个转折建立 `decisions`，回答：当时可见的信息是什么；人物实际做了什么决定，谁共同参与或反对；有哪些现实可选项；资金、政策、行业周期、技术、地域与社会观念如何约束选择；哪些关系提供资本、渠道、声誉、保护或冲突；结果是已证实、合理推断，还是仍未知。

只有文献直接表明“为何决策”时才写 `fact`。基于时间重合、激励和关系图的解释写 `inference`，并列出至少一个替代解释与可能推翻它的新证据。关系边也必须有来源；同框、同地址、同姓或共同出席不等于控制或亲密关系。

## Phase 6 · 落寞、退出与代际传承

分别检查，而不是默认人物一定“落寞”或一定“成功传承”：经营指标、控制权、声誉与个人生活叙事是否朝同一方向变化；退出是失败、主动变现、监管变化、代际交班还是媒体叙事；财富传承通过股权、信托、基金会、家族办公室、遗嘱、职业培养还是品牌控制完成；法律所有权、经济受益权、经营控制权和象征性继承是否属于不同的人。

只写公开且与商业传承直接相关的信息。

## Phase 7 · 冻结事实包

运行：

```bash
python3 scripts/validate_case.py cases/subject-slug/case.json --strict
```

验证通过后，将 `fact_freeze.status` 设为 `frozen`，记录 `frozen_at` 与主张集合摘要。冻结后如修改事实、来源、事件、关系或决策，必须重新校验并更新冻结时间。

## Phase 8 · 可选：MiroFish 情景推演

仅在用户要求“如果当时选择另一条路会怎样”“这些关系可能如何反应”或希望系统性生成替代解释时使用。先导出种子包：

```bash
python3 scripts/export_mirofish_seed.py \
  cases/subject-slug/case.json \
  --question "如果关键决策 X 没有发生，哪些路径最值得观察？" \
  --output cases/subject-slug/simulation
```

把生成的 `mirofish-seed.md` 上传到 MiroFish，按官方的图谱构建 → 环境准备 → 模拟 → 报告流程运行。输出写入 `case.json.scenarios`，`epistemic_status` 固定为 `simulation`，并保留运行版本、问题、输入事实冻结时间和限制。具体接口与安全边界见 [references/mirofish-integration.md](references/mirofish-integration.md)。

创建计划时必须把 seed 绑定到当前 frozen `case.json`。每个 MiroFish 阶段只有在 request/response digest、executor attestation、阶段必需 ID 和终态字段全部匹配时才能 `complete`；离线重放不得把“已有 seed”写成“模拟已执行”。

## Phase 9 · 生成网页

网页不是普通长文，必须让读者一眼分辨“发生了什么、我们凭什么相信、哪些仍是推断”。运行：

```bash
python3 scripts/render_case.py \
  cases/subject-slug/case.json \
  --output cases/subject-slug/site/index.html \
  --view-model cases/subject-slug/site/view-model.json \
  --attest
```

未冻结案件只允许生成带有“研究草稿 · 尚未事实冻结”提示的预览页；不得把该预览页、`view-model.json` 或 HTML attestation 当作正式事实冻结。正式交付仍必须通过 `validate_case.py --strict` 与 Leader 流水线门禁。

页面固定包含：封面结论、起家/登顶/转折/退出或传承四幕、可筛选时间线、关键决策、关系网络、财富路径、主张核查、争议与缺口、来源台账；有访谈材料时增加“访谈口述史”区，显示本地音频、转写状态、时间戳披露和交叉核对；如有模拟，放在独立的“反事实沙盘”区。所有事实可展开到来源，单文件、无 CDN、移动端可读。视觉与交互契约见 [references/webpage-contract.md](references/webpage-contract.md)。

已有完整、冻结案件时，优先让 Leader 重放所有可再生制品：

```bash
python3 scripts/run_pipeline.py cases/subject-slug
```

退出码 `2` 表示某个阶段已写入结构化 `blocked` handoff；读取 `pipeline/run.json` 和对应 handoff 的 `unresolved`，补齐上游证据后再运行。不要绕过门禁手动修改 HTML 或 manifest。

## 交付检查

- `validate_case.py --strict` 通过；
- 页面由当前 `case.json` 重新渲染，不手改生成 HTML；
- 页面中的事实/自述/推断/争议/模拟视觉上可区分；
- 每条关键事实能追到来源，来源 URL、证据锚点、主张级语义复核和上下文审阅记录来自账本或案件数据；
- 没有把来源数量误当独立来源数量；
- 没有将 MiroFish 输出混入历史事实；
- 明确列出尚未解决的问题与下一轮最有效的搜索方向。
