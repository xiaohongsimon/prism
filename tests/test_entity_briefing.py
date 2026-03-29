"""Tests for entity-enriched briefing: _enrich_signals_with_entities and _generate_radar_changes."""

import sqlite3
import pytest
from prism.db import init_db
from prism.output.briefing import (
    _enrich_signals_with_entities,
    _generate_radar_changes,
)


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cluster(db, cluster_id, date="2026-03-29", topic="TestTopic"):
    db.execute(
        "INSERT INTO clusters (id, date, topic_label, item_count, merged_context) VALUES (?, ?, ?, 1, '')",
        (cluster_id, date, topic),
    )


def _make_signal(db, cluster_id, signal_id=None, layer="actionable", strength=4,
                 analysis_type="daily", is_current=1):
    if signal_id is not None:
        db.execute(
            """INSERT INTO signals (id, cluster_id, summary, signal_layer, signal_strength,
               why_it_matters, action, tl_perspective, tags_json, analysis_type, is_current)
               VALUES (?, ?, 'summary text', ?, ?, 'why', 'action', 'tl', '[]', ?, ?)""",
            (signal_id, cluster_id, layer, strength, analysis_type, is_current),
        )
    else:
        db.execute(
            """INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength,
               why_it_matters, action, tl_perspective, tags_json, analysis_type, is_current)
               VALUES (?, 'summary text', ?, ?, 'why', 'action', 'tl', '[]', ?, ?)""",
            (cluster_id, layer, strength, analysis_type, is_current),
        )
    db.commit()


def _make_entity(db, entity_id, display_name, status="emerging",
                 first_seen_at="2026-03-20T00:00:00", event_count_7d=1,
                 m7_score=2.0, m30_score=4.0):
    db.execute(
        """INSERT INTO entity_profiles
           (id, canonical_name, display_name, category, status, first_seen_at,
            event_count_7d, m7_score, m30_score)
           VALUES (?, ?, ?, 'project', ?, ?, ?, ?, ?)""",
        (entity_id, display_name.lower(), display_name, status,
         first_seen_at, event_count_7d, m7_score, m30_score),
    )
    db.commit()


def _make_event(db, entity_id, signal_id=None, event_type="mention",
                date="2026-03-29", impact="medium", confidence=0.9,
                description=""):
    db.execute(
        """INSERT INTO entity_events
           (entity_id, signal_id, date, event_type, role, impact, confidence, description)
           VALUES (?, ?, ?, ?, 'subject', ?, ?, ?)""",
        (entity_id, signal_id, date, event_type, impact, confidence, description),
    )
    db.commit()


# ---------------------------------------------------------------------------
# _enrich_signals_with_entities
# ---------------------------------------------------------------------------

class TestEnrichSignalsWithEntities:

    def test_no_entity_data_returns_empty_context(self, db):
        """When no entity data exists, entity_context should be empty list."""
        _make_cluster(db, 1, date="2026-03-29")
        _make_signal(db, cluster_id=1, signal_id=10)

        signals = [
            {
                "cluster_id": 1,
                "topic_label": "TestTopic",
                "summary": "summary text",
                "signal_layer": "actionable",
                "signal_strength": 4,
                "why_it_matters": "why",
                "action": "action",
                "tl_perspective": "tl",
                "tags": [],
            }
        ]

        result = _enrich_signals_with_entities(db, signals, "2026-03-29")
        assert len(result) == 1
        assert result[0]["entity_context"] == []

    def test_entity_linked_to_signal_appears_in_context(self, db):
        """Entity linked to signal via entity_events.signal_id shows up in entity_context."""
        _make_cluster(db, 1, date="2026-03-29")
        _make_signal(db, cluster_id=1, signal_id=10)
        _make_entity(db, entity_id=100, display_name="vLLM", status="growing",
                     event_count_7d=3)
        _make_event(db, entity_id=100, signal_id=10, event_type="mention",
                    date="2026-03-29")

        signals = [
            {
                "cluster_id": 1,
                "topic_label": "vLLM release",
                "summary": "summary text",
                "signal_layer": "actionable",
                "signal_strength": 4,
                "why_it_matters": "why",
                "action": "action",
                "tl_perspective": "tl",
                "tags": [],
            }
        ]

        result = _enrich_signals_with_entities(db, signals, "2026-03-29")
        assert len(result) == 1
        ec = result[0]["entity_context"]
        assert len(ec) == 1
        assert ec[0]["name"] == "vLLM"
        assert ec[0]["status"] == "growing"
        assert ec[0]["week_count"] == 3

    def test_practice_note_populated_when_overlap(self, db):
        """practice_note is set when entity has both practice_* and external events in 14d."""
        _make_cluster(db, 1, date="2026-03-29")
        _make_signal(db, cluster_id=1, signal_id=10)
        _make_entity(db, entity_id=100, display_name="vLLM", status="growing")
        # Practice event
        _make_event(db, entity_id=100, signal_id=None, event_type="practice_test",
                    date="2026-03-25", description="speculative decoding")
        # External event linked to signal
        _make_event(db, entity_id=100, signal_id=10, event_type="release",
                    date="2026-03-29")

        signals = [
            {
                "cluster_id": 1,
                "topic_label": "vLLM",
                "summary": "summary text",
                "signal_layer": "actionable",
                "signal_strength": 4,
                "why_it_matters": "why",
                "action": "action",
                "tl_perspective": "tl",
                "tags": [],
            }
        ]

        result = _enrich_signals_with_entities(db, signals, "2026-03-29")
        ec = result[0]["entity_context"]
        assert len(ec) == 1
        assert ec[0]["practice_note"] is not None
        assert "omlx" in ec[0]["practice_note"]
        assert "3/25" in ec[0]["practice_note"]

    def test_no_practice_note_when_only_practice_events(self, db):
        """practice_note is None when there are no external events alongside practice events."""
        _make_cluster(db, 1, date="2026-03-29")
        _make_signal(db, cluster_id=1, signal_id=10)
        _make_entity(db, entity_id=100, display_name="vLLM", status="growing")
        # Practice event only
        _make_event(db, entity_id=100, signal_id=10, event_type="practice_test",
                    date="2026-03-25", description="speculative decoding")

        signals = [
            {
                "cluster_id": 1,
                "topic_label": "vLLM",
                "summary": "summary text",
                "signal_layer": "actionable",
                "signal_strength": 4,
                "why_it_matters": "why",
                "action": "action",
                "tl_perspective": "tl",
                "tags": [],
            }
        ]

        result = _enrich_signals_with_entities(db, signals, "2026-03-29")
        ec = result[0]["entity_context"]
        # The practice event IS linked to signal_id=10, but no non-practice event → no overlap
        # practice_note should be None
        for item in ec:
            assert item["practice_note"] is None

    def test_original_signal_keys_preserved(self, db):
        """Enrichment should not drop any original signal keys."""
        _make_cluster(db, 1, date="2026-03-29")
        _make_signal(db, cluster_id=1, signal_id=10)

        signals = [
            {
                "cluster_id": 1,
                "topic_label": "T",
                "summary": "s",
                "signal_layer": "strategic",
                "signal_strength": 2,
                "why_it_matters": "w",
                "action": "a",
                "tl_perspective": "t",
                "tags": ["tag1"],
            }
        ]

        result = _enrich_signals_with_entities(db, signals, "2026-03-29")
        assert result[0]["tags"] == ["tag1"]
        assert result[0]["topic_label"] == "T"
        assert "entity_context" in result[0]


# ---------------------------------------------------------------------------
# _generate_radar_changes
# ---------------------------------------------------------------------------

class TestGenerateRadarChanges:

    def test_empty_when_no_entities(self, db):
        result = _generate_radar_changes(db, "2026-03-29")
        assert result == []

    def test_new_entity_today(self, db):
        _make_entity(db, entity_id=1, display_name="Claude Code",
                     first_seen_at="2026-03-29T10:00:00")
        result = _generate_radar_changes(db, "2026-03-29")
        assert any("Claude Code" in line and "新发现" in line for line in result)

    def test_new_entity_yesterday_not_included(self, db):
        _make_entity(db, entity_id=1, display_name="Claude Code",
                     first_seen_at="2026-03-28T10:00:00")
        result = _generate_radar_changes(db, "2026-03-29")
        # Should not appear in 新发现 since it was created yesterday
        assert not any("Claude Code" in line and "新发现" in line for line in result)

    def test_growing_entity_appears(self, db):
        _make_entity(db, entity_id=2, display_name="DeepSeek-V3", status="growing",
                     m7_score=5.0, m30_score=8.0)
        # Add a recent event so last_event_at is within 7 days
        _make_event(db, entity_id=2, event_type="mention", date="2026-03-27")
        # Update last_event_at manually
        db.execute("UPDATE entity_profiles SET last_event_at = '2026-03-27' WHERE id = 2")
        db.commit()
        result = _generate_radar_changes(db, "2026-03-29")
        assert any("DeepSeek-V3" in line and "growing" in line for line in result)

    def test_declining_entity_appears(self, db):
        _make_entity(db, entity_id=3, display_name="RLHF", status="declining")
        result = _generate_radar_changes(db, "2026-03-29")
        assert any("RLHF" in line and "趋于沉寂" in line for line in result)

    def test_practice_overlap_entity_appears(self, db):
        _make_entity(db, entity_id=4, display_name="vLLM", status="growing")
        # Practice event within 14 days
        _make_event(db, entity_id=4, event_type="practice_test",
                    date="2026-03-22")
        # External event within 14 days
        _make_event(db, entity_id=4, event_type="release",
                    date="2026-03-28")
        result = _generate_radar_changes(db, "2026-03-29")
        assert any("vLLM" in line and "实践交叉" in line for line in result)

    def test_no_practice_overlap_without_external_event(self, db):
        """Only practice events → no 实践交叉 line."""
        _make_entity(db, entity_id=5, display_name="SGLang", status="emerging")
        _make_event(db, entity_id=5, event_type="practice_test", date="2026-03-22")
        result = _generate_radar_changes(db, "2026-03-29")
        assert not any("SGLang" in line and "实践交叉" in line for line in result)

    def test_invalid_date_returns_empty(self, db):
        result = _generate_radar_changes(db, "not-a-date")
        assert result == []


# ---------------------------------------------------------------------------
# Practice overlap detection (combined scenario)
# ---------------------------------------------------------------------------

class TestPracticeOverlapDetection:

    def test_entity_with_practice_commit_and_release(self, db):
        """Entity with practice_commit and release events in 14d triggers overlap in both
        enrichment and radar."""
        _make_cluster(db, 1, date="2026-03-29")
        _make_signal(db, cluster_id=1, signal_id=10)
        _make_entity(db, entity_id=10, display_name="FlashAttention", status="growing")
        # Practice event (within 14 days)
        _make_event(db, entity_id=10, signal_id=None, event_type="practice_commit",
                    date="2026-03-20", description="tried FA2 kernel")
        # Release event linked to signal
        _make_event(db, entity_id=10, signal_id=10, event_type="release",
                    date="2026-03-29")

        # Test enrichment
        signals = [
            {
                "cluster_id": 1,
                "topic_label": "FlashAttention",
                "summary": "FA3 released",
                "signal_layer": "actionable",
                "signal_strength": 4,
                "why_it_matters": "faster",
                "action": "evaluate",
                "tl_perspective": "tl",
                "tags": [],
            }
        ]
        result = _enrich_signals_with_entities(db, signals, "2026-03-29")
        ec = result[0]["entity_context"]
        assert len(ec) == 1
        assert ec[0]["practice_note"] is not None
        assert "3/20" in ec[0]["practice_note"]

        # Test radar
        radar = _generate_radar_changes(db, "2026-03-29")
        assert any("FlashAttention" in line and "实践交叉" in line for line in radar)
