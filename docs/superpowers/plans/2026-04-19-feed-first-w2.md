# Feed-First W2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace pairwise as the default interaction with a feed + explicit multi-dimensional feedback (save / dismiss / follow-author / mute-topic). Pairwise survives as a `/pairwise` calibration tool.

**Architecture:** New `feed_interactions` event table + new feed routes (`/feed`, `/feed/action`, `/feed/more`, `/feed/saved`) + reuse of existing `_get_candidate_pool`, `_update_preference_weights`, `signal_scores` and `preference_weights`. Zero new LLM calls in the request path.

**Tech Stack:** FastAPI, Jinja2, HTMX, vanilla CSS, SQLite. Same as W1.

**Reference spec:** `docs/superpowers/specs/2026-04-19-feed-first-w2-design.md`

---

## Task 1: Schema — `feed_interactions` table

**Files:**
- Modify: `prism/db.py` (add table to `init_db`)
- Test: `tests/test_feed_interactions_schema.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_feed_interactions_schema.py`:

```python
import sqlite3

from prism.db import init_db


def test_feed_interactions_table_exists():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='feed_interactions'"
    ).fetchone()
    assert row is not None


def test_feed_interactions_accepts_insert():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    conn.execute(
        "INSERT INTO feed_interactions (signal_id, action, target_key) "
        "VALUES (1, 'save', '')"
    )
    conn.execute(
        "INSERT INTO feed_interactions (signal_id, action, target_key) "
        "VALUES (0, 'follow_author', 'karpathy')"
    )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM feed_interactions").fetchone()[0]
    assert n == 2


def test_feed_interactions_indexes_exist():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    idx = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='feed_interactions'"
    ).fetchall()}
    assert "idx_feed_interactions_signal" in idx
    assert "idx_feed_interactions_action_created" in idx
```

- [ ] **Step 2: Run test to confirm it fails**

```
.venv/bin/pytest tests/test_feed_interactions_schema.py -v
```
Expected: FAIL — table doesn't exist.

- [ ] **Step 3: Add the table to `prism/db.py`**

Inside `init_db`, alongside other `CREATE TABLE IF NOT EXISTS` blocks, add:

```python
conn.execute(
    """CREATE TABLE IF NOT EXISTS feed_interactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        target_key TEXT NOT NULL DEFAULT '',
        response_time_ms INTEGER NOT NULL DEFAULT 0,
        context_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )"""
)
conn.execute(
    "CREATE INDEX IF NOT EXISTS idx_feed_interactions_signal "
    "ON feed_interactions(signal_id)"
)
conn.execute(
    "CREATE INDEX IF NOT EXISTS idx_feed_interactions_action_created "
    "ON feed_interactions(action, created_at)"
)
```

- [ ] **Step 4: Run test to confirm pass**

```
.venv/bin/pytest tests/test_feed_interactions_schema.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add prism/db.py tests/test_feed_interactions_schema.py
git commit -m "feat(db): add feed_interactions event table"
```

---

## Task 2: `record_feed_action` — save / dismiss handlers

**Files:**
- Create: `prism/web/feed.py` (new module for feed logic)
- Test: `tests/test_feed_action.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_feed_action.py`:

```python
import sqlite3

from prism.db import init_db


def _seed(conn):
    conn.execute("INSERT INTO clusters (id, date, topic_label) VALUES (1, '2026-04-19', 'AI')")
    conn.execute(
        "INSERT INTO signals (id, cluster_id, summary, signal_layer, signal_strength, "
        "tags_json, is_current, analysis_type) "
        "VALUES (1, 1, 's1', 'actionable', 4, '[\"llm\",\"eval\"]', 1, 'daily')"
    )
    conn.execute(
        "INSERT INTO sources (id, source_key, type, handle) VALUES (1, 'x:karpathy', 'x', 'karpathy')"
    )
    conn.execute(
        "INSERT INTO raw_items (id, source_id, url, author, body) "
        "VALUES (1, 1, 'https://x/1', 'karpathy', 'text')"
    )
    conn.execute("INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (1, 1)")
    conn.commit()


def test_save_writes_event_and_updates_bt_and_weights():
    from prism.web.feed import record_feed_action

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    _seed(conn)

    record_feed_action(conn, signal_id=1, action="save", target_key="", response_time_ms=0)

    ev = conn.execute(
        "SELECT signal_id, action FROM feed_interactions"
    ).fetchone()
    assert ev["signal_id"] == 1 and ev["action"] == "save"

    bt = conn.execute(
        "SELECT bt_score FROM signal_scores WHERE signal_id = 1"
    ).fetchone()
    assert bt["bt_score"] > 0  # nudged up from initial

    # save bumps all dimensions by +2.0
    author_w = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='author' AND key='karpathy'"
    ).fetchone()
    assert author_w["weight"] == 2.0

    tag_w = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='tag' AND key='llm'"
    ).fetchone()
    assert tag_w["weight"] == 2.0


def test_dismiss_drops_bt_and_weights():
    from prism.web.feed import record_feed_action

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    _seed(conn)

    record_feed_action(conn, signal_id=1, action="dismiss", target_key="", response_time_ms=0)

    ev = conn.execute("SELECT action FROM feed_interactions").fetchone()
    assert ev["action"] == "dismiss"

    author_w = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='author' AND key='karpathy'"
    ).fetchone()
    assert author_w["weight"] == -1.0
```

- [ ] **Step 2: Run test to confirm fail**

```
.venv/bin/pytest tests/test_feed_action.py -v
```
Expected: `ModuleNotFoundError: No module named 'prism.web.feed'`.

- [ ] **Step 3: Implement `prism/web/feed.py`**

Create `prism/web/feed.py`:

```python
"""Feed-first interaction: save / dismiss / follow / mute.

Explicit multi-dimensional feedback — the successor to pairwise as the
default interaction. Updates preference_weights and signal_scores in the
same code paths the pairwise pipeline uses, so downstream ranking and
source-weight logic see feed signals transparently.
"""
from __future__ import annotations

import json
import sqlite3

# Reuse existing pairwise helpers so feed feedback hits the same learning path.
from prism.web.pairwise import (
    _update_preference_weights,
    _ensure_signal_score,
    _update_source_weights,
)

# BT nudges for feed actions — deliberately smaller than a full pairwise win
# so frequent feed clicks don't dominate the slow-thinking pairwise signal.
BT_SAVE_BONUS = 0.2
BT_DISMISS_PENALTY = 0.1

# Preference-weights deltas by feed action.
ACTION_WEIGHT_DELTA = {
    "save": 2.0,
    "dismiss": -1.0,
}

# Author/tag deltas for dedicated follow / mute actions (only that one dimension).
FOLLOW_AUTHOR_WEIGHT = 3.0
MUTE_TOPIC_WEIGHT = -2.0


def record_feed_action(
    conn: sqlite3.Connection,
    *,
    signal_id: int,
    action: str,
    target_key: str = "",
    response_time_ms: int = 0,
    context: dict | None = None,
) -> None:
    """Record a feed interaction and update learning state.

    - save / dismiss → BT nudge on signal_scores + delta across all
      preference dimensions of the signal.
    - follow_author / unfollow_author → single author-dimension weight.
    - mute_topic / unmute_topic → single tag-dimension weight.
    """
    conn.execute(
        "INSERT INTO feed_interactions "
        "(signal_id, action, target_key, response_time_ms, context_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (signal_id, action, target_key, response_time_ms,
         json.dumps(context or {}, ensure_ascii=False)),
    )

    if action in ("save", "dismiss"):
        _ensure_signal_score(conn, signal_id)
        bonus = BT_SAVE_BONUS if action == "save" else -BT_DISMISS_PENALTY
        conn.execute(
            "UPDATE signal_scores SET bt_score = bt_score + ?, "
            "updated_at = datetime('now') WHERE signal_id = ?",
            (bonus, signal_id),
        )
        _update_preference_weights(conn, signal_id, ACTION_WEIGHT_DELTA[action])
        _update_source_weights(conn, signal_id, won=(action == "save"))

    elif action == "follow_author" and target_key:
        _set_weight(conn, "author", target_key, FOLLOW_AUTHOR_WEIGHT)
    elif action == "unfollow_author" and target_key:
        _set_weight(conn, "author", target_key, 0.0)
    elif action == "mute_topic" and target_key:
        _set_weight(conn, "tag", target_key, MUTE_TOPIC_WEIGHT)
    elif action == "unmute_topic" and target_key:
        _set_weight(conn, "tag", target_key, 0.0)

    conn.commit()


def _set_weight(conn: sqlite3.Connection, dimension: str, key: str, weight: float) -> None:
    conn.execute(
        "INSERT INTO preference_weights (dimension, key, weight, updated_at) "
        "VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%S','now')) "
        "ON CONFLICT(dimension, key) DO UPDATE SET "
        "weight = excluded.weight, updated_at = excluded.updated_at",
        (dimension, key, weight),
    )
```

- [ ] **Step 4: Run test to confirm pass**

```
.venv/bin/pytest tests/test_feed_action.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add prism/web/feed.py tests/test_feed_action.py
git commit -m "feat(feed): record_feed_action for save/dismiss with BT nudge + pref update"
```

---

## Task 3: follow_author and mute_topic handlers

**Files:**
- Modify: (nothing new; extend existing test coverage)
- Test: `tests/test_feed_follow_mute.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_feed_follow_mute.py`:

```python
import sqlite3

from prism.db import init_db
from prism.web.feed import record_feed_action


def _mkconn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def test_follow_author_sets_author_weight_3():
    conn = _mkconn()
    record_feed_action(conn, signal_id=0, action="follow_author", target_key="karpathy")
    w = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='author' AND key='karpathy'"
    ).fetchone()
    assert w["weight"] == 3.0


def test_unfollow_author_clears_weight():
    conn = _mkconn()
    record_feed_action(conn, signal_id=0, action="follow_author", target_key="karpathy")
    record_feed_action(conn, signal_id=0, action="unfollow_author", target_key="karpathy")
    w = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='author' AND key='karpathy'"
    ).fetchone()
    assert w["weight"] == 0.0


def test_mute_topic_sets_tag_weight_negative():
    conn = _mkconn()
    record_feed_action(conn, signal_id=0, action="mute_topic", target_key="crypto")
    w = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='tag' AND key='crypto'"
    ).fetchone()
    assert w["weight"] == -2.0


def test_unmute_topic_clears_weight():
    conn = _mkconn()
    record_feed_action(conn, signal_id=0, action="mute_topic", target_key="crypto")
    record_feed_action(conn, signal_id=0, action="unmute_topic", target_key="crypto")
    w = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='tag' AND key='crypto'"
    ).fetchone()
    assert w["weight"] == 0.0


def test_feed_interactions_row_logged():
    conn = _mkconn()
    record_feed_action(conn, signal_id=0, action="follow_author", target_key="karpathy")
    record_feed_action(conn, signal_id=0, action="mute_topic", target_key="crypto")
    n = conn.execute("SELECT COUNT(*) FROM feed_interactions").fetchone()[0]
    assert n == 2
```

- [ ] **Step 2: Run to confirm pass**

```
.venv/bin/pytest tests/test_feed_follow_mute.py -v
```
Expected: 5 passed (T2 already implemented the handlers).

- [ ] **Step 3: Commit**

```bash
git add tests/test_feed_follow_mute.py
git commit -m "test(feed): follow_author / mute_topic coverage"
```

---

## Task 4: Feed ranking + exclude recently-acted signals

**Files:**
- Modify: `prism/web/pairwise.py` — extend `_get_candidate_pool(conn, extra_exclude_ids=None)`
- Create: `prism/web/feed.py` — add `rank_feed(conn, limit, offset) -> list[dict]`
- Test: `tests/test_feed_rank.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_feed_rank.py`:

```python
import sqlite3

from prism.db import init_db


def _seed_signal(conn, sid, author, tags, source_key="x:demo"):
    conn.execute(
        "INSERT OR IGNORE INTO sources (id, source_key, type, handle) "
        "VALUES (1, ?, 'x', 'demo')",
        (source_key,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO clusters (id, date, topic_label) "
        "VALUES (1, '2026-04-19', 'AI')"
    )
    import json
    conn.execute(
        "INSERT INTO signals (id, cluster_id, summary, signal_layer, signal_strength, "
        "tags_json, is_current, analysis_type) "
        "VALUES (?, 1, ?, 'actionable', 3, ?, 1, 'daily')",
        (sid, f"s{sid}", json.dumps(tags)),
    )
    conn.execute(
        "INSERT INTO raw_items (id, source_id, url, author, body) "
        "VALUES (?, 1, ?, ?, 't')",
        (sid, f"https://x/{sid}", author),
    )
    conn.execute(
        "INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (1, ?)",
        (sid,),
    )


def _mkconn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def test_rank_feed_returns_signals_highest_first():
    from prism.web.feed import rank_feed
    conn = _mkconn()
    for i in range(1, 6):
        _seed_signal(conn, i, f"a{i}", ["ai"])
    conn.commit()

    # Boost signal 3 via preference_weights (author match)
    conn.execute(
        "INSERT INTO preference_weights (dimension, key, weight) VALUES ('author','a3', 5.0)"
    )
    conn.commit()

    rows = rank_feed(conn, limit=5, offset=0)
    assert rows, "should return something"
    assert rows[0]["signal_id"] == 3


def test_rank_feed_excludes_recently_dismissed():
    from prism.web.feed import rank_feed, record_feed_action
    conn = _mkconn()
    for i in range(1, 4):
        _seed_signal(conn, i, f"a{i}", ["ai"])
    conn.commit()

    record_feed_action(conn, signal_id=2, action="dismiss")

    rows = rank_feed(conn, limit=10, offset=0)
    ids = [r["signal_id"] for r in rows]
    assert 2 not in ids


def test_rank_feed_pagination():
    from prism.web.feed import rank_feed
    conn = _mkconn()
    for i in range(1, 8):
        _seed_signal(conn, i, f"a{i}", ["ai"])
    conn.commit()

    page1 = rank_feed(conn, limit=3, offset=0)
    page2 = rank_feed(conn, limit=3, offset=3)
    ids1 = {r["signal_id"] for r in page1}
    ids2 = {r["signal_id"] for r in page2}
    assert ids1.isdisjoint(ids2)
```

- [ ] **Step 2: Run to confirm failure**

```
.venv/bin/pytest tests/test_feed_rank.py -v
```

- [ ] **Step 3: Extend `_get_candidate_pool` to accept extra_exclude_ids**

In `prism/web/pairwise.py`, change signature of `_get_candidate_pool`:

```python
def _get_candidate_pool(
    conn: sqlite3.Connection,
    extra_exclude_ids: set[int] | None = None,
) -> list[dict]:
```

Inside the function, after building `recent_ids`, add:

```python
    if extra_exclude_ids:
        recent_ids = recent_ids | set(extra_exclude_ids)
```

All existing callers pass no argument → default None → behavior unchanged.

- [ ] **Step 4: Implement `rank_feed` in `prism/web/feed.py`**

Append to `prism/web/feed.py`:

```python
from prism.web.pairwise import _get_candidate_pool, _load_pref_weights


_DIMENSION_WEIGHT = {
    "author": 1.0,
    "tag": 0.6,
    "source": 0.8,
    "layer": 0.4,
}


def _score_signal(signal: dict, pref_map: dict[tuple[str, str], float]) -> float:
    score = signal.get("bt_score", 0.0) + signal.get("signal_strength", 0) * 10.0

    # author
    for author in signal.get("authors", []) or []:
        score += pref_map.get(("author", author), 0.0) * _DIMENSION_WEIGHT["author"]

    # tags
    for tag in signal.get("tags", []) or []:
        score += pref_map.get(("tag", tag), 0.0) * _DIMENSION_WEIGHT["tag"]

    # source
    for sk in signal.get("source_keys", []) or []:
        score += pref_map.get(("source", sk), 0.0) * _DIMENSION_WEIGHT["source"]

    # layer
    layer = signal.get("signal_layer") or ""
    if layer:
        score += pref_map.get(("layer", layer), 0.0) * _DIMENSION_WEIGHT["layer"]

    return score


def _recent_feed_excludes(conn: sqlite3.Connection, days: int = 7) -> set[int]:
    rows = conn.execute(
        "SELECT DISTINCT signal_id FROM feed_interactions "
        "WHERE action IN ('save','dismiss') "
        "AND created_at > datetime('now', ?)",
        (f"-{days} days",),
    ).fetchall()
    return {r[0] for r in rows if r[0]}


def rank_feed(conn: sqlite3.Connection, limit: int = 10, offset: int = 0) -> list[dict]:
    """Return signals ranked by feed_score desc, paged by limit/offset."""
    excl = _recent_feed_excludes(conn)
    pool = _get_candidate_pool(conn, extra_exclude_ids=excl)
    pref_map = _load_pref_weights(conn)
    ranked = sorted(pool, key=lambda s: _score_signal(s, pref_map), reverse=True)
    return ranked[offset:offset + limit]
```

- [ ] **Step 5: Run test to confirm pass**

```
.venv/bin/pytest tests/test_feed_rank.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add prism/web/pairwise.py prism/web/feed.py tests/test_feed_rank.py
git commit -m "feat(feed): rank_feed scoring with pref weights + recency exclusion"
```

---

## Task 5: Feed web routes + templates

**Files:**
- Modify: `prism/web/routes.py` (add routes)
- Create: `prism/web/templates/feed.html`, `partials/feed_card.html`, `partials/feed_empty.html`
- Test: `tests/test_feed_route.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_feed_route.py`:

```python
import sqlite3
from fastapi.testclient import TestClient

from prism.db import init_db
from prism.api.app import create_app


def _mkconn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _seed(conn, n=3):
    import json
    conn.execute(
        "INSERT OR IGNORE INTO sources (id, source_key, type, handle) "
        "VALUES (1, 'x:demo', 'x', 'demo')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO clusters (id, date, topic_label) "
        "VALUES (1, '2026-04-19', 'AI')"
    )
    for i in range(1, n + 1):
        conn.execute(
            "INSERT INTO signals (id, cluster_id, summary, signal_layer, signal_strength, "
            "tags_json, is_current, analysis_type) VALUES (?, 1, ?, 'actionable', 3, ?, 1, 'daily')",
            (i, f"summary-{i}", json.dumps(["ai"])),
        )
        conn.execute(
            "INSERT INTO raw_items (id, source_id, url, author, body) "
            "VALUES (?, 1, ?, 'demo', 't')",
            (i, f"https://x/{i}"),
        )
        conn.execute(
            "INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (1, ?)",
            (i,),
        )
    conn.commit()


def test_feed_route_returns_200_and_renders_signals():
    conn = _mkconn()
    _seed(conn, 3)
    app = create_app(conn=conn)
    r = TestClient(app).get("/feed")
    assert r.status_code == 200
    assert "summary-1" in r.text or "summary-2" in r.text or "summary-3" in r.text


def test_feed_action_save_writes_event():
    conn = _mkconn()
    _seed(conn, 1)
    app = create_app(conn=conn)
    r = TestClient(app).post(
        "/feed/action",
        data={"signal_id": "1", "action": "save", "target_key": "", "response_time_ms": "0"},
    )
    assert r.status_code == 200
    n = conn.execute("SELECT COUNT(*) FROM feed_interactions WHERE action='save'").fetchone()[0]
    assert n == 1


def test_feed_more_pagination():
    conn = _mkconn()
    _seed(conn, 5)
    app = create_app(conn=conn)
    client = TestClient(app)
    r = client.get("/feed/more?offset=2")
    assert r.status_code == 200


def test_root_redirects_to_feed():
    conn = _mkconn()
    _seed(conn, 1)
    app = create_app(conn=conn)
    client = TestClient(app)
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "/feed" in r.headers["location"]
```

- [ ] **Step 2: Run to confirm failure**

```
.venv/bin/pytest tests/test_feed_route.py -v
```

- [ ] **Step 3: Create `prism/web/templates/partials/feed_card.html`**

```html
<article class="feed-card" id="feed-card-{{ signal.signal_id }}">
    <header class="feed-meta">
        <span class="feed-topic">{{ signal.topic_label }}</span>
        {% if signal.source_keys %}<span class="feed-source">{{ signal.source_keys[0] }}</span>{% endif %}
        {% if signal.authors %}<span class="feed-author">@{{ signal.authors[0] }}</span>{% endif %}
    </header>
    <div class="feed-body">
        <p class="feed-summary">{{ signal.summary }}</p>
        {% if signal.why_it_matters %}<p class="feed-why">{{ signal.why_it_matters }}</p>{% endif %}
        {% if signal.urls %}<p class="feed-links"><a href="{{ signal.urls[0] }}" target="_blank" rel="noopener">原文 ↗</a></p>{% endif %}
    </div>
    <footer class="feed-actions">
        <form hx-post="/feed/action" hx-target="#feed-card-{{ signal.signal_id }}" hx-swap="outerHTML" style="display:inline">
            <input type="hidden" name="signal_id" value="{{ signal.signal_id }}">
            <input type="hidden" name="action" value="save">
            <button type="submit" class="btn btn-save">👍 Save</button>
        </form>
        {% if signal.authors %}
        <form hx-post="/feed/action" hx-target="this" hx-swap="outerHTML" style="display:inline">
            <input type="hidden" name="signal_id" value="{{ signal.signal_id }}">
            <input type="hidden" name="action" value="follow_author">
            <input type="hidden" name="target_key" value="{{ signal.authors[0] }}">
            <button type="submit" class="btn btn-follow">🔔 Follow {{ signal.authors[0] }}</button>
        </form>
        {% endif %}
        {% if signal.tags %}
        <form hx-post="/feed/action" hx-target="this" hx-swap="outerHTML" style="display:inline">
            <input type="hidden" name="signal_id" value="{{ signal.signal_id }}">
            <input type="hidden" name="action" value="mute_topic">
            <input type="hidden" name="target_key" value="{{ signal.tags[0] }}">
            <button type="submit" class="btn btn-mute">🙈 Mute #{{ signal.tags[0] }}</button>
        </form>
        {% endif %}
        <form hx-post="/feed/action" hx-target="#feed-card-{{ signal.signal_id }}" hx-swap="outerHTML" style="display:inline">
            <input type="hidden" name="signal_id" value="{{ signal.signal_id }}">
            <input type="hidden" name="action" value="dismiss">
            <button type="submit" class="btn btn-dismiss">✕</button>
        </form>
    </footer>
</article>
```

- [ ] **Step 4: Create `prism/web/templates/partials/feed_empty.html`**

```html
<div class="feed-empty">
    <p>暂时没有更多信号了。试试：</p>
    <ul>
        <li>运行 <code>prism sync</code> 拉新内容</li>
        <li>在 <a href="/persona">/persona</a> 更新一下兴趣描述</li>
        <li>或者去 <a href="/pairwise">/pairwise</a> 做一轮校准</li>
    </ul>
</div>
```

- [ ] **Step 5: Create `prism/web/templates/feed.html`**

```html
{% extends "base.html" %}
{% block title %}Feed · Prism{% endblock %}
{% block body %}
<div class="feed-container">
    <nav class="feed-nav">
        <a href="/feed" class="active">Feed</a>
        <a href="/feed/saved">Saved</a>
        <a href="/pairwise">Pairwise 校准</a>
        <a href="/pairwise/sources">Sources</a>
        <a href="/taste/sources">Proposals</a>
        <a href="/persona">Persona</a>
    </nav>

    <form class="feed-submit" hx-post="/pairwise/feed" hx-target="#feed-list" hx-swap="afterbegin">
        <input type="text" name="url" placeholder="粘一个感兴趣的链接 / 话题" class="feed-input">
        <input type="text" name="note" placeholder="备注（可选）" class="feed-note">
        <button type="submit">投喂</button>
    </form>

    <div id="feed-list" class="feed-list"
         hx-get="/feed/more?offset=0"
         hx-trigger="load"
         hx-swap="innerHTML">
        <p class="feed-loading">加载中...</p>
    </div>

    <div class="feed-more-wrapper">
        <button id="feed-load-more"
                hx-get="/feed/more?offset={{ next_offset }}"
                hx-target="#feed-list"
                hx-swap="beforeend">加载更多</button>
    </div>
</div>
{% endblock %}
```

- [ ] **Step 6: Add routes to `prism/web/routes.py`**

Near the existing pairwise routes block, add:

```python
from prism.web.feed import rank_feed, record_feed_action


@web_router.get("/feed", response_class=HTMLResponse)
def feed_index(request: Request):
    tpl = _jinja_env.get_template("feed.html")
    return HTMLResponse(tpl.render(next_offset=0))


@web_router.get("/feed/more", response_class=HTMLResponse)
def feed_more(request: Request, offset: int = 0, limit: int = 10):
    conn = _db(request)
    rows = rank_feed(conn, limit=limit, offset=offset)
    if not rows:
        tpl = _jinja_env.get_template("partials/feed_empty.html")
        return HTMLResponse(tpl.render())
    card_tpl = _jinja_env.get_template("partials/feed_card.html")
    html = "".join(card_tpl.render(signal=r) for r in rows)
    return HTMLResponse(html)


@web_router.post("/feed/action", response_class=HTMLResponse)
def feed_action(
    request: Request,
    signal_id: int = Form(...),
    action: str = Form(...),
    target_key: str = Form(""),
    response_time_ms: int = Form(0),
):
    conn = _db(request)
    record_feed_action(
        conn,
        signal_id=signal_id,
        action=action,
        target_key=target_key,
        response_time_ms=response_time_ms,
    )
    # save/dismiss → swap card out with confirmation
    if action in ("save", "dismiss"):
        label = "已保存" if action == "save" else "已隐藏"
        return HTMLResponse(
            f'<div class="feed-card feed-done">{label} ✓</div>'
        )
    # follow / mute → return a small toast-like button replacement
    labels = {
        "follow_author": f"已关注 {target_key}",
        "mute_topic": f"已屏蔽 #{target_key}",
        "unfollow_author": f"取消关注 {target_key}",
        "unmute_topic": f"取消屏蔽 #{target_key}",
    }
    return HTMLResponse(
        f'<span class="btn btn-done">{labels.get(action, "ok")}</span>'
    )
```

Also change the root route. Find the existing `GET /` handler (`routes.py` around line 240-250 per the prior snapshot; search for `@web_router.get("/")`). Replace its body with:

```python
@web_router.get("/", response_class=HTMLResponse)
def index(request: Request):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/feed", status_code=307)
```

- [ ] **Step 7: Run tests**

```
.venv/bin/pytest tests/test_feed_route.py -v
```
Expected: 4 passed.

- [ ] **Step 8: Commit**

```bash
git add prism/web/routes.py prism/web/templates/feed.html \
        prism/web/templates/partials/feed_card.html \
        prism/web/templates/partials/feed_empty.html \
        tests/test_feed_route.py
git commit -m "feat(web): /feed routes and templates, redirect / to /feed"
```

---

## Task 6: Saved signals page

**Files:**
- Modify: `prism/web/routes.py`
- Create: `prism/web/templates/feed_saved.html`
- Test: `tests/test_feed_saved.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_feed_saved.py`:

```python
import sqlite3
from fastapi.testclient import TestClient

from prism.db import init_db
from prism.api.app import create_app
from prism.web.feed import record_feed_action


def test_saved_page_lists_saved_signals():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute(
        "INSERT INTO clusters (id, date, topic_label) VALUES (1,'2026-04-19','AI')"
    )
    conn.execute(
        "INSERT INTO signals (id, cluster_id, summary, signal_layer, signal_strength, "
        "tags_json, is_current, analysis_type) "
        "VALUES (1,1,'saved-this','actionable',3,'[]',1,'daily')"
    )
    conn.commit()
    record_feed_action(conn, signal_id=1, action="save")

    r = TestClient(create_app(conn=conn)).get("/feed/saved")
    assert r.status_code == 200
    assert "saved-this" in r.text
```

- [ ] **Step 2: Run to confirm failure**

```
.venv/bin/pytest tests/test_feed_saved.py -v
```

- [ ] **Step 3: Create `prism/web/templates/feed_saved.html`**

```html
{% extends "base.html" %}
{% block title %}Saved · Prism{% endblock %}
{% block body %}
<div class="feed-container">
    <nav class="feed-nav">
        <a href="/feed">Feed</a>
        <a href="/feed/saved" class="active">Saved</a>
        <a href="/pairwise">Pairwise 校准</a>
    </nav>
    {% if not signals %}
    <p>还没有已保存的信号。</p>
    {% else %}
    <ul class="saved-list">
        {% for s in signals %}
        <li class="saved-item">
            <div class="saved-meta">{{ s.created_at }} · {{ s.topic_label or "" }}</div>
            <div class="saved-summary">{{ s.summary }}</div>
            {% if s.url %}<a href="{{ s.url }}" target="_blank" rel="noopener">原文 ↗</a>{% endif %}
        </li>
        {% endfor %}
    </ul>
    {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 4: Add route in `prism/web/routes.py`**

```python
@web_router.get("/feed/saved", response_class=HTMLResponse)
def feed_saved(request: Request):
    conn = _db(request)
    rows = conn.execute(
        """SELECT fi.created_at, s.summary, c.topic_label,
                  (SELECT url FROM raw_items ri
                   JOIN cluster_items ci ON ci.raw_item_id = ri.id
                   WHERE ci.cluster_id = s.cluster_id LIMIT 1) AS url
             FROM feed_interactions fi
             JOIN signals s ON s.id = fi.signal_id
             LEFT JOIN clusters c ON c.id = s.cluster_id
            WHERE fi.action = 'save'
            ORDER BY fi.created_at DESC
            LIMIT 200"""
    ).fetchall()
    tpl = _jinja_env.get_template("feed_saved.html")
    return HTMLResponse(tpl.render(signals=rows))
```

- [ ] **Step 5: Run tests**

```
.venv/bin/pytest tests/test_feed_saved.py -v
```
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add prism/web/routes.py prism/web/templates/feed_saved.html tests/test_feed_saved.py
git commit -m "feat(web): /feed/saved page lists save actions"
```

---

## Task 7: Minimal CSS for feed cards

**Files:**
- Modify: `prism/web/static/style.css` (append feed-specific rules)

- [ ] **Step 1: Append feed styles**

Find the `static/style.css` file (if absent, use `prism/web/templates/base.html` inline block). Add at the end:

```css
.feed-container { max-width: 720px; margin: 0 auto; padding: 16px; }
.feed-nav { display: flex; gap: 16px; margin-bottom: 16px; border-bottom: 1px solid #eee; padding-bottom: 8px; }
.feed-nav a { text-decoration: none; color: #555; padding: 4px 8px; }
.feed-nav a.active { color: #222; border-bottom: 2px solid #222; }
.feed-submit { display: flex; gap: 8px; margin-bottom: 16px; }
.feed-submit .feed-input { flex: 2; padding: 6px; }
.feed-submit .feed-note { flex: 1; padding: 6px; }
.feed-list { display: flex; flex-direction: column; gap: 16px; }
.feed-card { border: 1px solid #e4e4e4; border-radius: 8px; padding: 14px; background: #fff; }
.feed-meta { display: flex; gap: 10px; font-size: 12px; color: #888; margin-bottom: 8px; }
.feed-summary { margin: 4px 0; font-size: 15px; line-height: 1.5; }
.feed-why { margin: 4px 0; color: #666; font-size: 13px; }
.feed-actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; }
.feed-actions .btn { padding: 4px 10px; border: 1px solid #ccc; border-radius: 4px; background: #fafafa; cursor: pointer; font-size: 13px; }
.feed-actions .btn-save { background: #e8f5e8; border-color: #4a4; }
.feed-actions .btn-dismiss { background: #f5e8e8; border-color: #a44; }
.feed-done { padding: 10px; text-align: center; color: #666; background: #f9f9f9; border-radius: 8px; }
.feed-more-wrapper { text-align: center; margin: 24px 0; }
#feed-load-more { padding: 8px 24px; cursor: pointer; }
.saved-list { list-style: none; padding: 0; }
.saved-item { border-bottom: 1px solid #eee; padding: 12px 0; }
.saved-meta { font-size: 12px; color: #888; }
```

- [ ] **Step 2: Commit**

```bash
git add prism/web/static/style.css
git commit -m "style(feed): minimal feed card + nav styling"
```

---

## Task 8: Full test run + manual verification

- [ ] **Step 1: Run full suite**

```
.venv/bin/pytest tests/ --ignore=tests/test_api.py -q
```
Expected: all passing (test_api.py has one pre-existing unrelated failure).

- [ ] **Step 2: Migrate real DB**

```
.venv/bin/python -c "import sqlite3; from prism.db import init_db; conn=sqlite3.connect('data/prism.sqlite3'); init_db(conn); print('schema updated')"
```

Verify:
```
sqlite3 data/prism.sqlite3 ".schema feed_interactions"
```
Should show the new table.

- [ ] **Step 3: Manual smoke test**

```
.venv/bin/prism serve --port 8080
```

- Open `http://localhost:8080/` → should redirect to `/feed`
- Check ~10 cards render with topic / author / source visible
- Click Save on one → card swaps to "已保存 ✓"
- Click Dismiss on another → card swaps to "已隐藏 ✓"
- Click Follow Author on one → button swaps to "已关注 X"
- Click 加载更多 → next 10 load
- Navigate to `/feed/saved` → the saved card appears
- Navigate to `/pairwise` → old pairwise UI still works
- Query: `sqlite3 data/prism.sqlite3 "SELECT action, COUNT(*) FROM feed_interactions GROUP BY action;"` should list your clicks

- [ ] **Step 4: Empty commit marking milestone**

```bash
git commit --allow-empty -m "docs: W2 feed-first redesign complete"
```

---

## Self-review checklist

- [x] Every spec requirement has a task (feed_interactions table ✓, save/dismiss ✓, follow/mute ✓, rank_feed ✓, /feed + /feed/more + /feed/action routes ✓, / redirect ✓, /feed/saved ✓, CSS ✓).
- [x] No TBD / "similar to Task N"; every step has actual code.
- [x] Type consistency: `rank_feed(conn, limit, offset) → list[dict]`; `record_feed_action(conn, *, signal_id, action, target_key, response_time_ms, context)` used consistently in routes and tests.
- [x] `_get_candidate_pool` signature change is backward-compatible (new arg has default None).
- [x] Test modules isolated (in-memory conn via `create_app(conn=conn)`).

## Execution handoff

Subagent-driven execution next. Task order: T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8.
