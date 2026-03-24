# Prism — AI Signal Intelligence System

> 精简源、深挖掘、强关联的 AI 信号情报系统

## 1. Problem Statement

现有系统 shu 本质是 ML 圈新闻聚合器：38 个 X 账号 + 8 个 YouTube + GitHub trending，逐条 LLM 摘要。核心缺陷：

- **信噪比低**：38 个 X 源中大量转发党，产出噪声
- **无深度**：只抓推文/标题，不展开 thread、不追 repo 活跃度、不看论文
- **无关联**：逐条分析，无法发现跨源趋势和隐性连接
- **可行动性弱**：摘要告诉你"发生了什么"，但不告诉你"该做什么"

## 2. Design Goals

1. **源精简**：~10 个 X 头部 + arXiv + GitHub trending，少而深
2. **挖掘深度**：thread 全文、repo issue/PR、论文摘要，从"标题党"到"全文党"
3. **关联分析**：批量窗口分析，交叉关联、趋势追踪、去重聚类
4. **双模输出**：人类友好的叙述体日报 + agent 友好的结构化 API
5. **动态管理**：源的增删改通过 CLI/API，无需改代码重启

## 3. Non-Goals (v1)

- 第二层源（工程博客、专利、Discord/Slack）
- 第三层源（硬件动态、投融资、政策）
- 第四层源（私有/内部）
- 实时推送通知
- 多用户/权限系统

## 4. Architecture

```
Pipeline 架构（两阶段分析）:

源采集(adapter) → raw_items(SQLite) → 去重/聚类 → 批量 LLM 分析(24h窗口) → signals + trends → briefing + API
```

### 4.1 Source Layer

三类源，每类打深：

#### X/Twitter（精选 ~10 个头部账号）

- 从 shu 的 38 个中精选原创型博主（非转发党）
- 深度采集：完整 thread 展开 + 引用推文链
- **Thread 采集机制**：
  - 主方案：X syndication embed API 获取时间线推文（同 shu），对检测到的 thread（self-reply chain）通过 playwright 展开完整 thread 页面
  - 检测规则：推文 `in_reply_to` 指向同一作者 → 判定为 thread
  - Fallback：playwright 失败时退化为仅保留首条推文，标记 `thread_partial=true`
  - 引用推文：从推文 `entities.urls` 中提取 `twitter.com/*/status/*` 链接，抓取被引用推文文本（仅一层，不递归）
  - 成本控制：playwright 仅对 thread 触发，非 thread 推文走纯 HTTP（同 shu）
- **X adapter 观测与退化**：
  - 关键指标：thread 完整率（成功展开 / 检测到的 thread 总数），目标 ≥70%
  - `prism status` 展示各源的 thread 完整率和近 24h 趋势
  - 告警：thread 完整率连续 3 天 <50% 时在日报中标红提醒
  - **最小可用定义**：即使 thread 展开完全失效，Prism 退化为"精选头部账号首条推文 + arXiv + GitHub deep"，仍然通过源精选 + 聚类关联 + 叙述体日报优于 shu。thread 是增量价值，不是生存依赖
- 频率：每小时
- 配置示例：
  ```yaml
  - type: x
    handle: karpathy
    depth: thread  # thread | tweet
  ```

#### arXiv 每日新论文（新增）

- RSS 订阅 cs.LG、cs.CL、cs.AI
- 采集：标题 + 摘要 + 作者 + 机构
- 频率：每日（arXiv 固定发布时间）
- 两阶段初筛（cs.LG+CL+AI 日均 100-200 篇，需过滤）：
  1. 关键词白名单（~40 词：LLM, agent, inference, RLHF, MoE, RAG, alignment, reasoning, scaling, multimodal, tool-use, code-generation 等）→ 过滤到 ~30-50 篇
  2. 便宜 LLM 打分（相关性 1-5，≥3 通过）→ 最终 ~10-20 篇进入分析管线
- 配置示例：
  ```yaml
  - type: arxiv
    categories: [cs.LG, cs.CL, cs.AI]
    filter: keyword+llm
  ```

#### GitHub Trending（打深）

- 不只抓首页排名，追踪 star 增速曲线（连续多天采集，算 delta）
- 高增速 repo 深度抓取：README 摘要 + 最近 7 天 issue/PR 标题
- 区分"一日爆火"和"持续增长"
- 配置示例：
  ```yaml
  - type: github_trending
    track_days: 7
    deep_fetch: true  # README + issues
  ```

#### 源管理

- **源唯一标识**：每个源有显式 `source_key` 作为跨 adapter 的稳定业务主键。DB 中同时有自增 `id` 用于 FK。
  - X 源：`source_key = "x:{handle}"`，如 `x:karpathy`
  - arXiv 源：v1 为单例，`source_key = "arxiv:daily"`
  - GitHub Trending 源：v1 为单例，`source_key = "github:trending"`
  - CLI/API 统一用 source_key 操作：`prism source enable x:karpathy`、`prism source remove github:trending`
  - YAML 配置中可显式指定 `key`，未指定时按上述规则自动生成
- **YAML 是声明式配置**（what should exist），**SQLite sources 表是运行时状态**（last_synced_at, consecutive_failures 等）
- **YAML 是最终权威**。冲突仲裁规则：
  - 启动 reconcile：YAML 新增 → DB 插入；YAML 删除 → DB 标记 `origin=yaml_removed`, `enabled=false`；DB 运行时字段（last_synced_at, consecutive_failures）永不被 YAML 覆盖
  - CLI/API 新增：写入 DB（`origin=cli`）**并同时回写 YAML**，保证 YAML 始终是完整 source of truth
  - CLI/API 删除：从 DB 标记 disabled **并同时从 YAML 移除**
  - 自动禁用（连续失败）：仅改 DB `enabled=false, disabled_reason=auto`，不改 YAML
  - **reconcile 与 disabled 的交互**：启动 reconcile 时，若 YAML 中存在该源但 DB `disabled_reason=auto`，**尊重 DB 的 disabled 状态**，不因 YAML 存在而恢复。恢复路径只有两条：(1) `prism source enable <key>` 手动恢复 (2) `auto_retry_at` 触发且成功
  - 这避免了"禁用→重启→恢复→再失败→再禁用"的抖动循环
  - 避免 YAML 和 DB 漂移的核心原则：**任何变更都同时更新两处**
- CLI 动态增删：
  ```bash
  prism source add x --handle karpathy --depth thread
  prism source remove x --handle karpathy
  prism source list
  ```
- REST API 同样暴露 CRUD
- MCP server 可调用

### 4.2 Analysis Pipeline

两阶段设计，核心改进在于从"逐条分析"升级为"批量关联分析"。

#### 阶段一：预处理（无 LLM 或轻量模型）

1. **去重/聚类**：同一话题/repo 被多源提到 → 合并为信号簇（cluster）
   - **算法**（v1 简单规则，不用 embedding）：
     1. URL 精确匹配：同一 URL 出现在不同源 → 同簇
     2. GitHub repo 名匹配：不同源提到同一 `owner/repo` → 同簇
     3. 标题相似度：normalized Jaccard on bigrams > 0.5 → 同簇
     4. 实体共现：两条 item 共享 2+ 个已知实体 → 同簇
   - **增量策略**：hourly `prism cluster` 只将新 raw_items 匹配到当天已有 clusters，不重新聚类历史数据。无匹配则创建新 cluster。
   - **评估闭环**：
     - **Precision（误合并）**：上线第 1 周每日人工抽样 20 个 clusters，标注是否存在不相关 item 被错误合并。目标：precision ≥80%。
     - **Recall 代理指标（漏合并）**：由于直接测量 recall 需要 item-pair 级标注成本过高，v1 用代理指标 — "单条簇占比"（singleton ratio）。若当日 >70% 的 clusters 只含 1 条 item，且人工抽查发现其中有明显应合并的情况，则判定 recall 不足。
     - 若不达标，优先调阈值；若规则法天花板不够，再引入 embedding 或 reranker。
     - `prism cluster --eval` 命令输出当日聚类统计（簇数、平均簇大小、最大簇、单条簇占比）辅助判断。
   - **merged_context 构建**：cluster 内所有 item 的 body 按 published_at 降序（最新优先）拼接，截断到 4000 tokens。超长时按以下优先级保留：(1) 源优先级（X 原创 > arXiv > GitHub）、(2) 新鲜度（published_at 降序）、(3) 内容长度（更长的 item 信息量更大）。注意：signal_strength 此时尚未产出，不可用于预处理阶段排序。
2. **富化**：补全上下文
   - X：thread 展开全文（见 4.1 thread 采集机制）
   - GitHub：README 摘要（前 500 tokens）+ 最近 7 天 issue/PR 标题列表
   - arXiv：论文完整摘要
3. **实体标记**：
   - v1 使用 `config/entities.yaml` 手工维护的已知实体词典
   - 分类：project（vLLM, SGLang, LangChain...）、org（OpenAI, Anthropic, Meta AI...）、person（从 X 源配置自动导入 handle→人名映射）
   - 匹配方式：大小写不敏感的子串匹配，在 title + body 上执行

#### 阶段二：批量 LLM 分析

一个时间窗口内（24h）的所有信号簇一起喂给 LLM。

**Prompt 结构**：

```
System: 你是 AI 信号情报分析师。用户是算法团队 TL，管理 ~40 人和 1500+ GPU。
        你的任务是分析过去 24 小时的信号簇，产出结构化分析报告。

User:
## 昨日热点摘要（用于趋势对比）
{yesterday_top_topics_summary}

## 今日信号簇（共 N 个）
### Cluster 1: {topic_label}
来源: {source_types_and_counts}
实体: {entities}
内容:
{merged_context}
---
### Cluster 2: ...
---

请输出 JSON:
{output_schema}
```

**输出 JSON Schema**：

```json
{
  "clusters": [
    {
      "cluster_id": "int",
      "summary": "中文摘要 80-200字",
      "signal_layer": "actionable|strategic|noise",
      "signal_strength": "1-5",
      "why_it_matters": "60字以内",
      "action": "动词开头 30字行动建议，或'无'",
      "tl_perspective": "100字 TL 视角解读",
      "tags": ["标签1", "标签2"]
    }
  ],
  "cross_links": [
    {
      "cluster_a": "int",
      "cluster_b": "int",
      "relation_type": "same_topic|builds_on|contradicts|same_project|converging_trend",
      "reason": "一句话解释关联"
    }
  ],
  "trends": [
    {
      "topic": "话题名",
      "direction": "heating|cooling|stable|new",
      "evidence": "一句话证据"
    }
  ],
  "briefing_narrative": "3-5段叙述体日报，直接可用于 daily brief"
}
```

**Context window 管理**：
- 预算：~60K tokens（qwen-plus）
- 每个 cluster 的 merged_context 限 4000 tokens（见上文）
- 若当日 clusters 总量超出 context budget，分批调用：按 cluster 内 item 数量降序 + 源多样性（跨源簇优先）排列，多源/大簇优先进入主批次，单源/小簇进入补充批次
- 补充批次只做单簇分析（无交叉关联），结果合并入主批次输出

**增量 vs 日级分析的交互**：
- hourly `--incremental`：新簇用便宜模型做单簇快速打标（summary + signal_layer + tags），存入 signals 表，标记 `analysis_type=incremental`, `is_current=true`
- daily `--daily`：24h 窗口批量分析，**逻辑失效**当天所有 incremental 结果（`is_current=false`），插入新行标记 `analysis_type=daily`, `is_current=true`。历史结果保留用于审计和对比，不物理删除。
- briefing 仅从 `is_current=true AND analysis_type=daily` 的结果生成
- 每条 signal 记录 `model_id`、`prompt_version`、`job_run_id`，支持重放和版本比较

模型选择：
- 预处理/初筛：百炼便宜模型
- 增量单簇分析：百炼便宜模型
- 日级批量关联分析：qwen-plus 或按需升级（强模型，需跨条推理）

### 4.3 Storage

SQLite + WAL + FTS5，表结构：

| 表 | 用途 | 关键字段 |
|---|---|---|
| `sources` | 源配置 | type, handle, config_yaml, enabled, last_synced_at, consecutive_failures |
| `raw_items` | 原始采集 | source_id, url, title, body, author, published_at, raw_json |
| `clusters` | 信号簇 | date, topic_label, item_count, merged_context |
| `cluster_items` | 簇↔原始项 | cluster_id, raw_item_id（多对多） |
| `signals` | LLM 分析结果 | cluster_id, summary, signal_layer, signal_strength, why_it_matters, action, tl_perspective, tags_json, analysis_type(incremental/daily), model_id, prompt_version, job_run_id, created_at, is_current(bool) |
| `cross_links` | 簇间关联 | cluster_a_id, cluster_b_id, relation_type(same_topic/builds_on/contradicts/same_project/converging_trend), reason, job_run_id, is_current |
| `trends` | 趋势追踪 | topic_label, date, heat_score, delta_vs_yesterday, job_run_id, is_current |
| `briefings` | 每日日报 | date, html, markdown, generated_at |

| `job_runs` | pipeline 执行记录 | job_type(sync/cluster/analyze_incremental/analyze_daily/briefing), started_at, finished_at, status(ok/partial/failed), stats_json |

设计决策：
- `raw_items` 与 `signals` 解耦 — 可对同一批数据重新分析
- `clusters` 是中间层 — 一个 cluster 可能包含多条推文 + 论文
- `trends` 按天记录 — 查趋势即 `SELECT * WHERE topic ORDER BY date`
- FTS5 建在 `raw_items` 和 `signals` 上

### 4.4 Output Layer

#### 每日 Briefing（人类友好）

叙述体日报，非逐条列表：

```
# Prism Daily Brief — {date}

## 今日全局
3句话概括今天AI圈最值得关注的事。

## 🔴 需要行动（0-3条）
每条：是什么 → 为什么重要 → 建议你做什么

## 🔵 值得关注的趋势
叙述体：哪些话题在升温，跨源印证了什么

## 📊 GitHub 热力
持续增长 vs 一日爆火，star增速曲线

## 🔗 今日关联发现
LLM发现的跨源连接
```

- 格式：HTML + Markdown 双存
- 推送：Notion 页面
- 按天归档

#### Agent API（结构化）

```
GET  /api/signals?days=7&layer=actionable&topic=vllm
GET  /api/trends?days=14&topic=inference
GET  /api/clusters/{id}
GET  /api/briefing?date=today
GET  /api/search?q=speculative+decoding
POST /api/sources
DELETE /api/sources/{id}
PUT  /api/sources/{id}
```

MCP server 包装 API，agent 可自然语言查信号。

#### CLI

```bash
prism sync                    # 手动触发采集
prism analyze                 # 手动触发分析
prism briefing                # 生成今日日报
prism source list/add/remove  # 源管理
prism status                  # 各源健康状态
```

### 4.5 Scheduling

```
每小时 (launchd)
├── prism sync                    # 采集所有源
├── prism cluster                 # 增量去重聚类
└── prism analyze --incremental   # 新簇单条分析（低成本模型）

每日 08:00 (launchd)
├── prism analyze --daily         # 24h窗口批量关联分析（强模型）
├── prism trends                  # 计算趋势 delta
├── prism briefing                # 生成日报
└── prism publish --notion        # 推送 Notion
```

成本控制：强模型每天只调一次（日级批量分析），小时级用便宜模型。

健康检查：
- 每次 sync 记录成功/失败/耗时
- `prism status` 展示各源最后成功时间、连续失败次数
- 自动禁用策略（区分失败类型）：
  - **硬失败**（HTTP 404/403、源不存在）：连续 2 次 → 自动禁用
  - **软失败**（超时、429 限流、5xx）：连续 6 次（即 6 小时）→ 自动禁用
  - 禁用后 24h 自动尝试恢复一次（`auto_retry_at`），成功则重新启用
  - `prism source enable <handle>` 可手动恢复
  - 日报中展示被禁用/降级的源列表

**错误恢复**：每个 pipeline 阶段独立执行。sync 部分失败（如 5/10 源成功）不阻塞 cluster 和 analyze，后续阶段处理已有数据。失败记录日志并在 `prism status` 中展示。

**数据保留**：raw_items 保留 90 天（定期清理），clusters/signals/trends 永久保留。briefings 按天归档永久保留。

## 5. Tech Stack

- **Language**: Python 3.10+
- **Database**: SQLite (WAL + FTS5)
- **Web**: FastAPI + uvicorn
- **HTTP**: httpx
- **Templates**: Jinja2
- **Config**: python-dotenv + YAML
- **Optional**: playwright (X thread scraping)

零重型依赖，和 shu 一致。

## 6. Relationship to Other Systems

- **shu**: 前身，仅参考代码，不复用。Prism 独立项目。迁移计划：shu 保持运行，Prism 稳定产出 ≥1 周后，对比日报质量，确认后停用 shu 的 launchd job（`com.signal-radar.sync`）。
- **dynasty**: Prism 通过 MCP server 为 dynasty agent 提供信号查询能力，但运行独立。Prism 即是 dynasty 的信号模块外部实现 — dynasty Phase 1B 中的 "signal module v1" 由 Prism 承担，dynasty 内部不再重复建设。
- **Notion**: 日报推送目标。新建独立 Notion 页面（不复用 shu 的页面），避免迁移期数据混淆。

## 7. Success Criteria

核心目标是**更高信噪比 + 更强关联 + 更可行动**。"时间差"（比公众更早发现）是 v2+ 的待验证假设，v1 不以此为验收标准。

1. **信噪比** — 日报 actionable 条目中，≥80% 被用户认为确实值得关注（人工抽样验收）
2. **关联发现** — 日报中有"关联发现"段落，且每周至少 2 次产出用户认可的跨源连接
3. **可行动性** — 每条 actionable 信号有具体行动建议，用户无需二次查找即可决策
4. **趋势追踪** — 能看到话题升温/降温，与用户直觉一致
5. **源管理零摩擦** — CLI 一行命令增删
6. **24/7 无人值守运行** — 健康检查 + 自动禁用故障源
