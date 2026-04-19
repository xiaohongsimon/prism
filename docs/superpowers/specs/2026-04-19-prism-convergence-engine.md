# Prism Convergence Engine — 三支柱 + 收敛回路

> Date: 2026-04-19
> Status: Design, advancing to implementation plan
> Author: xiaohongsimon + Claude

## 0. 用户的三条核心诉求

1. **信息收集要全** — 不能漏掉我真正关心的东西
2. **推荐越来越准** — 要有机制保证和我偏好逐步对齐收敛
3. **指引我的技术品味越变越好** — 不是一味投我所好，要能往上拉我

对应三个引擎 + 一个周循环把它们粘合。

## 1. 当前失败的证据

详见 DB 查询（127 次投票，75% "都不行"）。根本原因：`sources.yaml` 为"AI 前沿工程师" persona 建池，但学出的偏好权重显示用户实际在"TL/方法论/个人成长"域。正偏好: `zarazhangrui/danshipper/方法论/产品设计/个人成长`；负偏好: `arxiv/LLM/AI Agent/strategic layer`。
同时：`external_feeds` 2 条全 unprocessed；`pair_strategy` 127/127 硬编码 exploit；源权重计算了但没反哺 sync 频率——闭环多处断点。

## 2. 架构总览

```
           ┌─────────────── Weekly Convergence Loop ───────────────┐
           │                                                       │
           ▼                                                       │
   ┌──────────────┐      ┌──────────────┐      ┌──────────────┐    │
   │  Coverage    │─────▶│  Alignment   │─────▶│  Taste       │────┤
   │  Engine      │      │  Engine      │      │  Cultivation │    │
   │  (信息全)     │      │  (越来越准)   │      │  (品味)       │    │
   └──────────────┘      └──────────────┘      └──────────────┘    │
           ▲                     ▲                     ▲           │
           │                     │                     │           │
           └──── preference_weights + decision_log ────┘           │
                             │                                     │
                             └────── user vote / feed ─────────────┘
```

三个引擎共享一份状态（`preference_weights` + `signal_scores` + `decision_log`），由一个周循环 + 每次投票触发的实时更新串起来。

## 3. Engine 1 — Coverage（信息收集全）

### 目标

为每个用户高权重话题/作者，保证至少有 **3 条独立入口**（不同平台或不同作者），并每周主动搜索"我在漏什么"。

### 组件

**3.1 Persona Snapshot**（基线，一次性/按需重跑）

- `/persona` 页：6 题问卷 + 自由文字 + 种子账号列表
- LLM 抽取 → `preference_weights` 注入 `dimension='persona_bias'` 行（clip 到 ±5）
- 同时生成 20-30 条候选源 → `source_proposals`
- Versioned: 新 snapshot active 后旧的 bias 归零

**3.2 Source Graph Expander**（持续运行）

- 输入：当前 `preference_weights` 中 `dimension='source' or 'author'` 且 weight > 1.0 的源
- 动作：LLM 为每个源产出"此人/此源的邻居"列表——TA 关注的人、TA 转发最多的 X 账号、TA 引用的论文作者、TA 的 podcast 嘉宾等
- 输出：候选源 → `source_proposals(origin='graph_expansion', origin_ref=<source_key>)`
- 频率：每周一次（周日夜里），每次每个种子源最多产出 5 条候选
- 去重：同 handle 已在 sources.yaml 或已 rejected 的跳过

**3.3 Gap Detector**（周任务）

- 读取高权重 tag（如 `方法论 +2.5`），统计该 tag 下过去 14 天召回量
- 若 tag weight > 1.5 但召回量 < 10 条 → 判定为"供不应求"，LLM 建议新源
- 覆盖维度：每个高权重 tag 至少要有 X/YouTube/长文 三类源（缺哪类补哪类）

**3.4 Blindspot Scanner**（周任务）

- LLM prompt：给定用户 persona + top 10 偏好 tag，"一个该领域的资深从业者通常还会关注什么但当前源列表里没有？"
- 输出 5-10 条候选源 → `source_proposals(origin='blindspot')`
- 差异化：与 graph/gap 去重

**3.5 External Feed Consumer**（实时 + 补跑）

- `external_feeds` 表每小时扫 `processed=0`
- LLM 抽：`url_canonical / author / content_type / topics / summary / source_hint`
- 动作：
  1. 注入合成 signal（feedback_weight=3.0）到 pool
  2. 若 source_hint 不在 sources.yaml → 生成 `source_proposals(origin='external_feed')`
  3. 若该 author 在图里，boost 其邻居优先级
- 触发：提交时立即跑一次 + 每小时兜底

**3.6 Proposal Review — `/taste/sources`**

- 所有 `source_proposals WHERE status='pending'` 按 origin 分组展示
- 每条显示：源配置、rationale、origin（你投喂的 / 周扫图发现的 / Gap 发现的 / Blindspot）、预览（最近 3 条样本）
- 一键 accept / reject / snooze (7 天)
- Accept → 写 sources.yaml（ruamel.yaml 保注释），decision_log 记录
- 2 周未 review 的自动 snooze → 30 天后若仍 pending 自动 reject

## 4. Engine 2 — Alignment（推荐越来越准）

### 目标

让系统对用户偏好的理解可审计、可纠偏、能收敛。**关键升级**：权重不再是 scalar，而是 `(weight, confidence, last_touched)` 三元组。

### 组件

**4.1 Confidence-aware preference weights**

Schema 升级：

```sql
ALTER TABLE preference_weights ADD COLUMN confidence REAL NOT NULL DEFAULT 0.3;
ALTER TABLE preference_weights ADD COLUMN sample_count INTEGER NOT NULL DEFAULT 0;
```

- 每次投票触发：`confidence ← min(1.0, confidence + 0.05)`，`sample_count += 1`
- 长期未触到的 key：confidence 按天衰减 `× 0.995`
- 在 pool 筛选和 pair 打分时：`effective_weight = weight * confidence`

**4.2 Active-learning pair selection**

升级现有 70/20/10：

| 策略 | 比例 | 选法 |
|---|---|---|
| exploit | 50% | 高 effective_weight、confidence > 0.6 |
| explore | 20% | 两条都是新信号或触及 confidence < 0.4 的维度 |
| **active** | 15% | 特意挑偏好不确定处的对比（两条信号在某维度上权重接近） |
| reach | 10% | Taste cultivation（见 §5） |
| random | 5% | 打破局部最优 |

每次 `select_pair()` 返回真实 strategy 名，写入 `pairwise_comparisons.pair_strategy`——修掉 127/127 硬编码。

**4.3 EWMA 时间衰减**

- 老投票对 preference weight 的影响按指数衰减（半衰期 60 天可配置）
- 用户兴趣迁移时，旧权重不会死死压住新信号
- 实现：每次 `_update_preference_weights` 前先对当前 weight 做 `weight *= decay_factor`，其中 decay 根据距 `last_touched` 的天数计算

**4.4 偏好反哺召回频率**（补上闭环）

- 每日 06:00 任务读 `preference_weights(dimension='source')`
- 计算每个源的 sync 频率：`base_freq × clamp(1 + effective_weight × 0.3, 0.1, 3.0)`
- 更新 `source_sync_schedule(source_key, next_run_at, interval_minutes)` 表（新）
- launchd sync 作业读这个表而不是一刀切

**4.5 Taste Page — `/taste`**

纠偏界面，展示系统当前的"信念状态"：

- **你的画像**（只读展示）：当前 active persona_snapshot + 自动生成的 1-2 句总结
- **系统理解的你喜欢**：top 20 正权重 key 按 effective_weight 排序，每条有 ❌ 按钮（one-click 归零）和 slider（±5 调整）
- **系统理解的你不喜欢**：top 20 负权重 key，同上
- **不确定**：confidence < 0.3 的 key，引导做 active-learning pair
- **最近变化**：过去 7 天 weight 变化最大的 10 条
- **即将发生**：即将 sync 调频的源、即将提议的新源

所有用户在此页的修改写 `user_overrides` 表（新），优先级最高（effective_weight 先 check override，再 fall back 到学习值）。

**4.6 "连续都不行" 熔断升级**

当前：3 连 neither → 切随机。
升级：3 连 neither → 弹出 modal，三个选项：

1. "内容不对味" → 跳到 `/taste` 直接改偏好
2. "累了换一批" → 切随机 + 提高 explore 比例 30 分钟
3. "我看看随机的" → 切随机一次

## 5. Engine 3 — Taste Cultivation（品味提升）

### 目标

系统不能只投所好。10% 的 pair 是"刻意拔高"——在用户感兴趣的领域里放入"更高段位"的内容，慢慢拉品味。

### 核心假设

"品味提升"不是让用户看自己不感兴趣的东西，而是在 **TA 感兴趣的话题域** 内，引入 TA 还没看过的 **更深度/更经典/专家共识度更高** 的内容。

### 组件

**5.1 Canon 库**（每周离线生成）

对每个高权重 tag（weight > 1.5）：

- LLM prompt: "如果一个人想真正掌握【话题X】，经典必读/必听/必看是什么？列 10-20 条，给出作者、年代、一句为什么经典"
- 存入新表 `canon_items(tag, item_title, item_type, author, year, url, rationale, created_at)`
- 用户 `/taste/canon` 可浏览、标记"已读/想读/不感兴趣"

**5.2 Reach pair 注入**（10% 的 pair）

- Reach 策略：从 Canon 库或高"专家共识"源 pool 中抽一条 vs 用户当前喜好的一条
- Card 上打标 "🎯 拔高推荐" + 鼠标悬停显示 "为什么推：这是【方法论】领域被广泛引用的经典"
- 用户选它 → weight += 1.0 且 **额外 boost tag 权重**（鼓励拔高）
- 用户选另一个 / 都不行 → 只按常规 feedback 处理，**不惩罚 canon 本身**（避免快速抹平品味引导）

**5.3 Expert overlay**（每周刷新）

- 对每个高权重作者（weight > 2）, LLM 问："该作者圈子里的同级/更资深从业者是谁？"
- 生成 "expert_graph" 关系：`author_expert_graph(author_key, peer_key, tier, relationship)`
- Reach pair 从 Tier > 当前作者 tier 的账号内容里抽

**5.4 品味分数 Taste Score**（slow metric）

- 每周计算：最近 7 天用户的 pairwise 选择 vs "expert consensus"（LLM 模拟 "专家会怎么选"）的相关系数
- 展示在 `/taste` 页，趋势图
- 目的：让"品味提升"可量化、可看到进步

**5.5 Depth ladder**

- 若用户持续选短平快内容而回避长文/深度 → 某周的 reach pair 里固定一条深度内容，配上 "📖 长文，读完约 8 分钟" 的预期管理
- 选了 → 长度偏好 weight 微调
- 没选 → 不惩罚长度 weight，但降低该次深度 reach 频次

## 6. Weekly Convergence Loop

新调度作业 `prism weekly-convergence`，每周日 22:00：

```
1. [Coverage] 跑 Source Graph Expander → 写入 source_proposals
2. [Coverage] 跑 Gap Detector → 写入 source_proposals  
3. [Coverage] 跑 Blindspot Scanner → 写入 source_proposals
4. [Alignment] confidence 日衰减累计、EWMA 应用
5. [Alignment] 重算 source_sync_schedule（次周生效）
6. [Taste] 刷新 Canon 库（高权重 tag 若 canon_items 少于 10 条）
7. [Taste] 刷新 expert_graph
8. [Taste] 计算本周 Taste Score
9. [Report] 生成周报邮件/页面：本周学到什么、本周提议什么、下周排期
10. [Log] 所有动作写 decision_log
```

Daily 作业保留 hourly sync + external feed consumer + 每日 06:00 重算 sync 频率。

## 7. Schema 变更汇总

```sql
-- 新表
CREATE TABLE persona_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    answers_json TEXT NOT NULL,
    free_text TEXT DEFAULT '',
    seed_handles_json TEXT DEFAULT '[]',
    extracted_summary TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE source_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_config_json TEXT NOT NULL,
    display_name TEXT NOT NULL,
    rationale TEXT DEFAULT '',
    origin TEXT NOT NULL,               -- persona/graph/gap/blindspot/external_feed
    origin_ref TEXT,
    sample_preview_json TEXT DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    snooze_until TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    reviewed_at TEXT
);

CREATE TABLE source_sync_schedule (
    source_key TEXT PRIMARY KEY,
    base_interval_minutes INTEGER NOT NULL,
    effective_interval_minutes INTEGER NOT NULL,
    next_run_at TEXT,
    last_run_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE canon_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag TEXT NOT NULL,
    item_title TEXT NOT NULL,
    item_type TEXT NOT NULL,            -- paper/book/talk/essay/podcast
    author TEXT,
    year INTEGER,
    url TEXT,
    rationale TEXT,
    user_status TEXT DEFAULT 'new',     -- new/read/want_to_read/not_interested
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE author_expert_graph (
    author_key TEXT NOT NULL,
    peer_key TEXT NOT NULL,
    tier INTEGER NOT NULL,
    relationship TEXT,
    refreshed_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (author_key, peer_key)
);

CREATE TABLE user_overrides (
    dimension TEXT NOT NULL,
    key TEXT NOT NULL,
    override_weight REAL NOT NULL,
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (dimension, key)
);

CREATE TABLE taste_score_history (
    week_start TEXT PRIMARY KEY,
    score REAL NOT NULL,
    correlation_method TEXT,
    notes TEXT
);

-- 现有表升级
ALTER TABLE preference_weights ADD COLUMN confidence REAL NOT NULL DEFAULT 0.3;
ALTER TABLE preference_weights ADD COLUMN sample_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE preference_weights ADD COLUMN last_touched TEXT;
ALTER TABLE external_feeds ADD COLUMN extracted_json TEXT DEFAULT '';
```

所有 CREATE 用 `IF NOT EXISTS`，ALTER 前 `PRAGMA table_info` 检查，与现有 `init_db()` 风格一致。

## 8. 代码结构

```
prism/
├── convergence/              # 新模块
│   ├── __init__.py
│   ├── coverage.py           # source graph + gap + blindspot
│   ├── alignment.py          # confidence weights + EWMA + sync schedule
│   ├── taste.py              # canon + expert graph + taste score
│   └── weekly_loop.py        # 编排
├── persona.py                # 新：persona snapshot CRUD + LLM 抽取
├── pipeline/
│   └── external_feed.py      # 新：消费器
├── sources/
│   └── yaml_editor.py        # 新：ruamel.yaml 安全写
├── web/
│   ├── routes.py             # 加 /persona, /taste, /taste/sources, /taste/canon
│   ├── ranking.py            # 策略扩到 5 路
│   ├── pairwise.py           # strategy 真落 + confidence 更新
│   └── templates/
│       ├── persona.html
│       ├── taste.html
│       ├── taste_sources.html
│       ├── taste_canon.html
│       └── pair_cards.html   # 加 reach 标识
├── cli.py                    # 加 prism sources prune / propose / weekly-convergence
└── scheduling/
    ├── com.prism.external-feed.plist    # hourly
    ├── com.prism.sync-schedule.plist    # daily 06:00
    └── com.prism.weekly-convergence.plist  # Sun 22:00
```

## 9. Rollout — 三周，每周一个可见跃迁

### Week 1: 止血 + 召回救援

可见变化：用户可以告诉系统"我是谁"，被动等待得到主动推荐的源

- Persona snapshot + /persona 页 + LLM 抽取
- `source_proposals` 表 + /taste/sources 页
- External feed consumer
- pair_strategy 字段真落
- `prism sources prune` CLI
- **交付物**：新 persona 跑一次，"neither" 率当周降到 < 50%

### Week 2: 对齐收敛机制

可见变化：系统开始"懂得"自己的不确定，用户可以校正

- confidence-aware weights schema + EWMA 衰减
- Active-learning 策略纳入 select_pair
- `/taste` 页（当前偏好 + 覆盖按钮）
- 源频率反哺（source_sync_schedule）
- 熔断 modal 升级
- **交付物**："neither" 率稳定 < 40%；Taste Score 开始有首个数据点

### Week 3: 品味引擎 + 周循环

可见变化：系统开始"拔高"，每周给一份周报

- Canon 库 + 生成任务
- Expert graph
- Reach pair（10%）+ card 标识
- Source Graph Expander + Gap Detector + Blindspot Scanner
- Weekly convergence loop + 周报页
- Taste Score 计算
- **交付物**：每周收到周报；canon 库有 5 个 tag 的条目；Taste Score 连续 2 周非降

## 10. 指标 / Acceptance

| 维度 | 指标 | 目标 |
|---|---|---|
| 收敛准确性 | "neither" 率 | 2 周内 < 40%，4 周内 < 30% |
| 信息完整性 | 高权重 tag 的源覆盖数 | 每个 weight > 1.5 的 tag ≥ 3 个独立源 |
| 机制可观测 | `pair_strategy` 分布 | 非单值，5 种策略都有记录 |
| 学习可审计 | `decision_log` 覆盖度 | 所有自动决策 100% 记录 |
| 外部投喂可用 | processed rate | 提交后 1 小时内 100% 处理 |
| 品味提升 | Taste Score | 连续 2 周非降为达标 |
| 用户控制 | user_overrides 使用率 | > 0 即证明纠偏通路活着 |

## 11. 风险与开放问题

- **LLM 成本**：每周一次的 graph/gap/blindspot + canon + expert graph 会有几百个 LLM 调用。本地 omlx 足够但需错峰。用 async queue + rate limit，不是并行轰炸。
- **Canon 质量**：LLM 列 "经典必读" 可能幻觉书名作者。对照策略：第一次生成后附 URL search 验证存在性；用户标记 not_interested 的永不再推。
- **Reach pair 的反馈歧义**：用户跳过 reach 可能是累了也可能是真不想看。对策：reach 连续 3 次被跳过再做惩罚，否则容忍。
- **Taste Score "专家共识"的 ground truth**：LLM 模拟专家会有偏差。可以用"高 follower-to-following ratio + 高互引率"的账号作为训练参考，而不是纯 LLM 幻觉。
- **兴趣真实迁移 vs 噪声**：EWMA 60 天半衰期是默认，若用户快速换方向需要调短；这是一个可观测的二阶参数，先默认 60 天。
- **用户 never 访问 /taste 怎么办**：周报里塞一个"系统本周学到什么"的摘要，比点进 /taste 被动看更可能被读。

---

## 下一步

Spec 完成。进入 `writing-plans` skill 产出 Week 1 的实施计划，完成后直接开干。Week 2/3 在 Week 1 收尾后再写对应 plan——因为下一步的很多参数（阈值、prompt 效果）要看 Week 1 数据才能定准。
