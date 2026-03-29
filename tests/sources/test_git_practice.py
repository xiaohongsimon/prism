"""Tests for git_practice adapter."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prism.sources.git_practice import (
    GitPracticeAdapter,
    _parse_commits,
    sync_repo,
)

# ---------------------------------------------------------------------------
# Unit: _parse_commits
# ---------------------------------------------------------------------------

def test_parse_commits_basic():
    log_output = (
        "abc12345|feat: add new feature|Alice|2026-03-29 10:00:00 +0800\n"
        "def67890|fix: resolve bug|Bob|2026-03-28 09:00:00 +0800\n"
    )
    commits = _parse_commits(log_output)
    assert len(commits) == 2
    assert commits[0]["hash"] == "abc12345"
    assert commits[0]["subject"] == "feat: add new feature"
    assert commits[0]["author"] == "Alice"
    assert commits[1]["hash"] == "def67890"
    assert commits[1]["subject"] == "fix: resolve bug"


def test_parse_commits_empty():
    assert _parse_commits("") == []


def test_parse_commits_skips_malformed_lines():
    log_output = "only-two|fields\nabc|subject|author|2026-03-29 10:00:00 +0800\n"
    commits = _parse_commits(log_output)
    assert len(commits) == 1
    assert commits[0]["hash"] == "abc"


def test_parse_commits_subject_with_pipe():
    """split("|", 3) gives hash, subject, author, date — pipe in subject gets merged into subject."""
    # Format: %H|%s|%an|%ai — split at most 3 times → 4 parts
    # "abc|feat: x|y|author|2026-..." → ["abc", "feat: x", "y", "author|2026-..."]
    # subject = parts[1] = "feat: x" (pipe goes to parts[2] which becomes author)
    log_output = "abc|feat: x|y|author|2026-03-29 10:00:00 +0800\n"
    commits = _parse_commits(log_output)
    assert len(commits) == 1
    # With split("|", 3): hash=abc, subject="feat: x", author="y", date="author|2026-..."
    assert commits[0]["hash"] == "abc"
    assert commits[0]["subject"] == "feat: x"


# ---------------------------------------------------------------------------
# Unit: sync_repo with subprocess mock
# ---------------------------------------------------------------------------

FAKE_LOG = (
    "aabbccdd|feat: initial commit|Developer|2026-03-29 08:00:00 +0800\n"
    "11223344|chore: add tests|Developer|2026-03-28 20:00:00 +0800\n"
)

FAKE_DIFF_STAT = (
    " prism/sources/git_practice.py | 50 +++++\n"
    " tests/sources/test_git.py    | 30 +++\n"
    " 2 files changed, 80 insertions(+)"
)


def _make_run_result(stdout: str = "", returncode: int = 0, stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.returncode = returncode
    m.stderr = stderr
    return m


def test_sync_repo_generates_raw_item(tmp_path):
    """sync_repo returns a RawItem when commits exist."""
    # Create a fake git repo dir so Path.exists() passes
    repo = tmp_path / "myproject"
    repo.mkdir()

    call_count = 0
    def fake_run(cmd, capture_output, text, **kw):
        nonlocal call_count
        call_count += 1
        if "log" in cmd:
            return _make_run_result(stdout=FAKE_LOG)
        else:
            return _make_run_result(stdout=FAKE_DIFF_STAT)

    with patch("prism.sources.git_practice.subprocess.run", side_effect=fake_run):
        item = sync_repo(str(repo), lookback_hours=24)

    assert item is not None
    assert item.title == f"[Practice] myproject daily activity"
    assert "myproject" in item.url
    assert "feat: initial commit" in item.body
    assert "chore: add tests" in item.body
    # FAKE_DIFF_STAT has a leading space; _run_git strips stdout, so compare stripped
    assert FAKE_DIFF_STAT.strip() in item.body

    raw = json.loads(item.raw_json)
    assert raw["repo_name"] == "myproject"
    assert len(raw["commits"]) == 2
    assert raw["commits"][0]["hash"] == "aabbccdd"


def test_sync_repo_no_recent_commits(tmp_path):
    """sync_repo returns None when there are no recent commits."""
    repo = tmp_path / "emptyproject"
    repo.mkdir()

    with patch(
        "prism.sources.git_practice.subprocess.run",
        return_value=_make_run_result(stdout=""),
    ):
        item = sync_repo(str(repo), lookback_hours=24)

    assert item is None


def test_sync_repo_missing_path():
    """sync_repo returns None when repo path does not exist."""
    item = sync_repo("/nonexistent/path/that/does/not/exist", lookback_hours=24)
    assert item is None


def test_sync_repo_git_error(tmp_path):
    """sync_repo returns None when git log fails."""
    repo = tmp_path / "badrepo"
    repo.mkdir()

    with patch(
        "prism.sources.git_practice.subprocess.run",
        return_value=_make_run_result(stdout="", returncode=128, stderr="not a git repo"),
    ):
        item = sync_repo(str(repo), lookback_hours=24)

    assert item is None


# ---------------------------------------------------------------------------
# Integration: GitPracticeAdapter.sync
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_adapter_sync_single_repo(tmp_path):
    """Adapter returns one item for a repo with commits."""
    repo = tmp_path / "prism"
    repo.mkdir()

    def fake_run(cmd, capture_output, text, **kw):
        if "log" in cmd:
            return _make_run_result(stdout=FAKE_LOG)
        return _make_run_result(stdout=FAKE_DIFF_STAT)

    with patch("prism.sources.git_practice.subprocess.run", side_effect=fake_run):
        adapter = GitPracticeAdapter()
        result = await adapter.sync({
            "key": "practice:git",
            "repos": [str(repo)],
            "lookback_hours": 24,
        })

    assert result.success
    assert result.source_key == "practice:git"
    assert len(result.items) == 1
    assert result.stats["items"] == 1
    assert result.stats["skipped"] == 0


@pytest.mark.asyncio
async def test_adapter_sync_skips_empty_repos(tmp_path):
    """Adapter skips repos with no recent commits."""
    repo = tmp_path / "quietrepo"
    repo.mkdir()

    with patch(
        "prism.sources.git_practice.subprocess.run",
        return_value=_make_run_result(stdout=""),
    ):
        adapter = GitPracticeAdapter()
        result = await adapter.sync({
            "key": "practice:git",
            "repos": [str(repo)],
            "lookback_hours": 24,
        })

    assert result.success
    assert len(result.items) == 0
    assert result.stats["skipped"] == 1


@pytest.mark.asyncio
async def test_adapter_sync_multiple_repos(tmp_path):
    """Adapter handles multiple repos independently."""
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    repo_a.mkdir()
    repo_b.mkdir()

    call_log = []

    def fake_run(cmd, capture_output, text, **kw):
        if "log" in cmd:
            # repo_a has commits, repo_b doesn't
            if str(repo_a) in cmd:
                return _make_run_result(stdout=FAKE_LOG)
            return _make_run_result(stdout="")
        return _make_run_result(stdout=FAKE_DIFF_STAT)

    with patch("prism.sources.git_practice.subprocess.run", side_effect=fake_run):
        adapter = GitPracticeAdapter()
        result = await adapter.sync({
            "key": "practice:git",
            "repos": [str(repo_a), str(repo_b)],
            "lookback_hours": 24,
        })

    assert result.success
    assert len(result.items) == 1
    assert result.stats["repos"] == 2
    assert result.stats["items"] == 1
    assert result.stats["skipped"] == 1


@pytest.mark.asyncio
async def test_adapter_sync_empty_repos_list():
    """Adapter handles empty repos list gracefully."""
    adapter = GitPracticeAdapter()
    result = await adapter.sync({"key": "practice:git", "repos": []})

    assert result.success
    assert len(result.items) == 0
    assert result.stats["repos"] == 0
