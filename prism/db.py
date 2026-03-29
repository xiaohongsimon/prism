import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT UNIQUE NOT NULL,
            type TEXT NOT NULL,
            handle TEXT NOT NULL DEFAULT '',
            config_yaml TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            origin TEXT NOT NULL DEFAULT 'yaml',
            disabled_reason TEXT,
            last_synced_at TEXT,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            auto_retry_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS raw_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            url TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            author TEXT NOT NULL DEFAULT '',
            published_at TEXT,
            raw_json TEXT NOT NULL DEFAULT '{}',
            thread_partial INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source_id, url)
        );

        CREATE TABLE IF NOT EXISTS clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            topic_label TEXT NOT NULL DEFAULT '',
            item_count INTEGER NOT NULL DEFAULT 0,
            merged_context TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS cluster_items (
            cluster_id INTEGER NOT NULL REFERENCES clusters(id),
            raw_item_id INTEGER NOT NULL REFERENCES raw_items(id),
            PRIMARY KEY (cluster_id, raw_item_id)
        );

        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_id INTEGER NOT NULL REFERENCES clusters(id),
            summary TEXT NOT NULL DEFAULT '',
            signal_layer TEXT NOT NULL DEFAULT 'noise',
            signal_strength INTEGER NOT NULL DEFAULT 0,
            why_it_matters TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL DEFAULT '',
            tl_perspective TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            analysis_type TEXT NOT NULL DEFAULT 'incremental',
            model_id TEXT NOT NULL DEFAULT '',
            prompt_version TEXT NOT NULL DEFAULT '',
            job_run_id INTEGER REFERENCES job_runs(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            is_current INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS cross_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_a_id INTEGER NOT NULL REFERENCES clusters(id),
            cluster_b_id INTEGER NOT NULL REFERENCES clusters(id),
            relation_type TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            job_run_id INTEGER REFERENCES job_runs(id),
            is_current INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS trends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_label TEXT NOT NULL,
            date TEXT NOT NULL,
            heat_score REAL NOT NULL DEFAULT 0.0,
            delta_vs_yesterday REAL NOT NULL DEFAULT 0.0,
            job_run_id INTEGER REFERENCES job_runs(id),
            is_current INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS briefings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            html TEXT NOT NULL DEFAULT '',
            markdown TEXT NOT NULL DEFAULT '',
            generated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS job_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'ok',
            stats_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS item_search USING fts5(
            title, body, content=raw_items, content_rowid=id
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS signal_search USING fts5(
            summary, tl_perspective, content=signals, content_rowid=id
        );

        -- FTS5 sync triggers for raw_items
        CREATE TRIGGER IF NOT EXISTS raw_items_ai AFTER INSERT ON raw_items BEGIN
            INSERT INTO item_search(rowid, title, body) VALUES (new.id, new.title, new.body);
        END;
        CREATE TRIGGER IF NOT EXISTS raw_items_ad AFTER DELETE ON raw_items BEGIN
            INSERT INTO item_search(item_search, rowid, title, body) VALUES('delete', old.id, old.title, old.body);
        END;

        -- FTS5 sync triggers for signals
        CREATE TRIGGER IF NOT EXISTS signals_ai AFTER INSERT ON signals BEGIN
            INSERT INTO signal_search(rowid, summary, tl_perspective) VALUES (new.id, new.summary, new.tl_perspective);
        END;
        CREATE TRIGGER IF NOT EXISTS signals_ad AFTER DELETE ON signals BEGIN
            INSERT INTO signal_search(signal_search, rowid, summary, tl_perspective) VALUES('delete', old.id, old.summary, old.tl_perspective);
        END;

        -- Entity system tables (v2)
        CREATE TABLE IF NOT EXISTS entity_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_name TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            category TEXT NOT NULL CHECK(category IN ('person','org','project','model','technique','dataset')),
            status TEXT DEFAULT 'emerging' CHECK(status IN ('emerging','growing','mature','declining')),
            summary TEXT DEFAULT '',
            needs_review INTEGER DEFAULT 1,
            first_seen_at TEXT NOT NULL,
            last_event_at TEXT,
            event_count_7d INTEGER DEFAULT 0,
            event_count_30d INTEGER DEFAULT 0,
            event_count_total INTEGER DEFAULT 0,
            m7_score REAL DEFAULT 0.0,
            m30_score REAL DEFAULT 0.0,
            metadata_json TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS entity_aliases (
            alias_norm TEXT NOT NULL,
            entity_id INTEGER NOT NULL REFERENCES entity_profiles(id),
            surface_form TEXT NOT NULL,
            source TEXT DEFAULT 'llm',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (alias_norm, entity_id)
        );
        CREATE INDEX IF NOT EXISTS idx_alias_norm ON entity_aliases(alias_norm);

        CREATE TABLE IF NOT EXISTS entity_candidates (
            name_norm TEXT PRIMARY KEY,
            display_name TEXT DEFAULT '',
            category TEXT DEFAULT '',
            mention_count INTEGER DEFAULT 1,
            first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
            sample_signals_json TEXT DEFAULT '[]',
            expires_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS entity_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL REFERENCES entity_profiles(id),
            signal_id INTEGER REFERENCES signals(id),
            date TEXT NOT NULL,
            event_type TEXT NOT NULL,
            role TEXT DEFAULT 'subject',
            impact TEXT DEFAULT 'medium' CHECK(impact IN ('high','medium','low')),
            confidence REAL DEFAULT 0.8,
            description TEXT DEFAULT '',
            metadata_json TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_entity_events_entity ON entity_events(entity_id, date);
        CREATE INDEX IF NOT EXISTS idx_entity_events_date ON entity_events(date);

        CREATE VIRTUAL TABLE IF NOT EXISTS entity_search USING fts5(
            canonical_name, display_name, summary,
            content=entity_profiles, content_rowid=id
        );

        CREATE TRIGGER IF NOT EXISTS entity_profiles_ai AFTER INSERT ON entity_profiles BEGIN
            INSERT INTO entity_search(rowid, canonical_name, display_name, summary)
            VALUES (new.id, new.canonical_name, new.display_name, new.summary);
        END;
        CREATE TRIGGER IF NOT EXISTS entity_profiles_ad AFTER DELETE ON entity_profiles BEGIN
            INSERT INTO entity_search(entity_search, rowid, canonical_name, display_name, summary)
            VALUES('delete', old.id, old.canonical_name, old.display_name, old.summary);
        END;
    """)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def insert_source(conn: sqlite3.Connection, *, source_key: str, type: str,
                  handle: str = "", config_yaml: str = "", origin: str = "yaml") -> int:
    cursor = conn.execute(
        "INSERT INTO sources (source_key, type, handle, config_yaml, origin) VALUES (?, ?, ?, ?, ?)",
        (source_key, type, handle, config_yaml, origin))
    conn.commit()
    return cursor.lastrowid


def get_source_by_key(conn: sqlite3.Connection, source_key: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM sources WHERE source_key = ?", (source_key,)).fetchone()


def get_enabled_sources(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM sources WHERE enabled = 1").fetchall()


def insert_raw_item(conn: sqlite3.Connection, *, source_id: int, url: str,
                    title: str = "", body: str = "", author: str = "",
                    published_at: str = "", raw_json: str = "{}",
                    thread_partial: bool = False) -> Optional[int]:
    try:
        cursor = conn.execute(
            """INSERT INTO raw_items (source_id, url, title, body, author, published_at, raw_json, thread_partial)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (source_id, url, title, body, author, published_at, raw_json, int(thread_partial)))
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None  # Duplicate URL for this source


def insert_job_run(conn: sqlite3.Connection, *, job_type: str, status: str = "ok",
                   stats_json: str = "{}") -> int:
    cursor = conn.execute(
        "INSERT INTO job_runs (job_type, status, stats_json) VALUES (?, ?, ?)",
        (job_type, status, stats_json))
    conn.commit()
    return cursor.lastrowid


def finish_job_run(conn: sqlite3.Connection, job_id: int, status: str, stats_json: str = "{}") -> None:
    conn.execute(
        "UPDATE job_runs SET finished_at = datetime('now'), status = ?, stats_json = ? WHERE id = ?",
        (status, stats_json, job_id))
    conn.commit()
