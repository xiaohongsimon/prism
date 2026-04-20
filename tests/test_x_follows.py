"""Tests for prism.discovery.x_follows.

Bird subprocess is mocked — these tests run offline.
Spec: docs/superpowers/specs/2026-04-20-x-follows-discovery.md
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from prism.discovery import x_follows
from prism.discovery.x_follows import (
    DiffResult,
    FollowEntry,
    apply_diff,
    diff_follows,
    parse_follows,
    sync_follows,
)


# ---------------------------------------------------------------------------
# parse_follows — defensive against schema variation
# ---------------------------------------------------------------------------


def test_parse_follows_handles_screen_name():
    raw = [
        {"screen_name": "karpathy", "name": "Andrej Karpathy", "id_str": "33836629"},
        {"screen_name": "simonw", "name": "Simon Willison"},
    ]
    out = parse_follows(raw)
    assert [f.handle for f in out] == ["karpathy", "simonw"]
    assert out[0].display_name == "Andrej Karpathy"
    assert out[0].user_id == "33836629"


def test_parse_follows_handles_nested_user_object():
    raw = [
        {"user": {"username": "levelsio", "name": "Pieter Levels", "rest_id": "1234"}},
        {"legacy": {"screen_name": "swyx", "name": "swyx"}},
    ]
    out = parse_follows(raw)
    handles = {f.handle for f in out}
    assert handles == {"levelsio", "swyx"}


def test_parse_follows_skips_malformed():
    raw = [
        {"screen_name": "valid"},
        {},                                  # empty
        {"name": "no handle"},               # no handle key
        "not a dict",                        # type: ignore
        {"screen_name": ""},                 # empty handle
    ]
    out = parse_follows(raw)
    assert [f.handle for f in out] == ["valid"]


def test_parse_follows_dedupes_case_insensitive():
    raw = [
        {"screen_name": "Karpathy"},
        {"screen_name": "karpathy"},     # duplicate, different case
        {"screen_name": "KARPATHY"},
    ]
    out = parse_follows(raw)
    assert len(out) == 1
    assert out[0].handle == "Karpathy"   # first wins


def test_parse_follows_handles_empty_input():
    assert parse_follows([]) == []
    assert parse_follows(None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# diff_follows
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, x_handles: list[str]) -> Path:
    """Write a minimal sources.yaml with the given X handles."""
    body = "sources:\n"
    for h in x_handles:
        body += f"  - type: x\n    handle: {h}\n    depth: thread\n"
    # Add a non-X entry to make sure it's ignored by diff
    body += "  - type: hackernews\n    key: hn:best\n    feed_url: x\n"
    p = tmp_path / "sources.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_diff_basic(tmp_path):
    yaml = _write_yaml(tmp_path, ["karpathy", "simonw"])
    follows = [
        FollowEntry(handle="karpathy"),     # already in yaml
        FollowEntry(handle="levelsio"),     # new
        FollowEntry(handle="amasad"),       # new
    ]
    diff = diff_follows(follows, yaml)
    assert {f.handle for f in diff.to_add} == {"levelsio", "amasad"}
    assert diff.orphans == ["simonw"]      # in yaml, not followed


def test_diff_empty_yaml(tmp_path):
    yaml = _write_yaml(tmp_path, [])
    follows = [FollowEntry(handle="a"), FollowEntry(handle="b")]
    diff = diff_follows(follows, yaml)
    assert {f.handle for f in diff.to_add} == {"a", "b"}
    assert diff.orphans == []


def test_diff_case_insensitive(tmp_path):
    yaml = _write_yaml(tmp_path, ["Karpathy"])
    follows = [FollowEntry(handle="karpathy")]   # different case, should match
    diff = diff_follows(follows, yaml)
    assert diff.to_add == []
    assert diff.orphans == []


# ---------------------------------------------------------------------------
# apply_diff
# ---------------------------------------------------------------------------


def test_apply_dry_run_does_not_write(db, tmp_path):
    yaml = _write_yaml(tmp_path, ["a"])
    diff = DiffResult(
        to_add=[FollowEntry(handle="b", display_name="B")],
        orphans=["a"],
        yaml_x_handles={"a"},
    )
    before = yaml.read_text()
    outcome = apply_diff(db, yaml, diff, dry_run=True)
    after = yaml.read_text()
    assert before == after
    assert outcome.added == 0
    # Decision log should also be empty in dry-run
    rows = db.execute("SELECT count(*) FROM decision_log").fetchone()[0]
    assert rows == 0


def test_apply_writes_yaml_and_decision_log_with_orphans(db, tmp_path):
    yaml = _write_yaml(tmp_path, ["a"])
    diff = DiffResult(
        to_add=[FollowEntry(handle="newguy", display_name="New Guy", user_id="999")],
        orphans=["a"],
        yaml_x_handles={"a"},
    )
    outcome = apply_diff(db, yaml, diff, dry_run=False, check_orphans=True)

    # Yaml updated
    assert "newguy" in yaml.read_text()

    # Counts
    assert outcome.added == 1
    assert outcome.orphan == 1

    # Decision log: one add, one orphan, one scan
    actions = [r[0] for r in db.execute(
        "SELECT action FROM decision_log ORDER BY id"
    ).fetchall()]
    assert "x_follow_added" in actions
    assert "x_follow_orphan" in actions
    assert "x_follow_scan" in actions


def test_apply_skips_orphans_by_default(db, tmp_path):
    """Default behavior: don't log orphans (bird view may be incomplete)."""
    yaml = _write_yaml(tmp_path, ["a"])
    diff = DiffResult(
        to_add=[FollowEntry(handle="b")],
        orphans=["a"],
        yaml_x_handles={"a"},
    )
    outcome = apply_diff(db, yaml, diff, dry_run=False)
    assert outcome.added == 1
    assert outcome.orphan == 0       # not logged

    actions = [r[0] for r in db.execute(
        "SELECT action FROM decision_log ORDER BY id"
    ).fetchall()]
    assert "x_follow_orphan" not in actions
    assert "x_follow_added" in actions


def test_apply_truncates_to_max_new(db, tmp_path):
    yaml = _write_yaml(tmp_path, [])
    follows = [FollowEntry(handle=f"u{i}") for i in range(10)]
    diff = DiffResult(to_add=follows, orphans=[], yaml_x_handles=set())
    outcome = apply_diff(db, yaml, diff, max_new=3, dry_run=False)
    assert outcome.added == 3
    assert outcome.truncated == 7


# ---------------------------------------------------------------------------
# sync_follows — full driver with mocked bird
# ---------------------------------------------------------------------------


def test_sync_follows_credentials_missing_exits_clean(db, tmp_path):
    yaml = _write_yaml(tmp_path, ["existing"])
    with patch.object(
        x_follows, "run_bird_following",
        return_value=(None, "credentials missing (run `bird check`)"),
    ):
        outcome, diff = sync_follows(db, yaml, dry_run=False)
    assert outcome.status == "blocked"
    assert diff is None
    # Should still log the blockage so we can see it over time
    rows = db.execute(
        "SELECT action FROM decision_log WHERE action = 'x_follow_blocked'"
    ).fetchall()
    assert len(rows) == 1


def test_sync_follows_bird_missing_exits_clean(db, tmp_path):
    yaml = _write_yaml(tmp_path, [])
    with patch.object(
        x_follows, "run_bird_following",
        return_value=(None, "bird CLI not found on PATH (install: ...)"),
    ):
        outcome, _ = sync_follows(db, yaml, dry_run=False)
    assert outcome.status == "bird_missing"


def test_sync_follows_happy_path(db, tmp_path):
    yaml = _write_yaml(tmp_path, ["a"])
    fake_bird_output = [
        {"screen_name": "a", "name": "A"},
        {"screen_name": "b", "name": "B"},
        {"screen_name": "c", "name": "C"},
    ]
    with patch.object(
        x_follows, "run_bird_following",
        return_value=(fake_bird_output, ""),
    ):
        outcome, diff = sync_follows(db, yaml, dry_run=False, max_new=10)
    assert outcome.status == "ok"
    assert outcome.added == 2
    assert {f.handle for f in diff.to_add} == {"b", "c"}
    text = yaml.read_text()
    assert "handle: b" in text
    assert "handle: c" in text
