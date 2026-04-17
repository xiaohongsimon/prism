# Prism v2 Entity Core — Implementation Plan (1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent entity tracking to Prism — auto-extract entities from signals, normalize with aliases, track lifecycle, and expose via CLI.

**Architecture:** Insert `entity_link` step after `analyze` in existing pipeline. 4 new DB tables (entity_profiles, entity_aliases, entity_candidates, entity_events) + FTS. Entity extraction uses deterministic candidate detection + LLM selection. Lifecycle is pure metrics (no LLM). Migration from entities.yaml on first run.

**Tech Stack:** Python 3.12, SQLite, Click CLI, pytest, existing `call_llm_json` from `prism.pipeline.llm`

**Spec:** `docs/specs/2026-03-29-prism-v2-entity-system.md`

**Scope:** This plan covers entity core only. Source adapters (YouTube, HN, etc.) and Briefing v2 are separate plans.

---

## File Structure

```
prism/
├── models.py                      # MODIFY: add EntityProfile, EntityAlias, EntityCandidate, EntityEvent dataclasses
├── db.py                          # MODIFY: add entity tables to init_db(), add entity helper functions
├── pipeline/
│   ├── entity_normalize.py        # CREATE: normalize(), resolve(), alias CRUD
│   ├── entity_extract.py          # CREATE: deterministic_candidates(), LLM extraction prompt, post-processing
│   ├── entity_lifecycle.py        # CREATE: update_lifecycle_scores(), update_entity_statuses()
│   ├── entity_link.py             # CREATE: run_entity_link() — orchestrator
│   └── entities.py                # MODIFY: add load_entities_from_db(), migrate_yaml_to_db()
├── cli.py                         # MODIFY: add `entity-link`, `entity list`, `practice` commands
tests/
├── test_entity_normalize.py       # CREATE
├── test_entity_extract.py         # CREATE
├── test_entity_lifecycle.py       # CREATE
├── test_entity_link.py            # CREATE
├── test_entity_migration.py       # CREATE
```

---

### Task 1: Entity DB Schema

**Files:**
- Modify: `prism/db.py`
- Modify: `prism/models.py`
- Test: `tests/test_db.py` (existing, extend)

- [ ] **Step 1: Write test for new tables existence**

```python
# tests/test_entity_schema.py
import sqlite3
from prism.db import get_connection


def test_entity_tables_exist(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "entity_profiles" in tables
    assert "entity_aliases" in tables
    assert "entity_candidates" in tables
    assert "entity_events" in tables


def test_entity_profile_insert(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO entity_profiles (canonical_name, display_name, category, first_seen_at) "
        "VALUES ('vllm', 'vLLM', 'project', datetime('now'))")
    conn.commit()
    row = conn.execute("SELECT * FROM entity_profiles WHERE canonical_name = 'vllm'").fetchone()
    assert row["display_name"] == "vLLM"
    assert row["category"] == "project"
    assert row["status"] == "emerging"
    assert row["needs_review"] == 1


def test_entity_alias_insert(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO entity_profiles (id, canonical_name, display_name, category, first_seen_at) "
        "VALUES (1, 'vllm', 'vLLM', 'project', datetime('now'))")
    conn.execute(
        "INSERT INTO entity_aliases (alias_norm, entity_id, surface_form, created_at) "
        "VALUES ('vllm', 1, 'vLLM', datetime('now'))")
    conn.commit()
    row = conn.execute("SELECT * FROM entity_aliases WHERE alias_norm = 'vllm'").fetchone()
    assert row["entity_id"] == 1
    assert row["surface_form"] == "vLLM"


def test_entity_candidate_expires(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO entity_candidates (name_norm, display_name, category, first_seen_at, last_seen_at, expires_at) "
        "VALUES ('newproject', 'NewProject', 'project', datetime('now'), datetime('now'), datetime('now', '+30 days'))")
    conn.commit()
    row = conn.execute("SELECT * FROM entity_candidates WHERE name_norm = 'newproject'").fetchone()
    assert row is not None


def test_entity_event_insert(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO entity_profiles (id, canonical_name, display_name, category, first_seen_at) "
        "VALUES (1, 'vllm', 'vLLM', 'project', datetime('now'))")
    conn.execute(
        "INSERT INTO entity_events (entity_id, date, event_type, impact, description) "
        "VALUES (1, '2026-03-29', 'release', 'high', 'vLLM 0.8 released')")
    conn.commit()
    row = conn.execute("SELECT * FROM entity_events WHERE entity_id = 1").fetchone()
    assert row["event_type"] == "release"
    assert row["impact"] == "high"


def test_category_check_constraint(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO entity_profiles (canonical_name, display_name, category, first_seen_at) "
            "VALUES ('bad', 'Bad', 'invalid_category', datetime('now'))")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd $PROJECT_ROOT && python -m pytest tests/test_entity_schema.py -v`
Expected: FAIL — tables don't exist

- [ ] **Step 3: Add entity tables to db.py init_db()**

Add after the existing `signal_search` trigger block in `prism/db.py`:

```python
        -- Entity system tables (v2)
        CREATE TABLE IF NOT EXISTS entity_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_name TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            category TEXT NOT NULL CHECK(category IN ('person','org','project','model','technique','dataset')),
            status TEXT DEFAULT 'emerging' CHECK(status IN ('emerging','growing','mature','declining')),
            summary TEXT DEFAULT '',
            needs_review INTEGER DEFAULT 1,
            first_seen_at TEXT NOT NULL,
            last_event_at TEXT,
            event_count_7d INTEGER DEFAULT 0,
            event_count_30d INTEGER DEFAULT 0,
            event_count_total INTEGER DEFAULT 0,
            m7_score REAL DEFAULT 0.0,
            m30_score REAL DEFAULT 0.0,
            metadata_json TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS entity_aliases (
            alias_norm TEXT NOT NULL,
            entity_id INTEGER NOT NULL REFERENCES entity_profiles(id),
            surface_form TEXT NOT NULL,
            source TEXT DEFAULT 'llm',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (alias_norm, entity_id)
        );
        CREATE INDEX IF NOT EXISTS idx_alias_norm ON entity_aliases(alias_norm);

        CREATE TABLE IF NOT EXISTS entity_candidates (
            name_norm TEXT PRIMARY KEY,
            display_name TEXT DEFAULT '',
            category TEXT DEFAULT '',
            mention_count INTEGER DEFAULT 1,
            first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
            sample_signals_json TEXT DEFAULT '[]',
            expires_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS entity_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL REFERENCES entity_profiles(id),
            signal_id INTEGER REFERENCES signals(id),
            date TEXT NOT NULL,
            event_type TEXT NOT NULL,
            role TEXT DEFAULT 'subject',
            impact TEXT DEFAULT 'medium' CHECK(impact IN ('high','medium','low')),
            confidence REAL DEFAULT 0.8,
            description TEXT DEFAULT '',
            metadata_json TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_entity_events_entity ON entity_events(entity_id, date);
        CREATE INDEX IF NOT EXISTS idx_entity_events_date ON entity_events(date);

        CREATE VIRTUAL TABLE IF NOT EXISTS entity_search USING fts5(
            canonical_name, display_name, summary,
            content=entity_profiles, content_rowid=id
        );

        CREATE TRIGGER IF NOT EXISTS entity_profiles_ai AFTER INSERT ON entity_profiles BEGIN
            INSERT INTO entity_search(rowid, canonical_name, display_name, summary)
            VALUES (new.id, new.canonical_name, new.display_name, new.summary);
        END;
        CREATE TRIGGER IF NOT EXISTS entity_profiles_ad AFTER DELETE ON entity_profiles BEGIN
            INSERT INTO entity_search(entity_search, rowid, canonical_name, display_name, summary)
            VALUES('delete', old.id, old.canonical_name, old.display_name, old.summary);
        END;
```

- [ ] **Step 4: Add dataclasses to models.py**

Append to `prism/models.py`:

```python
@dataclass
class EntityProfile:
    id: Optional[int] = None
    canonical_name: str = ""
    display_name: str = ""
    category: str = ""  # person|org|project|model|technique|dataset
    status: str = "emerging"
    summary: str = ""
    needs_review: bool = True
    first_seen_at: Optional[datetime] = None
    last_event_at: Optional[datetime] = None
    event_count_7d: int = 0
    event_count_30d: int = 0
    event_count_total: int = 0
    m7_score: float = 0.0
    m30_score: float = 0.0
    metadata_json: str = "{}"


@dataclass
class EntityAlias:
    alias_norm: str = ""
    entity_id: int = 0
    surface_form: str = ""
    source: str = "llm"


@dataclass
class EntityCandidate:
    name_norm: str = ""
    display_name: str = ""
    category: str = ""
    mention_count: int = 1
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    sample_signals_json: str = "[]"
    expires_at: Optional[datetime] = None


@dataclass
class EntityEvent:
    id: Optional[int] = None
    entity_id: int = 0
    signal_id: Optional[int] = None
    date: str = ""
    event_type: str = ""
    role: str = "subject"
    impact: str = "medium"
    confidence: float = 0.8
    description: str = ""
    metadata_json: str = "{}"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd $PROJECT_ROOT && python -m pytest tests/test_entity_schema.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add prism/db.py prism/models.py tests/test_entity_schema.py
git commit -m "feat(entity): add entity schema — profiles, aliases, candidates, events tables"
```

---

### Task 2: Entity Normalization

**Files:**
- Create: `prism/pipeline/entity_normalize.py`
- Test: `tests/test_entity_normalize.py`

- [ ] **Step 1: Write tests for normalize()**

```python
# tests/test_entity_normalize.py
from prism.pipeline.entity_normalize import normalize, resolve, upsert_alias


def test_normalize_basic():
    assert normalize("vLLM") == "vllm"
    assert normalize("  OpenAI  ") == "openai"
    assert normalize("GPT-4-turbo") == "gpt-4-turbo"


def test_normalize_unicode():
    # NFKC normalization
    assert normalize("ﬁne-tuning") == "fine-tuning"


def test_normalize_strips_punctuation():
    assert normalize("vLLM!") == "vllm"
    assert normalize("(PagedAttention)") == "pagedattention"


def test_normalize_collapses_whitespace():
    assert normalize("deep  seek") == "deep seek"


def test_resolve_exact_alias(tmp_path):
    from prism.db import get_connection
    conn = get_connection(tmp_path / "test.db")
    # Insert entity + alias
    conn.execute(
        "INSERT INTO entity_profiles (id, canonical_name, display_name, category, first_seen_at) "
        "VALUES (1, 'vllm', 'vLLM', 'project', datetime('now'))")
    conn.execute(
        "INSERT INTO entity_aliases (alias_norm, entity_id, surface_form, created_at) "
        "VALUES ('vllm', 1, 'vLLM', datetime('now'))")
    conn.commit()

    match = resolve(conn, "vllm", "project")
    assert match is not None
    assert match["id"] == 1


def test_resolve_no_match(tmp_path):
    from prism.db import get_connection
    conn = get_connection(tmp_path / "test.db")
    match = resolve(conn, "nonexistent", "project")
    assert match is None


def test_resolve_fuzzy_match(tmp_path):
    from prism.db import get_connection
    conn = get_connection(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO entity_profiles (id, canonical_name, display_name, category, first_seen_at) "
        "VALUES (1, 'vllm', 'vLLM', 'project', datetime('now'))")
    conn.execute(
        "INSERT INTO entity_aliases (alias_norm, entity_id, surface_form, created_at) "
        "VALUES ('vllm', 1, 'vLLM', datetime('now'))")
    conn.commit()

    # "vllm-project" is fuzzy-close to "vllm"
    match = resolve(conn, "vllm-project", "project")
    assert match is not None
    assert match["id"] == 1


def test_resolve_rejects_cross_category(tmp_path):
    from prism.db import get_connection
    conn = get_connection(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO entity_profiles (id, canonical_name, display_name, category, first_seen_at) "
        "VALUES (1, 'go', 'Go', 'technique', datetime('now'))")
    conn.execute(
        "INSERT INTO entity_aliases (alias_norm, entity_id, surface_form, created_at) "
        "VALUES ('go', 1, 'Go', datetime('now'))")
    conn.commit()

    # "go" as an org should NOT match "go" as a technique
    match = resolve(conn, "go", "org")
    assert match is None


def test_upsert_alias(tmp_path):
    from prism.db import get_connection
    conn = get_connection(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO entity_profiles (id, canonical_name, display_name, category, first_seen_at) "
        "VALUES (1, 'vllm', 'vLLM', 'project', datetime('now'))")
    conn.commit()

    upsert_alias(conn, entity_id=1, surface_form="VLLM-Project", source="llm")
    row = conn.execute(
        "SELECT * FROM entity_aliases WHERE alias_norm = 'vllm-project'").fetchone()
    assert row is not None
    assert row["surface_form"] == "VLLM-Project"

    # Upsert again — no error
    upsert_alias(conn, entity_id=1, surface_form="vllm-project", source="llm")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd $PROJECT_ROOT && python -m pytest tests/test_entity_normalize.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement entity_normalize.py**

```python
# prism/pipeline/entity_normalize.py
"""Entity name normalization, alias resolution, and deduplication."""

import re
import sqlite3
import unicodedata
from typing import Optional


def normalize(text: str) -> str:
    """Normalize entity mention to canonical lookup form.

    NFKC → casefold → strip punctuation → collapse whitespace.
    """
    s = unicodedata.normalize("NFKC", text)
    s = s.casefold().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _jaro_winkler(s1: str, s2: str) -> float:
    """Simple Jaro-Winkler similarity. Returns 0.0-1.0."""
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0

    match_dist = max(len1, len2) // 2 - 1
    if match_dist < 0:
        match_dist = 0

    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0
    transpositions = 0

    for i in range(len1):
        start = max(0, i - match_dist)
        end = min(i + match_dist + 1, len2)
        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1

    jaro = (matches / len1 + matches / len2 + (matches - transpositions / 2) / matches) / 3

    # Winkler prefix bonus
    prefix = 0
    for i in range(min(4, len1, len2)):
        if s1[i] == s2[i]:
            prefix += 1
        else:
            break

    return jaro + prefix * 0.1 * (1 - jaro)


def resolve(conn: sqlite3.Connection, name_norm: str, category: str,
            fuzzy_threshold: float = 0.9) -> Optional[sqlite3.Row]:
    """Resolve a normalized name to an existing entity profile.

    Priority: exact alias match → fuzzy same-category match.
    Returns entity_profiles row or None.
    """
    # 1. Exact alias match (same category)
    row = conn.execute(
        "SELECT ep.* FROM entity_aliases ea "
        "JOIN entity_profiles ep ON ea.entity_id = ep.id "
        "WHERE ea.alias_norm = ? AND ep.category = ?",
        (name_norm, category),
    ).fetchone()
    if row:
        return row

    # 2. Exact alias match (any category) — weaker, still useful
    row = conn.execute(
        "SELECT ep.* FROM entity_aliases ea "
        "JOIN entity_profiles ep ON ea.entity_id = ep.id "
        "WHERE ea.alias_norm = ?",
        (name_norm,),
    ).fetchone()
    if row:
        return row

    # 3. Fuzzy match against same-category aliases
    candidates = conn.execute(
        "SELECT ea.alias_norm, ep.* FROM entity_aliases ea "
        "JOIN entity_profiles ep ON ea.entity_id = ep.id "
        "WHERE ep.category = ?",
        (category,),
    ).fetchall()

    best_score = 0.0
    best_match = None
    for c in candidates:
        score = _jaro_winkler(name_norm, c["alias_norm"])
        if score > best_score and score >= fuzzy_threshold:
            best_score = score
            best_match = c
    return best_match


def upsert_alias(conn: sqlite3.Connection, entity_id: int, surface_form: str,
                 source: str = "llm") -> None:
    """Add an alias for an entity if it doesn't already exist."""
    alias_norm = normalize(surface_form)
    conn.execute(
        "INSERT OR IGNORE INTO entity_aliases (alias_norm, entity_id, surface_form, source, created_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        (alias_norm, entity_id, surface_form, source),
    )
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd $PROJECT_ROOT && python -m pytest tests/test_entity_normalize.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add prism/pipeline/entity_normalize.py tests/test_entity_normalize.py
git commit -m "feat(entity): add entity normalization — normalize, resolve, upsert_alias"
```

---

### Task 3: Deterministic Candidate Extraction

**Files:**
- Create: `prism/pipeline/entity_extract.py`
- Test: `tests/test_entity_extract.py`

- [ ] **Step 1: Write tests for deterministic_candidates()**

```python
# tests/test_entity_extract.py
from prism.pipeline.entity_extract import deterministic_candidates, STOPLIST


def test_extracts_github_repo():
    signal = {
        "summary": "vLLM 0.8 released with speculative decoding",
        "tags_json": '["vllm", "inference"]',
        "why_it_matters": "Check https://github.com/vllm-project/vllm",
    }
    candidates = deterministic_candidates(signal)
    names = {c["mention"] for c in candidates}
    assert "vllm-project/vllm" in names or "vLLM" in names


def test_extracts_at_handles():
    signal = {
        "summary": "@karpathy announced new course on LLMs",
        "tags_json": '["education"]',
        "why_it_matters": "",
    }
    candidates = deterministic_candidates(signal)
    names = {c["mention"] for c in candidates}
    assert "karpathy" in names


def test_extracts_titlecase_names():
    signal = {
        "summary": "OpenAI released GPT-5, Anthropic responded with Claude 4",
        "tags_json": '[]',
        "why_it_matters": "",
    }
    candidates = deterministic_candidates(signal)
    names = {c["mention"] for c in candidates}
    assert "OpenAI" in names
    assert "Anthropic" in names


def test_extracts_from_tags():
    signal = {
        "summary": "New model released",
        "tags_json": '["DeepSeek-V3", "MoE"]',
        "why_it_matters": "",
    }
    candidates = deterministic_candidates(signal)
    names = {c["mention"] for c in candidates}
    assert "DeepSeek-V3" in names


def test_stoplist_filters():
    signal = {
        "summary": "The AI model showed improved performance on tasks",
        "tags_json": '[]',
        "why_it_matters": "",
    }
    candidates = deterministic_candidates(signal)
    names = {c["mention"] for c in candidates}
    # Generic terms should be filtered
    assert "The" not in names
    assert "AI" not in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd $PROJECT_ROOT && python -m pytest tests/test_entity_extract.py -v`
Expected: FAIL

- [ ] **Step 3: Implement deterministic_candidates()**

```python
# prism/pipeline/entity_extract.py
"""Entity extraction: deterministic candidates + LLM selection."""

import json
import re
from typing import Optional

STOPLIST = frozenset({
    "ai", "ml", "llm", "api", "gpu", "cpu", "ram", "the", "new", "model",
    "data", "code", "test", "bug", "fix", "update", "release", "version",
    "performance", "training", "inference", "benchmark", "evaluation",
    "paper", "research", "open", "source", "machine", "learning", "deep",
    "neural", "network", "transformer", "attention", "token", "embedding",
})

_GITHUB_REPO_RE = re.compile(r"github\.com/([\w.-]+/[\w.-]+)")
_AT_HANDLE_RE = re.compile(r"@(\w{2,30})")
_TITLECASE_RE = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)*)\b")
_PROPER_NAME_RE = re.compile(r"\b([A-Z][a-zA-Z0-9]*(?:[-_.][A-Za-z0-9]+)*)\b")


def deterministic_candidates(signal: dict) -> list[dict]:
    """Extract entity candidates from signal text without LLM.

    Returns list of {"mention": str, "source": str} dicts.
    """
    text = " ".join(filter(None, [
        signal.get("summary", ""),
        signal.get("why_it_matters", ""),
    ]))

    # Parse tags
    tags_raw = signal.get("tags_json", "[]")
    try:
        tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
    except (json.JSONDecodeError, TypeError):
        tags = []

    seen = set()
    candidates = []

    def _add(mention: str, source: str):
        key = mention.lower().strip()
        if key and key not in seen and key not in STOPLIST and len(key) >= 2:
            seen.add(key)
            candidates.append({"mention": mention.strip(), "source": source})

    # 1. GitHub repo URLs
    for m in _GITHUB_REPO_RE.finditer(text):
        _add(m.group(1), "repo_url")

    # 2. @handles
    for m in _AT_HANDLE_RE.finditer(text):
        _add(m.group(1), "handle")

    # 3. Tags (high quality — already curated by analyze step)
    for tag in tags:
        if isinstance(tag, str) and tag.lower() not in STOPLIST:
            _add(tag, "tag")

    # 4. Proper nouns / Title-case names from text
    for m in _PROPER_NAME_RE.finditer(text):
        name = m.group(1)
        if len(name) >= 3 and name.lower() not in STOPLIST:
            _add(name, "proper_noun")

    return candidates


# ---------------------------------------------------------------------------
# LLM extraction prompt + call
# ---------------------------------------------------------------------------

ENTITY_EXTRACT_SYSTEM = """You are Prism entity linker.
Extract only persistent, trackable entities from this signal.
Categories: person, org, project, model, technique, dataset.
REJECT: broad themes (AI, machine learning), file names, commit hashes, PR numbers, generic nouns.
Return 0-5 entities. Prefer matching KNOWN entities. Max 2 brand-new entities.
Return valid JSON only."""

ENTITY_EXTRACT_USER_TEMPLATE = """DATE: {date}
SIGNAL:
- topic: {topic_label}
- summary: {summary}
- why_it_matters: {why_it_matters}
- tags: {tags}
- sources: {source_types}

CANDIDATES (from text analysis):
{candidates_text}

KNOWN ENTITIES (may match):
{known_entities_text}

Return JSON:
{{
  "entities": [
    {{
      "mention": "exact text",
      "canonical_name": "normalized name",
      "matched_entity_id": null,
      "category": "project",
      "role": "subject",
      "specificity": 4,
      "confidence": 0.9
    }}
  ]
}}"""


def build_extraction_prompt(signal: dict, candidates: list[dict],
                            known_entities: list[dict], date: str) -> str:
    """Build the user prompt for LLM entity extraction."""
    candidates_text = "\n".join(
        f"- {c['mention']} (from {c['source']})" for c in candidates
    ) or "None detected"

    known_text = "\n".join(
        f"- [{e['id']}] {e['canonical_name']} ({e['category']}) aliases: {e.get('aliases', '')}"
        for e in known_entities[:50]
    ) or "None yet"

    return ENTITY_EXTRACT_USER_TEMPLATE.format(
        date=date,
        topic_label=signal.get("topic_label", ""),
        summary=signal.get("summary", ""),
        why_it_matters=signal.get("why_it_matters", ""),
        tags=signal.get("tags_json", "[]"),
        source_types=signal.get("source_types", ""),
        candidates_text=candidates_text,
        known_entities_text=known_text,
    )


def extract_entities_llm(signal: dict, candidates: list[dict],
                         known_entities: list[dict], date: str,
                         model: Optional[str] = None) -> dict:
    """Call LLM to extract entities from a signal. Returns parsed JSON."""
    from prism.pipeline.llm import call_llm_json

    prompt = build_extraction_prompt(signal, candidates, known_entities, date)
    result = call_llm_json(prompt, system=ENTITY_EXTRACT_SYSTEM, model=model)
    return result if result else {"entities": []}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd $PROJECT_ROOT && python -m pytest tests/test_entity_extract.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add prism/pipeline/entity_extract.py tests/test_entity_extract.py
git commit -m "feat(entity): add deterministic candidate extraction + LLM prompt"
```

---

### Task 4: Entity Lifecycle Scoring

**Files:**
- Create: `prism/pipeline/entity_lifecycle.py`
- Test: `tests/test_entity_lifecycle.py`

- [ ] **Step 1: Write tests for lifecycle scoring**

```python
# tests/test_entity_lifecycle.py
from datetime import date, timedelta
from prism.db import get_connection
from prism.pipeline.entity_lifecycle import (
    update_lifecycle_scores, compute_status, IMPACT_WEIGHT,
)


def _setup_entity_with_events(conn, entity_id, name, events):
    """Helper: insert entity + events. events = list of (days_ago, event_type, impact)."""
    today = date.today()
    conn.execute(
        "INSERT INTO entity_profiles (id, canonical_name, display_name, category, first_seen_at) "
        "VALUES (?, ?, ?, 'project', ?)",
        (entity_id, name, name, (today - timedelta(days=max(d for d, _, _ in events) + 1)).isoformat()),
    )
    for days_ago, event_type, impact in events:
        d = (today - timedelta(days=days_ago)).isoformat()
        conn.execute(
            "INSERT INTO entity_events (entity_id, date, event_type, impact, confidence) "
            "VALUES (?, ?, ?, ?, 0.9)",
            (entity_id, d, event_type, impact),
        )
    conn.commit()


def test_emerging_status(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    # Young entity (< 14 days) with 2+ events and decent m7
    _setup_entity_with_events(conn, 1, "new-project", [
        (1, "release", "high"),
        (3, "discussion", "medium"),
    ])
    update_lifecycle_scores(conn, date.today().isoformat())
    row = conn.execute("SELECT * FROM entity_profiles WHERE id = 1").fetchone()
    assert row["m7_score"] > 0
    status = compute_status(row)
    assert status == "emerging"


def test_declining_status(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    # Old entity with no recent events
    today = date.today()
    conn.execute(
        "INSERT INTO entity_profiles (id, canonical_name, display_name, category, first_seen_at) "
        "VALUES (1, 'old-project', 'old-project', 'project', ?)",
        ((today - timedelta(days=90)).isoformat(),),
    )
    conn.execute(
        "INSERT INTO entity_events (entity_id, date, event_type, impact, confidence) "
        "VALUES (1, ?, 'discussion', 'low', 0.8)",
        ((today - timedelta(days=45)).isoformat(),),
    )
    conn.commit()
    update_lifecycle_scores(conn, today.isoformat())
    row = conn.execute("SELECT * FROM entity_profiles WHERE id = 1").fetchone()
    status = compute_status(row)
    assert status == "declining"


def test_practice_boost(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    _setup_entity_with_events(conn, 1, "practiced", [
        (2, "practice_commit", "medium"),  # practice gets 1.25x boost
    ])
    _setup_entity_with_events(conn, 2, "external", [
        (2, "release", "medium"),  # same impact, no boost
    ])
    update_lifecycle_scores(conn, date.today().isoformat())
    r1 = conn.execute("SELECT m7_score FROM entity_profiles WHERE id = 1").fetchone()
    r2 = conn.execute("SELECT m7_score FROM entity_profiles WHERE id = 2").fetchone()
    assert r1["m7_score"] > r2["m7_score"]


def test_event_counts_updated(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    _setup_entity_with_events(conn, 1, "counter-test", [
        (1, "release", "high"),
        (5, "discussion", "medium"),
        (10, "paper", "low"),
        (35, "discussion", "low"),  # outside 30d
    ])
    update_lifecycle_scores(conn, date.today().isoformat())
    row = conn.execute("SELECT * FROM entity_profiles WHERE id = 1").fetchone()
    assert row["event_count_7d"] == 2  # days 1 and 5
    assert row["event_count_30d"] == 3  # days 1, 5, 10
    assert row["event_count_total"] == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd $PROJECT_ROOT && python -m pytest tests/test_entity_lifecycle.py -v`
Expected: FAIL

- [ ] **Step 3: Implement entity_lifecycle.py**

```python
# prism/pipeline/entity_lifecycle.py
"""Entity lifecycle: scoring and status transitions."""

import sqlite3
from datetime import date, datetime, timedelta
from math import exp

IMPACT_WEIGHT = {"high": 3.0, "medium": 1.5, "low": 0.5}
PRACTICE_BOOST = 1.25


def update_lifecycle_scores(conn: sqlite3.Connection, today_str: str) -> int:
    """Recompute m7, m30, event counts for all active entities. Returns count updated."""
    today = datetime.strptime(today_str, "%Y-%m-%d").date()
    cutoff_60d = (today - timedelta(days=60)).isoformat()

    entities = conn.execute(
        "SELECT id, first_seen_at FROM entity_profiles WHERE status != 'archived'"
    ).fetchall()

    count = 0
    for entity in entities:
        events = conn.execute(
            "SELECT date, event_type, impact, confidence FROM entity_events "
            "WHERE entity_id = ? AND date >= ?",
            (entity["id"], cutoff_60d),
        ).fetchall()

        m7, m30 = 0.0, 0.0
        count_7d, count_30d, count_total = 0, 0, 0

        # Also count total events (not just 60d window)
        total_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM entity_events WHERE entity_id = ?",
            (entity["id"],),
        ).fetchone()
        count_total = total_row["cnt"] if total_row else 0

        for e in events:
            try:
                event_date = datetime.strptime(e["date"], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            age = (today - event_date).days
            if age < 0:
                continue

            weight = IMPACT_WEIGHT.get(e["impact"], 1.0) * (e["confidence"] or 0.8)
            if e["event_type"] and e["event_type"].startswith("practice_"):
                weight *= PRACTICE_BOOST

            m7 += weight * exp(-age / 7)
            m30 += weight * exp(-age / 30)
            if age <= 7:
                count_7d += 1
            if age <= 30:
                count_30d += 1

        # Find last event date
        last_event = conn.execute(
            "SELECT MAX(date) as d FROM entity_events WHERE entity_id = ?",
            (entity["id"],),
        ).fetchone()
        last_event_at = last_event["d"] if last_event else None

        conn.execute(
            "UPDATE entity_profiles SET m7_score=?, m30_score=?, "
            "event_count_7d=?, event_count_30d=?, event_count_total=?, last_event_at=? "
            "WHERE id=?",
            (round(m7, 4), round(m30, 4), count_7d, count_30d, count_total,
             last_event_at, entity["id"]),
        )
        count += 1

    conn.commit()
    return count


def compute_status(entity_row: sqlite3.Row) -> str:
    """Compute target status from entity profile scores. Pure function, no DB."""
    first_seen = entity_row["first_seen_at"]
    if not first_seen:
        return "emerging"

    try:
        first_date = datetime.strptime(first_seen[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return "emerging"

    age = (date.today() - first_date).days
    m7 = entity_row["m7_score"] or 0.0
    m30 = entity_row["m30_score"] or 0.0
    total = entity_row["event_count_total"] or 0

    baseline = max(1.0, m30 / 4.3)

    # Days since last event
    last_event_at = entity_row["last_event_at"]
    if last_event_at:
        try:
            last_date = datetime.strptime(last_event_at[:10], "%Y-%m-%d").date()
            days_silent = (date.today() - last_date).days
        except (ValueError, TypeError):
            days_silent = 999
    else:
        days_silent = 999

    if age <= 14 and total >= 2 and m7 >= 3:
        return "emerging"
    elif total >= 4 and m7 >= 1.5 * baseline:
        return "growing"
    elif age >= 21 and m30 >= 8 and 0.67 <= m7 / baseline <= 1.5:
        return "mature"
    elif age >= 21 and (days_silent > 14 or m7 < 0.5 * baseline):
        return "declining"
    else:
        return entity_row["status"] or "emerging"


def update_entity_statuses(conn: sqlite3.Connection) -> int:
    """Apply status transitions with hysteresis. Returns count changed."""
    entities = conn.execute(
        "SELECT * FROM entity_profiles WHERE status != 'archived'"
    ).fetchall()

    changed = 0
    for entity in entities:
        target = compute_status(entity)
        if target != entity["status"]:
            # TODO: Add hysteresis tracking (consecutive days) in v2.1
            # For now, apply immediately
            conn.execute(
                "UPDATE entity_profiles SET status = ? WHERE id = ?",
                (target, entity["id"]),
            )
            changed += 1

    conn.commit()
    return changed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd $PROJECT_ROOT && python -m pytest tests/test_entity_lifecycle.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add prism/pipeline/entity_lifecycle.py tests/test_entity_lifecycle.py
git commit -m "feat(entity): add lifecycle scoring — m7/m30 decay, status rules, practice boost"
```

---

### Task 5: Entity Link Pipeline Orchestrator

**Files:**
- Create: `prism/pipeline/entity_link.py`
- Test: `tests/test_entity_link.py`

- [ ] **Step 1: Write tests for run_entity_link()**

```python
# tests/test_entity_link.py
import json
from datetime import date
from unittest.mock import patch
from prism.db import get_connection
from prism.pipeline.entity_link import run_entity_link, stage_candidate, promote_ready_candidates


def _insert_signal(conn, cluster_id, summary, tags=None):
    conn.execute(
        "INSERT INTO clusters (id, date, topic_label, item_count, merged_context) "
        "VALUES (?, ?, 'test', 1, 'ctx')",
        (cluster_id, date.today().isoformat()),
    )
    conn.execute(
        "INSERT INTO signals (id, cluster_id, summary, signal_layer, signal_strength, "
        "why_it_matters, tags_json, analysis_type, is_current) "
        "VALUES (?, ?, ?, 'actionable', 3, 'important', ?, 'incremental', 1)",
        (cluster_id, cluster_id, summary, json.dumps(tags or [])),
    )
    conn.commit()


def test_stage_candidate(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    stage_candidate(conn, name_norm="newlib", display_name="NewLib",
                    category="project", signal_id=1)
    row = conn.execute("SELECT * FROM entity_candidates WHERE name_norm = 'newlib'").fetchone()
    assert row is not None
    assert row["mention_count"] == 1

    # Stage again — increments count
    stage_candidate(conn, name_norm="newlib", display_name="NewLib",
                    category="project", signal_id=2)
    row = conn.execute("SELECT * FROM entity_candidates WHERE name_norm = 'newlib'").fetchone()
    assert row["mention_count"] == 2


def test_promote_ready_candidates(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    # Insert candidate with 3 mentions
    conn.execute(
        "INSERT INTO entity_candidates (name_norm, display_name, category, mention_count, "
        "first_seen_at, last_seen_at, sample_signals_json, expires_at) "
        "VALUES ('promoted', 'Promoted', 'project', 3, datetime('now'), datetime('now'), "
        "'[1,2,3]', datetime('now', '+30 days'))")
    conn.commit()

    count = promote_ready_candidates(conn)
    assert count == 1

    # Should now exist in entity_profiles
    row = conn.execute(
        "SELECT * FROM entity_profiles WHERE canonical_name = 'promoted'").fetchone()
    assert row is not None
    assert row["display_name"] == "Promoted"

    # Should be removed from candidates
    cand = conn.execute(
        "SELECT * FROM entity_candidates WHERE name_norm = 'promoted'").fetchone()
    assert cand is None


def test_run_entity_link_with_mock_llm(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    _insert_signal(conn, 1, "vLLM 0.8 released with PagedAttention v2", ["vllm", "inference"])

    mock_llm_response = {
        "entities": [
            {
                "mention": "vLLM",
                "canonical_name": "vllm",
                "matched_entity_id": None,
                "category": "project",
                "role": "subject",
                "specificity": 5,
                "confidence": 0.95,
            }
        ]
    }

    with patch("prism.pipeline.entity_extract.extract_entities_llm", return_value=mock_llm_response):
        stats = run_entity_link(conn, date.today().isoformat())

    assert stats["signals_processed"] == 1
    # vLLM should be staged as candidate (first time seen, needs 3 mentions)
    # OR created if specificity + confidence are high enough
    # With specificity=5 and confidence=0.95, it should be promoted directly
    profiles = conn.execute("SELECT * FROM entity_profiles").fetchall()
    assert len(profiles) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd $PROJECT_ROOT && python -m pytest tests/test_entity_link.py -v`
Expected: FAIL

- [ ] **Step 3: Implement entity_link.py**

```python
# prism/pipeline/entity_link.py
"""Entity link pipeline: extract entities from signals, resolve, persist."""

import json
import logging
import sqlite3
from datetime import date, datetime

from prism.db import insert_job_run, finish_job_run
from prism.pipeline.entity_normalize import normalize, resolve, upsert_alias
from prism.pipeline.entity_extract import deterministic_candidates, extract_entities_llm
from prism.pipeline.entity_lifecycle import update_lifecycle_scores, update_entity_statuses

logger = logging.getLogger(__name__)

# Anti-sprawl constants
MAX_ENTITIES_PER_SIGNAL = 5
MAX_NEW_ENTITIES_PER_SIGNAL = 2
MIN_SPECIFICITY = 4
MIN_CONFIDENCE = 0.8
CANDIDATE_PROMOTE_THRESHOLD = 3
CANDIDATE_EXPIRY_DAYS = 30


def _load_current_signals(conn: sqlite3.Connection, dt: str) -> list[dict]:
    """Load today's signals with cluster metadata."""
    rows = conn.execute(
        "SELECT s.id as signal_id, s.summary, s.why_it_matters, s.tags_json, "
        "s.signal_layer, s.signal_strength, c.topic_label, c.date "
        "FROM signals s JOIN clusters c ON s.cluster_id = c.id "
        "WHERE c.date = ? AND s.is_current = 1 "
        "ORDER BY s.signal_strength DESC",
        (dt,),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_known_entities(conn: sqlite3.Connection) -> list[dict]:
    """Load all entity profiles with their aliases for matching."""
    entities = conn.execute(
        "SELECT id, canonical_name, display_name, category FROM entity_profiles"
    ).fetchall()

    result = []
    for e in entities:
        aliases = conn.execute(
            "SELECT alias_norm FROM entity_aliases WHERE entity_id = ?",
            (e["id"],),
        ).fetchall()
        result.append({
            "id": e["id"],
            "canonical_name": e["canonical_name"],
            "display_name": e["display_name"],
            "category": e["category"],
            "aliases": ", ".join(a["alias_norm"] for a in aliases),
        })
    return result


def _is_promotable(ent: dict) -> bool:
    """Check if an extracted entity should be directly promoted to profile."""
    specificity = ent.get("specificity", 0)
    confidence = ent.get("confidence", 0)
    return confidence >= MIN_CONFIDENCE and specificity >= MIN_SPECIFICITY


def _create_profile(conn: sqlite3.Connection, ent: dict) -> int:
    """Create a new entity profile and its initial alias."""
    name_norm = normalize(ent.get("canonical_name", ent["mention"]))
    display = ent.get("canonical_name", ent["mention"])
    category = ent.get("category", "project")

    cursor = conn.execute(
        "INSERT INTO entity_profiles (canonical_name, display_name, category, first_seen_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        (name_norm, display, category),
    )
    entity_id = cursor.lastrowid
    upsert_alias(conn, entity_id, ent["mention"], source="llm")
    if display != ent["mention"]:
        upsert_alias(conn, entity_id, display, source="llm")
    conn.commit()
    return entity_id


def _insert_event(conn: sqlite3.Connection, entity_id: int, signal: dict,
                  ent: dict) -> None:
    """Insert an entity event from a signal."""
    signal_layer = signal.get("signal_layer", "noise")
    impact = "high" if signal.get("signal_strength", 0) >= 4 else (
        "medium" if signal.get("signal_strength", 0) >= 2 else "low"
    )
    conn.execute(
        "INSERT INTO entity_events (entity_id, signal_id, date, event_type, role, "
        "impact, confidence, description) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entity_id,
            signal.get("signal_id"),
            signal.get("date", date.today().isoformat()),
            "discussion",  # default; could be refined
            ent.get("role", "subject"),
            impact,
            ent.get("confidence", 0.8),
            signal.get("summary", "")[:500],
        ),
    )


def stage_candidate(conn: sqlite3.Connection, *, name_norm: str, display_name: str,
                    category: str, signal_id: int) -> None:
    """Stage a low-confidence entity as candidate. Increments count if exists."""
    existing = conn.execute(
        "SELECT * FROM entity_candidates WHERE name_norm = ?", (name_norm,)
    ).fetchone()

    if existing:
        samples = json.loads(existing["sample_signals_json"] or "[]")
        if signal_id not in samples and len(samples) < 3:
            samples.append(signal_id)
        conn.execute(
            "UPDATE entity_candidates SET mention_count = mention_count + 1, "
            "last_seen_at = datetime('now'), sample_signals_json = ? WHERE name_norm = ?",
            (json.dumps(samples), name_norm),
        )
    else:
        conn.execute(
            "INSERT INTO entity_candidates (name_norm, display_name, category, "
            "first_seen_at, last_seen_at, sample_signals_json, expires_at) "
            "VALUES (?, ?, ?, datetime('now'), datetime('now'), ?, datetime('now', '+30 days'))",
            (name_norm, display_name, category, json.dumps([signal_id])),
        )
    conn.commit()


def promote_ready_candidates(conn: sqlite3.Connection) -> int:
    """Promote candidates with enough mentions to entity_profiles."""
    ready = conn.execute(
        "SELECT * FROM entity_candidates WHERE mention_count >= ?",
        (CANDIDATE_PROMOTE_THRESHOLD,),
    ).fetchall()

    promoted = 0
    for cand in ready:
        # Check not already exists
        existing = conn.execute(
            "SELECT id FROM entity_profiles WHERE canonical_name = ?",
            (cand["name_norm"],),
        ).fetchone()
        if existing:
            conn.execute("DELETE FROM entity_candidates WHERE name_norm = ?",
                         (cand["name_norm"],))
            continue

        cursor = conn.execute(
            "INSERT INTO entity_profiles (canonical_name, display_name, category, first_seen_at) "
            "VALUES (?, ?, ?, ?)",
            (cand["name_norm"], cand["display_name"], cand["category"] or "project",
             cand["first_seen_at"]),
        )
        entity_id = cursor.lastrowid
        upsert_alias(conn, entity_id, cand["display_name"], source="promoted")
        conn.execute("DELETE FROM entity_candidates WHERE name_norm = ?",
                     (cand["name_norm"],))
        promoted += 1

    conn.commit()
    return promoted


def expire_candidates(conn: sqlite3.Connection) -> int:
    """Remove expired candidates."""
    cursor = conn.execute(
        "DELETE FROM entity_candidates WHERE expires_at < datetime('now')")
    conn.commit()
    return cursor.rowcount


def run_entity_link(conn: sqlite3.Connection, dt: str,
                    model: str = None) -> dict:
    """Main entity link pipeline step.

    Runs after analyze, before trends.
    """
    job_id = insert_job_run(conn, job_type="entity_link")
    signals = _load_current_signals(conn, dt)
    known_entities = _load_known_entities(conn)

    stats = {"signals_processed": 0, "entities_linked": 0,
             "entities_created": 0, "candidates_staged": 0}

    for signal in signals:
        # Step A: Deterministic candidates
        candidates = deterministic_candidates(signal)

        # Step B: LLM extraction
        try:
            llm_out = extract_entities_llm(
                signal, candidates, known_entities, dt, model=model)
        except Exception as exc:
            logger.error("Entity extraction failed for signal %s: %s",
                         signal.get("signal_id"), exc)
            continue

        stats["signals_processed"] += 1
        new_count = 0

        # Step C: Process each extracted entity
        for ent in llm_out.get("entities", [])[:MAX_ENTITIES_PER_SIGNAL]:
            name_norm = normalize(ent.get("canonical_name", ent.get("mention", "")))
            if not name_norm:
                continue

            category = ent.get("category", "project")
            matched_id = ent.get("matched_entity_id")

            # Try to resolve to existing entity
            match = None
            if matched_id:
                match = conn.execute(
                    "SELECT * FROM entity_profiles WHERE id = ?", (matched_id,)
                ).fetchone()

            if not match:
                match = resolve(conn, name_norm, category)

            if match:
                upsert_alias(conn, match["id"], ent.get("mention", ""), source="llm")
                _insert_event(conn, match["id"], signal, ent)
                stats["entities_linked"] += 1
            elif _is_promotable(ent) and new_count < MAX_NEW_ENTITIES_PER_SIGNAL:
                try:
                    entity_id = _create_profile(conn, ent)
                    _insert_event(conn, entity_id, signal, ent)
                    known_entities = _load_known_entities(conn)  # refresh
                    stats["entities_created"] += 1
                    new_count += 1
                except Exception as exc:
                    logger.warning("Failed to create entity %s: %s", name_norm, exc)
            else:
                stage_candidate(conn, name_norm=name_norm,
                                display_name=ent.get("canonical_name", ent.get("mention", "")),
                                category=category,
                                signal_id=signal.get("signal_id", 0))
                stats["candidates_staged"] += 1

    # Post-processing
    expired = expire_candidates(conn)
    promoted = promote_ready_candidates(conn)
    update_lifecycle_scores(conn, dt)
    update_entity_statuses(conn)

    stats["candidates_expired"] = expired
    stats["candidates_promoted"] = promoted

    finish_job_run(conn, job_id, status="ok",
                   stats_json=json.dumps(stats))
    return stats
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd $PROJECT_ROOT && python -m pytest tests/test_entity_link.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add prism/pipeline/entity_link.py tests/test_entity_link.py
git commit -m "feat(entity): add entity_link pipeline — extract, resolve, stage, promote, lifecycle"
```

---

### Task 6: YAML Migration + entities.py Update

**Files:**
- Modify: `prism/pipeline/entities.py`
- Test: `tests/test_entity_migration.py`

- [ ] **Step 1: Write migration tests**

```python
# tests/test_entity_migration.py
from pathlib import Path
from prism.db import get_connection
from prism.pipeline.entities import migrate_yaml_to_db, load_entities_from_db, tag_entities_from_db


def test_migrate_yaml_to_db(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    yaml_path = tmp_path / "entities.yaml"
    yaml_path.write_text("""
project: [vLLM, SGLang]
org: [OpenAI, Anthropic]
person: [{handle: karpathy, name: Andrej Karpathy}]
""")
    count = migrate_yaml_to_db(conn, yaml_path)
    assert count == 5  # 2 projects + 2 orgs + 1 person

    # Check profiles created
    row = conn.execute(
        "SELECT * FROM entity_profiles WHERE canonical_name = 'vllm'").fetchone()
    assert row is not None
    assert row["category"] == "project"
    assert row["status"] == "mature"
    assert row["needs_review"] == 0

    # Check person
    row = conn.execute(
        "SELECT * FROM entity_profiles WHERE display_name = 'Andrej Karpathy'").fetchone()
    assert row is not None
    assert row["category"] == "person"


def test_migrate_idempotent(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    yaml_path = tmp_path / "entities.yaml"
    yaml_path.write_text("project: [vLLM]\n")
    migrate_yaml_to_db(conn, yaml_path)
    count = migrate_yaml_to_db(conn, yaml_path)  # run again
    assert count == 0  # no new entities


def test_load_entities_from_db(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO entity_profiles (id, canonical_name, display_name, category, first_seen_at) "
        "VALUES (1, 'vllm', 'vLLM', 'project', datetime('now'))")
    conn.execute(
        "INSERT INTO entity_aliases (alias_norm, entity_id, surface_form, created_at) "
        "VALUES ('vllm', 1, 'vLLM', datetime('now'))")
    conn.commit()

    entities = load_entities_from_db(conn)
    assert "project" in entities
    assert "vLLM" in entities["project"]


def test_tag_entities_from_db(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO entity_profiles (id, canonical_name, display_name, category, first_seen_at) "
        "VALUES (1, 'vllm', 'vLLM', 'project', datetime('now'))")
    conn.execute(
        "INSERT INTO entity_aliases (alias_norm, entity_id, surface_form, created_at) "
        "VALUES ('vllm', 1, 'vLLM', datetime('now'))")
    conn.commit()

    tags = tag_entities_from_db(conn, "New vLLM release announced", "Details about the update")
    assert "vLLM" in tags
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd $PROJECT_ROOT && python -m pytest tests/test_entity_migration.py -v`
Expected: FAIL

- [ ] **Step 3: Update entities.py with migration + DB-backed functions**

Replace `prism/pipeline/entities.py` content:

```python
"""Entity tagging: YAML migration and DB-backed entity lookup."""

import sqlite3
from pathlib import Path
from typing import Optional

import yaml

from prism.pipeline.entity_normalize import normalize, upsert_alias


def load_entities(yaml_path: Path) -> dict:
    """Load entities dict from YAML file (legacy, still used by clustering)."""
    data = yaml.safe_load(yaml_path.read_text()) or {}
    return data


def tag_entities(title: str, body: str, entities: dict) -> set[str]:
    """Case-insensitive substring match against entity names (legacy)."""
    text = f"{title} {body}".lower()
    matched = set()
    for category, items in entities.items():
        for item in items:
            if isinstance(item, dict):
                name = item.get("name", "")
                handle = item.get("handle", "")
                if name and name.lower() in text:
                    matched.add(name)
                if handle and handle.lower() in text:
                    matched.add(name or handle)
            else:
                if str(item).lower() in text:
                    matched.add(str(item))
    return matched


def migrate_yaml_to_db(conn: sqlite3.Connection, yaml_path: Path) -> int:
    """One-time migration: import entities.yaml into entity_profiles.

    Returns count of new entities created.
    """
    if not yaml_path.exists():
        return 0

    data = yaml.safe_load(yaml_path.read_text()) or {}
    category_map = {"project": "project", "org": "org", "person": "person",
                    "model": "model", "technique": "technique", "dataset": "dataset"}
    created = 0

    for cat, entries in data.items():
        db_cat = category_map.get(cat, cat)
        if db_cat not in ("person", "org", "project", "model", "technique", "dataset"):
            continue

        for entry in entries:
            if isinstance(entry, dict):
                name = entry.get("name", entry.get("handle", ""))
                handle = entry.get("handle", "")
            else:
                name = str(entry)
                handle = ""

            if not name:
                continue

            name_norm = normalize(name)
            existing = conn.execute(
                "SELECT id FROM entity_profiles WHERE canonical_name = ?",
                (name_norm,),
            ).fetchone()
            if existing:
                continue

            cursor = conn.execute(
                "INSERT INTO entity_profiles (canonical_name, display_name, category, "
                "status, needs_review, first_seen_at) VALUES (?, ?, ?, 'mature', 0, datetime('now'))",
                (name_norm, name, db_cat),
            )
            entity_id = cursor.lastrowid
            upsert_alias(conn, entity_id, name, source="yaml_migration")
            if handle and handle != name:
                upsert_alias(conn, entity_id, handle, source="yaml_migration")
            created += 1

    conn.commit()
    return created


def load_entities_from_db(conn: sqlite3.Connection) -> dict:
    """Load entities from DB in same format as YAML for backward compatibility."""
    rows = conn.execute(
        "SELECT display_name, category FROM entity_profiles"
    ).fetchall()

    result: dict[str, list] = {}
    for r in rows:
        cat = r["category"]
        if cat not in result:
            result[cat] = []
        result[cat].append(r["display_name"])
    return result


def tag_entities_from_db(conn: sqlite3.Connection, title: str, body: str) -> set[str]:
    """Tag entities using DB aliases instead of YAML."""
    text = f"{title} {body}".lower()
    matched = set()

    aliases = conn.execute(
        "SELECT ea.alias_norm, ep.display_name FROM entity_aliases ea "
        "JOIN entity_profiles ep ON ea.entity_id = ep.id"
    ).fetchall()

    for alias in aliases:
        if alias["alias_norm"] in text:
            matched.add(alias["display_name"])

    return matched
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd $PROJECT_ROOT && python -m pytest tests/test_entity_migration.py -v`
Expected: all PASS

- [ ] **Step 5: Also verify existing entity tests still pass**

Run: `cd $PROJECT_ROOT && python -m pytest tests/test_entities.py -v`
Expected: all PASS (backward compatible)

- [ ] **Step 6: Commit**

```bash
git add prism/pipeline/entities.py tests/test_entity_migration.py
git commit -m "feat(entity): add YAML-to-DB migration + DB-backed entity tagging"
```

---

### Task 7: CLI Commands

**Files:**
- Modify: `prism/cli.py`
- Test: manual CLI testing

- [ ] **Step 1: Add entity-link command to cli.py**

Add after the `trends` command in `prism/cli.py`:

```python
@cli.command("entity-link")
@click.option("--date", default=None, help="Date to process (YYYY-MM-DD)")
@click.option("--model", default=None, help="LLM model override for extraction")
def entity_link(date, model):
    """Run entity extraction and linking on today's signals."""
    from datetime import date as date_cls
    from prism.pipeline.entity_link import run_entity_link
    from prism.pipeline.entities import migrate_yaml_to_db

    conn = get_connection(settings.db_path)
    link_date = date or date_cls.today().isoformat()

    # Auto-migrate YAML on first run
    if settings.entity_config.exists():
        migrated = migrate_yaml_to_db(conn, settings.entity_config)
        if migrated:
            click.echo(f"Migrated {migrated} entities from YAML to DB")

    stats = run_entity_link(conn, link_date, model=model)
    click.echo(f"Entity link: {stats['signals_processed']} signals processed, "
               f"{stats['entities_linked']} linked, {stats['entities_created']} created, "
               f"{stats['candidates_staged']} staged")


@cli.group()
def entity():
    """Manage entities."""
    pass


@entity.command("list")
@click.option("--status", default=None, help="Filter by status")
@click.option("--category", default=None, help="Filter by category")
def entity_list(status, category):
    """List tracked entities."""
    conn = get_connection(settings.db_path)
    query = "SELECT * FROM entity_profiles WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY m7_score DESC"

    rows = conn.execute(query, params).fetchall()
    if not rows:
        click.echo("No entities tracked yet.")
        return

    for r in rows:
        review = " [needs review]" if r["needs_review"] else ""
        click.echo(f"  {r['display_name']:25s}  {r['category']:10s}  "
                    f"{r['status']:10s}  m7={r['m7_score']:.1f}  "
                    f"events_7d={r['event_count_7d']}{review}")


@entity.command("show")
@click.argument("name")
def entity_show(name):
    """Show entity details and recent events."""
    from prism.pipeline.entity_normalize import normalize
    conn = get_connection(settings.db_path)
    name_norm = normalize(name)

    row = conn.execute(
        "SELECT * FROM entity_profiles WHERE canonical_name = ?", (name_norm,)
    ).fetchone()
    if not row:
        click.echo(f"Entity '{name}' not found.")
        return

    click.echo(f"Name: {row['display_name']}  ({row['category']})")
    click.echo(f"Status: {row['status']}  |  m7={row['m7_score']:.2f}  m30={row['m30_score']:.2f}")
    click.echo(f"Events: 7d={row['event_count_7d']}  30d={row['event_count_30d']}  total={row['event_count_total']}")
    click.echo(f"First seen: {row['first_seen_at']}  Last event: {row['last_event_at']}")

    # Show aliases
    aliases = conn.execute(
        "SELECT surface_form FROM entity_aliases WHERE entity_id = ?", (row["id"],)
    ).fetchall()
    if aliases:
        click.echo(f"Aliases: {', '.join(a['surface_form'] for a in aliases)}")

    # Show recent events
    events = conn.execute(
        "SELECT * FROM entity_events WHERE entity_id = ? ORDER BY date DESC LIMIT 10",
        (row["id"],),
    ).fetchall()
    if events:
        click.echo("\nRecent events:")
        for e in events:
            click.echo(f"  {e['date']}  {e['event_type']:15s}  {e['impact']:6s}  {e['description'][:60]}")


@cli.command()
@click.argument("note")
def practice(note):
    """Log a manual practice note."""
    from datetime import date as date_cls, datetime
    conn = get_connection(settings.db_path)

    # Create a practice source if not exists
    source = conn.execute(
        "SELECT id FROM sources WHERE source_key = 'practice:manual'"
    ).fetchone()
    if not source:
        from prism.db import insert_source
        source_id = insert_source(conn, source_key="practice:manual",
                                  type="practice_notes", handle="manual")
    else:
        source_id = source["id"]

    # Insert as raw_item
    from prism.db import insert_raw_item
    url = f"practice:{datetime.now().isoformat()}"
    item_id = insert_raw_item(conn, source_id=source_id, url=url,
                              title=f"[Practice] Manual note",
                              body=note, author="user")
    if item_id:
        click.echo(f"Practice note logged (item_id={item_id}). Run 'prism cluster' + 'prism entity-link' to process.")
    else:
        click.echo("Failed to log practice note.")
```

- [ ] **Step 2: Test CLI manually**

Run:
```bash
cd $PROJECT_ROOT
python -m prism.cli entity list
python -m prism.cli entity-link --help
python -m prism.cli practice --help
```
Expected: help text displays, no errors

- [ ] **Step 3: Commit**

```bash
git add prism/cli.py
git commit -m "feat(entity): add CLI commands — entity-link, entity list/show, practice"
```

---

### Task 8: Integration Test + Full Pipeline Verification

**Files:**
- Test: `tests/test_entity_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_entity_integration.py
"""End-to-end entity pipeline test: signals → entity_link → lifecycle."""

import json
from datetime import date
from unittest.mock import patch
from prism.db import get_connection
from prism.pipeline.entities import migrate_yaml_to_db
from prism.pipeline.entity_link import run_entity_link


def _seed_db(conn, yaml_path):
    """Set up DB with clusters, signals, and migrated YAML entities."""
    yaml_path.write_text("""
project: [vLLM, SGLang]
org: [OpenAI]
""")
    migrate_yaml_to_db(conn, yaml_path)

    today = date.today().isoformat()

    # Insert 3 clusters + signals
    for i, (topic, summary, tags) in enumerate([
        ("vLLM update", "vLLM 0.8 released with speculative decoding optimization",
         ["vllm", "inference"]),
        ("OpenAI news", "OpenAI announces GPT-5 with improved reasoning",
         ["openai", "gpt-5"]),
        ("New framework", "LangGraph launches v2 with better agent support",
         ["langgraph", "agents"]),
    ], start=1):
        conn.execute(
            "INSERT INTO clusters (id, date, topic_label, item_count, merged_context) "
            "VALUES (?, ?, ?, 1, ?)",
            (i, today, topic, summary),
        )
        conn.execute(
            "INSERT INTO signals (id, cluster_id, summary, signal_layer, signal_strength, "
            "why_it_matters, tags_json, analysis_type, is_current) "
            "VALUES (?, ?, ?, 'actionable', 4, 'relevant', ?, 'incremental', 1)",
            (i, i, summary, json.dumps(tags)),
        )
    conn.commit()


def test_full_entity_pipeline(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    _seed_db(conn, tmp_path / "entities.yaml")

    # Mock LLM to return structured entity extractions
    def mock_extract(signal, candidates, known, dt, model=None):
        summary = signal.get("summary", "")
        if "vLLM" in summary:
            return {"entities": [
                {"mention": "vLLM", "canonical_name": "vLLM", "matched_entity_id": None,
                 "category": "project", "role": "subject", "specificity": 5, "confidence": 0.95}
            ]}
        elif "OpenAI" in summary:
            return {"entities": [
                {"mention": "OpenAI", "canonical_name": "OpenAI", "matched_entity_id": None,
                 "category": "org", "role": "subject", "specificity": 5, "confidence": 0.95},
                {"mention": "GPT-5", "canonical_name": "GPT-5", "matched_entity_id": None,
                 "category": "model", "role": "subject", "specificity": 5, "confidence": 0.9}
            ]}
        elif "LangGraph" in summary:
            return {"entities": [
                {"mention": "LangGraph", "canonical_name": "LangGraph",
                 "category": "project", "role": "subject", "specificity": 4, "confidence": 0.85}
            ]}
        return {"entities": []}

    with patch("prism.pipeline.entity_link.extract_entities_llm", side_effect=mock_extract):
        stats = run_entity_link(conn, date.today().isoformat())

    # Verify stats
    assert stats["signals_processed"] == 3
    assert stats["entities_linked"] >= 2  # vLLM and OpenAI (from YAML migration)

    # Verify entity profiles
    profiles = conn.execute(
        "SELECT * FROM entity_profiles ORDER BY canonical_name").fetchall()
    names = {p["canonical_name"] for p in profiles}
    assert "vllm" in names      # from YAML migration
    assert "openai" in names    # from YAML migration
    assert "sglang" in names    # from YAML migration

    # GPT-5 should be created (high confidence + specificity)
    assert "gpt-5" in names

    # LangGraph should be created (meets threshold)
    assert "langgraph" in names

    # Verify events were created
    vllm_id = conn.execute(
        "SELECT id FROM entity_profiles WHERE canonical_name = 'vllm'").fetchone()["id"]
    events = conn.execute(
        "SELECT * FROM entity_events WHERE entity_id = ?", (vllm_id,)).fetchall()
    assert len(events) >= 1

    # Verify lifecycle scores were computed
    vllm = conn.execute(
        "SELECT * FROM entity_profiles WHERE canonical_name = 'vllm'").fetchone()
    assert vllm["m7_score"] > 0
    assert vllm["event_count_7d"] >= 1
```

- [ ] **Step 2: Run integration test**

Run: `cd $PROJECT_ROOT && python -m pytest tests/test_entity_integration.py -v`
Expected: all PASS

- [ ] **Step 3: Run full test suite to verify no regressions**

Run: `cd $PROJECT_ROOT && python -m pytest tests/ -v --tb=short`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_entity_integration.py
git commit -m "test(entity): add integration test for full entity pipeline"
```

---

## Plan Summary

| Task | Description | Files | Est. |
|------|-------------|-------|------|
| 1 | Entity DB Schema | db.py, models.py | 5 min |
| 2 | Entity Normalization | entity_normalize.py | 5 min |
| 3 | Deterministic Extraction | entity_extract.py | 5 min |
| 4 | Lifecycle Scoring | entity_lifecycle.py | 5 min |
| 5 | Entity Link Orchestrator | entity_link.py | 10 min |
| 6 | YAML Migration | entities.py | 5 min |
| 7 | CLI Commands | cli.py | 5 min |
| 8 | Integration Test | test_entity_integration.py | 5 min |

**Next plans:**
- **Plan 2:** Source Adapters (YouTube, HN, GitHub releases, model economics)
- **Plan 3:** Briefing v2 (entity-enriched signals, radar summary, practice sources)
