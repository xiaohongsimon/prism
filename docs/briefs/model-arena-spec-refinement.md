# Brief: Model Arena Spec Refinement

## Background

We have a draft spec for "model-arena" — a lightweight Python package that makes multiple local LLM models compete on the same task via Best-of-N, with Opus as judge and Thompson Sampling MAB for routing.

The spec has already been through one round of Codex review which improved:
- async-first API
- runs + attempts two-table data model
- infra failure vs quality separation
- score-based MAB reward instead of binary winner/loser

## Current Spec

See `docs/superpowers/specs/2026-03-26-model-arena-design.md` for the full spec.

## What We Want

Review the spec for practical implementability and propose improvements. Focus on:

1. **API completeness**: Is the `compete()` interface sufficient? What about observability, configuration, and lifecycle management (e.g., viewing stats, adding models programmatically)?

2. **MAB correctness**: Is Thompson Sampling with score-based Beta rewards mathematically sound? Are there edge cases (e.g., all models score similarly, score scale drift over time, cold start with many new models at once)?

3. **Judge protocol robustness**: What happens when Opus returns malformed JSON? When all outputs are equally bad? When the prompt is too long for some models but not others?

4. **Operational gaps**: Logging, error recovery, model hot-reload, config validation, concurrent access (if two Prism runs overlap)?

5. **Integration friction**: How easy is it really to plug into Prism's existing sync pipeline (which appears to use ThreadPoolExecutor, not asyncio)?

## Constraints

- Keep it lightweight — this is NOT a grand platform
- 3-4 models, ~4 runs/day to start
- Must work with omlx (OpenAI-compatible API on localhost)
- SQLite is fine for storage
- No dashboard or UI needed yet

## Output Format

Produce a proposal with:
- Summary of recommended changes (bullet list)
- Detailed rationale for each change
- Any risks or trade-offs
- Concrete code/schema suggestions where applicable
