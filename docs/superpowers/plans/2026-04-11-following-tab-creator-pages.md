# Following Tab — Creator Pages + Video-to-Article Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the "关注" tab from a mixed feed into a three-layer navigation: creator list → creator profile → article detail, with a new video-to-article pipeline.

**Architecture:** YouTube sources split from 1 multi-channel entry to N single-channel entries in sources.yaml. New `articles` table stores LLM-generated structured articles from video subtitles. Three new/modified web pages: creator list (replaces follow tab), creator profile (`/creator/{key}`), article detail (`/article/{id}`).

**Tech Stack:** Python 3, FastAPI, SQLite, Jinja2 + HTMX, `markdown` library, existing `call_llm_json()` for LLM calls, `youtube-transcript-api` for subtitles.

**Spec:** `docs/superpowers/specs/2026-04-11-following-tab-creator-pages.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `config/sources.yaml` | Split YouTube entry into per-channel sources, add display_name/avatar |
| Modify | `prism/sources/youtube.py` | Support single-channel config (`channel_id` field), always enrich subtitles |
| Modify | `prism/db.py` | Add `articles` table to `init_db()` |
| Create | `prism/pipeline/articlize.py` | Video-to-article pipeline: fetch eligible items, call LLM, store articles |
| Modify | `prism/cli.py` | Add `prism articlize` command |
| Create | `prism/pipeline/migrate_youtube.py` | One-shot migration script for YouTube source splitting |
| Modify | `prism/web/routes.py` | New routes: `/creator/{key}`, `/article/{id}`, modify follow tab |
| Create | `prism/web/templates/creators.html` | Creator list page (replaces follow tab content) |
| Create | `prism/web/templates/creator_profile.html` | Creator profile page (video/tweet list) |
| Create | `prism/web/templates/article.html` | Article detail page (structured content) |
| Modify | `prism/web/static/style.css` | Styles for creator cards, profile page, article page |
| Create | `tests/pipeline/test_articlize.py` | Tests for articlize pipeline |
| Modify | `tests/sources/test_youtube.py` | Update tests for single-channel adapter |
| Create | `tests/web/test_creator_routes.py` | Tests for new web routes |
| Create | `tests/pipeline/__init__.py` | Package init |

---

### Task 1: Add `articles` table to DB schema

**Files:**
- Modify: `prism/db.py`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py`:

```python
def test_articles_table_exists(db):
    """articles table should exist after init_db."""
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='articles'"
    ).fetchone()
    assert row is not None, "articles table not created"


def test_articles_insert_and_read(db):
    """Basic CRUD on articles table."""
    # Need a source and raw_item first
    db.execute(
        "INSERT INTO sources (source_key, type, handle) VALUES (?, ?, ?)",
        ("youtube:test", "youtube", "test"),
    )
    db.execute(
        "INSERT INTO raw_items (source_id, url, title, body, author) VALUES (?, ?, ?, ?, ?)",
        (1, "https://youtube.com/watch?v=abc", "Test Video", "transcript...", "TestChannel"),
    )
    db.commit()

    db.execute(
        """INSERT INTO articles (raw_item_id, title, subtitle, structured_body, highlights_json, word_count, model_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (1, "Test Video", "One line summary", "## Section\nContent", '["quote1"]', 100, "qwen3"),
    )
    db.commit()

    row = db.execute("SELECT * FROM articles WHERE raw_item_id = 1").fetchone()
    assert row["title"] == "Test Video"
    assert row["subtitle"] == "One line summary"
    assert row["word_count"] == 100


def test_articles_unique_raw_item_id(db):
    """raw_item_id should be unique — one article per raw_item."""
    import sqlite3 as _sqlite3
    db.execute("INSERT INTO sources (source_key, type) VALUES ('yt:t', 'youtube')")
    db.execute("INSERT INTO raw_items (source_id, url, title) VALUES (1, 'https://yt.com/1', 'V1')")
    db.commit()
    db.execute("INSERT INTO articles (raw_item_id, title) VALUES (1, 'Article 1')")
    db.commit()
    with pytest.raises(_sqlite3.IntegrityError):
        db.execute("INSERT INTO articles (raw_item_id, title) VALUES (1, 'Article 1 duplicate')")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_db.py::test_articles_table_exists tests/test_db.py::test_articles_insert_and_read tests/test_db.py::test_articles_unique_raw_item_id -v`
Expected: FAIL — `articles` table does not exist

- [ ] **Step 3: Add articles table to init_db()**

In `prism/db.py`, inside `init_db()`, add after the last CREATE TABLE (before migrations section):

```sql
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_item_id INTEGER UNIQUE REFERENCES raw_items(id),
    title TEXT NOT NULL,
    subtitle TEXT,
    structured_body TEXT,
    highlights_json TEXT,
    word_count INTEGER,
    model_id TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT
);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add prism/db.py tests/test_db.py
git commit -m "feat: add articles table for video-to-article content"
```

---

### Task 2: Modify YouTube adapter for single-channel mode

**Files:**
- Modify: `prism/sources/youtube.py`
- Modify: `tests/sources/test_youtube.py`

- [ ] **Step 1: Write failing tests for single-channel config**

Add to `tests/sources/test_youtube.py`:

```python
@pytest.mark.asyncio
async def test_adapter_sync_single_channel():
    """Single-channel config using channel_id (not channels list)."""
    feed = _make_atom_feed([
        {"video_id": "sc001", "title": "Single Channel Video", "published": _recent_ts()}
    ])
    mock_resp = _make_mock_response(feed)

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=mock_resp)

        adapter = YoutubeAdapter()
        result = await adapter.sync({
            "key": "youtube:bestpartners",
            "channel_id": "UCGWYKICLOE8Wxy7q3eYXmPA",
            "display_name": "最佳拍档",
        })

    assert result.success is True
    assert result.source_key == "youtube:bestpartners"
    assert len(result.items) == 1
    assert result.items[0].title == "Single Channel Video"


@pytest.mark.asyncio
async def test_adapter_always_enriches_subtitles():
    """Subtitles should be attempted for ALL videos, not just short bodies."""
    feed = _make_atom_feed([{
        "video_id": "enrich1",
        "title": "Long Description Video",
        "description": "A" * 500,  # body > 200 chars
        "published": _recent_ts(),
    }])
    mock_resp = _make_mock_response(feed)

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=mock_resp)

        with patch("prism.sources.subtitles.extract_subtitles", return_value="Full transcript text here " * 100) as mock_sub:
            adapter = YoutubeAdapter()
            result = await adapter.sync({
                "key": "youtube:test",
                "channel_id": "UCtest",
            })

            # extract_subtitles should be called even though body > 200
            mock_sub.assert_called_once()
            assert len(result.items[0].body) > 500
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/sources/test_youtube.py::test_adapter_sync_single_channel tests/sources/test_youtube.py::test_adapter_always_enriches_subtitles -v`
Expected: FAIL

- [ ] **Step 3: Modify YoutubeAdapter.sync() for single-channel + always-enrich**

In `prism/sources/youtube.py`, modify the `sync()` method:

```python
async def sync(self, config: dict) -> SyncResult:
    source_key = config.get("key", "youtube:channels")
    # Support both single-channel (channel_id) and multi-channel (channels) config
    single_channel = config.get("channel_id")
    if single_channel:
        channels = [single_channel]
    else:
        channels = config.get("channels", [])
    lookback_hours = int(config.get("lookback_hours", _LOOKBACK_HOURS))
    # ... rest of existing channel iteration logic stays the same ...
```

Then modify the subtitle enrichment section — remove the `if len(item.body) < 200:` guard:

```python
    enriched = 0
    for item in regular_items:
        try:
            from prism.sources.subtitles import extract_subtitles
            transcript = extract_subtitles(item.url)
            if transcript and len(transcript) > len(item.body):
                item.body = transcript[:8000]  # Cap at 8k chars (up from 4k)
                enriched += 1
        except Exception as exc:
            logger.warning("Subtitle extraction failed for %s: %s", item.url, exc)
```

- [ ] **Step 4: Run all YouTube tests**

Run: `.venv/bin/pytest tests/sources/test_youtube.py -v`
Expected: ALL PASS (existing multi-channel tests still pass + new single-channel tests pass)

- [ ] **Step 5: Commit**

```bash
git add prism/sources/youtube.py tests/sources/test_youtube.py
git commit -m "feat: youtube adapter supports single-channel config + always enriches subtitles"
```

---

### Task 3: Split YouTube sources in YAML + migration script

**Files:**
- Modify: `config/sources.yaml`
- Create: `prism/pipeline/migrate_youtube.py`

- [ ] **Step 1: Update sources.yaml**

Replace the single YouTube entry (lines ~136-148) with per-channel entries:

```yaml
# YouTube — 每频道一个 source
- type: youtube
  key: "youtube:bestpartners"
  channel_id: UCGWYKICLOE8Wxy7q3eYXmPA
  display_name: "最佳拍档"

- type: youtube
  key: "youtube:sunriches"
  channel_id: UCkHrq03gWLLx6vjS2DOJ8aA
  display_name: "孙富贵"

- type: youtube
  key: "youtube:storytellerfan"
  channel_id: UCUGLhcs3-3y_yhZZsgRzrzw
  display_name: "老范讲故事"

- type: youtube
  key: "youtube:maskfinance"
  channel_id: UCjJklW6MyT2yjHEOrRu-FOA
  display_name: "蒙面财经MaskFinance"

- type: youtube
  key: "youtube:a16z"
  channel_id: UCQ1VQj-37kl2yS_VUhfQHsw
  display_name: "a16z Deep Dives"

- type: youtube
  key: "youtube:sunlao"
  channel_id: UC1Lk6WO-eKuYc6GHYbKVY2g
  display_name: "政經孫老師"

- type: youtube
  key: "youtube:ltshijie"
  channel_id: UCVThAeUXPZcUfdYBvWGV3UA
  display_name: "LT+"

- type: youtube
  key: "youtube:caijinglengyan"
  channel_id: UCn9_KbNANeyYREePe8YA2DA
  display_name: "财经冷眼"
```

- [ ] **Step 2: Write migration script**

Create `prism/pipeline/migrate_youtube.py`:

```python
"""One-shot migration: split youtube:ai-interviews into per-channel sources.

Run: .venv/bin/python -m prism.pipeline.migrate_youtube [--dry-run]

Safety:
- Checks channel_id coverage first (must be >= 90%)
- Dry-run mode by default
- Works on a DB copy for testing
"""

import json
import sqlite3
import sys
import logging

import yaml

logger = logging.getLogger(__name__)

# channel_id → new source_key mapping (must match sources.yaml)
CHANNEL_MAP = {
    "UCGWYKICLOE8Wxy7q3eYXmPA": "youtube:bestpartners",
    "UCkHrq03gWLLx6vjS2DOJ8aA": "youtube:sunriches",
    "UCUGLhcs3-3y_yhZZsgRzrzw": "youtube:storytellerfan",
    "UCjJklW6MyT2yjHEOrRu-FOA": "youtube:maskfinance",
    "UCQ1VQj-37kl2yS_VUhfQHsw": "youtube:a16z",
    "UC1Lk6WO-eKuYc6GHYbKVY2g": "youtube:sunlao",
    "UCVThAeUXPZcUfdYBvWGV3UA": "youtube:ltshijie",
    "UCn9_KbNANeyYREePe8YA2DA": "youtube:caijinglengyan",
}

OLD_SOURCE_KEY = "youtube:ai-interviews"


def validate_coverage(conn: sqlite3.Connection) -> tuple[int, int]:
    """Check how many raw_items have channel_id in raw_json. Returns (total, covered)."""
    old_source = conn.execute(
        "SELECT id FROM sources WHERE source_key = ?", (OLD_SOURCE_KEY,)
    ).fetchone()
    if not old_source:
        return 0, 0

    rows = conn.execute(
        "SELECT raw_json FROM raw_items WHERE source_id = ?", (old_source["id"],)
    ).fetchall()

    total = len(rows)
    covered = 0
    for r in rows:
        try:
            data = json.loads(r["raw_json"])
            if data.get("channel_id") in CHANNEL_MAP:
                covered += 1
        except (json.JSONDecodeError, TypeError):
            pass
    return total, covered


def migrate(conn: sqlite3.Connection, dry_run: bool = True) -> dict:
    """Migrate raw_items from old multi-channel source to new per-channel sources.

    Returns stats dict.
    """
    total, covered = validate_coverage(conn)
    pct = (covered * 100 // total) if total else 0
    print(f"Coverage check: {covered}/{total} ({pct}%) items have valid channel_id")

    if pct < 90:
        print("ABORT: coverage < 90%. Fix data first.")
        return {"aborted": True, "total": total, "covered": covered}

    old_source = conn.execute(
        "SELECT id FROM sources WHERE source_key = ?", (OLD_SOURCE_KEY,)
    ).fetchone()
    old_source_id = old_source["id"]

    # Ensure new sources exist in DB (reconcile_sources should have created them)
    new_sources = {}
    for channel_id, new_key in CHANNEL_MAP.items():
        row = conn.execute(
            "SELECT id FROM sources WHERE source_key = ?", (new_key,)
        ).fetchone()
        if not row:
            print(f"WARNING: source {new_key} not found in DB. Run 'prism sync' first to reconcile.")
            if not dry_run:
                return {"aborted": True, "reason": f"missing source {new_key}"}
        else:
            new_sources[channel_id] = row["id"]

    # Reassign raw_items
    migrated = 0
    skipped = 0
    items = conn.execute(
        "SELECT id, raw_json FROM raw_items WHERE source_id = ?", (old_source_id,)
    ).fetchall()

    for item in items:
        try:
            data = json.loads(item["raw_json"])
            channel_id = data.get("channel_id")
        except (json.JSONDecodeError, TypeError):
            channel_id = None

        if channel_id not in new_sources:
            skipped += 1
            continue

        new_source_id = new_sources[channel_id]
        if dry_run:
            print(f"  [DRY RUN] item {item['id']} → source_id {new_source_id}")
        else:
            conn.execute(
                "UPDATE raw_items SET source_id = ? WHERE id = ?",
                (new_source_id, item["id"]),
            )
        migrated += 1

    if not dry_run:
        # Mark old source as yaml_removed
        conn.execute(
            "UPDATE sources SET origin = 'yaml_removed', enabled = 0 WHERE id = ?",
            (old_source_id,),
        )
        conn.commit()

    stats = {"total": total, "migrated": migrated, "skipped": skipped, "dry_run": dry_run}
    print(f"Migration {'(DRY RUN) ' if dry_run else ''}complete: {stats}")
    return stats


if __name__ == "__main__":
    from prism.config import settings
    dry_run = "--dry-run" in sys.argv or len(sys.argv) == 1  # dry-run by default

    conn = sqlite3.connect(str(settings.db_path))
    conn.row_factory = sqlite3.Row

    # Run reconcile first to create new source entries
    from prism.source_manager import reconcile_sources
    reconcile_sources(conn, settings.source_config)

    migrate(conn, dry_run=dry_run)
    conn.close()
```

- [ ] **Step 3: Test migration on DB copy**

```bash
cp data/prism.sqlite3 data/prism.sqlite3.bak
.venv/bin/python -m prism.pipeline.migrate_youtube --dry-run
```

Expected: `Coverage check: 145/145 (100%)` + dry-run reassignment log

- [ ] **Step 4: Run actual migration**

```bash
.venv/bin/python -m prism.pipeline.migrate_youtube --execute
```

(Pass `--execute` — modify the `if __name__` block to check for this flag instead of `--dry-run`)

- [ ] **Step 5: Verify migration**

```bash
.venv/bin/python3 -c "
import sqlite3
conn = sqlite3.connect('data/prism.sqlite3')
conn.row_factory = sqlite3.Row
rows = conn.execute('''
    SELECT s.source_key, count(ri.id) as cnt
    FROM sources s LEFT JOIN raw_items ri ON ri.source_id = s.id
    WHERE s.type = 'youtube'
    GROUP BY s.id ORDER BY cnt DESC
''').fetchall()
for r in rows:
    print(f'{r[\"source_key\"]}: {r[\"cnt\"]} items')
"
```

Expected: Each channel has its own count, old `youtube:ai-interviews` has 0 items.

- [ ] **Step 6: Commit**

```bash
git add config/sources.yaml prism/pipeline/migrate_youtube.py
git commit -m "feat: split YouTube into per-channel sources + migration script"
```

---

### Task 4: Build articlize pipeline

**Files:**
- Create: `prism/pipeline/articlize.py`
- Create: `tests/pipeline/__init__.py`
- Create: `tests/pipeline/test_articlize.py`

- [ ] **Step 1: Create tests package**

```bash
touch tests/pipeline/__init__.py
```

- [ ] **Step 2: Write failing tests**

Create `tests/pipeline/test_articlize.py`:

```python
"""Tests for video-to-article pipeline."""

import json
import sqlite3
import pytest
from unittest.mock import patch, MagicMock

from prism.db import init_db


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()


def _insert_youtube_item(db, source_key="youtube:test", body="transcript text", title="Test Video", url="https://youtube.com/watch?v=abc"):
    """Helper to insert a source + raw_item for testing."""
    db.execute("INSERT INTO sources (source_key, type) VALUES (?, 'youtube')", (source_key,))
    db.execute(
        "INSERT INTO raw_items (source_id, url, title, body, author) VALUES (1, ?, ?, ?, 'TestChannel')",
        (url, title, body),
    )
    db.commit()
    return 1  # raw_item_id


def test_find_eligible_items(db):
    """Should find YouTube items with body content and no existing article."""
    from prism.pipeline.articlize import find_eligible_items

    _insert_youtube_item(db, body="transcript " * 100)
    items = find_eligible_items(db)
    assert len(items) == 1
    assert items[0]["title"] == "Test Video"


def test_find_eligible_items_skips_empty_body(db):
    """Items with empty body should be skipped."""
    from prism.pipeline.articlize import find_eligible_items

    _insert_youtube_item(db, body="")
    items = find_eligible_items(db)
    assert len(items) == 0


def test_find_eligible_items_skips_existing_article(db):
    """Items that already have an article should be skipped."""
    from prism.pipeline.articlize import find_eligible_items

    _insert_youtube_item(db)
    db.execute("INSERT INTO articles (raw_item_id, title) VALUES (1, 'Existing')")
    db.commit()
    items = find_eligible_items(db)
    assert len(items) == 0


def test_find_eligible_items_skips_long_body(db):
    """Items with body > 6000 chars should be skipped (MVP limit)."""
    from prism.pipeline.articlize import find_eligible_items

    _insert_youtube_item(db, body="x" * 6001)
    items = find_eligible_items(db)
    assert len(items) == 0


def test_parse_llm_response_valid():
    """Valid JSON response should parse correctly."""
    from prism.pipeline.articlize import parse_llm_response

    raw = '{"subtitle": "Summary", "body": "## Section\\nContent", "highlights": ["quote1"]}'
    result = parse_llm_response(raw)
    assert result["subtitle"] == "Summary"
    assert "## Section" in result["body"]
    assert len(result["highlights"]) == 1


def test_parse_llm_response_wrapped_in_markdown():
    """JSON wrapped in ```json ... ``` should parse correctly."""
    from prism.pipeline.articlize import parse_llm_response

    raw = 'Here is the result:\n```json\n{"subtitle": "S", "body": "## A\\nB", "highlights": []}\n```'
    result = parse_llm_response(raw)
    assert result["subtitle"] == "S"


def test_parse_llm_response_invalid():
    """Invalid response should return None."""
    from prism.pipeline.articlize import parse_llm_response

    assert parse_llm_response("This is not JSON at all") is None
    assert parse_llm_response('{"subtitle": "S", "body": ""}') is None  # empty body


def test_save_article(db):
    """Should insert article into DB."""
    from prism.pipeline.articlize import save_article

    _insert_youtube_item(db)
    save_article(db, raw_item_id=1, title="Test Video", subtitle="Summary",
                 structured_body="## Section\nContent", highlights=["q1"], model_id="qwen3")

    row = db.execute("SELECT * FROM articles WHERE raw_item_id = 1").fetchone()
    assert row is not None
    assert row["subtitle"] == "Summary"
    assert row["word_count"] > 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/pipeline/test_articlize.py -v`
Expected: FAIL — module not found

- [ ] **Step 4: Implement articlize.py**

Create `prism/pipeline/articlize.py`:

```python
"""Video-to-article pipeline: convert YouTube subtitles into structured articles."""

import json
import re
import logging
import sqlite3
from datetime import datetime

logger = logging.getLogger(__name__)

MAX_BODY_LENGTH = 6000  # MVP: skip videos with body > this

ARTICLIZE_SYSTEM = """你是一个专业的内容编辑。将视频字幕转化为结构化文章。"""

ARTICLIZE_USER_TEMPLATE = """将以下视频字幕转化为结构化文章。

视频标题: {title}

字幕原文:
{body}

要求:
1. 提取 3-5 个核心章节，每个章节有标题和正文
2. 用 **粗体** 标注关键洞察和数据点
3. 提取 3-5 条最有价值的原始引用（用 > 引用格式）
4. 写一句话摘要（subtitle）
5. 去除口语化填充词、重复内容、无关闲聊
6. 保留原始观点和论证逻辑，不要添加评论

输出 JSON（不要输出其他内容）:
{{"subtitle": "一句话摘要", "body": "Markdown 正文", "highlights": ["关键引用1", "关键引用2"]}}"""


def find_eligible_items(conn: sqlite3.Connection) -> list[dict]:
    """Find YouTube raw_items that need article generation.

    Conditions:
    - source type = youtube
    - body is not empty and length <= MAX_BODY_LENGTH
    - no existing article for this raw_item
    """
    rows = conn.execute(
        """
        SELECT ri.id, ri.title, ri.body, ri.url, ri.author, s.source_key
        FROM raw_items ri
        JOIN sources s ON ri.source_id = s.id
        LEFT JOIN articles a ON a.raw_item_id = ri.id
        WHERE s.type = 'youtube'
          AND length(ri.body) > 0
          AND length(ri.body) <= ?
          AND a.id IS NULL
        ORDER BY ri.created_at DESC
        """,
        (MAX_BODY_LENGTH,),
    ).fetchall()
    return [dict(r) for r in rows]


def parse_llm_response(raw: str) -> dict | None:
    """Extract and validate JSON from LLM response.

    Handles: raw JSON, ```json wrapped, thinking tags.
    Returns parsed dict or None if invalid.
    """
    # Strip thinking tags
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    # Try direct parse first
    try:
        result = json.loads(text)
        if _validate_article(result):
            return result
    except json.JSONDecodeError:
        pass

    # Try extracting ```json block
    m = re.search(r"```json\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group(1))
            if _validate_article(result):
                return result
        except json.JSONDecodeError:
            pass

    # Try finding first {...} block
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group(0))
            if _validate_article(result):
                return result
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse LLM response as valid article JSON")
    return None


def _validate_article(data: dict) -> bool:
    """Check article JSON has required fields with valid content."""
    if not isinstance(data, dict):
        return False
    body = data.get("body", "")
    if not body or len(body.strip()) < 10:
        return False
    if not data.get("subtitle"):
        return False
    return True


def save_article(
    conn: sqlite3.Connection,
    *,
    raw_item_id: int,
    title: str,
    subtitle: str,
    structured_body: str,
    highlights: list[str],
    model_id: str,
) -> int:
    """Insert article into DB. Returns article id."""
    word_count = len(structured_body)
    cursor = conn.execute(
        """INSERT INTO articles (raw_item_id, title, subtitle, structured_body,
           highlights_json, word_count, model_id, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            raw_item_id,
            title,
            subtitle,
            structured_body,
            json.dumps(highlights, ensure_ascii=False),
            word_count,
            model_id,
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    return cursor.lastrowid


def run_articlize(conn: sqlite3.Connection) -> dict:
    """Main entry point: find eligible items and generate articles.

    Returns stats dict.
    """
    from prism.pipeline.llm import call_llm_json

    items = find_eligible_items(conn)
    logger.info("Found %d eligible items for articlize", len(items))

    stats = {"total": len(items), "success": 0, "failed": 0, "skipped": 0}

    for item in items:
        prompt = ARTICLIZE_USER_TEMPLATE.format(title=item["title"], body=item["body"])
        try:
            raw_response = call_llm_json(prompt, system=ARTICLIZE_SYSTEM, max_tokens=4096)
            # call_llm_json returns a dict, but we need to validate our schema
            if isinstance(raw_response, dict) and _validate_article(raw_response):
                parsed = raw_response
            else:
                # Fallback: try parsing as string
                parsed = parse_llm_response(json.dumps(raw_response) if isinstance(raw_response, dict) else str(raw_response))
        except Exception as exc:
            logger.warning("LLM call failed for item %d (%s): %s", item["id"], item["title"], exc)
            stats["failed"] += 1
            continue

        if not parsed:
            logger.warning("Invalid LLM response for item %d (%s)", item["id"], item["title"])
            stats["failed"] += 1
            continue

        save_article(
            conn,
            raw_item_id=item["id"],
            title=item["title"],
            subtitle=parsed["subtitle"],
            structured_body=parsed["body"],
            highlights=parsed.get("highlights", []),
            model_id="omlx",
        )
        stats["success"] += 1
        logger.info("Generated article for: %s", item["title"])

    return stats
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/pipeline/test_articlize.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add prism/pipeline/articlize.py tests/pipeline/__init__.py tests/pipeline/test_articlize.py
git commit -m "feat: articlize pipeline — convert video subtitles to structured articles"
```

---

### Task 5: Add `prism articlize` CLI command

**Files:**
- Modify: `prism/cli.py`

- [ ] **Step 1: Add the command**

In `prism/cli.py`, add after the sync command:

```python
@cli.command()
def articlize():
    """Generate structured articles from YouTube video subtitles."""
    from prism.pipeline.articlize import run_articlize
    conn = get_connection(settings.db_path)
    stats = run_articlize(conn)
    click.echo(
        f"Articlize complete: {stats['success']} generated, "
        f"{stats['failed']} failed, {stats['skipped']} skipped "
        f"(of {stats['total']} eligible)"
    )
```

- [ ] **Step 2: Verify CLI help shows the command**

Run: `.venv/bin/prism articlize --help`
Expected: Shows "Generate structured articles from YouTube video subtitles."

- [ ] **Step 3: Commit**

```bash
git add prism/cli.py
git commit -m "feat: add prism articlize CLI command"
```

---

### Task 6: Creator list page (replace follow tab)

**Files:**
- Modify: `prism/web/routes.py`
- Create: `prism/web/templates/creators.html`
- Modify: `prism/web/static/style.css`
- Create: `tests/web/test_creator_routes.py`

- [ ] **Step 1: Write failing route test**

Create `tests/web/test_creator_routes.py`:

```python
"""Tests for creator list and profile routes."""

import sqlite3
import json
import pytest
from fastapi.testclient import TestClient

from prism.db import init_db


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def client(db):
    from prism.api.app import create_app
    app = create_app(conn=db)
    return TestClient(app)


def _seed_creators(db):
    """Insert YouTube + X sources with raw_items."""
    # YouTube creator
    db.execute(
        "INSERT INTO sources (source_key, type, handle, config_yaml, enabled) VALUES (?, ?, ?, ?, 1)",
        ("youtube:testchannel", "youtube", "testchannel",
         'display_name: "Test Channel"\nchannel_id: UCtest123', ),
    )
    db.execute(
        "INSERT INTO raw_items (source_id, url, title, body, author, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (1, "https://youtube.com/watch?v=v1", "Video 1", "transcript", "Test Channel"),
    )
    db.execute(
        "INSERT INTO raw_items (source_id, url, title, body, author, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (1, "https://youtube.com/watch?v=v2", "Video 2", "transcript2", "Test Channel"),
    )

    # X creator
    db.execute(
        "INSERT INTO sources (source_key, type, handle, enabled) VALUES (?, ?, ?, 1)",
        ("x:karpathy", "x", "karpathy"),
    )
    db.execute(
        "INSERT INTO raw_items (source_id, url, title, body, author, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (2, "https://x.com/karpathy/status/1", "", "Tweet content here", "karpathy"),
    )
    db.commit()


def test_follow_tab_shows_creators(client, db):
    """Follow tab should show creator cards, not mixed feed."""
    _seed_creators(db)
    resp = client.get("/?tab=follow")
    assert resp.status_code == 200
    assert "Test Channel" in resp.text
    assert "karpathy" in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/web/test_creator_routes.py::test_follow_tab_shows_creators -v`
Expected: FAIL (route still returns old feed)

- [ ] **Step 3: Implement creator list route**

In `prism/web/routes.py`, modify the follow tab branch in the `index()` function. When `tab == "follow"`, instead of calling `compute_feed`, query creators directly:

```python
if tab == "follow":
    # Creator list mode
    from prism.web.ranking import FOLLOW_SOURCE_TYPES
    creators = _build_creator_list(conn)
    return _render("creators.html", request=request, tab=tab, creators=creators)
```

Add helper function:

```python
def _build_creator_list(conn) -> dict:
    """Build grouped creator list for follow tab.

    Returns: {"youtube": [creator_dicts], "x": [creator_dicts]}
    """
    from prism.web.ranking import FOLLOW_SOURCE_TYPES
    import yaml as _yaml

    groups = {}
    type_meta = {
        "youtube": {"icon": "▶", "label": "YouTube 频道"},
        "x": {"icon": "𝕏", "label": "X 博主"},
        "follow_builders": {"icon": "𝕏", "label": "Builders"},
        "github_releases": {"icon": "📦", "label": "GitHub"},
    }

    sources = conn.execute(
        """SELECT s.id, s.source_key, s.type, s.handle, s.config_yaml
           FROM sources s
           WHERE s.type IN ({}) AND s.enabled = 1
           ORDER BY s.type, s.source_key""".format(
            ",".join("?" * len(FOLLOW_SOURCE_TYPES))
        ),
        list(FOLLOW_SOURCE_TYPES),
    ).fetchall()

    for src in sources:
        src_type = src["type"]
        config = {}
        if src["config_yaml"]:
            try:
                config = _yaml.safe_load(src["config_yaml"]) or {}
            except Exception:
                pass

        display_name = config.get("display_name", src["handle"] or src["source_key"])
        channel_id = config.get("channel_id", "")

        # Get item count and latest 2 items
        items_info = conn.execute(
            """SELECT count(*) as cnt,
                      max(created_at) as latest
               FROM raw_items WHERE source_id = ?""",
            (src["id"],),
        ).fetchone()

        recent = conn.execute(
            """SELECT title, body, url, created_at
               FROM raw_items WHERE source_id = ?
               ORDER BY created_at DESC LIMIT 2""",
            (src["id"],),
        ).fetchall()

        # Avatar
        if src_type == "youtube":
            avatar = config.get("avatar", "")
        elif src_type in ("x", "follow_builders"):
            handle = src["handle"] or src["source_key"].split(":")[-1]
            avatar = f"https://unavatar.io/x/{handle}"
        else:
            avatar = ""

        creator = {
            "source_key": src["source_key"],
            "type": src_type,
            "display_name": display_name,
            "avatar": avatar,
            "item_count": items_info["cnt"] if items_info else 0,
            "latest": items_info["latest"] if items_info else "",
            "recent_items": [
                {"title": r["title"], "body": r["body"][:80], "url": r["url"]}
                for r in recent
            ],
        }

        if src_type not in groups:
            meta = type_meta.get(src_type, {"icon": "📌", "label": src_type})
            groups[src_type] = {"icon": meta["icon"], "label": meta["label"], "creators": []}
        groups[src_type]["creators"].append(creator)

    return groups
```

- [ ] **Step 4: Create creators.html template**

Create `prism/web/templates/creators.html`:

```html
{% extends "base.html" %}
{% block content %}
<div class="creators-page">
{% for type_key, group in creators.items() %}
  <div class="creator-group">
    <h2 class="creator-group-title">{{ group.icon }} {{ group.label }}</h2>
    <div class="creator-grid">
    {% for c in group.creators %}
      <a class="creator-card" href="/creator/{{ c.source_key }}">
        <div class="creator-card-header">
          {% if c.avatar %}
          <img class="creator-avatar" src="{{ c.avatar }}" alt="{{ c.display_name }}" loading="lazy" onerror="this.style.display='none'">
          {% endif %}
          <div class="creator-info">
            <span class="creator-name">{{ c.display_name }}</span>
            <span class="creator-meta">{{ c.item_count }} 条 · {{ c.latest[:10] if c.latest else '—' }}</span>
          </div>
        </div>
        {% if c.recent_items %}
        <ul class="creator-preview">
          {% for ri in c.recent_items %}
          <li class="creator-preview-item">{{ ri.title if ri.title else ri.body }}</li>
          {% endfor %}
        </ul>
        {% endif %}
      </a>
    {% endfor %}
    </div>
  </div>
{% endfor %}
</div>
{% endblock %}
```

- [ ] **Step 5: Add CSS for creator cards**

Append to `prism/web/static/style.css`:

```css
/* Creator list page */
.creators-page { padding: 0 16px; }
.creator-group { margin-bottom: 24px; }
.creator-group-title { font-size: 16px; font-weight: 600; margin: 16px 0 8px; color: #666; }
.creator-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
.creator-card { display: block; background: var(--card-bg, #fff); border: 1px solid var(--border, #e5e5e5); border-radius: 12px; padding: 14px; text-decoration: none; color: inherit; transition: box-shadow 0.15s; }
.creator-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
.creator-card-header { display: flex; align-items: center; gap: 10px; }
.creator-avatar { width: 40px; height: 40px; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
.creator-info { display: flex; flex-direction: column; min-width: 0; }
.creator-name { font-weight: 600; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.creator-meta { font-size: 12px; color: #999; margin-top: 2px; }
.creator-preview { list-style: none; padding: 0; margin: 10px 0 0; }
.creator-preview-item { font-size: 13px; color: #666; line-height: 1.4; padding: 2px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.creator-preview-item::before { content: "·"; margin-right: 6px; color: #ccc; }

/* Creator profile page */
.creator-profile { padding: 0 16px; }
.creator-header { display: flex; align-items: center; gap: 14px; padding: 16px 0; border-bottom: 1px solid var(--border, #e5e5e5); margin-bottom: 16px; }
.creator-header .creator-avatar { width: 56px; height: 56px; }
.creator-header-info { flex: 1; }
.creator-header-info h1 { font-size: 20px; margin: 0; }
.creator-header-info .creator-source-link { font-size: 13px; color: #0066cc; text-decoration: none; }
.back-link { display: inline-block; padding: 8px 0; font-size: 14px; color: #666; text-decoration: none; }
.back-link:hover { color: #333; }

/* Video/tweet item card in creator profile */
.item-list { display: flex; flex-direction: column; gap: 10px; }
.item-card { display: block; background: var(--card-bg, #fff); border: 1px solid var(--border, #e5e5e5); border-radius: 10px; padding: 14px; text-decoration: none; color: inherit; transition: background 0.15s; }
.item-card:hover { background: var(--card-hover, #f8f8f8); }
.item-card-title { font-weight: 600; font-size: 15px; margin-bottom: 4px; }
.item-card-subtitle { font-size: 13px; color: #666; line-height: 1.5; margin-bottom: 6px; }
.item-card-meta { font-size: 12px; color: #999; }
.item-card-status { display: inline-block; font-size: 11px; padding: 1px 6px; border-radius: 4px; }
.item-card-status.has-article { background: #e8f5e9; color: #2e7d32; }
.item-card-status.no-subtitle { background: #fff3e0; color: #e65100; }

/* Article detail page */
.article-page { padding: 0 16px; max-width: 720px; margin: 0 auto; }
.article-header { padding: 20px 0 16px; border-bottom: 1px solid var(--border, #e5e5e5); margin-bottom: 20px; }
.article-header h1 { font-size: 22px; line-height: 1.3; margin: 0 0 6px; }
.article-header .article-subtitle { font-size: 15px; color: #666; margin-bottom: 8px; }
.article-header .article-meta { font-size: 13px; color: #999; }
.article-header .article-meta a { color: #0066cc; text-decoration: none; }
.article-body { font-size: 15px; line-height: 1.8; }
.article-body h2 { font-size: 18px; margin-top: 24px; margin-bottom: 8px; }
.article-body blockquote { border-left: 3px solid #ddd; padding-left: 14px; margin: 12px 0; color: #555; font-style: italic; }
.article-body strong { color: #1a1a1a; }
```

- [ ] **Step 6: Run test**

Run: `.venv/bin/pytest tests/web/test_creator_routes.py::test_follow_tab_shows_creators -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add prism/web/routes.py prism/web/templates/creators.html prism/web/static/style.css tests/web/test_creator_routes.py
git commit -m "feat: creator list page replaces follow tab mixed feed"
```

---

### Task 7: Creator profile page

**Files:**
- Modify: `prism/web/routes.py`
- Create: `prism/web/templates/creator_profile.html`

- [ ] **Step 1: Write failing test**

Add to `tests/web/test_creator_routes.py`:

```python
def test_creator_profile_youtube(client, db):
    """Creator profile should show video list."""
    _seed_creators(db)
    resp = client.get("/creator/youtube:testchannel")
    assert resp.status_code == 200
    assert "Video 1" in resp.text
    assert "Video 2" in resp.text
    assert "Test Channel" in resp.text


def test_creator_profile_x(client, db):
    """Creator profile for X should show tweets."""
    _seed_creators(db)
    resp = client.get("/creator/x:karpathy")
    assert resp.status_code == 200
    assert "Tweet content here" in resp.text


def test_creator_profile_not_found(client, db):
    """Non-existent source should 404."""
    resp = client.get("/creator/youtube:nonexistent")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/web/test_creator_routes.py -v`
Expected: FAIL on new tests

- [ ] **Step 3: Add creator profile route**

In `prism/web/routes.py`, add:

```python
@web_router.get("/creator/{source_key:path}", response_class=HTMLResponse)
def creator_profile(request: Request, source_key: str):
    """Creator profile — list of videos/tweets for a specific source."""
    conn = _db(request)
    source = conn.execute(
        "SELECT * FROM sources WHERE source_key = ?", (source_key,)
    ).fetchone()
    if not source:
        return HTMLResponse("<div class='empty'>创作者不存在</div>", status_code=404)

    import yaml as _yaml
    config = {}
    if source["config_yaml"]:
        try:
            config = _yaml.safe_load(source["config_yaml"]) or {}
        except Exception:
            pass

    display_name = config.get("display_name", source["handle"] or source_key)
    channel_id = config.get("channel_id", "")

    # Avatar
    if source["type"] == "youtube":
        avatar = config.get("avatar", "")
        source_url = f"https://www.youtube.com/channel/{channel_id}" if channel_id else ""
    elif source["type"] in ("x", "follow_builders"):
        handle = source["handle"] or source_key.split(":")[-1]
        avatar = f"https://unavatar.io/x/{handle}"
        source_url = f"https://x.com/{handle}"
    else:
        avatar = ""
        source_url = ""

    # Fetch items with optional article info
    items = conn.execute(
        """SELECT ri.id, ri.url, ri.title, ri.body, ri.author, ri.created_at, ri.published_at,
                  a.id as article_id, a.subtitle as article_subtitle, a.word_count
           FROM raw_items ri
           LEFT JOIN articles a ON a.raw_item_id = ri.id
           WHERE ri.source_id = ?
           ORDER BY ri.created_at DESC
           LIMIT 100""",
        (source["id"],),
    ).fetchall()

    return _render(
        "creator_profile.html",
        request=request,
        source=source,
        display_name=display_name,
        avatar=avatar,
        source_url=source_url,
        source_type=source["type"],
        items=[dict(r) for r in items],
    )
```

- [ ] **Step 4: Create creator_profile.html template**

Create `prism/web/templates/creator_profile.html`:

```html
{% extends "base.html" %}
{% block content %}
<div class="creator-profile">
  <a class="back-link" href="/?tab=follow">← 关注</a>

  <div class="creator-header">
    {% if avatar %}
    <img class="creator-avatar" src="{{ avatar }}" alt="{{ display_name }}" onerror="this.style.display='none'">
    {% endif %}
    <div class="creator-header-info">
      <h1>{{ display_name }}</h1>
      {% if source_url %}
      <a class="creator-source-link" href="{{ source_url }}" target="_blank" rel="noopener">
        {% if source_type == 'youtube' %}打开 YouTube 频道{% else %}打开 X 主页{% endif %} ↗
      </a>
      {% endif %}
    </div>
  </div>

  <div class="item-list">
  {% for item in items %}
    {% if source_type == 'youtube' %}
    <a class="item-card" href="{% if item.article_id %}/article/{{ item.article_id }}{% else %}{{ item.url }}{% endif %}">
      <div class="item-card-title">{{ item.title }}</div>
      {% if item.article_subtitle %}
      <div class="item-card-subtitle">{{ item.article_subtitle }}</div>
      {% endif %}
      <div class="item-card-meta">
        {{ (item.published_at or item.created_at or '')[:10] }}
        {% if item.article_id %}
          · {{ item.word_count }} 字
          <span class="item-card-status has-article">已转文章</span>
        {% elif item.body %}
          <span class="item-card-status">待转换</span>
        {% else %}
          <span class="item-card-status no-subtitle">暂无字幕</span>
        {% endif %}
      </div>
    </a>
    {% else %}
    {# X tweets — link to original #}
    <a class="item-card" href="{{ item.url }}" target="_blank" rel="noopener">
      <div class="item-card-subtitle">{{ item.body[:280] }}</div>
      <div class="item-card-meta">
        {{ (item.published_at or item.created_at or '')[:10] }}
        · {{ item.author }}
      </div>
    </a>
    {% endif %}
  {% endfor %}
  </div>
</div>
{% endblock %}
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/web/test_creator_routes.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add prism/web/routes.py prism/web/templates/creator_profile.html
git commit -m "feat: creator profile page with video/tweet list"
```

---

### Task 8: Article detail page

**Files:**
- Modify: `prism/web/routes.py`
- Create: `prism/web/templates/article.html`

- [ ] **Step 1: Write failing test**

Add to `tests/web/test_creator_routes.py`:

```python
def test_article_detail_page(client, db):
    """Article detail page should render structured content."""
    _seed_creators(db)
    # Insert an article for Video 1
    db.execute(
        """INSERT INTO articles (raw_item_id, title, subtitle, structured_body, highlights_json, word_count, model_id)
           VALUES (1, 'Video 1', 'Summary of video', '## Section 1\nContent here\n\n## Section 2\nMore content',
                   '["Key quote 1"]', 200, 'omlx')"""
    )
    db.commit()

    article = db.execute("SELECT id FROM articles WHERE raw_item_id = 1").fetchone()
    resp = client.get(f"/article/{article['id']}")
    assert resp.status_code == 200
    assert "Video 1" in resp.text
    assert "Summary of video" in resp.text
    assert "Section 1" in resp.text
    assert "Content here" in resp.text


def test_article_not_found(client, db):
    """Non-existent article should 404."""
    resp = client.get("/article/99999")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/web/test_creator_routes.py::test_article_detail_page tests/web/test_creator_routes.py::test_article_not_found -v`
Expected: FAIL

- [ ] **Step 3: Add article route**

In `prism/web/routes.py`, add:

```python
@web_router.get("/article/{article_id}", response_class=HTMLResponse)
def article_detail(request: Request, article_id: int):
    """Article detail page — structured content from video subtitles."""
    conn = _db(request)
    row = conn.execute(
        """SELECT a.*, ri.url as source_url, ri.author, ri.published_at, ri.created_at as item_created,
                  s.source_key, s.type as source_type
           FROM articles a
           JOIN raw_items ri ON a.raw_item_id = ri.id
           JOIN sources s ON ri.source_id = s.id
           WHERE a.id = ?""",
        (article_id,),
    ).fetchone()

    if not row:
        return HTMLResponse("<div class='empty'>文章不存在</div>", status_code=404)

    import markdown as _md
    body_html = _md.markdown(
        row["structured_body"] or "",
        extensions=["extra", "sane_lists"],
    )

    import json
    highlights = []
    if row["highlights_json"]:
        try:
            highlights = json.loads(row["highlights_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    return _render(
        "article.html",
        request=request,
        article=dict(row),
        body_html=body_html,
        highlights=highlights,
        source_key=row["source_key"],
    )
```

- [ ] **Step 4: Create article.html template**

Create `prism/web/templates/article.html`:

```html
{% extends "base.html" %}
{% block content %}
<div class="article-page">
  <a class="back-link" href="/creator/{{ source_key }}">← {{ article.author or '返回' }}</a>

  <div class="article-header">
    <h1>{{ article.title }}</h1>
    {% if article.subtitle %}
    <div class="article-subtitle">{{ article.subtitle }}</div>
    {% endif %}
    <div class="article-meta">
      {{ (article.published_at or article.item_created or '')[:10] }}
      · {{ article.word_count }} 字
      · <a href="{{ article.source_url }}" target="_blank" rel="noopener">观看原视频 ↗</a>
    </div>
  </div>

  <div class="article-body">
    {{ body_html | safe }}
  </div>

  {% if highlights %}
  <div class="article-highlights">
    <h2>关键引用</h2>
    {% for h in highlights %}
    <blockquote>{{ h }}</blockquote>
    {% endfor %}
  </div>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 5: Install markdown library if needed**

Run: `.venv/bin/pip install markdown`

- [ ] **Step 6: Run tests**

Run: `.venv/bin/pytest tests/web/test_creator_routes.py -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add prism/web/routes.py prism/web/templates/article.html
git commit -m "feat: article detail page with Markdown rendering"
```

---

### Task 9: Integration test + manual verification

**Files:** None new — test existing code together.

- [ ] **Step 1: Run full test suite**

```bash
.venv/bin/pytest tests/ -v --tb=short
```

Expected: ALL PASS (no regressions)

- [ ] **Step 2: Start dev server and verify UI**

```bash
.venv/bin/prism serve --port 8080
```

Open `http://localhost:8080/?tab=follow` and verify:
1. Creator cards appear grouped by YouTube / X
2. Each card shows avatar, name, item count, latest 2 titles
3. Clicking a YouTube creator → shows video list with status badges
4. Clicking a video with article → shows structured article page
5. Back navigation works (← links)

- [ ] **Step 3: Commit any fixes from manual testing**

```bash
git add -A
git commit -m "fix: polish creator pages after manual testing"
```

---

## Task Dependency Graph

```
Task 1 (articles table)
    ↓
Task 2 (YouTube adapter) → Task 3 (YAML split + migration)
    ↓
Task 4 (articlize pipeline) → Task 5 (CLI command)
    ↓
Task 6 (creator list page) → Task 7 (creator profile) → Task 8 (article detail)
    ↓
Task 9 (integration test)
```

Tasks 1, 2, 3 can be parallelized. Tasks 6, 7, 8 are sequential (each builds on the previous template/route).
