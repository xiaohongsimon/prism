# Codex Final Review Resolution

> Synthesis of Codex final review into spec updates

## Review Source
- `docs/review/codex/2026-03-24-prism-design-final-review.md`

## Resolutions

### [P1] source identity 非 X 源缺通用主键
**Action**: Accepted. 引入显式 `source_key` 作为跨 adapter 稳定业务主键。X 源 = `x:{handle}`，arXiv = `arxiv:daily`（v1 单例），GitHub = `github:trending`（v1 单例）。CLI/API 统一用 source_key 操作。

### [P1] reconcile 与自动禁用的状态机空白
**Action**: Accepted. 明确规定：reconcile 遇到 `disabled_reason=auto` 时尊重 DB 状态，不因 YAML 存在而恢复。恢复路径只有手动 enable 或 auto_retry 成功。消除抖动循环。

### [P2] 聚类 recall 评估不可靠
**Action**: Accepted. 将 recall 改为代理指标 "singleton ratio"（单条簇占比 >70% + 人工抽查发现应合并情况 → 判定 recall 不足）。更诚实地反映实际可测量能力。

### [P2] cross_links / trends 缺 provenance
**Action**: Accepted. 两表均补 `job_run_id` + `is_current`，与 signals 表一致，支持 rerun 审计。

## Open Questions — CC Response

1. **arXiv/GitHub v1 单例** → 已显式写入 spec（source_key = `arxiv:daily` / `github:trending`）
2. **daily/trends 是否允许 rerun** → 是，provenance 已扩展到所有下游表
