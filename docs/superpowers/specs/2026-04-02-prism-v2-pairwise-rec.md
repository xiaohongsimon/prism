# Prism v2: Pairwise Preference Learning Recommendation System

> 从新闻聚合器转型为 AI 驱动的个人推荐系统，核心交互为 pairwise comparison

## 1. 目标

用户（单人，忙碌的 AI TL）每次打开 Web UI 看到两条信号，选更感兴趣的那个。系统从选择 + 文字反馈 + 外部投喂中持续学习偏好，动态调整排序和召回。

**v1 MVP 核心验证**: 用户是否愿意持续做 pairwise 选择。

**v1 scope**: Pairwise UI + Bradley-Terry 评分 + 源权重动态调整 + Decision Log + 外部投喂。

**不做**: LLM 自动发现新源、独立 Meta 层、复杂 ML 模型。

## 2. 数据模型变更

### 2.1 新表: pairwise_comparisons

记录每次 pairwise 交互结果。

```sql
CREATE TABLE IF NOT EXISTS pairwise_comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_a_id INTEGER NOT NULL REFERENCES signals(id),
    signal_b_id INTEGER NOT NULL REFERENCES signals(id),
    winner TEXT NOT NULL CHECK(winner IN ('a', 'b', 'both', 'neither', 'skip')),
    user_comment TEXT DEFAULT '',
    pair_strategy TEXT DEFAULT 'exploit',  -- exploit | explore | random
    response_time_ms INTEGER,  -- 用户决策时间，元信号
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### 2.2 新表: signal_scores

Bradley-Terry Elo 评分，每条信号一个 score。

```sql
CREATE TABLE IF NOT EXISTS signal_scores (
    signal_id INTEGER PRIMARY KEY REFERENCES signals(id),
    bt_score REAL NOT NULL DEFAULT 1500.0,  -- Bradley-Terry score, 初始 1500
    comparison_count INTEGER NOT NULL DEFAULT 0,  -- 被比较次数
    win_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### 2.3 新表: source_weights

动态源权重，驱动召回层。

```sql
CREATE TABLE IF NOT EXISTS source_weights (
    source_key TEXT PRIMARY KEY,
    weight REAL NOT NULL DEFAULT 1.0,  -- 1.0 = 默认, >1 = 高频采集, <0.3 = 低频
    win_rate REAL NOT NULL DEFAULT 0.5,  -- 该源产出信号的 pairwise 胜率
    total_comparisons INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### 2.4 新表: decision_log

所有自动决策的审计日志。

```sql
CREATE TABLE IF NOT EXISTS decision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    layer TEXT NOT NULL CHECK(layer IN ('recall', 'ranking')),
    action TEXT NOT NULL,  -- e.g. 'adjust_source_weight', 'update_bt_score'
    reason TEXT NOT NULL,
    context_json TEXT NOT NULL DEFAULT '{}'  -- before/after state, trigger info
);
```

### 2.5 新表: external_feeds

用户投喂的外部链接/话题。

```sql
CREATE TABLE IF NOT EXISTS external_feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL DEFAULT '' UNIQUE,
    topic TEXT NOT NULL DEFAULT '',
    user_note TEXT NOT NULL DEFAULT '',
    extracted_tags_json TEXT NOT NULL DEFAULT '[]',  -- LLM 提取的标签
    processed INTEGER NOT NULL DEFAULT 0,  -- 0=未处理, 1=已更新偏好, 2=已触发pair
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### 2.6 扩展现有表: preference_weights

新增维度 'author'，保持 schema 不变，仅增加数据。

反馈信号权重常量：
- 外部投喂: delta = 3.0
- save: delta = 2.0
- pairwise 选择胜者: delta = 1.0
- 'both': delta = 0.3 (对两条信号的所有维度)
- 'neither': delta = -0.5

## 3. 偏好模型: Bradley-Terry

### 3.1 核心算法

每条信号维护一个 `bt_score`（Elo rating）。每次 pairwise 比较后更新：

```python
K = 48  # 学习率（信号生命周期7天，需较快收敛）

def update_bt_scores(score_a: float, score_b: float, winner: str) -> tuple[float, float]:
    """返回更新后的 (new_a, new_b)"""
    expected_a = 1.0 / (1.0 + 10 ** ((score_b - score_a) / 400))
    expected_b = 1.0 - expected_a
    
    if winner == 'a':
        actual_a, actual_b = 1.0, 0.0
    elif winner == 'b':
        actual_a, actual_b = 0.0, 1.0
    elif winner == 'both':
        actual_a, actual_b = 0.5, 0.5
    else:  # neither, skip — 不更新
        return score_a, score_b
    
    new_a = score_a + K * (actual_a - expected_a)
    new_b = score_b + K * (actual_b - expected_b)
    return new_a, new_b
```

### 3.2 冷启动

新信号没有 bt_score 时，初始化为 1500。未来可用 LLM embedding 相似度初始化（v2 scope）。

### 3.3 多维权重更新

每次 pairwise 结果同时更新 preference_weights：
- 胜者的 source_key, tags, signal_layer, author → +1.0
- 败者的对应维度 → -0.3（轻微惩罚，避免过度抑制）
- 'both' → 双方 +0.3
- 'neither' → 双方 -0.5

## 4. Pair 选择策略

### 4.1 候选池

从 `signals WHERE is_current = 1` 中选取，排除：
- 已在最近 50 次 pairwise 中出现过的信号（避免重复）
- created_at 超过 7 天的信号（时效性）

### 4.2 策略分配

```python
PAIR_STRATEGY_WEIGHTS = {
    'exploit': 0.7,   # 一条高分 + 一条低比较次数
    'explore': 0.2,   # 两条都是新信号（comparison_count < 3）
    'random':  0.1,   # 随机
}
```

**exploit 策略**: 从候选池中选 bt_score top 30% 的一条 + comparison_count 最少的一条。两条信号的 topic 不能完全相同（避免无效比较）。

**explore 策略**: 从 comparison_count < 3 的信号中随机选两条。

**random 策略**: 完全随机。

### 4.3 破局机制

如果 pairwise_comparisons 最近连续 3 次 winner='neither'，下一次强制使用 random 策略。

### 4.4 外部投喂触发

当用户投喂外部链接后，下一次 pairwise 中的一条固定为与投喂内容最相关的信号（候选池中与 extracted_tags_json 的 Jaccard 相似度最高的信号，无匹配则 fallback 到随机），另一条按正常策略选择。这让用户立即看到系统对投喂的响应。

## 5. 召回层: 源权重动态调整

### 5.1 胜率计算

每个源的胜率 = 该源产出信号在 pairwise 中被选为胜者的次数 / 该源信号参与 pairwise 的总次数。

更新时机: 每次 pairwise 反馈后实时更新 source_weights 表。

### 5.2 权重调整规则

每日定时任务（daily cron）执行：

```python
for source in sources:
    sw = source_weights[source.source_key]
    if sw.total_comparisons < 10:
        continue  # 数据不足，不调整
    
    if sw.win_rate > 0.6:
        new_weight = min(sw.weight + 0.2, 3.0)  # 加法调整，上限 3x
    elif sw.win_rate < 0.3:
        new_weight = max(sw.weight - 0.2, 0.1)  # 加法调整，下限 0.1x
    else:
        new_weight = sw.weight  # 不变
    
    if new_weight != sw.weight:
        log_decision('recall', 'adjust_source_weight', 
                     f'{source.source_key}: {sw.weight:.2f} → {new_weight:.2f}, win_rate={sw.win_rate:.2f}')
        sw.weight = new_weight
```

### 5.3 外部投喂的源效应

当用户投喂一个外部链接：
1. 提取域名/作者/平台信息
2. 如果对应源已存在 → source_weights.weight = min(sw.weight + 0.5, 3.0)
3. 如果对应源不存在 → 记录到 decision_log，提示系统（暂不自动添加，Phase 2）
4. URL 去重：external_feeds 有 UNIQUE(url) 约束，重复投喂同一链接只更新 user_note

## 6. 排序层变更

### 6.1 新的 compute_feed

保留现有 compute_feed 用于 hot/follow tab（不变），新增 pairwise 专用逻辑：

```python
def select_pair(conn: sqlite3.Connection) -> tuple[dict, dict] | None:
    """选择下一对信号供 pairwise 比较。返回 None 如果候选不足。"""
    ...

def compute_pairwise_feed(conn: sqlite3.Connection, page: int = 1) -> list[dict]:
    """返回历史 pairwise 结果列表，按时间倒序，用于回顾页。"""
    ...
```

### 6.2 Tab 变更

| Tab | 行为 | 变更 |
|-----|------|------|
| 推荐 | **Pairwise 模式** — 每次展示两条 | **新** |
| 关注 | Follow 源列表（不变） | 无 |
| 热门 | 按热度排序列表（不变） | 无 |
| 历史 | Pairwise 历史记录 | **新** |

### 6.3 综合排序（热门 tab 和兜底）

热门 tab 的 score 公式增加 BT score 因子：

```
score = w_heat * heat_norm + w_bt * bt_norm + w_decay * decay

TAB_WEIGHTS_V2 = {
    "hot":    (0.4, 0.3, 0.3),   # (heat, bt, decay)
    "follow": (0.2, 0.0, 0.8),   # follow 不用 bt
}
```

bt_norm = bt_score / max_bt_score（归一化到 0-1）。

## 7. Web UI 变更

### 7.1 Pairwise 页面（推荐 tab）

```
┌─────────────────────────────────────────────┐
│  [推荐]  关注  热门  历史                      │
├──────────────────┬──────────────────────────┤
│                  │                          │
│   信号 A 卡片     │    信号 B 卡片             │
│   (topic_label)  │    (topic_label)         │
│   summary        │    summary               │
│   tags, source   │    tags, source          │
│                  │                          │
├──────────────────┴──────────────────────────┤
│  [选 A]  [都好]  [都不行]  [选 B]             │
├─────────────────────────────────────────────┤
│  💬 说说你的想法（可选）          [提交并下一对]  │
├─────────────────────────────────────────────┤
│  📎 投喂链接/话题                  [投喂]       │
└─────────────────────────────────────────────┘
```

### 7.2 新模板文件

- `templates/pairwise.html` — 主 pairwise 页面
- `templates/partials/pair_card.html` — 单侧信号卡片（复用 card.html 结构）
- `templates/partials/pair_actions.html` — 底部选择按钮区域
- `templates/history.html` — pairwise 历史记录列表

### 7.3 HTMX 交互

- 选择按钮 POST /pairwise/vote → 返回下一对（整个 pairwise 区域替换）
- 投喂表单 POST /pairwise/feed → 返回确认提示 + 下一对
- 评论框随选择一起提交（同一个 form）

## 8. API 端点变更

### 8.1 新端点

| Route | Method | 功能 |
|-------|--------|------|
| `/pairwise` | GET | 渲染 pairwise 页面（推荐 tab） |
| `/pairwise/pair` | GET | HTMX: 获取下一对信号 |
| `/pairwise/vote` | POST | 记录投票 + 返回下一对 |
| `/pairwise/feed` | POST | 接收外部投喂链接/话题 |
| `/pairwise/history` | GET | 渲染历史页面 |

### 8.2 投票请求格式

```
POST /pairwise/vote
Content-Type: application/x-www-form-urlencoded

signal_a_id=123&signal_b_id=456&winner=a&comment=这条更有技术深度&response_time_ms=3200
```

### 8.3 投喂请求格式

```
POST /pairwise/feed
Content-Type: application/x-www-form-urlencoded

url=https://example.com/article&note=这个方向很有意思
```

## 9. 文字反馈处理

**v1 MVP**: 用户 comment 仅存储到 pairwise_comparisons.user_comment，在历史页面展示。不做 LLM 自动提取。

**v2 扩展**（v1 不做）: 后台 LLM 提取偏好标签 → 更新 preference_weights。

## 10. Decision Log 规范

每次自动决策必须记录：

```python
def log_decision(layer: str, action: str, reason: str, context: dict = {}):
    conn.execute(
        "INSERT INTO decision_log (layer, action, reason, context_json) VALUES (?, ?, ?, ?)",
        (layer, action, reason, json.dumps(context, ensure_ascii=False))
    )
```

记录场景（v1 精简版）：
- source_weight 调整（每日 cron）
- 外部投喂处理结果
- pair 策略异常（连续 neither 触发破局）

不记录（避免日志过大）：每次 BT score 微调、每次普通 pair 策略选择。

## 11. 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `prism/db.py` | 修改 | 新增 5 张表的 schema |
| `prism/web/pairwise.py` | **新建** | Pair 选择 + BT 评分 + 投喂处理 |
| `prism/web/ranking.py` | 修改 | 热门 tab 加入 bt_score 因子 |
| `prism/web/routes.py` | 修改 | 新增 /pairwise/* 端点 |
| `prism/web/templates/pairwise.html` | **新建** | Pairwise 主页面 |
| `prism/web/templates/partials/pair_card.html` | **新建** | Pair 信号卡片 |
| `prism/web/templates/partials/pair_actions.html` | **新建** | 选择按钮 |
| `prism/web/templates/history.html` | **新建** | 历史回顾页 |
| `prism/web/templates/feed.html` | 修改 | Tab 栏新增"历史" |
| `prism/cli.py` | 修改 | 新增 source-weights / decision-log 命令 |
| `prism/scheduling/daily.sh` | 修改 | 加入源权重调整任务 |
| `tests/web/test_pairwise.py` | **新建** | Pairwise 核心逻辑测试 |
| `tests/web/test_ranking.py` | 修改 | BT score 集成测试 |

## 12. 测试策略

- **test_bt_score_update**: 验证 BT 公式 — A 胜后 score_a 上升, score_b 下降
- **test_bt_both**: 两者都好时分数微调
- **test_bt_neither_skip**: neither/skip 不改分
- **test_pair_selection_exploit**: exploit 策略选高分+低比较次数
- **test_pair_selection_explore**: explore 策略选新信号
- **test_pair_selection_random**: random 策略覆盖所有信号
- **test_pair_break_loop**: 连续 3 次 neither 后切 random
- **test_source_weight_update**: 高胜率源权重上升，低胜率下降
- **test_external_feed_preference**: 投喂更新 preference_weights delta=3.0
- **test_external_feed_next_pair**: 投喂后下一对含相关信号
- **test_decision_log**: 每次自动决策都有 log 记录
- **test_pairwise_vote_endpoint**: POST /pairwise/vote 返回下一对
- **test_pairwise_feed_endpoint**: POST /pairwise/feed 存储并响应
- **test_hot_tab_bt_integration**: 热门 tab score 包含 bt_norm 因子
- **test_select_pair_insufficient**: 候选不足时返回 None
- **test_select_pair_no_duplicate**: signal_a_id != signal_b_id
- **test_external_feed_url_dedup**: 重复 URL 只更新 note
