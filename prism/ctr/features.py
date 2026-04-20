"""Feature extraction for the CTR ranker.

V1 features are intentionally lightweight — no embeddings, no LLM. Pure
numeric + one-hot over columns that already exist in SQLite. We'll add
text features once we have enough data to justify them.

Feature categories:
  * signal meta      — strength, layer, cluster size, recency, body/summary length
  * content meta     — has_summary_zh, tag count, author count, source count
  * source type      — one-hot over {x, youtube, hackernews, reddit, arxiv,
                        github_trending, github_releases, hn_search, other}
  * preference fit   — tag/author/source weight aggregates from preference_weights
  * engagement       — X favorite/retweet/reply counts when present
  * context          — served feed_score (lets XGB learn residual over heuristic)
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Iterable


_SOURCE_TYPES = [
    "x", "youtube", "hackernews", "reddit", "arxiv",
    "github_trending", "github_releases", "hn_search",
]
_LAYERS = ["actionable", "strategic", "noise"]


FEATURE_NAMES: list[str] = [
    "signal_strength",
    "item_count",
    "summary_len",
    "content_zh_len",
    "why_len",
    "has_content_zh",
    "tag_count",
    "author_count",
    "source_count",
    "recency_hours",
    *(f"layer_{L}" for L in _LAYERS),
    *(f"stype_{T}" for T in _SOURCE_TYPES),
    "stype_other",
    "pref_tag_max", "pref_tag_sum",
    "pref_author_max", "pref_author_sum",
    "pref_source_max", "pref_source_sum",
    "pref_layer",
    "eng_likes", "eng_retweets", "eng_replies",
    "bt_score",
    "feed_score",
]


def _load_pref_map(conn: sqlite3.Connection) -> dict[tuple[str, str], float]:
    rows = conn.execute(
        "SELECT dimension, key, weight FROM preference_weights"
    ).fetchall()
    return {(r["dimension"], r["key"]): r["weight"] for r in rows}


def _hours_since(ts: str | None, *, now: datetime | None = None) -> float:
    if not ts:
        return 9999.0
    now = now or datetime.now(timezone.utc)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            t = datetime.strptime(ts[:19], fmt).replace(tzinfo=timezone.utc)
            return max(0.0, (now - t).total_seconds() / 3600.0)
        except ValueError:
            continue
    return 9999.0


def _signal_row(conn: sqlite3.Connection, signal_id: int) -> dict | None:
    row = conn.execute(
        """SELECT s.id, s.cluster_id, s.summary, s.content_zh, s.signal_layer,
                  s.signal_strength, s.why_it_matters, s.tags_json,
                  s.created_at, c.item_count, c.date AS cluster_date
           FROM signals s JOIN clusters c ON c.id = s.cluster_id
           WHERE s.id = ?""",
        (signal_id,),
    ).fetchone()
    return dict(row) if row else None


def _signal_detail(conn: sqlite3.Connection, cluster_id: int) -> dict:
    rows = conn.execute(
        """SELECT ri.author, ri.raw_json, src.source_key, src.type AS stype
           FROM cluster_items ci
           JOIN raw_items ri ON ri.id = ci.raw_item_id
           JOIN sources src ON src.id = ri.source_id
           WHERE ci.cluster_id = ?""",
        (cluster_id,),
    ).fetchall()
    authors: set[str] = set()
    source_keys: set[str] = set()
    source_types: set[str] = set()
    likes = retweets = replies = 0
    for r in rows:
        if r["author"]:
            authors.add(r["author"])
        if r["source_key"]:
            source_keys.add(r["source_key"])
        if r["stype"]:
            source_types.add(r["stype"])
        if r["stype"] == "x" and r["raw_json"]:
            try:
                tw = (json.loads(r["raw_json"]) or {}).get("tweet") or {}
                likes = max(likes, int(tw.get("favorite_count") or 0))
                retweets = max(retweets, int(tw.get("retweet_count") or 0))
                replies = max(replies, int(tw.get("reply_count") or 0))
            except (ValueError, TypeError):
                pass
    return {
        "authors": list(authors),
        "source_keys": list(source_keys),
        "source_types": list(source_types),
        "eng_likes": likes,
        "eng_retweets": retweets,
        "eng_replies": replies,
    }


def _bt_score(conn: sqlite3.Connection, signal_id: int) -> float:
    row = conn.execute(
        "SELECT bt_score FROM signal_scores WHERE signal_id = ?",
        (signal_id,),
    ).fetchone()
    return float(row["bt_score"]) if row else 1500.0


def _pref_stats(
    pref_map: dict[tuple[str, str], float],
    dimension: str,
    keys: Iterable[str],
) -> tuple[float, float]:
    weights = [pref_map.get((dimension, k), 0.0) for k in keys]
    if not weights:
        return 0.0, 0.0
    return max(weights), sum(weights)


def extract(
    conn: sqlite3.Connection,
    signal_id: int,
    *,
    feed_score: float = 0.0,
    pref_map: dict[tuple[str, str], float] | None = None,
    now: datetime | None = None,
) -> dict[str, float]:
    """Return a feature dict keyed by FEATURE_NAMES for the given signal."""
    sig = _signal_row(conn, signal_id)
    if sig is None:
        return {n: 0.0 for n in FEATURE_NAMES}

    pref_map = pref_map if pref_map is not None else _load_pref_map(conn)
    detail = _signal_detail(conn, sig["cluster_id"])

    try:
        tags = json.loads(sig["tags_json"]) if sig["tags_json"] else []
    except (ValueError, TypeError):
        tags = []

    tag_max, tag_sum = _pref_stats(pref_map, "tag", tags)
    auth_max, auth_sum = _pref_stats(pref_map, "author", detail["authors"])
    src_max, src_sum = _pref_stats(pref_map, "source", detail["source_keys"])
    layer = sig.get("signal_layer") or ""
    pref_layer = pref_map.get(("layer", layer), 0.0)

    # Recency uses cluster.date when present (when content broke); falls
    # back to the signal's own created_at.
    recency_ts = sig.get("cluster_date") or sig.get("created_at")
    recency = _hours_since(recency_ts, now=now)

    feats: dict[str, float] = {
        "signal_strength": float(sig.get("signal_strength") or 0),
        "item_count": float(sig.get("item_count") or 0),
        "summary_len": float(len(sig.get("summary") or "")),
        "content_zh_len": float(len(sig.get("content_zh") or "")),
        "why_len": float(len(sig.get("why_it_matters") or "")),
        "has_content_zh": 1.0 if sig.get("content_zh") else 0.0,
        "tag_count": float(len(tags)),
        "author_count": float(len(detail["authors"])),
        "source_count": float(len(detail["source_keys"])),
        "recency_hours": recency,
        "pref_tag_max": tag_max, "pref_tag_sum": tag_sum,
        "pref_author_max": auth_max, "pref_author_sum": auth_sum,
        "pref_source_max": src_max, "pref_source_sum": src_sum,
        "pref_layer": pref_layer,
        "eng_likes": float(detail["eng_likes"]),
        "eng_retweets": float(detail["eng_retweets"]),
        "eng_replies": float(detail["eng_replies"]),
        "bt_score": _bt_score(conn, signal_id),
        "feed_score": float(feed_score),
    }

    for L in _LAYERS:
        feats[f"layer_{L}"] = 1.0 if layer == L else 0.0

    stypes = set(detail["source_types"])
    for T in _SOURCE_TYPES:
        feats[f"stype_{T}"] = 1.0 if T in stypes else 0.0
    feats["stype_other"] = 1.0 if stypes and not (stypes & set(_SOURCE_TYPES)) else 0.0

    # Ensure every declared feature is present (safety if we forgot one).
    for n in FEATURE_NAMES:
        feats.setdefault(n, 0.0)
    return feats
