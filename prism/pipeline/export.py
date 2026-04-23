"""EPUB export of /feed/following content for offline reading.

Query enabled sources in FOLLOW_SOURCE_TYPES, pull raw_items from the last
N days (LEFT JOIN articles), bundle per-source chapters into an EPUB the
user can sideload to iOS Books.app / 微信读书 for flights.

No LLM calls. Pure DB → EPUB.
"""

from __future__ import annotations

import html as _html
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml as _yaml
from ebooklib import epub


# Mirror prism.web.ranking.FOLLOW_SOURCE_TYPES. Duplicated to avoid a
# pipeline → web import dependency.
FOLLOW_SOURCE_TYPES = {
    "x", "youtube", "follow_builders", "github_releases",
    "xiaoyuzhou", "course",
}

# EPUB section titles, in reading order.
SECTION_TITLES = {
    "youtube": "📺 YouTube",
    "xiaoyuzhou": "🎙 小宇宙",
    "course": "🎓 课程",
    "x": "𝕏 X",
    "follow_builders": "𝕏 Builders",
    "github_releases": "📦 GitHub Releases",
    "other": "🔧 其他",
}
SECTION_ORDER = [
    "youtube", "xiaoyuzhou", "course",
    "x", "follow_builders", "github_releases", "other",
]


@dataclass
class ExportItem:
    title: str
    url: str
    author: str
    published_at: str
    body: str              # prefer body_zh, fall back to body
    body_en: str           # original body (for dual-language display)
    article_body: str      # articles.structured_body if available


@dataclass
class SourceBundle:
    source_key: str
    source_type: str
    display_name: str
    handle: str
    items: list[ExportItem] = field(default_factory=list)


# ── helpers ──────────────────────────────────────────────────────────────────


def _section_key(src_type: str) -> str:
    return src_type if src_type in SECTION_TITLES else "other"


def _display_name(source_row) -> str:
    config = {}
    if source_row["config_yaml"]:
        try:
            config = _yaml.safe_load(source_row["config_yaml"]) or {}
        except Exception:
            config = {}
    return (
        config.get("display_name")
        or source_row["handle"]
        or source_row["source_key"]
    )


def gather_items(
    conn: sqlite3.Connection, days: int = 7
) -> dict[str, list[SourceBundle]]:
    """Pull followed-source raw_items from last N days, grouped per section."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).strftime("%Y-%m-%d %H:%M:%S")

    placeholders = ",".join("?" * len(FOLLOW_SOURCE_TYPES))
    sources = conn.execute(
        f"""SELECT id, source_key, type, handle, config_yaml
            FROM sources
            WHERE enabled = 1 AND type IN ({placeholders})
            ORDER BY type, source_key""",
        list(FOLLOW_SOURCE_TYPES),
    ).fetchall()

    sections: dict[str, list[SourceBundle]] = {k: [] for k in SECTION_ORDER}

    for src in sources:
        rows = conn.execute(
            """SELECT ri.title, ri.body, ri.body_zh, ri.url, ri.author,
                      ri.published_at, ri.created_at, a.structured_body
               FROM raw_items ri
               LEFT JOIN articles a ON a.raw_item_id = ri.id
               WHERE ri.source_id = ? AND ri.created_at >= ?
               ORDER BY ri.created_at DESC""",
            (src["id"], cutoff),
        ).fetchall()
        if not rows:
            continue

        items: list[ExportItem] = []
        for r in rows:
            zh = (r["body_zh"] or "").strip()
            en = (r["body"] or "").strip()
            items.append(
                ExportItem(
                    title=(r["title"] or "").strip(),
                    url=r["url"] or "",
                    author=r["author"] or "",
                    published_at=r["published_at"] or r["created_at"] or "",
                    body=zh or en,
                    body_en=en,
                    article_body=(r["structured_body"] or "").strip(),
                )
            )

        sections[_section_key(src["type"])].append(
            SourceBundle(
                source_key=src["source_key"],
                source_type=src["type"],
                display_name=_display_name(src),
                handle=src["handle"] or "",
                items=items,
            )
        )
    return sections


def _md_to_html(md_text: str) -> str:
    """Render markdown (e.g. articles.structured_body) to HTML for EPUB."""
    import markdown as _md

    return _md.markdown(md_text, extensions=["extra", "nl2br"])


def _render_item_html(item: ExportItem) -> str:
    esc = _html.escape
    title = esc(item.title or "(无标题)")

    meta_parts: list[str] = []
    if item.author:
        meta_parts.append(f"作者: {esc(item.author)}")
    if item.published_at:
        meta_parts.append(f"时间: {esc(item.published_at[:16])}")
    if item.url:
        meta_parts.append(f'<a href="{esc(item.url)}">原文链接</a>')
    meta = " · ".join(meta_parts)

    if item.article_body:
        body_html = _md_to_html(item.article_body)
    else:
        body = item.body or ""
        body_html = "<p>" + esc(body).replace("\n", "<br/>") + "</p>"
        # When translation differs from original, append original at bottom.
        if item.body and item.body_en and item.body != item.body_en:
            body_html += (
                '<hr/><p class="orig-label">原文：</p><p>'
                + esc(item.body_en).replace("\n", "<br/>")
                + "</p>"
            )

    return (
        '<article class="prism-item">'
        f"<h3>{title}</h3>"
        f'<p class="prism-meta">{meta}</p>'
        f"{body_html}"
        "</article>"
    )


def _render_source_chapter(bundle: SourceBundle) -> str:
    esc = _html.escape
    head = f"<h2>{esc(bundle.display_name)}</h2>"
    handle_line = (
        f'<p class="prism-meta">@{esc(bundle.handle)} · {esc(bundle.source_key)} · '
        f"{len(bundle.items)} 条</p>"
    )
    items_html = "\n".join(_render_item_html(it) for it in bundle.items)
    return f"<section>{head}{handle_line}{items_html}</section>"


CSS = """
body { font-family: serif; line-height: 1.65; padding: 0 0.5em; }
h1 { color: #1a1a1a; }
h2 { color: #333; border-bottom: 1px solid #ccc; padding-bottom: 0.3em; }
h3 { color: #111; margin-top: 1.5em; }
.prism-meta { color: #888; font-size: 0.9em; }
.orig-label { color: #aaa; font-size: 0.85em; margin-top: 1em; }
article.prism-item {
    margin-bottom: 2em;
    padding-bottom: 1em;
    border-bottom: 1px dashed #ddd;
}
section { margin-bottom: 3em; }
a { color: #2060c0; text-decoration: none; }
hr { border: 0; border-top: 1px solid #eee; margin: 1em 0; }
"""


def build_epub(conn: sqlite3.Connection, days: int = 7) -> bytes:
    """Generate an EPUB in memory and return its bytes."""
    sections = gather_items(conn, days=days)
    today = datetime.now().strftime("%Y-%m-%d")

    book = epub.EpubBook()
    book.set_identifier(f"prism-{today}-{days}d")
    book.set_title(f"Prism 订阅 · 近 {days} 天 · {today}")
    book.set_language("zh")
    book.add_author("Prism")

    css = epub.EpubItem(
        uid="style_default",
        file_name="style/default.css",
        media_type="text/css",
        content=CSS,
    )
    book.add_item(css)

    chapters: list[epub.EpubHtml] = []
    toc: list = []
    total_items = 0
    total_sources = 0

    for section_key in SECTION_ORDER:
        bundles = sections.get(section_key, [])
        section_chapters: list[epub.EpubHtml] = []
        for idx, bundle in enumerate(bundles):
            if not bundle.items:
                continue
            chapter_id = f"{section_key}_{idx:03d}"
            chapter = epub.EpubHtml(
                title=bundle.display_name,
                file_name=f"{chapter_id}.xhtml",
                lang="zh",
            )
            chapter.content = (
                f"<html><head><title>{_html.escape(bundle.display_name)}</title>"
                '<link rel="stylesheet" type="text/css" href="style/default.css"/>'
                f"</head><body>{_render_source_chapter(bundle)}</body></html>"
            )
            chapter.add_item(css)
            book.add_item(chapter)
            chapters.append(chapter)
            section_chapters.append(chapter)
            total_items += len(bundle.items)
            total_sources += 1
        if section_chapters:
            toc.append(
                (epub.Section(SECTION_TITLES[section_key]), tuple(section_chapters))
            )

    intro = epub.EpubHtml(title="说明", file_name="intro.xhtml", lang="zh")
    intro.content = (
        "<html><head><title>Prism 订阅</title>"
        '<link rel="stylesheet" type="text/css" href="style/default.css"/>'
        "</head><body>"
        "<h1>Prism 订阅离线版</h1>"
        f"<p>生成时间：{today}</p>"
        f"<p>内容范围：近 {days} 天</p>"
        f"<p>源 / 条目：{total_sources} 个源 · {total_items} 条</p>"
        "<p><em>按源分章节；每章按时间倒序排列。原文链接在每条标题下方。</em></p>"
        "</body></html>"
    )
    intro.add_item(css)
    book.add_item(intro)

    book.toc = tuple([intro] + toc)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", intro] + chapters

    # ebooklib only writes to a file path — round-trip through a tempfile.
    with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tf:
        tmp_path = Path(tf.name)
    try:
        epub.write_epub(str(tmp_path), book)
        return tmp_path.read_bytes()
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def export_epub_to_file(
    conn: sqlite3.Connection, days: int, out_path: Path
) -> int:
    """Write EPUB to disk; return byte size."""
    data = build_epub(conn, days=days)
    out_path.write_bytes(data)
    return len(data)


def default_filename(days: int) -> str:
    return f"prism-{datetime.now().strftime('%Y%m%d')}-{days}d.epub"
