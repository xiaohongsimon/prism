# Prism Web Feed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an X-style personal AI news feed as a local web service with ranking, feedback, and auto-start.

**Architecture:** FastAPI serves Jinja2 HTML pages with HTMX for dynamic interactions. A ranking engine scores signals by combining heat, learned preference, and time decay. Feedback (like/dislike/save) updates preference weights in SQLite. A launchd plist auto-starts the service on boot.

**Tech Stack:** FastAPI, Jinja2, HTMX (CDN), vanilla CSS, SQLite

**Spec:** `docs/superpowers/specs/2026-03-29-prism-web-feed-design.md`

---

## File Structure

```
prism/
├── db.py                          # Modify: add feedback + preference_weights tables
├── api/app.py                     # Modify: mount web routes, add static/template dirs
├── web/
│   ├── __init__.py
│   ├── routes.py                  # New: frontend routes (/, /feed, /feedback, /channel/*)
│   ├── ranking.py                 # New: scoring engine (heat + preference + decay)
│   ├── static/
│   │   └── style.css              # New: X-style dark theme
│   └── templates/
│       ├── base.html              # New: base layout (head, nav, HTMX script)
│       ├── feed.html              # New: main page (tabs + feed container)
│       ├── channel.html           # New: channel detail page
│       └── partials/
│           ├── card.html          # New: single feed card fragment
│           └── card_actions.html  # New: action bar fragment (post-feedback swap)
├── scheduling/
│   └── com.prism.web.plist        # New: launchd auto-start config
tests/
├── web/
│   ├── __init__.py
│   ├── test_ranking.py            # New: ranking engine tests
│   ├── test_routes.py             # New: route integration tests
│   └── test_feedback.py           # New: feedback + preference update tests
```

---

### Task 1: DB Schema — feedback and preference_weights tables

**Files:**
- Modify: `prism/db.py` (add tables after entity system block, ~line 201)
- Test: `tests/web/__init__.py`, `tests/web/test_feedback.py`

- [ ] **Step 1: Create test directory and write failing test**

Create `tests/web/__init__.py` (empty) and `tests/web/test_feedback.py`:

```python
import sqlite3
from prism.db import init_db


def _fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def test_feedback_table_exists():
    conn = _fresh_db()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "feedback" in tables
    assert "preference_weights" in tables


def test_insert_feedback():
    conn = _fresh_db()
    # Create minimal source → raw_item → cluster → signal chain
    conn.execute("INSERT INTO sources (source_key, type) VALUES ('test:s', 'test')")
    conn.execute("INSERT INTO raw_items (source_id, url) VALUES (1, 'http://a')")
    conn.execute("INSERT INTO clusters (date, topic_label, item_count) VALUES ('2026-03-29', 'test', 1)")
    conn.execute("INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength) VALUES (1, 'test', 'noise', 3)")
    conn.execute("INSERT INTO feedback (signal_id, action) VALUES (1, 'like')")
    conn.commit()
    row = conn.execute("SELECT * FROM feedback WHERE signal_id = 1").fetchone()
    assert row["action"] == "like"
    assert row["created_at"] is not None


def test_preference_weights_upsert():
    conn = _fresh_db()
    conn.execute(
        "INSERT INTO preference_weights (dimension, key, weight) VALUES ('source', 'karpathy', 1.0)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO preference_weights (dimension, key, weight, updated_at) "
        "VALUES ('source', 'karpathy', 2.0, strftime('%Y-%m-%dT%H:%M:%S', 'now'))"
    )
    conn.commit()
    row = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='source' AND key='karpathy'"
    ).fetchone()
    assert row["weight"] == 2.0


def test_feedback_action_check_constraint():
    conn = _fresh_db()
    conn.execute("INSERT INTO sources (source_key, type) VALUES ('test:s', 'test')")
    conn.execute("INSERT INTO raw_items (source_id, url) VALUES (1, 'http://a')")
    conn.execute("INSERT INTO clusters (date, topic_label, item_count) VALUES ('2026-03-29', 'test', 1)")
    conn.execute("INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength) VALUES (1, 'test', 'noise', 3)")
    conn.commit()
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO feedback (signal_id, action) VALUES (1, 'invalid')")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/web/test_feedback.py -v`
Expected: FAIL — `feedback` table does not exist

- [ ] **Step 3: Add tables to init_db**

In `prism/db.py`, add after the `entity_profiles_ad` trigger (before the closing `"""`  of `init_db`), around line 201:

```sql
        -- Feedback & preference system
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL REFERENCES signals(id),
            action TEXT NOT NULL CHECK(action IN ('like', 'dislike', 'save')),
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_feedback_signal ON feedback(signal_id);
        CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at);

        CREATE TABLE IF NOT EXISTS preference_weights (
            dimension TEXT NOT NULL,
            key TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 0.0,
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
            PRIMARY KEY (dimension, key)
        );
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/web/test_feedback.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add prism/db.py tests/web/__init__.py tests/web/test_feedback.py
git commit -m "feat(db): add feedback and preference_weights tables"
```

---

### Task 2: Ranking Engine

**Files:**
- Create: `prism/web/__init__.py`, `prism/web/ranking.py`
- Test: `tests/web/test_ranking.py`

- [ ] **Step 1: Write failing tests**

Create `prism/web/__init__.py` (empty) and `tests/web/test_ranking.py`:

```python
import math
import sqlite3
from prism.db import init_db
from prism.web.ranking import compute_feed, update_preferences


def _fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _seed(conn):
    """Insert 3 signals with different strengths and sources."""
    conn.execute("INSERT INTO sources (source_key, type, handle) VALUES ('x:karpathy', 'x', 'karpathy')")
    conn.execute("INSERT INTO sources (source_key, type, handle) VALUES ('hn:best', 'hackernews', '')")

    conn.execute("INSERT INTO raw_items (source_id, url, title, published_at) VALUES (1, 'http://a', 'A', '2026-03-29T06:00:00')")
    conn.execute("INSERT INTO raw_items (source_id, url, title, published_at) VALUES (2, 'http://b', 'B', '2026-03-29T03:00:00')")
    conn.execute("INSERT INTO raw_items (source_id, url, title, published_at) VALUES (1, 'http://c', 'C', '2026-03-28T12:00:00')")

    conn.execute("INSERT INTO clusters (date, topic_label, item_count) VALUES ('2026-03-29', 'GPT-5', 3)")
    conn.execute("INSERT INTO clusters (date, topic_label, item_count) VALUES ('2026-03-29', 'vLLM', 1)")
    conn.execute("INSERT INTO clusters (date, topic_label, item_count) VALUES ('2026-03-28', 'Old Topic', 1)")

    conn.execute("INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (1, 1)")
    conn.execute("INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (2, 2)")
    conn.execute("INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (3, 3)")

    conn.execute("INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength, tags_json, is_current) VALUES (1, 'GPT-5 leak', 'actionable', 5, '[\"gpt\",\"benchmark\"]', 1)")
    conn.execute("INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength, tags_json, is_current) VALUES (2, 'vLLM release', 'strategic', 3, '[\"vllm\",\"infra\"]', 1)")
    conn.execute("INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength, tags_json, is_current) VALUES (3, 'Old stuff', 'noise', 1, '[]', 1)")

    conn.execute("INSERT INTO trends (topic_label, date, heat_score, is_current) VALUES ('GPT-5', '2026-03-29', 15.0, 1)")
    conn.execute("INSERT INTO trends (topic_label, date, heat_score, is_current) VALUES ('vLLM', '2026-03-29', 3.0, 1)")
    conn.commit()


def test_compute_feed_returns_sorted_by_score():
    conn = _fresh_db()
    _seed(conn)
    items = compute_feed(conn, tab="hot", page=1, per_page=10)
    assert len(items) >= 2
    scores = [it["score"] for it in items]
    assert scores == sorted(scores, reverse=True)


def test_compute_feed_recommend_tab_uses_preference():
    conn = _fresh_db()
    _seed(conn)
    # Boost 'vllm' tag so vLLM signal rises above GPT-5
    conn.execute("INSERT INTO preference_weights (dimension, key, weight) VALUES ('tag', 'vllm', 10.0)")
    conn.commit()
    items = compute_feed(conn, tab="recommend", page=1, per_page=10)
    assert items[0]["topic_label"] == "vLLM"


def test_compute_feed_follow_tab_filters_sources():
    conn = _fresh_db()
    _seed(conn)
    # Disable hn:best source
    conn.execute("UPDATE sources SET enabled = 0 WHERE source_key = 'hn:best'")
    conn.commit()
    items = compute_feed(conn, tab="follow", page=1, per_page=10)
    source_keys = set()
    for it in items:
        for sk in it.get("source_keys", []):
            source_keys.add(sk)
    assert "hn:best" not in source_keys


def test_compute_feed_pagination():
    conn = _fresh_db()
    _seed(conn)
    page1 = compute_feed(conn, tab="hot", page=1, per_page=1)
    page2 = compute_feed(conn, tab="hot", page=2, per_page=1)
    assert len(page1) == 1
    assert len(page2) >= 1
    assert page1[0]["signal_id"] != page2[0]["signal_id"]


def test_update_preferences_like():
    conn = _fresh_db()
    _seed(conn)
    update_preferences(conn, signal_id=1, action="like")
    row = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='layer' AND key='actionable'"
    ).fetchone()
    assert row is not None
    assert row["weight"] == 1.0


def test_update_preferences_dislike():
    conn = _fresh_db()
    _seed(conn)
    update_preferences(conn, signal_id=2, action="dislike")
    row = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='tag' AND key='vllm'"
    ).fetchone()
    assert row is not None
    assert row["weight"] == -1.0


def test_update_preferences_save():
    conn = _fresh_db()
    _seed(conn)
    update_preferences(conn, signal_id=1, action="save")
    row = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='tag' AND key='gpt'"
    ).fetchone()
    assert row is not None
    assert row["weight"] == 2.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/web/test_ranking.py -v`
Expected: FAIL — `ImportError: cannot import name 'compute_feed' from 'prism.web.ranking'`

- [ ] **Step 3: Implement ranking engine**

Create `prism/web/ranking.py`:

```python
"""Feed ranking engine: heat + preference + time decay."""

import json
import math
import sqlite3
from datetime import datetime, timezone


# Score weights per tab: (heat, preference, decay)
TAB_WEIGHTS = {
    "recommend": (0.4, 0.4, 0.2),
    "follow":    (0.2, 0.5, 0.3),
    "hot":       (0.6, 0.0, 0.4),
}

HALF_LIFE_HOURS = 24.0

# Feedback deltas per action
ACTION_DELTAS = {"like": 1.0, "dislike": -1.0, "save": 2.0}


def _time_decay(published_at: str | None) -> float:
    """Exponential decay based on age in hours."""
    if not published_at:
        return 0.5
    try:
        pub = datetime.fromisoformat(published_at)
    except (ValueError, TypeError):
        return 0.5
    now = datetime.now(timezone.utc)
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    age_hours = max((now - pub).total_seconds() / 3600, 0)
    return math.exp(-age_hours / HALF_LIFE_HOURS)


def _load_preference_map(conn: sqlite3.Connection) -> dict[tuple[str, str], float]:
    """Load all preference weights into a dict keyed by (dimension, key)."""
    rows = conn.execute("SELECT dimension, key, weight FROM preference_weights").fetchall()
    return {(r["dimension"], r["key"]): r["weight"] for r in rows}


def _preference_score(pref_map: dict, signal_row: dict) -> float:
    """Compute preference score for a signal from weighted dimensions."""
    total = 0.0
    # Source dimension
    for sk in signal_row.get("source_keys", []):
        total += pref_map.get(("source", sk), 0.0)
    # Tag dimension
    for tag in signal_row.get("tags", []):
        total += pref_map.get(("tag", tag), 0.0)
    # Layer dimension
    total += pref_map.get(("layer", signal_row.get("signal_layer", "")), 0.0)
    # Sigmoid normalization to 0-1
    return 1.0 / (1.0 + math.exp(-total))


def compute_feed(
    conn: sqlite3.Connection,
    tab: str = "recommend",
    page: int = 1,
    per_page: int = 20,
) -> list[dict]:
    """Compute ranked feed items for a tab."""
    w_heat, w_pref, w_decay = TAB_WEIGHTS.get(tab, TAB_WEIGHTS["recommend"])

    # Load signals joined with cluster and source info
    rows = conn.execute(
        """
        SELECT s.id AS signal_id, s.cluster_id, s.summary, s.signal_layer,
               s.signal_strength, s.why_it_matters, s.tags_json, s.created_at,
               c.topic_label, c.item_count, c.date AS cluster_date
        FROM signals s
        JOIN clusters c ON s.cluster_id = c.id
        WHERE s.is_current = 1
        ORDER BY s.created_at DESC
        """
    ).fetchall()

    if not rows:
        return []

    # Max heat for normalization
    max_heat = max(
        (r["signal_strength"] * r["item_count"] for r in rows), default=1.0
    ) or 1.0

    # Load preference map
    pref_map = _load_preference_map(conn) if w_pref > 0 else {}

    # Load source keys for each cluster
    cluster_sources: dict[int, list[str]] = {}
    source_rows = conn.execute(
        """
        SELECT ci.cluster_id, src.source_key, src.enabled
        FROM cluster_items ci
        JOIN raw_items ri ON ri.id = ci.raw_item_id
        JOIN sources src ON src.id = ri.source_id
        """
    ).fetchall()
    enabled_sources: set[str] = set()
    for sr in source_rows:
        cluster_sources.setdefault(sr["cluster_id"], [])
        if sr["source_key"] not in cluster_sources[sr["cluster_id"]]:
            cluster_sources[sr["cluster_id"]].append(sr["source_key"])
        if sr["enabled"]:
            enabled_sources.add(sr["source_key"])

    # Build scored items
    items = []
    for r in rows:
        tags = []
        try:
            tags = json.loads(r["tags_json"]) if r["tags_json"] else []
        except (json.JSONDecodeError, TypeError):
            pass

        source_keys = cluster_sources.get(r["cluster_id"], [])

        # Follow tab: skip clusters with no enabled sources
        if tab == "follow":
            if not any(sk in enabled_sources for sk in source_keys):
                continue

        item = {
            "signal_id": r["signal_id"],
            "cluster_id": r["cluster_id"],
            "topic_label": r["topic_label"],
            "summary": r["summary"],
            "signal_layer": r["signal_layer"],
            "signal_strength": r["signal_strength"],
            "why_it_matters": r["why_it_matters"],
            "item_count": r["item_count"],
            "tags": tags,
            "source_keys": source_keys,
            "cluster_date": r["cluster_date"],
            "created_at": r["created_at"],
        }

        heat_norm = (r["signal_strength"] * r["item_count"]) / max_heat
        pref = _preference_score(pref_map, item) if w_pref > 0 else 0.5
        decay = _time_decay(r["created_at"])

        item["score"] = w_heat * heat_norm + w_pref * pref + w_decay * decay
        items.append(item)

    items.sort(key=lambda x: x["score"], reverse=True)

    # Pagination
    start = (page - 1) * per_page
    return items[start : start + per_page]


def update_preferences(conn: sqlite3.Connection, signal_id: int, action: str) -> None:
    """Update preference weights based on user feedback on a signal."""
    delta = ACTION_DELTAS.get(action, 0.0)
    if delta == 0.0:
        return

    # Get signal details
    row = conn.execute(
        "SELECT s.signal_layer, s.tags_json, c.topic_label "
        "FROM signals s JOIN clusters c ON s.cluster_id = c.id "
        "WHERE s.id = ?",
        (signal_id,),
    ).fetchone()
    if not row:
        return

    # Get source keys for this signal's cluster
    sources = conn.execute(
        "SELECT DISTINCT src.source_key "
        "FROM cluster_items ci "
        "JOIN raw_items ri ON ri.id = ci.raw_item_id "
        "JOIN sources src ON src.id = ri.source_id "
        "WHERE ci.cluster_id = (SELECT cluster_id FROM signals WHERE id = ?)",
        (signal_id,),
    ).fetchall()

    keys_to_update: list[tuple[str, str]] = []

    # Layer dimension
    keys_to_update.append(("layer", row["signal_layer"]))

    # Tag dimension
    tags = []
    try:
        tags = json.loads(row["tags_json"]) if row["tags_json"] else []
    except (json.JSONDecodeError, TypeError):
        pass
    for tag in tags:
        keys_to_update.append(("tag", tag))

    # Source dimension
    for src in sources:
        keys_to_update.append(("source", src["source_key"]))

    # Upsert weights
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
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/web/test_ranking.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add prism/web/__init__.py prism/web/ranking.py tests/web/test_ranking.py
git commit -m "feat(web): ranking engine with heat + preference + time decay"
```

---

### Task 3: CSS Theme and Base Templates

**Files:**
- Create: `prism/web/static/style.css`
- Create: `prism/web/templates/base.html`
- Create: `prism/web/templates/partials/card.html`
- Create: `prism/web/templates/partials/card_actions.html`
- Create: `prism/web/templates/feed.html`
- Create: `prism/web/templates/channel.html`

No tests for this task — pure static assets. Tested via integration in Task 4.

- [ ] **Step 1: Create static CSS**

Create `prism/web/static/style.css`:

```css
/* Prism Feed — X-style dark theme */
* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    background: #000;
    color: #e7e9ea;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 15px;
    line-height: 1.45;
}

a { color: #1d9bf0; text-decoration: none; }
a:hover { text-decoration: underline; }

/* Layout */
.container {
    max-width: 600px;
    margin: 0 auto;
    border-left: 1px solid #2f3336;
    border-right: 1px solid #2f3336;
    min-height: 100vh;
}

/* Top nav */
.top-nav {
    position: sticky;
    top: 0;
    background: rgba(0, 0, 0, 0.85);
    backdrop-filter: blur(12px);
    border-bottom: 1px solid #2f3336;
    padding: 12px 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    z-index: 10;
}

.top-nav .logo {
    font-size: 18px;
    font-weight: 700;
}

.tabs {
    display: flex;
    gap: 4px;
}

.tab {
    padding: 6px 14px;
    font-size: 13px;
    color: #71767b;
    cursor: pointer;
    border-bottom: 2px solid transparent;
    background: none;
    border-top: none;
    border-left: none;
    border-right: none;
    font-family: inherit;
}

.tab:hover { color: #e7e9ea; }
.tab.active { color: #e7e9ea; font-weight: 600; border-bottom-color: #1d9bf0; }

.settings-btn {
    font-size: 14px;
    color: #71767b;
    cursor: pointer;
    background: none;
    border: none;
}

/* Feed card */
.card {
    border-bottom: 1px solid #2f3336;
    padding: 12px 16px;
    display: flex;
    gap: 10px;
    transition: background 0.15s;
}
.card:hover { background: #080808; }

.card-icon {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 16px;
    flex-shrink: 0;
}
.card-icon.actionable { background: #1d3a5c; }
.card-icon.strategic  { background: #1a3a2a; }
.card-icon.noise      { background: #2d2d44; }
.card-icon.paper      { background: #2d2520; }

.card-body { flex: 1; min-width: 0; }

.card-title {
    font-size: 15px;
    font-weight: 700;
    margin-bottom: 2px;
}

.card-meta {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 8px;
    font-size: 13px;
    color: #71767b;
    flex-wrap: wrap;
}

.card-meta .dot { color: #2f3336; }

.badge {
    padding: 1px 8px;
    border-radius: 12px;
    font-size: 12px;
    margin-left: 4px;
}
.badge.actionable { background: #1a2733; color: #1d9bf0; }
.badge.strategic  { background: #1a2e1a; color: #00ba7c; }
.badge.noise      { background: #2d2d2d; color: #71767b; }

.card-summary {
    margin-bottom: 8px;
    line-height: 1.5;
}

.card-sources {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 10px;
}

.source-pill {
    background: #1a2733;
    color: #1d9bf0;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 12px;
    cursor: pointer;
    border: none;
    font-family: inherit;
}
.source-pill:hover { background: #22303c; }

/* Action bar */
.actions {
    display: flex;
    gap: 24px;
    max-width: 300px;
}

.action-btn {
    display: flex;
    align-items: center;
    gap: 4px;
    color: #71767b;
    font-size: 13px;
    cursor: pointer;
    padding: 4px;
    border-radius: 50%;
    background: none;
    border: none;
    font-family: inherit;
    transition: color 0.15s;
}
.action-btn:hover { color: #e7e9ea; }
.action-btn.liked    { color: #1d9bf0; }
.action-btn.disliked { color: #f4212e; }
.action-btn.saved    { color: #ffd400; }

/* Channel page */
.channel-header {
    padding: 16px;
    border-bottom: 1px solid #2f3336;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.channel-header h2 {
    font-size: 18px;
    font-weight: 700;
}

.follow-btn, .unfollow-btn {
    padding: 6px 16px;
    border-radius: 20px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid #536471;
    font-family: inherit;
}
.follow-btn { background: #e7e9ea; color: #0f1419; }
.follow-btn:hover { background: #d7dbdc; }
.unfollow-btn { background: transparent; color: #e7e9ea; }
.unfollow-btn:hover { background: #200; border-color: #67000d; color: #f4212e; }

/* Loading indicator */
.loading {
    text-align: center;
    padding: 20px;
    color: #71767b;
    font-size: 14px;
}

/* Empty state */
.empty {
    text-align: center;
    padding: 40px 16px;
    color: #71767b;
}
```

- [ ] **Step 2: Create base.html template**

Create `prism/web/templates/base.html`:

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Prism</title>
    <link rel="stylesheet" href="/static/style.css">
    <script src="https://unpkg.com/htmx.org@2.0.4"></script>
</head>
<body>
    <div class="container">
        {% block content %}{% endblock %}
    </div>
</body>
</html>
```

- [ ] **Step 3: Create card partial**

Create `prism/web/templates/partials/card.html`:

```html
<div class="card" id="card-{{ item.signal_id }}">
    <div class="card-icon {{ item.signal_layer }}">
        {% if item.signal_layer == 'actionable' %}🔥
        {% elif item.signal_layer == 'strategic' %}📡
        {% else %}📄{% endif %}
    </div>
    <div class="card-body">
        <div class="card-title">{{ item.topic_label }}</div>
        <div class="card-meta">
            <span>热度 {{ item.signal_strength * item.item_count }}</span>
            <span class="dot">·</span>
            <span>{{ item.cluster_date }}</span>
            <span class="dot">·</span>
            <span>{{ item.item_count }} sources</span>
            <span class="badge {{ item.signal_layer }}">{{ item.signal_layer }}</span>
        </div>
        <div class="card-summary">{{ item.summary }}</div>
        <div class="card-sources">
            {% for sk in item.source_keys %}
            <a class="source-pill" href="/channel/{{ sk }}">{{ sk }}</a>
            {% endfor %}
        </div>
        <div id="actions-{{ item.signal_id }}">
            {% include "partials/card_actions.html" %}
        </div>
    </div>
</div>
```

- [ ] **Step 4: Create card_actions partial**

Create `prism/web/templates/partials/card_actions.html`:

```html
<div class="actions">
    <button class="action-btn {{ 'liked' if feedback_state == 'like' else '' }}"
            hx-post="/feedback"
            hx-vals='{"signal_id": {{ item.signal_id }}, "action": "like"}'
            hx-target="#actions-{{ item.signal_id }}"
            hx-swap="innerHTML">👍</button>
    <button class="action-btn {{ 'disliked' if feedback_state == 'dislike' else '' }}"
            hx-post="/feedback"
            hx-vals='{"signal_id": {{ item.signal_id }}, "action": "dislike"}'
            hx-target="#actions-{{ item.signal_id }}"
            hx-swap="innerHTML">👎</button>
    <button class="action-btn {{ 'saved' if feedback_state == 'save' else '' }}"
            hx-post="/feedback"
            hx-vals='{"signal_id": {{ item.signal_id }}, "action": "save"}'
            hx-target="#actions-{{ item.signal_id }}"
            hx-swap="innerHTML">⭐</button>
    <a class="action-btn" href="{{ item.source_keys[0] if item.source_keys else '#' }}" target="_blank">🔗</a>
</div>
```

- [ ] **Step 5: Create feed.html template**

Create `prism/web/templates/feed.html`:

```html
{% extends "base.html" %}

{% block content %}
<div class="top-nav">
    <div class="logo">Prism</div>
    <div class="tabs">
        <button class="tab {{ 'active' if tab == 'recommend' }}"
                hx-get="/feed?tab=recommend"
                hx-target="#feed-list"
                hx-push-url="/?tab=recommend">推荐</button>
        <button class="tab {{ 'active' if tab == 'follow' }}"
                hx-get="/feed?tab=follow"
                hx-target="#feed-list"
                hx-push-url="/?tab=follow">关注</button>
        <button class="tab {{ 'active' if tab == 'hot' }}"
                hx-get="/feed?tab=hot"
                hx-target="#feed-list"
                hx-push-url="/?tab=hot">热门</button>
    </div>
    <div class="settings-btn">⚙️</div>
</div>

<div id="feed-list">
    {% for item in items %}
        {% include "partials/card.html" %}
    {% endfor %}

    {% if items | length >= per_page %}
    <div class="loading"
         hx-get="/feed?tab={{ tab }}&page={{ page + 1 }}"
         hx-trigger="revealed"
         hx-swap="outerHTML">
        加载更多...
    </div>
    {% endif %}

    {% if not items %}
    <div class="empty">暂无内容</div>
    {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 6: Create channel.html template**

Create `prism/web/templates/channel.html`:

```html
{% extends "base.html" %}

{% block content %}
<div class="top-nav">
    <a class="logo" href="/" style="text-decoration:none;color:#e7e9ea">← Prism</a>
</div>

<div class="channel-header">
    <h2>{{ source_key }}</h2>
    {% if enabled %}
    <button class="unfollow-btn"
            hx-post="/channel/{{ source_key }}/unfollow"
            hx-swap="outerHTML">取消关注</button>
    {% else %}
    <button class="follow-btn"
            hx-post="/channel/{{ source_key }}/follow"
            hx-swap="outerHTML">关注</button>
    {% endif %}
</div>

<div id="feed-list">
    {% for item in items %}
        {% include "partials/card.html" %}
    {% endfor %}

    {% if not items %}
    <div class="empty">该频道暂无内容</div>
    {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 7: Commit**

```bash
git add prism/web/static/style.css prism/web/templates/
git commit -m "feat(web): X-style dark theme CSS and Jinja2 templates"
```

---

### Task 4: Web Routes

**Files:**
- Create: `prism/web/routes.py`
- Modify: `prism/api/app.py` (mount web router + static + templates)
- Test: `tests/web/test_routes.py`

- [ ] **Step 1: Write failing tests**

Create `tests/web/test_routes.py`:

```python
import sqlite3
from fastapi.testclient import TestClient
from prism.db import init_db
from prism.api.app import create_app


def _test_client():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    # Seed data
    conn.execute("INSERT INTO sources (source_key, type, handle) VALUES ('x:karpathy', 'x', 'karpathy')")
    conn.execute("INSERT INTO raw_items (source_id, url, title, published_at) VALUES (1, 'http://a', 'A', '2026-03-29T06:00:00')")
    conn.execute("INSERT INTO clusters (date, topic_label, item_count) VALUES ('2026-03-29', 'GPT-5 Leak', 1)")
    conn.execute("INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (1, 1)")
    conn.execute("INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength, tags_json, is_current) VALUES (1, 'GPT-5 benchmark', 'actionable', 5, '[\"gpt\"]', 1)")
    conn.execute("INSERT INTO trends (topic_label, date, heat_score, is_current) VALUES ('GPT-5 Leak', '2026-03-29', 5.0, 1)")
    conn.commit()
    app = create_app(conn=conn)
    return TestClient(app)


def test_index_returns_html():
    client = _test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Prism" in resp.text
    assert "GPT-5" in resp.text


def test_feed_fragment_returns_cards():
    client = _test_client()
    resp = client.get("/feed?tab=hot&page=1")
    assert resp.status_code == 200
    assert "card" in resp.text
    assert "GPT-5" in resp.text


def test_feedback_post():
    client = _test_client()
    resp = client.post("/feedback", data={"signal_id": "1", "action": "like"})
    assert resp.status_code == 200
    assert "liked" in resp.text


def test_channel_page():
    client = _test_client()
    resp = client.get("/channel/x:karpathy")
    assert resp.status_code == 200
    assert "x:karpathy" in resp.text


def test_channel_unfollow():
    client = _test_client()
    resp = client.post("/channel/x:karpathy/unfollow")
    assert resp.status_code == 200
    assert "关注" in resp.text  # Button should now say "关注" (follow)


def test_channel_follow():
    client = _test_client()
    # First unfollow
    client.post("/channel/x:karpathy/unfollow")
    # Then follow again
    resp = client.post("/channel/x:karpathy/follow")
    assert resp.status_code == 200
    assert "取消关注" in resp.text


def test_static_css():
    client = _test_client()
    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    assert "background" in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/web/test_routes.py -v`
Expected: FAIL — routes not mounted, 404s

- [ ] **Step 3: Create web routes**

Create `prism/web/routes.py`:

```python
"""Frontend web routes for the Prism feed UI."""

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader

from prism.web.ranking import compute_feed, update_preferences

TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)

web_router = APIRouter()


def _db(request: Request) -> sqlite3.Connection:
    return request.state.db


def _feedback_map(conn: sqlite3.Connection, signal_ids: list[int]) -> dict[int, str]:
    """Get latest feedback action for each signal_id."""
    if not signal_ids:
        return {}
    placeholders = ",".join("?" * len(signal_ids))
    rows = conn.execute(
        f"SELECT signal_id, action FROM feedback "
        f"WHERE signal_id IN ({placeholders}) "
        f"ORDER BY created_at ASC",
        signal_ids,
    ).fetchall()
    # Last action wins
    return {r["signal_id"]: r["action"] for r in rows}


@web_router.get("/", response_class=HTMLResponse)
def index(request: Request, tab: str = "recommend"):
    conn = _db(request)
    items = compute_feed(conn, tab=tab, page=1, per_page=20)
    fb = _feedback_map(conn, [it["signal_id"] for it in items])
    template = _env.get_template("feed.html")
    html = template.render(
        items=items, tab=tab, page=1, per_page=20,
        feedback_map=fb,
    )
    return HTMLResponse(html)


@web_router.get("/feed", response_class=HTMLResponse)
def feed_fragment(request: Request, tab: str = "recommend", page: int = 1, per_page: int = 20):
    conn = _db(request)
    items = compute_feed(conn, tab=tab, page=page, per_page=per_page)
    fb = _feedback_map(conn, [it["signal_id"] for it in items])
    parts = []
    card_tpl = _env.get_template("partials/card.html")
    for item in items:
        parts.append(card_tpl.render(item=item, feedback_state=fb.get(item["signal_id"])))

    # Append infinite scroll trigger if full page
    if len(items) >= per_page:
        parts.append(
            f'<div class="loading" '
            f'hx-get="/feed?tab={tab}&page={page + 1}&per_page={per_page}" '
            f'hx-trigger="revealed" hx-swap="outerHTML">加载更多...</div>'
        )
    return HTMLResponse("".join(parts))


@web_router.post("/feedback", response_class=HTMLResponse)
def post_feedback(request: Request, signal_id: int = Form(...), action: str = Form(...)):
    conn = _db(request)
    # Record feedback
    conn.execute("INSERT INTO feedback (signal_id, action) VALUES (?, ?)", (signal_id, action))
    conn.commit()
    # Update preference weights
    update_preferences(conn, signal_id=signal_id, action=action)
    # Return updated action bar
    signal = conn.execute(
        "SELECT s.*, c.topic_label FROM signals s JOIN clusters c ON s.cluster_id = c.id WHERE s.id = ?",
        (signal_id,),
    ).fetchone()
    source_keys = [r["source_key"] for r in conn.execute(
        "SELECT DISTINCT src.source_key FROM cluster_items ci "
        "JOIN raw_items ri ON ri.id = ci.raw_item_id "
        "JOIN sources src ON src.id = ri.source_id "
        "WHERE ci.cluster_id = ?", (signal["cluster_id"],),
    ).fetchall()]
    item = {"signal_id": signal_id, "source_keys": source_keys}
    tpl = _env.get_template("partials/card_actions.html")
    return HTMLResponse(tpl.render(item=item, feedback_state=action))


@web_router.get("/channel/{source_key:path}", response_class=HTMLResponse)
def channel_page(request: Request, source_key: str):
    conn = _db(request)
    source = conn.execute("SELECT * FROM sources WHERE source_key = ?", (source_key,)).fetchone()
    enabled = source["enabled"] if source else False

    # Get signals from this source's clusters
    rows = conn.execute(
        """
        SELECT DISTINCT s.id AS signal_id, s.cluster_id, s.summary, s.signal_layer,
               s.signal_strength, s.tags_json, s.created_at, s.why_it_matters,
               c.topic_label, c.item_count, c.date AS cluster_date
        FROM signals s
        JOIN clusters c ON s.cluster_id = c.id
        JOIN cluster_items ci ON ci.cluster_id = c.id
        JOIN raw_items ri ON ri.id = ci.raw_item_id
        JOIN sources src ON src.id = ri.source_id
        WHERE src.source_key = ? AND s.is_current = 1
        ORDER BY s.created_at DESC
        LIMIT 50
        """,
        (source_key,),
    ).fetchall()

    import json
    items = []
    for r in rows:
        tags = []
        try:
            tags = json.loads(r["tags_json"]) if r["tags_json"] else []
        except (json.JSONDecodeError, TypeError):
            pass
        items.append({
            "signal_id": r["signal_id"],
            "cluster_id": r["cluster_id"],
            "topic_label": r["topic_label"],
            "summary": r["summary"],
            "signal_layer": r["signal_layer"],
            "signal_strength": r["signal_strength"],
            "why_it_matters": r["why_it_matters"],
            "item_count": r["item_count"],
            "tags": tags,
            "source_keys": [source_key],
            "cluster_date": r["cluster_date"],
            "created_at": r["created_at"],
        })

    fb = _feedback_map(conn, [it["signal_id"] for it in items])
    template = _env.get_template("channel.html")
    return HTMLResponse(template.render(
        source_key=source_key, enabled=enabled, items=items,
        feedback_map=fb,
    ))


@web_router.post("/channel/{source_key:path}/unfollow", response_class=HTMLResponse)
def unfollow(request: Request, source_key: str):
    conn = _db(request)
    conn.execute(
        "UPDATE sources SET enabled = 0, disabled_reason = 'manual' WHERE source_key = ?",
        (source_key,),
    )
    conn.commit()
    return HTMLResponse(
        f'<button class="follow-btn" '
        f'hx-post="/channel/{source_key}/follow" hx-swap="outerHTML">关注</button>'
    )


@web_router.post("/channel/{source_key:path}/follow", response_class=HTMLResponse)
def follow(request: Request, source_key: str):
    conn = _db(request)
    conn.execute(
        "UPDATE sources SET enabled = 1, disabled_reason = NULL WHERE source_key = ?",
        (source_key,),
    )
    conn.commit()
    return HTMLResponse(
        f'<button class="unfollow-btn" '
        f'hx-post="/channel/{source_key}/unfollow" hx-swap="outerHTML">取消关注</button>'
    )
```

- [ ] **Step 4: Modify app.py to mount web routes and static files**

Replace the entire content of `prism/api/app.py`:

```python
"""FastAPI application factory."""

import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from prism.api.routes import router
from prism.web.routes import web_router

STATIC_DIR = Path(__file__).parent.parent / "web" / "static"


def create_app(conn: Optional[sqlite3.Connection] = None) -> FastAPI:
    """Create FastAPI app, optionally injecting a DB connection (for testing)."""
    app = FastAPI(title="Prism", version="1.0")

    if conn is not None:
        app.state.db = conn
    else:
        from prism.config import settings
        from prism.db import get_connection
        app.state.db = get_connection(settings.db_path)

    @app.middleware("http")
    async def db_middleware(request: Request, call_next):
        request.state.db = app.state.db
        return await call_next(request)

    # Static files
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # API routes (existing)
    app.include_router(router, prefix="/api")

    # Web frontend routes
    app.include_router(web_router)

    return app
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/web/test_routes.py -v`
Expected: All 7 tests PASS

- [ ] **Step 6: Run all existing tests to check for regressions**

Run: `.venv/bin/pytest tests/ -v`
Expected: All tests PASS (existing + new)

- [ ] **Step 7: Commit**

```bash
git add prism/web/routes.py prism/api/app.py tests/web/test_routes.py
git commit -m "feat(web): frontend routes with HTMX feed, feedback, and channel management"
```

---

### Task 5: Fix card template feedback_state rendering

The card partial uses `feedback_state` but in `feed.html` we pass `feedback_map`. We need the card partial to look up from the map, or have the feed template set the variable per card.

**Files:**
- Modify: `prism/web/templates/feed.html`
- Modify: `prism/web/templates/channel.html`

- [ ] **Step 1: Update feed.html to pass feedback_state per card**

Replace the card loop in `prism/web/templates/feed.html`:

```html
    {% for item in items %}
        {% set feedback_state = feedback_map.get(item.signal_id) if feedback_map else None %}
        {% include "partials/card.html" %}
    {% endfor %}
```

- [ ] **Step 2: Update channel.html to pass feedback_state per card**

Replace the card loop in `prism/web/templates/channel.html`:

```html
    {% for item in items %}
        {% set feedback_state = feedback_map.get(item.signal_id) if feedback_map else None %}
        {% include "partials/card.html" %}
    {% endfor %}
```

- [ ] **Step 3: Run tests**

Run: `.venv/bin/pytest tests/web/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add prism/web/templates/feed.html prism/web/templates/channel.html
git commit -m "fix(web): pass feedback_state per card from feedback_map"
```

---

### Task 6: Launchd Auto-Start Configuration

**Files:**
- Create: `prism/scheduling/com.prism.web.plist`

- [ ] **Step 1: Create plist file**

Create `prism/scheduling/com.prism.web.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.prism.web</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/leehom/work/prism/.venv/bin/prism</string>
        <string>serve</string>
        <string>--port</string>
        <string>8000</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/leehom/work/prism</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/leehom/work/prism/data/web.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/leehom/work/prism/data/web.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/Users/leehom/work/prism/.venv/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
```

- [ ] **Step 2: Install and load the plist**

```bash
cp prism/scheduling/com.prism.web.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.prism.web.plist
```

- [ ] **Step 3: Verify the service is running**

```bash
launchctl list | grep prism.web
curl -s http://localhost:8000/ | head -5
```

Expected: Process running, HTML response with "Prism"

- [ ] **Step 4: Commit**

```bash
git add prism/scheduling/com.prism.web.plist
git commit -m "feat(scheduling): launchd plist for web service auto-start"
```

---

### Task 7: Smoke Test — End-to-End Verification

No new files. Verify everything works together against the real database.

- [ ] **Step 1: Run full test suite**

```bash
.venv/bin/pytest tests/ -v
```

Expected: All tests PASS

- [ ] **Step 2: Start server manually and test in browser**

```bash
.venv/bin/prism serve --port 8000
```

Open `http://localhost:8000/` in browser. Verify:
- Feed loads with real signals
- Tabs switch (推荐/关注/热门)
- Cards display with source pills
- 👍/👎/⭐ buttons work (check DB for feedback rows)
- Source pill links to channel page
- Channel page shows unfollow button

- [ ] **Step 3: Verify launchd service**

```bash
launchctl list | grep prism.web
curl -s http://localhost:8000/ | grep Prism
```

- [ ] **Step 4: Final commit**

```bash
git commit --allow-empty -m "chore: smoke test passed for web feed"
```
