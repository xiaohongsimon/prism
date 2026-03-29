"""Entity tagging: load entity dictionary and tag text."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import yaml


def load_entities(yaml_path: Path) -> dict:
    """Load entities dict from YAML file.

    Expected format:
        project: [vLLM, SGLang]
        org: [OpenAI, Anthropic]
        person: [{handle: karpathy, name: Andrej Karpathy}]
    """
    data = yaml.safe_load(yaml_path.read_text()) or {}
    return data


def tag_entities(title: str, body: str, entities: dict) -> set[str]:
    """Case-insensitive substring match against entity names.

    Returns set of matched entity names.
    """
    text = f"{title} {body}".lower()
    matched = set()

    for category, items in entities.items():
        for item in items:
            if isinstance(item, dict):
                # Person-style entry: check both name and handle
                name = item.get("name", "")
                handle = item.get("handle", "")
                if name and name.lower() in text:
                    matched.add(name)
                if handle and handle.lower() in text:
                    matched.add(name or handle)
            else:
                # Simple string entry
                if str(item).lower() in text:
                    matched.add(str(item))

    return matched


# ---------------------------------------------------------------------------
# DB-backed functions (Task 6)
# ---------------------------------------------------------------------------

def migrate_yaml_to_db(conn: sqlite3.Connection, yaml_path: Path) -> int:
    """Migrate entities from YAML into entity_profiles (idempotent).

    Iterates over categories project/org/person/model/technique/dataset.
    For each entry, normalizes the name, skips if already present in
    entity_profiles, otherwise INSERTs with status='mature', needs_review=0.
    Calls upsert_alias for the display_name (and handle for person entries).

    Returns the count of newly created profiles.
    """
    from prism.pipeline.entity_normalize import normalize, upsert_alias

    data = yaml.safe_load(yaml_path.read_text()) or {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    created = 0

    valid_categories = {"person", "org", "project", "model", "technique", "dataset"}

    for category, items in data.items():
        if category not in valid_categories:
            continue
        if not items:
            continue
        for item in items:
            if isinstance(item, dict):
                display_name = item.get("name") or item.get("handle") or ""
                handle = item.get("handle") or ""
            else:
                display_name = str(item)
                handle = ""

            if not display_name:
                continue

            canonical_name = normalize(display_name)

            # Skip if already exists
            existing = conn.execute(
                "SELECT id FROM entity_profiles WHERE canonical_name = ?",
                (canonical_name,),
            ).fetchone()
            if existing is not None:
                continue

            cur = conn.execute(
                """
                INSERT INTO entity_profiles
                    (canonical_name, display_name, category, status,
                     needs_review, first_seen_at)
                VALUES (?, ?, ?, 'mature', 0, ?)
                """,
                (canonical_name, display_name, category, now),
            )
            conn.commit()
            entity_id = cur.lastrowid

            upsert_alias(conn, entity_id, display_name, source="yaml")
            if handle and handle != display_name:
                upsert_alias(conn, entity_id, handle, source="yaml")

            created += 1

    return created


def load_entities_from_db(conn: sqlite3.Connection) -> dict:
    """Return entities in the same format as load_entities() (YAML-compatible).

    Returns {category: [display_name, ...]} — person entries are plain strings
    (not dicts) for simplicity; consumers should treat them equivalently.
    """
    rows = conn.execute(
        "SELECT display_name, category FROM entity_profiles ORDER BY category, display_name"
    ).fetchall()

    result: dict = {}
    for row in rows:
        cat = row["category"]
        result.setdefault(cat, [])
        result[cat].append(row["display_name"])

    return result


def tag_entities_from_db(conn: sqlite3.Connection, title: str, body: str) -> set[str]:
    """Tag text using alias index from DB.

    Queries entity_aliases JOIN entity_profiles, does case-insensitive
    substring match of alias_norm against normalised text, returns a set of
    display_names.
    """
    from prism.pipeline.entity_normalize import normalize

    text_norm = normalize(f"{title} {body}")

    rows = conn.execute(
        """
        SELECT ea.alias_norm, ep.display_name
        FROM entity_aliases ea
        JOIN entity_profiles ep ON ea.entity_id = ep.id
        """
    ).fetchall()

    matched: set[str] = set()
    for row in rows:
        if row["alias_norm"] and row["alias_norm"] in text_norm:
            matched.add(row["display_name"])

    return matched
