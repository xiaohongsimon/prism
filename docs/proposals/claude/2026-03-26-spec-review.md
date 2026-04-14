# Prism Spec Post-Implementation Review — CC (Opus)

## Summary

Spec 的架构决策是正确的：分层 pipeline、YAML/DB 双态管理、provenance 建模、软硬失败区分。这些在实现中都体现出了设计价值。但 spec 描述的三大差异化能力（X thread 深采、跨源聚类、GitHub 深度追踪），在实现层面都存在显著 gap — 有的是 stub，有的是规则缺失，有的是运行时环境不支持。

当前系统的实际能力是：**arXiv keyword 采集 + GitHub trending 首页抓取 + 规则聚类 + LLM 批量分析 + 叙述体日报**。这本身已经比 shu 有提升（聚类 + 叙述体），但距离 spec 描述的 "精简源、深挖掘、强关联" 还有明确距离。

## Spec-to-Code Gap Analysis

### Gap 1: X adapter — stub 多于实现

| Spec 承诺 | 实现状态 |
|---|---|
| Playwright thread 展开 | `_try_expand_thread()` 返回 None，永远不展开 |
| 引用推文链抓取 | URL 提取了但未 fetch |
| Thread 完整率 ≥70% | 当前 = 0% |
| `prism status` 展示 thread 完整率 | 未实现 |
| 连续 3 天 <50% 告警 | 未实现 |

**判断**：spec 自己定义了"最小可用定义"（thread 失效不影响系统生存），这是对的。但问题是 X 基础采集本身也因 429 限流而不可用 — 这超出了 spec 的退化预案。Spec 只预案了 thread 展开失败，没有预案基础 timeline fetch 失败。

### Gap 2: 聚类 — 4 条规则只实现了 3 条，且效果不足

- **Entity co-occurrence (Rule 4)**: `cluster_items()` 接受 `entities` 参数但 `_find_cluster()` 完全不使用
- **增量聚类**: CLI 每次调用 `cluster_items(..., existing_clusters=[])` 从头聚类，不是 spec 说的"新 item 匹配到当天已有 clusters"
- **merged_context 排序**: 只按 published_at 排序，缺少 spec 定义的源优先级和内容长度排序
- **Live 数据**: singleton ratio ~99.3%，仅 6 个多条簇，0 个跨源簇

**判断**：聚类是 spec 承诺的核心价值之一（"关联分析"），当前效果远未达到 spec 的设想。这不只是实现遗漏，还说明规则法的天花板可能比预期更低 — 尤其是在 X 源不可用的情况下，arXiv 和 GitHub 的 item 缺乏自然的交叉信号。

### Gap 3: GitHub "打深" 完全未实现

- `fetch_repo_details()` 是 stub，返回空 dict
- 无多日 star 增速追踪（DB 中没有 repo 历史表）
- Briefing 模板没有 GitHub 热力专区

### Gap 4: API 路由绕过 source_manager

`routes.py:115-157` 的 POST/PUT/DELETE 直接写 DB，不经过 `source_manager.py`，也不回写 YAML。这正是 Codex 两轮 review 都 flag 过的 ownership drift 问题。

### Gap 5: Daily rerun 语义不完整

`run_daily_analysis()` 只 invalidate incremental signals（line 186-191），不 invalidate 同日期的旧 daily signals 和 cross_links。如果 rerun，会叠加多组 "current" daily 结果。

### Gap 6: `_load_narrative()` 不按日期过滤

`briefing.py:71-74` 直接取最新 `analyze_daily` job_run，不管日期。这意味着 2026-03-26 的 briefing 可以复用 2026-03-25 的 narrative。

## Design Decisions: What Worked

1. **source_key 设计** — 跨 adapter 统一标识，reconcile 逻辑正确尊重 auto-disabled 状态，无重启抖动
2. **分层存储 (raw → cluster → signal → briefing)** — 支持 reanalysis 和 audit，pipeline 各阶段可独立重跑
3. **软硬失败区分** — X 的 429 被正确分类为软失败，6 次后才禁用，避免过早下线
4. **job_runs + provenance** — signals 表的 model_id/prompt_version/job_run_id/is_current 设计正确
5. **YAML 是权威 + CLI 回写** — source_manager 实现了这个设计，reconcile 逻辑与 spec 一致

## Design Decisions: What Didn't Work

1. **Topic label 做趋势追踪键** — `calculate_trends()` 对 topic_label 做精确匹配，但 label 是从最长标题启发式生成的，天然不稳定。跨天同一话题的 label 几乎不可能完全一致。
2. **X syndication 作为唯一采集路径** — spec 预案了 thread 展开的退化，但没有预案 timeline 本身的不可用。实际运行中 429 发生在更前置的位置。
3. **YAML 权威 + API 直写 DB 并存** — 设计原则明确但 API 实现没有遵循。
4. **Entity tagging 与 clustering 脱节** — `entities.py` 做了标记但结果只打印到 stdout，不进入 DB 也不参与聚类。

## Uncovered Risks

### R1: Fail-open pipeline（最严重）

系统各阶段 fail-open：
- `analyze --daily` 返回 0 signals → `briefing --save` 仍保存空报
- `publish --notion` 失败打印 error 但返回 0 → `daily.sh` 继续
- 无人值守场景下会持续产出空白/过时日报，用户不会被告知

### R2: LLM 单点依赖

408 个 cluster 没有 current signal — 说明 LLM (127.0.0.1:8002) 不可用时，增量分析默默失败。Pipeline 继续跑但不产出分析结果。

### R3: Scheduler 无锁

hourly.sh 和 daily.sh 无 single-flight 保护。如果 hourly 分析还在重试 LLM 时下一个 hourly 触发，或 daily 和 hourly 重叠，依赖 SQLite WAL 的并发能力但没有显式互斥。

### R4: 数据膨胀无清理

Spec 说 raw_items 保留 90 天，但代码中无任何清理逻辑。

## Prior Review Follow-Up

| Item | Spec 已修复? | 代码已实现? | 残余 Gap |
|---|---|---|---|
| [P0] 目标从"更早"改为"更准" | Yes | Yes | 无 |
| [P0] merged_context 不依赖后置字段 | Yes | Partial | 只用 freshness，缺源优先级+长度 |
| [P1] X thread 退化路径 + 观测 | Yes | No | thread stub + 无完整率指标 + X 基础采集也不可用 |
| [P1] 聚类评估闭环 | Yes | Partial | eval_stats 有，但 entity rule 未实现，增量聚类未实现 |
| [P1] signals provenance | Yes | Mostly | Schema 正确，但 rerun 不 invalidate 旧 daily |
| [P1] 源管理仲裁规则 | Yes | Partial | source_manager 正确，API 绕过 |
| [P1] source_key 统一标识 | Yes | Yes | 无重大 gap |
| [P1] reconcile 尊重 auto-disabled | Yes | Yes | API PUT 可绕过 |
| [P2] 聚类 recall 评估 | Yes（改为代理指标） | Yes | singleton ratio = 99%，说明 recall 极差 |
| [P2] cross_links/trends provenance | Yes | Yes | Schema 有 job_run_id + is_current |

## Operational Readiness Assessment

**结论：未就绪。**

最低需补齐项：

1. **Fail-closed gating** — analyze/briefing/publish 在输出为空时 exit 非 0，daily.sh 据此中止
2. **X 替代方案或降级声明** — 要么换采集方式（RSS/nitter/API），要么在 spec 中显式去除 X 作为 v1 依赖
3. **Date-scoped briefing** — `_load_narrative()` 必须按日期过滤
4. **Scheduler 锁** — flock 或 PID file 防 overlap
5. **Landing uncommitted changes** — 未提交的修复是真实运行时问题，需要 commit 并加测试

## Recommendations for v1.1 (按优先级)

### P0 — 运行可靠性
1. Fail-closed pipeline gating（analyze 0 outputs → briefing 不执行）
2. 提交当前 uncommitted changes + 补回归测试
3. `_load_narrative()` 按日期过滤
4. daily rerun 先 invalidate 同日期旧 daily signals + cross_links

### P1 — 核心价值补齐
5. 实现增量聚类（新 item 匹配当天已有 clusters）
6. 实现 entity co-occurrence 聚类规则 (Rule 4)
7. Entity tagging 结果入 DB + 传入 clustering
8. merged_context 排序实现完整 spec 规定（源优先级 + 新鲜度 + 长度）
9. API 路由通过 source_manager 统一管道

### P1 — X 策略决策
10. 选定 X 替代采集方案（或显式降级 spec）：
    - 方案 A：付费 API（成本较高但最稳定）
    - 方案 B：替代前端（Nitter 等，但也不稳定）
    - 方案 C：Spec 降级 — 去除 X 作为 v1 必要源，改为"best-effort"

### P2 — 功能完整度
11. GitHub deep fetch 实现（或从 spec 移除）
12. Topic label → canonical topic key 分离（趋势追踪用稳定键）
13. 90 天 raw_items 清理任务
14. Scheduler flock 保护
15. arXiv keyword vs keyword+llm 配置对齐
