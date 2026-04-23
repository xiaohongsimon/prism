"""Feed-first interaction: save / dismiss / follow / mute.

Explicit multi-dimensional feedback over the subscription stream. Writes
to `feed_interactions` (raw event log) and `preference_weights` (the
ranking-visible dimension weights). As of Wave 1 (2026-04-23) this module
no longer touches `signal_scores` or `source_weights` — BT/pairwise
scoring has been removed.
"""
from __future__ import annotations

import json
import re
import sqlite3


_MD_STRIP_RE = re.compile(r'(\*\*|__|[*_`~#>]|\[([^\]]+)\]\([^)]+\))')
_SENT_SPLIT_RE = re.compile(r'[。！？!?\n]')


def compress_headline(text: str, max_len: int = 50) -> str:
    """Condense a long summary into a one-glance feed headline.

    - Strip common markdown markers and link wrappers (keep link text).
    - Take the first sentence (split on 。！？!? or newline).
    - Hard-truncate to max_len chars with ellipsis if still too long.
    """
    if not text:
        return ""
    # Replace markdown links [text](url) with just the text, then strip other markers.
    t = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    t = re.sub(r'[*_`~#>]+', '', t)
    # First sentence.
    head = _SENT_SPLIT_RE.split(t, maxsplit=1)[0].strip()
    if not head:
        head = t.strip()
    if len(head) <= max_len:
        return head
    return head[:max_len].rstrip() + '…'

# Preference-weights deltas by feed action.
ACTION_WEIGHT_DELTA = {
    "save": 2.0,
    "dismiss": -1.0,
}

# Author/tag deltas for dedicated follow / mute actions (only that one dimension).
FOLLOW_AUTHOR_WEIGHT = 3.0
MUTE_TOPIC_WEIGHT = -2.0


def _get_signal_dimensions(conn: sqlite3.Connection, signal_id: int) -> list[tuple[str, str]]:
    """Collect (dimension, key) pairs for every preference-bearing facet of
    a signal: its layer, tags, source_keys, authors. Returned as a flat
    list suitable for bulk upsert into `preference_weights`.
    """
    row = conn.execute(
        "SELECT signal_layer, tags_json FROM signals WHERE id = ?", (signal_id,)
    ).fetchone()
    if not row:
        return []
    tags: list[str] = []
    try:
        tags = json.loads(row["tags_json"]) if row["tags_json"] else []
    except (json.JSONDecodeError, TypeError):
        pass

    source_keys = [
        r["source_key"] for r in conn.execute(
            """SELECT DISTINCT src.source_key
               FROM signals s
               JOIN cluster_items ci ON ci.cluster_id = s.cluster_id
               JOIN raw_items ri ON ri.id = ci.raw_item_id
               JOIN sources src ON src.id = ri.source_id
               WHERE s.id = ?""",
            (signal_id,),
        ).fetchall()
    ]
    authors = [
        r["author"] for r in conn.execute(
            """SELECT DISTINCT ri.author FROM signals s
               JOIN cluster_items ci ON ci.cluster_id = s.cluster_id
               JOIN raw_items ri ON ri.id = ci.raw_item_id
               WHERE s.id = ? AND ri.author != ''""",
            (signal_id,),
        ).fetchall()
    ]

    pairs: list[tuple[str, str]] = []
    if row["signal_layer"]:
        pairs.append(("layer", row["signal_layer"]))
    pairs.extend(("tag", t) for t in tags)
    pairs.extend(("source", sk) for sk in source_keys)
    pairs.extend(("author", a) for a in authors)
    return pairs


def _bump_preference_weights(
    conn: sqlite3.Connection, signal_id: int, delta: float,
) -> None:
    """Add `delta` to every preference dimension belonging to `signal_id`."""
    for dimension, key in _get_signal_dimensions(conn, signal_id):
        existing = conn.execute(
            "SELECT weight FROM preference_weights WHERE dimension = ? AND key = ?",
            (dimension, key),
        ).fetchone()
        new_weight = (existing["weight"] if existing else 0.0) + delta
        conn.execute(
            "INSERT OR REPLACE INTO preference_weights "
            "(dimension, key, weight, updated_at) "
            "VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%S', 'now'))",
            (dimension, key, new_weight),
        )


def record_feed_action(
    conn: sqlite3.Connection,
    *,
    signal_id: int,
    action: str,
    target_key: str = "",
    response_time_ms: int = 0,
    context: dict | None = None,
) -> int:
    """Record a feed interaction and update learning state.

    - save / dismiss → delta across all preference dimensions of the signal.
    - follow_author / unfollow_author → single author-dimension weight.
    - mute_topic / unmute_topic → single tag-dimension weight.

    Returns the `feed_interactions.id` of the row just inserted.
    """
    cur = conn.execute(
        "INSERT INTO feed_interactions "
        "(signal_id, action, target_key, response_time_ms, context_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (signal_id, action, target_key, response_time_ms,
         json.dumps(context or {}, ensure_ascii=False)),
    )
    interaction_id = cur.lastrowid

    if action in ("save", "dismiss"):
        _bump_preference_weights(conn, signal_id, ACTION_WEIGHT_DELTA[action])
    elif action == "follow_author" and target_key:
        _set_weight(conn, "author", target_key, FOLLOW_AUTHOR_WEIGHT)
    elif action == "unfollow_author" and target_key:
        _set_weight(conn, "author", target_key, 0.0)
    elif action == "mute_topic" and target_key:
        _set_weight(conn, "tag", target_key, MUTE_TOPIC_WEIGHT)
    elif action == "unmute_topic" and target_key:
        _set_weight(conn, "tag", target_key, 0.0)

    conn.commit()
    return int(interaction_id) if interaction_id is not None else 0


def _set_weight(conn: sqlite3.Connection, dimension: str, key: str, weight: float) -> None:
    conn.execute(
        "INSERT INTO preference_weights (dimension, key, weight, updated_at) "
        "VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%S','now')) "
        "ON CONFLICT(dimension, key) DO UPDATE SET "
        "weight = excluded.weight, updated_at = excluded.updated_at",
        (dimension, key, weight),
    )


from prism.web.feed_pool import _get_candidate_pool, _load_pref_weights


_DIMENSION_WEIGHT = {
    "author": 1.0,
    "tag": 0.6,
    "source": 0.8,
    "layer": 0.4,
}


def _score_signal(signal: dict, pref_map: dict[tuple[str, str], float]) -> float:
    # signal_strength is the LLM-emitted quality integer (1..5). Post-Wave 1
    # there's no BT component — pure heuristic over preference dimensions
    # keyed off the signal's own metadata.
    score = signal.get("signal_strength", 0) * 10.0

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

    Skips the default diversity cap — the feed does its own channel
    interleave below (`_diversify_by_channel`), which is the right place
    to rebalance.
    """
    excl = _recent_feed_excludes(conn)
    return _get_candidate_pool(
        conn,
        extra_exclude_ids=excl,
        apply_diversity_cap=False,
    )


# Channel diversity cap: in any trailing window of FEED_DIVERSITY_WINDOW
# items, at most FEED_DIVERSITY_MAX_PER_TYPE come from the same
# source_type. Prevents YouTube (or any one channel) from filling a full
# screen when its signals happen to dominate raw score.
FEED_DIVERSITY_WINDOW = 5
FEED_DIVERSITY_MAX_PER_TYPE = 2


def _primary_type(signal: dict) -> str:
    types = signal.get("source_types") or []
    return types[0] if types else ""


def _diversify_by_channel(
    ranked: list[dict],
    window: int = FEED_DIVERSITY_WINDOW,
    max_per_type: int = FEED_DIVERSITY_MAX_PER_TYPE,
) -> list[dict]:
    """Balanced-greedy interleave.

    Bucket remaining items by source_type (preserving score order
    within each bucket). At each step pick from the bucket whose head
    fits in the trailing window AND which has the LARGEST remaining —
    so usage stays balanced throughout and we don't end up with a
    monochrome tail. If no bucket fits (pool too skewed for this
    stretch), fall back to the bucket with the most remaining.
    """
    buckets: dict[str, list[dict]] = {}
    for s in ranked:
        buckets.setdefault(_primary_type(s), []).append(s)

    result: list[dict] = []
    remaining_total = len(ranked)
    while remaining_total > 0:
        trailing = result[-(window - 1):] if window > 1 else []
        trailing_count: dict[str, int] = {}
        for r in trailing:
            t = _primary_type(r)
            trailing_count[t] = trailing_count.get(t, 0) + 1

        fitters = [
            t for t, b in buckets.items()
            if b and trailing_count.get(t, 0) < max_per_type
        ]
        if fitters:
            # Balance tail: prefer the type with the largest remaining.
            pick_type = max(fitters, key=lambda t: len(buckets[t]))
        else:
            # Pool too skewed — pick whichever still has items.
            pick_type = max(
                (t for t, b in buckets.items() if b),
                key=lambda t: len(buckets[t]),
            )
        result.append(buckets[pick_type].pop(0))
        remaining_total -= 1
    return result


def rank_feed(conn: sqlite3.Connection, limit: int = 10, offset: int = 0) -> list[dict]:
    """Return signals ranked by feed_score desc with channel-diversity
    interleaving, then paged by limit/offset.

    Each returned signal carries a 'feed_score' key — the heuristic score
    used for ordering. Impression logging stores this so the CTR model
    can learn a residual over the current heuristic.

    Diversify BEFORE paging so offset/limit slices through a stable
    interleaved list — consecutive pages remain consistent.
    """
    pool = _feed_pool(conn)
    pref_map = _load_pref_weights(conn)
    scored = [(s, _score_signal(s, pref_map)) for s in pool]
    scored.sort(key=lambda x: x[1], reverse=True)
    for s, sc in scored:
        s["feed_score"] = sc
    ranked = [s for s, _ in scored]
    diversified = _diversify_by_channel(ranked)
    return diversified[offset:offset + limit]


