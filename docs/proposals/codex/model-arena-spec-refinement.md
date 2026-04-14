# Proposal: Model Arena Spec Refinement

## Summary of Recommended Changes

- Keep the current spec's small scope. I agree with keeping this as a lightweight internal package, using SQLite, a file-backed model registry, a two-table `runs` + `attempts` storage model, and a strict separation between infra failures and judged quality.
- Make `compete_sync()` a first-class API in v1, not a later wrapper. Prism's current analysis path is synchronous and uses `ThreadPoolExecutor`, so async-only as the primary integration surface adds unnecessary friction.
- Narrow the first Prism rollout to incremental per-cluster analysis. Do not make daily batch analysis the first arena task; its output shape is larger, harder to judge, and more likely to fail in confusing ways.
- Replace the current free-form `task_type + prompt` contract with a registered `TaskSpec` that defines contestant prompt version, judge rubric version, output parser/schema, fallback policy, and default `n`.
- Add attempt states for task-contract failures and eligibility filtering: at minimum `invalid_output` and `skipped_context_limit`. Invalid JSON for a JSON task is not an infra failure; it is a task failure and should be tracked explicitly.
- Change the bandit update rule. I agree with moving beyond pure winner/loser bits, but raw `score / 5.0` updates into a Beta posterior are not mathematically clean and will drift with judge calibration. Use pairwise win/tie/loss updates derived from judge ranking plus an `all_poor` gate.
- Harden judge protocol handling: schema validation, one repair retry on malformed JSON, explicit `tie` / `all_poor` semantics, randomized output labels, and canonicalized contestant outputs before judging.
- Add explicit degraded behavior and fallback policy. `ArenaResult` needs nullable winner fields, a `degraded_reason`, and a `fallback_used` flag so Prism can continue when the judge fails or only one usable output survives.
- Add the minimal operational pieces needed for day one: per-run metadata, config validation, registry reload-on-read, SQLite `WAL` + `busy_timeout`, one DB connection per arena run, and a process-level concurrency limit so nested Prism threads do not oversubscribe local model serving.
- Add a minimal observability surface, not a dashboard: `list_models()`, `get_stats()`, `validate_config()`, and a small CLI if desired.

## Detailed Rationale

### 1. Keep the current storage split and lightweight scope

I agree with the current spec on the core shape:

- `arena_runs` + `arena_attempts` is the right storage split.
- SQLite is the right database for 3-4 models and roughly 4 runs/day.
- Infra failures should stay separate from judged quality.
- A file-backed registry is enough; v1 does not need DB-backed model CRUD.

Those choices are implementable immediately and match Prism's scale. The main issue is not over-architecture; it is that a few missing contracts will make the first integration brittle.

### 2. The public API is too thin for Prism as it exists today

The current `compete(task_type, prompt, n)` signature is not enough for Prism's real call sites.

Prism does not call a generic text model interface. Its current analysis path passes:

- a system prompt
- a user prompt
- an expected JSON output shape
- a sync call path from threaded workers

That means arena needs to own more of the contract, otherwise every caller will reinvent parsing, fallback, and validation around it.

The practical fix is:

- keep async support
- add `compete_sync()` in v1
- define `TaskSpec` so `task_type` is registered rather than just a free string

That makes the arena API fit both Prism's current pipeline and future async callers without forcing event-loop plumbing into `prism/pipeline/analyze.py`.

I would also not make "add models programmatically" a v1 goal. I agree with the current spec that `models.yaml` is enough. The missing piece is config validation and reload behavior, not runtime CRUD.

### 3. First rollout should be incremental analysis only

The current spec's Prism integration sketch is too optimistic. Prism currently has two different analysis shapes:

- incremental per-cluster analysis with a relatively compact JSON schema
- daily batch analysis with multi-cluster output, cross-links, and narrative text

Incremental analysis is the right first task for arena because:

- the contestant output is smaller
- JSON validation is straightforward
- judge comparison is easier
- failures are easier to reason about

Daily batch analysis should stay single-model initially. Judging entire daily batch outputs is a much harder protocol problem and will blur whether failures come from model quality, prompt length, or judge confusion.

### 4. Nested concurrency is the biggest real integration risk

This is the largest practical gap in the current spec.

Prism already parallelizes cluster analysis with `ThreadPoolExecutor`. If each worker starts an arena round with `n=3`, the concurrency multiplies:

- outer concurrency across clusters
- inner concurrency across contestant models
- plus one judge call per completed round

With 4 worker threads and 3 contestants, Prism can quickly turn one "4 workers" setting into roughly 12 local model calls plus judge traffic. That may be fine on paper, but it is exactly the kind of thing that fails on first rollout because the serving stack, memory residency, or local scheduling is slightly less ideal than assumed.

So the spec should explicitly require one of these:

- a process-level arena semaphore, for example `max_concurrent_rounds = 1 or 2`
- or reducing Prism's outer worker count when arena is enabled

For first rollout, I would do both:

- arena internally caps concurrent rounds
- Prism uses a lower `max_workers` when the arena path is active

That is much safer than assuming the existing threaded pipeline can simply wrap arena calls unchanged.

### 5. Output validation must be part of the arena contract

The current spec treats attempts mostly as transport outcomes: `success`, `timeout`, `oom`, `api_error`.

That is not enough for Prism. A contestant can return HTTP 200 and still be unusable because:

- JSON is malformed
- required fields are missing
- fields have the wrong types
- output is truncated or clearly incomplete

For a task that requires structured JSON, that is not an infra failure. It is a task failure. If arena does not model it explicitly, the routing data becomes misleading and Prism has to bolt validation on afterwards.

The lightweight answer is to let each `TaskSpec` optionally define:

- a parser
- an output schema validator
- a canonicalizer used before judging

Then attempts can be classified as:

- `success`
- `invalid_output`
- `timeout`
- `oom`
- `api_error`
- `skipped_context_limit`

I would still keep infra failures out of quality routing, which I agree with in the current spec. But `invalid_output` should count as a quality-side failure for tasks that explicitly require machine-readable structure.

### 6. The current Thompson Sampling update is the one part I would change materially

I agree with the current spec's intent: pure winner/loser bits throw away too much information. A strong second-place model should not get the same update as a terrible answer.

However, the current formula:

```text
alpha += score / 5.0
beta  += 1 - score / 5.0
```

is only a heuristic. Beta Thompson Sampling is naturally paired with Bernoulli outcomes. Using judge scores as fractional pseudo-counts can work in practice, but it makes routing depend on score scale calibration in a way the spec does not control:

- if the judge becomes stricter later, rewards drop even when rankings stay the same
- if all models are similarly mediocre, everyone still accumulates moderate reward
- changing the judge prompt version shifts reward scale even if model quality does not change

For this size of system, the cleaner lightweight approach is:

- keep Beta Thompson Sampling
- derive updates from pairwise comparisons within the same judged round
- use tie handling and an `all_poor` gate

That gives the "second place gets some credit" property without making the posterior depend directly on absolute score calibration.

Example:

- if A beats B and C, A gets 2 wins
- if B beats C but loses to A, B gets 1 win and 1 loss
- ties count as 0.5 / 0.5
- if the judge marks `all_poor = true`, skip bandit update entirely

This is much easier to reason about and more robust to judge scale drift.

### 7. Judge protocol needs explicit failure and tie semantics

The current judge protocol is directionally good. I agree with:

- anonymized labels
- versioned judge prompts
- persisted raw judge response
- allowing `winner: null`

What is missing is the operational protocol around those fields.

The spec should define:

- how labels are assigned: randomize label mapping per run and persist it
- what happens if JSON is malformed: one repair retry, then `judge_error`
- how ties are represented: not just `winner: null`, but also tie groups or pairwise ties
- how "all outputs are bad" is represented: `all_poor = true`
- whether the judge result is usable for routing: `usable_for_bandit = true|false`

I would also canonicalize contestant outputs before sending them to the judge. If this is a structured JSON task, the judge should compare normalized fields, not raw JSON formatting. That reduces spurious differences from whitespace, key ordering, and markdown fences.

### 8. Prompt/context eligibility must be checked before selection

The brief explicitly asks about prompts that are too long for some models. The current spec does not cover this.

That matters because context-window mismatch is predictable. It should not show up later as a generic API failure if the system could have known before sending the request.

Each model registry entry should carry at least:

- `max_input_tokens`
- `max_output_tokens`
- optionally `supports_json` if that becomes useful

Then each run should:

1. estimate prompt tokens
2. filter ineligible models before bandit sampling
3. persist `skipped_context_limit` for filtered-out models if they were active but ineligible
4. mark the run degraded if eligibility reduces the pool below the requested `n`

That makes the system's behavior explainable and avoids poisoning failure analytics with predictable prompt-fit issues.

### 9. Versioning must cover contestant protocol, not just judge prompt

The current spec versions judge prompts, which is good, but that is not enough.

If contestant prompts or output schemas change, historical outcomes are no longer directly comparable. The same model can appear to improve or degrade just because the task contract changed.

So bandit state should be scoped to an evaluation protocol version that includes at least:

- `task_type`
- contestant prompt version
- judge prompt version
- output schema version if relevant

At this scale I would not persist mutable `alpha` / `beta` state. Compute them from raw attempts within the current protocol version, or within a recent time window. The dataset is tiny, so recomputing is cheap and avoids a whole class of cache invalidation and concurrency bugs.

### 10. Degraded-mode behavior should be explicit, because Prism needs to keep moving

The current spec returns `degraded: bool`, which is good, but it is not enough for callers.

Prism needs to know:

- did the judge fail?
- was there only one usable contestant?
- was a fallback winner used?
- is `best_output` safe to consume?

So `ArenaResult` should include:

- nullable `best_output`
- nullable `winner_model_id`
- `degraded_reason`
- `fallback_used`
- `bandit_updated`

I would also let the task config define fallback policy:

- `raise`
- `best_single_success`
- `designated_primary_model`

For Prism incremental analysis, `best_single_success` is probably the right fallback. It keeps the pipeline alive without pretending a proper judged round happened.

### 11. Observability should be minimal but real

I agree that this does not need a UI or dashboard. But the current spec still needs a tiny read surface so operators can answer obvious questions:

- which models are active?
- how often does each one fail?
- how often do runs degrade?
- which model is winning on a given task?

That can stay lightweight:

- `list_models()`
- `get_stats(task_type=None, since_days=30)`
- `validate_config()`
- optional CLI wrappers

The run table should also capture enough metadata to link an arena run back to Prism, such as `job_run_id`, `cluster_id`, or a generic `metadata_json`.

## Risks & Trade-offs

- A first-class sync API slightly increases surface area, but it removes much larger integration complexity from Prism's current threaded pipeline.
- Pairwise bandit updates are less expressive than continuous-reward bandits, but they are much easier to reason about and less sensitive to judge score drift. That is the better trade for a 3-4 model system.
- Treating `invalid_output` as a quality-side failure may penalize models for formatting rather than substance. In Prism's case that is acceptable, because invalid structured output is operationally unusable.
- Adding prompt/context eligibility checks means some runs will degrade earlier instead of "trying anyway." That is a good failure mode, but it may make the system look more conservative at first.
- Scoping routing stats by protocol version or recent window resets some history after prompt changes. That reduces sample size, but it prevents false learning from incomparable runs.
- Fallback behavior keeps the pipeline running when judging fails, but it can mask judge instability if the degraded path is not surfaced prominently in logs and stats.
- Global concurrency limiting will reduce throughput. That is intentional. For the current workload, reliability is more valuable than maximum parallelism.

## Concrete Suggestions

### 1. API shape

```python
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class TaskSpec:
    task_type: str
    system_prompt: str
    contestant_prompt_version: str
    judge_prompt_version: str
    default_n: int = 3
    output_parser: Optional[Callable[[str], Any]] = None
    output_validator: Optional[Callable[[Any], None]] = None
    fallback_policy: str = "best_single_success"   # raise | best_single_success | designated_primary_model
    quality_floor: float = 2.5
    tie_epsilon: float = 0.2


@dataclass
class ArenaResult:
    run_id: str
    best_output: Optional[str]
    best_parsed_output: Optional[Any]
    winner_model_id: Optional[str]
    winner_score: Optional[float]
    judge_comment: str
    outputs: list
    failures: list
    degraded: bool
    degraded_reason: Optional[str]
    fallback_used: bool
    bandit_updated: bool


def compete_sync(
    *,
    task_spec: TaskSpec,
    user_prompt: str,
    n: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> ArenaResult:
    ...


async def compete(
    *,
    task_spec: TaskSpec,
    user_prompt: str,
    n: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> ArenaResult:
    ...
```

Notes:

- Keep the caller-facing prompt split as `system_prompt` from `TaskSpec` plus `user_prompt` from the call site.
- `metadata` should be persisted so Prism can link arena runs back to cluster IDs or job runs.
- `best_output` and winner fields must be nullable.

### 2. Minimal model registry additions

```yaml
models:
  qwen-27b:
    endpoint: http://127.0.0.1:8002/v1
    model_name: qwen3.5-27b-distilled
    api_key: omlx-xxx
    timeout_s: 90
    max_input_tokens: 64000
    max_output_tokens: 4000
    status: active
```

I would not add more than this in v1. The key missing field is context capacity.

### 3. Bandit update rule

Use pairwise updates per judged round:

```python
def pairwise_updates(scores: dict[str, float], tie_epsilon: float, quality_floor: float, all_poor: bool):
    if all_poor or not scores or max(scores.values()) < quality_floor:
        return None

    wins = {label: 0.0 for label in scores}
    losses = {label: 0.0 for label in scores}
    labels = list(scores)

    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            delta = scores[a] - scores[b]
            if abs(delta) <= tie_epsilon:
                wins[a] += 0.5
                wins[b] += 0.5
                losses[a] += 0.5
                losses[b] += 0.5
            elif delta > 0:
                wins[a] += 1.0
                losses[b] += 1.0
            else:
                wins[b] += 1.0
                losses[a] += 1.0

    return wins, losses
```

Then Thompson Sampling stays standard:

```python
alpha = 1 + total_pairwise_wins
beta = 1 + total_pairwise_losses
sample = Beta(alpha, beta).sample()
```

Additional routing rules:

- If active eligible model count `<= n`, select all of them and skip sampling.
- Require `min_judged_rounds` per new model before full exploitation, for example 3.
- Compute stats only from the current `task_type + contestant_prompt_version + judge_prompt_version`, or from a recent rolling window.

### 4. Judge response schema

```json
{
  "winner": "B",
  "scores": {"A": 4.2, "B": 4.6, "C": 3.1},
  "all_poor": false,
  "usable_for_bandit": true,
  "tie_groups": [],
  "comment": "B is the most complete and actionable.",
  "judge_prompt_version": "signal_analysis_v1"
}
```

Judge protocol:

- Randomize label assignment per run.
- Validate response locally.
- If invalid, send one repair prompt with the raw response.
- If still invalid, persist `judge_error`, set `bandit_updated = false`, and use fallback policy if possible.

### 5. Storage/schema adjustments

The current two-table model is good. I would extend it, not replace it.

```sql
CREATE TABLE arena_runs (
    id TEXT PRIMARY KEY,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    task_type TEXT NOT NULL,
    contestant_prompt_version TEXT NOT NULL,
    judge_prompt_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    input_payload_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    requested_n INTEGER NOT NULL,
    eligible_n INTEGER NOT NULL,
    selected_n INTEGER NOT NULL,
    success_count INTEGER NOT NULL DEFAULT 0,
    invalid_output_count INTEGER NOT NULL DEFAULT 0,
    degraded INTEGER NOT NULL DEFAULT 0,
    degraded_reason TEXT,
    judge_model TEXT,
    judge_status TEXT NOT NULL DEFAULT 'not_run',
    judge_raw_response TEXT,
    winner_label TEXT,
    winner_model_id TEXT,
    judge_comment TEXT,
    fallback_used INTEGER NOT NULL DEFAULT 0,
    bandit_updated INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL
);

CREATE TABLE arena_attempts (
    run_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    label TEXT,
    selection_rank INTEGER,
    status TEXT NOT NULL,         -- success | invalid_output | timeout | oom | api_error | skipped_context_limit
    latency_ms INTEGER,
    prompt_tokens_est INTEGER,
    output TEXT,
    parsed_output_json TEXT,
    score REAL,
    is_winner INTEGER NOT NULL DEFAULT 0,
    error_type TEXT,
    error_message TEXT,
    PRIMARY KEY (run_id, model_id),
    FOREIGN KEY (run_id) REFERENCES arena_runs(id)
);

CREATE INDEX idx_arena_runs_task_ts
ON arena_runs(task_type, ts DESC);

CREATE INDEX idx_arena_attempts_run_status
ON arena_attempts(run_id, status);

CREATE INDEX idx_arena_attempts_model_status
ON arena_attempts(model_id, status);
```

Two specific notes:

- `prompt_hash` alone is not enough if the goal is auditability and replay. Store the actual input payload.
- The current proposed index on `(model_id, run_id)` does not help much for common task-level routing queries.

### 6. Prism integration path

Start here:

```python
# prism/pipeline/analyze.py
result = compete_sync(
    task_spec=INCREMENTAL_SIGNAL_ANALYSIS_TASK,
    user_prompt=prompt,
    metadata={"cluster_id": cd["id"], "job_type": "analyze_incremental"},
)

if result.best_parsed_output is None:
    return None

analysis = result.best_parsed_output
```

Do not start by replacing daily batch analysis.

Operational guardrail:

- when arena is enabled, reduce outer `max_workers`, or set arena's global `max_concurrent_rounds` to 1-2 so one Prism process cannot launch too many local model calls at once

### 7. SQLite and config behavior

- Use a separate `arena.db`, which I agree with from the current spec.
- Open one SQLite connection per arena run; do not share one connection object across threaded callers.
- Set `PRAGMA journal_mode=WAL`.
- Also set `PRAGMA busy_timeout`, because overlapping runs are plausible.
- Reload `models.yaml` on each call, or cache it behind an mtime check. With this workload, that is simpler than building hot-reload infrastructure.
- Add `validate_config()` that checks duplicate IDs, missing required fields, and obviously invalid numeric settings before the first run starts.
