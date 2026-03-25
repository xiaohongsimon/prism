from unittest.mock import AsyncMock, patch
from prism.pipeline.sync import run_sync
from prism.sources.base import SyncResult
from prism.models import RawItem


def test_sync_stores_items(db):
    # Seed a source
    db.execute("INSERT INTO sources (source_key, type, handle, enabled) VALUES ('x:test', 'x', 'test', 1)")
    db.commit()
    mock_result = SyncResult(source_key="x:test", items=[
        RawItem(url="https://x.com/1", title="Test tweet", body="body", author="test")
    ], success=True)
    with patch("prism.pipeline.sync.get_adapter") as mock_adapter:
        mock_adapter.return_value.sync = AsyncMock(return_value=mock_result)
        import asyncio
        asyncio.run(run_sync(db, source_key=None))
    items = db.execute("SELECT * FROM raw_items").fetchall()
    assert len(items) == 1


def test_sync_partial_failure_continues(db):
    db.execute("INSERT INTO sources (source_key, type, handle, enabled) VALUES ('x:a', 'x', 'a', 1)")
    db.execute("INSERT INTO sources (source_key, type, handle, enabled) VALUES ('x:b', 'x', 'b', 1)")
    db.commit()
    fail_result = SyncResult(source_key="x:a", items=[], success=False, error="timeout")
    ok_result = SyncResult(source_key="x:b", items=[
        RawItem(url="https://x.com/2", title="OK", body="ok", author="b")
    ], success=True)
    with patch("prism.pipeline.sync.get_adapter") as mock_adapter:
        mock_adapter.return_value.sync = AsyncMock(side_effect=[fail_result, ok_result])
        import asyncio
        asyncio.run(run_sync(db, source_key=None))
    # b's item should still be stored
    items = db.execute("SELECT * FROM raw_items").fetchall()
    assert len(items) == 1
    # a should have consecutive_failures incremented
    src_a = db.execute("SELECT * FROM sources WHERE source_key='x:a'").fetchone()
    assert src_a["consecutive_failures"] == 1


def test_hard_fail_disables_at_2(db):
    db.execute("INSERT INTO sources (source_key, type, handle, enabled, consecutive_failures) VALUES ('x:bad', 'x', 'bad', 1, 1)")
    db.commit()
    fail_result = SyncResult(source_key="x:bad", items=[], success=False, error="HTTP 404")
    with patch("prism.pipeline.sync.get_adapter") as mock_adapter:
        mock_adapter.return_value.sync = AsyncMock(return_value=fail_result)
        import asyncio
        asyncio.run(run_sync(db, source_key=None))
    src = db.execute("SELECT * FROM sources WHERE source_key='x:bad'").fetchone()
    assert src["enabled"] == 0
    assert src["disabled_reason"] == "auto"
    assert src["auto_retry_at"] is not None


def test_auto_retry_reenables_on_success(db):
    """Sources past auto_retry_at should be retried even if disabled."""
    db.execute("""INSERT INTO sources (source_key, type, handle, enabled, disabled_reason, auto_retry_at)
                  VALUES ('x:retry', 'x', 'retry', 0, 'auto', datetime('now', '-1 hour'))""")
    db.commit()
    ok_result = SyncResult(source_key="x:retry", items=[], success=True)
    with patch("prism.pipeline.sync.get_adapter") as mock_adapter:
        mock_adapter.return_value.sync = AsyncMock(return_value=ok_result)
        import asyncio
        asyncio.run(run_sync(db, source_key=None))
    src = db.execute("SELECT * FROM sources WHERE source_key='x:retry'").fetchone()
    assert src["enabled"] == 1
    assert src["disabled_reason"] is None
