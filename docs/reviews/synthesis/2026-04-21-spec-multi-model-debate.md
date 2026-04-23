# SPEC 多模型辩论综合 — 2026-04-21

Orchestrator: Claude Opus 4.6（本会话）
Preset: debate / Stakes: high
6/8 参战（百炼 GLM-5 / Qwen-3.5-Plus 未配置 key；Zenmux 6 模全部成功）

## 参与模型与角色

| # | Model | Lab | Role | Confidence |
|---|-------|-----|------|------------|
| 1 | Claude Opus 4.6（Zenmux） | Anthropic | Proposer A 主方案拥护者 | high |
| 2 | Grok 4 | xAI | Red Team 找 fatal flaw | high |
| 3 | Gemini 3.1 Pro | Google | Proposer B 替代方案 | high |
| 4 | MiMo V2 Pro | Xiaomi | Feasibility Analyst | medium |
| 5 | Kimi K2.5 | Moonshot | User Advocate | medium |
| 6 | MiniMax M2.7 | MiniMax | Scope Guardian YAGNI | high |

原始应答：`/tmp/mm-{opus2,grok,gemini,mimo,kimi,minimax}-output.txt`

---

## 共识点（≥4 模型一致）

### ① Freshness Warden 被全票质疑过度设计
**opus2 / grok / gemini / mimo / kimi / minimax 一致反对原样落地。**

分化仅在"怎么瘦身"：
- opus2：Wave 2 只保留 scan + 徽章，drain 推到 Wave 3 并改为"现有 cron --limit 参数动态化"
- gemini：不要 drain worker，改用 HTTP 429 被动背压 + 指数退避
- kimi：砍 Warden 角色概念，drain 做成 `omlx-drain` 独立脚本（launchd 每 5 分钟），观测复用 `sources` 表现有字段
- minimax：Warden 是"策略决策者"而非"独立进程"，直接在 `xyz_queue.sh` 加两行逻辑（omlx idle 时优先取 translate）
- mimo：若坚持 drain worker，必须加 SQLite advisory lock + 老化因子 + 订阅/非订阅预算分配（7:3）

**共同底线：不要为 drain 新建独立 worker + 新表。**

### ② W1-11（`personalize/` ReRanker Protocol 预留）时机过早
**opus2 / kimi / gemini / minimax 一致质疑，仅 mimo 支持但要求更严格契约。**

- opus2：W1-11 降级为空目录 + TODO，Protocol 推到"有真实消费者"时
- kimi：不做 Protocol，只做函数钩子 `rerank(candidates, context)`，默认时间倒排
- gemini：假 seam，真正的个性化应该前置到 prompt 层（articlize/analyze 阶段注入用户 context）
- minimax：SPEC §6.3–6.4 的"保留 vs 清理"讨论本身就是过度设计

**共同底线：Protocol 级抽象过重，函数钩子即可。**

### ③ SPEC 最大盲点是数据备份 / 灾难恢复
**opus2 / grok / mimo 共同提出（opus2 将其列为"严重度 1"）。**

- 单机 SQLite 24/7 跑，35+ 张表，磁盘故障 = 全丢
- Time Machine 不够，需要 offsite / 定期导出
- opus2 建议 Wave 1 追加 W1-0：每日 `sqlite3 .backup` 到外部存储

### ④ X cookie 静默失效告警不应等 Wave 2
**opus2 / kimi / gemini 共同提出。**

- 60+ X 源是最大渠道，cookie 失效 = 主路径 `/feed/following` 悄悄瘦身
- opus2：前移到 Wave 1，最简版（sync 返回 0 items 连续 N 次就告警）
- kimi：`sources` 表已有字段，基于现有字段加规则自动 disable 长期失败源
- gemini：自动熔断（连续 3 天抛错挂起）+ 强触达通道（Telegram/邮件）

### ⑤ Wave 1 砍表前应做历史偏好蒸馏
**gemini 首提，kimi / opus2 隐含认同（mimo 的"分阶段验证"亦同源）。**

- gemini：DROP 前写一次性 `data_distill.py`，用 LLM 把 `feed_interactions` / `pairwise_comparisons` / `ctr_samples` 里的历史 winner 和 saved item 聚合成 `docs/user_preference_profile.md`
- 价值：未来重建个性化层的冷启动基线 / 作者的技术品味演化快照
- 成本：一次性一两小时 LLM 跑批，比砍完才发现缺数据便宜得多

---

## 分歧点

### 定位本身是否正确（主流支持，Gemini / Grok 反对）

| 立场 | 模型 | 论据 |
|---|---|---|
| 支持收敛到订阅阅读器 | opus2 / mimo / kimi / minimax | 单用户数据稀疏，BT/CTR 欠拟合；主路径就是扫订阅 |
| 反对：应该再激进一步 | gemini | 订阅流并未解决注意力分配；应跃迁到 LLM 驱动的"动态简报"（每日 3-5 篇 cross-cluster synthesis） |
| 反对：是对推荐的放弃 | grok | "延后方向"是安慰剂；6 个月内订阅流一爆炸会后悔 |

**orchestrator 评价**：grok 的批评偏情绪，没有指出具体失败路径；gemini 的"动态简报"方向有道理但超出当前 scope（且依赖大量本地推理，与 799 积压的现实矛盾）。**主流方向稳。**

### omlx-sdk intent 粒度（真正的分裂点）

| 立场 | 模型 | 论据 |
|---|---|---|
| capability 粒度（5 个） | opus2 / mimo | 模型能力比任务语义稳定；任务→capability 映射已写清楚 |
| semantic 粒度（translate/triage/...） | gemini / grok | "能力"随时间变质；未来垂直小模型微调需要按任务路由 |
| 进一步压缩到 2 档（FAST/DEEP） | minimax | omlx-sdk 之外只有 Claude Code 备选，不存在"换模型"灵活性需求 |
| 折中：capability + 任务 tag | kimi | 保留 capability 做路由，在 `tags` 里记 `pipeline`/`task_semantic` 供分析 |

**orchestrator 评价**：**kimi 的折中方案最合理** — 解耦但不丢失语义。当前代码已经在传 `caller` 和 `tags`，只需约定 tag 结构。semantic intent 的风险是 prism 端的业务语义不稳定（articlize 的定义正在演化），不适合作为稳定契约。

### 公共 / 私人分层的 seam 位置（3 种视角）

| 视角 | 模型 | 论据 |
|---|---|---|
| seam 在排序层是对的 | opus2 / mimo | 但 opus2 指出 sources.yaml 本身也是偏好信号，公开它等于公开 80% 画像 |
| seam 应该在 prompt 层 | gemini | articlize/analyze 阶段注入用户 context；公共层只做清洗 + 聚类，不做语义提炼 |
| seam 不该做 Protocol | kimi / minimax | 函数钩子足够；Protocol 是对"未来 fork"的提前投资，项目开源策略本身未收敛 |

**orchestrator 评价**：opus2 提出的 "sources.yaml 是最强偏好" 是全场最尖锐的盲点。公开 sources.yaml + 私有排序权重 ≠ 真正的隐私边界。gemini 的"个性化前置到 prompt"是长期正确方向但 Wave 1 落地过重。

---

## 其他独立洞察（单模型但值得记录）

- **opus2**：`fast` 和 `default` 当前映射到同一模型（gemma-4-26b-a4b-it-8bit）→ 文档需显式标注"当前等价，分开为未来小模型预留"，避免维护者困惑
- **mimo**：Warden drain worker 的"优先级反转"隐患 — `FOLLOWING_FIRST` 可能让非订阅源高价值 signal 被无限饿死
- **kimi**：需要 `user_source_read_watermark` 表记录"已读水位"，cookie `last_seen_at` 太脆弱，清浏览器就丢
- **gemini**：90 天硬删过粗 — 应对历史 signals 做"极致压缩归档"（Entity 知识卡片或向量），否则系统只有短期记忆
- **minimax**：SPEC 缺"omlx 回滚路径" — Wave 2 迁移过程若 omlx 故障，整个系统裸奔无 graceful fallback。需要 `call_with_fallback(caller_intent, omlx_fn, claude_fn)` 兜底
- **grok**：Notion publish / 翻译转录的 DMCA 版权风险（在"公开站点"设想下）
- **kimi**：`/feed/following` 徽章会增加认知噪音（作者日常是"扫新内容"不是"检查源健康状态"）

---

## Orchestrator Decision: REVISED

基于 6 模型共识，对 SPEC §11–§12 做以下修订：

### 立即纳入（强共识，低成本）

1. **追加 W1-0：数据库备份脚本** — 每日 `sqlite3 .backup` 到外部存储 + 保留 14 天。生存级需求，不可推迟。
2. **W1-11 降级** — `prism/personalize/` 只建空目录 + TODO 注释，**不定义 ReRanker Protocol**。未来有真实消费者时再抽象。
3. **X 静默失败告警前移到 Wave 1** — 基于 `sources.last_sync_ok` 字段，sync 返回 0 items 连续 N 次就写 `/board` 告警 + 可选邮件/iMessage。

### 修订 Wave 2 Freshness Warden

- **保留**：`source_health` 观测（或复用现有 sources 字段，见下）+ `/board` 徽章（作者自己看 dashboard，不在 `/feed/following` 加徽章 — 认知噪音）
- **砍掉**：独立 drain worker + 新 launchd tick
- **替代方案**：直接在 `hourly.sh` / `xyz_queue.sh` 加逻辑 — omlx idle 时优先消费 translate_backlog 最旧的 20 条。不引入新进程、不抢调度资源。
- **记录**：若未来需要更复杂调度，再升级为独立 worker，届时必须加 SQLite advisory lock + 老化因子（mimo 的警告）

### 修订 intent 协议（§4.2）

保留 capability 粒度（5 个 intent），**但强制约定 `tags` 结构**：
```python
chat(messages, intent="fast", tags={
  "pipeline": "translate",      # articlize | triage | translate | expand | briefing
  "task_semantic": "ja-to-zh",  # 任务级语义，供事后分析和模型切换决策
})
```
这样 prism 端业务语义不绑到 SDK，但未来想把 "translate" 切到专用小模型时，manager 端路由表可以直接用 tag 匹配。

### 追加 Wave 1 收尾动作

**W1-12：数据蒸馏再砍表** — 执行 §11 的 DROP 前，写一次性 `scripts/distill_preferences.py`：
- 读取 `feed_interactions` / `pairwise_comparisons` / `ctr_samples` 的历史 winner / save / skip 记录
- 用 reasoning intent 聚合成 `docs/user_preference_profile_2026Q1.md`（主题 / 源 / 作者的偏好向量 + 典型案例）
- 归档后再 DROP 原表

### 补 SPEC 章节

- **§14 数据与隐私**（新）：备份策略、外部投喂 /external-feed 的数据归属、sources.yaml 的公开性声明（opus2 盲点）
- **§15 回滚 / 降级**（新）：omlx 持续失败时的 call_with_fallback 统一降级路径（minimax 盲点）
- **§4.2.3 intent 映射表**：补"fast 和 default 当前等价"注释 + `tags` 约定

### 搁置但记录

- **gemini 的"个性化前置到 prompt 层"** — 长期正确方向，但当前翻译/articlize 已经有积压压力，前置个性化会让每次调用更贵。记入 Wave 4 作为 "preference layer rebuild" 的实现路径候选。
- **gemini 的"动态简报替代订阅流"** — 超出当前定位 scope，但值得作为 `/briefing` 子路径实验（不替代主路径）
- **kimi 的 `user_source_read_watermark` 表** — W2-2 "新更新"状态如果用 cookie 实现遇到问题，再升级到此方案

---

## Decision Report

| # | Model | Lab | Role | Status | Adopted |
|---|-------|-----|------|--------|---------|
| 1 | Claude Opus 4.6 | Anthropic | Proposer A | ✓ | full（W1-0 备份、W1-11 降级、Warden 瘦身主干） |
| 2 | Grok 4 | xAI | Red Team | ✓ | partial（DMCA 风险记入 §14 待补；其他批评未采纳） |
| 3 | Gemini 3.1 Pro | Google | Proposer B | ✓ | partial（数据蒸馏纳入 W1-12；prompt 层个性化记入 Wave 4） |
| 4 | MiMo V2 Pro | Xiaomi | Feasibility | ✓ | full（死锁 / 饥饿警告作为 Warden 未来升级约束） |
| 5 | Kimi K2.5 | Moonshot | User Advocate | ✓ | full（徽章搬 /board、intent tag 结构、函数钩子） |
| 6 | MiniMax M2.7 | MiniMax | Scope Guardian | ✓ | full（omlx 回滚路径、Warden 并入现有 cron） |

- **Consensus points**: 5（Warden 瘦身 / W1-11 降级 / 数据备份盲点 / X cookie 告警 / 数据蒸馏）
- **Divergence points**: 3（定位本身 / intent 粒度 / seam 位置）
- **Opus Decision**: **REVISED** — SPEC 核心方向正确，但 §12 Wave 1/2 需按上面修订。
- **Key Risk**: Warden 并入现有 cron 听起来简单，但 `hourly.sh` 已经塞了 sync / expand-links / cluster / analyze-triage / analyze-expand / quality-scan 6 步，再加 drain 逻辑会让这个 shell 脚本变成调度黑洞，未来 debug 成本高。建议 drain 逻辑放独立 `prism/scheduling/drain.sh` 由 launchd 独立调度，但实现仍是"消费现有 queue + 调 pipeline"，不引入新 worker 抽象。
