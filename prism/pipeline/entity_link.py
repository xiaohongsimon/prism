"""
Entity link pipeline orchestrator for Prism v2 Entity Core (Task 5).

Ties together: entity_extract → entity_normalize → candidate staging →
profile promotion → entity_lifecycle, driven by daily signals.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

from prism.db import insert_job_run, finish_job_run
from prism.pipeline.entity_normalize import normalize, resolve, upsert_alias
from prism.pipeline.entity_extract import deterministic_candidates, extract_entities_llm
from prism.pipeline.entity_lifecycle import update_lifecycle_scores, update_entity_statuses

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_ENTITIES_PER_SIGNAL = 5
MAX_NEW_ENTITIES_PER_SIGNAL = 2
MIN_SPECIFICITY = 4
MIN_CONFIDENCE = 0.8
CANDIDATE_PROMOTE_THRESHOLD = 3
CANDIDATE_EXPIRY_DAYS = 30

VALID_CATEGORIES = {"person", "org", "project", "model", "technique", "dataset"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _impact_from_strength(signal_strength: int) -> str:
    """Map signal_strength integer to impact label."""
    if signal_strength >= 4:
        return "high"
    if signal_strength >= 2:
        return "medium"
    return "low"


def _create_profile(
    conn: sqlite3.Connection,
    *,
    canonical_name: str,
    display_name: str,
    category: str,
) -> int:
    """INSERT a new entity_profile row + canonical alias. Returns entity_id."""
    now = _now_iso()
    cur = conn.execute(
        """
        INSERT INTO entity_profiles
            (canonical_name, display_name, category, status, first_seen_at)
        VALUES (?, ?, ?, 'emerging', ?)
        """,
        (canonical_name, display_name, category, now),
    )
    conn.commit()
    entity_id = cur.lastrowid
    # Register the canonical name as its own alias
    upsert_alias(conn, entity_id, display_name, source="llm")
    return entity_id


def _insert_event(
    conn: sqlite3.Connection,
    *,
    entity_id: int,
    signal_id: Optional[int],
    date: str,
    event_type: str,
    impact: str,
    confidence: float,
    description: str = "",
) -> None:
    """Insert one entity_events row."""
    conn.execute(
        """
        INSERT INTO entity_events
            (entity_id, signal_id, date, event_type, role, impact, confidence, description)
        VALUES (?, ?, ?, ?, 'subject', ?, ?, ?)
        """,
        (entity_id, signal_id, date, event_type, impact, confidence, description),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# stage_candidate
# ---------------------------------------------------------------------------

def stage_candidate(
    conn: sqlite3.Connection,
    *,
    name_norm: str,
    display_name: str,
    category: str,
    signal_id: Optional[int],
) -> None:
    """Upsert a candidate entity mention.

    - If candidate exists: increment mention_count, update last_seen_at,
      append signal_id to sample_signals_json (max 3 entries).
    - If new: INSERT with expires_at = now + CANDIDATE_EXPIRY_DAYS.
    """
    existing = conn.execute(
        "SELECT * FROM entity_candidates WHERE name_norm = ?",
        (name_norm,),
    ).fetchone()

    if existing is not None:
        # Parse existing sample signals
        try:
            samples: list = json.loads(existing["sample_signals_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            samples = []

        if signal_id is not None and signal_id not in samples:
            samples.append(signal_id)
        samples = samples[-3:]  # keep at most 3

        conn.execute(
            """
            UPDATE entity_candidates
            SET mention_count = mention_count + 1,
                last_seen_at = datetime('now'),
                sample_signals_json = ?
            WHERE name_norm = ?
            """,
            (json.dumps(samples), name_norm),
        )
    else:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=CANDIDATE_EXPIRY_DAYS)
        ).strftime("%Y-%m-%dT%H:%M:%S")

        samples = [signal_id] if signal_id is not None else []
        conn.execute(
            """
            INSERT INTO entity_candidates
                (name_norm, display_name, category, mention_count,
                 sample_signals_json, expires_at)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (name_norm, display_name, category, json.dumps(samples), expires_at),
        )

    conn.commit()


# ---------------------------------------------------------------------------
# promote_ready_candidates
# ---------------------------------------------------------------------------

def promote_ready_candidates(conn: sqlite3.Connection) -> int:
    """Promote candidates with mention_count >= CANDIDATE_PROMOTE_THRESHOLD.

    For each qualifying candidate:
      - Skip if canonical_name already exists in entity_profiles.
      - Create entity_profile + alias.
      - Delete from entity_candidates.

    Returns the number promoted.
    """
    candidates = conn.execute(
        "SELECT * FROM entity_candidates WHERE mention_count >= ?",
        (CANDIDATE_PROMOTE_THRESHOLD,),
    ).fetchall()

    promoted = 0
    for cand in candidates:
        name_norm = cand["name_norm"]
        display_name = cand["display_name"] or name_norm
        category = cand["category"] or "project"

        # Validate category
        if category not in VALID_CATEGORIES:
            category = "project"

        # Skip if already exists in entity_profiles
        existing = conn.execute(
            "SELECT id FROM entity_profiles WHERE canonical_name = ?",
            (name_norm,),
        ).fetchone()
        if existing is not None:
            # Clean up the duplicate candidate
            conn.execute(
                "DELETE FROM entity_candidates WHERE name_norm = ?",
                (name_norm,),
            )
            conn.commit()
            continue

        _create_profile(
            conn,
            canonical_name=name_norm,
            display_name=display_name,
            category=category,
        )

        conn.execute(
            "DELETE FROM entity_candidates WHERE name_norm = ?",
            (name_norm,),
        )
        conn.commit()
        promoted += 1
        logger.info("Promoted candidate → entity_profile: %s [%s]", name_norm, category)

    return promoted


# ---------------------------------------------------------------------------
# expire_candidates
# ---------------------------------------------------------------------------

def expire_candidates(conn: sqlite3.Connection) -> int:
    """Delete candidates past their expiry date.

    Returns the count deleted.
    """
    cur = conn.execute(
        "DELETE FROM entity_candidates WHERE expires_at < datetime('now')"
    )
    conn.commit()
    deleted = cur.rowcount
    if deleted:
        logger.info("Expired %d candidates", deleted)
    return deleted


# ---------------------------------------------------------------------------
# run_entity_link  (main orchestrator)
# ---------------------------------------------------------------------------

def run_entity_link(
    conn: sqlite3.Connection,
    dt: str,
    model: Optional[str] = None,
) -> dict:
    """Run the full entity-link pipeline for date *dt*.

    Steps:
      1. Load current signals for *dt* (signals JOIN clusters WHERE date=dt AND is_current=1).
      2. Load known entities (entity_profiles + aliases).
      3. For each signal:
         a. deterministic_candidates(signal)
         b. extract_entities_llm(...) — wrapped in try/except
         c. For each extracted entity (max MAX_ENTITIES_PER_SIGNAL):
            - normalize canonical_name
            - Try resolve() against known entities
            - Matched → upsert_alias + insert entity_event
            - Not matched + promotable + under new-entity budget → create profile + event
            - Else → stage_candidate
      4. expire_candidates()
      5. promote_ready_candidates()
      6. update_lifecycle_scores(dt)
      7. update_entity_statuses()
      8. Return stats dict.

    Parameters
    ----------
    conn : sqlite3.Connection
    dt : str
        ISO date string, e.g. "2026-03-29".
    model : str, optional
        Override the default LLM model.

    Returns
    -------
    dict
        Stats: signals_processed, entities_linked, entities_created,
               entities_staged, candidates_expired, candidates_promoted,
               lifecycle_updated, status_changes.
    """
    job_id = insert_job_run(conn, job_type="entity_link")

    stats = {
        "signals_processed": 0,
        "entities_linked": 0,
        "entities_created": 0,
        "entities_staged": 0,
        "candidates_expired": 0,
        "candidates_promoted": 0,
        "lifecycle_updated": 0,
        "status_changes": 0,
    }

    try:
        # -------------------------------------------------------------------
        # Step 1: Load signals for date
        # -------------------------------------------------------------------
        signals = conn.execute(
            """
            SELECT s.id AS signal_id,
                   s.summary,
                   s.why_it_matters,
                   s.tags_json,
                   s.signal_layer,
                   s.signal_strength,
                   c.topic_label,
                   c.date
            FROM signals s
            JOIN clusters c ON s.cluster_id = c.id
            WHERE c.date = ? AND s.is_current = 1
            """,
            (dt,),
        ).fetchall()

        logger.info("entity_link: %d signals found for %s", len(signals), dt)

        # -------------------------------------------------------------------
        # Step 2: Load known entities for context
        # -------------------------------------------------------------------
        known_entity_rows = conn.execute(
            "SELECT canonical_name, display_name, category FROM entity_profiles"
        ).fetchall()
        known_entities = [dict(row) for row in known_entity_rows]

        # -------------------------------------------------------------------
        # Step 3: Process each signal
        # -------------------------------------------------------------------
        for signal_row in signals:
            signal = dict(signal_row)
            signal_id = signal["signal_id"]
            signal_strength = signal.get("signal_strength") or 0

            stats["signals_processed"] += 1
            new_count = 0  # entities newly created from this signal

            # 3a. Deterministic candidates
            candidates = deterministic_candidates(signal)

            # 3b. LLM extraction
            try:
                llm_result = extract_entities_llm(
                    signal, candidates, known_entities, dt, model
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "extract_entities_llm failed for signal %s: %s", signal_id, exc
                )
                llm_result = {"entities": []}

            entities = llm_result.get("entities") or []

            # Cap at MAX_ENTITIES_PER_SIGNAL
            entities = entities[:MAX_ENTITIES_PER_SIGNAL]

            for ent in entities:
                raw_name: str = ent.get("name") or ""
                category: str = ent.get("category") or "project"
                confidence: float = float(ent.get("confidence") or 0.0)
                specificity: int = int(ent.get("specificity") or 0)

                if not raw_name:
                    continue

                # Validate category
                if category not in VALID_CATEGORIES:
                    category = "project"

                name_norm = normalize(raw_name)
                impact = _impact_from_strength(signal_strength)
                date_str = signal.get("date") or dt

                # 3c. Try to resolve against known entities
                matched = resolve(conn, name_norm, category)

                if matched is not None:
                    # Link: upsert alias + insert event
                    upsert_alias(conn, matched["id"], raw_name, source="llm")
                    _insert_event(
                        conn,
                        entity_id=matched["id"],
                        signal_id=signal_id,
                        date=date_str,
                        event_type="mention",
                        impact=impact,
                        confidence=confidence,
                        description=signal.get("summary") or "",
                    )
                    stats["entities_linked"] += 1

                elif (
                    confidence >= MIN_CONFIDENCE
                    and specificity >= MIN_SPECIFICITY
                    and new_count < MAX_NEW_ENTITIES_PER_SIGNAL
                ):
                    # Create new profile + insert event
                    entity_id = _create_profile(
                        conn,
                        canonical_name=name_norm,
                        display_name=raw_name,
                        category=category,
                    )
                    _insert_event(
                        conn,
                        entity_id=entity_id,
                        signal_id=signal_id,
                        date=date_str,
                        event_type="mention",
                        impact=impact,
                        confidence=confidence,
                        description=signal.get("summary") or "",
                    )
                    # Add to known_entities for subsequent signals this run
                    known_entities.append(
                        {"canonical_name": name_norm, "display_name": raw_name, "category": category}
                    )
                    new_count += 1
                    stats["entities_created"] += 1

                else:
                    # Stage as candidate
                    stage_candidate(
                        conn,
                        name_norm=name_norm,
                        display_name=raw_name,
                        category=category,
                        signal_id=signal_id,
                    )
                    stats["entities_staged"] += 1

        # -------------------------------------------------------------------
        # Steps 4–7: housekeeping + lifecycle
        # -------------------------------------------------------------------
        stats["candidates_expired"] = expire_candidates(conn)
        stats["candidates_promoted"] = promote_ready_candidates(conn)
        stats["lifecycle_updated"] = update_lifecycle_scores(conn, dt)
        stats["status_changes"] = update_entity_statuses(conn)

        finish_job_run(conn, job_id, status="ok", stats_json=json.dumps(stats))
        logger.info("entity_link complete: %s", stats)

    except Exception as exc:
        logger.exception("entity_link failed: %s", exc)
        finish_job_run(conn, job_id, status="error", stats_json=json.dumps(stats))
        raise

    return stats
