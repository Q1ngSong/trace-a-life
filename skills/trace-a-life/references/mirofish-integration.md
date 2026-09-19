# MiroFish 集成与隔离

MiroFish 用于基于已核验种子材料构建图谱、生成角色与环境，并进行多 Agent 社会模拟。它适合回答“如果当时另一项决定发生，哪些参与者反应与二阶效应值得观察”，不适合证明历史事实或给出统计意义上的因果估计。

## 何时进入

必须同时满足：

- `fact_freeze.status == "frozen"`；
- 关键人物与实体已经消歧；
- 关系边有来源和置信度；
- 用户明确需要反事实、情景推演或替代解释；
- 模拟问题不会要求暴露私人数据。

## Seed 内容

`export_mirofish_seed.py` 生成：案件范围和冻结时间；只包含 `verified`、`credible` 与明确标注的 `partial` 主张；人物、公司、机构及有证据的关系；决策发生前已经可见的信息；社会、政策、行业和资本环境；不确定性、缺口和禁止模拟为事实的内容；推演问题与观察指标。

默认不把 `self_reported`、`unverified`、`contradicted` 作为世界事实注入。它们可以作为“某角色持有的说法”单独写入，并明确来源身份。

seed manifest 同时记录 `source_case_sha256` 与 `seed_sha256`。加载时必须传当前 frozen `case.json`，并核对主体、冻结时间、seed 正文中的实际 claim status 和文件 digest；只靠 manifest 自述的状态不能创建 SimulationPlan。

## 官方流程映射

参考快照中的官方实现采用多阶段流程，不存在一个可假定的 `/api/simulate` 单接口：

1. 图谱 API：ontology generation → graph build → task polling；
2. simulation create；
3. simulation prepare → preparation status；
4. simulation start → run status；
5. report generate → generation status → report retrieval；
6. 可选 ReportAgent chat 或 agent interview。

本 Skill 默认导出兼容的上传材料，让用户通过 MiroFish 官方 UI 运行。只有在用户明确授权调用其本地实例，且当前版本的 API 请求体已从运行中的实例或对应 commit 验证后，才自动执行 API 流程。

每一阶段必须先 `start`，再由 HTTP executor 返回 request/response digest attestation。`complete` 会核对 attestation、`success=true`、该阶段必需的 project/task/graph/simulation/report id，以及 polling 阶段的终态字段；空 output、非终态、无 attestation 或 digest 不匹配均不能解锁下一阶段。离线 Leader replay 只写 `requested/reported-only`，不得伪造执行完成。

## 输出回收

任何模拟结果写入 `case.json.scenarios[]` 时固定包含 `id`、`title`、`question`、`epistemic_status: simulation`、`fact_freeze_at`、`mirofish_version`、`summary`、`signals` 与 `limitations`。

网页必须把这一区域与史实分开，并使用“沙盘”“情景”“可能”而非“真相”“证明”“必然”。

## 安全

只连接用户控制或信任的 MiroFish 实例。不要把含敏感商业数据的案件上传到未知公网实例。MiroFish 属于可执行多 Agent 模拟系统，应固定版本、保存输入、记录模型和配置，并把输出视为模型产物。
