"""
Entity name normalization, alias resolution, and deduplication.
No external dependencies — Jaro-Winkler is implemented inline.
"""

import re
import sqlite3
import unicodedata
from typing import Optional


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Return a canonical, lowercase, punctuation-stripped form of *text*.

    Steps:
    1. NFKC unicode normalisation (e.g. ligatures like ﬁ → fi)
    2. casefold (locale-aware lowercase)
    3. Strip punctuation, keeping word chars (\\w) and hyphens
    4. Collapse internal whitespace to a single space and strip edges
    """
    text = unicodedata.normalize("NFKC", text)
    text = text.casefold()
    # Keep: word chars (letters, digits, underscore), hyphens, spaces
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Jaro-Winkler similarity (no external deps)
# ---------------------------------------------------------------------------

def _jaro(s1: str, s2: str) -> float:
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0

    match_dist = max(len1, len2) // 2 - 1
    match_dist = max(match_dist, 0)

    s1_matches = [False] * len1
    s2_matches = [False] * len2

    matches = 0
    transpositions = 0

    for i in range(len1):
        start = max(0, i - match_dist)
        end = min(i + match_dist + 1, len2)
        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1

    return (matches / len1 + matches / len2 +
            (matches - transpositions / 2) / matches) / 3.0


def _jaro_winkler(s1: str, s2: str, p: float = 0.1) -> float:
    """Jaro-Winkler similarity in [0, 1].

    *p* is the scaling factor for the common-prefix bonus (standard = 0.1).
    The prefix length is capped at 4 characters.
    """
    jaro = _jaro(s1, s2)
    # Common prefix length (max 4)
    prefix = 0
    for ch1, ch2 in zip(s1[:4], s2[:4]):
        if ch1 == ch2:
            prefix += 1
        else:
            break
    return jaro + prefix * p * (1.0 - jaro)


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------

def resolve(
    conn: sqlite3.Connection,
    name_norm: str,
    category: str,
    fuzzy_threshold: float = 0.9,
) -> Optional[sqlite3.Row]:
    """Look up an entity by normalised name.

    Priority order:
    1. Exact alias_norm match, same category
    2. Exact alias_norm match, any category
    3. Fuzzy (Jaro-Winkler ≥ fuzzy_threshold) match, same category only

    Returns the entity_profiles row, or None.
    """
    # Priority 1: exact, same category
    row = conn.execute(
        """
        SELECT ep.*
        FROM entity_aliases ea
        JOIN entity_profiles ep ON ea.entity_id = ep.id
        WHERE ea.alias_norm = ? AND ep.category = ?
        LIMIT 1
        """,
        (name_norm, category),
    ).fetchone()
    if row is not None:
        return row

    # Priority 2: exact, any category
    row = conn.execute(
        """
        SELECT ep.*
        FROM entity_aliases ea
        JOIN entity_profiles ep ON ea.entity_id = ep.id
        WHERE ea.alias_norm = ?
        LIMIT 1
        """,
        (name_norm,),
    ).fetchone()
    if row is not None:
        return row

    # Priority 3: fuzzy, same category only
    candidates = conn.execute(
        """
        SELECT ep.*, ea.alias_norm AS _alias_norm
        FROM entity_aliases ea
        JOIN entity_profiles ep ON ea.entity_id = ep.id
        WHERE ep.category = ?
        """,
        (category,),
    ).fetchall()

    best_row: Optional[sqlite3.Row] = None
    best_score: float = -1.0
    for candidate in candidates:
        score = _jaro_winkler(name_norm, candidate["_alias_norm"])
        if score >= fuzzy_threshold and score > best_score:
            best_score = score
            best_row = candidate

    return best_row


# ---------------------------------------------------------------------------
# upsert_alias
# ---------------------------------------------------------------------------

def upsert_alias(
    conn: sqlite3.Connection,
    entity_id: int,
    surface_form: str,
    source: str = "llm",
) -> None:
    """Insert a new alias for *entity_id* (idempotent — INSERT OR IGNORE)."""
    alias_norm = normalize(surface_form)
    conn.execute(
        """
        INSERT OR IGNORE INTO entity_aliases (alias_norm, entity_id, surface_form, source)
        VALUES (?, ?, ?, ?)
        """,
        (alias_norm, entity_id, surface_form, source),
    )
    conn.commit()
