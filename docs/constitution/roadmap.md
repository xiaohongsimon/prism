# Roadmap — Prism

> Constitution / Roadmap 层。本文件是"下一步做什么、按什么顺序"的单一来源。
> Mission 见 `mission.md`；技术栈见 `tech-stack.md`；当前代码现状见 `../SPEC.md`。
> 从 `docs/SPEC.md` §12–§13 抽离（2026-04-21）。
>
> **2026-04-21 多模型辩论修订**：已接收 6 模型辩论结果（见 `../reviews/synthesis/2026-04-21-spec-multi-model-debate.md`）。
> 强共识修订待合入 Wave 1/2：
> - 追加 W1-0：数据库每日备份（生存级盲点）
> - W1-11 降级：`personalize/` 只建空目录 + TODO，不提前定义 Protocol
> - 追加 W1-12：砍表前用 LLM 蒸馏历史偏好到 `docs/user_preference_profile_2026Q1.md`
> - W2-7 Warden 瘦身：砍独立 drain worker，合入现有 cron；徽章移到 `/board`
> - W2-x 前移 X cookie 静默失败告警到 Wave 1
> 修订细节与原因见辩论综合文档。

---

## 12. 行动清单（按新定位重排）

### 🟢 Wave 1 — 清理上一代推荐实现（解耦、降复杂度；不等于放弃推荐方向）

| # | 操作 | 影响 |
|---|---|---|
| W1-1 | 砍 `web/ranking.py` 整个模块 | 纯死代码 |
| W1-2 | 砍 `/feedback` 路由 + `feedback` 表；/article 的 like 走 /feed/action | 合并双反馈通路 |
| W1-3 | 砍 BT / pairwise：删 `web/pairwise.py::update_bt_scores`、`record_vote`、feed.py 里的 bt_score 项 + `signal_scores` 表 | BT 彻底下线 |
| W1-4 | 砍 `/pairwise/liked`、`/pairwise/sources`、`/pairwise/profile`（档案页）+ `pairwise_comparisons` 表 | 停止维护僵尸 |
| W1-5 | 砍 daily.sh 的 `adjust_source_weights()` SQL block + `source_weights` 表 | 建立在僵尸 pairwise 数据上 |
| W1-6 | 砍 CTR 训练链：删 CLI (`ctr *`)、`ctr_samples` / `feed_impressions` 表、`data/ctr/`、impressions 日志点 | 空转的 XGBoost 链 |
| W1-7 | 砍 `persona_snapshots` 表 + 相关代码 | 显式偏好采集，不需要 |
| W1-8 | 砍孤儿 partial `feed_card_{a,c,d,f}.html` | 被派发器取代 |
| W1-9 | 砍 `pipeline/entities.py`（旧版） | 已被 entity_extract 取代 |
| W1-10 | briefing 里删掉对 entity 上下文的空查询（或写 fallback） | entity 系统冷冻，减少跑空 |
| W1-11 | **预留 `prism/personalize/` seam**：Protocol `ReRanker` + `IdentityReRanker` 默认实现（按时间倒排）；`/feed` 和 `/feed/following` 全部走 ReRanker | 清理时同步建未来插入点，见 §0 |

### 🟡 Wave 2 — 收敛主路径 + omlx-sdk + Freshness Warden

| # | 操作 | 影响 |
|---|---|---|
| W2-1 | `GET /`（登录态）redirect 到 `/feed/following` 而非 `/feed` | 主路径对齐 |
| W2-2 | `/feed/following` 加 "上次访问以来的更新" 高亮（cookie 存 last_seen_at，或 DB per-source） | 新定位核心 UX |
| W2-3 | **articlize 提频**到 hourly.sh 或 fast.sh，并**扩展到播客 / 长文**（不再只限 YouTube） | 跨模态快扫 |
| W2-4 | 统一 analyze 调用：fast.sh / daily.sh 全切 `--triage/--expand`，弃用 `--incremental` | 一种调用方式 |
| W2-5 | 外部投喂闭环：`process-external-feeds` 写回 `external_feeds.processed=1`；`/pairwise/feed` 改名 `/external-feed` | 名字对齐 + 状态机闭环 |
| W2-6 | **omlx-sdk 接入**（增量）：先切 `translate-bodies` 用 `intent="fast"` + `project="prism"` + `session_id=job_run_id`；其他调用点保持 `call_llm` 兜底，按意图映射表（§4.2.3）逐步迁 | caller view 可观测 + 模型可换 |
| W2-7 | **Freshness Warden 落地**：新模块 `pipeline/freshness.py`、`source_health` 表、`prism warden tick` CLI、挂 launchd 每 5 分钟；`/feed/following` 每个分桶显示徽章（§6.7） | 订阅健康可见 + 消积压 |
| W2-8 | **X 推荐扩展**：新 adapter `x_bookmarks`（作为强正反馈走 external_feeds 通路）+ `x_list`（话题 list 订阅） | 补 X 平台推荐面 |
| W2-9 | 翻译管线加监控：失败告警、质量采样（纳入 `source_health` 口径） | 时效性保证 |
| W2-10 | 收敛反馈事件：保留 follow/unfollow/mute/save，评估 dismiss/click 是否可简化 | 反馈模型对齐用法 |
| W2-11 | **GitHub Discussions 启用** + Issue 模板 `.github/ISSUE_TEMPLATE/suggest-source.yml`；网站 `/showcase` 和 `/sources` 挂 giscus 镜像 | 社区反馈通路，零后端 |

### 🔵 Wave 3 — 卫生 / 长期

| # | 操作 | 影响 |
|---|---|---|
| W3-1 | 翻译列合并到单表 `translations(entity_table, entity_id, lang, body, updated_at)` | 散列三处 → 统一 |
| W3-2 | 删死字段：`raw_items.thread_partial`、`entity_candidates.expires_at`、`signals.tl_perspective` 冗余列、`PRISM_API_TOKEN` | 数据卫生 |
| W3-3 | `notion_exports` 迁入 init_db()，加 purge 策略 | init_db 是唯一 schema 入口 |
| W3-4 | 砍 auth 系统，换 `PRISM_SECRET` 环境变量 gate | 单用户去多租户抽象 |
| W3-5 | CLI 命令分组（cron/admin/dev） | 34 命令扁平 |
| W3-6 | 课程 adapter 去抽象 | base.py 协议没必要 |
| W3-7 | 旧 spec 移到 `docs/specs/archive/`，只留 SPEC.md 作 single source of truth + RUNTIME.md | 避免多代 spec 混淆 |
| W3-8 | xyz_queue 补崩溃恢复剧本 | 目前无恢复路径 |

### 🟣 Wave 4 — 长期方向（不定工期，写进来是为了保持方向感）

- 低点击率博主/源的"建议取消订阅"机制（Freshness Warden 延伸）
- "发现新源" UX：基于你当前订阅的特征推荐类似源（follow_builders 之外的通路；X Who-to-Follow 也可作为一个入口）
- 翻译质量：按博主/源定制 prompt（技术博客 vs 播客 vs 长文 vs 微博）
- 视频/播客结构化：章节/高亮的交互式定位（点 highlight 跳到原视频/音频时间点）
- HN 评论抓取（signal 补完 —— 当前只抓 submission，讨论价值被忽略）
- **偏好层重建**（Wave 1 预留的 `personalize/` seam 里填 `PersonalReRanker`）——
  目标不是"点击率优化"，是：
  1. 筛信息（订阅扫不动时做二轮过滤）
  2. **反映并提升作者自己的技术品味**（镜子，不是纯投其所好）

---

## 13. Review Checklist（给自己打勾）

定位是否 OK？
- [ ] §0 Mission 新版准确反映你的日常使用
- [ ] §0 "开源/个人化张力 + Token/本地机经济学 + pluggable seam" 的定性认可
- [ ] 主路径（/feed/following）被提升，/feed 次路径定位明确
- [ ] 推荐引擎是"延后方向 + 可插拔 seam"，不是 non-goal

架构规约是否认可？
- [ ] §4.2 所有本地推理走 omlx-sdk + intent 映射表
- [ ] §6.7 Freshness Warden 双职责（观测 + 推进）落地

Wave 1 是否全清？
- [ ] Pairwise / BT 整条链
- [ ] CTR 整条链
- [ ] web/ranking.py + /feedback 双反馈
- [ ] persona_snapshots
- [ ] 孤儿 partial / 旧 entities.py
- [ ] **预留 `prism/personalize/` seam（ReRanker Protocol + IdentityReRanker）**

Wave 2 是否改造？
- [ ] /feed/following 做成主页 + 新更新高亮 + Warden 徽章
- [ ] articlize 提频 + 扩播客/长文
- [ ] analyze 全面两阶段
- [ ] external_feeds 闭环
- [ ] omlx-sdk 增量接入（translate 先切）
- [ ] Freshness Warden 模块 + source_health 表 + tick launchd
- [ ] x_bookmarks + x_list adapter
- [ ] GitHub Discussions + Issue 模板 + giscus 镜像

保留但需收敛？
- [ ] Entity 系统：保留代码不投入，briefing 空查清理
- [ ] xyz_queue：保留 + 补崩溃恢复（W3-8）

---

*本文档由逆向代码生成，可能有细节偏差。发现不对的地方直接改 SPEC.md，然后修代码向它收敛。*
