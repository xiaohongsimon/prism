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
    conn: sqlite3.Connection,
    days: int = 7,
    per_source_cap: int = 15,
) -> dict[str, list[SourceBundle]]:
    """Pull followed-source raw_items from last N days, grouped per section.

    `per_source_cap` limits how many items to include per source (newest
    first). Needed because 微信读书 / iOS Books chug on >500-chapter EPUBs.
    Pass 0 to disable.
    """
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
        limit_clause = ""
        params: list = [src["id"], cutoff]
        if per_source_cap and per_source_cap > 0:
            limit_clause = " LIMIT ?"
            params.append(per_source_cap)
        rows = conn.execute(
            f"""SELECT ri.title, ri.body, ri.body_zh, ri.url, ri.author,
                       ri.published_at, ri.created_at, a.structured_body
                FROM raw_items ri
                LEFT JOIN articles a ON a.raw_item_id = ri.id
                WHERE ri.source_id = ? AND ri.created_at >= ?
                ORDER BY ri.created_at DESC{limit_clause}""",
            params,
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


def _item_title(item: ExportItem) -> str:
    """Chapter title: prefer explicit title, fall back to body excerpt."""
    if item.title:
        return item.title.strip()[:80]
    body = (item.body or item.body_en or "").strip()
    if not body:
        return "(无标题)"
    # Take first sentence or first ~40 chars, whichever is shorter.
    snippet = body.split("\n", 1)[0]
    for sep in ("。", "！", "？", ". ", "! ", "? "):
        if sep in snippet[:60]:
            snippet = snippet.split(sep, 1)[0] + sep.strip()
            break
    return snippet[:60].strip()


def _truncate_body(body: str, max_chars: int) -> tuple[str, bool]:
    """Truncate body text at a paragraph boundary. Returns (text, truncated)."""
    if max_chars <= 0 or len(body) <= max_chars:
        return body, False
    cut = body[:max_chars]
    # Back off to the last newline so we don't split mid-paragraph.
    nl = cut.rfind("\n")
    if nl > max_chars * 0.6:
        cut = cut[:nl]
    return cut, True


def _render_item_page(
    item: ExportItem,
    bundle: SourceBundle,
    max_chars: int = 40000,
) -> str:
    """Full standalone HTML page for a single item — each becomes its own
    EPUB chapter so microsoft readers / 微信读书 can jump straight to it.

    <h1> is used so that readers that auto-detect chapter boundaries
    (including 微信读书) pick up each item as a navigable unit.

    `max_chars` caps raw body length before rendering — 微信读书 freezes on
    huge chapters. Truncated bodies get a "查看完整内容 → 原文链接" footer.
    Pass 0 to disable truncation.
    """
    esc = _html.escape
    title = esc(_item_title(item))

    meta_parts: list[str] = []
    meta_parts.append(esc(bundle.display_name))
    if bundle.handle and bundle.handle != bundle.display_name:
        meta_parts.append(f"@{esc(bundle.handle)}")
    if item.published_at:
        meta_parts.append(esc(item.published_at[:16]))
    if item.url:
        meta_parts.append(f'<a href="{esc(item.url)}">原文</a>')
    meta = " · ".join(meta_parts)

    truncated = False
    if item.article_body:
        src_text, truncated = _truncate_body(item.article_body, max_chars)
        body_html = _md_to_html(src_text)
    else:
        body = item.body or ""
        body, truncated = _truncate_body(body, max_chars)
        paragraphs = [p for p in body.split("\n") if p.strip()]
        body_html = "\n".join(f"<p>{esc(p)}</p>" for p in paragraphs) or "<p></p>"
        # Dual-language: append original under a divider if translation differs,
        # but only if we still have room — skip EN half when the zh body was
        # already truncated to keep chapter size sane.
        if (
            not truncated
            and item.body
            and item.body_en
            and item.body != item.body_en
        ):
            en_body, en_trunc = _truncate_body(item.body_en, max_chars)
            en_paras = [p for p in en_body.split("\n") if p.strip()]
            en_html = "\n".join(f"<p>{esc(p)}</p>" for p in en_paras)
            body_html += (
                '<hr class="orig-divider"/>'
                '<p class="orig-label">原文 Original</p>'
                f"{en_html}"
            )
            if en_trunc:
                truncated = True

    if truncated:
        link = (
            f'<a href="{esc(item.url)}">原文链接</a>' if item.url else "原文"
        )
        body_html += (
            '<p class="prism-trunc">（内容过长已截断，完整内容见 '
            f"{link}）</p>"
        )

    return (
        f"<html><head><title>{title}</title>"
        '<link rel="stylesheet" type="text/css" href="style/default.css"/>'
        "</head><body>"
        f'<h1 class="prism-title">{title}</h1>'
        f'<p class="prism-meta">{meta}</p>'
        f'<div class="prism-body">{body_html}</div>'
        "</body></html>"
    )


# Readers like 微信读书 / iOS Books.app honor <h1> as the chapter anchor and
# apply their own typography below. Keep CSS conservative so it doesn't
# fight the reader — just tune spacing + muted meta.
CSS = """
body {
    font-family: "PingFang SC", "Noto Serif CJK SC", "Songti SC", serif;
    line-height: 1.85;
    padding: 1em 0.5em;
    color: #1a1a1a;
}
h1.prism-title {
    font-size: 1.6em;
    font-weight: 600;
    margin: 0.6em 0 0.4em 0;
    line-height: 1.35;
    color: #111;
}
p.prism-meta {
    color: #888;
    font-size: 0.85em;
    margin: 0 0 1.8em 0;
    padding-bottom: 0.8em;
    border-bottom: 1px solid #e5e5e5;
}
p.prism-meta a { color: #888; }
.prism-body p { margin: 0.9em 0; text-indent: 0; }
.prism-body h2 { font-size: 1.2em; margin: 1.5em 0 0.5em; color: #222; }
.prism-body h3 { font-size: 1.05em; margin: 1.2em 0 0.4em; color: #333; }
.prism-body blockquote {
    border-left: 3px solid #d0d0d0;
    padding-left: 1em;
    color: #555;
    margin: 1em 0;
}
.prism-body ul, .prism-body ol { margin: 0.8em 0; padding-left: 1.6em; }
.prism-body li { margin: 0.3em 0; }
.prism-body code {
    background: #f2f2f2; padding: 1px 4px; border-radius: 3px;
    font-family: "SF Mono", Consolas, monospace; font-size: 0.9em;
}
.prism-body pre {
    background: #f6f6f6; padding: 0.8em; border-radius: 4px;
    overflow-x: auto; font-size: 0.88em;
}
.orig-divider { border: 0; border-top: 1px dashed #ddd; margin: 2em 0 1em; }
.orig-label { color: #aaa; font-size: 0.82em; margin: 0 0 0.5em 0; }
.prism-trunc { color: #aaa; font-size: 0.85em; margin-top: 1.6em;
    padding-top: 0.8em; border-top: 1px dashed #e5e5e5; }
a { color: #2060c0; text-decoration: none; }
"""


def build_epub(
    conn: sqlite3.Connection,
    days: int = 7,
    per_source_cap: int = 15,
    max_chars: int = 40000,
) -> bytes:
    """Generate an EPUB with per-item chapters and a 3-level TOC:
    大类（YouTube/X/...） → 博主 → 单条文章.

    `per_source_cap` limits items per source (newest first).
    `max_chars` caps raw body length per chapter — both guard against
    reader freezes on extremely large inputs. Pass 0 to disable either.
    """
    sections = gather_items(conn, days=days, per_source_cap=per_source_cap)
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

    all_chapters: list[epub.EpubHtml] = []
    toc_top: list = []
    total_items = 0
    total_sources = 0

    for sec_idx, section_key in enumerate(SECTION_ORDER):
        bundles = sections.get(section_key, [])
        section_bundle_entries: list = []

        for b_idx, bundle in enumerate(bundles):
            if not bundle.items:
                continue

            bundle_chapters: list[epub.EpubHtml] = []
            for i_idx, item in enumerate(bundle.items):
                chapter_id = f"{sec_idx:02d}_{b_idx:03d}_{i_idx:03d}"
                chapter = epub.EpubHtml(
                    title=_item_title(item),
                    file_name=f"ch_{chapter_id}.xhtml",
                    lang="zh",
                )
                chapter.content = _render_item_page(
                    item, bundle, max_chars=max_chars
                )
                chapter.add_item(css)
                book.add_item(chapter)
                all_chapters.append(chapter)
                bundle_chapters.append(chapter)

            if bundle_chapters:
                # Nest: Section(博主) → list of item chapters
                section_bundle_entries.append(
                    (epub.Section(bundle.display_name), tuple(bundle_chapters))
                )
                total_items += len(bundle_chapters)
                total_sources += 1

        if section_bundle_entries:
            # Outer: Section(大类) → list of bundle entries
            toc_top.append(
                (epub.Section(SECTION_TITLES[section_key]),
                 tuple(section_bundle_entries))
            )

    intro = epub.EpubHtml(title="说明", file_name="intro.xhtml", lang="zh")
    intro.content = (
        "<html><head><title>Prism 订阅</title>"
        '<link rel="stylesheet" type="text/css" href="style/default.css"/>'
        "</head><body>"
        '<h1 class="prism-title">Prism 订阅离线版</h1>'
        f'<p class="prism-meta">{today} · 近 {days} 天 · '
        f"{total_sources} 个源 · {total_items} 条</p>"
        '<div class="prism-body">'
        "<p>按渠道 → 博主 → 单条内容三级目录导航，点目录可直接跳到任一条。</p>"
        "<p>每条内容独立成章，方便微信读书/iOS Books 等阅读器的翻页和进度同步。</p>"
        "</div>"
        "</body></html>"
    )
    intro.add_item(css)
    book.add_item(intro)

    book.toc = tuple([intro] + toc_top)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", intro] + all_chapters

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
    conn: sqlite3.Connection,
    days: int,
    out_path: Path,
    per_source_cap: int = 15,
    max_chars: int = 40000,
) -> int:
    """Write EPUB to disk; return byte size."""
    data = build_epub(
        conn,
        days=days,
        per_source_cap=per_source_cap,
        max_chars=max_chars,
    )
    out_path.write_bytes(data)
    return len(data)


def default_filename(days: int) -> str:
    return f"prism-{datetime.now().strftime('%Y%m%d')}-{days}d.epub"
