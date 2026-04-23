"""Course source base types and dispatcher.

One course = one signal. The adapter looks at `config['provider']` and routes
to the matching provider (dlai, coursera, ...). Each provider returns a
CourseRef that the adapter packages into a single RawItem.

Idempotent by design: the RawItem's URL is the course page URL, and
insert_raw_item dedups on (source_id, url) — re-syncing the same course is
a no-op unless metadata changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Protocol

from prism.models import RawItem
from prism.sources.base import SyncResult


@dataclass
class CourseRef:
    """Normalized course metadata returned by a provider."""

    course_url: str
    title: str
    author: str = ""
    description: str = ""
    published_at: Optional[datetime] = None
    # Each lesson: {"idx": int, "title": str, "duration_sec": int?, "url": str?}
    lessons: list[dict] = field(default_factory=list)
    # Path (absolute or repo-relative) to bilingual / organized notes, if any
    notes_path: str = ""
    # Free-form provider extras (partner, course_number, ...) — serialized into raw_json
    extra: dict = field(default_factory=dict)


class CourseProvider(Protocol):
    async def fetch(self, config: dict) -> CourseRef: ...


def _build_body(ref: CourseRef) -> str:
    """Assemble a compact, human-readable body: description + TOC + notes link."""
    parts: list[str] = []
    if ref.description:
        parts.append(ref.description.strip())

    if ref.lessons:
        parts.append("")
        parts.append("Lessons:")
        for lesson in ref.lessons:
            idx = lesson.get("idx")
            title = lesson.get("title", "")
            prefix = f"  {idx}. " if idx is not None else "  - "
            parts.append(f"{prefix}{title}".rstrip())

    if ref.notes_path:
        parts.append("")
        parts.append(f"Notes: {ref.notes_path}")

    return "\n".join(parts).strip()


class CourseAdapter:
    """Dispatches to a CourseProvider based on `config['provider']`."""

    async def sync(self, config: dict) -> SyncResult:
        source_key = config.get("source_key", "")
        provider_name = (config.get("provider") or "").strip().lower()

        try:
            provider = _get_provider(provider_name)
        except ValueError as exc:
            return SyncResult(
                source_key=source_key, items=[], success=False, error=str(exc)
            )

        try:
            ref = await provider.fetch(config)
        except Exception as exc:  # provider errors surface as soft failures
            return SyncResult(
                source_key=source_key, items=[], success=False, error=str(exc)
            )

        raw_json = json.dumps(
            {
                "provider": provider_name,
                "notes_path": ref.notes_path,
                "lessons": ref.lessons,
                **ref.extra,
            },
            ensure_ascii=False,
        )

        item = RawItem(
            url=ref.course_url,
            title=ref.title,
            body=_build_body(ref),
            author=ref.author,
            published_at=ref.published_at,
            raw_json=raw_json,
        )

        return SyncResult(source_key=source_key, items=[item], success=True)


def _get_provider(name: str) -> CourseProvider:
    """Lazy-load providers to avoid import cycles and keep optional deps local."""
    if name == "dlai":
        from prism.sources.course.dlai import DlaiProvider

        return DlaiProvider()
    raise ValueError(f"Unknown course provider: {name!r}")
