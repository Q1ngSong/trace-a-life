# Wave 2 · 确定性商业分析提案

本方向把已有案件中的事件、决策、社会环境、关系、财富记录和原子主张整理成 `xray-business-analysis/1` 提案。提案只复制已有记录和显式 `epistemic_status`/`status`，不生成新的因果结论。

## 六个主题

提案固定输出起家、扩张、关键决策、社会环境、人际关系、财富变化与传承六个主题。每个主题都有 `verified_facts`、`self_reports`、`inferences`、`unknowns` 四层；未带显式状态的 context/wealth 记录保留在 `unknowns`，等待后续核验。

事件章节的归属是显式映射：`origins`→起家、`ascent`→扩张、`turning-point`→关键决策、`legacy`→财富变化与传承；未知章节不会被关键词猜测归类。

## 交付与限制

- 构建器：`skills/trace-a-life/xray_person/analysis/proposal.py`
- CLI：`skills/trace-a-life/scripts/build_analysis.py`
- 默认输出：案件目录下的 `analysis/proposal.json`
- 支持完整 `case.json` 和公开 `public-research/1` `results.json` 包装。
- 不接入模型、图数据库或网络服务，不回写 `case.json`，不修改网页。
