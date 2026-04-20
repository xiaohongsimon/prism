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


def get_followed_authors(conn: sqlite3.Connection) -> set[str]:
    """Authors the user follows.

    Union of two sources:
    1. X-type entries in `sources` (sources.yaml is the source of truth for
       the user's existing follow graph — handle == author).
    2. `preference_weights` rows where dimension='author' and weight reaches
       the follow threshold (set via the feed 'follow_author' action).

    Handles are compared case-insensitively because `raw_items.author` and
    `sources.handle` occasionally differ in case.
    """
    followed: set[str] = set()
    for row in conn.execute(
        "SELECT handle FROM sources "
        "WHERE type = 'x' AND enabled = 1 AND handle != ''"
    ).fetchall():
        followed.add(row[0].lower())
    for row in conn.execute(
        "SELECT key FROM preference_weights "
        "WHERE dimension = 'author' AND weight >= ?",
        (FOLLOW_AUTHOR_WEIGHT,),
    ).fetchall():
        followed.add(row[0].lower())
    return followed


def _recent_feed_excludes(conn: sqlite3.Connection, days: int = 7) -> set[int]:
    rows = conn.execute(
        "SELECT DISTINCT signal_id FROM feed_interactions "
        "WHERE action IN ('save','dismiss') "
        "AND created_at > datetime('now', ?)",
        (f"-{days} days",),
    ).fetchall()
    return {r[0] for r in rows if r[0]}


def _feed_pool(conn: sqlite3.Connection) -> list[dict]:
    """Candidate pool for feed ranking.

    Feed should NOT inherit pairwise's 'recently compared' blacklist —
    that list shadows almost every X signal once the user has done
    meaningful pairwise rounds. Feed also skips the diversity cap, since
    the feed's own scoring (author/tag/source prefs) is the right way to
    rebalance — not a hard cap.
    """
    excl = _recent_feed_excludes(conn)
    return _get_candidate_pool(
        conn,
        extra_exclude_ids=excl,
        apply_pairwise_recent_filter=False,
        apply_diversity_cap=False,
    )


def rank_feed(conn: sqlite3.Connection, limit: int = 10, offset: int = 0) -> list[dict]:
    """Return signals ranked by feed_score desc, paged by limit/offset."""
    pool = _feed_pool(conn)
    pref_map = _load_pref_weights(conn)
    ranked = sorted(pool, key=lambda s: _score_signal(s, pref_map), reverse=True)
    return ranked[offset:offset + limit]


