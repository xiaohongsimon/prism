"""Feed ranking engine: heat + preference + time decay."""

import json
import math
import sqlite3
from datetime import datetime, timezone


# Score weights per tab: (heat, preference, decay)
TAB_WEIGHTS = {
    "recommend": (0.4, 0.4, 0.2),
    "follow":    (0.2, 0.5, 0.3),
    "hot":       (0.6, 0.0, 0.4),
}

HALF_LIFE_HOURS = 24.0

# Feedback deltas per action
ACTION_DELTAS = {"like": 1.0, "dislike": -1.0, "save": 2.0}


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
) -> list[dict]:
    """Compute ranked feed items for a tab."""
    w_heat, w_pref, w_decay = TAB_WEIGHTS.get(tab, TAB_WEIGHTS["recommend"])

    # Load signals joined with cluster info
    rows = conn.execute(
        """
        SELECT s.id AS signal_id, s.cluster_id, s.summary, s.signal_layer,
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
    max_heat = max(
        (r["signal_strength"] * r["item_count"] for r in rows), default=1.0
    ) or 1.0

    # Load preference map
    pref_map = _load_preference_map(conn) if w_pref > 0 else {}

    # Load source keys and authors for each cluster
    cluster_sources: dict[int, list[str]] = {}
    cluster_authors: dict[int, list[str]] = {}
    source_rows = conn.execute(
        """
        SELECT ci.cluster_id, src.source_key, src.enabled, ri.author
        FROM cluster_items ci
        JOIN raw_items ri ON ri.id = ci.raw_item_id
        JOIN sources src ON src.id = ri.source_id
        """
    ).fetchall()
    enabled_sources: set[str] = set()
    for sr in source_rows:
        cluster_sources.setdefault(sr["cluster_id"], [])
        if sr["source_key"] not in cluster_sources[sr["cluster_id"]]:
            cluster_sources[sr["cluster_id"]].append(sr["source_key"])
        if sr["enabled"]:
            enabled_sources.add(sr["source_key"])
        if sr["author"] and sr["author"].strip():
            cluster_authors.setdefault(sr["cluster_id"], [])
            author = sr["author"].strip()
            if author not in cluster_authors[sr["cluster_id"]]:
                cluster_authors[sr["cluster_id"]].append(author)

    # Build scored items
    items = []
    for r in rows:
        tags = []
        try:
            tags = json.loads(r["tags_json"]) if r["tags_json"] else []
        except (json.JSONDecodeError, TypeError):
            pass

        source_keys = cluster_sources.get(r["cluster_id"], [])

        # Follow tab: skip clusters with no enabled sources
        if tab == "follow":
            if not any(sk in enabled_sources for sk in source_keys):
                continue

        authors = cluster_authors.get(r["cluster_id"], [])

        item = {
            "signal_id": r["signal_id"],
            "cluster_id": r["cluster_id"],
            "topic_label": r["topic_label"],
            "summary": r["summary"],
            "signal_layer": r["signal_layer"],
            "signal_strength": r["signal_strength"],
            "why_it_matters": r["why_it_matters"],
            "item_count": r["item_count"],
            "tags": tags,
            "source_keys": source_keys,
            "authors": authors,
            "cluster_date": r["cluster_date"],
            "created_at": r["created_at"],
        }

        heat_norm = (r["signal_strength"] * r["item_count"]) / max_heat
        pref = _preference_score(pref_map, item) if w_pref > 0 else 0.0
        decay = _time_decay(r["created_at"])

        # pref is a raw sum of weights (unbounded) — allows strong explicit
        # preferences to dominate heat when w_pref > 0.
        item["score"] = w_heat * heat_norm + w_pref * pref + w_decay * decay
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
