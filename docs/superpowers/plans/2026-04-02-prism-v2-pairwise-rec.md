# Prism v2 Pairwise Recommendation System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform Prism from a news aggregator into a pairwise preference learning recommendation system with Bradley-Terry scoring, dynamic source weights, and external feed ingestion.

**Architecture:** New module `prism/web/pairwise.py` holds all pairwise logic (BT scoring, pair selection, preference updates, external feed processing). Routes added to existing `routes.py`. New templates for pairwise UI. DB schema extended with 5 new tables. Existing ranking.py modified to include BT scores in hot tab.

**Tech Stack:** Python 3, SQLite, FastAPI, Jinja2, HTMX, pytest

**Spec:** `docs/superpowers/specs/2026-04-02-prism-v2-pairwise-rec.md`

---

### Task 1: DB Schema — Add 5 new tables

**Files:**
- Modify: `prism/db.py:250` (before the PRAGMA line)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing test for new tables**

Add to `tests/test_db.py`:

```python
def test_pairwise_tables_exist():
    """All v2 pairwise tables should be created by init_db."""
    import sqlite3
    from prism.db import init_db
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)

    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    for t in ["pairwise_comparisons", "signal_scores", "source_weights",
              "decision_log", "external_feeds"]:
        assert t in tables, f"Missing table: {t}"

    # Verify pairwise_comparisons columns
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pairwise_comparisons)").fetchall()}
    assert "signal_a_id" in cols
    assert "winner" in cols
    assert "pair_strategy" in cols
    assert "response_time_ms" in cols

    # Verify signal_scores columns
    cols = {r[1] for r in conn.execute("PRAGMA table_info(signal_scores)").fetchall()}
    assert "bt_score" in cols
    assert "comparison_count" in cols

    # Verify external_feeds has UNIQUE url
    conn.execute("INSERT INTO external_feeds (url, topic) VALUES ('http://a', 'test')")
    try:
        conn.execute("INSERT INTO external_feeds (url, topic) VALUES ('http://a', 'dupe')")
        assert False, "Should have raised IntegrityError for duplicate URL"
    except sqlite3.IntegrityError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_db.py::test_pairwise_tables_exist -v`
Expected: FAIL — tables don't exist yet.

- [ ] **Step 3: Add new tables to init_db**

In `prism/db.py`, add the following SQL before the `PRAGMA journal_mode=WAL` line (before line 251):

```sql
        -- Pairwise recommendation system (v2)
        CREATE TABLE IF NOT EXISTS pairwise_comparisons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_a_id INTEGER NOT NULL REFERENCES signals(id),
            signal_b_id INTEGER NOT NULL REFERENCES signals(id),
            winner TEXT NOT NULL CHECK(winner IN ('a', 'b', 'both', 'neither', 'skip')),
            user_comment TEXT DEFAULT '',
            pair_strategy TEXT DEFAULT 'exploit',
            response_time_ms INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS signal_scores (
            signal_id INTEGER PRIMARY KEY REFERENCES signals(id),
            bt_score REAL NOT NULL DEFAULT 1500.0,
            comparison_count INTEGER NOT NULL DEFAULT 0,
            win_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS source_weights (
            source_key TEXT PRIMARY KEY,
            weight REAL NOT NULL DEFAULT 1.0,
            win_rate REAL NOT NULL DEFAULT 0.5,
            total_comparisons INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS decision_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            layer TEXT NOT NULL CHECK(layer IN ('recall', 'ranking')),
            action TEXT NOT NULL,
            reason TEXT NOT NULL,
            context_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS external_feeds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL DEFAULT '' UNIQUE,
            topic TEXT NOT NULL DEFAULT '',
            user_note TEXT NOT NULL DEFAULT '',
            extracted_tags_json TEXT NOT NULL DEFAULT '[]',
            processed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add prism/db.py tests/test_db.py
git commit -m "feat(db): add 5 pairwise recommendation tables"
```

---

### Task 2: Pairwise core logic — BT scoring + pair selection

**Files:**
- Create: `prism/web/pairwise.py`
- Create: `tests/web/test_pairwise.py`

- [ ] **Step 1: Write failing tests for BT scoring**

Create `tests/web/test_pairwise.py`:

```python
"""Tests for pairwise recommendation engine."""

import json
import sqlite3
import pytest
from prism.db import init_db


def _fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _seed_signals(conn, n=5):
    """Insert n signals with clusters and sources for testing."""
    conn.execute("INSERT INTO sources (source_key, type, handle) VALUES ('hn:best', 'hackernews', '')")
    conn.execute("INSERT INTO sources (source_key, type, handle) VALUES ('x:karpathy', 'x', 'karpathy')")
    for i in range(1, n + 1):
        conn.execute(
            "INSERT INTO raw_items (source_id, url, title, published_at) VALUES (?, ?, ?, datetime('now'))",
            (1 if i % 2 else 2, f"http://item{i}", f"Item {i}"),
        )
        conn.execute(
            "INSERT INTO clusters (date, topic_label, item_count) VALUES (date('now'), ?, 1)",
            (f"Topic {i}",),
        )
        conn.execute("INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (?, ?)", (i, i))
        conn.execute(
            "INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength, "
            "tags_json, is_current) VALUES (?, ?, 'actionable', 3, ?, 1)",
            (i, f"Signal {i} summary", json.dumps([f"tag{i}"])),
        )
    conn.commit()


# --- BT Scoring ---

def test_bt_score_update():
    from prism.web.pairwise import update_bt_scores
    new_a, new_b = update_bt_scores(1500.0, 1500.0, "a")
    assert new_a > 1500.0
    assert new_b < 1500.0
    assert abs((new_a - 1500.0) + (1500.0 - new_b)) < 0.01  # zero-sum


def test_bt_both():
    from prism.web.pairwise import update_bt_scores
    new_a, new_b = update_bt_scores(1500.0, 1500.0, "both")
    assert abs(new_a - 1500.0) < 0.01
    assert abs(new_b - 1500.0) < 0.01


def test_bt_neither_skip():
    from prism.web.pairwise import update_bt_scores
    new_a, new_b = update_bt_scores(1500.0, 1500.0, "neither")
    assert new_a == 1500.0
    assert new_b == 1500.0
    new_a2, new_b2 = update_bt_scores(1600.0, 1400.0, "skip")
    assert new_a2 == 1600.0
    assert new_b2 == 1400.0


def test_bt_underdog_wins_more():
    """Lower-rated signal gains more when winning."""
    from prism.web.pairwise import update_bt_scores
    new_a, new_b = update_bt_scores(1300.0, 1700.0, "a")
    gain_a = new_a - 1300.0
    new_c, new_d = update_bt_scores(1700.0, 1300.0, "a")
    gain_c = new_c - 1700.0
    assert gain_a > gain_c  # underdog gains more


# --- Pair Selection ---

def test_select_pair_returns_two_different_signals():
    from prism.web.pairwise import select_pair
    conn = _fresh_db()
    _seed_signals(conn, 5)
    result = select_pair(conn)
    assert result is not None
    a, b = result
    assert a["signal_id"] != b["signal_id"]


def test_select_pair_insufficient():
    from prism.web.pairwise import select_pair
    conn = _fresh_db()
    _seed_signals(conn, 1)  # only 1 signal — not enough for a pair
    result = select_pair(conn)
    assert result is None


def test_pair_break_loop():
    """After 3 consecutive 'neither', force random strategy."""
    from prism.web.pairwise import select_pair, record_vote
    conn = _fresh_db()
    _seed_signals(conn, 10)
    # Record 3 'neither' votes
    for _ in range(3):
        pair = select_pair(conn)
        assert pair is not None
        a, b = pair
        record_vote(conn, a["signal_id"], b["signal_id"], "neither", "", 0)
    # Next pair should still work (random fallback)
    pair = select_pair(conn)
    assert pair is not None


# --- Record Vote ---

def test_record_vote_updates_bt_and_preferences():
    from prism.web.pairwise import record_vote
    conn = _fresh_db()
    _seed_signals(conn, 2)
    record_vote(conn, signal_a_id=1, signal_b_id=2, winner="a",
                comment="better technical depth", response_time_ms=3000)

    # Check BT scores updated
    row_a = conn.execute("SELECT bt_score FROM signal_scores WHERE signal_id = 1").fetchone()
    row_b = conn.execute("SELECT bt_score FROM signal_scores WHERE signal_id = 2").fetchone()
    assert row_a is not None
    assert row_b is not None
    assert row_a["bt_score"] > 1500.0
    assert row_b["bt_score"] < 1500.0

    # Check pairwise_comparisons recorded
    row = conn.execute("SELECT * FROM pairwise_comparisons ORDER BY id DESC LIMIT 1").fetchone()
    assert row["winner"] == "a"
    assert row["user_comment"] == "better technical depth"

    # Check source_weights updated
    sw = conn.execute("SELECT * FROM source_weights WHERE source_key = 'hn:best'").fetchone()
    assert sw is not None
    assert sw["total_comparisons"] > 0


# --- External Feed ---

def test_external_feed_preference():
    from prism.web.pairwise import process_external_feed
    conn = _fresh_db()
    _seed_signals(conn, 2)
    process_external_feed(conn, url="https://example.com/article", note="interesting infra work")

    row = conn.execute("SELECT * FROM external_feeds WHERE url = 'https://example.com/article'").fetchone()
    assert row is not None
    assert row["user_note"] == "interesting infra work"


def test_external_feed_url_dedup():
    from prism.web.pairwise import process_external_feed
    conn = _fresh_db()
    process_external_feed(conn, url="https://example.com/a", note="first")
    process_external_feed(conn, url="https://example.com/a", note="updated")
    rows = conn.execute("SELECT * FROM external_feeds WHERE url = 'https://example.com/a'").fetchall()
    assert len(rows) == 1
    assert rows[0]["user_note"] == "updated"


# --- Decision Log ---

def test_decision_log_on_source_weight_adjust():
    from prism.web.pairwise import adjust_source_weights
    conn = _fresh_db()
    _seed_signals(conn, 5)
    # Simulate enough comparisons for hn:best with high win rate
    conn.execute(
        "INSERT OR REPLACE INTO source_weights (source_key, weight, win_rate, total_comparisons) "
        "VALUES ('hn:best', 1.0, 0.8, 15)"
    )
    conn.commit()
    adjust_source_weights(conn)
    logs = conn.execute("SELECT * FROM decision_log WHERE action = 'adjust_source_weight'").fetchall()
    assert len(logs) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/web/test_pairwise.py -v`
Expected: FAIL — `prism.web.pairwise` does not exist.

- [ ] **Step 3: Implement pairwise.py**

Create `prism/web/pairwise.py`:

```python
"""Pairwise recommendation engine: BT scoring, pair selection, preference updates."""

import json
import random
import sqlite3
from datetime import datetime, timezone, timedelta

# --- Constants ---

K = 48  # BT learning rate
BT_INITIAL = 1500.0
RECENT_PAIR_LIMIT = 50  # exclude signals seen in last N pairs
SIGNAL_MAX_AGE_DAYS = 7
PAIR_STRATEGY_WEIGHTS = {"exploit": 0.7, "explore": 0.2, "random": 0.1}
NEITHER_BREAK_THRESHOLD = 3

# Pairwise feedback deltas for preference_weights
WINNER_DELTA = 1.0
LOSER_DELTA = -0.3
BOTH_DELTA = 0.3
NEITHER_DELTA = -0.5
EXTERNAL_FEED_DELTA = 3.0


# --- Bradley-Terry Scoring ---

def update_bt_scores(score_a: float, score_b: float, winner: str) -> tuple[float, float]:
    """Return updated (new_a, new_b) after a pairwise comparison."""
    if winner not in ("a", "b", "both"):
        return score_a, score_b

    expected_a = 1.0 / (1.0 + 10 ** ((score_b - score_a) / 400))
    expected_b = 1.0 - expected_a

    if winner == "a":
        actual_a, actual_b = 1.0, 0.0
    elif winner == "b":
        actual_a, actual_b = 0.0, 1.0
    else:  # both
        actual_a, actual_b = 0.5, 0.5

    new_a = score_a + K * (actual_a - expected_a)
    new_b = score_b + K * (actual_b - expected_b)
    return new_a, new_b


def _ensure_signal_score(conn: sqlite3.Connection, signal_id: int) -> float:
    """Ensure signal has a bt_score row; return current score."""
    row = conn.execute("SELECT bt_score FROM signal_scores WHERE signal_id = ?", (signal_id,)).fetchone()
    if row:
        return row["bt_score"]
    conn.execute(
        "INSERT INTO signal_scores (signal_id, bt_score) VALUES (?, ?)",
        (signal_id, BT_INITIAL),
    )
    return BT_INITIAL


# --- Pair Selection ---

def _get_candidate_pool(conn: sqlite3.Connection) -> list[dict]:
    """Get signals eligible for pairwise comparison."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SIGNAL_MAX_AGE_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")

    # Get recently shown signal IDs
    recent_ids = set()
    rows = conn.execute(
        "SELECT signal_a_id, signal_b_id FROM pairwise_comparisons "
        "ORDER BY id DESC LIMIT ?",
        (RECENT_PAIR_LIMIT,),
    ).fetchall()
    for r in rows:
        recent_ids.add(r["signal_a_id"])
        recent_ids.add(r["signal_b_id"])

    # Get current signals within age limit
    signals = conn.execute(
        """SELECT s.id AS signal_id, s.cluster_id, s.summary, s.signal_layer,
                  s.signal_strength, s.tags_json, s.created_at,
                  c.topic_label, c.item_count
           FROM signals s JOIN clusters c ON s.cluster_id = c.id
           WHERE s.is_current = 1 AND s.created_at >= ?
           ORDER BY s.created_at DESC""",
        (cutoff,),
    ).fetchall()

    pool = []
    for s in signals:
        if s["signal_id"] in recent_ids:
            continue
        score_row = conn.execute(
            "SELECT bt_score, comparison_count FROM signal_scores WHERE signal_id = ?",
            (s["signal_id"],),
        ).fetchone()
        bt_score = score_row["bt_score"] if score_row else BT_INITIAL
        comp_count = score_row["comparison_count"] if score_row else 0
        tags = []
        try:
            tags = json.loads(s["tags_json"]) if s["tags_json"] else []
        except (json.JSONDecodeError, TypeError):
            pass
        pool.append({
            "signal_id": s["signal_id"],
            "cluster_id": s["cluster_id"],
            "topic_label": s["topic_label"],
            "summary": s["summary"],
            "signal_layer": s["signal_layer"],
            "signal_strength": s["signal_strength"],
            "tags": tags,
            "item_count": s["item_count"],
            "created_at": s["created_at"],
            "bt_score": bt_score,
            "comparison_count": comp_count,
        })
    return pool


def _check_neither_streak(conn: sqlite3.Connection) -> bool:
    """Return True if last N comparisons were all 'neither'."""
    rows = conn.execute(
        "SELECT winner FROM pairwise_comparisons ORDER BY id DESC LIMIT ?",
        (NEITHER_BREAK_THRESHOLD,),
    ).fetchall()
    if len(rows) < NEITHER_BREAK_THRESHOLD:
        return False
    return all(r["winner"] == "neither" for r in rows)


def select_pair(conn: sqlite3.Connection) -> tuple[dict, dict] | None:
    """Select next pair for comparison. Returns None if insufficient candidates."""
    pool = _get_candidate_pool(conn)
    if len(pool) < 2:
        return None

    # Force random if neither streak
    if _check_neither_streak(conn):
        chosen = random.sample(pool, 2)
        return chosen[0], chosen[1]

    # Pick strategy
    r = random.random()
    if r < PAIR_STRATEGY_WEIGHTS["exploit"]:
        return _pick_exploit(pool)
    elif r < PAIR_STRATEGY_WEIGHTS["exploit"] + PAIR_STRATEGY_WEIGHTS["explore"]:
        return _pick_explore(pool)
    else:
        return _pick_random(pool)


def _pick_exploit(pool: list[dict]) -> tuple[dict, dict]:
    """One high-score signal + one low-comparison signal, different topics."""
    sorted_by_score = sorted(pool, key=lambda x: x["bt_score"], reverse=True)
    top_n = max(1, len(sorted_by_score) * 30 // 100)
    high = random.choice(sorted_by_score[:top_n])

    # Find lowest comparison_count signal with different topic
    candidates = sorted(
        [s for s in pool if s["signal_id"] != high["signal_id"] and s["topic_label"] != high["topic_label"]],
        key=lambda x: x["comparison_count"],
    )
    if not candidates:
        # Fallback: allow same topic
        candidates = [s for s in pool if s["signal_id"] != high["signal_id"]]
    if not candidates:
        return pool[0], pool[1]
    low = candidates[0]
    return high, low


def _pick_explore(pool: list[dict]) -> tuple[dict, dict]:
    """Two signals with low comparison count."""
    new_signals = [s for s in pool if s["comparison_count"] < 3]
    if len(new_signals) >= 2:
        chosen = random.sample(new_signals, 2)
        return chosen[0], chosen[1]
    # Fallback to random
    return _pick_random(pool)


def _pick_random(pool: list[dict]) -> tuple[dict, dict]:
    """Completely random pair."""
    chosen = random.sample(pool, 2)
    return chosen[0], chosen[1]


# --- Record Vote ---

def _get_signal_source_keys(conn: sqlite3.Connection, signal_id: int) -> list[str]:
    """Get source keys for a signal's cluster."""
    rows = conn.execute(
        """SELECT DISTINCT src.source_key
           FROM signals s
           JOIN cluster_items ci ON ci.cluster_id = s.cluster_id
           JOIN raw_items ri ON ri.id = ci.raw_item_id
           JOIN sources src ON src.id = ri.source_id
           WHERE s.id = ?""",
        (signal_id,),
    ).fetchall()
    return [r["source_key"] for r in rows]


def _get_signal_meta(conn: sqlite3.Connection, signal_id: int) -> dict:
    """Get tags, layer, authors for a signal."""
    row = conn.execute(
        "SELECT signal_layer, tags_json FROM signals WHERE id = ?", (signal_id,)
    ).fetchone()
    if not row:
        return {"tags": [], "layer": "", "authors": []}
    tags = []
    try:
        tags = json.loads(row["tags_json"]) if row["tags_json"] else []
    except (json.JSONDecodeError, TypeError):
        pass
    # Get authors
    authors = []
    author_rows = conn.execute(
        """SELECT DISTINCT ri.author FROM signals s
           JOIN cluster_items ci ON ci.cluster_id = s.cluster_id
           JOIN raw_items ri ON ri.id = ci.raw_item_id
           WHERE s.id = ? AND ri.author != ''""",
        (signal_id,),
    ).fetchall()
    authors = [r["author"] for r in author_rows]
    return {"tags": tags, "layer": row["signal_layer"], "authors": authors}


def _update_preference_weights(conn: sqlite3.Connection, signal_id: int, delta: float):
    """Update preference_weights for all dimensions of a signal."""
    meta = _get_signal_meta(conn, signal_id)
    source_keys = _get_signal_source_keys(conn, signal_id)

    keys_to_update = []
    keys_to_update.append(("layer", meta["layer"]))
    for tag in meta["tags"]:
        keys_to_update.append(("tag", tag))
    for sk in source_keys:
        keys_to_update.append(("source", sk))
    for author in meta["authors"]:
        keys_to_update.append(("author", author))

    for dimension, key in keys_to_update:
        existing = conn.execute(
            "SELECT weight FROM preference_weights WHERE dimension = ? AND key = ?",
            (dimension, key),
        ).fetchone()
        new_weight = (existing["weight"] if existing else 0.0) + delta
        conn.execute(
            "INSERT OR REPLACE INTO preference_weights (dimension, key, weight, updated_at) "
            "VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%S', 'now'))",
            (dimension, key, new_weight),
        )


def _update_source_weights(conn: sqlite3.Connection, signal_id: int, won: bool):
    """Update source_weights win_rate and total_comparisons for a signal's sources."""
    source_keys = _get_signal_source_keys(conn, signal_id)
    for sk in source_keys:
        row = conn.execute("SELECT * FROM source_weights WHERE source_key = ?", (sk,)).fetchone()
        if row:
            new_total = row["total_comparisons"] + 1
            new_wins = (row["win_rate"] * row["total_comparisons"] + (1 if won else 0))
            new_rate = new_wins / new_total
            conn.execute(
                "UPDATE source_weights SET win_rate = ?, total_comparisons = ?, "
                "updated_at = datetime('now') WHERE source_key = ?",
                (new_rate, new_total, sk),
            )
        else:
            conn.execute(
                "INSERT INTO source_weights (source_key, weight, win_rate, total_comparisons) "
                "VALUES (?, 1.0, ?, 1)",
                (sk, 1.0 if won else 0.0),
            )


def record_vote(
    conn: sqlite3.Connection,
    signal_a_id: int,
    signal_b_id: int,
    winner: str,
    comment: str = "",
    response_time_ms: int = 0,
) -> None:
    """Record a pairwise vote and update all dependent scores."""
    # Determine strategy used (stored for analytics)
    strategy = "exploit"  # default; actual strategy tracked in select_pair

    # Record comparison
    conn.execute(
        "INSERT INTO pairwise_comparisons "
        "(signal_a_id, signal_b_id, winner, user_comment, pair_strategy, response_time_ms) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (signal_a_id, signal_b_id, winner, comment, strategy, response_time_ms),
    )

    # Update BT scores
    score_a = _ensure_signal_score(conn, signal_a_id)
    score_b = _ensure_signal_score(conn, signal_b_id)
    new_a, new_b = update_bt_scores(score_a, score_b, winner)

    conn.execute(
        "UPDATE signal_scores SET bt_score = ?, comparison_count = comparison_count + 1, "
        "win_count = win_count + CASE WHEN ? IN ('a') THEN 1 ELSE 0 END, "
        "updated_at = datetime('now') WHERE signal_id = ?",
        (new_a, winner, signal_a_id),
    )
    conn.execute(
        "UPDATE signal_scores SET bt_score = ?, comparison_count = comparison_count + 1, "
        "win_count = win_count + CASE WHEN ? IN ('b') THEN 1 ELSE 0 END, "
        "updated_at = datetime('now') WHERE signal_id = ?",
        (new_b, winner, signal_b_id),
    )

    # Update preference weights
    if winner == "a":
        _update_preference_weights(conn, signal_a_id, WINNER_DELTA)
        _update_preference_weights(conn, signal_b_id, LOSER_DELTA)
    elif winner == "b":
        _update_preference_weights(conn, signal_b_id, WINNER_DELTA)
        _update_preference_weights(conn, signal_a_id, LOSER_DELTA)
    elif winner == "both":
        _update_preference_weights(conn, signal_a_id, BOTH_DELTA)
        _update_preference_weights(conn, signal_b_id, BOTH_DELTA)
    elif winner == "neither":
        _update_preference_weights(conn, signal_a_id, NEITHER_DELTA)
        _update_preference_weights(conn, signal_b_id, NEITHER_DELTA)

    # Update source weights
    if winner in ("a", "b"):
        winner_id = signal_a_id if winner == "a" else signal_b_id
        loser_id = signal_b_id if winner == "a" else signal_a_id
        _update_source_weights(conn, winner_id, won=True)
        _update_source_weights(conn, loser_id, won=False)
    elif winner in ("both", "neither"):
        _update_source_weights(conn, signal_a_id, won=(winner == "both"))
        _update_source_weights(conn, signal_b_id, won=(winner == "both"))

    conn.commit()


# --- External Feed ---

def process_external_feed(conn: sqlite3.Connection, url: str, note: str = "") -> None:
    """Process an externally provided URL/topic as strong positive feedback."""
    # Upsert: insert or update note
    existing = conn.execute("SELECT id FROM external_feeds WHERE url = ?", (url,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE external_feeds SET user_note = ?, processed = 0 WHERE url = ?",
            (note, url),
        )
    else:
        conn.execute(
            "INSERT INTO external_feeds (url, user_note) VALUES (?, ?)",
            (url, note),
        )
    conn.commit()


# --- Source Weight Adjustment (daily cron) ---

def adjust_source_weights(conn: sqlite3.Connection) -> None:
    """Daily job: adjust source weights based on pairwise win rates."""
    rows = conn.execute("SELECT * FROM source_weights").fetchall()
    for sw in rows:
        if sw["total_comparisons"] < 10:
            continue

        old_weight = sw["weight"]
        if sw["win_rate"] > 0.6:
            new_weight = min(old_weight + 0.2, 3.0)
        elif sw["win_rate"] < 0.3:
            new_weight = max(old_weight - 0.2, 0.1)
        else:
            new_weight = old_weight

        if new_weight != old_weight:
            conn.execute(
                "UPDATE source_weights SET weight = ?, updated_at = datetime('now') "
                "WHERE source_key = ?",
                (new_weight, sw["source_key"]),
            )
            log_decision(
                conn, "recall", "adjust_source_weight",
                f"{sw['source_key']}: {old_weight:.2f} -> {new_weight:.2f}, "
                f"win_rate={sw['win_rate']:.2f}",
                {"old_weight": old_weight, "new_weight": new_weight,
                 "win_rate": sw["win_rate"]},
            )
    conn.commit()


# --- Decision Log ---

def log_decision(conn: sqlite3.Connection, layer: str, action: str,
                 reason: str, context: dict | None = None) -> None:
    """Record an automated decision for audit trail."""
    conn.execute(
        "INSERT INTO decision_log (layer, action, reason, context_json) VALUES (?, ?, ?, ?)",
        (layer, action, reason, json.dumps(context or {}, ensure_ascii=False)),
    )


# --- Pairwise History ---

def get_pairwise_history(conn: sqlite3.Connection, page: int = 1, per_page: int = 20) -> list[dict]:
    """Get pairwise comparison history for the history tab."""
    offset = (page - 1) * per_page
    rows = conn.execute(
        """SELECT pc.*, 
                  sa.summary AS summary_a, ca.topic_label AS topic_a,
                  sb.summary AS summary_b, cb.topic_label AS topic_b
           FROM pairwise_comparisons pc
           JOIN signals sa ON sa.id = pc.signal_a_id
           JOIN clusters ca ON ca.id = sa.cluster_id
           JOIN signals sb ON sb.id = pc.signal_b_id
           JOIN clusters cb ON cb.id = sb.cluster_id
           ORDER BY pc.id DESC
           LIMIT ? OFFSET ?""",
        (per_page, offset),
    ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run all tests**

Run: `.venv/bin/pytest tests/web/test_pairwise.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add prism/web/pairwise.py tests/web/test_pairwise.py
git commit -m "feat(pairwise): BT scoring, pair selection, preference updates, external feed"
```

---

### Task 3: Ranking integration — BT scores in hot tab

**Files:**
- Modify: `prism/web/ranking.py`
- Modify: `tests/web/test_ranking.py`

- [ ] **Step 1: Write failing test**

Add to `tests/web/test_ranking.py`:

```python
def test_hot_tab_bt_integration():
    """Hot tab should incorporate BT scores into ranking."""
    conn = _fresh_db()
    _seed(conn)
    # Give signal 2 (vLLM, strength=3) a much higher BT score
    conn.execute("INSERT INTO signal_scores (signal_id, bt_score) VALUES (2, 2000.0)")
    conn.execute("INSERT INTO signal_scores (signal_id, bt_score) VALUES (1, 1000.0)")
    conn.commit()
    items = compute_feed(conn, tab="hot", page=1, per_page=10)
    # With BT boost, vLLM (signal 2) should rank higher despite lower signal_strength
    assert items[0]["topic_label"] == "vLLM"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/web/test_ranking.py::test_hot_tab_bt_integration -v`
Expected: FAIL — BT scores not yet in ranking.

- [ ] **Step 3: Implement BT score integration in ranking.py**

In `prism/web/ranking.py`, update TAB_WEIGHTS to include bt weight:

```python
# Score weights per tab: (heat, preference, decay, bt)
TAB_WEIGHTS = {
    "recommend": (0.4, 0.4, 0.2, 0.0),
    "follow":    (0.2, 0.5, 0.3, 0.0),
    "hot":       (0.3, 0.0, 0.3, 0.4),
}
```

In `compute_feed()`, after loading signals, load BT scores:

```python
    # Load BT scores
    bt_scores = {}
    bt_rows = conn.execute("SELECT signal_id, bt_score FROM signal_scores").fetchall()
    for br in bt_rows:
        bt_scores[br["signal_id"]] = br["bt_score"]
    max_bt = max(bt_scores.values(), default=1500.0) or 1500.0
```

Update the scoring line to include bt_norm:

```python
        bt = bt_scores.get(r["signal_id"], 1500.0)
        bt_norm = bt / max_bt
        item["score"] = w_heat * heat_norm + w_pref * pref + w_decay * decay + w_bt * bt_norm
```

Update the unpacking of TAB_WEIGHTS:

```python
    w_heat, w_pref, w_decay, w_bt = TAB_WEIGHTS.get(tab, TAB_WEIGHTS["recommend"])
```

- [ ] **Step 4: Run all ranking tests**

Run: `.venv/bin/pytest tests/web/test_ranking.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add prism/web/ranking.py tests/web/test_ranking.py
git commit -m "feat(ranking): integrate BT scores into hot tab scoring"
```

---

### Task 4: Web routes — Pairwise endpoints

**Files:**
- Modify: `prism/web/routes.py`
- Test: `tests/web/test_routes.py` (existing, add pairwise endpoint tests)

- [ ] **Step 1: Write failing tests**

Add to `tests/web/test_routes.py`:

```python
def test_pairwise_page(client):
    """GET / with tab=recommend should redirect to pairwise view."""
    resp = client.get("/?tab=recommend")
    assert resp.status_code == 200


def test_pairwise_vote(client):
    """POST /pairwise/vote should record vote and return next pair."""
    # Seed enough data first
    resp = client.post("/pairwise/vote", data={
        "signal_a_id": "1", "signal_b_id": "2",
        "winner": "a", "comment": "", "response_time_ms": "1000"
    })
    assert resp.status_code == 200


def test_pairwise_feed(client):
    """POST /pairwise/feed should accept external link."""
    resp = client.post("/pairwise/feed", data={
        "url": "https://example.com/test", "note": "interesting"
    })
    assert resp.status_code == 200
```

- [ ] **Step 2: Add pairwise routes to routes.py**

Add imports at top of `prism/web/routes.py`:

```python
from prism.web.pairwise import (
    select_pair, record_vote, process_external_feed, get_pairwise_history,
)
```

Add route handlers:

```python
@web_router.get("/pairwise/pair")
async def pairwise_pair(request: Request):
    """HTMX: return next pair of signals."""
    conn = _db(request)
    pair = select_pair(conn)
    if pair is None:
        tpl = _jinja_env.get_template("partials/pair_empty.html")
        return HTMLResponse(tpl.render())
    a, b = pair
    tpl = _jinja_env.get_template("partials/pair_cards.html")
    return HTMLResponse(tpl.render(signal_a=a, signal_b=b))


@web_router.post("/pairwise/vote")
async def pairwise_vote(
    request: Request,
    signal_a_id: int = Form(...),
    signal_b_id: int = Form(...),
    winner: str = Form(...),
    comment: str = Form(""),
    response_time_ms: int = Form(0),
):
    """Record vote and return next pair."""
    conn = _db(request)
    record_vote(conn, signal_a_id, signal_b_id, winner, comment, response_time_ms)
    # Return next pair
    pair = select_pair(conn)
    if pair is None:
        tpl = _jinja_env.get_template("partials/pair_empty.html")
        return HTMLResponse(tpl.render())
    a, b = pair
    tpl = _jinja_env.get_template("partials/pair_cards.html")
    return HTMLResponse(tpl.render(signal_a=a, signal_b=b))


@web_router.post("/pairwise/feed")
async def pairwise_feed(
    request: Request,
    url: str = Form(""),
    note: str = Form(""),
):
    """Accept external link/topic as strong positive feedback."""
    conn = _db(request)
    if url.strip():
        process_external_feed(conn, url=url.strip(), note=note.strip())
    # Return confirmation + next pair
    pair = select_pair(conn)
    if pair is None:
        tpl = _jinja_env.get_template("partials/pair_empty.html")
        return HTMLResponse(tpl.render(feed_success=True))
    a, b = pair
    tpl = _jinja_env.get_template("partials/pair_cards.html")
    return HTMLResponse(tpl.render(signal_a=a, signal_b=b, feed_success=True))


@web_router.get("/pairwise/history")
async def pairwise_history(request: Request, page: int = 1):
    """Render pairwise history page."""
    conn = _db(request)
    history = get_pairwise_history(conn, page=page)
    tpl = _jinja_env.get_template("history.html")
    return HTMLResponse(tpl.render(history=history, page=page, tab="history"))
```

Update the main feed route `"/"` to redirect recommend tab to pairwise:

In the existing `"/"` GET handler, add a check at the top:

```python
    if tab == "recommend":
        pair = select_pair(conn)
        tpl = _jinja_env.get_template("pairwise.html")
        return HTMLResponse(tpl.render(
            pair=pair, signal_a=pair[0] if pair else None,
            signal_b=pair[1] if pair else None, tab="recommend",
        ))
```

- [ ] **Step 3: Run tests**

Run: `.venv/bin/pytest tests/web/test_routes.py -v`
Expected: ALL PASS (may need template files first — proceed to Task 5).

- [ ] **Step 4: Commit**

```bash
git add prism/web/routes.py tests/web/test_routes.py
git commit -m "feat(routes): add pairwise vote, feed, history endpoints"
```

---

### Task 5: Templates — Pairwise UI

**Files:**
- Create: `prism/web/templates/pairwise.html`
- Create: `prism/web/templates/partials/pair_cards.html`
- Create: `prism/web/templates/partials/pair_empty.html`
- Create: `prism/web/templates/history.html`
- Modify: `prism/web/templates/feed.html` (add history tab)

- [ ] **Step 1: Create pairwise.html**

```html
{% extends "base.html" %}
{% block content %}
<nav class="top-nav">
    <span class="logo">⬡ Prism</span>
    <div class="tabs">
        <a class="tab active" href="/?tab=recommend">推荐</a>
        <a class="tab" href="/?tab=follow">关注</a>
        <a class="tab" href="/?tab=hot">热门</a>
        <a class="tab" href="/pairwise/history">历史</a>
    </div>
</nav>
<div id="pairwise-area">
{% if signal_a and signal_b %}
    {% include "partials/pair_cards.html" %}
{% else %}
    {% include "partials/pair_empty.html" %}
{% endif %}
</div>
{% endblock %}
```

- [ ] **Step 2: Create pair_cards.html**

```html
<div class="pair-container">
    <div class="pair-cards">
        <div class="pair-card pair-card-a">
            <div class="pair-label">A</div>
            <div class="card-title">{{ signal_a.topic_label }}</div>
            <div class="card-summary">{{ signal_a.summary }}</div>
            <div class="card-meta">
                <span>🔥 {{ signal_a.signal_strength }}</span>
                <span class="sep"></span>
                <span>{{ signal_a.created_at[:10] }}</span>
            </div>
            {% if signal_a.tags %}
            <div class="card-tags">
                {% for tag in signal_a.tags[:5] %}
                <span class="tag-pill">{{ tag }}</span>
                {% endfor %}
            </div>
            {% endif %}
        </div>
        <div class="pair-card pair-card-b">
            <div class="pair-label">B</div>
            <div class="card-title">{{ signal_b.topic_label }}</div>
            <div class="card-summary">{{ signal_b.summary }}</div>
            <div class="card-meta">
                <span>🔥 {{ signal_b.signal_strength }}</span>
                <span class="sep"></span>
                <span>{{ signal_b.created_at[:10] }}</span>
            </div>
            {% if signal_b.tags %}
            <div class="card-tags">
                {% for tag in signal_b.tags[:5] %}
                <span class="tag-pill">{{ tag }}</span>
                {% endfor %}
            </div>
            {% endif %}
        </div>
    </div>
    {% if feed_success %}
    <div class="feed-toast">已收到投喂</div>
    {% endif %}
    <form class="pair-actions"
          hx-post="/pairwise/vote"
          hx-target="#pairwise-area"
          hx-swap="innerHTML">
        <input type="hidden" name="signal_a_id" value="{{ signal_a.signal_id }}">
        <input type="hidden" name="signal_b_id" value="{{ signal_b.signal_id }}">
        <input type="hidden" name="response_time_ms" value="0" id="response-timer">
        <div class="pair-buttons">
            <button type="submit" name="winner" value="a" class="btn btn-pick">选 A</button>
            <button type="submit" name="winner" value="both" class="btn btn-both">都好</button>
            <button type="submit" name="winner" value="neither" class="btn btn-neither">都不行</button>
            <button type="submit" name="winner" value="b" class="btn btn-pick">选 B</button>
        </div>
        <div class="pair-comment">
            <input type="text" name="comment" placeholder="说说你的想法（可选）" class="comment-input">
        </div>
    </form>
    <form class="feed-form"
          hx-post="/pairwise/feed"
          hx-target="#pairwise-area"
          hx-swap="innerHTML">
        <input type="text" name="url" placeholder="投喂链接或话题" class="feed-input">
        <input type="text" name="note" placeholder="备注（可选）" class="feed-note">
        <button type="submit" class="btn btn-feed">投喂</button>
    </form>
</div>
<script>
(function() {
    var start = Date.now();
    document.querySelector('.pair-actions').addEventListener('submit', function() {
        document.getElementById('response-timer').value = Date.now() - start;
    });
})();
</script>
```

- [ ] **Step 3: Create pair_empty.html**

```html
<div class="pair-empty">
    <p>暂无足够的信号进行比较</p>
    <p>等待系统采集更多内容，或投喂一个链接：</p>
    <form class="feed-form"
          hx-post="/pairwise/feed"
          hx-target="#pairwise-area"
          hx-swap="innerHTML">
        <input type="text" name="url" placeholder="投喂链接或话题" class="feed-input">
        <input type="text" name="note" placeholder="备注（可选）" class="feed-note">
        <button type="submit" class="btn btn-feed">投喂</button>
    </form>
    {% if feed_success %}
    <div class="feed-toast">已收到投喂</div>
    {% endif %}
</div>
```

- [ ] **Step 4: Create history.html**

```html
{% extends "base.html" %}
{% block content %}
<nav class="top-nav">
    <span class="logo">⬡ Prism</span>
    <div class="tabs">
        <a class="tab" href="/?tab=recommend">推荐</a>
        <a class="tab" href="/?tab=follow">关注</a>
        <a class="tab" href="/?tab=hot">热门</a>
        <a class="tab active" href="/pairwise/history">历史</a>
    </div>
</nav>
<div class="history-list">
{% for h in history %}
<div class="history-item">
    <div class="history-pair">
        <div class="history-signal {% if h.winner == 'a' %}winner{% endif %}">
            <span class="history-label">A</span> {{ h.topic_a }}
        </div>
        <div class="history-vs">vs</div>
        <div class="history-signal {% if h.winner == 'b' %}winner{% endif %}">
            <span class="history-label">B</span> {{ h.topic_b }}
        </div>
    </div>
    <div class="history-meta">
        <span>{{ h.created_at[:16] }}</span>
        <span class="history-winner">
            {% if h.winner == 'a' %}选了 A
            {% elif h.winner == 'b' %}选了 B
            {% elif h.winner == 'both' %}都好
            {% elif h.winner == 'neither' %}都不行
            {% else %}跳过{% endif %}
        </span>
        {% if h.user_comment %}
        <span class="history-comment">💬 {{ h.user_comment }}</span>
        {% endif %}
    </div>
</div>
{% endfor %}
{% if not history %}
<div class="empty">还没有比较记录</div>
{% endif %}
</div>
{% endblock %}
```

- [ ] **Step 5: Update feed.html — add history tab**

In `prism/web/templates/feed.html`, add the history tab link after the hot tab:

```html
        <a class="tab {% if tab == 'hot' %}active{% endif %}"
           href="/?tab=hot">热门</a>
        <a class="tab" href="/pairwise/history">历史</a>
```

- [ ] **Step 6: Commit**

```bash
git add prism/web/templates/pairwise.html prism/web/templates/partials/pair_cards.html \
       prism/web/templates/partials/pair_empty.html prism/web/templates/history.html \
       prism/web/templates/feed.html
git commit -m "feat(ui): pairwise comparison templates + history page"
```

---

### Task 6: CSS + daily cron + full integration test

**Files:**
- Modify: `prism/web/static/style.css` (add pairwise styles)
- Modify: `prism/scheduling/daily.sh` (add source weight adjustment)
- Run: full test suite

- [ ] **Step 1: Add pairwise CSS to style.css**

Append to `prism/web/static/style.css`:

```css
/* === Pairwise Comparison === */
.pair-container { max-width: 900px; margin: 0 auto; padding: 1rem; }
.pair-cards { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: 1rem; }
.pair-card { background: var(--card-bg, #1a1a2e); border-radius: 12px; padding: 1.25rem; border: 2px solid transparent; transition: border-color 0.2s; }
.pair-card:hover { border-color: var(--accent, #6c63ff); }
.pair-label { font-size: 0.75rem; font-weight: 700; color: var(--accent, #6c63ff); text-transform: uppercase; margin-bottom: 0.5rem; }
.pair-actions { text-align: center; margin-bottom: 1rem; }
.pair-buttons { display: flex; gap: 0.5rem; justify-content: center; margin-bottom: 0.75rem; }
.btn { padding: 0.5rem 1.25rem; border-radius: 8px; border: 1px solid var(--border, #333); background: var(--card-bg, #1a1a2e); color: var(--text, #e0e0e0); cursor: pointer; font-size: 0.9rem; transition: all 0.2s; }
.btn:hover { background: var(--accent, #6c63ff); color: #fff; border-color: var(--accent, #6c63ff); }
.btn-pick { min-width: 80px; }
.btn-both, .btn-neither { font-size: 0.8rem; opacity: 0.8; }
.btn-feed { background: var(--accent, #6c63ff); color: #fff; border: none; }
.comment-input, .feed-input, .feed-note { width: 100%; max-width: 500px; padding: 0.5rem; border-radius: 6px; border: 1px solid var(--border, #333); background: var(--input-bg, #111); color: var(--text, #e0e0e0); margin-bottom: 0.5rem; }
.feed-form { display: flex; gap: 0.5rem; justify-content: center; align-items: center; flex-wrap: wrap; margin-top: 0.5rem; }
.feed-toast { text-align: center; color: #4caf50; font-size: 0.85rem; margin: 0.5rem 0; }
.pair-empty { text-align: center; padding: 3rem 1rem; color: var(--text-secondary, #888); }
.tag-pill { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 4px; background: var(--tag-bg, #222); color: var(--text-secondary, #aaa); font-size: 0.75rem; margin: 0.15rem; }
.card-tags { margin-top: 0.5rem; }

/* === History === */
.history-list { max-width: 700px; margin: 0 auto; padding: 1rem; }
.history-item { background: var(--card-bg, #1a1a2e); border-radius: 10px; padding: 1rem; margin-bottom: 0.75rem; }
.history-pair { display: flex; align-items: center; gap: 0.75rem; }
.history-signal { flex: 1; padding: 0.5rem; border-radius: 6px; background: var(--input-bg, #111); }
.history-signal.winner { border-left: 3px solid var(--accent, #6c63ff); }
.history-vs { font-size: 0.8rem; color: var(--text-secondary, #888); font-weight: 700; }
.history-label { font-weight: 700; color: var(--accent, #6c63ff); margin-right: 0.25rem; }
.history-meta { margin-top: 0.5rem; font-size: 0.8rem; color: var(--text-secondary, #888); display: flex; gap: 1rem; flex-wrap: wrap; }
.history-winner { color: var(--accent, #6c63ff); font-weight: 600; }
.history-comment { font-style: italic; }
```

- [ ] **Step 2: Update daily.sh**

Read current `prism/scheduling/daily.sh` and add source weight adjustment:

```bash
# Adjust source weights based on pairwise win rates
$VENV/bin/python -c "
from prism.config import settings
from prism.db import get_connection
from prism.web.pairwise import adjust_source_weights
conn = get_connection(settings.db_path)
adjust_source_weights(conn)
print('Source weights adjusted')
"
```

- [ ] **Step 3: Run full test suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: ALL PASS.

- [ ] **Step 4: Commit**

```bash
git add prism/web/static/style.css prism/scheduling/daily.sh
git commit -m "feat(pairwise): CSS styles + daily source weight adjustment cron"
```

- [ ] **Step 5: Manual verification — start server and test pairwise flow**

```bash
.venv/bin/prism serve --port 8080
```

Open http://localhost:8080/?tab=recommend — should see pairwise view with two signal cards.
