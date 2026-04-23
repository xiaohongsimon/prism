# Prism — Reality Audit (Reverse-Engineered Spec)

> 本文档从当前代码逆向生成，目的是把项目**真实状态**一次性摊开来 review。
> 换句话说：**目标**在 `constitution/`，**现实**在这里，**差距**是 §11 的技术债地图。
>
> **2026-04-21 拆分**：本文原包含 mission / tech-stack / roadmap，现已抽离到 `docs/constitution/`：
> - Mission（项目为什么存在）→ `constitution/mission.md`
> - Tech Stack（系统是什么）→ `constitution/tech-stack.md`（含原 §2 数据 / §3 源 / §4 管线 / §5 Web）
> - Roadmap（下一步做什么）→ `constitution/roadmap.md`（含原 §12 Wave / §13 Checklist）
>
> 本文件保留"现实复盘"单一职责：
> - §1 System Topology —— 各渠道端到端走查（真实跑起来是什么样）
> - §6 Ranking & Feedback Loop —— 排序/反馈的上一代实现现状（为什么要清理）
> - §7–§10 —— Output / Scheduling / External deps / Configuration
> - §11 —— 技术债地图（当前代码相对目标的偏离清单）
>
> 与 `RUNTIME.md` 互补：RUNTIME 管"现在在跑什么 / 出问题先看哪"，本文管"系统长什么样、哪里在失控"。
> 与 `docs/specs/2026-03-24-prism-design.md` 的关系：原 design 是目标愿景，本文档是现实复盘。差异就是偏离量。

---

> **Wave 1 更新（2026-04-23）** — §6 / §11 里列出的"上一代实现残留"已经落地清理：
> - 砍表：`signal_scores` / `pairwise_comparisons` / `source_weights` / `ctr_samples` / `feed_impressions`
> - 砍码：`prism/web/pairwise.py`、`prism/ctr/`、`tests/ctr/`、`tests/web/test_pairwise.py`、`tests/test_pair_strategy.py`
> - 砍路径：`/feed/action` 背景任务里的 CTR 物化、`/feed/more` 里的 impression 记录、`daily.sh` 里的 `adjust_source_weights`
> - 重写：`/pairwise/liked` / `/pairwise/profile` 的后端查询从 `pairwise_comparisons` 切到 `feed_interactions`（URL 路径保留，避免动模板）
> - 新增：`prism/personalize/` ReRanker 接口（默认 IdentityReRanker 直通），`/feed/more` 现在把候选行通过 Protocol 走一遍 — 未来换实验变体零改动
> - 历史数据：pairwise_comparisons (133) + signal_scores (260) CSV 归档到 `data/archive/wave1/MANIFEST.json`
>
> 下面的 §6 / §11 文本保留了清理前的描述，仅作为"曾经长这样"的考古。

---

## 1. System Topology

```
                ┌──────────── External Sources ─────────────┐
                │  X (bird)   YouTube   GitHub   HN   arXiv │
                │  Reddit     PH        xyz      DLAI  Claude sessions │
                │  Follow-builders       Model economics    │
                └──────────────┬────────────────────────────┘
                               │ sync (CLI: prism sync)
                               ▼
                     ┌───────────────────┐
                     │   raw_items       │  ◀── FTS5: item_search
                     └─────────┬─────────┘
                               │ cluster (Jaccard + entity co-occurrence)
                               ▼
                     ┌───────────────────┐
                     │   clusters        │
                     │   cluster_items   │
                     └─────────┬─────────┘
                               │ analyze  (triage cheap → expand reasoning)
                               ▼
                     ┌───────────────────┐
                     │   signals         │  ◀── FTS5: signal_search
                     │   cross_links     │      (articlize for YouTube)
                     │   articles        │
                     └─────────┬─────────┘
                               │
          ┌────────────────────┼────────────────────────┐
          ▼                    ▼                        ▼
   ┌─────────────┐    ┌────────────────┐      ┌──────────────────┐
   │ trends      │    │ Web (FastAPI)  │      │ Briefing → Notion│
   │ briefings   │    │   /feed        │      │ publish-videos   │
   └─────────────┘    │   /article     │      └──────────────────┘
                      │   /board (ops) │
                      │   /pairwise/*  │ ← legacy URL 前缀，后端重写到 feed_interactions
                      └────────┬───────┘
                               │ feedback → feed_interactions
                               ▼                ↓
                     ┌─────────────────────────────────┐
                     │ preference_weights (author/tag/source/layer)
                     │ decision_log (recall / ranking 自动决策)
                     │ external_feeds (外部 URL 投喂队列)
                     └─────────────────────────────────┘

        （Wave 1 清理 2026-04-23: signal_scores / source_weights /
         pairwise_comparisons / ctr_samples / feed_impressions 已随
         prism/web/pairwise.py 和 prism/ctr/ 一同删除；历史数据归档到
         data/archive/wave1/。ReRanker 接口见 prism/personalize/，默认
         IdentityReRanker 直通。）

       Cloudflare Tunnel → prism.simon-ai.net → :8080 (prism serve)
```

### 1.1 每个渠道的端到端走查（各举一例）

> 目的：把"sync → raw_items → cluster → analyze → 呈现"这条链在不同渠道下的形态讲清楚。
> 六个样例覆盖了**短文本 / 视频 / 社区热门 / 学术 / 异步转写 / 代码活动**六类路径。

---

#### ① X 个人账号 — `x:karpathy`（最朴素的路径）

**Steps**
1. **sync** (`prism/sources/x.py`, hourly) — 通过 bird CLI 的私有 GraphQL（cookie 鉴权）
   拉取 @karpathy 最近 ~40 条推文；展开 `t.co` 短链；如果是 thread 会合并成一条。
2. **入库** → `raw_items`：一条 tweet = 一条 row。
3. **translate-bodies** (fast.sh, 3h 一跑) — gemma 翻译到 `body_zh`。
4. **cluster** (hourly) — 若近 24h 内多个源都在讨论同一条 URL / 关键词（如别人也在
   转这条），会聚到同一 cluster；单发就独占一个 cluster。
5. **analyze --triage**（廉价模型）给这条 cluster 打一个 `signal_strength` +
   `signal_layer` + tags；**analyze --expand**（推理模型）只在 strength≥4 时深读。
6. 前端 `/feed/following` 把这条作为"karpathy 的新更新"渲染出来；点卡片跳原文。

**Output 样例**
```json
// raw_items
{
  "source_id": "x:karpathy",
  "url": "https://twitter.com/karpathy/status/17xxxxxx",
  "title": "When training LLMs, the loss curves we stare at ...",  // 首行
  "body":  "When training LLMs, the loss curves we stare at ...\n(full tweet text)",
  "body_zh": "在训练 LLM 时，我们盯着的 loss 曲线 ...",
  "author": "karpathy",
  "published_at": "2026-04-20T14:22Z"
}

// signals (after analyze --expand)
{
  "summary": "Karpathy 观察 LLM 训练 loss 曲线对 learning rate 更敏感的一种现象",
  "signal_layer": "strategic",
  "signal_strength": 4,
  "why_it_matters": "对微调工程有直接启示 ...",
  "tags_json": "[\"llm\", \"training\", \"karpathy\"]"
}
```
**前端呈现**：`/feed/following` 的"karpathy"分桶里一张卡片，标题 + 译文预览 + 原文链接。

---

#### ② YouTube 频道订阅 — `yt:3Blue1Brown`（最重的一条，带结构化）

**Steps**
1. **sync** (`youtube.py`) — 拉频道 RSS（Atom），得到最近 15 条 `<entry>`：标题 + 视频 URL +
   简短描述。**body 很短**（只是 RSS 描述），不是字幕。
2. **raw_items** 入库，`published_at` 来自 RSS。
3. **enrich-youtube --limit 20**（daily）或 expand-links（hourly）— 用 `youtube-transcript-api`
   抓字幕（快；yt-dlp fallback，见 memory `feedback_youtube_transcript.md`），回写到 `body`。
4. **cluster / analyze** 和 X 一样，但因为 body 是字幕全文，analyze 能产生更细粒度的 signal。
5. **articlize** (daily, `pipeline/articlize.py`) — **把字幕 + 元数据喂给 LLM 生成结构化文章**：
   tl;dr、关键观点、章节高亮 → 写入 `articles` 表。
6. **publish-videos --limit 10** — 把 `articles` 推到 Notion（作为供外部阅读的公共产物）。
7. 前端 `/article/{id}` 提供本地阅读；`/feed/following` 在卡片上加"阅读结构化"按钮。

**Output 样例**
```json
// raw_items (enriched)
{
  "url": "https://youtu.be/abc123",
  "title": "Neural networks, part 5: backpropagation intuition",
  "body": "Hi everyone, today we're going to ... [full transcript, 8000 words]",
  "author": "3Blue1Brown",
  "published_at": "2026-04-19T18:00Z"
}

// articles (articlize)
{
  "item_id": ...,
  "headline": "反向传播的直觉：一步步把链式法则拆给你看",
  "tldr": "把 backprop 当作一个从 loss 往输入逐层"分账"的过程 ...",
  "chapters_json": "[{\"t\":0, \"title\":\"为什么还要再讲 backprop\"}, {\"t\":145, ...}]",
  "highlights_json": "[ ...章节高亮观点 ... ]",
  "lang": "zh-CN"
}
```
**前端呈现**：`/feed/following` 上卡片标出"新结构化"；点进 `/article/{id}` 看 tl;dr + 章节跳转。
**时效痛点**：articlize 只在 daily 跑 —— 白天看到更新、晚上才有结构化版，是 §11 "应加强"里的改进点。

---

#### ③ Hacker News `hn:best` / `hn:search:<kw>`（社区热门）

**Steps**
1. **sync** (`hackernews.py`) — 拉 `https://hnrss.org/best`（/best 源）；
   `hn_search.py` 跑 ~16 个关键词走 Algolia。
2. **raw_items**：title = submission 标题，body = 若是 Ask/Show HN 取 text，否则是
   link preview（可能空）。
3. **expand-links** 把外链抓个预览（不是全文抓取，只是 og:description 之类）。
4. **cluster** — HN 最容易聚类：同一个外链经常被多个提交者分别发。cluster 把它们合成一条。
5. **analyze** — HN 最重要的信号其实常常是**评论里的观点**，当前管线**不深抓评论**
   （这是结构性限制；未来可加 HN 评论拉取）。
6. 前端 `/feed` 或 `/feed/following`（如订阅了 hn）展示。

**Output 样例**
```json
// raw_items
{
  "url": "https://news.ycombinator.com/item?id=45xxxxx",
  "title": "Show HN: A tiny DB that does X really well",
  "body": "Hey HN, I built this because ... (submission text)",
  "author": "jdoe (HN user)",
  "published_at": "2026-04-21T09:10Z"
}
```
**已知限制**：HN 的价值常在讨论里 → 当前 SPEC 只抓 submission，signal 会比应有的弱。

---

#### ④ arXiv — `arxiv:cs.LG` + keyword 白名单（学术）

**Steps**
1. **sync** (`arxiv.py`) — 拉 `export.arxiv.org` cs.LG/cs.CL/cs.AI 的 RSS；
   通过 ~40 个关键词白名单过滤（`retrieval`、`agents`、`rlhf` 等）。
2. **raw_items**：title = 论文标题，body = abstract，author = 作者列表。
3. **translate-bodies** 把 abstract 翻成中文存 `body_zh`。
4. **cluster** — 多日内多个 arxiv cluster 若关键词重合度 > 阈值会合并（弱）。
5. **analyze** — 对 abstract 做结构化：主要贡献、方法类别、对比 baseline、实用性评估。
6. 前端 `/feed` 显示；不走 articlize（arxiv 不是 video）。

**Output 样例**
```json
// signals
{
  "summary": "提出一种以 entropy-regularized RL 做 agent planning 的方法，在 N 基准上提升 X%",
  "signal_layer": "strategic",
  "signal_strength": 3,
  "tags_json": "[\"agents\", \"rl\", \"benchmark\"]"
}
```

---

#### ⑤ 小宇宙播客 — `xyz:some_podcast`（**异步状态机**，最复杂）

**Steps**
1. `xiaoyuzhou.py` 的 `sync()` 是 **no-op**（不会在 hourly 阶段抓 episode）。
2. **daily** 跑 `prism xyz-queue discover` — 扫所有 `xyz:*` 源，把最近 30 天的 episode
   元数据塞进 `xyz_episode_queue`，状态 `pending`。
3. **launchd xyz_queue.sh 每 15 分钟**跑 `prism xyz-queue tick`：
   - 负载检测（omlx 忙就退出）
   - 取一条 `pending` → 下载音频 → **ASR 转写**（本地模型）→ 状态 `transcribed`
   - 再取 `transcribed` → 作为 `raw_items` 插入 + 做 articlize → 状态 `inserted`/`done`
4. 进了 `raw_items` 后，后续 cluster / analyze / articlize 和 YouTube 一样走。

**Output 样例**
```
xyz_episode_queue:
  pending      →  transcribed       →  inserted       →  done
     │              │                       │                 │
     ↓              ↓                       ↓                 ↓
   元数据入队     ASR转写完成            已写 raw_items       已 articlize
```
```json
// raw_items (after inserted)
{
  "url": "https://www.xiaoyuzhoufm.com/episode/xxx",
  "title": "第 42 期：聊聊本地 LLM 的工程实践",
  "body": "[ASR 全文 10000 字]",
  "author": "某播客",
  "published_at": "2026-04-18T22:00Z"
}
```
**已知坑**：崩溃恢复剧本缺失（§11 已登记）；且应**只处理作者订阅的源**，不主动批量（memory `feedback_xiaoyuzhou_on_demand.md`）。

---

#### ⑥ GitHub — 两条截然不同的通路

**⑥a GitHub Trending** (`github.py`)
1. 拉 trending repos（可选 token）→ 每个 repo 一行 `raw_items`，title = owner/repo，
   body = 描述 + star 增量。
2. cluster：同 repo 在多个渠道出现（比如 HN 也在讨论）会合并。
3. analyze：生成 "这个 repo 为什么火" 的 signal。
4. **⑥b GitHub Releases** (`github_releases.py`) — 对 ~15 个 org（pydantic、langchain、
   openai、huggingface 等）走 API 拉 48h 内的 release notes，body = changelog 摘录。
5. **⑥c GitHub Home** (`github_home.py`) — 用 `gh` CLI 抓 `received_events`（作者 github
   首页流），过滤掉 Release/Watch/Create 噪声。

**Output 样例（release）**
```json
{
  "url": "https://github.com/pydantic/pydantic/releases/tag/v3.5.0",
  "title": "pydantic v3.5.0",
  "body": "## What's new\n- Feature X ...\n- Breaking: Y ...",
  "author": "pydantic",
  "published_at": "2026-04-20T10:00Z"
}
```

---

### 1.2 整体合理性 review

把六个渠道走一遍后可以看出**整条管线是一条统一的"扇入-归一-扇出"漏斗**：

**扇入层**（左侧 22 个 adapter）：
- 唯一契约是 `SourceAdapter.sync() → SyncResult(items: list[RawItem])`
- 每个渠道的脏活（cookie、私有 API、负载感知、ASR）都被封装在 adapter / 队列里
- ✅ **扇入设计是合理的** —— 加新源只需写一个 adapter，不触动下游

**归一层**（`raw_items` 这张表是整条管线的腰）：
- 所有渠道都被归一成 `{url, title, body, author, published_at}` —— 这使得下游
  cluster / translate / analyze / articlize 可以**对所有来源同形处理**
- ✅ **归一设计是合理的**；代价是丢了渠道特异的结构（例如 HN 评论、arXiv 作者列表），
  这是下游弱化 signal 的根因（见 HN 例子）

**加工层**（cluster → analyze → articlize）：
- cluster：Jaccard + 同 URL + entity 共现 —— 跨渠道去重，合并同一热点
- analyze：两阶段 triage + expand —— cheap 模型先打分、reasoning 模型深读高价值 —— 是**成本/质量折中的关键设计**
- articlize：只对视频类（目前 YouTube，未来可以扩展到播客 + 长文），把"要看的"变成"可以快扫的"
- ⚠️ **加工层也存在倾斜**：articlize 只在 daily 跑 → YouTube 时效跟不上日内更新，
  是当前定位（跨模态快扫）下最影响体验的点（已记录到 §11 📉）

**扇出层**：
- `/feed/following`（主路径）、`/article/{id}`、briefing → Notion、publish-videos → Notion
- ✅ 主路径清晰；⚠️ **pairwise / BT / CTR 的扇出通路**仍然存在但不再服务于当前定位
  （见 §6 / §11）

**合理性总结**：
- 架构骨架（adapter → raw_items 归一 → 统一下游加工）是合理的，**当前阶段不需要大改**
- 真正的负担在于 **「加工层 + 扇出层里多代定位的残留代码同时存在」**：BT、CTR、ranking.py、
  persona_snapshots、两套 feed 代码、旧 /feedback 路由 —— 这些是 §11 / §12 的清理目标，
  清掉之后整体会更接近"扇入 → 归一 → 加工 → 扇出"这条干净的漏斗
- 单渠道层面最大的未做功课是：**HN 不抓评论**、**articlize 不覆盖播客/长文**、**xyz 缺崩溃恢复** —— 三个都是沿着当前定位（跨模态结构化快扫）最值得投入的方向

---

## 6. Ranking & Feedback Loop

> **新定位下的原则**：当前阶段排序的意义大幅降级 —— 主路径是 `/feed/following`，
> **时间倒排 + 按订阅源分桶**就够用了，用户自己做筛选。下面这一堆 ranking 机制
> 大部分是上一代定位（pairwise → CTR 精准推荐）的遗留 —— **当前阶段应该清理而不是
> 优化**。长期 mission（§0）仍希望做偏好推荐，但那套代码与未来目标不再贴合，
> 清理它们是为了给未来更合身的实现腾地方，不是放弃推荐方向本身。

### 6.1 当前生产排序公式（`web/feed.py::rank_feed`，仅用于 `/feed`，不是 /feed/following）

```
feed_score = bt_score                        # ← BT 僵尸数据还在加权
           + signal_strength * 10
           + 1.0 * author_pref
           + 0.6 * tag_pref
           + 0.8 * source_pref
           + 0.4 * layer_pref
```
- 然后 `_diversify_by_channel()` 按 source_type 交错防止刷屏（5 窗口内最多 2 条同类型）
- `/feed/following` 走另一条路径（按 source 分桶），不经过 rank_feed，和上面这套基本无关

### 6.2 两套并行 ranking 代码（都是技术债）

- `web/feed.py::rank_feed` — 活，被 `/feed/more` 调用 —— 新定位下 `/feed` 是次路径，这套排序重要性下降
- `web/ranking.py::compute_feed` — **死** — 定义了 hot/recommend/follow 三 tab + TAB_WEIGHTS，但 routes 已全部走 feed.py。**纯僵尸模块，该整个删掉**

### 6.3 Pairwise / BT — 清理上一代实现（不等于放弃推荐方向）

当前阶段不需要精准推荐，且这套实现绑定在旧定位上 → Pairwise / BT 整条链应该清理。
长期再做偏好推荐时会按新定位重写（见 §0 "延后但未放弃的方向"），不会复用这些表/代码。

- 砍表：`signal_scores`、`pairwise_comparisons`
- 砍代码：`web/pairwise.py::update_bt_scores`、`record_vote`、save/dismiss 路径里的 BT 更新（BT_SAVE_BONUS / BT_DISMISS_PENALTY）
- 砍分数项：`rank_feed` 里的 `bt_score`
- 砍路由：`/pairwise/liked`、`/pairwise/sources`、`/pairwise/profile`（档案页无价值，历史数据可导出一次后删除）
- 砍 cron：daily.sh 里的 `adjust_source_weights()`（基于已无新数据的 pairwise win rate，本身就在慢慢失效）
- 保留但重命名：`/pairwise/feed` → `/external-feed`（跟 pairwise 无关，只是外部 URL 投喂）

### 6.4 CTR 训练链路 — 清理空转链条（不等于否定推荐方向）

- `ctr_samples` / `feed_impressions` 表、`ctr samples/backfill/stats/train/eval` CLI、`data/ctr/` 模型文件 —— **清理**
- `rank_feed` 里本来就没加载 CTR 模型 → 训练链条一直在空转，即使"推荐方向"还保留，这条链也是负资产
- 当前阶段 `/feed/following` 用时间倒排、`/feed` 用 signal_strength + 简单权重就够；
  长期如要做偏好推荐，会按新定位（"反映技术品味的演化"而非"点击率优化"）重建，不会回到 XGBoost CTR 这套

### 6.5 简化后的排序公式（目标态）

```
/feed/following:
  ORDER BY source_bucket, published_at DESC
  （按你关注的源分组，每组按时间倒排；新更新高亮）

/feed（发现新源）:
  feed_score = signal_strength * 10
             + author_pref (follow = +3, 其他 = 0)
             + source_pref (weight from explicit follow/unfollow)
             - tag_mute_penalty (muted tag = -100)
  ORDER BY feed_score DESC, published_at DESC
```

比现在少：`bt_score`、`tag_pref` 的连续加权、`layer_pref`、`_diversify_by_channel`
（分桶逻辑挪到 /following 里即可）。

### 6.6 反馈事件全览（按新定位重新评估）

| event_type | 来源 UI | 保留理由 | 新定位下的作用 |
|---|---|---|---|
| **follow_author** | 创作者名 / /creator 页 | ⭐ 最强信号 | 订阅管理；直接决定是否进 /following |
| **unfollow_author** | 同上 | ⭐ 最强负信号 | 从 /following 摘掉该源 |
| **mute_topic** | feed tag | ✓ 有用的负信号 | 硬屏蔽该 tag（≤-3） |
| **save** | feed 卡片 | ✓ 稍后读入口 | 写 feed_interactions；新定位下不改 BT |
| dismiss | feed 卡片 | ⚠ 价值下降 | 本质是"这条不想看" —— 能不能合并到 mute_topic？ |
| click | feed + article | ⚠ 价值下降 | 新定位不用 CTR 训练；只保留基础计数做 UX（"低点击率源提示取消订阅"） |
| save（legacy /feedback） | /article like | ✗ 冗余 | 与 /feed/action 重复，砍掉 |
| like/unlike (creator) | /creator 页 | ⚠ 孤岛 | 只影响 source 权重 ±0.5，不影响 signal —— 要么接入统一通路，要么砍 |
| external feed | /article/like + /pairwise/feed | ✓ 保留 | 从外部导入 URL 作为 signal，价值清晰 |

⚠️ **两个反馈通路** /feedback 和 /feed/action 应该合并成一条 —— /feedback 直接砍。

### 6.7 Freshness Warden（订阅守望）— 观测 + 主动推进

> **为什么有这一节**：当前管线"观测"和"推进"是两件分开的事。观测散在 `health_check.py` / `quality-scan` / `/board`；
> 推进靠 cron 固定频率 + 固定限额。结果是：`/board` 能看到"翻译积压 799"，但没人主动把 omlx 吃满。
> 本节把两件事合成**一个命名角色**，既保证订阅流的健康可见，又保证 omlx 的空闲时间被用来消积压。

#### 6.7.1 两层职责

| 层 | 做什么 | 当前谁在做 |
|---|---|---|
| **观测层** | 跟踪每个 source 的 last_sync_ok / last_new_item / silent_fail；跟踪各队列积压（translate / articlize / analyze expand / xyz_queue） | `health_check.py` + `quality-scan` + `/board`（散） |
| **推进层** | omlx idle 时，按优先级从积压队列里取任务喂给 omlx | **缺失** ← 是 799 积压无人消费的根因 |

#### 6.7.2 新模块 `prism/pipeline/freshness.py`

合并散点，暴露三个子命令：

- `prism warden scan` — 观测一轮，写 `source_health` 表 + 刷 `/board` 数据源（替代 `health_check.py` 里源健康那部分）
- `prism warden drain --budget N` — 推进一轮（见 6.7.3）
- `prism warden tick` — scan + drain 合一，launchd 调度用

新表 `source_health`（per-source 单行滚动写）：

```sql
source_key TEXT PRIMARY KEY
last_sync_attempt_at
last_sync_ok_at
last_new_item_at         -- 上次真的产出 raw_items 的时间
consecutive_failures
silent_fail_count        -- sync 成功但长期无新 item（可能 cookie 静默失效）
status                   -- green / yellow / red
```

#### 6.7.3 Drain worker 的优先级策略

```
while omlx_idle() and budget > 0:
    task = pick_highest_priority(
        queues=[translate_backlog, articlize_backlog, analyze_expand_backlog, xyz_queue],
        priority=FOLLOWING_FIRST,   # 用户订阅的源优先
    )
    if task is None: break
    run(task)     # 走 omlx-sdk (§4.2)
    budget -= 1
```

- **负载感知**：复用 `xyz_queue` 已有的 omlx idle 探测逻辑（别再写第二份）
- **优先级**：
  1. 订阅博主（`/feed/following` 会看到的源）的 translate / articlize
  2. 近 24h 内的新 raw_items 的 translate
  3. 近 7 天的 analyze --expand（strength≥4）
  4. xyz_queue（已有独立 tick，可选统一到 warden 下 —— Wave 2 再合）
  5. 旧积压
- **budget** 由 launchd 传：tick 每 5 分钟跑一次、每次最多处理 N 条（初值 10，根据 omlx 忙闲调）

#### 6.7.4 主路径可见性（`/feed/following` 打徽章）

每个分桶头显示：

- `🟢 2h 前更新` — 健康
- `🟡 3d 无新内容` — 可能博主本来就没发，也可能源静默失效
- `🔴 sync 连续失败 / cookie 过期` — 需要手动介入
- `⏳ 翻译中 / 待结构化` — 正在被 drain worker 处理

这解决你截图里"dashboard 显示 799 待译，但 feed 页看不出来"的割裂。

#### 6.7.5 与现有模块的关系

| 模块 | 动作 |
|---|---|
| `scripts/health_check.py` | 源相关部分并入 `freshness.py`；进程级 unstick（reset consecutive_failures）保留在 health_check |
| `prism quality-scan` | 异常检测逻辑不动；改为 **消费 source_health** 而非各自查询 |
| `/board` | 数据源改为 `source_health` + 队列长度；不用再重算 |
| launchd `com.prism.hourly` / `fast` / `daily` | 内嵌的 `--limit` 配额逐步交给 drain worker 控制；脚本收敛（Wave 2）|
| `xyz_queue.sh` | 保留独立 tick；Wave 2 评估并入 warden |

---

## 7. Output & Publishing

### 7.1 Briefing（`output/briefing.py`）
- 加载 signals + trends + cross_links + narrative（job_runs）+ entity 上下文（⚠️ entity 已冻结 → 这部分是空跑）+ radar（新/成长/衰退 entity）
- Jinja2 模板 `templates/briefing.html.j2`
- 落盘 `briefings/{date}.html` + 写 `briefings` 表

### 7.2 Notion（`output/notion.py` + CLI `publish` / `publish-videos`）
- `NOTION_API_KEY` + `NOTION_BRIEFING_PARENT_PAGE_ID`
- `publish --notion`：推当日 briefing
- `publish-videos --limit 10`：YouTube ≥500 字符字幕 → LLM 结构化（chapters + <<insight>>）→ Notion page
- 去重靠 `notion_exports` 表，**但该表不在 init_db() 里 —— 是 CLI 里 inline CREATE** ⚠️
- 无限增长，无 purge 策略

---

## 8. Scheduling（launchd）

| Job | 频率 | 脚本 | 关键命令 |
|---|---|---|---|
| `com.prism.web` | RunAtLoad + KeepAlive | — | `prism serve --port 8080` |
| `com.prism.hourly` | 3600s | `hourly.sh` | sync → expand-links → cluster → analyze --triage → analyze --expand → quality-scan |
| `com.prism.fast` | 10800s (3h) | `fast.sh` | 子集 sync（x/follow_builders/hn/reddit/ph）→ translate → cluster → analyze --incremental |
| `com.prism.daily` | 每日 08:00 | `daily.sh` | sync-follows → sync → … → articlize → analyze --daily → briefing → publish × 2 → cleanup → adjust_source_weights |
| `com.prism.xyz-queue` | 900s (15m) | `xyz_queue.sh` | `xyz-queue tick` |
| `com.prism.external-feed` | 3600s | CLI 直接 | `process-external-feeds` |
| `com.cloudflare.prism-tunnel` | RunAtLoad + KeepAlive | — | cloudflared tunnel |

⚠️ **`fast.sh` 不在 `prism/scheduling/` 目录下**（但 plist 在 ~/Library/LaunchAgents/ 里）—— 2026-04-21 刚加，还是 untracked。

---

## 9. External Dependencies

| 服务 | 模块 | 认证 | 已知失败模式 |
|---|---|---|---|
| omlx | llm.py::call_llm | 本地 | gateway 抖 → 降级 backend；高并发 503 |
| Anthropic Claude | llm.py::call_claude | 经 :8100 token tracker 代理 | 代理挂 → 重试 3 次，再挂静默 |
| Notion | publish / publish-videos | NOTION_API_KEY | 无 backoff，依赖 httpx.raise_for_status |
| X | bird CLI | `AUTH_TOKEN + CT0` | cookie 失效静默；需代理（memory）|
| GitHub | github/github_releases/github_home | 可选 token / gh 已登录 | 60 req/hr 无 token；429 |
| YouTube | youtube / yt-dlp | 无 / Chrome cookies | 字幕不可得 |
| HN / Reddit / PH / arXiv | 各自 adapter | 公开 | 超时 → SyncResult(success=False) |
| Cloudflare Tunnel | cloudflared | `~/.cloudflared/` | 挂 → launchd 重启 |

**凭据分散**：`.env` + `~/.config/prism/x_cookies.env` + `~/.cloudflared/` + shell 环境变量，无集中管理。

---

## 10. Configuration Surfaces

### `prism/config.py`（28 行，ENV → 设置）
- DB 路径
- 两套 LLM endpoint（omlx + premium claude）
- 两个/三个模型 ID（主 / cheap / premium）
- Notion 凭据
- `PRISM_ADMIN_PASSWORD`（启用多用户鉴权）
- `PRISM_API_TOKEN` — **存在但代码里无引用** ⚠️

### `config/sources.yaml`
- 唯一 source of truth，sources 表只跟运行时状态
- 每条 entry：type + handle + display_name + 可选 depth/lang/filters

### `config/entities.yaml`
- person / org / project 名单，~24 条 seed
- **已冻结** —— entity-link 首次运行会迁入 DB，之后不再用

---

## 11. 技术债地图（按新定位重排）

### 🧹 应清理的上一代推荐实现（不等于放弃推荐方向）

> 注意：长期 mission 仍保留"基于偏好的推荐引擎"（见 §0），所以这一节的"清理"**不是
> 放弃方向**，而是清掉和旧定位耦合太深、已变成僵尸的具体实现 —— 未来重建会按
> 新定位设计新的偏好层，不会复用这些表和代码。

1. **Pairwise + BT 系统**（决策：**清理**）
   - 表：`signal_scores`、`pairwise_comparisons`
   - 代码：`web/pairwise.py::update_bt_scores/record_vote`、save/dismiss 路径的 BT 更新
   - 路由：`/pairwise/liked`、`/pairwise/sources`、`/pairwise/profile`（只读档案）
   - 每日 `adjust_source_weights`（基于已无新数据的 pairwise win rate，在慢慢失效）
   - 当前阶段不需要精准排序，人肉筛选足够；且这套实现绑定在"两条选一条"的旧交互上

2. **CTR 训练链**（决策：**清理**）
   - 表：`ctr_samples`、`feed_impressions`
   - CLI：`ctr samples/backfill/stats/train/eval`
   - 模型文件：`data/ctr/`
   - `rank_feed` 根本没加载 CTR 模型 → 训练链空转（即使未来做推荐，也不会以"点击率"为目标）

3. **`web/ranking.py`（整个模块）**（决策：**清理**）
   - 死代码，被 `feed.py::rank_feed` 取代

4. **`/feedback` 路由 + `feedback` 表**（决策：**清理**）
   - 与 /feed/action 重复，且更新的是死模块 ranking.py 的权重

5. **persona_snapshots 问卷偏好**（决策：**清理**）
   - 显式问卷当前阶段用不上；未来做推荐时要靠隐式信号（follow/mute/阅读时长 + 文字反馈）

### 🧊 应复审的冻结模块

6. **Entity 系统**（决策：**降级为可选，不主动推进**）
   - 4 个 pipeline 文件 + 5 张表 + 3 个 CLI，完整但 cron 不调用
   - briefing 里还在查 entity 上下文 → 空查（应从 briefing 代码里移除空查逻辑）
   - 新定位（扫订阅 + 点原文）对 entity graph 没刚需
   - **保留代码但不投入**；briefing 里的 entity 查询要么删要么写 fallback

### 🏗️ 半成品 / WIP（保留但需收敛）

7. **`analyze --triage / --expand` 两阶段**
   - hourly.sh 已切过去，fast.sh / daily.sh 仍用 `--incremental` 兜底
   - 对新定位非常重要（结构化质量是核心）→ 应全面切换两阶段，弃 `--incremental`

8. **外部投喂闭环**
   - `external_feeds.processed` 字段没人写 1
   - `/pairwise/feed` 名字骗人 → 改名 `/external-feed`
   - 新定位下**外部 URL 投喂是有价值的**（发现新源的入口之一）

9. **小宇宙 xyz_queue**
   - 状态机 + ASR + 负载检测，复杂但功能对"订阅阅读器"有价值
   - **保留**；但缺失崩溃恢复剧本要补

10. **`notion_exports` 表在 CLI 里 inline 建**
    - 应迁入 `init_db()`，加 purge 策略

### 🧊 过度设计（对新定位）

11. **Auth 系统**（auth_users / invite_codes / auth_sessions）
    - 单用户项目，一个 shared secret 就够了

12. **`course/` 扩展框架**
    - 只实现了 DLAI，抽象过度，应去抽象

### 📉 新定位下被**低估**的模块（应加强投入）

13. **翻译管线（translate.py）**
    - 当前 fast.sh 每 3h 跑 gemma 翻译，时效性是核心价值
    - 缺：翻译失败告警、质量评估、按博主/源定制 prompt

14. **articlize（视频结构化）**
    - 当前只有 daily.sh 跑 → **时效不够** — 你白天看到 YT 订阅更新，晚上才有结构化版本
    - **应提频到 hourly 或 fast.sh**

15. **/feed/following 主路径**
    - 你每日主要路径，但缺：
      - "上次访问以来的新更新"高亮
      - 低价值博主提示（长期不点击 → 提示取消订阅）
      - 源分组的可调顺序

16. **/ 根路径 redirect**
    - 当前 `GET /` → `/feed`，对登录用户应该是 `/feed/following`

### 📏 Scale / 卫生问题

17. **34 个 CLI 命令** 应分组：日常管线 / 手管 / 调试 / 归档

18. **35+ 表 vs 单用户**：砍完 §11.1-5 之后能降到 ~20 张

19. **docs/specs 已经多代重叠**：原设计 → v2 entity → pairwise → feed-first → convergence-engine → 本次定位。**把旧 spec 移到 docs/specs/archive/**，本文档作为 single source of truth

20. **孤儿资源**
    - partial 模板：`feed_card_{a,c,d,f}.html`
    - 死字段：`raw_items.thread_partial`、`entity_candidates.expires_at`、`signals.{content_zh, tl_perspective, summary}` 冗余列
    - 死 config：`PRISM_API_TOKEN`

---


---

*本文档由逆向代码生成，可能有细节偏差。发现不对的地方直接改 SPEC.md，然后修代码向它收敛。*
