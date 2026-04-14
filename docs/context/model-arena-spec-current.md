# Model Arena: Local Multi-Model Best-of-N Framework

**Date:** 2026-03-26
**Status:** Draft (reviewed)
**Author:** leehom + Claude

## Problem

Single-model LLM calls leave quality on the table. Different models have different strengths: a model that is great at translation may be mediocre at signal analysis. We have 512GB unified memory on Mac Studio, enough to host 3-4 small models simultaneously. We should make them compete on real tasks, track who wins, and route accordingly.

## Core Idea

**竞争上岗**: multiple local models compete on the same task. Opus judges. Better models get more future opportunities via Multi-Armed Bandit routing. Performance data accumulates per task type, building a model capability profile over time.

## Review-Driven Adjustments

This version makes four changes to the original draft so the system is easier to implement and trust:

- `compete()` is defined as an async-first API.
- Storage is split into `runs` and `attempts`, so a single arena round can be audited and replayed.
- Model quality is tracked separately from infra failures like timeout, OOM, or API errors.
- Bandit updates use judge scores, not just winner/loser bits.

## Non-Goals (For Now)

- Full model evaluation or benchmarking system
- Model fine-tuning pipeline
- Grand infrastructure architecture
- Replacing the existing multi-model-orchestrate CC skill

## Design

### Package Structure

```text
model-arena/
├── model_arena/
│   ├── __init__.py       # exports: compete()
│   ├── pool.py           # model registry + parallel inference
│   ├── judge.py          # Opus judge protocol
│   ├── bandit.py         # Thompson Sampling router
│   ├── storage.py        # SQLite persistence
│   ├── types.py          # ArenaResult / ModelOutput dataclasses
│   └── config.py         # load models.yaml
├── models.yaml           # model registry
├── arena.db              # SQLite (auto-created)
├── pyproject.toml
└── tests/
```

### Public API

The primary interface is async:

```python
from model_arena import compete

result = await compete(
    task_type="signal_analysis",
    prompt="分析这条推文的信号价值...",
    n=3,
)

result.run_id             # str: arena run ID
result.best_output        # str: winning output
result.model_id           # str: winning model ID
result.score              # float: winning score
result.comment            # str: Opus commentary
result.outputs            # list[ModelOutput]: successful contestants
result.failures           # list[ModelFailure]: timeout / error / oom
result.degraded           # bool: true when arena could not complete normally
```

If a sync caller is needed later, provide a small wrapper like `compete_sync()` around the async implementation instead of making `compete()` ambiguous.

### Model Registry (`models.yaml`)

```yaml
models:
  qwen-27b:
    endpoint: http://127.0.0.1:8002/v1
    model_name: qwen3.5-27b-distilled
    api_key: omlx-xxx
    timeout_s: 90
    status: active    # active | retired

  glm-9b:
    endpoint: http://127.0.0.1:8002/v1
    model_name: glm-4.7-flash
    api_key: omlx-xxx
    timeout_s: 60
    status: active

  minimax-m2:
    endpoint: http://127.0.0.1:8002/v1
    model_name: minimax-m2.5
    api_key: omlx-xxx
    timeout_s: 60
    status: active
```

Adding a new model means adding an entry. It starts competing immediately with neutral prior probability.

Retiring a model means setting `status: retired`. It stops being selected but history is preserved.

All models use an OpenAI-compatible API, so `pool.py` stays a thin wrapper around `openai.AsyncOpenAI`.

### Parallel Inference (`pool.py`)

1. Router selects `N` models from the active pool for the given `task_type`.
2. Send the same prompt to all selected models concurrently via `asyncio.gather`.
3. Persist one `run` row immediately, then one `attempt` row per selected model.
4. Record success, timeout, OOM, or API error per attempt.
5. Judge only the successful outputs.
6. If fewer than 2 models return successfully, mark the run degraded and skip bandit quality updates.

Key rule: infra failures are persisted, but they do not automatically become quality losses.

### Judge Protocol (`judge.py`)

Opus receives all successful outputs, anonymized as `Output A / B / C`, plus a stable rubric per `task_type`.

Expected structured output:

```json
{
  "winner": "B",
  "scores": {"A": 3.5, "B": 4.2, "C": 2.8},
  "comment": "B captures the core signal most precisely and provides actionable framing.",
  "judge_prompt_version": "signal_analysis_v1"
}
```

Judge requirements:

- Use a versioned prompt template so historical scores remain interpretable.
- Allow `winner: null` if outputs are effectively tied or all poor.
- Score every successful output on the same bounded scale, for example `0.0` to `5.0`.
- Persist the raw judge response for auditability.

### MAB Routing (`bandit.py`)

**Algorithm:** Thompson Sampling with Beta distribution, using score-derived rewards.

Instead of reducing each judged run to winner vs loser, convert each judge score into a normalized reward:

```text
reward = clamp(score / 5.0, 0.0, 1.0)
alpha = 1 + sum(reward for judged attempts of model m on task_type t)
beta = 1 + sum(1 - reward for judged attempts of model m on task_type t)
sample = Beta(alpha, beta).sample()
```

Selection:

```text
For each active model m in task_type t:
    compute sampled value from Beta(alpha_m, beta_m)

Sort by sampled value descending, pick top N.
```

Why this is better:

- A strong second-place model still gets credit.
- Narrow losses are not treated the same as terrible outputs.
- New models still start with a neutral `Beta(1,1)` prior.
- Routing remains per `task_type`, so strengths stay domain-specific.

### Data Storage (`storage.py`)

Use two tables: one per arena run, one per model attempt.

```sql
CREATE TABLE arena_runs (
    id TEXT PRIMARY KEY,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    task_type TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    requested_n INTEGER NOT NULL,
    selected_n INTEGER NOT NULL,
    success_count INTEGER DEFAULT 0,
    judge_model TEXT,
    judge_prompt_version TEXT,
    winner_label TEXT,
    winner_model_id TEXT,
    judge_comment TEXT,
    status TEXT NOT NULL
);

CREATE TABLE arena_attempts (
    run_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    label TEXT,
    status TEXT NOT NULL,        -- success | timeout | oom | api_error
    latency_ms INTEGER,
    output TEXT,
    score REAL,
    reward REAL,
    is_winner BOOLEAN DEFAULT FALSE,
    error_type TEXT,
    error_message TEXT,
    PRIMARY KEY (run_id, model_id),
    FOREIGN KEY (run_id) REFERENCES arena_runs(id)
);

CREATE INDEX idx_attempts_model_task
ON arena_attempts(model_id, run_id);
```

Why not a single `competitions` table:

- One arena round becomes impossible to reconstruct cleanly.
- Judge metadata gets duplicated across rows.
- Infra failures and judged outputs get mixed into one overloaded record shape.

### Failure Semantics

Arena should distinguish among:

- `success`: model returned output and was judged
- `timeout`: model did not respond before deadline
- `oom`: local runtime ran out of memory
- `api_error`: transport or server-side failure

Only `success` rows contribute to the quality bandit. Failure rates can be analyzed separately and later folded into routing if reliability becomes important.

### First Integration: Prism

Prism's `pipeline/llm.py` currently calls a single model. Change to:

```python
# Before
analysis = await call_llm(prompt)

# After
from model_arena import compete

result = await compete(
    task_type="signal_analysis",
    prompt=prompt,
    n=3,
)
analysis = result.best_output
```

Prism runs about 4 times per day. At 3 models per run, that is about 12 local model calls plus 4 Opus judge calls per day. Cost is likely modest, but latency should still be measured in the first rollout.

### Future Integration Points

- YouTube transcript translation: `compete(task_type="translation", prompt=...)`
- WeChat analysis: `compete(task_type="chat_analysis", prompt=...)`
- Any new task: choose a `task_type` string and call `compete()`

Each new `task_type` starts fresh, so all models explore from zero in that domain.

## Operational Notes

- All local models are served via omlx (MLX) on the same Mac Studio.
- omlx handles model loading and switching; arena only calls the API.
- `models.yaml` is the source of truth for the active model pool.
- `arena.db` is the source of truth for run history.
- If the judge call fails, persist the run with `status="judge_error"` and do not update the bandit.
- Log latency for both generation and judge calls from day one.

## What's Explicitly Deferred

- Structured multi-dimensional scoring beyond a scalar score plus free text
- Automated model discovery or download from Hugging Face
- Dashboard or visualization of model performance
- Training on the accumulated corpus
- Reliability-aware routing that blends quality and uptime into one policy
- Integration with Dynasty or the CC skill system
