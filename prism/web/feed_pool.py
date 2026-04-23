"""Candidate pool + preference map + external-feed helpers.

Extracted from the now-deleted `prism/web/pairwise.py` during Wave 1 cleanup
(2026-04-23). The old module mixed pool-building (still needed) with
Bradley-Terry scoring + pair selection (removed). This module keeps only
the pieces the feed route actually uses:

- `_get_candidate_pool` — cluster-resolved signal pool with tweet/engagement
  metadata baked in. Previously pulled `bt_score` from `signal_scores`;
  now returns pool rows without BT (the table is dropped in Wave 1).
- `_load_pref_weights` — read `preference_weights` into a dict.
- `process_external_feed` — upsert an externally-submitted URL.

All BT / source_weights / pairwise_comparisons queries have been removed.
The `apply_pairwise_recent_filter` parameter is gone — pairwise history no
longer exists to filter against.
"""
from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone

SIGNAL_MAX_AGE_DAYS = 7
PREF_BLOCK_THRESHOLD = -10.0  # sources/tags below this are excluded


def _load_pref_weights(conn: sqlite3.Connection) -> dict[tuple[str, str], float]:
    """Load preference weights as {(dimension, key): weight}."""
    rows = conn.execute(
        "SELECT dimension, key, weight FROM preference_weights"
    ).fetchall()
    return {(r["dimension"], r["key"]): r["weight"] for r in rows}


def _get_candidate_pool(
    conn: sqlite3.Connection,
    extra_exclude_ids: set[int] | None = None,
    apply_diversity_cap: bool = True,
    max_age_days: int | None = SIGNAL_MAX_AGE_DAYS,
) -> list[dict]:
    """Return current signals with per-cluster URL/engagement/tweet metadata.

    `max_age_days=None` disables the age cutoff (creator-profile page wants
    full history). `apply_diversity_cap=False` skips the source-type
    rebalancing (the feed does its own channel interleave in `feed.py`).
    """
    if max_age_days is not None:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max_age_days)
        ).strftime("%Y-%m-%dT%H:%M:%S")
    else:
        cutoff = ""  # sorts before any real ISO timestamp

    pref_map = _load_pref_weights(conn)
    blocked_sources = {
        k for (d, k), w in pref_map.items()
        if d == "source" and w <= PREF_BLOCK_THRESHOLD
    }
    blocked_tags = {
        k for (d, k), w in pref_map.items()
        if d == "tag" and w <= PREF_BLOCK_THRESHOLD
    }

    exclude_ids = set(extra_exclude_ids or ())

    signals = conn.execute(
        """SELECT DISTINCT s.id AS signal_id, s.cluster_id, s.summary, s.signal_layer,
                  s.signal_strength, s.why_it_matters, s.tags_json, s.created_at,
                  s.content_zh, c.topic_label, c.item_count
           FROM signals s
           JOIN clusters c ON s.cluster_id = c.id
           JOIN cluster_items ci ON ci.cluster_id = c.id
           JOIN raw_items ri ON ri.id = ci.raw_item_id
           WHERE s.is_current = 1
             AND COALESCE(NULLIF(ri.published_at, ''), s.created_at) >= ?
           ORDER BY s.created_at DESC""",
        (cutoff,),
    ).fetchall()

    pool = []
    for s in signals:
        if s["signal_id"] in exclude_ids:
            continue
        tags = []
        try:
            tags = json.loads(s["tags_json"]) if s["tags_json"] else []
        except (json.JSONDecodeError, TypeError):
            pass
        detail_rows = conn.execute(
            """SELECT ri.url, ri.author, ri.published_at, ri.raw_json,
                      src.source_key, src.type AS source_type,
                      COALESCE(ri.body_zh, ri.body) AS raw_body,
                      a.id AS article_id
               FROM cluster_items ci
               JOIN raw_items ri ON ri.id = ci.raw_item_id
               JOIN sources src ON src.id = ri.source_id
               LEFT JOIN articles a ON a.raw_item_id = ri.id
               WHERE ci.cluster_id = ?""",
            (s["cluster_id"],),
        ).fetchall()

        dr_source_keys = {dr["source_key"] for dr in detail_rows}
        if blocked_sources and dr_source_keys and dr_source_keys.issubset(blocked_sources):
            continue
        if blocked_tags and tags and all(t in blocked_tags for t in tags):
            continue

        urls: list[str] = []
        source_keys: list[str] = []
        authors: list[str] = []
        source_types: set[str] = set()
        published_at: str | None = None
        full_body = ""
        article_id: int | None = None
        _aggregator_domains = (
            "news.ycombinator.com", "twitter.com", "x.com", "xcancel.com",
        )
        for dr in detail_rows:
            if dr["raw_body"] and len(dr["raw_body"]) > len(full_body):
                full_body = dr["raw_body"]
            if dr["url"] and dr["url"].startswith("http") and dr["url"] not in urls:
                is_aggregator = any(d in dr["url"] for d in _aggregator_domains)
                if is_aggregator:
                    urls.append(dr["url"])
                else:
                    urls.insert(0, dr["url"])
            if dr["source_key"] not in source_keys:
                source_keys.append(dr["source_key"])
            if dr["author"] and dr["author"].strip() and dr["author"] not in authors:
                authors.append(dr["author"])
            source_types.add(dr["source_type"])
            if dr["published_at"] and (
                published_at is None or dr["published_at"] < published_at
            ):
                published_at = dr["published_at"]
            if article_id is None and dr["article_id"]:
                article_id = dr["article_id"]

        engagement: dict = {}
        tweet_text = ""
        tweet_avatar = tweet_name = tweet_handle = ""
        tweet_verified = False
        tweet_media: list[dict] = []
        quoted_tweet: dict = {}
        for dr in detail_rows:
            try:
                raw = json.loads(dr["raw_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            tweet = raw.get("tweet", {})
            if not tweet:
                continue
            user = tweet.get("user", {})
            if user:
                engagement = {
                    "likes": tweet.get("favorite_count", 0),
                    "retweets": tweet.get("retweet_count", 0),
                    "replies": tweet.get("reply_count", 0),
                    "quotes": tweet.get("quote_count", 0),
                }
                tweet_text = tweet.get("full_text", "") or tweet.get("text", "")
                tweet_avatar = user.get("profile_image_url_https", "").replace(
                    "_normal.", "_200x200."
                )
                tweet_name = user.get("name", "")
                tweet_handle = user.get("screen_name", "")
                tweet_verified = user.get("is_blue_verified", False)
                tweet_date = tweet.get("created_at", "")
                if tweet_date and (not published_at or published_at.startswith("2026")):
                    try:
                        from email.utils import parsedate_to_datetime
                        dt = parsedate_to_datetime(tweet_date)
                        published_at = dt.strftime("%Y-%m-%dT%H:%M:%S")
                    except Exception:
                        pass
            else:
                engagement = {
                    "likes": tweet.get("likes", 0),
                    "retweets": tweet.get("retweets", 0),
                    "replies": tweet.get("replies", 0),
                    "quotes": 0,
                }
                tweet_text = tweet.get("text", "")
                tweet_url = tweet.get("url", "")
                tweet_handle = (
                    tweet_url.split("x.com/")[-1].split("/")[0]
                    if "x.com/" in tweet_url else ""
                )
                tweet_name = raw.get("builder_name", tweet_handle)
                tweet_avatar = f"https://unavatar.io/x/{tweet_handle}" if tweet_handle else ""
                tweet_verified = False
                tweet_date = tweet.get("createdAt", "")
                if tweet_date and (not published_at or published_at.startswith("2026")):
                    published_at = tweet_date.replace("Z", "").split(".")[0]
            ext_media = (
                tweet.get("extended_entities", {}).get("media", [])
                if "extended_entities" in tweet else []
            )
            ent_media = tweet.get("entities", {}).get("media", [])
            for m in (ext_media or ent_media):
                murl = m.get("media_url_https", "")
                if murl:
                    tweet_media.append({"type": m.get("type", "photo"), "url": murl})
            qt = tweet.get("quoted_status", {})
            if qt:
                qt_user = qt.get("user", {})
                quoted_tweet = {
                    "name": qt_user.get("name", ""),
                    "handle": qt_user.get("screen_name", ""),
                    "avatar": qt_user.get("profile_image_url_https", "").replace(
                        "_normal.", "_200x200."
                    ),
                    "text": qt.get("full_text", "") or qt.get("text", ""),
                    "verified": qt_user.get("is_blue_verified", False),
                }
                if not tweet_media:
                    qt_media = qt.get("entities", {}).get("media", [])
                    for m in qt_media:
                        murl = m.get("media_url_https", "")
                        if murl:
                            tweet_media.append({"type": m.get("type", "photo"), "url": murl})
            break  # first tweet wins

        effective_date = published_at or s["created_at"] or ""
        if effective_date and effective_date < cutoff:
            continue

        row = {
            "signal_id": s["signal_id"],
            "cluster_id": s["cluster_id"],
            "topic_label": s["topic_label"],
            "summary": s["summary"],
            "why_it_matters": s["why_it_matters"] or "",
            "signal_layer": s["signal_layer"],
            "signal_strength": s["signal_strength"],
            "tags": tags,
            "item_count": s["item_count"],
            "created_at": s["created_at"],
            "published_at": published_at or s["created_at"],
            "urls": urls,
            "source_keys": source_keys,
            "authors": authors,
            "source_types": list(source_types),
            "is_video": "youtube" in source_types,
            "engagement": engagement,
            "tweet_text": tweet_text,
            "tweet_avatar": tweet_avatar,
            "tweet_name": tweet_name,
            "tweet_handle": tweet_handle,
            "tweet_verified": tweet_verified,
            "tweet_media": tweet_media,
            "quoted_tweet": quoted_tweet,
            "content_zh": s["content_zh"] or "",
            "full_body": full_body,
            "article_id": article_id,
            "card_avatar": "",
            "card_name": "",
            "card_channel": "",
        }
        # Build universal card header from best available data
        st_list = row["source_types"]
        if row["tweet_avatar"]:
            row["card_avatar"] = row["tweet_avatar"]
            row["card_name"] = row["tweet_name"]
            row["card_channel"] = "X"
        elif "github_trending" in st_list or "github_releases" in st_list:
            owner = ""
            for u in row["urls"]:
                if "github.com/" in u:
                    parts = u.split("github.com/")[-1].split("/")
                    if parts:
                        owner = parts[0]
                        break
            if owner:
                row["card_avatar"] = f"https://github.com/{owner}.png?size=80"
                row["card_name"] = row["authors"][0] if row["authors"] else owner
            row["card_channel"] = "GitHub"
        elif "youtube" in st_list:
            row["card_name"] = row["authors"][0] if row["authors"] else ""
            row["card_channel"] = "YouTube"
        elif "arxiv" in st_list:
            row["card_name"] = ", ".join(row["authors"][:2]) if row["authors"] else ""
            row["card_channel"] = "arXiv"
        elif "hn" in st_list:
            row["card_name"] = row["authors"][0] if row["authors"] else ""
            row["card_channel"] = "Hacker News"
        else:
            row["card_name"] = row["authors"][0] if row["authors"] else ""
            row["card_channel"] = row["source_keys"][0] if row["source_keys"] else ""

        pool.append(row)

    if pool and apply_diversity_cap:
        from collections import Counter
        for _ in range(3):  # iterate to stabilize
            type_counts: Counter = Counter()
            for p in pool:
                for st in p.get("source_types", []):
                    type_counts[st] += 1
            max_per_type = max(len(pool) // 3, 6)
            changed = False
            for dominant_type, cnt in type_counts.most_common():
                if cnt > max_per_type:
                    dominant = [p for p in pool if dominant_type in p.get("source_types", [])]
                    others = [p for p in pool if dominant_type not in p.get("source_types", [])]
                    random.shuffle(dominant)
                    pool = others + dominant[:max_per_type]
                    changed = True
            if not changed:
                break

    return pool


def process_external_feed(conn: sqlite3.Connection, url: str, note: str = "") -> None:
    """Upsert an externally-submitted URL as a strong positive-feedback seed.

    Kept from the pairwise era — the `external_feeds` table is still wired
    into the pipeline (cron picks up `processed = 0` rows). Upsert behavior
    preserved so re-submitting the same URL refreshes `user_note` and
    re-queues for processing.
    """
    existing = conn.execute(
        "SELECT id FROM external_feeds WHERE url = ?", (url,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE external_feeds SET user_note = ?, processed = 0 WHERE url = ?",
            (note, url),
        )
    else:
        conn.execute(
            "INSERT INTO external_feeds (url, user_note) VALUES (?, ?)",
            (url, note),
        )
    conn.commit()
