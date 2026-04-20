"""Tests for follow-author detection used by feed-card follow buttons."""
import sqlite3

from prism.db import init_db


def _mkconn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def test_get_followed_authors_merges_sources_yaml_and_prefs():
    from prism.web.feed import get_followed_authors, FOLLOW_AUTHOR_WEIGHT

    conn = _mkconn()
    conn.execute(
        "INSERT INTO sources (source_key, type, handle, enabled) "
        "VALUES ('x:karpathy','x','karpathy',1),"
        "       ('x:simonw','x','simonw',1),"
        "       ('hn:demo','hn','',1),"
        "       ('x:disabled','x','zzz',0)"
    )
    conn.execute(
        "INSERT INTO preference_weights (dimension, key, weight) "
        "VALUES ('author','explicitfollow', ?),('author','weaksignal', 0.5)",
        (FOLLOW_AUTHOR_WEIGHT,),
    )
    conn.commit()

    followed = get_followed_authors(conn)
    assert "karpathy" in followed
    assert "simonw" in followed
    assert "explicitfollow" in followed
    assert "zzz" not in followed  # disabled
    assert "weaksignal" not in followed  # below threshold
