"""Tests for prism.pipeline.entity_lifecycle — lifecycle scoring (Task 4)."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from prism.db import init_db
from prism.pipeline.entity_lifecycle import (
    PRACTICE_BOOST,
    compute_status,
    update_entity_statuses,
    update_lifecycle_scores,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def conn():
    """In-memory SQLite database with Prism schema."""
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


TODAY = date.today()


def _iso(days_ago: int) -> str:
    """Return an ISO date string for today minus `days_ago`."""
    return (TODAY - timedelta(days=days_ago)).isoformat()


def _setup_entity_with_events(
    conn: sqlite3.Connection,
    entity_id_hint: int,
    name: str,
    events: list[tuple[int, str, str]],
    first_seen_days_ago: int = 0,
) -> int:
    """Insert an entity_profile and its events, return the real entity_id.

    Parameters
    ----------
    conn : sqlite3.Connection
    entity_id_hint : int
        Not used directly (auto-increment); kept for API symmetry with the spec.
    name : str
        canonical_name / display_name for the entity.
    events : list of (days_ago, event_type, impact)
        Each tuple creates one entity_events row with confidence=0.9.
    first_seen_days_ago : int
        How many days ago first_seen_at should be.

    Returns
    -------
    int
        The actual entity_id assigned by the DB.
    """
    first_seen = _iso(first_seen_days_ago)
    cur = conn.execute(
        """
        INSERT INTO entity_profiles
            (canonical_name, display_name, category, status, first_seen_at)
        VALUES (?, ?, 'project', 'emerging', ?)
        """,
        (name, name, first_seen),
    )
    conn.commit()
    entity_id = cur.lastrowid

    for days_ago, event_type, impact in events:
        conn.execute(
            """
            INSERT INTO entity_events
                (entity_id, date, event_type, impact, confidence)
            VALUES (?, ?, ?, ?, 0.9)
            """,
            (entity_id, _iso(days_ago), event_type, impact),
        )
    conn.commit()
    return entity_id


# ---------------------------------------------------------------------------
# test_emerging
# ---------------------------------------------------------------------------


def test_emerging(conn):
    """Young entity with 3 recent high-impact events should become 'emerging'."""
    entity_id = _setup_entity_with_events(
        conn,
        1,
        "new-project",
        events=[
            (1, "mention", "high"),
            (3, "mention", "high"),
            (5, "mention", "high"),
        ],
        first_seen_days_ago=7,  # 7 days old — within the ≤14d window
    )

    updated = update_lifecycle_scores(conn, TODAY.isoformat())
    assert updated >= 1

    row = conn.execute(
        "SELECT * FROM entity_profiles WHERE id = ?", (entity_id,)
    ).fetchone()

    # Verify m7 is substantially positive
    assert row["m7_score"] > 3.0, f"Expected m7 > 3.0, got {row['m7_score']}"
    assert row["event_count_total"] == 3

    changed = update_entity_statuses(conn)
    row = conn.execute(
        "SELECT status FROM entity_profiles WHERE id = ?", (entity_id,)
    ).fetchone()
    assert row["status"] == "emerging", f"Expected 'emerging', got '{row['status']}'"


# ---------------------------------------------------------------------------
# test_declining
# ---------------------------------------------------------------------------


def test_declining(conn):
    """Old entity whose last event was 45 days ago should become 'declining'."""
    entity_id = _setup_entity_with_events(
        conn,
        2,
        "old-project",
        events=[
            (90, "mention", "medium"),
            (80, "mention", "medium"),
            (75, "mention", "medium"),
            (70, "mention", "medium"),
            (45, "mention", "low"),  # last event 45 days ago
        ],
        first_seen_days_ago=120,  # very old entity
    )

    updated = update_lifecycle_scores(conn, TODAY.isoformat())
    assert updated >= 1

    update_entity_statuses(conn)

    row = conn.execute(
        "SELECT status, last_event_at FROM entity_profiles WHERE id = ?", (entity_id,)
    ).fetchone()
    assert row["status"] == "declining", f"Expected 'declining', got '{row['status']}'"


# ---------------------------------------------------------------------------
# test_practice_boost
# ---------------------------------------------------------------------------


def test_practice_boost(conn):
    """A practice_commit event must yield higher m7 than a plain event of equal impact."""
    # Entity A: practice event
    entity_a = _setup_entity_with_events(
        conn,
        3,
        "practice-entity",
        events=[(1, "practice_commit", "medium")],
        first_seen_days_ago=30,
    )

    # Entity B: plain external event (same age, same impact)
    entity_b = _setup_entity_with_events(
        conn,
        4,
        "external-entity",
        events=[(1, "external_mention", "medium")],
        first_seen_days_ago=30,
    )

    update_lifecycle_scores(conn, TODAY.isoformat())

    row_a = conn.execute(
        "SELECT m7_score FROM entity_profiles WHERE id = ?", (entity_a,)
    ).fetchone()
    row_b = conn.execute(
        "SELECT m7_score FROM entity_profiles WHERE id = ?", (entity_b,)
    ).fetchone()

    m7_a = row_a["m7_score"]
    m7_b = row_b["m7_score"]

    assert m7_a > m7_b, (
        f"Expected practice_commit m7 ({m7_a:.4f}) > external_mention m7 ({m7_b:.4f})"
    )
    # The ratio should be approximately PRACTICE_BOOST
    ratio = m7_a / m7_b
    assert abs(ratio - PRACTICE_BOOST) < 0.01, (
        f"Expected ratio ≈ {PRACTICE_BOOST}, got {ratio:.4f}"
    )


# ---------------------------------------------------------------------------
# test_event_counts
# ---------------------------------------------------------------------------


def test_event_counts(conn):
    """event_count_7d, event_count_30d, event_count_total must be accurate."""
    entity_id = _setup_entity_with_events(
        conn,
        5,
        "count-entity",
        events=[
            (2, "mention", "medium"),   # within 7d
            (5, "mention", "medium"),   # within 7d
            (10, "mention", "medium"),  # within 30d (not 7d)
            (20, "mention", "medium"),  # within 30d (not 7d)
            (45, "mention", "medium"),  # outside 30d window
            (50, "mention", "medium"),  # outside 30d window
        ],
        first_seen_days_ago=60,
    )

    update_lifecycle_scores(conn, TODAY.isoformat())

    row = conn.execute(
        "SELECT event_count_7d, event_count_30d, event_count_total "
        "FROM entity_profiles WHERE id = ?",
        (entity_id,),
    ).fetchone()

    assert row["event_count_7d"] == 2, f"Expected 2, got {row['event_count_7d']}"
    assert row["event_count_30d"] == 4, f"Expected 4, got {row['event_count_30d']}"
    assert row["event_count_total"] == 6, f"Expected 6, got {row['event_count_total']}"


# ---------------------------------------------------------------------------
# test_last_event_at
# ---------------------------------------------------------------------------


def test_last_event_at(conn):
    """last_event_at should reflect the most recent event date."""
    most_recent_days_ago = 3
    entity_id = _setup_entity_with_events(
        conn,
        6,
        "last-event-entity",
        events=[
            (10, "mention", "medium"),
            (most_recent_days_ago, "mention", "high"),
            (7, "mention", "low"),
        ],
        first_seen_days_ago=20,
    )

    update_lifecycle_scores(conn, TODAY.isoformat())

    row = conn.execute(
        "SELECT last_event_at FROM entity_profiles WHERE id = ?", (entity_id,)
    ).fetchone()

    expected = _iso(most_recent_days_ago)
    assert row["last_event_at"] == expected, (
        f"Expected last_event_at={expected!r}, got {row['last_event_at']!r}"
    )


# ---------------------------------------------------------------------------
# test_no_events
# ---------------------------------------------------------------------------


def test_no_events(conn):
    """Entity with zero events should have all counts = 0 and scores = 0.0."""
    cur = conn.execute(
        """
        INSERT INTO entity_profiles
            (canonical_name, display_name, category, first_seen_at)
        VALUES ('empty-entity', 'Empty', 'project', ?)
        """,
        (_iso(5),),
    )
    conn.commit()
    entity_id = cur.lastrowid

    updated = update_lifecycle_scores(conn, TODAY.isoformat())
    assert updated >= 1

    row = conn.execute(
        "SELECT * FROM entity_profiles WHERE id = ?", (entity_id,)
    ).fetchone()

    assert row["m7_score"] == 0.0
    assert row["m30_score"] == 0.0
    assert row["event_count_7d"] == 0
    assert row["event_count_30d"] == 0
    assert row["event_count_total"] == 0
    assert row["last_event_at"] is None
