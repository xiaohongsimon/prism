"""Tests for claude_sessions adapter."""

import json
from pathlib import Path

import pytest

from prism.sources.claude_sessions import (
    ClaudeSessionsAdapter,
    _project_name_from_path,
    sync_memory_dir,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project(base: Path, project_name: str, content: str) -> Path:
    """Create a fake Claude project memory structure."""
    memory_dir = base / project_name / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_file = memory_dir / "MEMORY.md"
    memory_file.write_text(content, encoding="utf-8")
    return memory_file


# ---------------------------------------------------------------------------
# Unit: _project_name_from_path
# ---------------------------------------------------------------------------

def test_project_name_from_path(tmp_path):
    memory_file = tmp_path / "my-project" / "memory" / "MEMORY.md"
    memory_file.parent.mkdir(parents=True)
    memory_file.touch()
    assert _project_name_from_path(memory_file) == "my-project"


# ---------------------------------------------------------------------------
# Unit: sync_memory_dir
# ---------------------------------------------------------------------------

def test_sync_memory_dir_reads_content(tmp_path):
    """sync_memory_dir returns RawItems with MEMORY.md content."""
    content = "# Project Memory\n\n- Learned that X is better than Y\n- Next: implement Z"
    _make_project(tmp_path, "-Users-leehom-work-prism", content)

    items = sync_memory_dir(str(tmp_path))

    assert len(items) == 1
    item = items[0]
    assert item.title == "[Practice] Claude Code memory update — -Users-leehom-work-prism"
    assert content in item.body
    assert "claude:-Users-leehom-work-prism:" in item.url


def test_sync_memory_dir_multiple_projects(tmp_path):
    """sync_memory_dir returns one item per project."""
    _make_project(tmp_path, "project-alpha", "Alpha memory content")
    _make_project(tmp_path, "project-beta", "Beta memory content")
    _make_project(tmp_path, "project-gamma", "Gamma memory content")

    items = sync_memory_dir(str(tmp_path))

    assert len(items) == 3
    titles = {item.title for item in items}
    assert "[Practice] Claude Code memory update — project-alpha" in titles
    assert "[Practice] Claude Code memory update — project-beta" in titles
    assert "[Practice] Claude Code memory update — project-gamma" in titles


def test_sync_memory_dir_empty_directory(tmp_path):
    """sync_memory_dir returns empty list when no MEMORY.md files exist."""
    items = sync_memory_dir(str(tmp_path))
    assert items == []


def test_sync_memory_dir_nonexistent_path():
    """sync_memory_dir returns empty list when directory doesn't exist."""
    items = sync_memory_dir("/nonexistent/path/that/does/not/exist")
    assert items == []


def test_sync_memory_dir_skips_empty_content(tmp_path):
    """sync_memory_dir skips projects with empty MEMORY.md."""
    _make_project(tmp_path, "has-content", "Some useful memory here")
    # Create an empty MEMORY.md
    empty_dir = tmp_path / "empty-project" / "memory"
    empty_dir.mkdir(parents=True)
    (empty_dir / "MEMORY.md").write_text("", encoding="utf-8")

    items = sync_memory_dir(str(tmp_path))

    assert len(items) == 1
    assert "has-content" in items[0].title


def test_sync_memory_dir_raw_json_fields(tmp_path):
    """sync_memory_dir populates raw_json with correct fields."""
    content = "# Memory\n\n- key fact"
    _make_project(tmp_path, "test-project", content)

    items = sync_memory_dir(str(tmp_path))
    assert len(items) == 1

    raw = json.loads(items[0].raw_json)
    assert raw["project_name"] == "test-project"
    assert "memory_file" in raw
    assert "MEMORY.md" in raw["memory_file"]
    assert raw["content_length"] == len(content)


def test_sync_memory_dir_url_format(tmp_path):
    """URL is in expected format: claude:{project_name}:{date}."""
    _make_project(tmp_path, "my-project", "Some memory")

    items = sync_memory_dir(str(tmp_path))
    assert len(items) == 1

    url = items[0].url
    parts = url.split(":")
    assert parts[0] == "claude"
    assert parts[1] == "my-project"
    # Date part should be YYYY-MM-DD
    assert len(parts[2]) == 10
    assert parts[2].count("-") == 2


# ---------------------------------------------------------------------------
# Integration: ClaudeSessionsAdapter.sync
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_adapter_sync_reads_memory_dirs(tmp_path):
    """Adapter reads all MEMORY.md files and returns items."""
    _make_project(tmp_path, "proj-one", "Memory content one")
    _make_project(tmp_path, "proj-two", "Memory content two")

    adapter = ClaudeSessionsAdapter()
    result = await adapter.sync({
        "key": "practice:claude",
        "memory_dirs": [str(tmp_path)],
    })

    assert result.success
    assert result.source_key == "practice:claude"
    assert len(result.items) == 2
    assert result.stats["dirs"] == 1
    assert result.stats["items"] == 2


@pytest.mark.asyncio
async def test_adapter_sync_empty_dirs(tmp_path):
    """Adapter returns empty items for directory with no MEMORY.md files."""
    adapter = ClaudeSessionsAdapter()
    result = await adapter.sync({
        "key": "practice:claude",
        "memory_dirs": [str(tmp_path)],
    })

    assert result.success
    assert len(result.items) == 0
    assert result.stats["items"] == 0


@pytest.mark.asyncio
async def test_adapter_sync_missing_dir():
    """Adapter handles missing directory gracefully."""
    adapter = ClaudeSessionsAdapter()
    result = await adapter.sync({
        "key": "practice:claude",
        "memory_dirs": ["/nonexistent/path"],
    })

    assert result.success
    assert len(result.items) == 0


@pytest.mark.asyncio
async def test_adapter_sync_no_dirs_configured():
    """Adapter returns success with no items when no dirs configured."""
    adapter = ClaudeSessionsAdapter()
    result = await adapter.sync({"key": "practice:claude", "memory_dirs": []})

    assert result.success
    assert len(result.items) == 0
    assert result.stats["dirs"] == 0


@pytest.mark.asyncio
async def test_adapter_sync_multiple_dirs(tmp_path):
    """Adapter aggregates items from multiple memory_dirs."""
    dir_a = tmp_path / "dir_a"
    dir_b = tmp_path / "dir_b"
    dir_a.mkdir()
    dir_b.mkdir()

    _make_project(dir_a, "alpha", "Alpha memories")
    _make_project(dir_b, "beta", "Beta memories")
    _make_project(dir_b, "gamma", "Gamma memories")

    adapter = ClaudeSessionsAdapter()
    result = await adapter.sync({
        "key": "practice:claude",
        "memory_dirs": [str(dir_a), str(dir_b)],
    })

    assert result.success
    assert len(result.items) == 3
    assert result.stats["dirs"] == 2
    assert result.stats["items"] == 3
