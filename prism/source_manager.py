"""Source Manager: YAML↔DB reconcile and CRUD operations.

YAML is authoritative config (what should exist).
SQLite tracks runtime state (last_synced_at, consecutive_failures, etc.).

Reconcile rules:
- YAML new → DB insert
- YAML removed → DB mark disabled (enabled=0, origin='yaml_removed')
- YAML re-added after removal → DB re-enable (if disabled_reason='yaml_removed')
- DB runtime fields are never overwritten by YAML
- Auto-disabled sources (disabled_reason='auto') are NOT re-enabled by reconcile
"""

import sqlite3
import yaml
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# YAML I/O helpers
# ---------------------------------------------------------------------------

def _read_yaml(path: Path) -> list[dict]:
    """Read sources list from YAML file. Returns empty list if file doesn't exist."""
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    sources = data.get("sources", [])
    if not isinstance(sources, list):
        return []
    return sources or []


def _write_yaml(path: Path, sources: list[dict]) -> None:
    """Write sources list back to YAML file."""
    path.write_text(yaml.dump({"sources": sources}, default_flow_style=False, allow_unicode=True))


# ---------------------------------------------------------------------------
# Source key derivation
# ---------------------------------------------------------------------------

def _generate_source_key(type: str, handle: str = "", config: Optional[dict] = None) -> str:
    """Derive the canonical source_key.

    - If config has an explicit 'key' field, use it.
    - Otherwise: '{type}:{handle}'
    """
    if config and config.get("key"):
        return config["key"]
    return f"{type}:{handle}"


def _source_key_from_yaml_entry(entry: dict) -> str:
    """Derive source_key from a YAML sources entry dict."""
    type_ = entry.get("type", "")
    handle = entry.get("handle", "")
    explicit_key = entry.get("key")
    if explicit_key:
        return explicit_key
    return f"{type_}:{handle}"


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def reconcile_sources(conn: sqlite3.Connection, yaml_path: Path) -> None:
    """Sync YAML config → DB, respecting auto-disabled state.

    - New entries in YAML → INSERT into sources
    - Entries in DB but removed from YAML → mark disabled (if not already auto-disabled)
    - Auto-disabled entries are never touched by reconcile
    """
    yaml_entries = _read_yaml(yaml_path)

    # Build a map of source_key -> entry for all YAML-declared sources
    yaml_keys: dict[str, dict] = {}
    for entry in yaml_entries:
        key = _source_key_from_yaml_entry(entry)
        yaml_keys[key] = entry

    # Fetch all existing DB sources
    db_rows = conn.execute("SELECT * FROM sources").fetchall()
    db_keys = {row["source_key"]: row for row in db_rows}

    # 1. Insert new sources from YAML not yet in DB
    for key, entry in yaml_keys.items():
        if key not in db_keys:
            type_ = entry.get("type", "")
            handle = entry.get("handle", "")
            # Store full entry as config_yaml (exclude internal key field)
            config_snapshot = {k: v for k, v in entry.items() if k != "key"}
            config_yaml_str = yaml.dump(config_snapshot, default_flow_style=True).strip()
            conn.execute(
                "INSERT INTO sources (source_key, type, handle, config_yaml, enabled, origin) "
                "VALUES (?, ?, ?, ?, 1, 'yaml')",
                (key, type_, handle, config_yaml_str),
            )

    # 2. Re-enable sources that reappeared in YAML after being yaml_removed
    #    Also sync config_yaml for existing YAML-origin sources
    for key, entry in yaml_keys.items():
        if key in db_keys:
            if db_keys[key]["disabled_reason"] == "yaml_removed":
                conn.execute(
                    "UPDATE sources SET enabled=1, origin='yaml', disabled_reason=NULL WHERE source_key=?",
                    (key,),
                )
            # Sync config_yaml from YAML → DB for YAML-origin sources
            config_snapshot = {k: v for k, v in entry.items() if k != "key"}
            config_yaml_str = yaml.dump(config_snapshot, default_flow_style=True).strip()
            conn.execute(
                "UPDATE sources SET config_yaml = ? WHERE source_key = ? AND origin IN ('yaml', 'cli')",
                (config_yaml_str, key),
            )

    # 3. Disable DB sources that were removed from YAML (only if not auto-disabled)
    for key, row in db_keys.items():
        if key not in yaml_keys:
            # Only mark as disabled if not already auto-disabled
            if row["disabled_reason"] != "auto":
                conn.execute(
                    "UPDATE sources SET enabled=0, origin='yaml_removed', disabled_reason='yaml_removed' WHERE source_key=?",
                    (key,),
                )

    conn.commit()


def add_source(
    conn: sqlite3.Connection,
    yaml_path: Path,
    *,
    type: str,
    handle: str = "",
    config: Optional[dict] = None,
) -> str:
    """Add a new source: write to DB (origin=cli) AND append to YAML.

    Returns the generated source_key.
    """
    config = config or {}
    source_key = _generate_source_key(type, handle, config)

    # Build YAML entry
    entry: dict = {"type": type}
    if handle:
        entry["handle"] = handle
    # Include explicit key for non-handle types
    if config.get("key"):
        entry["key"] = config["key"]
    # Merge remaining config fields (excluding 'key' already handled)
    for k, v in config.items():
        if k != "key":
            entry[k] = v

    # Build config_yaml string for DB
    config_snapshot = {k: v for k, v in entry.items() if k not in ("type", "handle", "key")}
    config_yaml_str = yaml.dump(config_snapshot, default_flow_style=True).strip() if config_snapshot else ""

    # Insert into DB
    conn.execute(
        "INSERT OR IGNORE INTO sources (source_key, type, handle, config_yaml, enabled, origin) "
        "VALUES (?, ?, ?, ?, 1, 'cli')",
        (source_key, type, handle, config_yaml_str),
    )
    conn.commit()

    # Append to YAML
    existing = _read_yaml(yaml_path)
    # Avoid duplicates
    existing_keys = {_source_key_from_yaml_entry(e) for e in existing}
    if source_key not in existing_keys:
        existing.append(entry)
    _write_yaml(yaml_path, existing)

    return source_key


def remove_source(conn: sqlite3.Connection, yaml_path: Path, source_key: str) -> None:
    """Disable source in DB AND remove from YAML."""
    cursor = conn.execute(
        "UPDATE sources SET enabled=0, disabled_reason='manual' WHERE source_key=?",
        (source_key,),
    )
    conn.commit()
    if cursor.rowcount == 0:
        raise ValueError(f"Source not found: {source_key}")

    # Remove from YAML
    existing = _read_yaml(yaml_path)
    updated = [e for e in existing if _source_key_from_yaml_entry(e) != source_key]
    _write_yaml(yaml_path, updated)


def enable_source(conn: sqlite3.Connection, source_key: str) -> None:
    """Force-enable a source, clearing auto_disabled state and auto_retry_at."""
    cursor = conn.execute(
        "UPDATE sources SET enabled=1, disabled_reason=NULL, auto_retry_at=NULL WHERE source_key=?",
        (source_key,),
    )
    conn.commit()
    if cursor.rowcount == 0:
        raise ValueError(f"Source not found: {source_key}")


def list_sources(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all sources ordered by source_key."""
    return conn.execute("SELECT * FROM sources ORDER BY source_key").fetchall()
