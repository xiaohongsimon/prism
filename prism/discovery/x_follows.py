"""X follows discovery — sync `bird following` output into config/sources.yaml.

Spec: docs/superpowers/specs/2026-04-20-x-follows-discovery.md

Design notes:
- bird CLI is fragile (depends on X private GraphQL endpoints + cookie auth).
  This module treats every bird interaction as best-effort; failures must not
  crash the daily cron. All hard failures get logged to decision_log instead.
- The bird `following --json` schema is not officially documented. parse_follows()
  is intentionally defensive: it tries multiple plausible field names and
  silently skips malformed entries.
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from prism.sources.yaml_editor import _source_key, load_sources_list
from prism.source_manager import add_source

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FollowEntry:
    handle: str
    display_name: str = ""
    user_id: str = ""


@dataclass
class DiffResult:
    to_add: list[FollowEntry]      # in X following, not in yaml
    orphans: list[str]             # handles in yaml, no longer followed on X
    yaml_x_handles: set[str]       # current state, for debugging


@dataclass
class SyncOutcome:
    status: str                    # "ok" | "blocked" | "bird_missing" | "error"
    scanned: int = 0
    added: int = 0
    orphan: int = 0
    truncated: int = 0             # how many to_add we dropped due to max_new
    message: str = ""


# ---------------------------------------------------------------------------
# bird subprocess wrapper
# ---------------------------------------------------------------------------


def _bird_available() -> bool:
    return shutil.which("bird") is not None


def run_bird_following(
    *,
    max_pages: int = 50,
    timeout_s: int = 90,
) -> tuple[Optional[list[dict]], str]:
    """Call `bird following --all --json --max-pages N`.

    Returns (parsed_json_list_or_None, error_message).
    Never raises. The caller decides what to do with failures.
    """
    if not _bird_available():
        return None, "bird CLI not found on PATH (install: npm i -g @leavingme/bird)"

    cmd = [
        "bird",
        "following",
        "--all",
        "--max-pages", str(max_pages),
        "--json",
        "--plain",
        "--no-color",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return None, f"bird timed out after {timeout_s}s"
    except Exception as e:  # noqa: BLE001
        return None, f"bird subprocess failed: {e}"

    if proc.returncode != 0:
        # Common cause: missing cookies. Surface stderr verbatim (truncated).
        stderr = (proc.stderr or "").strip().splitlines()
        # Look for the credential hint
        if any("auth_token" in ln or "credentials" in ln.lower() for ln in stderr):
            return None, "credentials missing (run `bird check`; login to x.com or set AUTH_TOKEN/CT0)"
        head = " | ".join(stderr[:6])
        return None, f"bird exited {proc.returncode}: {head[:400]}"

    stdout = proc.stdout.strip()
    if not stdout:
        return [], ""

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        return None, f"bird returned non-JSON: {e}"

    # bird may return either a bare list or {users: [...]} — handle both.
    if isinstance(data, dict):
        for key in ("users", "following", "data", "items"):
            if isinstance(data.get(key), list):
                return data[key], ""
        return None, f"bird JSON has no users array (keys: {list(data)[:5]})"
    if isinstance(data, list):
        return data, ""
    return None, f"bird JSON unexpected type: {type(data).__name__}"


# ---------------------------------------------------------------------------
# Parsing & diff
# ---------------------------------------------------------------------------


# Field name fallbacks — bird's schema is undocumented; cover known variants.
_HANDLE_KEYS = ("screen_name", "username", "handle")
_NAME_KEYS = ("name", "display_name", "displayName")
_ID_KEYS = ("id_str", "rest_id", "id", "user_id")


def _pick(d: dict, keys: tuple[str, ...]) -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    return ""


def parse_follows(raw: list[dict]) -> list[FollowEntry]:
    """Defensively extract FollowEntry rows from bird's JSON output."""
    out: list[FollowEntry] = []
    seen: set[str] = set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        # bird sometimes nests user data under {user: {...}} or {legacy: {...}}
        candidates = [item]
        for nest_key in ("user", "legacy", "core"):
            sub = item.get(nest_key)
            if isinstance(sub, dict):
                candidates.append(sub)

        handle = ""
        display = ""
        uid = ""
        for c in candidates:
            handle = handle or _pick(c, _HANDLE_KEYS)
            display = display or _pick(c, _NAME_KEYS)
            uid = uid or _pick(c, _ID_KEYS)
        if not handle:
            continue
        # X handles are case-insensitive but we keep the casing bird reports;
        # dedup is on lowercase.
        key = handle.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(FollowEntry(handle=handle, display_name=display, user_id=uid))
    return out


def _yaml_x_handles(yaml_path: Path) -> set[str]:
    """Return lowercase set of handles already configured as type: x sources."""
    handles: set[str] = set()
    for entry in load_sources_list(yaml_path):
        if entry.get("type") == "x":
            h = (entry.get("handle") or "").strip().lower()
            if h:
                handles.add(h)
    return handles


def diff_follows(
    follows: Iterable[FollowEntry],
    yaml_path: Path,
) -> DiffResult:
    yaml_handles = _yaml_x_handles(yaml_path)
    follow_handles_lc = {f.handle.lower() for f in follows}

    to_add = [f for f in follows if f.handle.lower() not in yaml_handles]
    orphans = sorted(h for h in yaml_handles if h not in follow_handles_lc)
    return DiffResult(to_add=to_add, orphans=orphans, yaml_x_handles=yaml_handles)


# ---------------------------------------------------------------------------
# Apply (writes yaml + decision_log)
# ---------------------------------------------------------------------------


def _log_decision(
    conn: sqlite3.Connection,
    *,
    action: str,
    reason: str,
    context: dict,
) -> None:
    conn.execute(
        "INSERT INTO decision_log (layer, action, reason, context_json) "
        "VALUES ('recall', ?, ?, ?)",
        (action, reason, json.dumps(context, ensure_ascii=False)),
    )
    conn.commit()


def apply_diff(
    conn: sqlite3.Connection,
    yaml_path: Path,
    diff: DiffResult,
    *,
    max_new: int = 30,
    depth: str = "thread",
    dry_run: bool = True,
    check_orphans: bool = False,
) -> SyncOutcome:
    """Apply the diff: add to_add into yaml; optionally log orphans.

    Orphan detection is OFF by default because bird's `following` endpoint has
    been observed to return an incomplete view (X may paginate-truncate the
    GraphQL response). With incomplete data, every account bird *didn't* return
    looks like an "orphan" — false positives. Only enable --check-orphans when
    you trust bird's coverage (e.g. small follow lists).
    """
    truncated = max(0, len(diff.to_add) - max_new)
    additions = diff.to_add[:max_new]

    effective_orphans = diff.orphans if check_orphans else []

    outcome = SyncOutcome(
        status="ok",
        scanned=len(diff.yaml_x_handles) + len(diff.to_add),
        added=0,
        orphan=len(effective_orphans),
        truncated=truncated,
    )

    if dry_run:
        outcome.message = "dry-run; no changes written"
        return outcome

    for entry in additions:
        cfg = {"depth": depth}
        if entry.display_name:
            cfg["display_name"] = entry.display_name
        try:
            add_source(
                conn, yaml_path,
                type="x", handle=entry.handle, config=cfg,
            )
        except Exception as exc:  # noqa: BLE001
            # Log and continue — one bad write shouldn't take down the loop
            logger.exception("add_source failed for @%s", entry.handle)
            _log_decision(
                conn,
                action="x_follow_add_failed",
                reason=f"add @{entry.handle} failed: {exc}",
                context={"handle": entry.handle},
            )
            continue
        outcome.added += 1
        _log_decision(
            conn,
            action="x_follow_added",
            reason=f"auto-added @{entry.handle} from X following",
            context={
                "handle": entry.handle,
                "display_name": entry.display_name,
                "user_id": entry.user_id,
            },
        )

    for h in effective_orphans:
        _log_decision(
            conn,
            action="x_follow_orphan",
            reason=f"@{h} unfollowed on X but still in sources.yaml",
            context={"handle": h},
        )

    _log_decision(
        conn,
        action="x_follow_scan",
        reason=(
            f"scanned, +{outcome.added} new, {outcome.orphan} orphan, "
            f"{truncated} truncated"
        ),
        context={
            "added": outcome.added,
            "orphan": outcome.orphan,
            "truncated": truncated,
            "max_new": max_new,
        },
    )
    return outcome


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------


def sync_follows(
    conn: sqlite3.Connection,
    yaml_path: Path,
    *,
    dry_run: bool = True,
    max_new: int = 30,
    depth: str = "thread",
    check_orphans: bool = False,
) -> tuple[SyncOutcome, Optional[DiffResult]]:
    """Full pipeline. Returns (outcome, diff_or_None_on_error)."""
    raw, err = run_bird_following()
    if raw is None:
        # Determine status flavor for clearer reporting
        if err and "not found" in err:
            status = "bird_missing"
        elif err and ("credentials" in err.lower() or "auth_token" in err.lower()):
            status = "blocked"
        else:
            status = "error"
        outcome = SyncOutcome(status=status, message=err)
        # Always log blockages so we can see them in decision_log over time
        _log_decision(
            conn,
            action="x_follow_blocked",
            reason=err or "unknown bird failure",
            context={"status": status},
        )
        return outcome, None

    follows = parse_follows(raw)
    diff = diff_follows(follows, yaml_path)
    outcome = apply_diff(
        conn, yaml_path, diff,
        max_new=max_new, depth=depth, dry_run=dry_run,
        check_orphans=check_orphans,
    )
    return outcome, diff
