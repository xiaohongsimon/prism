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
