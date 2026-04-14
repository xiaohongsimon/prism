# Prism Spec Post-Implementation Review

## Summary

The spec is materially better after the two review rounds. The most important conceptual fixes were correct: `source_key` is the right identifier, YAML-vs-runtime ownership is mostly well-specified, `job_runs`/`is_current` provenance is the right storage pattern, and the success criteria were correctly narrowed from "earlier" to "higher signal quality" (`docs/specs/2026-03-24-prism-design.md:92-108`, `:215-219`, `:352-360`).

The implementation, however, only partially realizes the spec's differentiators. In the live runtime state I inspected on 2026-03-26, all three X sources are auto-disabled, clustering is almost entirely singleton (1,182 clusters over two days, only 6 multi-item clusters, 0 cross-source clusters), there are 408 clusters with no current signal, and the 2026-03-26 daily run still saved a briefing after producing 0 daily signals and 0 trends (`data/daily.log:1-6`). In practice, Prism is currently closer to an arXiv-heavy collector with partial analysis than to the "deep X + cross-source intelligence" system the spec describes.

The uncommitted diff is directionally correct. It fixes config propagation into adapters (`prism/pipeline/sync.py`), YAML-to-DB config syncing (`prism/source_manager.py`), RSS/filter handling in arXiv (`prism/sources/arxiv.py`, `config/sources.yaml`), and topic-label generation for title-less clusters (`prism/pipeline/cluster.py`). Those changes are worth landing, but they do not close the larger gaps around X ingestion, clustering quality, GitHub depth, or fail-closed unattended operation.

## Spec-to-Code Gap Analysis

### 1. X deep collection is still mostly a stub

- What the spec said: X was supposed to be a primary differentiator: full thread expansion via Playwright, quoted-tweet fetch, thread completeness metrics in `prism status`, and red-alerting when completeness stayed below threshold (`docs/specs/2026-03-24-prism-design.md:45-56`).
- What the code does: `_try_expand_thread()` is a placeholder that always returns `None`, even when Playwright is installed (`prism/sources/x.py:154-171`). The adapter extracts quote URLs into `raw_json` but never fetches quoted tweet text (`prism/sources/x.py:76-100`). Sync results carry thread stats, but `run_sync()` does not persist them anywhere and `prism status` does not show completeness or 24h trends (`prism/sources/base.py:9-16`, `prism/pipeline/sync.py:58-86`, `:114-153`, `prism/cli.py:222-251`). In production, all X sources hit repeated 429s and were auto-disabled (`data/sync.log:1-35`).
- Whether the deviation was justified: No. The spec explicitly made deep X the main way Prism would outperform `shu`, and that value path is not implemented. The runtime evidence makes the gap worse, not better: Prism is not currently exercising the deep-X design at all.

### 2. GitHub "deep fetch" and growth tracking were not implemented

- What the spec said: GitHub Trending should track multi-day star growth, fetch README plus recent issues/PRs for high-growth repos, and distinguish sustained growth from one-day spikes (`docs/specs/2026-03-24-prism-design.md:80-90`, `:267-268`).
- What the code does: The adapter only scrapes the trending page, parses description/language/star counts, and stops there (`prism/sources/github.py:74-129`, `:154-197`). `fetch_repo_details()` is an explicit stub and is never called (`prism/sources/github.py:137-143`). The DB has no repo-history table for multi-day star curves (`prism/db.py:26-108`). The briefing template has no GitHub-specific heat section; it only renders generic trends (`prism/output/templates/briefing.html.j2:73-85`).
- Whether the deviation was justified: No, unless the spec is downgraded. This is not a polish gap; it removes an entire promised output dimension.

### 3. Clustering only partially matches the spec, and the live results are weak

- What the spec said: clustering should use four rules including entity co-occurrence, hourly runs should attach new raw items to same-day existing clusters, and `merged_context` truncation should respect source priority, freshness, and content length (`docs/specs/2026-03-24-prism-design.md:124-145`).
- What the code does: the implemented matcher uses URL, repo-name, and title-bigram similarity only; the `entities` parameter is not used in `_find_cluster()` at all (`prism/pipeline/cluster.py:97-120`). The CLI always calls `cluster_items(..., existing_clusters=[])`, so each run starts from scratch instead of matching into same-day existing clusters (`prism/cli.py:95-125`). Entity tagging is only printed to stdout and not persisted into clusters or prompts (`prism/pipeline/entities.py:19-42`, `prism/cli.py:117-123`). `build_merged_context()` sorts only by freshness and then allocates equal budget per body; it does not implement the spec's source-priority/content-length policy (`prism/pipeline/cluster.py:168-196`).
- Whether the deviation was justified: Partially at prototype time, but not any more. The live data says the current approach is not performing adequately: over two days the singleton ratio is about 0.993-0.996, only 6 clusters have more than one item, and none are cross-source. The uncommitted `cluster.py` change improves topic labels, but it does not address the actual clustering gaps.

### 4. "YAML is authoritative" is true only if callers avoid the API

- What the spec said: YAML should remain the final source of truth, while DB stores runtime state; CLI/API mutations must update both so state cannot drift (`docs/specs/2026-03-24-prism-design.md:100-108`, `:109-116`).
- What the code does: `source_manager.py` mostly honors this model for CLI/reconcile flows (`prism/source_manager.py:69-129`, `:132-206`). But the REST API writes directly to the DB, does not update YAML, generates `source_key` as `type:handle`, and cannot represent non-X singleton sources cleanly (`prism/api/routes.py:115-157`). `source add` in the CLI also requires `--handle`, so it is effectively X-only (`prism/cli.py:44-53`). The uncommitted `source_manager.py` and `sync.py` changes are fixing a real issue here by syncing YAML config changes back into the DB snapshot and passing parsed config into adapters.
- Whether the deviation was justified: No. This is exactly the ownership drift the prior reviews flagged. The code has the right state machine in one path and bypasses it in another.

### 5. Analysis/versioning semantics are only half-implemented

- What the spec said: daily analysis should logically supersede incremental results while preserving history; provenance should support reruns and audit; briefings should be generated from the current daily result set for the requested date (`docs/specs/2026-03-24-prism-design.md:215-219`, `:236-241`, `:251-276`).
- What the code does: `signals`, `cross_links`, and `trends` all have provenance fields in schema, which is good (`prism/db.py:55-108`). But `run_daily_analysis()` invalidates only incremental signals; it does not invalidate previous current daily signals or previous current cross-links for the same date, so reruns will stack multiple "current" daily outputs (`prism/pipeline/analyze.py:185-223`). `_load_narrative()` ignores the requested date and simply loads the latest `analyze_daily` job, which is why the saved 2026-03-26 briefing can reuse the 2026-03-25 narrative (`prism/output/briefing.py:69-81`).
- Whether the deviation was justified: No. The schema moved in the right direction, but the execution semantics do not yet match the provenance model the spec describes.

### 6. The arXiv runtime configuration has already drifted from the spec

- What the spec said: arXiv filtering should be two-stage: keyword whitelist, then cheap-LLM relevance scoring (`docs/specs/2026-03-24-prism-design.md:70-78`).
- What the code does: the adapter now supports `keyword+llm`, which is aligned with the spec (`prism/sources/arxiv.py:202-210`), but the current runtime config has been changed to `filter: keyword` only (`config/sources.yaml:11-17`). The uncommitted `arxiv.py` diff also fixes RSS 2.0 compatibility, which was a real operational gap.
- Whether the deviation was justified: Probably yes as a temporary operational tradeoff, but it should be made explicit. If v1 is running keyword-only for cost or reliability reasons, the spec should say so instead of silently implying the higher-precision path is active.

## Design Decisions: What Worked

- `source_key` plus explicit auto-disable semantics was the right design. The implementation in `source_manager.py` and `sync.py` correctly avoids the disable/restart/re-disable oscillation that the second review warned about (`prism/source_manager.py:69-129`, `prism/pipeline/sync.py:40-55`, `:89-111`). In the live DB, the three X sources stayed auto-disabled instead of flapping.
- The storage split between `raw_items`, `clusters`, `signals`, `trends`, and `job_runs` was correct. It made this retrospective possible because the system retains raw collection state, current-vs-historical analysis state, and pipeline execution records in a queryable way (`prism/db.py:26-108`).
- Distinguishing hard and soft failures also proved correct. X is failing with 429s, which the code treats as soft failures and disables only after 6 consecutive failures (`prism/pipeline/sync.py:13-17`, `:89-111`, `data/sync.log:1-35`). That behavior is more reasonable than the original simplistic "3 failures disables everything" rule.
- The uncommitted fixes show the spec's configuration shape was sound. Passing parsed YAML config through to adapters and syncing YAML edits back into DB snapshots is a small change with large impact, which means the underlying source model was worth keeping.

## Design Decisions: What Didn't Work

- Relying on X syndication as the sole practical X ingress path did not hold up. The spec anticipated thread-expansion fragility, but the real bottleneck arrived earlier: the base timeline fetch itself hit 429s and all X sources went dark before thread completeness was even measurable (`prism/sources/x.py:203-212`, `data/sync.log:1-35`).
- Using free-form `topic_label` as both a human-facing label and the key for trend continuity is too brittle. `topic_label` is heuristically generated from the longest title or ad hoc body keywords (`prism/pipeline/cluster.py:69-95`), while `calculate_trends()` does exact string matching against yesterday's label (`prism/pipeline/trends.py:41-60`). That design works only when labels happen to stay textually identical across days.
- Declaring YAML as the final authority while also exposing mutation paths that bypass the authority boundary created avoidable ambiguity. The design needed a single write path, not just a written rule.

## Uncovered Risks

- The system currently fails open, not fail closed. `prism analyze --daily` can return `0` outputs, `prism briefing --save` still persists a briefing, and `prism publish --notion` prints an error but returns success to the shell, so `daily.sh` continues (`prism/pipeline/analyze.py:156-183`, `prism/cli.py:168-173`, `:201-211`, `:257-277`, `prism/scheduling/daily.sh:1-12`, `data/daily.log:1-6`).
- LLM availability is a hidden single point of failure. The current runtime has 408 clusters with no current signal, and the sync log shows repeated LLM connection failures to `127.0.0.1:8002` while the hourly pipeline kept running (`prism/pipeline/llm.py:71-111`, `prism/pipeline/analyze.py:92-144`, `data/sync.log:7708-7726`).
- The launchd scripts have no locking or overlap protection. If an hourly incremental analysis is still retrying when the next hourly job fires, or if daily and hourly jobs overlap, the system relies on SQLite/WAL and ad hoc idempotency rather than an explicit single-run guard (`prism/scheduling/hourly.sh:1-11`, `prism/scheduling/daily.sh:1-12`).
- The reviewable HEAD and the current working tree are diverging. The uncommitted diff is not cosmetic; it addresses real runtime behavior. Until those changes are committed and covered by tests, the code review target and the running system can disagree.

## Prior Review Follow-Up

| Prior item | Addressed in spec update? | Implemented in code? | Remaining gap |
|---|---|---|---|
| First review [P0]: shift v1 value from "earlier" to "higher signal / more actionable" | Yes | Mostly yes | No major gap. The product is operating on that value proposition now. |
| First review [P0]: `merged_context` ordering must use pre-LLM fields | Yes | No | `build_merged_context()` uses freshness only and equal splitting, not source priority + freshness + content length (`prism/pipeline/cluster.py:168-196`). |
| First review [P1]: X thread fragility must have fallback + observability | Yes | No | Fallback exists only as `thread_partial`; expansion is stubbed and observability/alerting is missing (`prism/sources/x.py:154-171`, `prism/cli.py:222-251`). |
| First review [P1]: clustering needs an evaluation loop | Yes | Partial | `cluster_eval_stats` and `prism cluster --eval` exist, but entity co-occurrence and same-day incremental matching do not, and live singleton ratios are still ~99% (`prism/pipeline/cluster.py:152-165`, `prism/cli.py:144-147`). |
| First review [P1]: `signals` need provenance/versioning | Yes | Mostly yes | Schema is right, but rerun semantics are incomplete because prior current daily rows are not invalidated on rerun (`prism/db.py:55-108`, `prism/pipeline/analyze.py:185-223`). |
| First review [P1]: source management arbitration must be explicit | Yes | Partial | `source_manager.py` is good, but API routes bypass it and reintroduce drift (`prism/source_manager.py:69-129`, `prism/api/routes.py:115-157`). |
| Second review [P1]: non-X sources need a real business key | Yes | Mostly yes | `source_key` is in place, but API/CLI creation flows are still X-centric and do not cleanly support singleton non-X sources (`prism/cli.py:44-53`, `prism/api/routes.py:119-129`). |
| Second review [P1]: reconcile must respect auto-disabled sources across restarts | Yes | Mostly yes | The reconcile logic is correct, but API-level `enabled` mutation can still bypass the intended state machine (`prism/source_manager.py:102-129`, `prism/api/routes.py:132-145`). |

## Operational Readiness Assessment

The system is not yet ready for 24/7 unattended operation.

- The curated X layer is effectively down in production. Three of five sources are auto-disabled, all of them X.
- The analysis layer can silently stall. There are 408 clusters without any current signal, and incremental analysis failures are being logged without creating a hard stop for the scheduler.
- The daily pipeline can emit stale or empty artifacts. On 2026-03-26 it generated 0 daily signals and 0 trends, then still saved a briefing and attempted publication (`data/daily.log:1-6`).
- Briefing correctness is not date-safe because narrative retrieval is not date-scoped (`prism/output/briefing.py:69-81`).
- Observability is incomplete. The system has no persisted thread completeness metrics, no scheduler overlap protection, and no alert path for "analysis produced nothing for today's date".

Minimum missing pieces before I would call it unattended-ready:

1. Non-zero exit codes and fail-closed gating for `analyze`, `briefing`, and `publish`.
2. A real X ingest strategy or an explicit reduction of scope that no longer treats X depth as a v1 dependency.
3. Date-scoped briefing generation that refuses to save/publish when current daily signals/trends are absent.
4. Locking or single-flight protection for launchd jobs.
5. Landing and testing the current uncommitted fixes so the reviewed code matches deployed behavior.

## Recommendations for v1.1

1. Make source management single-path. Route API CRUD through `source_manager.py`, persist a config hash/version, and remove direct DB writes from `prism/api/routes.py`.
2. Reframe X as an explicitly unreliable source class unless a new transport is added. Either add rate-limit-aware backoff / alternative fetch infrastructure, or reduce the spec so v1.1 does not pretend thread-depth is currently operational.
3. Finish the promised clustering baseline before adding sophistication: match into same-day existing clusters, implement entity co-occurrence, persist entity tags, and review daily singleton/cross-source rates as a real acceptance gate.
4. Make the daily path fail closed. If `analyze --daily` yields no current daily signals for the requested date, `briefing --save` and `publish --notion` should stop with non-zero exit codes.
5. Separate human labels from trend identity. Introduce a canonical topic key based on repo/entity/URL anchors and keep `topic_label` purely presentational.
6. Either implement GitHub deep fetch and multi-day repo history, or remove "GitHub heat" from the spec and briefing contract.
7. Land the uncommitted fixes, then add regression tests for the specific issues they address: adapter config propagation, YAML->DB config sync, RSS 2.0 parsing, and title-less topic labeling.
8. Decide explicitly whether arXiv runs `keyword` or `keyword+llm` in production, and document the operational reason. Right now the code and the runtime config are pointing in different directions.
