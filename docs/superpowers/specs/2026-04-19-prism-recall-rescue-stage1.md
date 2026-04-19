# Prism Recall Rescue — Stage 1 Design

> Date: 2026-04-19
> Status: Design draft, pending user review
> Author: xiaohongsimon + Claude

## 1. Why

### The problem in data

User self-reported: "推荐内容本身不对我胃口". DB confirms:

**Pairwise vote distribution (all time, N=127):**

| winner | count | share |
|---|---|---|
| a | 15 | 12% |
| b | 12 | 9% |
| both | 5 | 4% |
| **neither** | **95** | **75%** |

75% of shown pairs are rejected. Yet the user kept voting 127 times — engagement is fine, content is wrong.

**Learned preference weights tell a consistent story:**

Top positives: `author:zarazhangrui (+3.8)`, `tag:技术学习 (+3.0)`, `tag:个人成长 (+3.0)`, `tag:方法论 (+2.5)`, `tag:产品设计 (+2.3)`, `tag:工程理念 (+2.0)`.

Top negatives: `layer:strategic (-41)`, `layer:noise (-34)`, `source:arxiv:daily (-32)`, `source:hn:best (-10)`, `source:feed:follow-builders (-10)`, `tag:LLM (-5.7)`, `tag:AI Agent (-5.4)`.

### Root cause

`sources.yaml` was set up for a persona of "AI engineer tracking the LLM frontier" (Karpathy, DeepMind, Claude team, arXiv, HN). The user's current information goal, as revealed by 127 votes, is "TL/个人成长者 accumulating methodology and growth insights" — a persona centered on Chinese creators, product design, engineering philosophy, personal development. The two persona source pools barely overlap.

Subtractive filtering (blocking disliked tags/sources) cannot fix this. The missing sources are missing — no amount of ranking cleverness surfaces what was never fetched.

### Secondary bugs found

1. `pair_strategy` column is hardcoded `"exploit"` 127/127 in `pairwise.py:520`. The 70/20/10 strategy runs but is never recorded, so `decision_log` has zero data on this axis.
2. `external_feeds` table has 2 records, 0 processed. The "投喂外部链接" feature is dead code — no consumer.
3. Source preference weights are computed in `_update_source_weights()` but never reshape sync frequency or recall priority. The learning → recall link of the two-layer loop is broken.

## 2. Goals and non-goals

### Goals (acceptance criteria)

| Metric | Current | Target (2 weeks post-Stage 1) |
|---|---|---|
| "neither" vote rate | 75% | < 40% |
| `external_feeds.processed=0` | 100% | 0% (all processed within 1 hour) |
| `pair_strategy` distinct values | 1 | ≥ 3 (exploit/explore/random recorded accurately) |
| `sources.yaml` entries matching persona | unknown, low | ≥ 60% of active sources |

### Non-goals (explicitly deferred)

- Phase 2/3 automatic source discovery (Stage 3).
- Source weight → sync frequency reshape (Stage 2).
- Learning feedback UI ("系统学到了什么") — Stage 2.
- Pair card density, undo, visual redesign — separate track.
- Meta layer daily optimization job — Stage 2 or later.

## 3. Design choice: why structured form + free text + seed accounts

User response to "how do you want to tell the system your persona" was "我也不知道". Three layered inputs give robust coverage without forcing one path:

- **Structured form (6 questions)** — low friction, guarantees minimum useful signal, forces user to think about axes they'd skip in free text (e.g., language preference, content depth).
- **Free text (optional, 200-500 words)** — captures nuance that doesn't fit any pre-defined field, especially emotional or goal-driven context ("I want to become a better TL").
- **Seed accounts (optional, 3-10 handles)** — gives LLM concrete examples to expand from via similarity search. Easier for user than describing topics in abstract.

LLM extraction merges all three into preference bias weights + candidate source list.

## 4. Components

### 4.1 Persona capture — `/persona` page

**Route**: `GET /persona` renders form, `POST /persona` saves.

**Form fields:**

1. 你当前的职业身份与主要工作方向？（text, required）
2. 你希望通过 Prism 解决什么信息问题？（multi-select: 跟踪前沿 / 积累方法论 / 学某个具体技能 / 找灵感 / 工作参考 / 其他文本）
3. 最近 1-3 个月你在主动钻研的领域或技能？（text）
4. 你持续关注或想学习的人？可以列 3-10 个账号，中英文都行（text, one per line）
5. 哪些话题你刷到就烦、明确不想再看？（text）
6. 你偏好的内容风格？（multi-select: 硬核论文 / 技术深度文 / 方法论思考 / 产品与体验讨论 / 行业动态 / 趣味段子；语言: 中文为主 / 英文为主 / 都行；长度: 短平快 / 长文深度 / 都可以）

**New table:**

```sql
CREATE TABLE persona_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    answers_json TEXT NOT NULL,          -- structured Q1-6 answers
    free_text TEXT DEFAULT '',           -- optional free-form
    seed_handles_json TEXT DEFAULT '[]', -- parsed from Q4
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

On submit: previous `is_active=1` rows get flipped to 0, new row becomes active. Triggers LLM extraction job.

### 4.2 Persona → bias weights

**New dimension in `preference_weights`**: `persona_bias`. No schema change needed.

**LLM extraction prompt** (offline, `omlx` local):

- Input: structured answers, free text, seed handles, plus current top/bottom `preference_weights` for context.
- Output (JSON):
  - `bias_weights`: list of `{dimension, key, weight}` — e.g. `{tag, 方法论, +2.0}`, `{tag, LLM, -3.0}`, `{layer, strategic, -2.0}`.
  - `candidate_sources`: list of `{type, handle_or_url, display_name, rationale, category}` — 20-30 entries.
- Weights clipped to [-5, +5] to avoid swamping learned signal.

**Combining persona bias with learned weights**: additive during pool filtering and pair strategy scoring. Persona bias weights are **versioned by snapshot**: when a new snapshot supersedes the active one, old `persona_bias` rows are zeroed (not deleted — for audit).

### 4.3 Source proposals — `/persona/propose`

**New table:**

```sql
CREATE TABLE source_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,          -- 'x' | 'youtube' | 'rss' | 'hn' | ...
    source_config_json TEXT NOT NULL,   -- full config block for sources.yaml
    display_name TEXT NOT NULL,
    rationale TEXT DEFAULT '',          -- LLM's reason, shown to user
    origin TEXT NOT NULL,               -- 'persona' | 'external_feed' | 'manual'
    origin_ref INTEGER,                 -- persona_snapshot id or external_feed id
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected','ignored')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    reviewed_at TEXT
);
```

**UI**: `/persona/propose` lists all `status='pending'` proposals grouped by category, each with accept / reject buttons (HTMX).

**Accept flow**:

- Append source block to `config/sources.yaml` (preserve comments, use ruamel.yaml to maintain structure).
- Write to `decision_log` with `layer='recall'`, `action='add_source'`.
- Set `status='accepted'`, `reviewed_at=now`.
- Next scheduled `prism sync` picks it up automatically; no restart required.

**Reject flow**: sets status, logs decision, no yaml change.

### 4.4 Source pruning — `prism sources prune` CLI

Computes aggregate weight per source from `preference_weights` + recent win-rate.

For each source with `weight < -5` OR (`win_rate < 0.1` AND `comparison_count ≥ 10`):

- Display: source key, current weight, sample recent items, recommended action (disable / reduce frequency to weekly).
- Interactive Y/N/S (skip).
- Y: comments out the source block in `sources.yaml` with a `# pruned 2026-04-19 by user: weight=-32` note; logs decision.

Non-interactive mode via flag `--yes` for all, or `--dry-run` to preview.

### 4.5 External feed consumer — fix

**New task**: `process_external_feeds()` in `prism/pipeline/external_feed.py`.

For each row where `processed=0`:

1. LLM extraction (JSON):
   - `url_canonical`, `author_handle`, `content_type` (tweet/article/video/paper/other), `topics` (list of tags), `summary_zh` (1-2 sentences), `source_hint` (suggested source_type + key for sources.yaml).
2. Create synthetic signal in `signals` table with `signal_strength=5` and feedback weight 3.0 (already defined). Link to a new or existing cluster.
3. If `source_hint` refers to a source not already in `sources.yaml`: create `source_proposals` row with `origin='external_feed'`, `origin_ref=feed_id`.
4. Set `external_feeds.processed=1`, add `extracted_json` column storing the LLM output.
5. Decision log entry.

**Schedule**: hourly launchd job `prism process-external-feeds`, plus triggered on new submission for immediate feel.

**Schema change**: `ALTER TABLE external_feeds ADD COLUMN extracted_json TEXT DEFAULT ''`.

### 4.6 Pair strategy recording fix

Change `select_pair()` signature: return `(signal_a, signal_b, strategy_used)` where `strategy_used` is one of `exploit / explore / random / neither_fallback`.

`routes.py:record_vote()` receives strategy via hidden form field (set by `pair_cards.html` when rendering the pair) and writes it into `pairwise_comparisons.pair_strategy`.

No new schema — column exists, just needs honest population. Add minimal test.

## 5. Data flow end-to-end

```
User opens /persona
  → submits Q1-6 + free text + seed handles
  → persona_snapshots row created, is_active=1, prior row flipped to 0
  → LLM extraction job (sync, ~3-5s)
  → writes preference_weights (dimension='persona_bias') rows
  → writes 20-30 source_proposals rows (origin='persona')
  → redirects to /persona/propose

User reviews proposals
  → accepts some → sources.yaml updated + decision_log entries
  → rejects rest

User runs `prism sources prune` (one-off, may repeat monthly)
  → disables/downgrades hated sources in sources.yaml
  → decision_log entries

Next `prism sync` cycle
  → pulls new sources, stops pulling pruned ones
  → pool composition shifts toward persona

Pairwise pool selection
  → combines learned weights + persona_bias additively
  → select_pair returns strategy name
  → record_vote stores actual strategy in DB

External feed submission
  → stored in external_feeds (processed=0)
  → hourly consumer extracts + creates signal + proposes source
  → user sees new proposal on /persona/propose next visit
```

## 6. Code structure

New/changed files:

- `prism/web/routes.py` — add `/persona` GET/POST, `/persona/propose` GET + accept/reject HTMX endpoints.
- `prism/web/templates/persona.html`, `persona_propose.html`, `persona_propose_item.html` — new templates.
- `prism/persona.py` (new) — persona snapshot CRUD, LLM extraction orchestration.
- `prism/pipeline/external_feed.py` (new) — consumer task.
- `prism/sources/yaml_editor.py` (new) — safe yaml append/comment-out using ruamel.yaml.
- `prism/cli.py` — add `prism sources prune`, `prism process-external-feeds` subcommands.
- `prism/db.py` — add `persona_snapshots`, `source_proposals` tables; alter `external_feeds`.
- `prism/web/ranking.py` + `prism/pairwise.py` — integrate `persona_bias` into pool filtering; fix strategy return value.
- `prism/scheduling/com.prism.external-feed.plist` (new) — hourly job.

Tests:

- `tests/test_persona.py` — snapshot CRUD, LLM extraction fixture (mock), bias weight write.
- `tests/test_external_feed.py` — end-to-end: insert row → run consumer → verify signal + proposal + processed flag.
- `tests/test_pair_strategy.py` — verify recorded strategy matches selector output.
- `tests/test_sources_prune.py` — dry-run produces expected diff.

## 7. Rollout plan

Single branch `recall-rescue-stage1` off `main`, since this is a solo project:

1. Schema migration + table creation — merge immediately, no downtime (SQLite).
2. Persona capture + extraction + source proposals — merge when tested.
3. External feed consumer + pair strategy fix — merge together.
4. `prism sources prune` CLI — merge last.

**Seed test**: after all merged, user completes /persona once, accepts 10-20 proposals, runs a pruning pass. Then measures "neither" rate over the next 100 votes. Target < 40%; if still > 60%, re-run persona with more specific answers or move to Stage 2 earlier.

## 8. Risks and open questions

- **LLM source proposal quality**: candidate handles may not exist or be irrelevant. Mitigation: limit to 20-30 per run, require user accept, show rationale. Iterate prompt.
- **`sources.yaml` merge conflicts with manual edits**: comment preservation via ruamel.yaml; if conflict detected, save proposed version to `sources.yaml.proposed` and warn user.
- **Persona may drift over months**: the `is_active` flag + versioned snapshots let user re-run any time. Old bias zeroes out.
- **omlx concurrency**: all LLM calls are user-triggered (persona extract) or batch hourly (external feed), never in render path. Low risk of 503.
- **Cold start for completely new domains**: if proposed sources never had signals in pool, the learned weight remains 0 and persona_bias alone drives initial surfacing until pairwise feedback accumulates. This is expected and by design.
- **Schema migration strategy**: `db.py` has a single `init_db()`. Adding `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE` guarded by `PRAGMA table_info` is consistent with existing style.

## 9. What this does NOT solve

If after Stage 1 the "neither" rate is still high, causes are likely:

- Persona itself was too vague or conflicted → re-run /persona with sharper answers.
- Candidate sources produced by LLM are wrong domain → iterate extraction prompt with few-shot examples.
- User's interest is actually in content that no RSS/X/YouTube source publishes regularly → surfaces a deeper product question.

In any of these, move to Stage 2 (source weight → sync frequency + learning feedback UI) only after the root cause is known.
