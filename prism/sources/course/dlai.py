"""DeepLearning.AI (learn.deeplearning.ai) course provider.

DLAI short courses live on the HLS-backed video.deeplearning.ai infra. We do
not re-scrape lessons at sync time: transcripts and bilingual notes are
prepared offline (see docs/notes/). This provider just reads static course
metadata straight from the YAML config, so sync is fast, network-free, and
fully deterministic.

Expected YAML config fields:
  key:            explicit source_key (e.g. dlai:spec-driven-development)
  display_name:   course title shown to the user (required)
  course_url:     learn.deeplearning.ai page URL (required, used for dedup)
  author:         instructor name (optional)
  description:    1-3 sentence course blurb (optional)
  published_at:   ISO date, e.g. '2026-04-15' (optional)
  notes_path:     repo-relative or absolute path to bilingual notes (optional)
  partner:        co-producer tag on DLAI's CDN (optional, e.g. 'JetBrains')
  course_number:  DLAI's internal C{N} course number (optional)
  lessons:        list of {idx: int, title: str} — the course TOC (optional)
"""

from __future__ import annotations

from datetime import datetime, timezone

from prism.sources.course.base import CourseRef


class DlaiProvider:
    async def fetch(self, config: dict) -> CourseRef:
        display_name = (config.get("display_name") or "").strip()
        course_url = (config.get("course_url") or "").strip()

        if not display_name:
            raise ValueError("dlai course missing 'display_name'")
        if not course_url:
            raise ValueError("dlai course missing 'course_url'")

        published_at = _parse_date(config.get("published_at"))

        lessons_raw = config.get("lessons") or []
        lessons: list[dict] = []
        for entry in lessons_raw:
            if isinstance(entry, dict) and "title" in entry:
                lessons.append(
                    {"idx": entry.get("idx"), "title": str(entry["title"])}
                )

        extra = {
            k: config[k]
            for k in ("partner", "course_number", "lesson_count")
            if k in config
        }

        return CourseRef(
            course_url=course_url,
            title=display_name,
            author=str(config.get("author") or ""),
            description=str(config.get("description") or ""),
            published_at=published_at,
            lessons=lessons,
            notes_path=str(config.get("notes_path") or ""),
            extra=extra,
        )


def _parse_date(value) -> datetime | None:
    """Accept 'YYYY-MM-DD' or full ISO strings; return None on anything else."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
