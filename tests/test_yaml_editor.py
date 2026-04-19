from pathlib import Path
import textwrap

from prism.sources.yaml_editor import (
    append_source_block,
    comment_out_source,
    load_sources_list,
)


SAMPLE_YAML = textwrap.dedent("""\
    sources:
      # Existing section
      - type: x
        handle: karpathy
        display_name: "Andrej Karpathy"
        depth: thread

      - type: hn
        feed: best
        display_name: "HN Best"
""")


def test_append_source_block_preserves_existing(tmp_path: Path):
    p = tmp_path / "sources.yaml"
    p.write_text(SAMPLE_YAML)

    append_source_block(
        p,
        source_config={
            "type": "x",
            "handle": "zarazhangrui",
            "display_name": "Zara Zhang Rui",
            "depth": "thread",
        },
        category_comment="persona-proposed 2026-04-19",
    )

    text = p.read_text()
    assert "handle: karpathy" in text
    assert "handle: zarazhangrui" in text
    assert "persona-proposed 2026-04-19" in text
    items = load_sources_list(p)
    assert len(items) == 3
    assert any(i.get("handle") == "zarazhangrui" for i in items)


def test_comment_out_source_by_key(tmp_path: Path):
    p = tmp_path / "sources.yaml"
    p.write_text(SAMPLE_YAML)

    removed = comment_out_source(p, source_key="hn:best", reason="weight=-10 pruned 2026-04-19")
    assert removed is True

    text = p.read_text()
    # The hn block should be commented out with the reason nearby
    assert "# pruned 2026-04-19" in text or "# weight=-10 pruned 2026-04-19" in text
    assert "#   - type: hn" in text or "# - type: hn" in text

    items = load_sources_list(p)
    assert not any(i.get("feed") == "best" and i.get("type") == "hn" for i in items)


def test_append_idempotent_on_duplicate_key(tmp_path: Path):
    p = tmp_path / "sources.yaml"
    p.write_text(SAMPLE_YAML)

    first = append_source_block(
        p, source_config={"type": "x", "handle": "karpathy", "display_name": "x", "depth": "thread"}
    )
    assert first is False  # already present, no change

    items = load_sources_list(p)
    assert sum(1 for i in items if i.get("handle") == "karpathy") == 1
