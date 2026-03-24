import yaml
from pathlib import Path
from prism.source_manager import reconcile_sources, add_source, remove_source, enable_source, list_sources


def _write_yaml(path, sources):
    path.write_text(yaml.dump({"sources": sources}))


def test_reconcile_adds_new_sources_from_yaml(db, tmp_path):
    yaml_path = tmp_path / "sources.yaml"
    _write_yaml(yaml_path, [
        {"type": "x", "handle": "karpathy", "depth": "thread"},
        {"type": "arxiv", "key": "arxiv:daily", "categories": ["cs.LG"]},
    ])
    reconcile_sources(db, yaml_path)
    sources = list_sources(db)
    assert len(sources) == 2
    assert sources[0]["source_key"] == "arxiv:daily"
    assert sources[1]["source_key"] == "x:karpathy"


def test_reconcile_respects_auto_disabled(db, tmp_path):
    yaml_path = tmp_path / "sources.yaml"
    _write_yaml(yaml_path, [{"type": "x", "handle": "karpathy"}])
    reconcile_sources(db, yaml_path)
    # Simulate auto-disable
    db.execute("UPDATE sources SET enabled=0, disabled_reason='auto' WHERE source_key='x:karpathy'")
    db.commit()
    # Re-reconcile should NOT re-enable
    reconcile_sources(db, yaml_path)
    src = db.execute("SELECT * FROM sources WHERE source_key='x:karpathy'").fetchone()
    assert src["enabled"] == 0


def test_add_source_writes_to_db_and_yaml(db, tmp_path):
    yaml_path = tmp_path / "sources.yaml"
    _write_yaml(yaml_path, [])
    add_source(db, yaml_path, type="x", handle="lecun", config={"depth": "tweet"})
    src = db.execute("SELECT * FROM sources WHERE source_key='x:lecun'").fetchone()
    assert src is not None
    assert src["origin"] == "cli"
    # Check YAML was updated
    data = yaml.safe_load(yaml_path.read_text())
    handles = [s.get("handle") for s in data["sources"]]
    assert "lecun" in handles


def test_remove_source_disables_and_removes_from_yaml(db, tmp_path):
    yaml_path = tmp_path / "sources.yaml"
    _write_yaml(yaml_path, [{"type": "x", "handle": "karpathy"}])
    reconcile_sources(db, yaml_path)
    remove_source(db, yaml_path, "x:karpathy")
    src = db.execute("SELECT * FROM sources WHERE source_key='x:karpathy'").fetchone()
    assert src["enabled"] == 0
    data = yaml.safe_load(yaml_path.read_text())
    assert len(data["sources"]) == 0


def test_enable_source_clears_auto_disable(db, tmp_path):
    yaml_path = tmp_path / "sources.yaml"
    _write_yaml(yaml_path, [{"type": "x", "handle": "karpathy"}])
    reconcile_sources(db, yaml_path)
    db.execute("UPDATE sources SET enabled=0, disabled_reason='auto' WHERE source_key='x:karpathy'")
    db.commit()
    enable_source(db, "x:karpathy")
    src = db.execute("SELECT * FROM sources WHERE source_key='x:karpathy'").fetchone()
    assert src["enabled"] == 1
    assert src["disabled_reason"] is None
