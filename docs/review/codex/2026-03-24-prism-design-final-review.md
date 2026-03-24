# Prism — Codex Final Review

> 对 synthesis 后 spec 的最终审查意见

## Review Scope

- Spec: `docs/specs/2026-03-24-prism-design.md`
- Synthesis: `docs/reviews/synthesis/2026-03-24-codex-review-resolution.md`
- Prior review: `docs/review/codex/2026-03-24-prism-design-review.md`

## Findings

### [P1] source identity 设计仍未完全自洽，非 X 源缺少通用主键

当前 spec 将 source identity 定义为 `(type, handle)`，并将其描述为“全局唯一”。这个定义对 X 源成立，但对 arXiv 和 GitHub Trending 这类配置里没有 `handle` 字段的源并不天然成立。与此同时，source enable/disable 的 CLI 例子也继续假设使用 `handle` 操作。

这会导致后续实现时出现两种不理想结果之一：

- 要么在 DB / CLI / API 中为不同 adapter 写分支特判
- 要么临时发明非 X 源的伪 `handle`，但没有在 spec 中定义

建议补一个统一规则：

- 方案 A：引入显式 `source_key` 作为跨 adapter 的稳定业务主键
- 方案 B：明确声明 v1 中 arXiv 和 GitHub Trending 只能是单例 source，并给出固定 key 命名

在这点补清之前，source CRUD 和 reconcile 实现都会带着隐式假设前进。

### [P1] YAML 权威与自动禁用的关系仍有状态机空白

spec 现在明确了 YAML 是最终权威，也强调“任何变更都同时更新两处”；但自动禁用场景又明确只写 DB、不改 YAML。这比上一版已经好很多，但还差最后一跳：当进程重启并再次执行 reconcile 时，系统应该保留 DB 中的 `enabled=false`，还是按 YAML 恢复启用，文档还没有写死。

这不是措辞问题，而是状态机问题。如果实现者理解不一致，就会出现：

- 某个源被自动禁用
- 重启后又因为 YAML 里仍存在而被重新启用
- 随后再次失败，再次禁用

这种抖动状态会直接影响无人值守运行的稳定性。建议在 spec 中显式规定：

- `enabled` 的最终有效值由哪个字段/规则决定
- reconcile 是否尊重 DB runtime disable
- 自动禁用后的恢复条件是“人工 enable / auto_retry 成功”还是“仅因 YAML 仍存在就恢复”

### [P2] 聚类评估方案能测 precision，但不足以可靠评估 recall

spec 已经补上了聚类评估闭环，这是很大的进步；但目前的评估办法是“每日人工抽样 20 个 clusters，标注误合并/漏合并”。这个流程对误合并比较友好，因此比较适合近似评估 precision；但对漏合并并不充分，因为漏合并往往发生在“本该合在一起却散落在别处”的 cluster 之间，仅抽已形成 cluster 很难系统性发现它们。

因此当前写成 `recall >= 60%` 会给人一种“已经有可靠测量办法”的错觉。更稳妥的处理方式是二选一：

- 把 recall 改成更弱的代理指标
- 增加 item/pair 级抽样方法，用于专门检查漏合并

如果不改，后续很容易出现“指标写得很完整，但其实没人能稳定算出来”的问题。

### [P2] provenance 已覆盖 signals，但 cross_links / trends 仍缺运行归因

这版 spec 已经把 `signals` 的 provenance 补得比较完整，包括 `analysis_type`、`model_id`、`prompt_version`、`job_run_id`、`is_current` 等，这个方向是正确的。但 `cross_links` 和 `trends` 依然只有业务字段，没有与某次具体运行结果绑定。

如果后续 daily analysis 或 trends 计算允许 rerun，就会出现一个审计缺口：

- 某条 signal 可以追溯到哪次 job
- 但某条 trend 或 cross-link 却无法直接确认来自哪次重算

这会削弱“保留历史结果用于审计和对比”的价值。建议最少为 `cross_links` 和 `trends` 增加 `job_run_id`；如果这些表也有“当前生效版本”语义，则应与 `signals` 一样显式建模。

## Open Questions

1. arXiv 与 GitHub Trending 在 v1 是否被设计成永远单例 source？如果是，建议直接写进 spec，而不是让实现层自行猜测。
2. daily analysis / trends 是否允许人工或自动 rerun？如果答案是允许，那么 provenance 应当继续向下游表扩展。

## Final Stance

这轮 spec 更新已经解决了前一版 review 中最关键的问题，尤其是：

- 将 v1 目标从“更早”收敛到“更准、更强关联、更可行动”
- 修复了 `merged_context` 的时序自洽性
- 明确了 thread 退化路径和观测指标
- 为 signals 增加了 versioning / provenance
- 为 source 管理补上了更具体的仲裁规则

当前我没有新的 P0 阻塞项。剩余问题主要集中在两类：

- 设计一致性：source identity、enabled 状态机
- 可验证性与审计性：cluster recall、cross_links/trends provenance

我的结论是：spec 已经可以进入实现阶段，但建议在真正开工前，把前两条 P1 再补清楚，这会显著减少后面实现时的歧义和返工。
