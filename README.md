<p align="center">
  <img src="prism/web/static/icon-192.png" width="80" alt="Prism">
</p>

<h1 align="center">Prism</h1>

<p align="center">
  <b>A multi-channel, multi-lingual, multi-modal subscription reader — because local compute makes it affordable to translate, transcribe, and structure <i>everything</i> you follow.</b><br>
  <sub>136 sources, 9M tokens/week, $0 API bill. Cast a wide net; let local LLMs do the heavy lifting; skim what matters in your language, in minutes.</sub>
</p>

<p align="center">
  <a href="https://prism.simon-ai.net/showcase"><b>Live instance →</b></a> ·
  <a href="https://prism.simon-ai.net/article/118">Example output</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#中文简介">中文</a>
</p>

<p align="center">
  <sub>136 sources · 26+ days running non-stop · 9M tokens/week · $0 LLM bill ·
  <a href="https://prism.simon-ai.net/decisions/weekly">130 autonomous decisions</a> in the last 30 days</sub>
</p>

---

## Why cloud-economics can't solve information overload

I work in ML. Every day, ~200 things on X, Hacker News, arXiv, YouTube, and Xiaoyuzhou (中文 podcasts) might matter. I tried every AI news app, every "personalized digest," every GPT-wrapper feed. They all failed the same way: **not enough coverage, no translation, no structured preview, no way to scan fast.**

Once I looked at the unit economics, the reason became obvious.

> **API economics:** every token costs. Every LLM call has to be high-probability useful, or you go broke. So cloud-based readers cover 1–2 sources, do one cheap summarisation pass, and ship. They physically cannot afford to translate every post, transcribe every podcast, or structure every long article.
>
> **Local-compute economics:** marginal cost ≈ 0. Once the hardware is paid for, running 100 LLM passes on a single Mac Studio is fine. You can afford to translate everything, transcribe every 中文 podcast, structure every YouTube video into tl;dr + chapters, and re-process yesterday's items when a better prompt lands.

Prism is what happens when you rebuild a subscription reader under the second set of economics. You follow sources across text/video/audio in multiple languages; the local LLMs translate, transcribe, and structure in the background; the UI is a clean "what did the people I follow say today" stream that you can skim in minutes.

Net effect on me: **faster access to signal, faster technical-taste growth.** That's the whole point.

## Three bets this project is making

Every design choice comes from one of these three bets. If the bet is wrong, the project is wrong.

1. **Local compute inverts the economics of information processing.** On cloud, every token has to be high-probability useful. On a Mac Studio with 512 GB unified memory, "translate everything, transcribe everything, structure everything, re-process on demand" is the business model. Same workload would cost **~$3,400/year on Claude Sonnet 4.5** (see Benchmark); local runs for **$0**. Privacy and tweakability come along for the ride.

2. **Multi-channel + multi-modal + multi-lingual ingestion is non-negotiable.** Single-feed filters (HN-only, X-only) will always miss the thing that actually mattered. Single-modality readers (text-only) can't touch podcasts or YouTube. Single-language tools (EN-only or 中文-only) miss half the world. The only way to beat overload is to cast a wide net — 136 sources across EN + 中文, text + video + audio — and rely on the LLM pipeline to compress brutally. That scale is only affordable under bet #1.

3. **The shareable layer and the personal layer are different products.** Translation + transcription + structured summaries are *public goods* — once the Mac Studio has paid the inference cost for me, the marginal cost of serving the same output to other readers is ~0, and the result is equally useful to everyone. Personal preference / recommendation is the opposite — it only fits one person, can't be shared, and is intentionally kept as a **pluggable seam** rather than hard-coded into the public pipeline. Open-source users get a clean structuring reader out of the box; private forks can plug in their own preference layer on top.

If you disagree with any of these, I'd genuinely like to hear why — [open an issue](https://github.com/xiaohongsimon/prism/issues) or start a [Discussion](https://github.com/xiaohongsimon/prism/discussions).

## What it does

- **Wide-net ingestion.** 136 sources across X (personal + For You timeline), Hacker News, arXiv, YouTube (RSS + subscriptions), GitHub (Trending / Releases / received_events), Reddit, Product Hunt, Xiaoyuzhou (中文 podcasts) and more. All configured in one [`config/sources.yaml`](config/sources.yaml).
- **Translate everything.** 中文 ⇄ EN translation runs on the local LLMs for every non-native post in your feed. No more scrolling past things because the language tax is too high.
- **Structure everything.** `sync → cluster → analyze` turns raw items into summarised signals with a bilingual summary, a "why it matters" line, and a strategic-vs-tactical tier. Because every token is free, the pipeline can re-process, re-score, and re-translate at will.
- **Podcast / video → structured article.** Feed a Xiaoyuzhou or YouTube episode in; get a 3-section markdown article with highlighted quotes out. See [`prism/pipeline/articlize.py`](prism/pipeline/articlize.py) for the prompt — and [article 118](https://prism.simon-ai.net/article/118) for an actual output (24 k characters of podcast transcript → 4.5 k-character structured article with quotes).
- **Subscription-first UI.** The daily path is `/feed/following` — the people and channels you follow, grouped by source, sorted by recency, with "new since last visit" highlighting. Scan in minutes, click through to originals for the few you care about.
- **External-feed injection.** Paste a URL from *any* other channel (WeChat, Slack, a friend) — it's ingested, translated, structured, and joins your feed.
- **Auditable autonomy.** Every autonomous decision (a source added, a source auto-disabled, an anomaly flagged) is logged with a reason to [`/decisions/weekly`](https://prism.simon-ai.net/decisions/weekly).

> **On the preference layer.** Earlier iterations of this project included a pairwise + Bradley–Terry recommendation layer as the "last-mile filter." As the system matured the daily usage converged on simple subscription scanning — the translation + structuring value proved to stand on its own. Recommendation is now a **deferred, pluggable** layer (see [`docs/SPEC.md`](docs/SPEC.md) §0). The public repo ships a clean `IdentityReRanker` that sorts by recency; a future personalization layer will live in a pluggable `prism/personalize/` module so forks can plug in their own taste.

## Benchmark

Numbers from my live instance (auto-updated at [prism.simon-ai.net/showcase](https://prism.simon-ai.net/showcase)):

| Metric                             | Value                      |
|------------------------------------|----------------------------|
| Active sources                     | **136**                    |
| Raw items ingested / week          | **~3,800**                 |
| Signals distilled / week           | **~3,100**                 |
| High-value signals (strategic)     | **~97%** of distilled      |
| Autonomous decisions / 30d         | **130+**                   |
| Continuous uptime                  | **26+ days**               |
| Tokens processed / week            | **~9M**                    |
| **LLM cost / week (local)**        | **$0**                     |
| Same workload on Claude Sonnet 4.5 | **~$65 / week (~$3,400 / year)** |

Runs on a Mac Studio via [mlx](https://github.com/ml-explore/mlx). Local inference is accessed through [omlx-sdk](https://github.com/xiaohongsimon/omlx-manager/tree/main/sdk) with a small set of capability intents (`fast` / `reasoning` / `coding`) — the pipeline declares intent, the SDK picks the model. Swap models in one config file; any OpenAI-compatible endpoint works.

## Architecture

```
sources.yaml  ◄── single source of truth (136 adapters)
     │
     ▼
[sync]            fetch raw items (adapters fan-in)
     │
     ▼
[cluster]         dedup + semantic grouping
     │
     ▼
[translate]       local LLM (intent=fast) — every non-native post → your language
     │
     ▼
[analyze]         local LLM (intent=fast triage → intent=reasoning expand)
     │            → summary · why_it_matters · strength · tags
     ▼
[articlize]       podcast/video transcripts → structured article
     │            (tl;dr + chapters + highlights)
     ▼
[personalize]     ← pluggable seam (default: IdentityReRanker = by recency)
     │
     ▼
/feed/following · /article · /briefing · /showcase · Notion
     │
     ▼
[Freshness Warden]  observes subscription health + drains LLM backlog when idle
                    (decision_log captures every autonomous action)
```

The pipeline is a clean fan-in → normalise → process → fan-out funnel:

| Layer          | Responsibility                                              |
|----------------|-------------------------------------------------------------|
| **Fan-in**     | 20+ adapters; each implements `SourceAdapter.sync()`        |
| **Normalise**  | Everything lands in `raw_items` with `{url, title, body, author, published_at}` — downstream treats all channels the same |
| **Process**    | Translate → cluster → two-stage analyze (cheap triage + reasoning expand) → articlize for video/podcast |
| **Fan-out**    | `/feed/following` (main), `/article/{id}`, `/briefing`, Notion publish |
| **Personalize**| Pluggable seam; default = recency. Private forks override.  |
| **Observe**    | Freshness Warden tracks source health + consumes LLM backlog during idle time |

### The design was pressure-tested by LLMs

The current architecture wasn't my first draft. Earlier versions had a three-layer optimisation loop and a heavy pairwise-comparison UI as the primary interaction. I asked six different LLMs (Claude Opus, GPT-5, Gemini 2.5, DeepSeek, Qwen-Max, local Qwen3) to critique the design. The critique converged on "the structuring pipeline is the actual value; the recommendation layer is over-engineered for a single user" — which, after six months of usage data, turned out to be correct. The full transcript is in [`docs/reviews/synthesis/2026-04-01-prism-v2-debate.md`](docs/reviews/synthesis/2026-04-01-prism-v2-debate.md).

The reverse-engineered spec that captures the current truth is [`docs/SPEC.md`](docs/SPEC.md).

## Design decisions worth arguing about

Every choice below has a real trade-off. If you think I picked wrong, I'd like to know.

- **SQLite, not Postgres.** This is a single-user system. A 4 GB SQLite file on disk is simpler, faster, and backup is `cp`. Not planning to change.
- **No vector DB — yet.** Embedding similarity is only used for cold-start signal scoring. For 3 k signals/week it doesn't earn its complexity. Will revisit if the corpus grows 10×.
- **Jinja2 + HTMX, no build step.** The whole frontend is server-rendered, zero npm, one `style.css`. Total frontend code: ~1.2 k lines of CSS + Jinja. Adding React would double the complexity and halve the iteration speed.
- **Preference layer is pluggable, not built-in.** The public repo ships an identity re-ranker (sort by recency). Recommendation is genuinely a single-user problem — burning it into the shared code would only produce something that fits me. A private fork can implement `ReRanker` and plug it into `prism/personalize/`. See SPEC §0 for the reasoning.
- **Intent-based LLM calls, not model-name hardcoding.** The pipeline calls `omlx.chat(intent="reasoning", ...)` rather than naming a specific model. Swapping models = editing one config; zero pipeline diffs.
- **No multi-user.** Single-user is a *feature* for the preference layer, and a conscious choice for the project scope. The public (structuring) layer is still usable by anyone who runs their own copy.
- **YAML as source config, not DB.** `config/sources.yaml` is the source of truth. The DB tracks *runtime state* (last-synced timestamps, health metrics). This split lets me version-control my feed config with git.

## What this does NOT do (yet)

I'd rather be honest about limits than oversell.

- **Chinese-community source adapters are thin.** X / HN / arXiv / YouTube are covered well. 即刻 / 知乎 / 微信公众号 don't have adapters yet. Contributions very welcome.
- **No real LLM-driven source discovery yet.** The system can flag low-value sources; it can't yet propose new ones automatically.
- **Structured article coverage is YouTube-heavy.** Podcast and long-form text structuring exist but run less frequently. Expanding articlize to podcasts / long articles on the hourly cadence is on the near-term roadmap.
- **Single-device.** No sync across machines. The SQLite file is my state.
- **Entity system is paused.** An earlier effort to build a unified entity graph across signals was over-engineered and shelved.
- **No mobile app.** The web UI is responsive but not a PWA-grade experience.
- **HN comments aren't ingested.** The submission itself is; the discussion (often where the value is) isn't yet.

## Quick start

**Prereqs:** Python 3.10+, an OpenAI-compatible LLM endpoint (local Ollama / mlx / vLLM, or cloud).

```bash
git clone https://github.com/xiaohongsimon/prism.git
cd prism
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then edit
```

Minimum `.env`:

```env
PRISM_LLM_BASE_URL=http://localhost:8002/v1
PRISM_LLM_MODEL=qwen3-30b-a3b-instruct
PRISM_LLM_CHEAP_MODEL=qwen3-4b-instruct
PRISM_ADMIN_PASSWORD=whatever
```

Run:

```bash
prism sync                           # fetch from all sources
prism cluster                        # dedup + group
prism analyze --triage               # cheap-model pass over every new cluster
prism analyze --expand --limit 30    # reasoning-model deep read on high-value ones
prism serve --port 8080              # web UI at localhost:8080
```

The main daily path is `http://localhost:8080/feed/following` once you've added sources you want to follow.

For production-style unattended running, see [`prism/scheduling/`](prism/scheduling/) (macOS launchd plists).

## Project layout

```
prism/
├── cli.py                 # CLI entry
├── db.py                  # SQLite schema (init_db is the whole thing)
├── pipeline/              # sync → cluster → translate → analyze → articlize
├── sources/               # 20+ source adapters; base.py defines the protocol
├── web/
│   ├── routes.py          # FastAPI
│   ├── feed.py            # feed assembly (/feed, /feed/following)
│   ├── board.py           # ops dashboard (self-only)
│   └── templates/         # Jinja2 + HTMX — zero build step
├── output/                # briefing + Notion publish
└── scheduling/            # launchd plists for 24/7 operation

docs/
├── SPEC.md                # ← current truth; reverse-engineered from code
├── RUNTIME.md             # what's alive right now, on-call playbook
├── specs/                 # historical design specs (multiple generations; archive)
└── reviews/synthesis/     # multi-model design critiques
```

## Who built this

I'm Simon — algorithm team TL at a major tech company, managing ~40 people and a 1,500+ GPU cluster by day. My job requires staying on top of what's shipping across AI research, infra, and open-source, and the existing tooling was drowning me. Prism is a nights-and-weekends answer to one concrete question: *how fast can a well-designed personal system grow my technical taste, if I stop paying per-token and start paying per-kWh?*

If you use this, break it, or disagree with any of the bets above — [open an issue](https://github.com/xiaohongsimon/prism/issues), start a [Discussion](https://github.com/xiaohongsimon/prism/discussions), or find me on X [@xiaohongsimon](https://x.com/xiaohongsimon). I care about the feedback.

If you want to contribute, the most valuable things right now:

- Chinese-community source adapters (即刻, 知乎, 微信公众号)
- HN comment ingestion (submissions only today)
- Podcast / long-form article structuring on the hourly cadence (currently YouTube-first)
- Better "subscription health" UX (the Freshness Warden layer described in SPEC §6.7)

## 中文简介

**一句话：** 一个多渠道、多语种、多模态的个人订阅阅读器——因为本地算力边际成本 ≈ 0，所以可以把 136 个源（含中英文、文本+视频+播客）的每条内容都翻译、转写、结构化，让我在几分钟内扫完每天"我关注的人说了啥"。

三个核心判断：

1. **本地算力倒置了信息处理的经济学。** 云 API 上，每个 token 都得高概率有用；本地上，"每条都翻、每段都转写、每个视频都结构化"才是常态。同样工作量在 Claude Sonnet 4.5 上大约 **\$3,400/年**，本地 **\$0**。
2. **多渠道 + 多模态 + 多语种是必须的。** 只看 HN 或只看 X 必然错过真正重要的东西；只看文字漏掉播客和视频；只看一种语言漏掉半个世界。只有广撒网（136 源）再靠 LLM 压缩，才能赢过信息过载。
3. **可分享的公共层 vs 不可分享的个人层，是两件不同的事。** 翻译、转写、结构化是公共产物——Mac Studio 已经为我付过的推理，再给其他读者消费几乎零成本。个性化推荐刚好相反——只拟合一个人，不能分享。所以公共 repo 只发一个干净的结构化阅读器（按时间倒排），未来的偏好层留作可插拔模块（`prism/personalize/`），任何 fork 都能装自己的口味。

[在线实例](https://prism.simon-ai.net/showcase) · [决策日志](https://prism.simon-ai.net/decisions/weekly) · [一个输出样本（播客→文章）](https://prism.simon-ai.net/article/118) · [完整 spec](docs/SPEC.md)

## License

[MIT](LICENSE) — use it, fork it, make it yours.
