# Spec Post-Implementation Review — Synthesis

> 综合 Codex (GPT-5.4)、CC (Opus)、Local 27B 三方 review 的最终结论

## Executive Summary

**Spec 的架构设计是成功的**。分层 pipeline、source_key 统一标识、YAML/DB 双态管理、provenance 建模、软硬失败区分 — 这些设计在实现中都体现了价值，未出现需要推倒重来的架构错误。

**但 spec 承诺的三大差异化能力在实现中都有显著 gap：**

| 差异化能力 | Spec 承诺 | 实现状态 | 三方一致度 |
|---|---|---|---|
| X thread 深采 | Playwright thread 展开、引用链、70% 完整率 | Stub，完整率=0%，基础采集因 429 不可用 | 三方一致 |
| 跨源聚类关联 | 4 规则 + 增量匹配 + entity co-occurrence | 3/4 规则，无增量匹配，entity 未入 DB，singleton ratio=99% | 三方一致 |
| GitHub 深度追踪 | 多日 star 曲线、README+issues、"一日爆火 vs 持续增长" | Stub，无历史 star 表 | 三方一致 |

**当前系统实际定位**：arXiv keyword 采集器 + 浅层 GitHub trending + LLM 批量分析 + 叙述体日报。这比 shu 有提升（聚类概念 + 叙述体格式），但未达到 spec 描述的 "精简源、深挖掘、强关联"。

## Agreements (CC + Codex + Local 一致)

以下问题三方 review 完全一致，无争议：

### 1. Entity co-occurrence 是死代码 [HIGH]
- `cluster_items()` 接受 `entities` 参数但 `_find_cluster()` 不使用
- `entities.py` 做了标记但只 print 到 stdout，不入 DB 不参与聚类
- **影响**：跨源聚类 recall 极差（99% singleton），直接损害核心价值

### 2. API 路由绕过 source_manager [HIGH]
- `routes.py:115-157` POST/PUT/DELETE 直接写 DB，不回写 YAML
- 这正是 Codex 两轮 review 都 flag 过的 ownership drift

### 3. Daily rerun 语义不完整 [MEDIUM]
- `run_daily_analysis()` 只 invalidate incremental，不 invalidate 同日期旧 daily signals/cross_links
- Rerun 会叠加多组 "current" 结果

### 4. `_load_narrative()` 不按日期过滤 [MEDIUM]
- `briefing.py:71-74` 取最新 job_run 不管日期
- 导致 briefing 可能混入非当日 narrative

### 5. merged_context 排序不完整 [MEDIUM]
- 只按 published_at 排序，缺源优先级 + 内容长度
- Local 27B 正确指出 "Source type is not available on RawItem" — 实现需要连表查 source type

### 6. Fail-open pipeline [CRITICAL]
- Codex 独到发现：查了 `data/daily.log`，确认 2026-03-26 daily 产出 0 signals 但仍保存 briefing
- 三方一致认为这是无人值守运行的最大风险

## CC 独到观点

### Topic label 不适合做趋势追踪键
- `trends.py` 对 topic_label 做精确匹配，但 label 从启发式生成，天然跨天不稳定
- **Codex 也独立发现了这个问题**，建议引入 canonical topic key
- **共识**：这是设计缺陷，不是实现 bug

### X 需要比 "thread 退化" 更前置的预案
- Spec 预案了 thread 展开失败，但没预案 timeline fetch 本身不可用
- 当前所有 X 源因 429 被 auto-disabled，问题发生在比 thread 更基础的层面

## Codex 独到观点

### 系统 fail-open 的具体运行时证据
- Codex 查了 `data/sync.log` 和 `data/daily.log`，给出了精确的失败场景
- 408 clusters 无 current signal = LLM 不可用时增量分析默默失败
- 2026-03-26 briefing 存了空报 = analyze 0 outputs 没有阻止 briefing

### Uncommitted changes 方向正确但需 commit + 测试
- 修复了 adapter config 传递、YAML→DB config 同步、RSS 2.0 解析、无标题 topic label
- 这些是真实运行时问题的修复，不应继续挂着

### arXiv 配置漂移
- 代码支持 `keyword+llm`，但 YAML 配成了 `keyword` only
- 应显式声明这是临时选择还是永久策略

## Local 27B 独到观点

### GitHub star delta tracking 完全缺失
- 不只是 `fetch_repo_details` stub，连存储 star 历史的 DB 表都没有
- 这意味着 "一日爆火 vs 持续增长" 的区分根本无法实现

## Prior Review Resolution Status

| 原始 Review Item | Spec 修复 | 代码实现 | 最终状态 |
|---|---|---|---|
| [P0] 目标 "更准" 而非 "更早" | ✅ | ✅ | **已关闭** |
| [P0] merged_context 不依赖后置字段 | ✅ | ⚠️ 部分 | 缺源优先级 + 长度排序 |
| [P1] X thread 退化路径 | ✅ | ❌ | Thread stub + X 采集本身不可用 |
| [P1] 聚类评估闭环 | ✅ | ⚠️ 部分 | eval_stats 有，entity rule 和增量聚类缺 |
| [P1] signals provenance | ✅ | ⚠️ 大部分 | Schema 正确，rerun 不 invalidate 旧 daily |
| [P1] 源管理仲裁 | ✅ | ⚠️ 部分 | source_manager OK，API 绕过 |
| [P1] source_key 统一 | ✅ | ✅ | **已关闭** |
| [P1] reconcile 尊重 auto-disabled | ✅ | ✅ | **已关闭**（API PUT 有小漏洞） |
| [P2] recall 代理指标 | ✅ | ✅ | 指标说明 recall 极差（99% singleton） |
| [P2] cross_links/trends provenance | ✅ | ✅ | **已关闭** |

## Operational Readiness Verdict

**未就绪。** 三方一致。

## Prioritized Action Items

### P0 — 不做无法安全运行 (must-fix before unattended)

| # | Item | Effort Est. |
|---|---|---|
| 1 | **Fail-closed gating**: analyze 0 outputs → exit 1 → briefing/publish 不执行 | Small |
| 2 | **`_load_narrative()` 按日期过滤**: WHERE job_type AND date = ? | Small |
| 3 | **Daily rerun invalidation**: invalidate 同日期旧 daily signals + cross_links | Small |
| 4 | **Commit uncommitted changes** + regression tests | Small |
| 5 | **Scheduler flock**: hourly.sh/daily.sh 加 `flock` 防 overlap | Small |

### P1 — 不做核心价值无法兑现

| # | Item | Effort Est. |
|---|---|---|
| 6 | **Entity tagging 入 DB + 参与聚类** (Rule 4 实现) | Medium |
| 7 | **增量聚类**: 新 item 匹配当天已有 clusters | Medium |
| 8 | **API 路由通过 source_manager 统一管道** | Medium |
| 9 | **merged_context 完整排序**: 连表查 source type 实现源优先级 | Small |
| 10 | **X 策略决策**: 付费 API / 替代前端 / spec 降级 — 三选一 | Decision |
| 11 | **Topic label → canonical key 分离** (趋势追踪稳定性) | Medium |

### P2 — 功能完整度

| # | Item | Effort Est. |
|---|---|---|
| 12 | GitHub deep fetch 实现（或从 spec/briefing 移除） | Medium-Large |
| 13 | 90 天 raw_items 清理 cron | Small |
| 14 | arXiv keyword vs keyword+llm 配置对齐 + 文档化 | Small |
| 15 | Thread 展开实现（或等 X 采集策略确定后再做） | Large |

---

## Decision Report

- **Preset**: debate
- **Stakes**: medium
- **Models invoked**: Codex (✓), Local-27B (✓)
- **Candidates**: 2 proposals generated (Codex + CC), both passed contract validation
- **Round 1**:
  - Local pre-analysis: used — 3 objections surfaced (entity dead code, thread stub, merged_context ordering), all 3 adopted
  - Codex critique: N/A (debate — Codex generated proposal, CC acted as synthesizer)
- **Round 2**: skipped (medium stakes)
- **Opus decision**: APPROVED
- **Degradation**: none
- **Key observation**: Codex 查了 runtime 数据 (logs, DB) 给出了比纯代码 review 更强的证据。Local 27B 在行号引用和死代码检测上非常精准。三方在所有核心发现上高度一致，无重大分歧。
