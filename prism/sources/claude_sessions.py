"""Claude sessions adapter — tracks Claude Code memory updates across projects.

For each configured memory_dir, globs for */memory/MEMORY.md files and creates
a RawItem per project containing the full MEMORY.md content. Deduplication is
handled by the existing source_id+url UNIQUE constraint in the DB.
"""

import json
import logging
from datetime import date
from pathlib import Path

from prism.models import RawItem
from prism.sources.base import SyncResult

logger = logging.getLogger(__name__)


def _read_memory_file(memory_file: Path) -> str | None:
    """Read and return content of a MEMORY.md file, or None on failure."""
    try:
        return memory_file.read_text(encoding="utf-8").strip()
    except OSError as e:
        logger.warning("claude_sessions: cannot read %s: %s", memory_file, e)
        return None


def _project_name_from_path(memory_file: Path) -> str:
    """Extract project name from .../projects/{project_name}/memory/MEMORY.md."""
    # memory_file.parent is the memory/ dir, parent of that is project dir
    return memory_file.parent.parent.name


def sync_memory_dir(memory_dir: str) -> list[RawItem]:
    """Scan a memory_dir for MEMORY.md files and produce RawItems."""
    base = Path(memory_dir)
    if not base.exists():
        logger.warning("claude_sessions: memory_dir does not exist: %s", memory_dir)
        return []

    items: list[RawItem] = []
    today = date.today().isoformat()

    # Glob pattern: {memory_dir}/*/memory/MEMORY.md
    memory_files = sorted(base.glob("*/memory/MEMORY.md"))

    if not memory_files:
        logger.debug("claude_sessions: no MEMORY.md files found in %s", memory_dir)
        return []

    for memory_file in memory_files:
        content = _read_memory_file(memory_file)
        if content is None:
            continue
        if not content:
            logger.debug("claude_sessions: empty MEMORY.md at %s, skipping", memory_file)
            continue

        project_name = _project_name_from_path(memory_file)
        url = f"claude:{project_name}:{today}"
        title = f"[Practice] Claude Code memory update — {project_name}"

        raw_json = json.dumps(
            {
                "project_name": project_name,
                "memory_file": str(memory_file),
                "date": today,
                "content_length": len(content),
            },
            ensure_ascii=False,
        )

        items.append(
            RawItem(
                url=url,
                title=title,
                body=content,
                author="",
                raw_json=raw_json,
            )
        )

    return items


class ClaudeSessionsAdapter:
    """Source adapter that ingests Claude Code MEMORY.md files as daily items."""

    async def sync(self, config: dict) -> SyncResult:
        """Read MEMORY.md files from configured directories and produce RawItems.

        Config keys:
            key (str): source key used in SyncResult
            memory_dirs (list[str]): directories containing project memory files
        """
        source_key = config.get("key", "practice:claude")
        memory_dirs: list[str] = config.get("memory_dirs", [])

        items: list[RawItem] = []

        for memory_dir in memory_dirs:
            dir_items = sync_memory_dir(memory_dir)
            items.extend(dir_items)

        logger.info(
            "claude_sessions: %d dirs → %d items",
            len(memory_dirs),
            len(items),
        )
        return SyncResult(
            source_key=source_key,
            items=items,
            success=True,
            stats={"dirs": len(memory_dirs), "items": len(items)},
        )
