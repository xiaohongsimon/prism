<p align="center">
  <img src="prism/web/static/icon-192.png" width="80" alt="Prism">
</p>

<h1 align="center">Prism</h1>

<p align="center">
  <b>Information triage at $0/token — because local compute inverts the economics that make good recommendation impossible on cloud.</b><br>
  <sub>136 sources, 9M tokens/week, $0 API bill. The system can afford to run 100 LLM passes to surface the 1 signal worth my time. A personal preference layer (pairwise + Bradley–Terry) handles the last-mile filtering.</sub>
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

I work in ML. Every day, ~200 things on X, Hacker News, arXiv, YouTube, and Xiaoyuzhou (中文 podcasts) might matter. I tried every AI news app, every "personalized digest," every GPT-wrapper feed. They all failed the same way: **not enough coverage, no auditability, no way to correct them.**

Once I looked at the unit economics, the reason became obvious.

> **API economics:** every token costs. Every LLM call has to be high-probability useful, or you go broke. So cloud-based readers cover 1–2 sources, do one cheap summarisation pass, and ship. They physically cannot afford to be thorough.
>
> **Local-compute economics:** marginal cost ≈ 0. Once the hardware is paid for, running 100 LLM passes to surface the 1 thing worth my time is fine. You can afford to translate everything, re-score historical items when a better prompt lands, A/B ensemble prompts overnight, and cluster aggressively.

Prism is what happens when you rebuild information triage under the second set of economics. Multi-channel ingestion + generous LLM processing does the wide-net part. A personal preference model (pairwise + Bradley–Terry) does the last-mile filtering. Every autonomous decision gets logged so the system is actually debuggable.

Net effect on me: **faster access to signal, faster technical-taste growth.** That's the whole point.

## Three bets this project is making

Every design choice in Prism comes from one of these three bets. If the bet is wrong, the project is wrong.

1. **Local compute inverts the economics of recommendation.** On cloud, every token has to be high-probability useful. On a Mac Studio with 512 GB unified memory, "100 scans for 1 hit" is the business model. This unlocks modes cloud APIs can't afford: reprocess yesterday's raw items when a better prompt ships, run ensemble prompts for high-value signals, translate every 中文 podcast by default, re-rank continuously. Same workload would cost **~$3,400/year on Claude Sonnet 4.5** (see Benchmark); local runs for **$0**. Privacy and tweakability come along for the ride.

2. **Multi-channel ingestion is non-negotiable.** Single-feed filters (HN-only, X-only) will always miss the thing that actually mattered. The only way to beat overload is to cast a wide net — 136 sources across EN + 中文, text + video + audio — and rely on the LLM pipeline to compress brutally. That scale is only affordable under bet #1.

3. **Personal preference is the last-mile filter — and must be auditable.** After the wide-net LLM pipeline, the system still doesn't know what *you* care about. Pairwise comparisons ("which of these two?") turn humans' good relative-judgment into 1-bit training signals; Bradley–Terry ELO turns those bits into scores in [~40 lines of code](prism/web/ranking.py). Every autonomous action (reweight a source, flag an anomaly, propose a new feed) is written to a single [`decision_log`](https://prism.simon-ai.net/decisions/weekly) with a reason. I can ask "why did you add this source on March 28?" and get a real answer. No cloud recommender will tell me that.

If you disagree with any of these, I'd genuinely like to hear why — [open an issue](https://github.com/xiaohongsimon/prism/issues).

## What it does

- **Wide-net ingestion.** 136 sources across X, Hacker News, arXiv, YouTube, GitHub Trending/Releases, Reddit, Product Hunt, Xiaoyuzhou (中文 podcasts) and more. All configured in one [`config/sources.yaml`](config/sources.yaml).
- **Local LLM pipeline, used generously.** `sync → cluster → analyze` turns raw items (tweets, HN threads, arXiv abstracts, YouTube transcripts, podcast episodes) into summarised signals with a bilingual summary, a "why it matters" line, and a strategic-vs-tactical tier. Because every token is free, the pipeline can re-process, re-score, and re-translate at will.
- **Podcast → structured article.** Feed a Xiaoyuzhou or YouTube episode in; get a 3-section markdown article with highlighted quotes out. See [`prism/pipeline/articlize.py`](prism/pipeline/articlize.py) for the prompt — and [article 118](https://prism.simon-ai.net/article/118) for an actual output (24 k characters of podcast transcript → 4.5 k-character structured article with quotes).
- **Pairwise preference UI.** Two signals side by side. Pick A, B, both, neither, or drop a free-text note. Every interaction feeds the Bradley–Terry model. This is the only place where *your* taste enters the system.
- **Self-tuning recall.** Each source has a weight that drifts with the win-rate of the signals it produces. Sources you consistently prefer get crawled more often; dead weight gets throttled. All rule-based, zero LLM overhead in the ranking loop.
- **External-feed injection.** Paste a URL from *any* other channel (WeChat, Slack, a friend) — it's treated as a 3× positive preference signal, stronger than any in-feed action.
- **Decision Log.** Every autonomous decision is logged with a reason. Browsable at [`/decisions/weekly`](https://prism.simon-ai.net/decisions/weekly).

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

Runs on a Mac Studio via [mlx](https://github.com/ml-explore/mlx). Swap models in `.env` — any OpenAI-compatible endpoint works. I've tested Qwen3-30B, Gemma-3-27B, and qwen-plus (cloud).

## Architecture

```
sources.yaml  ◄── single source of truth (136 adapters)
     │
     ▼
[sync]        fetch raw items
     │
     ▼
[cluster]     dedup + semantic grouping
     │
     ▼
[analyze]     local LLM → summary · why_it_matters · strength · tags
     │
     ▼
[pairwise]    Bradley–Terry + your 1-bit choices → preference model
     │
     ▼
[decide]      adjust source weights · propose new sources · flag anomalies
     │        (everything → decision_log)
     ▼
signals → /feed · /briefing · /showcase · Notion
```

The two-layer loop:

| Layer   | Responsibility         | Implementation |
|---------|------------------------|----------------|
| Recall  | *Where* to look        | 17 source adapters + dynamic per-source weights (Phase 1: rules · Phase 2: LLM-suggested new sources · Phase 3: auto-trial & promote) |
| Ranking | *How* to order         | Bradley–Terry ELO + multi-dim weights (topic/source/author) + exploration (70% high-confidence · 20% double-new · 10% random) |

Feedback signal weights:

| Signal type          | Weight | Interpretation      |
|----------------------|--------|---------------------|
| External feed (URL)  | **3.0**| Strongest positive  |
| Save / star          | 2.0    | Explicit like       |
| Pairwise pick        | 1.0    | Standard            |
| "Both fine"          | 0.3    | Weak positive       |
| "Neither"            | −0.5   | Negative            |

### The design was pressure-tested by LLMs

The current two-layer architecture wasn't my first draft. Original design had a three-layer system with a separate "Meta" optimisation loop. I asked six different LLMs (Claude Opus, GPT-5, Gemini 2.5, DeepSeek, Qwen-Max, local Qwen3) to critique both options. Five of six argued the Meta layer was premature. I agreed, collapsed it into a background task on the ranking layer, and shipped the simpler version. The full transcript is in [`docs/reviews/synthesis/2026-04-01-prism-v2-debate.md`](docs/reviews/synthesis/2026-04-01-prism-v2-debate.md) — it's a snapshot of what multi-model design-critique looks like when used seriously.

## Design decisions worth arguing about

Every choice below has a real trade-off. If you think I picked wrong, I'd like to know.

- **SQLite, not Postgres.** This is a single-user system. A 4 GB SQLite file on disk is simpler, faster, and backup is `cp`. Not planning to change.
- **No vector DB — yet.** Embedding similarity is only used for cold-start signal scoring. For 3 k signals/week it doesn't earn its complexity. Will revisit if the corpus grows 10×.
- **Jinja2 + HTMX, no build step.** The whole frontend is server-rendered, zero npm, one `style.css`. Total frontend code: ~1.2 k lines of CSS + Jinja. Adding React would double the complexity and halve the iteration speed.
- **Bradley–Terry, not a neural re-ranker.** Simpler model, easier to reason about, no training loop. The multi-dim weight vector already captures topic/source/author preference. A neural re-ranker would be cool but I can't justify the opacity yet.
- **No multi-user.** Single-user is a *feature*. Personalisation only works when the feedback signal is yours. Sharing preferences across users kills the premise.
- **YAML as config, not DB.** `config/sources.yaml` is the source of truth. The DB tracks *runtime state* (last-synced timestamps, dynamic weights). This split lets me version-control my feed config with git.

## What this does NOT do (yet)

I'd rather be honest about limits than oversell.

- **Cold start is rough.** First 100–200 pairwise choices the model is basically random. Better onboarding is an open problem.
- **No real LLM-driven source discovery yet.** Phase 2 and 3 (auto-propose, auto-trial new sources) are on the roadmap but not shipped.
- **Chinese-language sources are thin.** X / HN / arXiv / YouTube are covered well. 即刻 / 知乎 / 微信公众号 don't have adapters yet. Contributions very welcome.
- **Single-device.** No sync across machines. The SQLite file is my state.
- **Entity system is paused.** An earlier effort to build a unified entity graph across signals (people, companies, papers) was over-engineered and is shelved. See [`docs/specs/2026-03-29-prism-v2-entity-system.md`](docs/specs/2026-03-29-prism-v2-entity-system.md) for why.
- **No mobile app.** The web UI is responsive but not a PWA-grade experience.

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
prism sync                   # fetch from all sources
prism cluster                # dedup + group
prism analyze --incremental  # LLM pass
prism serve --port 8080      # web UI at localhost:8080
```

First-run tip: the first few pairwise comparisons are noisy — keep picking for ~100 rounds before judging the quality.

For production-style unattended running, see [`prism/scheduling/`](prism/scheduling/) (macOS launchd plists).

## Project layout

```
prism/
├── cli.py                 # CLI entry
├── db.py                  # SQLite schema (init_db is the whole thing)
├── pipeline/              # sync → cluster → analyze → articlize
├── sources/               # 17 source adapters; base.py defines the protocol
├── web/
│   ├── routes.py          # FastAPI
│   ├── pairwise.py        # pair selection + ELO update  (<200 lines)
│   ├── ranking.py         # ranking + multi-dim weights   (<400 lines)
│   ├── slides.py          # multi-model horse race + judge
│   └── templates/         # Jinja2 + HTMX — zero build step
├── output/                # briefing + Notion publish
└── scheduling/            # launchd plists for 24/7 operation

docs/
├── specs/                 # design specs I wrote before implementing
└── reviews/synthesis/     # multi-model design critiques
```

## Who built this

I'm Simon — algorithm team TL at a major tech company, managing ~40 people and a 1,500+ GPU cluster by day. My job requires staying on top of what's shipping across AI research, infra, and open-source, and the existing tooling was drowning me. Prism is a nights-and-weekends answer to one concrete question: *how fast can a well-designed personal system grow my technical taste, if I stop paying per-token and start paying per-kWh?*

If you use this, break it, or disagree with any of the bets above — [open an issue](https://github.com/xiaohongsimon/prism/issues) or find me on X [@xiaohongsimon](https://x.com/xiaohongsimon). I care about the feedback.

If you want to contribute, the most valuable things right now:

- Chinese-community source adapters (即刻, 知乎, 微信公众号)
- Cold-start strategy — how do we make the first 50 rounds feel useful?
- A smarter pair-selection policy (current is 70/20/10 rules; active learning could do better)

## 中文简介

**一句话：** 用本地算力的经济学解决信息过载——因为边际成本 ≈ 0，所以可以 136 源广撒网、LLM 任意加工、翻译、重打分。再用一个可审计的个人偏好模型做最后一公里筛选。结果是：我获取信息、提高技术品味的速度大幅上升。

三个核心判断：

1. **本地算力倒置了推荐的经济学。** 云 API 上，每个 token 都得高概率有用；本地上，"扫 100 次命中 1 次"反而是常态。这个差别决定了你能覆盖多少源、能做多少次 re-processing、能不能给所有中文播客都翻译一遍。同样的工作量在 Claude Sonnet 4.5 上大约 **\$3,400/年**，本地 **\$0**。
2. **多渠道摄入是必须的。** 单源过滤（只看 HN、只看 X）必然错过真正重要的东西。只有广撒网——136 个源横跨中英文、视频、播客、文本——再靠 LLM 压缩，才能赢过信息过载。这个规模只有本地经济学扛得住。
3. **个人偏好是最后一公里的筛子。** Pairwise 对比 + Bradley–Terry ELO 把人做相对判断的优势变成训练信号；每一次自动决策（调源权重、加源、标异常）都写进 `decision_log`。不会被黑盒决策。

[在线实例](https://prism.simon-ai.net/showcase) · [决策日志](https://prism.simon-ai.net/decisions/weekly) · [一个输出样本（播客→文章）](https://prism.simon-ai.net/article/118)

## License

[MIT](LICENSE) — use it, fork it, make it yours.
