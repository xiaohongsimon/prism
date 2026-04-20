"""Pairwise recommendation engine: BT scoring, pair selection, preference updates."""

import json
import random
import sqlite3
from datetime import datetime, timezone, timedelta

# --- Constants ---

K = 48  # BT learning rate
BT_INITIAL = 1500.0
RECENT_PAIR_LIMIT = 50  # exclude signals seen in last N pairs
SIGNAL_MAX_AGE_DAYS = 7
PAIR_STRATEGY_WEIGHTS = {"exploit": 0.7, "explore": 0.2, "random": 0.1}
NEITHER_BREAK_THRESHOLD = 3

# Pairwise feedback deltas for preference_weights
WINNER_DELTA = 1.0
LOSER_DELTA = -0.5
BOTH_DELTA = 0.3
NEITHER_DELTA = -0.8
EXTERNAL_FEED_DELTA = 3.0


# --- Bradley-Terry Scoring ---

def update_bt_scores(score_a: float, score_b: float, winner: str) -> tuple[float, float]:
    """Return updated (new_a, new_b) after a pairwise comparison."""
    if winner not in ("a", "b", "both"):
        return score_a, score_b

    expected_a = 1.0 / (1.0 + 10 ** ((score_b - score_a) / 400))
    expected_b = 1.0 - expected_a

    if winner == "a":
        actual_a, actual_b = 1.0, 0.0
    elif winner == "b":
        actual_a, actual_b = 0.0, 1.0
    else:  # both
        actual_a, actual_b = 0.5, 0.5

    new_a = score_a + K * (actual_a - expected_a)
    new_b = score_b + K * (actual_b - expected_b)
    return new_a, new_b


def _ensure_signal_score(conn: sqlite3.Connection, signal_id: int) -> float:
    """Ensure signal has a bt_score row; return current score."""
    row = conn.execute("SELECT bt_score FROM signal_scores WHERE signal_id = ?", (signal_id,)).fetchone()
    if row:
        return row["bt_score"]
    conn.execute(
        "INSERT INTO signal_scores (signal_id, bt_score) VALUES (?, ?)",
        (signal_id, BT_INITIAL),
    )
    return BT_INITIAL


# --- Pair Selection ---

PREF_BLOCK_THRESHOLD = -10.0  # sources/tags below this are excluded from pairwise pool


def _load_pref_weights(conn: sqlite3.Connection) -> dict[tuple[str, str], float]:
    """Load preference weights as {(dimension, key): weight}."""
    rows = conn.execute("SELECT dimension, key, weight FROM preference_weights").fetchall()
    return {(r["dimension"], r["key"]): r["weight"] for r in rows}


def _get_candidate_pool(
    conn: sqlite3.Connection,
    extra_exclude_ids: set[int] | None = None,
) -> list[dict]:
    """Get signals eligible for pairwise comparison."""
    # Content must be published within SIGNAL_MAX_AGE_DAYS (not just synced recently)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SIGNAL_MAX_AGE_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")

    # Load preference weights for filtering
    pref_map = _load_pref_weights(conn)
    blocked_sources = {k for (d, k), w in pref_map.items() if d == "source" and w <= PREF_BLOCK_THRESHOLD}
    blocked_tags = {k for (d, k), w in pref_map.items() if d == "tag" and w <= PREF_BLOCK_THRESHOLD}

    # Get recently shown signal IDs
    recent_ids = set()
    rows = conn.execute(
        "SELECT signal_a_id, signal_b_id FROM pairwise_comparisons "
        "ORDER BY id DESC LIMIT ?",
        (RECENT_PAIR_LIMIT,),
    ).fetchall()
    for r in rows:
        recent_ids.add(r["signal_a_id"])
        recent_ids.add(r["signal_b_id"])

    if extra_exclude_ids:
        recent_ids = recent_ids | set(extra_exclude_ids)

    # Get current signals — filter by actual content publish date, not signal creation
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
        if s["signal_id"] in recent_ids:
            continue
        score_row = conn.execute(
            "SELECT bt_score, comparison_count FROM signal_scores WHERE signal_id = ?",
            (s["signal_id"],),
        ).fetchone()
        bt_score = score_row["bt_score"] if score_row else BT_INITIAL
        comp_count = score_row["comparison_count"] if score_row else 0
        tags = []
        try:
            tags = json.loads(s["tags_json"]) if s["tags_json"] else []
        except (json.JSONDecodeError, TypeError):
            pass
        # Get URLs, source_keys, authors, source_types, published_at, raw_json for this signal
        detail_rows = conn.execute(
            """SELECT ri.url, ri.author, ri.published_at, ri.raw_json,
                      src.source_key, src.type AS source_type,
                      COALESCE(ri.body_zh, ri.body) AS raw_body
               FROM cluster_items ci
               JOIN raw_items ri ON ri.id = ci.raw_item_id
               JOIN sources src ON src.id = ri.source_id
               WHERE ci.cluster_id = ?""",
            (s["cluster_id"],),
        ).fetchall()
        # Skip signals from blocked sources (learned from pairwise history)
        dr_source_keys = {dr["source_key"] for dr in detail_rows}
        if blocked_sources and dr_source_keys and dr_source_keys.issubset(blocked_sources):
            continue
        # Skip signals with only blocked tags
        if blocked_tags and tags and all(t in blocked_tags for t in tags):
            continue

        urls = []
        source_keys = []
        authors = []
        source_types = set()
        published_at = None
        full_body = ""
        _aggregator_domains = ("news.ycombinator.com", "twitter.com", "x.com", "xcancel.com")
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
            # Use earliest published_at as the real content date
            if dr["published_at"] and (published_at is None or dr["published_at"] < published_at):
                published_at = dr["published_at"]

        # Extract engagement metrics, avatar, media from raw_json (any source with tweet data)
        engagement = {}
        for dr in detail_rows:
            try:
                raw = json.loads(dr["raw_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            tweet = raw.get("tweet", {})
            if tweet:
                # Two tweet formats: syndication API (has user obj) vs follow-builders (flat)
                user = tweet.get("user", {})
                if user:
                    # Syndication API format
                    engagement = {
                        "likes": tweet.get("favorite_count", 0),
                        "retweets": tweet.get("retweet_count", 0),
                        "replies": tweet.get("reply_count", 0),
                        "quotes": tweet.get("quote_count", 0),
                    }
                    tweet_text = tweet.get("full_text", "") or tweet.get("text", "")
                    tweet_avatar = user.get("profile_image_url_https", "").replace("_normal.", "_200x200.")
                    tweet_name = user.get("name", "")
                    tweet_handle = user.get("screen_name", "")
                    tweet_verified = user.get("is_blue_verified", False)
                    # Fix published_at
                    tweet_date = tweet.get("created_at", "")
                    if tweet_date and (not published_at or published_at.startswith("2026")):
                        try:
                            from email.utils import parsedate_to_datetime
                            dt = parsedate_to_datetime(tweet_date)
                            published_at = dt.strftime("%Y-%m-%dT%H:%M:%S")
                        except Exception:
                            pass
                else:
                    # Follow-builders flat format
                    engagement = {
                        "likes": tweet.get("likes", 0),
                        "retweets": tweet.get("retweets", 0),
                        "replies": tweet.get("replies", 0),
                        "quotes": 0,
                    }
                    tweet_text = tweet.get("text", "")
                    # Extract handle from tweet URL
                    tweet_url = tweet.get("url", "")
                    tweet_handle = tweet_url.split("x.com/")[-1].split("/")[0] if "x.com/" in tweet_url else ""
                    tweet_name = raw.get("builder_name", tweet_handle)
                    tweet_avatar = f"https://unavatar.io/x/{tweet_handle}" if tweet_handle else ""
                    tweet_verified = False
                    # Fix published_at
                    tweet_date = tweet.get("createdAt", "")
                    if tweet_date and (not published_at or published_at.startswith("2026")):
                        published_at = tweet_date.replace("Z", "").split(".")[0]
                # Media images
                tweet_media = []
                ext_media = tweet.get("extended_entities", {}).get("media", []) if "extended_entities" in tweet else []
                ent_media = tweet.get("entities", {}).get("media", [])
                for m in (ext_media or ent_media):
                    murl = m.get("media_url_https", "")
                    if murl:
                        tweet_media.append({"type": m.get("type", "photo"), "url": murl})
                # Check quoted tweet
                qt = tweet.get("quoted_status", {})
                quoted_tweet = {}
                if qt:
                    qt_user = qt.get("user", {})
                    quoted_tweet = {
                        "name": qt_user.get("name", ""),
                        "handle": qt_user.get("screen_name", ""),
                        "avatar": qt_user.get("profile_image_url_https", "").replace("_normal.", "_200x200."),
                        "text": qt.get("full_text", "") or qt.get("text", ""),
                        "verified": qt_user.get("is_blue_verified", False),
                    }
                    if not tweet_media:
                        qt_media = qt.get("entities", {}).get("media", [])
                        for m in qt_media:
                            murl = m.get("media_url_https", "")
                            if murl:
                                tweet_media.append({"type": m.get("type", "photo"), "url": murl})
                break  # use first tweet's data
            else:
                tweet_text = ""
                tweet_avatar = tweet_name = tweet_handle = ""
                tweet_verified = False
                tweet_media = []
                quoted_tweet = {}
        else:
            tweet_text = ""
            tweet_avatar = tweet_name = tweet_handle = ""
            tweet_verified = False
            tweet_media = []
            quoted_tweet = {}

        # Filter out old content — skip if published before cutoff
        effective_date = published_at or s["created_at"] or ""
        if effective_date and effective_date < cutoff:
            continue

        pool.append({
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
            "published_at": published_at or s["created_at"],  # real content date
            "bt_score": bt_score,
            "comparison_count": comp_count,
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
            # Universal card header
            "card_avatar": "",
            "card_name": "",
            "card_channel": "",
        })
        # Build universal card header from best available data
        p = pool[-1]
        st_list = p["source_types"]
        if p["tweet_avatar"]:
            p["card_avatar"] = p["tweet_avatar"]
            p["card_name"] = p["tweet_name"]
            p["card_channel"] = "X"
        elif "github_trending" in st_list or "github_releases" in st_list:
            owner = ""
            for u in p["urls"]:
                if "github.com/" in u:
                    parts = u.split("github.com/")[-1].split("/")
                    if parts:
                        owner = parts[0]
                        break
            if owner:
                p["card_avatar"] = f"https://github.com/{owner}.png?size=80"
                p["card_name"] = p["authors"][0] if p["authors"] else owner
            p["card_channel"] = "GitHub"
        elif "youtube" in st_list:
            p["card_name"] = p["authors"][0] if p["authors"] else ""
            p["card_channel"] = "YouTube"
        elif "arxiv" in st_list:
            p["card_name"] = ", ".join(p["authors"][:2]) if p["authors"] else ""
            p["card_channel"] = "arXiv"
        elif "hn" in st_list:
            p["card_name"] = p["authors"][0] if p["authors"] else ""
            p["card_channel"] = "Hacker News"
        else:
            p["card_name"] = p["authors"][0] if p["authors"] else ""
            p["card_channel"] = p["source_keys"][0] if p["source_keys"] else ""

    # Cap any single source type to ensure diversity — repeat until stable
    if pool:
        from collections import Counter
        for _ in range(3):  # iterate to stabilize
            type_counts = Counter()
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


def _check_neither_streak(conn: sqlite3.Connection) -> bool:
    """Return True if last N comparisons were all 'neither'."""
    rows = conn.execute(
        "SELECT winner FROM pairwise_comparisons ORDER BY id DESC LIMIT ?",
        (NEITHER_BREAK_THRESHOLD,),
    ).fetchall()
    if len(rows) < NEITHER_BREAK_THRESHOLD:
        return False
    return all(r["winner"] == "neither" for r in rows)


def select_pair(conn: sqlite3.Connection) -> tuple[dict, dict, str] | None:
    """Select next pair for comparison. Returns (signal_a, signal_b, strategy) or None."""
    pool = _get_candidate_pool(conn)
    if len(pool) < 2:
        return None

    # Force random if neither streak
    if _check_neither_streak(conn):
        chosen = random.sample(pool, 2)
        return chosen[0], chosen[1], "neither_fallback"

    # Pick strategy
    r = random.random()
    if r < PAIR_STRATEGY_WEIGHTS["exploit"]:
        a, b = _pick_exploit(pool)
        return a, b, "exploit"
    elif r < PAIR_STRATEGY_WEIGHTS["exploit"] + PAIR_STRATEGY_WEIGHTS["explore"]:
        a, b = _pick_explore(pool)
        return a, b, "explore"
    else:
        a, b = _pick_random(pool)
        return a, b, "random"


def _pick_exploit(pool: list[dict]) -> tuple[dict, dict]:
    """One high-score signal + one low-comparison signal, different topics."""
    # Prefer high signal_strength (LLM quality score) alongside bt_score
    sorted_by_score = sorted(pool, key=lambda x: (x["bt_score"] + x.get("signal_strength", 0) * 50), reverse=True)
    top_n = max(1, len(sorted_by_score) * 30 // 100)
    high = random.choice(sorted_by_score[:top_n])

    # Find lowest comparison_count signal with different topic
    candidates = sorted(
        [s for s in pool if s["signal_id"] != high["signal_id"] and s["topic_label"] != high["topic_label"]],
        key=lambda x: x["comparison_count"],
    )
    if not candidates:
        # Fallback: allow same topic
        candidates = [s for s in pool if s["signal_id"] != high["signal_id"]]
    if not candidates:
        return pool[0], pool[1]
    low = candidates[0]
    return high, low


def _pick_explore(pool: list[dict]) -> tuple[dict, dict]:
    """Two signals with low comparison count."""
    new_signals = [s for s in pool if s["comparison_count"] < 3]
    if len(new_signals) >= 2:
        chosen = random.sample(new_signals, 2)
        return chosen[0], chosen[1]
    # Fallback to random
    return _pick_random(pool)


def _pick_random(pool: list[dict]) -> tuple[dict, dict]:
    """Completely random pair."""
    chosen = random.sample(pool, 2)
    return chosen[0], chosen[1]


# --- Record Vote ---

def _get_signal_source_keys(conn: sqlite3.Connection, signal_id: int) -> list[str]:
    """Get source keys for a signal's cluster."""
    rows = conn.execute(
        """SELECT DISTINCT src.source_key
           FROM signals s
           JOIN cluster_items ci ON ci.cluster_id = s.cluster_id
           JOIN raw_items ri ON ri.id = ci.raw_item_id
           JOIN sources src ON src.id = ri.source_id
           WHERE s.id = ?""",
        (signal_id,),
    ).fetchall()
    return [r["source_key"] for r in rows]


def _get_signal_meta(conn: sqlite3.Connection, signal_id: int) -> dict:
    """Get tags, layer, authors for a signal."""
    row = conn.execute(
        "SELECT signal_layer, tags_json FROM signals WHERE id = ?", (signal_id,)
    ).fetchone()
    if not row:
        return {"tags": [], "layer": "", "authors": []}
    tags = []
    try:
        tags = json.loads(row["tags_json"]) if row["tags_json"] else []
    except (json.JSONDecodeError, TypeError):
        pass
    # Get authors
    authors = []
    author_rows = conn.execute(
        """SELECT DISTINCT ri.author FROM signals s
           JOIN cluster_items ci ON ci.cluster_id = s.cluster_id
           JOIN raw_items ri ON ri.id = ci.raw_item_id
           WHERE s.id = ? AND ri.author != ''""",
        (signal_id,),
    ).fetchall()
    authors = [r["author"] for r in author_rows]
    return {"tags": tags, "layer": row["signal_layer"], "authors": authors}


def _update_preference_weights(conn: sqlite3.Connection, signal_id: int, delta: float):
    """Update preference_weights for all dimensions of a signal."""
    meta = _get_signal_meta(conn, signal_id)
    source_keys = _get_signal_source_keys(conn, signal_id)

    keys_to_update = []
    keys_to_update.append(("layer", meta["layer"]))
    for tag in meta["tags"]:
        keys_to_update.append(("tag", tag))
    for sk in source_keys:
        keys_to_update.append(("source", sk))
    for author in meta["authors"]:
        keys_to_update.append(("author", author))

    for dimension, key in keys_to_update:
        existing = conn.execute(
            "SELECT weight FROM preference_weights WHERE dimension = ? AND key = ?",
            (dimension, key),
        ).fetchone()
        new_weight = (existing["weight"] if existing else 0.0) + delta
        conn.execute(
            "INSERT OR REPLACE INTO preference_weights (dimension, key, weight, updated_at) "
            "VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%S', 'now'))",
            (dimension, key, new_weight),
        )


def _update_source_weights(conn: sqlite3.Connection, signal_id: int, won: bool):
    """Update source_weights win_rate and total_comparisons for a signal's sources."""
    source_keys = _get_signal_source_keys(conn, signal_id)
    for sk in source_keys:
        row = conn.execute("SELECT * FROM source_weights WHERE source_key = ?", (sk,)).fetchone()
        if row:
            new_total = row["total_comparisons"] + 1
            new_wins = (row["win_rate"] * row["total_comparisons"] + (1 if won else 0))
            new_rate = new_wins / new_total
            conn.execute(
                "UPDATE source_weights SET win_rate = ?, total_comparisons = ?, "
                "updated_at = datetime('now') WHERE source_key = ?",
                (new_rate, new_total, sk),
            )
        else:
            conn.execute(
                "INSERT INTO source_weights (source_key, weight, win_rate, total_comparisons) "
                "VALUES (?, 1.0, ?, 1)",
                (sk, 1.0 if won else 0.0),
            )


def record_vote(
    conn: sqlite3.Connection,
    signal_a_id: int,
    signal_b_id: int,
    winner: str,
    comment: str = "",
    response_time_ms: int = 0,
    strategy: str = "exploit",
) -> None:
    """Record a pairwise vote and update all dependent scores."""
    # Record comparison
    conn.execute(
        "INSERT INTO pairwise_comparisons "
        "(signal_a_id, signal_b_id, winner, user_comment, pair_strategy, response_time_ms) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (signal_a_id, signal_b_id, winner, comment, strategy, response_time_ms),
    )

    # Update BT scores
    score_a = _ensure_signal_score(conn, signal_a_id)
    score_b = _ensure_signal_score(conn, signal_b_id)
    new_a, new_b = update_bt_scores(score_a, score_b, winner)

    conn.execute(
        "UPDATE signal_scores SET bt_score = ?, comparison_count = comparison_count + 1, "
        "win_count = win_count + CASE WHEN ? IN ('a') THEN 1 ELSE 0 END, "
        "updated_at = datetime('now') WHERE signal_id = ?",
        (new_a, winner, signal_a_id),
    )
    conn.execute(
        "UPDATE signal_scores SET bt_score = ?, comparison_count = comparison_count + 1, "
        "win_count = win_count + CASE WHEN ? IN ('b') THEN 1 ELSE 0 END, "
        "updated_at = datetime('now') WHERE signal_id = ?",
        (new_b, winner, signal_b_id),
    )

    # Update preference weights
    if winner == "a":
        _update_preference_weights(conn, signal_a_id, WINNER_DELTA)
        _update_preference_weights(conn, signal_b_id, LOSER_DELTA)
    elif winner == "b":
        _update_preference_weights(conn, signal_b_id, WINNER_DELTA)
        _update_preference_weights(conn, signal_a_id, LOSER_DELTA)
    elif winner == "both":
        _update_preference_weights(conn, signal_a_id, BOTH_DELTA)
        _update_preference_weights(conn, signal_b_id, BOTH_DELTA)
    elif winner == "neither":
        _update_preference_weights(conn, signal_a_id, NEITHER_DELTA)
        _update_preference_weights(conn, signal_b_id, NEITHER_DELTA)

    # Update source weights
    if winner in ("a", "b"):
        winner_id = signal_a_id if winner == "a" else signal_b_id
        loser_id = signal_b_id if winner == "a" else signal_a_id
        _update_source_weights(conn, winner_id, won=True)
        _update_source_weights(conn, loser_id, won=False)
    elif winner in ("both", "neither"):
        _update_source_weights(conn, signal_a_id, won=(winner == "both"))
        _update_source_weights(conn, signal_b_id, won=(winner == "both"))

    conn.commit()


# --- External Feed ---

def process_external_feed(conn: sqlite3.Connection, url: str, note: str = "") -> None:
    """Process an externally provided URL/topic as strong positive feedback."""
    # Upsert: insert or update note
    existing = conn.execute("SELECT id FROM external_feeds WHERE url = ?", (url,)).fetchone()
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


# --- Source Weight Adjustment (daily cron) ---

def adjust_source_weights(conn: sqlite3.Connection) -> None:
    """Daily job: adjust source weights based on pairwise win rates."""
    rows = conn.execute("SELECT * FROM source_weights").fetchall()
    for sw in rows:
        if sw["total_comparisons"] < 10:
            continue

        old_weight = sw["weight"]
        if sw["win_rate"] > 0.6:
            new_weight = min(old_weight + 0.2, 3.0)
        elif sw["win_rate"] < 0.3:
            new_weight = max(old_weight - 0.2, 0.1)
        else:
            new_weight = old_weight

        if new_weight != old_weight:
            conn.execute(
                "UPDATE source_weights SET weight = ?, updated_at = datetime('now') "
                "WHERE source_key = ?",
                (new_weight, sw["source_key"]),
            )
            log_decision(
                conn, "recall", "adjust_source_weight",
                f"{sw['source_key']}: {old_weight:.2f} -> {new_weight:.2f}, "
                f"win_rate={sw['win_rate']:.2f}",
                {"old_weight": old_weight, "new_weight": new_weight,
                 "win_rate": sw["win_rate"]},
            )
    conn.commit()


# --- Decision Log ---

def log_decision(conn: sqlite3.Connection, layer: str, action: str,
                 reason: str, context: dict | None = None) -> None:
    """Record an automated decision for audit trail."""
    conn.execute(
        "INSERT INTO decision_log (layer, action, reason, context_json) VALUES (?, ?, ?, ?)",
        (layer, action, reason, json.dumps(context or {}, ensure_ascii=False)),
    )


# --- Pairwise History ---

def get_pairwise_history(conn: sqlite3.Connection, page: int = 1, per_page: int = 20) -> list[dict]:
    """Get pairwise comparison history for the history tab."""
    offset = (page - 1) * per_page
    rows = conn.execute(
        """SELECT pc.*,
                  sa.summary AS summary_a, ca.topic_label AS topic_a,
                  sb.summary AS summary_b, cb.topic_label AS topic_b
           FROM pairwise_comparisons pc
           JOIN signals sa ON sa.id = pc.signal_a_id
           JOIN clusters ca ON ca.id = sa.cluster_id
           JOIN signals sb ON sb.id = pc.signal_b_id
           JOIN clusters cb ON cb.id = sb.cluster_id
           ORDER BY pc.id DESC
           LIMIT ? OFFSET ?""",
        (per_page, offset),
    ).fetchall()
    return [dict(r) for r in rows]
