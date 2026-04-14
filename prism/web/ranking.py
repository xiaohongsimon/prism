"""Feed ranking engine: heat + preference + time decay + Bradley-Terry."""

import json
import math
import sqlite3
from datetime import datetime, timezone


# Score weights per tab: (heat, preference, decay, bt)
TAB_WEIGHTS = {
    "recommend": (0.4, 0.4, 0.2, 0.0),
    "follow":    (0.2, 0.5, 0.3, 0.0),
    "hot":       (0.3, 0.0, 0.3, 0.4),
}

HALF_LIFE_HOURS = 24.0

# Feedback deltas per action
ACTION_DELTAS = {"like": 1.0, "dislike": -1.0, "save": 2.0}

# Preference weight at or below this threshold means hard-block:
# the item is completely excluded from the feed, not just down-weighted.
BLOCK_THRESHOLD = -3.0

# "Follow" tab source types: personal/curated channels (not aggregators)
FOLLOW_SOURCE_TYPES = {"x", "youtube", "follow_builders", "github_releases"}


def _time_decay(published_at: str | None) -> float:
    """Exponential decay based on age in hours."""
    if not published_at:
        return 0.5
    try:
        pub = datetime.fromisoformat(published_at)
    except (ValueError, TypeError):
        return 0.5
    now = datetime.now(timezone.utc)
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    age_hours = max((now - pub).total_seconds() / 3600, 0)
    return math.exp(-age_hours / HALF_LIFE_HOURS)


def _load_preference_map(conn: sqlite3.Connection) -> dict[tuple[str, str], float]:
    """Load all preference weights into a dict keyed by (dimension, key)."""
    rows = conn.execute("SELECT dimension, key, weight FROM preference_weights").fetchall()
    return {(r["dimension"], r["key"]): r["weight"] for r in rows}


def _preference_score(pref_map: dict, signal_row: dict) -> float:
    """Compute preference score for a signal from weighted dimensions.

    Returns the raw sum of matching preference weights.  Positive means
    the user has expressed interest; negative means disinterest; zero is
    neutral.  The caller multiplies by w_pref directly so the score is
    unbounded — this lets a strong explicit preference (e.g. weight=10)
    meaningfully override heat differences.
    """
    total = 0.0
    # Source dimension
    for sk in signal_row.get("source_keys", []):
        total += pref_map.get(("source", sk), 0.0)
    # Tag dimension
    for tag in signal_row.get("tags", []):
        total += pref_map.get(("tag", tag), 0.0)
    # Layer dimension
    total += pref_map.get(("layer", signal_row.get("signal_layer", "")), 0.0)
    return total


def compute_feed(
    conn: sqlite3.Connection,
    tab: str = "recommend",
    page: int = 1,
    per_page: int = 20,
    channel: str = "",
) -> list[dict]:
    """Compute ranked feed items for a tab."""
    w_heat, w_pref, w_decay, w_bt = TAB_WEIGHTS.get(tab, TAB_WEIGHTS["recommend"])

    # Load signals joined with cluster info
    rows = conn.execute(
        """
        SELECT s.id AS signal_id, s.cluster_id, s.summary, s.content_zh, s.signal_layer,
               s.signal_strength, s.why_it_matters, s.tags_json, s.created_at,
               c.topic_label, c.item_count, c.date AS cluster_date
        FROM signals s
        JOIN clusters c ON s.cluster_id = c.id
        WHERE s.is_current = 1
        ORDER BY s.created_at DESC
        """
    ).fetchall()

    if not rows:
        return []

    # Max heat for normalization
    def _safe_int(v, default=0):
        try:
            return int(v)
        except (ValueError, TypeError):
            return default

    max_heat = max(
        (_safe_int(r["signal_strength"]) * _safe_int(r["item_count"]) for r in rows), default=1.0
    ) or 1.0

    # Load preference map
    pref_map = _load_preference_map(conn) if (w_pref > 0 or tab == "recommend") else {}

    # Build blocked sets — items matching any blocked dimension are excluded
    blocked: dict[str, set[str]] = {"tag": set(), "source": set(), "layer": set()}
    for (dim, key), weight in pref_map.items():
        if dim in blocked and weight <= BLOCK_THRESHOLD:
            blocked[dim].add(key)

    # Load cached slides set
    slides_set = {r["signal_id"] for r in conn.execute(
        "SELECT signal_id FROM signal_slides WHERE signal_id > 0"
    ).fetchall()}

    # Load source keys, types, authors, and URLs for each cluster
    cluster_sources: dict[int, list[str]] = {}
    cluster_source_types: dict[int, set[str]] = {}
    cluster_authors: dict[int, list[str]] = {}
    cluster_urls: dict[int, list[str]] = {}
    cluster_bodies: dict[int, str] = {}
    source_rows = conn.execute(
        """
        SELECT ci.cluster_id, src.source_key, src.type, src.enabled, ri.author, ri.url, ri.body, ri.body_zh
        FROM cluster_items ci
        JOIN raw_items ri ON ri.id = ci.raw_item_id
        JOIN sources src ON src.id = ri.source_id
        """
    ).fetchall()
    enabled_sources: set[str] = set()
    follow_sources: set[str] = set()
    for sr in source_rows:
        cid = sr["cluster_id"]
        cluster_sources.setdefault(cid, [])
        cluster_source_types.setdefault(cid, set())
        if sr["source_key"] not in cluster_sources[cid]:
            cluster_sources[cid].append(sr["source_key"])
        cluster_source_types[cid].add(sr["type"])
        if sr["enabled"]:
            enabled_sources.add(sr["source_key"])
        if sr["type"] in FOLLOW_SOURCE_TYPES:
            follow_sources.add(sr["source_key"])
        if sr["author"] and sr["author"].strip():
            cluster_authors.setdefault(cid, [])
            author = sr["author"].strip()
            if author not in cluster_authors[cid]:
                cluster_authors[cid].append(author)
        if sr["url"] and sr["url"].startswith("http"):
            cluster_urls.setdefault(cid, [])
            if sr["url"] not in cluster_urls[cid]:
                cluster_urls[cid].append(sr["url"])
        body_text = sr["body_zh"] or sr["body"]
        if body_text and len(body_text) > len(cluster_bodies.get(cid, "")):
            cluster_bodies[cid] = body_text

    # Load Bradley-Terry scores
    bt_scores = {}
    if w_bt > 0:
        bt_rows = conn.execute("SELECT signal_id, bt_score FROM signal_scores").fetchall()
        for br in bt_rows:
            bt_scores[br["signal_id"]] = br["bt_score"]
    max_bt = max(bt_scores.values(), default=1500.0) or 1500.0

    # Build scored items
    items = []
    for r in rows:
        tags = []
        try:
            tags = json.loads(r["tags_json"]) if r["tags_json"] else []
        except (json.JSONDecodeError, TypeError):
            pass

        source_keys = cluster_sources.get(r["cluster_id"], [])

        # Follow tab: only show clusters from personal/curated sources
        if tab == "follow":
            if not any(sk in follow_sources for sk in source_keys):
                continue
            # Channel filter = source type filter (e.g. "x", "youtube")
            if channel and channel not in cluster_source_types.get(r["cluster_id"], set()):
                continue

        # Hard-block: skip items where ALL source types are blocked, or any tag is blocked
        if blocked["source"] and source_keys and all(sk in blocked["source"] for sk in source_keys):
            continue
        if blocked["tag"] and tags and any(t in blocked["tag"] for t in tags):
            continue
        if blocked["layer"] and r["signal_layer"] in blocked["layer"]:
            continue

        authors = cluster_authors.get(r["cluster_id"], [])
        urls = cluster_urls.get(r["cluster_id"], [])
        has_slides = r["signal_id"] in slides_set

        item = {
            "signal_id": r["signal_id"],
            "cluster_id": r["cluster_id"],
            "topic_label": r["topic_label"],
            "summary": r["content_zh"] or r["summary"],
            "signal_layer": r["signal_layer"],
            "signal_strength": r["signal_strength"],
            "why_it_matters": r["why_it_matters"],
            "item_count": r["item_count"],
            "tags": tags,
            "source_keys": source_keys,
            "authors": authors,
            "urls": urls,
            "has_slides": has_slides,
            "full_body": cluster_bodies.get(r["cluster_id"], ""),
            "cluster_date": r["cluster_date"],
            "created_at": r["created_at"],
        }

        heat_norm = (r["signal_strength"] * r["item_count"]) / max_heat
        pref = _preference_score(pref_map, item) if w_pref > 0 else 0.0
        decay = _time_decay(r["created_at"])

        bt = bt_scores.get(r["signal_id"], 1500.0)
        bt_norm = bt / max_bt
        # pref is a raw sum of weights (unbounded) — allows strong explicit
        # preferences to dominate heat when w_pref > 0.
        item["score"] = w_heat * heat_norm + w_pref * pref + w_decay * decay + w_bt * bt_norm
        items.append(item)

    items.sort(key=lambda x: x["score"], reverse=True)

    # Pagination
    start = (page - 1) * per_page
    return items[start : start + per_page]


def update_preferences(conn: sqlite3.Connection, signal_id: int, action: str) -> None:
    """Update preference weights based on user feedback on a signal."""
    delta = ACTION_DELTAS.get(action, 0.0)
    if delta == 0.0:
        return

    # Get signal details
    row = conn.execute(
        "SELECT s.signal_layer, s.tags_json, c.topic_label "
        "FROM signals s JOIN clusters c ON s.cluster_id = c.id "
        "WHERE s.id = ?",
        (signal_id,),
    ).fetchone()
    if not row:
        return

    # Get source keys for this signal's cluster
    sources = conn.execute(
        "SELECT DISTINCT src.source_key "
        "FROM cluster_items ci "
        "JOIN raw_items ri ON ri.id = ci.raw_item_id "
        "JOIN sources src ON src.id = ri.source_id "
        "WHERE ci.cluster_id = (SELECT cluster_id FROM signals WHERE id = ?)",
        (signal_id,),
    ).fetchall()

    keys_to_update: list[tuple[str, str]] = []

    # Layer dimension
    keys_to_update.append(("layer", row["signal_layer"]))

    # Tag dimension
    tags = []
    try:
        tags = json.loads(row["tags_json"]) if row["tags_json"] else []
    except (json.JSONDecodeError, TypeError):
        pass
    for tag in tags:
        keys_to_update.append(("tag", tag))

    # Source dimension
    for src in sources:
        keys_to_update.append(("source", src["source_key"]))

    # Upsert weights
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
    conn.commit()
