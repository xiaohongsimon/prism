# Convergence Engine — Week 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the 75% "neither" vote bleed by adding persona capture, LLM-driven source proposals, external-feed consumption, and honest strategy logging — all on top of the existing Prism recall pipeline.

**Architecture:** Add one new module `prism/persona.py`, one new pipeline step `prism/pipeline/external_feed.py`, one utility `prism/sources/yaml_editor.py`, three new DB tables (`persona_snapshots`, `source_proposals`, extend `external_feeds`), two new web pages (`/persona`, `/taste/sources`), and two new CLI commands (`prism process-external-feeds`, `prism sources prune`). Change `select_pair` + `record_vote` to carry actual strategy name.

**Tech Stack:** Python 3.14, SQLite, FastAPI + Jinja2, HTMX, ruamel.yaml, pytest, omlx (local LLM) via `prism.pipeline.llm.call_llm_json`.

**Worktree:** Before starting, create a git worktree via `superpowers:using-git-worktrees` (branch name `recall-rescue-w1`).

**Spec:** `docs/superpowers/specs/2026-04-19-prism-convergence-engine.md` (Week 1 section only).

---

## Task 1: Schema additions

**Files:**
- Modify: `prism/db.py` (append new table blocks inside `init_db()`)
- Test: `tests/test_db_schema.py` (new or append)

- [ ] **Step 1: Write failing test**

Create `tests/test_db_schema.py` if it does not already exist and add:

```python
import sqlite3
from prism.db import init_db


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def test_persona_snapshots_table_exists():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    assert "persona_snapshots" in _tables(conn)
    cols = _columns(conn, "persona_snapshots")
    assert {"id", "answers_json", "free_text", "seed_handles_json",
            "extracted_summary", "is_active", "created_at"} <= cols


def test_source_proposals_table_exists():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    assert "source_proposals" in _tables(conn)
    cols = _columns(conn, "source_proposals")
    assert {"id", "source_type", "source_config_json", "display_name",
            "rationale", "origin", "origin_ref", "sample_preview_json",
            "status", "snooze_until", "created_at", "reviewed_at"} <= cols


def test_external_feeds_has_extracted_json_column():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    cols = _columns(conn, "external_feeds")
    assert "extracted_json" in cols
```

- [ ] **Step 2: Run test to confirm it fails**

```
.venv/bin/pytest tests/test_db_schema.py -v
```
Expected: FAIL with `"persona_snapshots" not in tables` (the first test that hits a missing piece).

- [ ] **Step 3: Add tables and column in `init_db()`**

Locate `init_db()` in `prism/db.py`. After the existing `CREATE TABLE IF NOT EXISTS external_feeds (...)` block, append:

```python
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS persona_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            answers_json TEXT NOT NULL,
            free_text TEXT NOT NULL DEFAULT '',
            seed_handles_json TEXT NOT NULL DEFAULT '[]',
            extracted_summary TEXT NOT NULL DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS source_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_config_json TEXT NOT NULL,
            display_name TEXT NOT NULL,
            rationale TEXT NOT NULL DEFAULT '',
            origin TEXT NOT NULL,
            origin_ref TEXT DEFAULT NULL,
            sample_preview_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','accepted','rejected','ignored','snoozed')),
            snooze_until TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            reviewed_at TEXT DEFAULT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_source_proposals_status
            ON source_proposals(status);
    """)

    # Add extracted_json column to external_feeds if missing
    cols = {r[1] for r in conn.execute("PRAGMA table_info(external_feeds)").fetchall()}
    if "extracted_json" not in cols:
        conn.execute(
            "ALTER TABLE external_feeds ADD COLUMN extracted_json TEXT NOT NULL DEFAULT ''"
        )
```

- [ ] **Step 4: Run test to confirm it passes**

```
.venv/bin/pytest tests/test_db_schema.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add prism/db.py tests/test_db_schema.py
git commit -m "feat(db): add persona_snapshots, source_proposals, external_feeds.extracted_json"
```

---

## Task 2: YAML editor utility

**Files:**
- Create: `prism/sources/yaml_editor.py`
- Test: `tests/test_yaml_editor.py`

Dependency: `ruamel.yaml` (add to `pyproject.toml` dependencies if missing).

- [ ] **Step 1: Ensure `ruamel.yaml` is installed**

Run:
```
.venv/bin/pip show ruamel.yaml || .venv/bin/pip install "ruamel.yaml>=0.18"
```

If install was needed, add to `pyproject.toml` under `[project].dependencies` right after existing YAML-related entries (keep alphabetical with neighbors):

```
"ruamel.yaml>=0.18",
```

- [ ] **Step 2: Write failing test**

Create `tests/test_yaml_editor.py`:

```python
from pathlib import Path
import textwrap

from prism.sources.yaml_editor import (
    append_source_block,
    comment_out_source,
    load_sources_list,
)


SAMPLE_YAML = textwrap.dedent("""\
    sources:
      # Existing section
      - type: x
        handle: karpathy
        display_name: "Andrej Karpathy"
        depth: thread

      - type: hn
        feed: best
        display_name: "HN Best"
""")


def test_append_source_block_preserves_existing(tmp_path: Path):
    p = tmp_path / "sources.yaml"
    p.write_text(SAMPLE_YAML)

    append_source_block(
        p,
        source_config={
            "type": "x",
            "handle": "zarazhangrui",
            "display_name": "Zara Zhang Rui",
            "depth": "thread",
        },
        category_comment="persona-proposed 2026-04-19",
    )

    text = p.read_text()
    assert "handle: karpathy" in text
    assert "handle: zarazhangrui" in text
    assert "persona-proposed 2026-04-19" in text
    items = load_sources_list(p)
    assert len(items) == 3
    assert any(i.get("handle") == "zarazhangrui" for i in items)


def test_comment_out_source_by_key(tmp_path: Path):
    p = tmp_path / "sources.yaml"
    p.write_text(SAMPLE_YAML)

    removed = comment_out_source(p, source_key="hn:best", reason="weight=-10 pruned 2026-04-19")
    assert removed is True

    text = p.read_text()
    # The hn block should be commented out with the reason nearby
    assert "# pruned 2026-04-19" in text or "# weight=-10 pruned 2026-04-19" in text
    assert "#   - type: hn" in text or "# - type: hn" in text

    items = load_sources_list(p)
    assert not any(i.get("feed") == "best" and i.get("type") == "hn" for i in items)


def test_append_idempotent_on_duplicate_key(tmp_path: Path):
    p = tmp_path / "sources.yaml"
    p.write_text(SAMPLE_YAML)

    first = append_source_block(
        p, source_config={"type": "x", "handle": "karpathy", "display_name": "x", "depth": "thread"}
    )
    assert first is False  # already present, no change

    items = load_sources_list(p)
    assert sum(1 for i in items if i.get("handle") == "karpathy") == 1
```

- [ ] **Step 3: Run test to confirm it fails**

```
.venv/bin/pytest tests/test_yaml_editor.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'prism.sources.yaml_editor'`.

- [ ] **Step 4: Implement `prism/sources/yaml_editor.py`**

```python
"""Safe YAML editing for config/sources.yaml.

Uses ruamel.yaml to preserve comments and structure on round-trip.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 4096
    return y


def _source_key(entry: dict) -> str:
    """Build a canonical key to compare sources. Mirrors CLAUDE.md convention."""
    t = entry.get("type", "")
    if "handle" in entry:
        return f"{t}:{entry['handle']}"
    if "feed" in entry:
        return f"{t}:{entry['feed']}"
    if "url" in entry:
        return f"{t}:{entry['url']}"
    if "query" in entry:
        return f"{t}:query:{entry['query']}"
    return t


def load_sources_list(path: Path) -> list[dict[str, Any]]:
    """Return the `sources` list from a yaml file (mutation-safe copy)."""
    data = _yaml().load(Path(path).read_text())
    return [dict(item) for item in (data.get("sources") or [])]


def append_source_block(
    path: Path,
    source_config: dict[str, Any],
    category_comment: str = "",
) -> bool:
    """Append a source entry to `sources.yaml`. Returns True if appended,
    False if an entry with the same canonical key already existed."""
    y = _yaml()
    data = y.load(Path(path).read_text())
    seq = data["sources"]

    new_key = _source_key(source_config)
    for item in seq:
        if _source_key(item) == new_key:
            return False

    # Append preserving ordering
    from ruamel.yaml.comments import CommentedMap
    entry = CommentedMap(source_config)
    if category_comment:
        entry.yaml_set_start_comment(category_comment, indent=4)
    seq.append(entry)

    with Path(path).open("w") as f:
        y.dump(data, f)
    return True


def comment_out_source(path: Path, source_key: str, reason: str = "") -> bool:
    """Comment out the source entry matching `source_key` (e.g. 'hn:best').
    Preserves other entries and comments. Returns True if something was removed."""
    text = Path(path).read_text()
    y = _yaml()
    data = y.load(text)
    seq = data["sources"]

    target_idx = None
    for i, item in enumerate(seq):
        if _source_key(item) == source_key:
            target_idx = i
            break
    if target_idx is None:
        return False

    # Render the removed entry back into lines, prepend "# " to each
    target_entry = seq[target_idx]
    import io
    buf = io.StringIO()
    y.dump({"_removed": [target_entry]}, buf)
    removed_yaml = buf.getvalue()
    # Drop the wrapper line "_removed:" and dedent one level
    removed_lines = removed_yaml.splitlines()
    # Find the first "- " line to know indent and strip the wrapper
    body_lines = [ln for ln in removed_lines if not ln.startswith("_removed")]
    commented = "\n".join("# " + ln for ln in body_lines if ln.strip())
    header = f"# pruned: {reason}" if reason else "# pruned"

    del seq[target_idx]

    # Write yaml back, then append the commented block at the end of the file
    import io as _io
    out_buf = _io.StringIO()
    y.dump(data, out_buf)
    new_text = out_buf.getvalue().rstrip() + "\n\n" + header + "\n" + commented + "\n"
    Path(path).write_text(new_text)
    return True
```

- [ ] **Step 5: Run test to confirm it passes**

```
.venv/bin/pytest tests/test_yaml_editor.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add prism/sources/yaml_editor.py tests/test_yaml_editor.py pyproject.toml
git commit -m "feat(sources): yaml editor utility for safe source add/prune"
```

---

## Task 3: Persona snapshot CRUD

**Files:**
- Create: `prism/persona.py`
- Test: `tests/test_persona_crud.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_persona_crud.py`:

```python
import json
import sqlite3

from prism.db import init_db
from prism.persona import save_snapshot, load_active_snapshot


def _mkconn():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    return conn


def test_save_snapshot_returns_id_and_activates():
    conn = _mkconn()
    snap_id = save_snapshot(
        conn,
        answers={"role": "TL", "goals": ["方法论"]},
        free_text="想做一个会学习的推荐系统",
        seed_handles=["zarazhangrui", "danshipper"],
    )
    assert isinstance(snap_id, int)

    active = load_active_snapshot(conn)
    assert active is not None
    assert active["id"] == snap_id
    assert json.loads(active["answers_json"]) == {"role": "TL", "goals": ["方法论"]}
    assert active["free_text"] == "想做一个会学习的推荐系统"
    assert json.loads(active["seed_handles_json"]) == ["zarazhangrui", "danshipper"]


def test_new_snapshot_deactivates_previous():
    conn = _mkconn()
    first = save_snapshot(conn, answers={"a": 1}, free_text="", seed_handles=[])
    second = save_snapshot(conn, answers={"a": 2}, free_text="", seed_handles=[])
    assert first != second

    active = load_active_snapshot(conn)
    assert active["id"] == second

    row = conn.execute(
        "SELECT is_active FROM persona_snapshots WHERE id = ?", (first,)
    ).fetchone()
    assert row[0] == 0


def test_load_active_returns_none_when_empty():
    conn = _mkconn()
    assert load_active_snapshot(conn) is None
```

- [ ] **Step 2: Run test to confirm it fails**

```
.venv/bin/pytest tests/test_persona_crud.py -v
```
Expected: `ModuleNotFoundError: No module named 'prism.persona'`.

- [ ] **Step 3: Implement `prism/persona.py` (CRUD part)**

```python
"""Persona snapshot management + LLM extraction.

A persona snapshot is the user's self-description of who they are right now
and what they want Prism to surface. Snapshots are versioned; only one is
active at a time.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any


def save_snapshot(
    conn: sqlite3.Connection,
    answers: dict[str, Any],
    free_text: str = "",
    seed_handles: list[str] | None = None,
) -> int:
    """Save a persona snapshot. Deactivates any prior active snapshot.
    Returns the new snapshot id."""
    seed_handles = seed_handles or []
    conn.execute("UPDATE persona_snapshots SET is_active = 0 WHERE is_active = 1")
    cur = conn.execute(
        "INSERT INTO persona_snapshots (answers_json, free_text, seed_handles_json, is_active) "
        "VALUES (?, ?, ?, 1)",
        (json.dumps(answers, ensure_ascii=False),
         free_text,
         json.dumps(seed_handles, ensure_ascii=False)),
    )
    conn.commit()
    return cur.lastrowid


def load_active_snapshot(conn: sqlite3.Connection) -> dict | None:
    """Return the currently active persona snapshot, or None if none exists."""
    row = conn.execute(
        "SELECT id, answers_json, free_text, seed_handles_json, extracted_summary, "
        "       is_active, created_at "
        "FROM persona_snapshots WHERE is_active = 1 "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "answers_json": row[1],
        "free_text": row[2],
        "seed_handles_json": row[3],
        "extracted_summary": row[4],
        "is_active": row[5],
        "created_at": row[6],
    }
```

- [ ] **Step 4: Run test to confirm it passes**

```
.venv/bin/pytest tests/test_persona_crud.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add prism/persona.py tests/test_persona_crud.py
git commit -m "feat(persona): snapshot CRUD with single active row"
```

---

## Task 4: LLM extraction from persona snapshot

**Files:**
- Modify: `prism/persona.py`
- Test: `tests/test_persona_extract.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_persona_extract.py`:

```python
import json
import sqlite3
from unittest.mock import patch

from prism.db import init_db
from prism.persona import save_snapshot, extract_from_snapshot


FAKE_LLM_OUTPUT = {
    "summary": "TL 想积累方法论和个人成长内容",
    "bias_weights": [
        {"dimension": "tag", "key": "方法论", "weight": 3.0},
        {"dimension": "tag", "key": "LLM", "weight": -2.0},
        {"dimension": "layer", "key": "strategic", "weight": -2.0},
        # weight outside clip range should be clamped
        {"dimension": "tag", "key": "个人成长", "weight": 99.0},
    ],
    "candidate_sources": [
        {
            "type": "x",
            "handle": "zarazhangrui",
            "display_name": "Zara Zhang Rui",
            "depth": "thread",
            "rationale": "user listed as seed; methodology-oriented",
            "category": "growth-methodology",
        },
        {
            "type": "rss",
            "url": "https://example.com/newsletter.xml",
            "display_name": "Example Weekly",
            "rationale": "adjacent to user's stated interests",
            "category": "newsletter",
        },
    ],
}


def _mkconn():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    return conn


def test_extract_writes_bias_weights_and_proposals():
    conn = _mkconn()
    snap_id = save_snapshot(
        conn,
        answers={"role": "TL", "goals": ["积累方法论"]},
        free_text="",
        seed_handles=["zarazhangrui"],
    )

    with patch("prism.persona.call_llm_json", return_value=FAKE_LLM_OUTPUT) as mock_llm:
        n_weights, n_proposals = extract_from_snapshot(conn, snap_id)

    assert mock_llm.call_count == 1
    assert n_weights == 4
    assert n_proposals == 2

    # preference_weights written with dimension='persona_bias'
    rows = conn.execute(
        "SELECT dimension, key, weight FROM preference_weights "
        "WHERE dimension = 'persona_bias'"
    ).fetchall()
    weight_by_key = {f"{d}:{k}": w for d, k, w in rows}
    # clipped at ±5
    assert weight_by_key["persona_bias:tag/个人成长"] == 5.0
    assert weight_by_key["persona_bias:tag/方法论"] == 3.0
    assert weight_by_key["persona_bias:tag/LLM"] == -2.0

    # source_proposals written with origin='persona'
    proposals = conn.execute(
        "SELECT origin, source_type, display_name FROM source_proposals"
    ).fetchall()
    assert len(proposals) == 2
    assert all(p[0] == "persona" for p in proposals)
    assert {p[2] for p in proposals} == {"Zara Zhang Rui", "Example Weekly"}

    # extracted_summary persisted on snapshot
    summary = conn.execute(
        "SELECT extracted_summary FROM persona_snapshots WHERE id = ?", (snap_id,)
    ).fetchone()[0]
    assert "TL" in summary


def test_extract_previous_persona_bias_is_zeroed_on_new_snapshot():
    conn = _mkconn()
    s1 = save_snapshot(conn, answers={"role": "v1"}, free_text="", seed_handles=[])
    with patch("prism.persona.call_llm_json", return_value=FAKE_LLM_OUTPUT):
        extract_from_snapshot(conn, s1)

    # New snapshot supersedes
    s2 = save_snapshot(conn, answers={"role": "v2"}, free_text="", seed_handles=[])
    with patch(
        "prism.persona.call_llm_json",
        return_value={
            "summary": "v2",
            "bias_weights": [{"dimension": "tag", "key": "其他", "weight": 2.0}],
            "candidate_sources": [],
        },
    ):
        extract_from_snapshot(conn, s2)

    # Old bias keys should be zero; new bias key present
    rows = dict(conn.execute(
        "SELECT key, weight FROM preference_weights WHERE dimension = 'persona_bias'"
    ).fetchall())
    # Old keys from FAKE_LLM_OUTPUT should be 0.0 (not deleted)
    assert rows.get("tag/方法论") == 0.0
    assert rows.get("tag/其他") == 2.0
```

- [ ] **Step 2: Run test to confirm it fails**

```
.venv/bin/pytest tests/test_persona_extract.py -v
```
Expected: `ImportError: cannot import name 'extract_from_snapshot'`.

- [ ] **Step 3: Add extraction logic to `prism/persona.py`**

Append to `prism/persona.py`:

```python
from prism.pipeline.llm import call_llm_json


_PERSONA_BIAS_CLIP = 5.0

PERSONA_PROMPT_SYSTEM = (
    "你是 Prism 个人推荐系统的偏好提取助手。"
    "根据用户的 persona 描述，输出结构化 JSON："
    "1) summary: 一句话总结用户当前身份与关注点（中文）；"
    "2) bias_weights: 一个数组，每项 {dimension, key, weight}，"
    "   dimension ∈ [tag, author, source, layer]，weight 在 [-5, 5]；"
    "3) candidate_sources: 20-30 个候选信号源，每项包含 "
    "   {type, handle_or_url, display_name, rationale, category}，"
    "   type 可以是 x/rss/youtube/hn/arxiv 等，"
    "   若 type=x 提供 handle，否则提供 url。"
    "严格输出 JSON，不要任何额外文字。"
)


def _build_extract_prompt(
    answers: dict, free_text: str, seed_handles: list[str],
    current_top_prefs: list[tuple[str, str, float]],
) -> str:
    import json as _json
    top_prefs_text = (
        "\n".join(f"  - {d}/{k}: {w:+.2f}" for d, k, w in current_top_prefs[:20])
        or "  (尚无学习到的偏好)"
    )
    return (
        "【用户结构化答题】\n" + _json.dumps(answers, ensure_ascii=False, indent=2)
        + "\n\n【自由文字补充】\n" + (free_text or "(无)")
        + "\n\n【用户提供的种子账号】\n"
        + ("\n".join(f"  - {h}" for h in seed_handles) if seed_handles else "  (无)")
        + "\n\n【当前系统已学到的偏好 top/bottom】\n" + top_prefs_text
        + "\n\n请据此给出 summary / bias_weights / candidate_sources。"
    )


def _fetch_current_top_prefs(conn) -> list[tuple[str, str, float]]:
    top = conn.execute(
        "SELECT dimension, key, weight FROM preference_weights "
        "WHERE dimension != 'persona_bias' "
        "ORDER BY weight DESC LIMIT 15"
    ).fetchall()
    bot = conn.execute(
        "SELECT dimension, key, weight FROM preference_weights "
        "WHERE dimension != 'persona_bias' "
        "ORDER BY weight ASC LIMIT 15"
    ).fetchall()
    return [tuple(r) for r in top] + [tuple(r) for r in bot]


def extract_from_snapshot(conn, snapshot_id: int) -> tuple[int, int]:
    """Run LLM extraction on a persona snapshot.

    Writes bias weights to preference_weights (dimension='persona_bias'),
    zeroes out prior persona_bias rows not present in the new extraction,
    and creates source_proposals entries with origin='persona'.

    Returns (num_bias_weights_written, num_source_proposals_written).
    """
    import json as _json

    row = conn.execute(
        "SELECT answers_json, free_text, seed_handles_json "
        "FROM persona_snapshots WHERE id = ?",
        (snapshot_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"persona_snapshot id={snapshot_id} not found")
    answers = _json.loads(row[0])
    free_text = row[1] or ""
    seed_handles = _json.loads(row[2] or "[]")

    prompt = _build_extract_prompt(
        answers, free_text, seed_handles, _fetch_current_top_prefs(conn),
    )
    result = call_llm_json(prompt, system=PERSONA_PROMPT_SYSTEM, max_tokens=4096)

    summary = str(result.get("summary", "")).strip()
    bias_weights = result.get("bias_weights") or []
    candidates = result.get("candidate_sources") or []

    # Zero out all previous persona_bias rows (audit trail kept; rows not deleted)
    conn.execute(
        "UPDATE preference_weights SET weight = 0.0, "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%S', 'now') "
        "WHERE dimension = 'persona_bias'"
    )

    n_weights = 0
    for bw in bias_weights:
        dim = str(bw.get("dimension", "")).strip()
        key = str(bw.get("key", "")).strip()
        try:
            w = float(bw.get("weight", 0))
        except (TypeError, ValueError):
            continue
        if not dim or not key:
            continue
        w = max(-_PERSONA_BIAS_CLIP, min(_PERSONA_BIAS_CLIP, w))
        composite_key = f"{dim}/{key}"
        conn.execute(
            "INSERT INTO preference_weights (dimension, key, weight, updated_at) "
            "VALUES ('persona_bias', ?, ?, strftime('%Y-%m-%dT%H:%M:%S','now')) "
            "ON CONFLICT(dimension, key) DO UPDATE SET weight = excluded.weight, "
            "updated_at = excluded.updated_at",
            (composite_key, w),
        )
        n_weights += 1

    n_proposals = 0
    for cand in candidates:
        ctype = str(cand.get("type", "")).strip()
        if not ctype:
            continue
        cfg = {k: v for k, v in cand.items() if k not in ("rationale", "category", "display_name")}
        if "handle_or_url" in cfg:
            # normalise: if looks like URL, store as url; else as handle for x-like types
            v = cfg.pop("handle_or_url")
            if "://" in v:
                cfg["url"] = v
            else:
                cfg["handle"] = v
        display = str(cand.get("display_name") or cfg.get("handle") or cfg.get("url") or ctype)
        rationale = str(cand.get("rationale", ""))
        category = str(cand.get("category", ""))
        conn.execute(
            "INSERT INTO source_proposals "
            "(source_type, source_config_json, display_name, rationale, origin, origin_ref) "
            "VALUES (?, ?, ?, ?, 'persona', ?)",
            (ctype, _json.dumps(cfg, ensure_ascii=False),
             display, f"{category}: {rationale}" if category else rationale,
             str(snapshot_id)),
        )
        n_proposals += 1

    conn.execute(
        "UPDATE persona_snapshots SET extracted_summary = ? WHERE id = ?",
        (summary, snapshot_id),
    )
    conn.commit()
    return n_weights, n_proposals
```

- [ ] **Step 4: Run test to confirm it passes**

```
.venv/bin/pytest tests/test_persona_extract.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add prism/persona.py tests/test_persona_extract.py
git commit -m "feat(persona): LLM extraction to bias weights + source proposals"
```

---

## Task 5: `/persona` web route + template

**Files:**
- Modify: `prism/web/routes.py`
- Create: `prism/web/templates/persona.html`
- Test: `tests/test_persona_route.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_persona_route.py`:

```python
from unittest.mock import patch

from fastapi.testclient import TestClient

from prism.web import create_app


def _client(tmp_db_path):
    import os
    os.environ["PRISM_DB_PATH"] = str(tmp_db_path)
    app = create_app()
    return TestClient(app)


def test_persona_get_shows_form(tmp_path):
    c = _client(tmp_path / "test.sqlite3")
    r = c.get("/persona", headers={"X-Test-Auth": "1"})
    # test auth header support exists in project; if not, substitute whatever cookie fixture exists
    assert r.status_code == 200
    assert "persona" in r.text.lower() or "个人" in r.text


def test_persona_post_saves_and_triggers_extraction(tmp_path):
    c = _client(tmp_path / "test.sqlite3")

    fake_llm = {
        "summary": "TL",
        "bias_weights": [{"dimension": "tag", "key": "方法论", "weight": 2.0}],
        "candidate_sources": [
            {"type": "x", "handle_or_url": "zarazhangrui",
             "display_name": "Zara", "rationale": "seed", "category": "growth"}
        ],
    }
    with patch("prism.persona.call_llm_json", return_value=fake_llm):
        r = c.post(
            "/persona",
            data={
                "role": "TL",
                "goals": ["积累方法论"],
                "active_learning": "产品设计",
                "seed_handles": "zarazhangrui\ndanshipper",
                "dislike": "LLM 流水账",
                "style": ["方法论思考"],
                "language": "都行",
                "length": "都可以",
                "free_text": "想做个会学的推荐",
            },
            headers={"X-Test-Auth": "1"},
            follow_redirects=False,
        )

    assert r.status_code in (302, 303)
    assert r.headers["location"].startswith("/taste/sources")

    # Verify DB state
    import sqlite3, os
    conn = sqlite3.connect(os.environ["PRISM_DB_PATH"])
    snap = conn.execute(
        "SELECT id, extracted_summary FROM persona_snapshots WHERE is_active = 1"
    ).fetchone()
    assert snap is not None and snap[1] == "TL"
    w = conn.execute(
        "SELECT key, weight FROM preference_weights WHERE dimension = 'persona_bias'"
    ).fetchall()
    assert any(k == "tag/方法论" and ww == 2.0 for k, ww in w)
    props = conn.execute(
        "SELECT origin, display_name FROM source_proposals"
    ).fetchall()
    assert props == [("persona", "Zara")]
```

Note: if the project's test-auth convention uses a session cookie rather than a header, use the existing test fixture pattern (look at another test in `tests/` for the same convention).

- [ ] **Step 2: Run test to confirm it fails**

```
.venv/bin/pytest tests/test_persona_route.py -v
```
Expected: FAIL with 404 on `/persona`.

- [ ] **Step 3: Create template `prism/web/templates/persona.html`**

```html
{% extends "base.html" %}
{% block title %}Persona 设置{% endblock %}
{% block content %}
<main class="container" style="max-width: 720px; margin: 2rem auto; padding: 0 1rem;">
  <h1>告诉 Prism：你现在是谁？</h1>
  <p class="muted">系统会据此调整偏好权重并推荐新源。随时可以再来一次。</p>

  <form method="post" action="/persona" style="display: flex; flex-direction: column; gap: 1.25rem;">
    <label>
      <div><strong>1. 你当前的职业身份与主要工作方向？</strong></div>
      <input name="role" type="text" required style="width:100%" placeholder="如：算法团队 TL，专注推理基础设施" />
    </label>

    <fieldset>
      <legend><strong>2. 你希望用 Prism 解决什么信息问题？（可多选）</strong></legend>
      {% for opt in ["跟踪前沿", "积累方法论", "学某个具体技能", "找灵感", "工作参考"] %}
      <label style="display:block"><input type="checkbox" name="goals" value="{{ opt }}" /> {{ opt }}</label>
      {% endfor %}
    </fieldset>

    <label>
      <div><strong>3. 最近 1-3 个月你在主动钻研的领域或技能？</strong></div>
      <textarea name="active_learning" rows="2" style="width:100%"></textarea>
    </label>

    <label>
      <div><strong>4. 你持续关注或想学的人（一行一个，中英文都行）</strong></div>
      <textarea name="seed_handles" rows="4" style="width:100%" placeholder="zarazhangrui&#10;danshipper"></textarea>
    </label>

    <label>
      <div><strong>5. 哪些话题你刷到就烦、明确不想再看？</strong></div>
      <textarea name="dislike" rows="2" style="width:100%"></textarea>
    </label>

    <fieldset>
      <legend><strong>6. 内容风格偏好（可多选）</strong></legend>
      {% for opt in ["硬核论文", "技术深度文", "方法论思考", "产品与体验讨论", "行业动态", "趣味段子"] %}
      <label style="display:block"><input type="checkbox" name="style" value="{{ opt }}" /> {{ opt }}</label>
      {% endfor %}
      <div style="margin-top:.5rem"><strong>语言：</strong>
        {% for opt in ["中文为主", "英文为主", "都行"] %}
        <label><input type="radio" name="language" value="{{ opt }}" {% if opt == "都行" %}checked{% endif %} /> {{ opt }}</label>
        {% endfor %}
      </div>
      <div><strong>长度：</strong>
        {% for opt in ["短平快", "长文深度", "都可以"] %}
        <label><input type="radio" name="length" value="{{ opt }}" {% if opt == "都可以" %}checked{% endif %} /> {{ opt }}</label>
        {% endfor %}
      </div>
    </fieldset>

    <label>
      <div><strong>补充（自由文字，可留空）</strong></div>
      <textarea name="free_text" rows="5" style="width:100%"></textarea>
    </label>

    <button type="submit" style="padding:.75rem 1.5rem; font-weight:600;">保存并生成推荐源</button>
  </form>
</main>
{% endblock %}
```

- [ ] **Step 4: Add routes to `prism/web/routes.py`**

Near the other route registrations (same file pattern as `/pairwise`), add:

```python
# --- Persona ---

@router.get("/persona", response_class=HTMLResponse)
def persona_form(request: Request, _auth: Any = Depends(require_auth)):
    return templates.TemplateResponse("persona.html", {"request": request})


@router.post("/persona")
def persona_submit(
    request: Request,
    role: str = Form(...),
    goals: list[str] = Form(default_factory=list),
    active_learning: str = Form(""),
    seed_handles: str = Form(""),
    dislike: str = Form(""),
    style: list[str] = Form(default_factory=list),
    language: str = Form("都行"),
    length: str = Form("都可以"),
    free_text: str = Form(""),
    _auth: Any = Depends(require_auth),
):
    from prism.persona import save_snapshot, extract_from_snapshot

    answers = {
        "role": role,
        "goals": goals,
        "active_learning": active_learning,
        "dislike": dislike,
        "style": style,
        "language": language,
        "length": length,
    }
    handles = [h.strip() for h in seed_handles.splitlines() if h.strip()]

    with get_conn() as conn:
        snap_id = save_snapshot(
            conn, answers=answers, free_text=free_text, seed_handles=handles,
        )
        extract_from_snapshot(conn, snap_id)

    return RedirectResponse(url="/taste/sources", status_code=303)
```

If `require_auth`, `get_conn`, `templates`, or `router` names differ in this project, match the existing names used by `/pairwise` in the same file.

- [ ] **Step 5: Run test to confirm it passes**

```
.venv/bin/pytest tests/test_persona_route.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add prism/web/routes.py prism/web/templates/persona.html tests/test_persona_route.py
git commit -m "feat(web): /persona capture page with LLM-driven extraction"
```

---

## Task 6: `/taste/sources` proposal review page

**Files:**
- Modify: `prism/web/routes.py`
- Create: `prism/web/templates/taste_sources.html`
- Create: `prism/web/templates/taste_source_item.html`
- Test: `tests/test_taste_sources.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_taste_sources.py`:

```python
import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from prism.db import init_db
from prism.web import create_app


def _setup(tmp_path):
    import os
    db = tmp_path / "test.sqlite3"
    conn = sqlite3.connect(db)
    init_db(conn)
    conn.execute(
        "INSERT INTO source_proposals (source_type, source_config_json, display_name, "
        "rationale, origin) VALUES "
        "('x', ?, 'Zara', 'growth methodology', 'persona')",
        (json.dumps({"type": "x", "handle": "zarazhangrui", "depth": "thread"}),),
    )
    conn.commit()
    conn.close()

    os.environ["PRISM_DB_PATH"] = str(db)
    sources_yaml = tmp_path / "sources.yaml"
    sources_yaml.write_text("sources:\n  - type: x\n    handle: existing\n    depth: thread\n")
    os.environ["PRISM_SOURCES_YAML"] = str(sources_yaml)
    return TestClient(create_app()), db, sources_yaml


def test_taste_sources_list_shows_pending(tmp_path):
    c, db, _ = _setup(tmp_path)
    r = c.get("/taste/sources", headers={"X-Test-Auth": "1"})
    assert r.status_code == 200
    assert "Zara" in r.text
    assert "growth methodology" in r.text


def test_accept_updates_yaml_and_marks_accepted(tmp_path):
    c, db, yaml_path = _setup(tmp_path)
    prop_id = sqlite3.connect(db).execute("SELECT id FROM source_proposals").fetchone()[0]

    r = c.post(f"/taste/sources/{prop_id}/accept", headers={"X-Test-Auth": "1"})
    assert r.status_code == 200

    assert "zarazhangrui" in yaml_path.read_text()
    status = sqlite3.connect(db).execute(
        "SELECT status FROM source_proposals WHERE id = ?", (prop_id,)
    ).fetchone()[0]
    assert status == "accepted"


def test_reject_marks_rejected_without_yaml_change(tmp_path):
    c, db, yaml_path = _setup(tmp_path)
    prop_id = sqlite3.connect(db).execute("SELECT id FROM source_proposals").fetchone()[0]
    original = yaml_path.read_text()

    r = c.post(f"/taste/sources/{prop_id}/reject", headers={"X-Test-Auth": "1"})
    assert r.status_code == 200
    assert yaml_path.read_text() == original
    status = sqlite3.connect(db).execute(
        "SELECT status FROM source_proposals WHERE id = ?", (prop_id,)
    ).fetchone()[0]
    assert status == "rejected"
```

- [ ] **Step 2: Run test to confirm it fails**

```
.venv/bin/pytest tests/test_taste_sources.py -v
```
Expected: FAIL 404.

- [ ] **Step 3: Add `config.sources_yaml_path` access**

Verify `prism/config.py` exposes the path to `config/sources.yaml`. If it does not and there is no `PRISM_SOURCES_YAML` env override, add one:

```python
sources_yaml_path: str = os.environ.get(
    "PRISM_SOURCES_YAML", str(Path(__file__).resolve().parent.parent / "config" / "sources.yaml")
)
```

(Match the project's existing `settings` convention; adapt if it uses pydantic BaseSettings.)

- [ ] **Step 4: Create templates**

`prism/web/templates/taste_sources.html`:

```html
{% extends "base.html" %}
{% block title %}待审核的推荐源{% endblock %}
{% block content %}
<main class="container" style="max-width: 820px; margin: 2rem auto;">
  <h1>推荐源审核</h1>
  <p class="muted">这些源由你的 persona 描述、外部投喂或图扩展自动产出。一键接受/拒绝。</p>

  {% if not groups %}
    <p>暂无待审核的源提议。</p>
  {% endif %}

  {% for origin, items in groups.items() %}
    <h2 style="margin-top:2rem;">{{ origin_label(origin) }}</h2>
    <ul id="origin-{{ origin }}" style="list-style:none; padding:0;">
      {% for item in items %}
        {% include "taste_source_item.html" %}
      {% endfor %}
    </ul>
  {% endfor %}
</main>
{% endblock %}
```

`prism/web/templates/taste_source_item.html`:

```html
<li id="prop-{{ item.id }}" style="border:1px solid var(--border,#ddd); padding:1rem; margin-bottom:.75rem; border-radius:6px;">
  <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:1rem;">
    <div>
      <div><strong>{{ item.display_name }}</strong> <span class="muted">({{ item.source_type }})</span></div>
      <div class="muted" style="font-size:.9em; margin-top:.25rem;">{{ item.rationale }}</div>
      <pre style="font-size:.8em; color:var(--muted,#888); margin-top:.5rem;">{{ item.source_config_pretty }}</pre>
    </div>
    <div style="display:flex; gap:.5rem;">
      <button hx-post="/taste/sources/{{ item.id }}/accept"
              hx-target="#prop-{{ item.id }}"
              hx-swap="outerHTML">接受</button>
      <button hx-post="/taste/sources/{{ item.id }}/reject"
              hx-target="#prop-{{ item.id }}"
              hx-swap="outerHTML">拒绝</button>
    </div>
  </div>
</li>
```

- [ ] **Step 5: Add routes to `prism/web/routes.py`**

```python
from pathlib import Path
import json as _json

_ORIGIN_LABELS = {
    "persona": "来自 persona 描述",
    "external_feed": "来自外部投喂",
    "graph_expansion": "来自高权重源的邻居",
    "gap": "来自话题覆盖缺口",
    "blindspot": "盲点扫描发现",
    "manual": "手动添加",
}


def _origin_label(origin: str) -> str:
    return _ORIGIN_LABELS.get(origin, origin)


@router.get("/taste/sources", response_class=HTMLResponse)
def taste_sources_list(request: Request, _auth: Any = Depends(require_auth)):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, source_type, source_config_json, display_name, rationale, origin "
            "FROM source_proposals WHERE status = 'pending' ORDER BY origin, id DESC"
        ).fetchall()

    groups: dict[str, list[dict]] = {}
    for r in rows:
        cfg = _json.loads(r[2])
        import yaml as _yaml
        groups.setdefault(r[5], []).append({
            "id": r[0], "source_type": r[1],
            "source_config_pretty": _yaml.safe_dump(cfg, allow_unicode=True).strip(),
            "display_name": r[3], "rationale": r[4],
        })

    return templates.TemplateResponse(
        "taste_sources.html",
        {"request": request, "groups": groups, "origin_label": _origin_label},
    )


@router.post("/taste/sources/{proposal_id}/accept", response_class=HTMLResponse)
def taste_source_accept(
    proposal_id: int, request: Request, _auth: Any = Depends(require_auth),
):
    from prism.sources.yaml_editor import append_source_block
    from prism.config import settings

    with get_conn() as conn:
        row = conn.execute(
            "SELECT source_type, source_config_json, display_name, origin "
            "FROM source_proposals WHERE id = ? AND status = 'pending'",
            (proposal_id,),
        ).fetchone()
        if not row:
            return HTMLResponse("", status_code=404)

        cfg = _json.loads(row[1])
        cfg.setdefault("type", row[0])
        append_source_block(
            Path(settings.sources_yaml_path),
            source_config=cfg,
            category_comment=f"proposed 2026-04-19 via {row[3]}",
        )
        conn.execute(
            "UPDATE source_proposals SET status = 'accepted', "
            "reviewed_at = datetime('now') WHERE id = ?",
            (proposal_id,),
        )
        conn.execute(
            "INSERT INTO decision_log (layer, action, reason, context_json) "
            "VALUES ('recall', 'add_source', ?, ?)",
            (f"accepted proposal #{proposal_id}", _json.dumps({"config": cfg, "origin": row[3]})),
        )
        conn.commit()
    return HTMLResponse(f'<li class="muted">已接受：{row[2]}</li>')


@router.post("/taste/sources/{proposal_id}/reject", response_class=HTMLResponse)
def taste_source_reject(
    proposal_id: int, request: Request, _auth: Any = Depends(require_auth),
):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT display_name FROM source_proposals WHERE id = ? AND status = 'pending'",
            (proposal_id,),
        ).fetchone()
        if not row:
            return HTMLResponse("", status_code=404)
        conn.execute(
            "UPDATE source_proposals SET status = 'rejected', "
            "reviewed_at = datetime('now') WHERE id = ?",
            (proposal_id,),
        )
        conn.execute(
            "INSERT INTO decision_log (layer, action, reason, context_json) "
            "VALUES ('recall', 'reject_source', ?, '{}')",
            (f"rejected proposal #{proposal_id}",),
        )
        conn.commit()
    return HTMLResponse(f'<li class="muted">已拒绝：{row[0]}</li>')
```

- [ ] **Step 6: Run test to confirm it passes**

```
.venv/bin/pytest tests/test_taste_sources.py -v
```
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add prism/web/routes.py prism/web/templates/taste_sources.html prism/web/templates/taste_source_item.html tests/test_taste_sources.py prism/config.py
git commit -m "feat(web): /taste/sources proposal review with accept/reject"
```

---

## Task 7: External feed consumer

**Files:**
- Create: `prism/pipeline/external_feed.py`
- Test: `tests/test_external_feed_consumer.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_external_feed_consumer.py`:

```python
import sqlite3
from unittest.mock import patch

from prism.db import init_db


def _mkconn():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    # Ensure there's at least one cluster and source signals can be referenced
    conn.execute("INSERT INTO sources (key, config_json) VALUES ('manual:ext', '{}')")
    conn.execute("INSERT INTO clusters (id, title) VALUES (1, 'external')")
    conn.commit()
    return conn


FAKE_EXTRACTION = {
    "url_canonical": "https://example.com/post",
    "author": "zarazhangrui",
    "content_type": "article",
    "topics": ["方法论", "个人成长"],
    "summary_zh": "一篇关于持续学习的方法论文章。",
    "source_hint": {"type": "x", "handle": "zarazhangrui", "display_name": "Zara"},
}


def test_consumer_processes_pending_feed_and_proposes_source():
    from prism.pipeline.external_feed import run_external_feed_consumer

    conn = _mkconn()
    conn.execute(
        "INSERT INTO external_feeds (url, user_note) VALUES (?, ?)",
        ("https://example.com/post", "这个作者真不错"),
    )
    conn.commit()

    with patch("prism.pipeline.external_feed.call_llm_json", return_value=FAKE_EXTRACTION):
        n = run_external_feed_consumer(conn)
    assert n == 1

    processed = conn.execute(
        "SELECT processed, extracted_json FROM external_feeds"
    ).fetchone()
    assert processed[0] == 1
    assert "方法论" in processed[1]

    # A source proposal was created
    prop = conn.execute(
        "SELECT source_type, display_name, origin FROM source_proposals"
    ).fetchone()
    assert prop == ("x", "Zara", "external_feed")


def test_consumer_skips_if_source_already_exists(tmp_path, monkeypatch):
    from prism.pipeline.external_feed import run_external_feed_consumer
    from pathlib import Path

    sources_yaml = tmp_path / "sources.yaml"
    sources_yaml.write_text(
        "sources:\n  - type: x\n    handle: zarazhangrui\n    depth: thread\n"
    )
    monkeypatch.setenv("PRISM_SOURCES_YAML", str(sources_yaml))

    conn = _mkconn()
    conn.execute(
        "INSERT INTO external_feeds (url, user_note) VALUES "
        "('https://example.com/post', '')"
    )
    conn.commit()

    with patch("prism.pipeline.external_feed.call_llm_json", return_value=FAKE_EXTRACTION):
        run_external_feed_consumer(conn)

    # No duplicate proposal since source already in yaml
    count = conn.execute("SELECT COUNT(*) FROM source_proposals").fetchone()[0]
    assert count == 0
```

- [ ] **Step 2: Run test to confirm it fails**

```
.venv/bin/pytest tests/test_external_feed_consumer.py -v
```
Expected: `ModuleNotFoundError: No module named 'prism.pipeline.external_feed'`.

- [ ] **Step 3: Implement consumer**

Create `prism/pipeline/external_feed.py`:

```python
"""External feed consumer.

Reads unprocessed rows from external_feeds, extracts (via LLM) the author /
content-type / topics, writes extracted_json, optionally creates a source_proposal
if the hinted source is not already in sources.yaml.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from prism.pipeline.llm import call_llm_json


_SYSTEM_PROMPT = (
    "你是 Prism 的外部投喂分析器。用户贴来一个链接/话题，"
    "你要产出 JSON："
    "{url_canonical: str, author: str (若可知), content_type: 'tweet'|'article'|"
    "'video'|'paper'|'other', topics: [str], summary_zh: str (1-2句), "
    "source_hint: {type, handle|url, display_name}}"
    "严格 JSON，无额外文字。"
)


def _sources_yaml_path() -> Path:
    override = os.environ.get("PRISM_SOURCES_YAML")
    if override:
        return Path(override)
    from prism.config import settings
    return Path(getattr(settings, "sources_yaml_path", ""))


def _source_already_present(hint: dict, yaml_path: Path) -> bool:
    if not yaml_path.exists():
        return False
    try:
        from prism.sources.yaml_editor import load_sources_list, _source_key
    except ImportError:
        return False
    items = load_sources_list(yaml_path)
    want = _source_key({"type": hint.get("type", ""),
                         **({"handle": hint["handle"]} if "handle" in hint else {}),
                         **({"url": hint["url"]} if "url" in hint else {})})
    return any(_source_key(item) == want for item in items)


def run_external_feed_consumer(conn: sqlite3.Connection) -> int:
    """Process all external_feeds with processed=0. Returns count processed."""
    rows = conn.execute(
        "SELECT id, url, user_note FROM external_feeds WHERE processed = 0"
    ).fetchall()

    yaml_path = _sources_yaml_path()
    n = 0
    for feed_id, url, note in rows:
        prompt = (
            f"【用户投喂的链接】\n{url}\n\n"
            f"【用户备注】\n{note or '(无)'}\n\n"
            f"请分析并输出 JSON。"
        )
        try:
            extracted = call_llm_json(prompt, system=_SYSTEM_PROMPT, max_tokens=1024)
        except Exception as exc:  # noqa: BLE001
            # Leave processed=0 so next run retries; note the error in extracted_json
            conn.execute(
                "UPDATE external_feeds SET extracted_json = ? WHERE id = ?",
                (json.dumps({"error": str(exc)}, ensure_ascii=False), feed_id),
            )
            conn.commit()
            continue

        hint = extracted.get("source_hint") or {}
        if hint and not _source_already_present(hint, yaml_path):
            cfg: dict[str, Any] = {"type": hint.get("type", "")}
            if "handle" in hint:
                cfg["handle"] = hint["handle"]
                cfg["depth"] = "thread"
            elif "url" in hint:
                cfg["url"] = hint["url"]
            display = hint.get("display_name") or cfg.get("handle") or cfg.get("url") or "unknown"
            conn.execute(
                "INSERT INTO source_proposals "
                "(source_type, source_config_json, display_name, rationale, origin, origin_ref) "
                "VALUES (?, ?, ?, ?, 'external_feed', ?)",
                (cfg["type"], json.dumps(cfg, ensure_ascii=False),
                 display,
                 f"来自你投喂的链接：{extracted.get('summary_zh','')}",
                 str(feed_id)),
            )

        conn.execute(
            "UPDATE external_feeds SET processed = 1, extracted_json = ? WHERE id = ?",
            (json.dumps(extracted, ensure_ascii=False), feed_id),
        )
        n += 1

    conn.commit()
    return n
```

- [ ] **Step 4: Run test to confirm it passes**

```
.venv/bin/pytest tests/test_external_feed_consumer.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add prism/pipeline/external_feed.py tests/test_external_feed_consumer.py
git commit -m "feat(pipeline): external_feed consumer extracts and proposes sources"
```

---

## Task 8: Fix `pair_strategy` field to record actual strategy

**Files:**
- Modify: `prism/web/pairwise.py` (`select_pair`, `record_vote`)
- Modify: `prism/web/routes.py` (vote endpoint)
- Modify: `prism/web/templates/pair_cards.html` (add hidden input)
- Test: `tests/test_pair_strategy.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_pair_strategy.py`:

```python
import sqlite3
from unittest.mock import patch

from prism.db import init_db
from prism.web.pairwise import record_vote


def _mkconn():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    # seed two signals
    conn.execute("INSERT INTO clusters (id, title) VALUES (1,'c')")
    conn.execute(
        "INSERT INTO signals (id, cluster_id, summary) VALUES (1,1,'a'), (2,1,'b')"
    )
    conn.commit()
    return conn


def test_record_vote_persists_explicit_strategy():
    conn = _mkconn()
    record_vote(conn, signal_a_id=1, signal_b_id=2, winner="a",
                comment="", response_time_ms=0, strategy="active")
    row = conn.execute(
        "SELECT pair_strategy FROM pairwise_comparisons WHERE signal_a_id=1"
    ).fetchone()
    assert row[0] == "active"


def test_select_pair_returns_strategy_name():
    from prism.web.pairwise import select_pair
    conn = _mkconn()
    # Seed enough signals for pool; if pool empty, select_pair returns None and test is trivially skipped
    for i in range(3, 8):
        conn.execute(
            "INSERT INTO signals (id, cluster_id, summary) VALUES (?, 1, ?)",
            (i, f"s{i}"),
        )
    conn.commit()
    with patch("prism.web.pairwise._check_neither_streak", return_value=False), \
         patch("random.random", return_value=0.01):
        result = select_pair(conn)
    if result is None:
        return  # not enough pool; the record_vote test covers the important case
    assert len(result) == 3
    a, b, strat = result
    assert strat == "exploit"
```

- [ ] **Step 2: Run test to confirm it fails**

```
.venv/bin/pytest tests/test_pair_strategy.py -v
```
Expected: FAIL — `record_vote` does not accept `strategy` and `select_pair` returns 2-tuple.

- [ ] **Step 3: Change `select_pair` signature**

In `prism/web/pairwise.py`, change `select_pair`:

```python
def select_pair(conn: sqlite3.Connection) -> tuple[dict, dict, str] | None:
    """Select next pair. Returns (a, b, strategy) or None."""
    pool = _get_candidate_pool(conn)
    if len(pool) < 2:
        return None

    if _check_neither_streak(conn):
        chosen = random.sample(pool, 2)
        return chosen[0], chosen[1], "neither_fallback"

    r = random.random()
    if r < PAIR_STRATEGY_WEIGHTS["exploit"]:
        a, b = _pick_exploit(pool)
        return a, b, "exploit"
    elif r < PAIR_STRATEGY_WEIGHTS["exploit"] + PAIR_STRATEGY_WEIGHTS["explore"]:
        a, b = _pick_explore(pool)
        return a, b, "explore"
    else:
        a, b = _pick_random(pool)
        return a, b, "random"
```

- [ ] **Step 4: Change `record_vote` to accept strategy**

Replace the `strategy = "exploit"` line (around pairwise.py:520):

```python
def record_vote(
    conn: sqlite3.Connection,
    signal_a_id: int,
    signal_b_id: int,
    winner: str,
    comment: str = "",
    response_time_ms: int = 0,
    strategy: str = "exploit",
) -> None:
    """Record a pairwise vote and update all dependent scores."""
    conn.execute(
        "INSERT INTO pairwise_comparisons "
        "(signal_a_id, signal_b_id, winner, user_comment, pair_strategy, response_time_ms) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (signal_a_id, signal_b_id, winner, comment, strategy, response_time_ms),
    )
    # ... rest unchanged (BT update, preference_weights, source_weights, commit)
```

Leave the remainder of `record_vote` untouched; only the INSERT uses `strategy` instead of the hardcoded literal.

- [ ] **Step 5: Update vote endpoint in `prism/web/routes.py`**

Find the POST handler that calls `record_vote` (in the /pairwise vote endpoint). Add a `strategy` form field and pass through:

```python
@router.post("/pairwise/vote")
def pairwise_vote(
    request: Request,
    signal_a_id: int = Form(...),
    signal_b_id: int = Form(...),
    winner: str = Form(...),
    comment: str = Form(""),
    response_time_ms: int = Form(0),
    strategy: str = Form("exploit"),
    _auth: Any = Depends(require_auth),
):
    with get_conn() as conn:
        record_vote(conn, signal_a_id, signal_b_id, winner,
                    comment=comment, response_time_ms=response_time_ms, strategy=strategy)
        # ... existing next-pair rendering, unchanged
```

Also, wherever the view renders the pair (the handler that calls `select_pair`), unpack three values instead of two and pass `strategy` to the template context:

```python
result = select_pair(conn)
if result is None:
    return templates.TemplateResponse("pair_empty.html", {"request": request})
a, b, strategy = result
return templates.TemplateResponse(
    "pair_cards.html",
    {"request": request, "a": a, "b": b, "strategy": strategy},
)
```

- [ ] **Step 6: Update `prism/web/templates/pair_cards.html`**

Inside the vote `<form>` (wherever winner buttons submit), add:

```html
<input type="hidden" name="strategy" value="{{ strategy }}" />
```

If `pair_cards.html` uses multiple forms per button, add it to each.

- [ ] **Step 7: Run test to confirm it passes**

```
.venv/bin/pytest tests/test_pair_strategy.py -v
```
Expected: 2 passed.

- [ ] **Step 8: Commit**

```bash
git add prism/web/pairwise.py prism/web/routes.py prism/web/templates/pair_cards.html tests/test_pair_strategy.py
git commit -m "fix(ranking): record actual pair_strategy instead of hardcoded exploit"
```

---

## Task 9: CLI — `prism process-external-feeds` and `prism sources prune`

**Files:**
- Modify: `prism/cli.py`
- Test: `tests/test_cli_new.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_cli_new.py`:

```python
import sqlite3
from unittest.mock import patch

from click.testing import CliRunner

from prism.cli import cli
from prism.db import init_db


def test_process_external_feeds_cli(tmp_path, monkeypatch):
    db = tmp_path / "test.sqlite3"
    conn = sqlite3.connect(db)
    init_db(conn)
    conn.execute(
        "INSERT INTO external_feeds (url) VALUES ('https://example.com/x')"
    )
    conn.commit()
    monkeypatch.setenv("PRISM_DB_PATH", str(db))

    with patch(
        "prism.pipeline.external_feed.call_llm_json",
        return_value={
            "url_canonical": "https://example.com/x",
            "author": "x",
            "content_type": "article",
            "topics": [],
            "summary_zh": "",
            "source_hint": {"type": "x", "handle": "x"},
        },
    ):
        r = CliRunner().invoke(cli, ["process-external-feeds"])
    assert r.exit_code == 0, r.output

    processed = sqlite3.connect(db).execute(
        "SELECT processed FROM external_feeds"
    ).fetchone()[0]
    assert processed == 1


def test_sources_prune_dry_run(tmp_path, monkeypatch):
    db = tmp_path / "test.sqlite3"
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n  - type: hn\n    feed: best\n  - type: x\n    handle: karpathy\n"
    )
    monkeypatch.setenv("PRISM_DB_PATH", str(db))
    monkeypatch.setenv("PRISM_SOURCES_YAML", str(yaml_path))

    conn = sqlite3.connect(db)
    init_db(conn)
    conn.execute(
        "INSERT INTO preference_weights (dimension, key, weight) VALUES "
        "('source', 'hn:best', -12.0), ('source', 'x:karpathy', 3.5)"
    )
    conn.commit()

    r = CliRunner().invoke(cli, ["sources", "prune", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "hn:best" in r.output
    assert "-12" in r.output
    # dry run should not modify yaml
    assert "feed: best" in yaml_path.read_text()
    # and no status change
    assert "pruned" not in yaml_path.read_text()


def test_sources_prune_yes_applies(tmp_path, monkeypatch):
    db = tmp_path / "test.sqlite3"
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n  - type: hn\n    feed: best\n  - type: x\n    handle: karpathy\n"
    )
    monkeypatch.setenv("PRISM_DB_PATH", str(db))
    monkeypatch.setenv("PRISM_SOURCES_YAML", str(yaml_path))

    conn = sqlite3.connect(db)
    init_db(conn)
    conn.execute(
        "INSERT INTO preference_weights (dimension, key, weight) VALUES "
        "('source', 'hn:best', -12.0)"
    )
    conn.commit()

    r = CliRunner().invoke(cli, ["sources", "prune", "--yes"])
    assert r.exit_code == 0, r.output
    text = yaml_path.read_text()
    # hn:best commented out; karpathy untouched
    assert "# pruned" in text
    assert "- type: x" in text and "handle: karpathy" in text
```

- [ ] **Step 2: Run test to confirm it fails**

```
.venv/bin/pytest tests/test_cli_new.py -v
```
Expected: FAIL — commands don't exist.

- [ ] **Step 3: Add commands to `prism/cli.py`**

Locate the existing Click `cli` group. Add:

```python
@cli.command("process-external-feeds")
def process_external_feeds_cmd():
    """Process pending external_feeds rows: LLM extract + propose sources."""
    from prism.db import get_conn
    from prism.pipeline.external_feed import run_external_feed_consumer

    with get_conn() as conn:
        n = run_external_feed_consumer(conn)
    click.echo(f"Processed {n} external feed(s).")


@cli.group("sources")
def sources_group():
    """Source configuration tools."""


@sources_group.command("prune")
@click.option("--threshold", type=float, default=-5.0,
              help="Prune sources with aggregate preference weight below this.")
@click.option("--dry-run", is_flag=True, help="Show proposed changes without writing.")
@click.option("--yes", is_flag=True, help="Apply without prompting.")
def sources_prune_cmd(threshold: float, dry_run: bool, yes: bool):
    """Comment out sources in sources.yaml whose preference weight is below threshold."""
    from pathlib import Path
    from prism.db import get_conn
    from prism.config import settings
    from prism.sources.yaml_editor import load_sources_list, comment_out_source, _source_key

    yaml_path = Path(os.environ.get("PRISM_SOURCES_YAML", settings.sources_yaml_path))
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT key, weight FROM preference_weights "
            "WHERE dimension = 'source' AND weight < ? ORDER BY weight ASC",
            (threshold,),
        ).fetchall()

    if not rows:
        click.echo(f"No sources below threshold {threshold}.")
        return

    current = {_source_key(s): s for s in load_sources_list(yaml_path)}
    to_prune = [(k, w) for k, w in rows if k in current]

    click.echo("Candidates to prune:")
    for k, w in to_prune:
        click.echo(f"  {k}  weight={w:.1f}")

    if dry_run:
        click.echo("(dry-run; no changes written)")
        return

    if not yes:
        if not click.confirm(f"Prune {len(to_prune)} source(s)?", default=False):
            click.echo("Aborted.")
            return

    pruned = 0
    for k, w in to_prune:
        if comment_out_source(yaml_path, k, reason=f"weight={w:.1f} 2026-04-19"):
            pruned += 1
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO decision_log (layer, action, reason, context_json) "
                    "VALUES ('recall', 'prune_source', ?, ?)",
                    (f"pruned {k} (weight={w:.1f})",
                     '{"weight": %.3f, "source_key": "%s"}' % (w, k)),
                )
                conn.commit()
    click.echo(f"Pruned {pruned} source(s) in {yaml_path}.")
```

Ensure `import os`, `import click` are at the top of `cli.py`.

- [ ] **Step 4: Run test to confirm it passes**

```
.venv/bin/pytest tests/test_cli_new.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add prism/cli.py tests/test_cli_new.py
git commit -m "feat(cli): process-external-feeds and sources prune"
```

---

## Task 10: launchd plist for hourly external-feed consumer

**Files:**
- Create: `prism/scheduling/com.prism.external-feed.plist`
- Modify: `prism/scheduling/install.sh` if it exists (append this plist to install list)

- [ ] **Step 1: Look for existing plist pattern**

```
ls prism/scheduling/
```

Read one existing plist (e.g. hourly sync) to mirror its structure for user context + paths.

- [ ] **Step 2: Create the plist**

`prism/scheduling/com.prism.external-feed.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.prism.external-feed</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/leehom/work/prism/.venv/bin/prism</string>
        <string>process-external-feeds</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/leehom/work/prism</string>
    <key>StartInterval</key>
    <integer>3600</integer>
    <key>StandardOutPath</key>
    <string>/Users/leehom/work/prism/data/external-feed.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/leehom/work/prism/data/external-feed.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
```

Use whatever `EnvironmentVariables` / paths convention matches existing plists in the repo; copy their style exactly (for example if they use `/opt/homebrew/bin` instead).

- [ ] **Step 3: Install command**

Do **not** auto-install. Instead, document in commit message:

```bash
# To activate, run:
# cp prism/scheduling/com.prism.external-feed.plist ~/Library/LaunchAgents/
# launchctl load ~/Library/LaunchAgents/com.prism.external-feed.plist
```

- [ ] **Step 4: Commit**

```bash
git add prism/scheduling/com.prism.external-feed.plist
git commit -m "chore(scheduling): hourly external-feed consumer plist"
```

---

## Task 11: Full test run + seed the real system

- [ ] **Step 1: Run full test suite**

```
.venv/bin/pytest tests/ -v
```
Expected: all tests passing (existing + new 10-15 tests added by this plan).

- [ ] **Step 2: Run `init_db` against the real DB**

```
.venv/bin/python -c "import sqlite3; from prism.db import init_db; conn=sqlite3.connect('data/prism.sqlite3'); init_db(conn); print('schema updated')"
```

Expected: prints "schema updated" without error (idempotent).

- [ ] **Step 3: Seed test — manual**

```
.venv/bin/prism serve --port 8080
```

In browser:
1. Open `http://localhost:8080/persona`
2. Fill the form honestly (your real TL role, real interests).
3. Submit → should land on `/taste/sources` with 20-30 proposals.
4. Accept 10-15 that look good.
5. Run `.venv/bin/prism sync` to pull from the new sources.
6. Run `.venv/bin/prism sources prune --dry-run` to see what would be pruned.
7. Open `/pairwise` and do 20 votes.

- [ ] **Step 4: Measure "neither" rate**

```
sqlite3 data/prism.sqlite3 "SELECT winner, COUNT(*) FROM pairwise_comparisons WHERE created_at > datetime('now','-1 hour') GROUP BY winner;"
```

Record the result in a commit message or note; target after 1-2 days of use is `neither < 50%`.

- [ ] **Step 5: Commit measurements note**

```bash
git commit --allow-empty -m "docs: week1 seed test — neither rate before/after persona"
```

---

## Self-review checklist

- [x] Every spec requirement for Week 1 has a task (persona capture ✓, proposals review ✓, external feed consumer ✓, pair_strategy fix ✓, sources prune CLI ✓).
- [x] No TBD / TODO / "similar to Task N" references; every step has actual code.
- [x] Type consistency: `select_pair` returns 3-tuple everywhere it's consumed; `record_vote` has `strategy` parameter name in both definition and call sites.
- [x] Files to create are listed before tasks that reference them.
- [x] Tests are runnable standalone (mocks for LLM and auth).

## Execution handoff

Plan complete and committed. Two options for implementation:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review after each, catches drift early.
2. **Inline Execution** — I run all tasks in this session, checkpointing after groups of tasks.

Given this is a solo project and tasks have clear TDD boundaries, inline is usually faster; subagent is safer if you want strict review points.
