# Codex Review Resolution

> Synthesis of Codex review feedback into spec updates

## Review Source
- `docs/review/codex/2026-03-24-prism-design-review.md`

## Resolutions

### [P0] 目标与手段张力 — "更早"vs"更准"
**Action**: Accepted. 重写 Success Criteria，将"时间差"降为 v2+ 待验证假设，v1 核心验收目标改为：信噪比、关联发现、可行动性。Problem Statement 中也移除"无时间差"作为首要缺陷，替换为"可行动性弱"。

### [P0] merged_context 截断依赖后置字段
**Action**: Accepted. 修复为预处理阶段可用的排序依据：源优先级 > 新鲜度 > 内容长度。明确注释 signal_strength 此时不可用。

### [P1] X thread 脆弱性
**Action**: Accepted. 新增：thread 完整率指标（目标 ≥70%）、prism status 展示、连续 3 天 <50% 告警、最小可用定义（thread 失效不影响系统生存）。

### [P1] 聚类缺评估闭环
**Action**: Accepted. 新增：上线第 1 周每日人工抽样 20 clusters，precision ≥80% / recall ≥60% 目标，`prism cluster --eval` 统计命令，不达标时的升级路径（调阈值 → embedding/reranker）。

### [P1] signals 表缺 provenance 元数据
**Action**: Accepted. signals 表新增：analysis_type, model_id, prompt_version, job_run_id, created_at, is_current。新增 job_runs 表。daily 分析逻辑改为逻辑失效（is_current=false）而非物理覆盖。

### [P1] 三套源管理冲突仲裁
**Action**: Accepted. 定义 source identity = (type, handle)。明确 YAML 为最终权威，CLI/API 变更同时回写 YAML。自动禁用仅改 DB 不改 YAML。详细冲突仲裁规则已写入 spec。

### [P2] 自动禁用策略偏激进
**Action**: Accepted. 区分硬失败（404/403, 2 次禁用）和软失败（超时/429/5xx, 6 次禁用）。禁用后 24h 自动尝试恢复，支持手动 enable。

## Open Questions — CC Response

1. **v1 首要价值** → 已明确：更高信噪比 + 更强关联 + 更可行动
2. **聚类验收** → 已定义人工抽样 + 定量目标 + 升级路径
3. **daily 覆盖方式** → 逻辑失效（is_current=false），历史保留
4. **source 唯一标识** → (type, handle)，YAML 最终权威
5. **thread 失效后最小可用** → 成立，差异化靠源精选 + 聚类关联 + 叙述体日报
