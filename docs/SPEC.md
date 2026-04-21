# Prism — Reverse-Engineered Spec (as of 2026-04-21)

> 本文档从当前代码逆向生成，目的是把项目当前**真实状态**一次性摊开来 review。
> 与 `docs/RUNTIME.md` 互补：RUNTIME 管"现在在跑什么 / 出问题先看哪"，SPEC 管"系统是什么 / 有哪些模块 / 哪里在失控"。
> 和 `docs/specs/2026-03-24-prism-design.md` 的关系：原 design 是目标愿景，本文档是**现实复盘**。差异就是偏离量。

---

## 0. Mission (2026-04-21 重写)

> **关于这一节的定性**：项目的长期终局（目标用户、外部价值、开源策略）**尚未完全想清楚**
> —— 参见 memory `project_prism_positioning.md` 未收敛的 5 种框架。
> 所以本节写的是**当前聚焦**和**延后方向**，不是永久 mission。

### 当前聚焦 — 解决明确的个人痛点

Prism 当前阶段是**个人跨渠道 / 跨语种 / 跨模态的信息聚合与结构化阅读器**，服务作者本人。
三个明确的痛点：

1. **跨渠道**：把散落在 X / YouTube / HN / arXiv / GitHub / 小宇宙 / Reddit / PH / ... 的信号
   聚到一起，不用手动在多个 app / 网站之间跳
2. **跨语种**：用本地 AI 做外文内容的翻译，快速筛选
3. **跨模态**：视频 / 播客 / 长文 → 统一结构化成文本（tl;dr + 观点 + 章节高亮），快扫

### Core value proposition（短期）

```
你订阅 → 我跨渠道抓取 → 我跨语种翻译 → 我跨模态结构化 → 你快扫 → 你点原文
```

"代你预处理"是当前的核心 —— 人的判断比模型的猜测可靠，但人读不完多渠道多语种的原始流量。

### Usage model（按当前使用频率）

| 层级 | 路径 | 作用 |
|---|---|---|
| **主路径** | `/feed/following` | 订阅更新流（"我关注的人最近说了啥"）— **每日高频** |
| **主路径** | `/creator/{key}` | 单博主全量历史 |
| **主路径** | `/article/{id}` | 视频 / 长文的结构化阅读 |
| 次路径 | `/feed` | 发现新源（偶尔用） |
| 次路径 | `/briefing` | 周期性回顾（不一定每日） |
| 维护路径 | `/channel/{key}`、`sources.yaml` | 订阅增删 |

### 当前优先投入

1. **翻译质量 + 时效** — 关注的博主白天发了，晚上才能看到译文是不合格的
2. **结构化质量** — 视频转文字稿、长文 tl;dr、关键观点抽取的准确度
3. **"新更新"状态** — 上次访问以来哪些源有新内容
4. **订阅管理 UX** — 基于浏览行为推荐取消低价值订阅 / 发现新源
5. **原文链路可达性** — 点击到原文的速度和成功率

### 延后但未放弃的方向

下面这些**不是 non-goal，是 not-now**。它们在长期路线图里还有位置，但不在当前 scope：

- **基于偏好的推荐引擎（长期终局）** — 最终仍希望做一个推荐引擎，目标有两个：
  1. 筛选信息流（在已订阅的前提下再做一层个性化）
  2. **反向地帮助作者了解并提升自己的技术品味** —— 推荐不是单向的"投其所好"，
     而是作为镜子记录偏好演化、提示盲区
  当前代码里的 BT / CTR 是上一代"精准推荐"定位的产物，**应该先清理上一代实现、
  等定位真正落地时重建更贴合新目标的版本**，而不是继续维护僵尸代码
- **entity graph / 跨博主主题视图** — 当"扫订阅"之外需要"按主题追踪"时会有价值
- **对外发布 / 公开站点 / 开源** — 定位未收敛，见 `project_prism_positioning.md`

### 开源 / 个人化的内在张力（2026-04-21 补充）

项目想同时做两件性质不同的事，这是一个真实的架构约束，不是临时问题：

| 层 | 性质 | 可分享性 |
|---|---|---|
| **公共层** | 信息聚合、跨语种翻译、跨模态结构化 | ✅ 对所有读者都有价值，可以开源让人 fork 自部署，成品站点也可公开让人浏览 |
| **个人层** | 基于作者一人偏好的推荐引擎 | ❌ 只拟合单一开发者，无法兼顾多人偏好；**不能直接复用**，只能把**方法论 / 经验**抽出来分享 |

**为什么分享是经济上理性的（Token / 本地机经济学）**：

出发点是"我个人的新闻工作台"，但分享优质结构化结果不是单纯利他 —— 它在
当前硬件 / Token 经济下**几乎零边际成本**：

- **本地机经济学**：Mac Studio 512GB 已经为个人使用 7×24 跑推理。
  机器的折旧、电力、模型加载开销是**沉没成本**，多服务 N 个读者的**边际算力接近 0**
  （只要不打爆显存 / 带宽）
- **Token 经济学**：翻译 + 结构化是**生产一次 → 可复用多次**的工件。
  为自己生成的译文和结构化稿件，同步给其他读者不需要重新消耗 tokens
- **推论**：公共层（抓取 + 翻译 + 结构化）的产物天然适合对外分享，
  不对外反而浪费了已付出的算力；而个人层（偏好 / 推荐）仍然是纯私人消费

**架构推论**：

- 代码 + 数据加工能力是公共产物；偏好模型与训练数据是私人产物
- 未来做推荐时，设计上要让"个人偏好层"**可插拔**（别人 fork 后能替换成自己的），
  而不是把作者本人的偏好硬编码进公共代码
- 本 SPEC / sources.yaml / 管线 / 结构化 prompt 应继续按公共物维护；
  偏好相关的部分（权重、评分、推荐策略）应隔离在可替换模块里
- 对外分享的重点是**结构化产物 + 流程经验**，不是作者的偏好数据本身

**Pluggable personalize seam（Wave 1 清理时同步预留）**：

为了让清理上一代实现时不再把偏好逻辑硬编码进 web/feed.py，Wave 1 在删 BT/CTR 的同时：

- 新增空目录 `prism/personalize/`，定义一个 Protocol（草案）：

  ```python
  class ReRanker(Protocol):
      def rerank(
          self,
          candidates: list[Signal],
          context: FeedContext,   # path(following/feed), user_recent_actions, ...
      ) -> list[Signal]: ...
  ```

- 公共 repo ships **`IdentityReRanker`** —— 按 `published_at` 时间倒排，不做任何偏好加权，
  `/feed` 和 `/feed/following` 默认挂这个
- 作者本人维护私人分支，在 `personalize/` 里填 `PersonalReRanker` 做个性化加权
- **好处**：Wave 1 清理时 seam 已落定，未来真要重建偏好层不用重拆 feed 代码；
  同时开源读者拿到的是"纯时间流 + 结构化"的干净实现，没混入作者偏好

**社区反馈通路（零运维原则）**：

- **不自建评论 / 多用户系统** —— 与"不做多用户工程"non-goal 对齐
- 源建议 / 讨论 / Q&A 全部走 **GitHub**：
  - 启用 GitHub **Discussions**（分类：`Source suggestions` / `General` / `Show & tell`）
  - 配一个 Issue 模板"建议新源"收结构化输入（url / 类型 / 推荐理由 / 更新频率）
  - 真想直接加源的读者可以提 PR 改 `config/sources.yaml`
- 网站端用 **giscus**（iframe 嵌入，评论落到 GitHub Discussions）挂在 `/showcase`
  和 `/sources` 等公开页底部 —— 读者可以就地留言，但存储/moderation 全在 GitHub，
  站点后端不增任何负担

### 明确的 non-goal（这些是结构性约束，不是暂缓）

- 多用户 / SaaS
- 手机端专门工程（web 兼容够了）
- AI 助写 / 内容生产
- 离开"个人信息聚合"这个锚点去扩张功能

### 历史遗留

- 原设计（2026-03-24）核心是 **pairwise comparison + Bradley-Terry 精准推荐**
- 2026-04 重定位 → **feed-first + CTR**
- 2026-04-21 再收敛 → **跨渠道/跨语种/跨模态阅读器 + 延后的偏好推荐**
- 每次定位变化后没有彻底清理旧代码，所以 BT / pairwise / CTR 的代码全都在跑（详见 §6 §11）。
  清理它们不等于放弃推荐方向，只是不让上一代实现挡住下一代

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
                      │   /pairwise/*  │ ← 档案/只读
                      └────────┬───────┘
                               │ feedback → feed_interactions
                               ▼                ↓
                     ┌─────────────────────────────────┐
                     │ preference_weights (author/tag/source/layer)
                     │ source_weights     (pairwise win rate)
                     │ signal_scores      (BT, 僵尸中)
                     │ ctr_samples        (skip-above 训练数据)
                     └─────────────────────────────────┘
                               ↓
                     日终 adjust_source_weights()

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

## 2. Data Model — 35+ tables

> 数据库是 `data/prism.sqlite3`，schema 全部在 `prism/db.py::init_db()`。

### 2.1 核心信号流（活）

| 表 | 用途 | 写入 | 读取 | 备注 |
|---|---|---|---|---|
| `sources` | 源目录 + 运行时状态（enabled/失败计数） | sync, source_add/remove | 全管线 | 与 sources.yaml 有状态冗余 |
| `raw_items` | 采集的原始条目 | sync adapters | cluster, analyze | 含 `body_zh` 中文列、`thread_partial` 死字段 |
| `item_search` | raw_items FTS5 | triggers | 搜索 UI | |
| `clusters` | 每日主题聚类 | cluster | analyze, briefing | `merged_context` 冗余可算出 |
| `cluster_items` | raw_item ↔ cluster | cluster | 查询 | |
| `signals` | **核心产物**：LLM 洞见 + strength + tags | analyze | 全下游 | 含 `content_zh`、`tl_perspective` 等冗余列 |
| `signal_search` | signals FTS5 | triggers | 搜索 UI | |
| `cross_links` | 跨 cluster 关联（"A 影响 B"） | analyze --daily | briefing | 稀疏 |
| `trends` | 主题热度 + 日环比 | trends | briefing | 每日一行 |
| `articles` | YouTube 视频结构化 | articlize | /article, publish-videos | |
| `briefings` | 生成的每日 brief markdown | briefing --save | publish | |
| `external_feeds` | 用户投喂的 URL | /article/like, /pairwise/feed | **processed 字段无消费者** | ⚠️ 生命周期未闭环 |

### 2.2 排序/反馈（活 + 僵尸）

| 表 | 用途 | 状态 |
|---|---|---|
| `feed_interactions` | 新反馈事件汇（save/dismiss/click/follow_author/mute_topic） | **活** — 主反馈入口 |
| `feed_impressions` | 每次 /feed/more 曝光流水 | **活** — CTR skip-above 负例的基础 |
| `ctr_samples` | CTR 训练样本 | **活**，但训练/预测在 web 里是否在用需核实 |
| `preference_weights` | (dimension, key) → weight | **活** — /feed 实际查询此表 |
| `source_weights` | 源级 win rate | **活** — daily.sh 尾部刷新 |
| `feedback` | 旧 like/dislike/save | **半活** — 只被 /article 的遗留 /feedback 路由写 |
| `pairwise_comparisons` | 两两比较历史 | **🧟 僵尸** — 有表、有读路由（/pairwise/liked）、但没有路由能**产生**新记录 |
| `signal_scores` | Bradley-Terry 分数 | **🧟 僵尸** — BT 更新逻辑还在 save/dismiss 路径里跑（+0.2/-0.1），但没有 pairwise 输入 |
| `item_interactions` | 创作者页面 per-item like | **活但孤岛** — 不反馈到 signal 评分 |
| `persona_snapshots` | 问卷式偏好 survey | **半活** — 与 preference_weights 并存，两套偏好体系 |

### 2.3 Entity 系统（全部冷冻）

| 表 | 状态 |
|---|---|
| `entity_profiles` / `entity_aliases` / `entity_candidates` / `entity_events` / `entity_search` | **冻结** — schema 齐全，代码完整（`entity_*.py`），`prism entity-link` CLI 能跑，**但没有任何 cron 在调用它**，`signals` 也没有 `entity_id` FK。纯存量。|

### 2.4 运维/审计

| 表 | 用途 | 状态 |
|---|---|---|
| `job_runs` | 流水审计（job/start/end/stats） | 活 |
| `decision_log` | 自动决策审计 | 活（仅 source pruning 在写） |
| `quality_snapshots` / `quality_anomalies` | 健康度指标 | 活（quality-scan） |
| `auth_users` / `invite_codes` / `auth_sessions` | 多用户/邀请码 | **半活** — 仅当 `PRISM_ADMIN_PASSWORD` 设了才启用；对单用户定位来说是**过度设计** |

### 2.5 异步队列

| 表 | 状态 |
|---|---|
| `xyz_episode_queue` | 小宇宙回灌队列（pending → transcribed → inserted → done），WIP |
| `notion_exports` | publish-videos 去重表（**注意：在 CLI 里 inline 创建，不在 init_db()**，是脏点） |

**死字段/冗余**（读不到、没有人写）：
- `raw_items.thread_partial`
- `entity_candidates.expires_at`（没有 TTL 清理）
- `signals.tl_perspective` + `signals.summary` 双列
- `*_zh` 翻译列散落多表 ← 应合并成单表

---

## 3. Signal Sources — 22 个活 adapter

> 全部实现 `prism/sources/base.py::SourceAdapter`。配置在 `config/sources.yaml`（唯一 source of truth）。

### 3.1 Adapter 清单

| Adapter | 上游 | Auth | 关键坑 |
|---|---|---|---|
| `x.py` | X (bird CLI) | `AUTH_TOKEN + CT0` cookie | syndication 2026-04-20 废弃；cookie 失效静默失败；需 `NODE_USE_ENV_PROXY=1 + HTTPS_PROXY`（见 memory） |
| `x_home.py` | X For You (bird) | 同上 | 2026 新增 |
| `youtube.py` | YouTube RSS (Atom) | 无 | 公开 |
| `youtube_home.py` | yt-dlp 推荐/订阅 | Chrome cookies | 2026 新增；body 要靠 enrich-youtube 异步回填 |
| `github.py` | GitHub trending | 可选 token | |
| `github_home.py` | gh CLI received_events | 已有 gh 登录 | 2026 新增；过滤 Release/Watch/Create |
| `github_releases.py` | org releases API | 可选 token | 48h lookback |
| `hackernews.py` | hnrss.org /best | 无 | |
| `hn_search.py` | Algolia | 无 | ~16 个关键词查询 |
| `arxiv.py` | export.arxiv.org RSS | 无 | 白名单 ~40 关键词，cs.LG/CL/AI |
| `reddit.py` | public JSON | 无 | User-Agent 必填 |
| `producthunt.py` | Atom feed | 无 | |
| `xiaoyuzhou.py` | 小宇宙 | **sync 里是 no-op** | 真实工作在 `xyz_queue.py` 状态机 |
| `follow_builders.py` | zarazhangrui/follow-builders JSON | 无 | 48h 新鲜度检查 |
| `model_economics.py` | OpenRouter 公开价格 | 无 | 1 条汇总 signal |
| `git_practice.py` | 本地 git repo | 本地 FS | 每日 commit summary |
| `claude_sessions.py` | 本地 `~/.claude/projects/*/memory/MEMORY.md` glob | 本地 FS | |
| `course/dlai.py` | 手工配 | 无 | 只实现了 DLAI；course/base 是为扩展留的 |

### 3.2 工具类（不是真正的 adapter）

| 文件 | 作用 |
|---|---|
| `subtitles.py` | YouTube 字幕抓取（youtube-transcript-api 为主，yt-dlp 兜底 —— 见 memory） |
| `link_expander.py` | t.co 展开，被 external_feed 用 |
| `yaml_editor.py` | sources.yaml CRUD（被 web 动态源管理用） |
| `base.py` | SyncResult + Protocol |

### 3.3 sources.yaml 数量分布

- X 个人账号：60+
- X home：1
- HN：2（/best + 16 关键词 search）
- GitHub：3（trending + releases × 15 orgs + home）
- YouTube：8 频道 + 2 home feed
- 小宇宙：8
- Reddit：6 个子版
- 其他：arXiv / PH / follow-builders / model-economics / 课程 / 本地 git / Claude sessions

---

## 4. Pipeline Stages

### 4.1 CLI 命令表（34 个 —— 扩散到位了）

**核心管线（每小时/每日会跑）**

| 命令 | 谁跑 | 作用 |
|---|---|---|
| `sync` | hourly / fast / daily | 全源抓取 |
| `expand-links --limit N` | 三个脚本都跑 | t.co 展开 + 从 tweet URL 抓 YT 字幕 |
| `cluster` | hourly / fast | 按 URL/repo/Jaccard(>0.5)/entity 聚类 |
| `analyze --triage` | hourly | 廉价模型快速分类全部新 cluster |
| `analyze --expand --min-strength 4 --limit 30` | hourly | 推理模型深读高价值 signal |
| `analyze --incremental` | fast / daily | 旧单阶段入口（将被两阶段取代） |
| `analyze --daily` | daily | 每日跨 cluster 综合（cross_links） |
| `trends` | daily | 热度 + 环比 |
| `briefing --save` | daily | 生成日报 |
| `publish --notion` | daily | 推 Notion |
| `publish-videos --limit 10` | daily | YouTube 视频 + LLM 结构化推 Notion |
| `articlize` | daily | YouTube 字幕 → 结构化文章 |
| `translate-bodies` | fast | raw_items 翻译到 body_zh |
| `enrich-youtube --limit 20` | daily | YT 短 body 回补字幕 |
| `quality-scan` | 三个都跑 | 健康度 + 异常规则 |
| `cleanup --days 90` | daily | 过期清理 |
| `sync-follows --apply --max-new 30` | daily | bird 抓 X 关注 → sources.yaml |
| `xyz-queue discover` | daily | 扫 xyz:* 源，入队最近 30 天 episode |
| `xyz-queue tick` | 每 15 分钟 launchd | 负载感知地推进一个 episode |

**偶发/手动**

| 命令 | 作用 | 状态 |
|---|---|---|
| `source list/add/remove/enable` | 手工源管理 | 活 |
| `sources prune` | 根据 win rate 注释 sources.yaml | 活 |
| `status` | 系统状态 | 活 |
| `ctr samples/backfill/stats/train/eval` | CTR 模型工具链 | 活（但是否被 web 实际用了需要 §6 确认） |
| `entity-link / entity list / entity show` | Entity 系统 | **冻结** — CLI 存在，但没有 cron 在调用 |
| `practice` | 手记 → raw_item | 活，低频 |
| `process-external-feeds` | 消费 external_feeds | **半活** — 有 launchd（com.prism.external-feed 每小时），但 external_feeds.processed 的消费闭环不完整 |

**死代码/一次性**

| 命令 | 问题 |
|---|---|
| `migrate_youtube.py` | 一次性迁移脚本，留着当参考 |

### 4.2 LLM 层（目标：所有本地推理走 `omlx-sdk`）

**当前状态**：`pipeline/llm.py` 直接 raw HTTP 到 `:8002/:8003`，模型名硬编码
（`gemma-4-26b-a4b-it-8bit` / `Qwen3.6-35B-A3B-8bit` 等），retry / timeout / token 计量
散落在调用点，omlx-manager 看不到 caller view。

**目标状态（规约，写进 SPEC）**：

#### 4.2.1 所有本地推理调用必须走 omlx-sdk

- SDK 在 `~/work/omlx-manager/sdk`，独立包 `omlx-sdk`，已由 wechat-insight 等项目接入
- 不再 raw HTTP 打 `:8002/:8003` —— SDK 内部走 `:8002` 直连（`trust_env=False` 规避 v2ray 劫持 127.0.0.1）
- 旁路异步 fire-and-forget Bill 到 omlx-manager `:8003/v1/ingest` —— 业务路径 0 影响

#### 4.2.2 调用契约：`model=` 优先，`intent=` 兜底

omlx-sdk 的 intent 是**按"模型能力"分**的静态 alias（不是按"任务"分）。当前 5 个：

| intent | 当前映射模型 | 适合什么 |
|---|---|---|
| `default` | `gemma-4-26b-a4b-it-8bit` | 通用 |
| `fast` | `gemma-4-26b-a4b-it-8bit` | 低延迟 / 短输入 |
| `coding` | `Qwen3-Coder-Next-MLX-8bit` | 代码生成 |
| `reasoning` | `Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled-qx64-hi-mlx` | 深度推理 / CoT |
| `vision` | `gemma-4-26b-a4b-it-8bit` | 多模态 |

换模型 = 改 `intents.py` 的 `DEFAULT_INTENT_MAP`（或调用端传 `intent_map=`），**零 pipeline diff**。

#### 4.2.3 prism 任务 → intent 映射（SPEC 规约）

| prism 任务 | 调用方式 | 理由 |
|---|---|---|
| `translate-bodies` / 懒翻译 | `intent="fast"` | 翻译量大、单条短，优先吞吐 |
| `analyze --triage` | `intent="fast"` | 廉价模型对大量 cluster 打分类 |
| `analyze --expand` | `intent="reasoning"` | 深读 strength≥4，要 CoT |
| `articlize` (YouTube/播客/长文结构化) | `intent="reasoning"` | tl;dr + 章节 + 高亮需要结构化推理 |
| `briefing --save` 综合日报 | `intent="reasoning"` | 跨 cluster 综合 |
| slides horse race (`web/slides.py`) | **显式 `model=`** | 本就是多模型对比，例外场景 |
| 其他一次性 / 短 prompt | `intent="default"` | 不挑 |

**不走 omlx-sdk 的例外**：
- `call_claude()` → `localhost:8100/anthropic` token tracker proxy（Anthropic 云，独立计量通路，见 memory `project_token_tracker.md`）
- ASR（小宇宙转写）→ 当前 omlx-sdk 只包 chat/completions，ASR 暂不在 SDK 范围，保持现状

#### 4.2.4 调用方必须传的观测字段

```python
from omlx_sdk import OmlxClient

async with OmlxClient(caller="prism") as omlx:
    r = await omlx.chat(
        intent="fast",
        messages=[...],
        project="prism",            # → manager 按项目聚合
        session_id=job_run_id,      # → 对齐 job_runs 表，做调用追溯
        tags={"pipeline": "translate", "source_key": "x:karpathy"},
    )
```

- `caller="prism"` 固定
- `session_id` 建议用当次 `job_runs.id` 的字符串 —— manager 上按 session_id 能看到完整一跑消耗
- `tags` 至少包含 `pipeline`（translate / triage / expand / articlize / briefing），便于 manager 做 prism 内部维度拆解

#### 4.2.5 SDK 统一负责的能力

- Retry（HTTP 错误 / timeout）
- Timeout（默认 600s，可按调用覆盖）
- **Bill fire-and-forget**（manager 下线 / 慢不影响业务）
- 不走 shell 代理（`trust_env=False`）

**SDK 不管的、prism 自己管的**：
- `<think>...</think>` 标签剥离（`pipeline/llm.py` 已有逻辑，保留）
- 并发节流（和 Claude Code 抢 omlx 时的 503 规避）—— 这个应该沉到 SDK 后续版本，目前 prism 端自管

#### 4.2.6 消费节奏：被 Freshness Warden 的 drain worker 驱动

- omlx idle 时连续吃积压（translate / articlize / analyze expand 三个队列统一调度）
- omlx busy（Claude Code 占用）时让出
- 不再依赖 "cron 每 3h 跑一批、每批限额"这种被动节奏 —— 现状是产出 > 消费、积压只增不减
- 详见 §6.7 Freshness Warden

---

## 5. Web Surface — 34 路由 + 16 模板

### 5.1 路由分组

**主 UI**（按新定位）
- `GET /` → redirect `/feed` ⚠️ **应改为 redirect `/feed/following`**（登录态）
- `GET /feed/following` — **🌟 实际主路径** — 订阅博主的更新流，每日高频使用
- `GET /feed` + `GET /feed/more`（HTMX 分页）— 次路径，用于"发现新源"
- `POST /feed/action` — save / dismiss / follow_author / mute_topic
- `POST /feed/click` — 点击 beacon（匿名跳过，但异常静默 ⚠️ 见 §11）
- `GET /feed/saved` — 三路合并（feed save + article like + external URL）
- `GET /board` — 仅自己可见，管线健康仪表

**内容页**
- `GET /article/{id}` + `POST /article/{id}/like`
- `GET /channel/{key}` + `POST /channel/{key}/follow|unfollow`
- `GET /creator/{key}` + `POST /creator/item/{id}/like|unlike`

**Briefing & 公开**
- `GET /briefing` — 今日要点
- `GET /showcase` — 公开站点落地
- `GET /decisions/weekly` — 7 天公开决策日志
- `GET /quality` + `POST /quality/ack|scan`

**Pairwise 档案（🧟 只读）**
- `GET /pairwise/liked` — 历史 winners
- `GET /pairwise/sources` — 源分组
- `GET /pairwise/profile` — 偏好向量
- `POST /pairwise/profile/{delete,block}`
- `POST /pairwise/feed` — ⚠️ **名字骗人** — 其实是外部投喂入口，跟 pairwise 无关

**Auth**
- `/login` `/register` `/auth/{login,register,logout,invite}`

**其他**
- `POST /feedback` — 旧的 article like 入口（与 /feed/action 并行）
- `POST /api/export-notion/{cluster_id}`
- `GET /sw.js`
- `GET /translate/{item_id}` — 懒翻译

### 5.2 模板

16 个主模板 + 4 个 HTMX partial（feed_card 分发器 + _tweet/_video/_article 变体）。
**孤儿 partial**（强烈怀疑死文件）：`feed_card_a.html`、`feed_card_c.html`、`feed_card_d.html`、`feed_card_f.html` —— 已被单入口 `feed_card.html` 派发器取代。

### 5.3 Auth / 匿名 gate

单 cookie session `prism_session`（30 天）。公共路由白名单 `_PUBLIC_PATHS` 包括 /login /register /static /article /briefing /creator /translate /showcase /decisions /feed /sw.js。
所有改状态路由都有 `_get_user()` 401 gate（符合 memory `feedback_anon_gate.md`）。

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
