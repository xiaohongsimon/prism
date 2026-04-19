"""Safe YAML editing for config/sources.yaml.

Uses ruamel.yaml to preserve comments and structure on round-trip.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 4096
    return y


def _source_key(entry: dict) -> str:
    """Build a canonical key to compare sources. Mirrors CLAUDE.md convention."""
    t = entry.get("type", "")
    if "handle" in entry:
        return f"{t}:{entry['handle']}"
    if "feed" in entry:
        return f"{t}:{entry['feed']}"
    if "url" in entry:
        return f"{t}:{entry['url']}"
    if "query" in entry:
        return f"{t}:query:{entry['query']}"
    return t


def load_sources_list(path: Path) -> list[dict[str, Any]]:
    """Return the `sources` list from a yaml file (mutation-safe copy)."""
    data = _yaml().load(Path(path).read_text())
    return [dict(item) for item in (data.get("sources") or [])]


def append_source_block(
    path: Path,
    source_config: dict[str, Any],
    category_comment: str = "",
) -> bool:
    """Append a source entry to `sources.yaml`. Returns True if appended,
    False if an entry with the same canonical key already existed."""
    y = _yaml()
    data = y.load(Path(path).read_text())
    seq = data["sources"]

    new_key = _source_key(source_config)
    for item in seq:
        if _source_key(item) == new_key:
            return False

    # Append preserving ordering
    from ruamel.yaml.comments import CommentedMap
    entry = CommentedMap(source_config)
    if category_comment:
        entry.yaml_set_start_comment(category_comment, indent=4)
    seq.append(entry)

    with Path(path).open("w") as f:
        y.dump(data, f)
    return True


def comment_out_source(path: Path, source_key: str, reason: str = "") -> bool:
    """Comment out the source entry matching `source_key` (e.g. 'hn:best').
    Preserves other entries and comments. Returns True if something was removed."""
    text = Path(path).read_text()
    y = _yaml()
    data = y.load(text)
    seq = data["sources"]

    target_idx = None
    for i, item in enumerate(seq):
        if _source_key(item) == source_key:
            target_idx = i
            break
    if target_idx is None:
        return False

    # Render the removed entry back into lines, prepend "# " to each
    target_entry = seq[target_idx]
    import io
    buf = io.StringIO()
    y.dump({"_removed": [target_entry]}, buf)
    removed_yaml = buf.getvalue()
    # Drop the wrapper line "_removed:" and dedent one level
    removed_lines = removed_yaml.splitlines()
    # Find the first "- " line to know indent and strip the wrapper
    body_lines = [ln for ln in removed_lines if not ln.startswith("_removed")]
    commented = "\n".join("# " + ln for ln in body_lines if ln.strip())
    header = f"# {reason}" if reason else "# pruned"

    del seq[target_idx]

    # Write yaml back, then append the commented block at the end of the file
    import io as _io
    out_buf = _io.StringIO()
    y.dump(data, out_buf)
    new_text = out_buf.getvalue().rstrip() + "\n\n" + header + "\n" + commented + "\n"
    Path(path).write_text(new_text)
    return True
