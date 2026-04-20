# Feed-First Redesign (W2) — Design Spec

Date: 2026-04-19
Status: approved, implementation-ready
Prior art: `docs/superpowers/specs/2026-04-19-prism-convergence-engine.md` (W1)

## Motivation

W1 把 persona → proposal → external-feed 闭环打通了，但日常主交互仍是 pairwise。Pairwise 作为"推荐收敛的唯一主力"有四个结构性问题：

1. **信号密度 vs 交互成本失配** — 用户读两条、对比、决策，5-30 秒换 1 bit。
2. **多维偏好被压成 1 bit** — 选 A 到底是因为话题、作者、深度还是长度？pairwise 不知道。
3. **"两个都烂"是主导情形** — BT 模型假设每 pair 总有更好的一个；冷启动/池子差时 `neither` 占比 > 50%。
4. **收敛速度跟不上个人流量** — 日 <30 交互 × 1 bit 信号 = 周级别才能学会一个新作者。

W1 里 external_feed 权重 3.0（pairwise 的 3 倍）+ persona 表单一次吃几百次 pairwise 的量 —— **这些补丁本身就是在说 pairwise 不够**。

但 pairwise 有个价值不能丢：**强制慢思考、品味校准**。对应用户核心诉求"指引技术品味越变越好"。

## W2 目标

把 pairwise **从"主交互"降级为"品味校准工具"**，主交互改为 **feed + 显式多维反馈**。

| 层 | 交互 | 频率 | W2 范围 |
|---|---|---|---|
| 主 feed | 瀑布流 + save / dismiss / follow-author / mute-topic | 每次打开 | **IN** |
| 外部投喂 | 粘链接 + 备注 | 随时 | 沿用 W1，不动 |
| Pairwise 校准 | 每周"本周十佳对决" | 周级 | W3，不做 |
| Persona 表单 | 每 2-3 月 | 月级 | 沿用 W1，不动 |

## Non-goals（W2 不做）

- 不做 infinite scroll（手动 load-more 够用）
- 不做 dwell-time / click-through 被动信号（留 W3）
- 不删 pairwise 代码（降级为 `/pairwise` 入口保留，从首屏撤下）
- 不做 A/B 切流（单人产品，直接切）
- 不做周级"十佳对决"会话（W3）

## Architecture

### 数据模型

**新表 `feed_interactions`**（显式反馈事件日志）：

```sql
CREATE TABLE IF NOT EXISTS feed_interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    action TEXT NOT NULL,  -- 'save' | 'dismiss' | 'follow_author' | 'mute_topic' | 'unmute_topic' | 'unfollow_author'
    target_key TEXT NOT NULL DEFAULT '',  -- 对 follow_author 是 author 名，mute_topic 是 topic tag
    response_time_ms INTEGER NOT NULL DEFAULT 0,
    context_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_feed_interactions_signal ON feed_interactions(signal_id);
CREATE INDEX idx_feed_interactions_action_created ON feed_interactions(action, created_at);
```

Rationale：与 `pairwise_comparisons` 对等的事件日志，方便后续离线分析和回放。`target_key` 冗余记录是为了 follow_author / mute_topic 这种"不绑定单条 signal"的操作能独立查询。

**既有表沿用，不改 schema**：`signals`、`signal_scores`、`preference_weights`、`pairwise_comparisons`（pairwise 保留）。

### 反馈权重

| action | signal 层 delta (bt_score) | preference_weights delta | 更新维度 |
|---|---|---|---|
| `save` | +K（同 BT 胜） | +2.0 | author, tag, source, layer |
| `dismiss` | -K（同 BT 败） | -1.0 | author, tag, source, layer |
| `follow_author` | — | +3.0 | author（只 author 维度） |
| `unfollow_author` | — | 将 author 权重清零 | author |
| `mute_topic` | — | -2.0 | tag（只 tag 维度，多 tag 逐一应用） |
| `unmute_topic` | — | 将 tag 权重清零 | tag |
| `pairwise_win` | +K（现有）| +1.0（现有）| 现有 |
| `external_feed` | — | +3.0（现有 W1）| 现有 |

**关键实现**：复用 `_update_preference_weights(conn, signal_id, delta)`（已存在于 `prism/web/pairwise.py:464`），传不同 delta 就行。follow/mute 这种非 signal-id 的操作直接写 preference_weights 的 author/tag 维度。

**BT delta K** 使用现有 `update_bt_scores` 的 winner 路径（即 save → 等价于"赢了一场虚拟 pairwise，对手是该 signal 的平均对手"）。简化实现：save 时 `signal_scores.bt_score += BT_SAVE_BONUS`（常量 0.2），dismiss 时 `-= BT_DISMISS_PENALTY`（0.1）。不经过完整 BT 更新，避免拖慢交互。

### Feed 排序

```
feed_score(signal) =
    bt_score
  + signal_strength * 10
  + Σ(preference_weights 匹配维度 × dimension_weight)
  + recency_bonus
  - 0.5 * log(1 + days_since_last_shown)
```

**召回池**：直接复用 `_get_candidate_pool(conn)`（已过滤 blocked sources/tags、推送过的）。该函数已内置时间窗和 preference block。

**显式 exclude**：追加一个过滤 `signal_id NOT IN (SELECT signal_id FROM feed_interactions WHERE action IN ('dismiss','save') AND created_at > now()-7d)` 避免再推。save 过的需要在 `/feed/saved` 独立页查看（直接复用现有 `/pairwise/liked` 的查询逻辑，允许也显示 `feed_interactions.action='save'`）。

**分页**：offset-based，每页 10 条，按分数降序，分数相同用 recency tiebreak。

### 路由变更

| 路由 | 变更 | 说明 |
|---|---|---|
| `GET /` | **改重定向到 `/feed`** | 当前是 pairwise 首屏 |
| `GET /feed` | **新增** | 瀑布流渲染前 10 条 |
| `POST /feed/action` | **新增** | 接 `{signal_id, action, target_key?, response_time_ms}` → 写 `feed_interactions` + 更新权重/BT → HTMX swap 单卡（save/dismiss）或返回 toast（follow/mute） |
| `GET /feed/more?offset=N` | **新增** | HTMX 加载下一页 |
| `GET /feed/saved` | **新增** | 列出 action=save 的 signal |
| `GET /pairwise` | **保留** | 从首屏撤下，但 nav 保留入口，用户想校准时主动进入 |
| `POST /pairwise/vote` | **保留** | 不改，W3 再降权 |
| 其他 pairwise 子路由 | **保留** | 降级不删 |

### 闭环与收敛机制

- **显式反馈即时更新** preference_weights + signal_scores。
- **follow_author** 把该作者权重打到 +3.0，下次 `_get_candidate_pool` 会主动召回该作者所有 signal（因为现有排序里 author 匹配 × dimension_weight 已加分）。
- **mute_topic** 把该 tag 权重打到 ≤ `PREF_BLOCK_THRESHOLD`（现有常量），`_get_candidate_pool` 会直接 hard-block。
- **daily.sh** 沿用，周期性根据最近 7 天反馈（含 feed_interactions）更新 source_weights。
- **pairwise 信号保留**（权重不变），W3 再调整混合权重。

## UI

**主模板 `feed.html`**：
- Nav: Feed（当前）/ Saved / Pairwise Calibration / Sources / Profile / Persona
- 顶部：外部投喂输入框（沿用 `pairwise.html` 的 feed-form）
- 主体：Feed card 列表
- 底部：`[加载更多]` 按钮（HTMX，swap-beforeend）

**`partials/feed_card.html`**（每张卡）：
```
┌─────────────────────────────────────────┐
│ [topic_label · source · author · 时间]   │
│                                          │
│ Summary / Why-it-matters                 │
│                                          │
│ 链接 · 原文                               │
│                                          │
│ 👍 Save  🔔 Follow {author}             │
│ 🙈 Mute {topic}  ✕ Dismiss              │
└─────────────────────────────────────────┘
```
点 save/dismiss → 卡片 swap 为 "已保存/已隐藏" 的简短确认 + 1 秒后淡出。
点 follow/mute → 按钮变灰 + 顶部 toast。

### 回答用户核心诉求对齐检查

- **信息收集要全** ← W1 persona + external_feed 已覆盖，W2 不削弱
- **推荐越来越准** ← feed 反馈高频 × 多维（save/follow/mute/dismiss），比 pairwise 1 bit 快 4-8 倍
- **指引技术品味越变越好** ← W2 保留 pairwise 入口（校准），W3 做周级"十佳对决"真正激活这个能力

## Tests 策略

- `test_feed_interactions_schema`（T1）
- `test_feed_action_save_updates_bt_and_weights`（T2）
- `test_feed_action_dismiss_updates_bt_and_weights`（T2）
- `test_feed_action_follow_author_sets_author_weight`（T3）
- `test_feed_action_mute_topic_blocks_future_signals`（T3）
- `test_feed_rank_respects_pref_weights`（T4）
- `test_feed_excludes_recently_dismissed`（T4）
- `test_feed_route_renders_top_10`（T5）
- `test_feed_action_route_writes_event`（T5）
- `test_feed_more_pagination`（T5）
- `test_root_redirects_to_feed`（T6）
- `test_saved_page_lists_saved_signals`（T7）

## 迁移风险

- `/` 重定向改动会影响已有书签/launchd web plist——`prism/scheduling/com.prism.web.plist` 不受影响（它只 `serve --port`）。书签由用户自己处理。
- pairwise 历史数据保留，不做任何数据迁移。
- `_get_candidate_pool` 现在 pairwise 和 feed 共用——feed 需要额外 exclude 近期已反馈的 signal，做法是 **在 `_get_candidate_pool` 里加一个可选 `extra_exclude_ids: set[int]` 参数**，而不是复制该函数。这个改动只影响 `_get_candidate_pool`，pairwise 调用点传空集合保持原行为。

## 非目标/反模式

- 不引入前端 JS 框架（坚持 HTMX + vanilla CSS，与项目 CLAUDE.md 一致）。
- 不做 `save_as_bookmark` / `share` / 社交类动作（单人产品）。
- 不在 feed 层做 LLM 调用（所有 LLM 走异步离线任务）。
