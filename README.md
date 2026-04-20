<p align="center">
  <img src="prism/web/static/icon-192.png" width="80" alt="Prism">
</p>

<h1 align="center">Prism</h1>

<p align="center">
  <b>The best recommendation system is the one you can open, audit, and argue with.</b><br>
  <sub>So I built mine — local LLMs, pairwise preference learning, every autonomous decision logged.</sub>
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

## The problem I was trying to solve

I work in ML. Every day, there are ~200 things on X, Hacker News, arXiv, YouTube, and Xiaoyuzhou (中文 podcasts) that *might* be worth my attention. I tried every reader, every "AI news digest" service, every GPT-wrapper "personalized feed." They all failed in the same way: they decided what I should read, then showed me a headline, and I had no way to argue back. When they were wrong, I couldn't correct them. When they were right, I couldn't tell *why*.

So I built the opposite. Prism reads everything overnight on my Mac Studio, clusters and summarises it with local LLMs, and every morning asks me one question at a time: **"which of these two signals is more interesting?"** My 1-bit answer trains a Bradley–Terry preference model. Over weeks, the system learns what I care about — and more importantly, it **shows me exactly why** any signal made the cut, and logs every autonomous decision it made on my behalf (reweighting a source, flagging an anomaly, proposing a new feed).

This README is also a deliberate artifact. If you're here because you want to see how I think about recommendation, systems design, and self-hosted AI, read on.

## Three bets this project is making

Every design choice in Prism comes from one of these three bets. If the bet is wrong, the project is wrong.

1. **Pairwise > ratings > thumbs-up.** Humans are terrible at absolute ratings but excellent at relative ones. Asking "which of these two?" produces a cleaner, more stable preference signal than a 5-star slider ever will — and it costs the user less cognitive effort. Bradley–Terry ELO turns those pairwise bits directly into a score. ([~40 lines of code](prism/web/ranking.py) do the math.)

2. **Auditability is a feature, not a compliance checkbox.** Every autonomous action the system takes — throttling a low-performing source, flagging a spike, proposing a new feed — is written to a single [`decision_log`](https://prism.simon-ai.net/decisions/weekly) table with a reason. I can replay the system's history. I can ask "why did you add this source on March 28?" and get a real answer. No recommendation system I pay for will tell me that.

3. **Local LLMs crossed a threshold in 2025.** A Mac Studio with 512 GB of unified memory can now run Qwen3-30B or Gemma-3-27B at conversational latency. Throw in Bradley–Terry and some careful prompt design, and you get a personal news system that costs **$0/week in API fees** instead of ~$3,400/year on Claude Sonnet 4.5. Privacy and tweakability come along for the ride.

If you disagree with any of these, I'd genuinely like to hear why — [open an issue](https://github.com/xiaohongsimon/prism/issues).

## What it does

- **Pairwise UI.** Two signals side-by-side. Pick A, B, both, neither, or drop a free-text note. Every interaction trains the model.
- **Local LLM pipeline.** `sync → cluster → analyze` turns raw items (tweets, HN threads, arXiv abstracts, YouTube transcripts, podcast episodes) into summarised signals with a bilingual summary, a "why it matters" line, and a strategic-vs-tactical tier.
- **Self-tuning recall.** Each source has a weight that drifts with the win-rate of the signals it produces. Sources you consistently pick over get crawled more often; dead weight gets throttled. All rule-based, zero LLM overhead in the ranking loop.
- **Podcast → structured article.** Feed a Xiaoyuzhou or YouTube episode in; get a 3-section markdown article with highlighted quotes out. See [`prism/pipeline/articlize.py`](prism/pipeline/articlize.py) for the prompt — and [article 118](https://prism.simon-ai.net/article/118) for an actual output (24 k characters of podcast transcript → 4.5 k-character structured article with quotes).
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

I'm Simon — algorithm team TL at a major tech company, managing ~40 people and a 1,500+ GPU cluster by day. Prism is my nights-and-weekends project, built because I wanted a recommendation system that **respects my attention the way a good editor would** — with a point of view, a memory, and a paper trail.

If you use this, break it, or disagree with any of the choices above — [open an issue](https://github.com/xiaohongsimon/prism/issues) or find me on X [@xiaohongsimon](https://x.com/xiaohongsimon). I care about the feedback.

If you want to contribute, the most valuable things right now:

- Chinese-community source adapters (即刻, 知乎, 微信公众号)
- Cold-start strategy — how do we make the first 50 rounds feel useful?
- A smarter pair-selection policy (current is 70/20/10 rules; active learning could do better)

## 中文简介

**一句话：** AI 替你读 136 个信息源，每次给你两条，你选更喜欢的那个；选择就是训练信号。整套系统跑在你自己的机器上，LLM 零成本，数据不出设备。

这个项目的三个核心判断：

1. **Pairwise 比打分更能学到真实偏好。** 人做绝对打分一塌糊涂，做相对选择很稳。Bradley–Terry ELO 把每个 1-bit 选择直接变成分数。
2. **自动决策必须可审计。** 系统每一次调权、加源、标异常，都写进 `decision_log`，你可以回溯每一步为什么发生。不写日志的推荐系统不可信。
3. **本地 LLM 在 2025 年跨过了临界点。** 一台 Mac Studio 跑 Qwen3-30B 就够了，LLM 月账单从 ~\$270 变成 \$0，隐私和可调性是白送的。

[在线实例](https://prism.simon-ai.net/showcase) · [决策日志](https://prism.simon-ai.net/decisions/weekly) · [一个输出样本（播客→文章）](https://prism.simon-ai.net/article/118)

## License

[MIT](LICENSE) — use it, fork it, make it yours.
