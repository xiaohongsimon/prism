"""xiaoyuzhou episode queue — incremental backfill for xyz:* sources.

State machine per episode (xyz_episode_queue.status):
    pending → transcribed → inserted → done

`discover(conn, source_config)` — scan enabled xyz:* sources, fetch last-30d
    episode list from xiaoyuzhou, upsert rows with status='pending'.

`tick(conn)` — advance ONE episode by ONE step if omlx load is light:
    articlize (LLM-heavy) > insert (cheap) > download+ASR (Metal GPU, single-tenant).
    Picks newest-pubDate episode within each stage ("依次由近到远").

`status(conn)` — print per-status counts.

Called by `prism xyz-queue` CLI and launchd script.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional
from urllib.request import Request, urlopen

from prism.pipeline.llm_tasks import Scope, Task

import yaml

from prism.config import settings

log = logging.getLogger(__name__)

ROOT = Path("/Users/leehom/work/prism")
AUDIO_DIR = ROOT / "tmp" / "xyz_audio"
TRANS_DIR = ROOT / "tmp" / "xyz_transcripts"
ASR_BIN = Path(os.path.expanduser("~/.hotmic/venv/bin/mlx-qwen3-asr"))
BUILD_ID = "0FY8QORlKjMrVXC8qo9sc"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
BACKFILL_DAYS = 120
MAX_ATTEMPTS = 3
ASR_MODEL = "Qwen/Qwen3-ASR-1.7B"

# Articlize params. NOTE: gemma-4-* and Qwen3.6-35B in this omlx deploy are
# reasoning-only (content=null, reasoning_content holds everything) so they're
# unusable for JSON-returning pipelines. Qwen3-Coder-Next is the only 8bit
# non-thinking model currently loaded that returns content normally.
ARTICLIZE_MODEL = os.getenv("XYZ_QUEUE_MODEL", "Qwen3-Coder-Next-MLX-8bit")
CHUNK_CHARS = 5500
CHUNK_OVERLAP = 250


# ───────────────────────── discover ─────────────────────────

def _slugify(eid: str, title: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in title)[:40]
    return f"{eid}_{safe}".rstrip("_")


def _fetch_podcast(pid: str) -> dict:
    url = f"https://www.xiaoyuzhoufm.com/_next/data/{BUILD_ID}/podcast/{pid}.json"
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=30) as r:
        return json.load(r)


def _xyz_sources_from_config(source_config_path: Path) -> list[dict]:
    raw = yaml.safe_load(source_config_path.read_text(encoding="utf-8"))
    out = []
    for entry in raw.get("sources", []):
        if entry.get("type") != "xiaoyuzhou":
            continue
        if entry.get("enabled") is False:
            continue
        pid = entry.get("pid")
        key = entry.get("key")
        if not pid or not key:
            continue
        out.append({
            "source_key": key,
            "pid": pid,
            "display_name": entry.get("display_name") or key,
        })
    return out


def discover(conn: sqlite3.Connection, source_config_path: Optional[Path] = None) -> dict[str, int]:
    """Enqueue last-30d episodes from every enabled xyz:* source."""
    source_config_path = source_config_path or settings.source_config
    pods = _xyz_sources_from_config(source_config_path)
    cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=BACKFILL_DAYS)
    added = 0
    seen = 0
    for pod in pods:
        try:
            data = _fetch_podcast(pod["pid"])
        except Exception as e:
            log.warning("discover: fetch %s failed: %s", pod["source_key"], e)
            continue
        page = data.get("pageProps", {}).get("podcast", {}) or {}
        episodes = page.get("episodes", []) or []
        for ep in episodes:
            pd = ep.get("pubDate")
            if not pd:
                continue
            try:
                t = dt.datetime.fromisoformat(pd.replace("Z", "+00:00"))
            except ValueError:
                continue
            if t < cut:
                continue
            eid = ep.get("eid")
            title = ep.get("title") or ""
            audio_url = (ep.get("enclosure") or {}).get("url") or ""
            if not eid or not audio_url:
                continue
            seen += 1
            stem = _slugify(eid, title)
            row = conn.execute("SELECT eid FROM xyz_episode_queue WHERE eid=?", (eid,)).fetchone()
            if row:
                continue
            conn.execute(
                """INSERT INTO xyz_episode_queue
                   (eid, source_key, pid, title, pub_date, duration_sec, audio_url, stem, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (eid, pod["source_key"], pod["pid"], title, pd,
                 ep.get("duration"), audio_url, stem),
            )
            added += 1
    conn.commit()
    return {"sources": len(pods), "seen": seen, "added": added}


# ───────────────────────── load check ─────────────────────────

def _pgrep(pattern: str) -> bool:
    """True if a process matching pattern is running (excluding this one)."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True, timeout=5,
        )
        pids = [p for p in result.stdout.split() if p and int(p) != os.getpid()]
        return bool(pids)
    except Exception:
        return False


def _asr_busy() -> bool:
    return _pgrep("mlx-qwen3-asr")


def _llm_busy() -> bool:
    """Is omlx under active concurrent load from another prism job?"""
    # articlize runners we know about
    return _pgrep("prism.*articlize") or _pgrep("xyz_articlize") or _pgrep("xyz_batch_articlize")


def _omlx_reachable() -> bool:
    """Best-effort TCP reachability — omlx accepts a quick POST probe.
    Kept permissive: if we can't tell, assume reachable and let the real
    call_llm raise if it truly isn't.
    """
    import socket
    from urllib.parse import urlparse
    base = settings.llm_base_url.rstrip("/")
    if not base:
        return False
    try:
        u = urlparse(base)
        host = u.hostname or "127.0.0.1"
        port = u.port or (443 if u.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=2):
            return True
    except Exception:
        return False


# ───────────────────────── tick ─────────────────────────

def _set_status(conn, eid: str, status: str, *, error: Optional[str] = None,
                article_id: Optional[int] = None, bump_attempts: bool = False) -> None:
    cols = ["status=?", "updated_at=datetime('now')"]
    vals: list[Any] = [status]
    if error is not None:
        cols.append("error=?")
        vals.append(error)
    else:
        cols.append("error=NULL")
    if article_id is not None:
        cols.append("article_id=?")
        vals.append(article_id)
    if bump_attempts:
        cols.append("attempts=attempts+1")
    if status == "done":
        cols.append("done_at=datetime('now')")
    vals.append(eid)
    conn.execute(f"UPDATE xyz_episode_queue SET {', '.join(cols)} WHERE eid=?", vals)
    conn.commit()


def _pick(conn, status: str) -> Optional[sqlite3.Row]:
    """Pick next episode for `status`, round-robin across sources.

    For each source, use MAX(updated_at) as its "last touched" timestamp
    (sources never touched sort first). Among candidates in `status`, prefer
    the source that has been idle longest; within that source take the
    newest pub_date ("依次由近到远").
    """
    return conn.execute(
        """WITH last_touch AS (
             SELECT source_key,
                    COALESCE(MAX(updated_at), '1970-01-01') AS last_ts
             FROM xyz_episode_queue
             GROUP BY source_key
           )
           SELECT q.* FROM xyz_episode_queue q
           LEFT JOIN last_touch lt ON lt.source_key = q.source_key
           WHERE q.status = ? AND q.attempts < ?
           ORDER BY COALESCE(lt.last_ts, '1970-01-01') ASC,
                    q.pub_date DESC
           LIMIT 1""",
        (status, MAX_ATTEMPTS),
    ).fetchone()


def tick(conn: sqlite3.Connection) -> str:
    """Advance ONE episode by ONE stage. Returns a short status string."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    TRANS_DIR.mkdir(parents=True, exist_ok=True)

    # Stage 3: articlize (LLM, uses omlx)
    if not _llm_busy() and _omlx_reachable():
        row = _pick(conn, "inserted")
        if row:
            return _do_articlize(conn, row)

    # Stage 2: insert (cheap, no GPU/LLM)
    row = _pick(conn, "transcribed")
    if row:
        return _do_insert(conn, row)

    # Stage 1: download + ASR (Metal GPU, single-tenant)
    if not _asr_busy():
        row = _pick(conn, "pending")
        if row:
            return _do_download_asr(conn, row)

    return "idle"


# ───────────────────────── stage handlers ─────────────────────────

def _download(url: str, dst: Path) -> bool:
    if dst.exists() and dst.stat().st_size > 0:
        return True
    try:
        req = Request(url, headers={"User-Agent": UA})
        with urlopen(req, timeout=180) as r, dst.open("wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
        return dst.exists() and dst.stat().st_size > 0
    except Exception as e:
        log.warning("download failed %s: %s", dst.name, e)
        if dst.exists():
            dst.unlink()
        return False


def _do_download_asr(conn, row: sqlite3.Row) -> str:
    eid = row["eid"]
    stem = row["stem"]
    url = row["audio_url"]
    ext = url.rsplit(".", 1)[-1].split("?", 1)[0] or "m4a"
    audio = AUDIO_DIR / f"{stem}.{ext}"
    txt = TRANS_DIR / f"{stem}.txt"

    if txt.exists() and txt.stat().st_size > 0:
        _set_status(conn, eid, "transcribed")
        return f"transcribed (cached) {stem}"

    if not _download(url, audio):
        _set_status(conn, eid, "pending", error="download failed", bump_attempts=True)
        return f"fail download {stem}"

    ctx = f"{row['source_key']} {row['title']}"[:200]
    cmd = [
        str(ASR_BIN),
        "--model", ASR_MODEL,
        "--language", "Chinese",
        "--output-format", "all",
        "--context", ctx,
        "--output-dir", str(TRANS_DIR),
        str(audio),
    ]
    env = {**os.environ, "HF_ENDPOINT": "https://hf-mirror.com"}
    log.info("ASR start %s", stem)
    t0 = time.time()
    try:
        rc = subprocess.call(cmd, env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        _set_status(conn, eid, "pending", error=f"ASR binary missing: {ASR_BIN}", bump_attempts=True)
        return f"fail asr-missing {stem}"
    elapsed = int(time.time() - t0)

    produced = TRANS_DIR / f"{audio.stem}.txt"
    if produced.exists() and produced != txt:
        produced.replace(txt)
    if txt.exists() and txt.stat().st_size > 0:
        _set_status(conn, eid, "transcribed")
        return f"transcribed {stem} ({elapsed}s)"
    _set_status(conn, eid, "pending", error=f"ASR rc={rc}", bump_attempts=True)
    return f"fail asr {stem}"


def _do_insert(conn, row: sqlite3.Row) -> str:
    eid = row["eid"]
    stem = row["stem"]
    txt = TRANS_DIR / f"{stem}.txt"
    if not (txt.exists() and txt.stat().st_size > 0):
        _set_status(conn, eid, "pending", error="transcript missing", bump_attempts=True)
        return f"fail insert-no-transcript {stem}"
    transcript = txt.read_text(encoding="utf-8").strip()

    src = conn.execute("SELECT id, handle FROM sources WHERE source_key=?", (row["source_key"],)).fetchone()
    if not src:
        _set_status(conn, eid, "transcribed", error=f"source not registered: {row['source_key']}", bump_attempts=True)
        return f"fail insert-no-source {row['source_key']}"
    source_id = src["id"]
    author = src["handle"] or row["source_key"]

    url = f"https://www.xiaoyuzhoufm.com/episode/{eid}"
    title = row["title"]
    published = row["pub_date"]

    existing = conn.execute(
        "SELECT id FROM raw_items WHERE source_id=? AND url=?",
        (source_id, url),
    ).fetchone()
    if existing:
        rid = existing[0]
        conn.execute(
            "UPDATE raw_items SET title=?, body=?, author=?, published_at=? WHERE id=?",
            (title, transcript, author, published, rid),
        )
    else:
        cur = conn.execute(
            """INSERT INTO raw_items (source_id, url, title, body, author, published_at, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, '{}')""",
            (source_id, url, title, transcript, author, published),
        )
        rid = cur.lastrowid

    art = conn.execute("SELECT id FROM articles WHERE raw_item_id=?", (rid,)).fetchone()
    if art:
        aid = art[0]
    else:
        cur = conn.execute(
            """INSERT INTO articles (raw_item_id, title, subtitle, structured_body, word_count, model_id)
               VALUES (?, ?, ?, ?, ?, 'qwen3-asr-1.7b')""",
            (rid, title, transcript[:150], transcript, len(transcript)),
        )
        aid = cur.lastrowid

    _set_status(conn, eid, "inserted", article_id=aid)
    return f"inserted {stem} aid={aid}"


def _strip_and_find_json(text: str) -> Optional[dict]:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    if "```" in text:
        text = "\n".join(l for l in text.split("\n") if not l.strip().startswith("```")).strip()
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    end = -1
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        return None
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return None


def _chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = text.strip()
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        end = min(i + size, n)
        if end < n:
            window_start = max(i + size - 300, i + size // 2)
            boundary = -1
            for m in re.finditer(r"[。！？\n]", text[window_start:end]):
                boundary = window_start + m.end()
            if boundary > 0:
                end = boundary
        chunks.append(text[i:end].strip())
        if end >= n:
            break
        i = max(end - overlap, i + 1)
    return chunks


CHUNK_SYSTEM = """你是一位严谨的播客内容编辑。把原始口语转写改写为可读的长文段落，保留论点与论据，不做压缩性摘要。"""

CHUNK_USER_TEMPLATE = """这是播客《{title}》第 {idx}/{total} 段原始转写（口语流）。
请把它改写成可读的文字段落（清稿而非摘要），并分成 2-3 个小节。

严格要求：
1. 保留原文 65%-80% 的信息量，仅做清理（去填充词/合并重复/修正标点）
2. 每个小节写一个精炼小标题（`## 小标题`）
3. 段落内用 **粗体** 标注核心观点、关键概念、重要数据
4. 本段最精彩的 2 句保留为块引用（`> 原话`），置于对应段落末尾
5. 寒暄/收尾段可返回空 sections
6. body 字段是已组织好的 markdown 正文

原始转写：
{body}

输出严格 JSON：
{{"sections": [{{"heading": "小标题", "body": "## 小标题\\n\\n段落内容……"}}], "takeaways": ["要点1", "要点2"]}}"""

SUMMARY_SYSTEM = """你是一位专业的播客编辑，擅长用最短的文字抓住一期节目的内核。"""

SUMMARY_USER_TEMPLATE = """这是播客《{title}》整期的章节清单与各段核心要点，请生成卡片层摘要。

章节：
{headings}

各段要点：
{takeaways}

要求：
1. subtitle: 20-40 字，一句话讲清"这一期在讲什么"——主题 + 视角即可，不要堆料（不要冒号分号多句结构、不要方法论清单、不要"破局关键：A+B+C"这种挂件）
2. highlights: 3-5 条卡片要点（提炼后的观点/结论），每条 15-40 字

输出 JSON：
{{"subtitle": "一句话摘要", "highlights": ["观点1", "观点2"]}}"""


def _robust_json(prompt: str, system: str, max_tokens: int,
                 *, task, scope) -> Optional[dict]:
    from prism.pipeline.llm import call_llm, call_llm_json
    try:
        resp = call_llm_json(prompt, system=system, model=ARTICLIZE_MODEL, max_tokens=max_tokens,
                             task=task, scope=scope)
        if isinstance(resp, dict):
            return resp
    except Exception:
        pass
    try:
        raw = call_llm(prompt, system=system, model=ARTICLIZE_MODEL, max_tokens=max_tokens,
                       task=task, scope=scope)
        return _strip_and_find_json(raw)
    except Exception:
        return None


def _do_articlize(conn, row: sqlite3.Row) -> str:
    eid = row["eid"]
    article_id = row["article_id"]
    if not article_id:
        art = conn.execute(
            """SELECT a.id FROM articles a
               JOIN raw_items ri ON a.raw_item_id=ri.id
               JOIN sources s ON ri.source_id=s.id
               WHERE s.source_key=? AND ri.url=?""",
            (row["source_key"], f"https://www.xiaoyuzhoufm.com/episode/{eid}"),
        ).fetchone()
        if not art:
            _set_status(conn, eid, "transcribed", error="article not found for articlize", bump_attempts=True)
            return f"fail articlize-no-article {row['stem']}"
        article_id = art[0]

    art = conn.execute(
        "SELECT a.*, ri.title AS ep_title, ri.body AS raw_body FROM articles a "
        "JOIN raw_items ri ON a.raw_item_id=ri.id WHERE a.id=?",
        (article_id,),
    ).fetchone()
    if not art:
        _set_status(conn, eid, "inserted", error=f"article id {article_id} gone", bump_attempts=True)
        return f"fail articlize-missing {article_id}"

    title = art["ep_title"]
    transcript = art["raw_body"] or ""
    if not transcript:
        _set_status(conn, eid, "inserted", error="empty transcript", bump_attempts=True)
        return f"fail articlize-empty {eid}"

    chunks = _chunk_text(transcript)
    all_sections: list[dict] = []
    all_takeaways: list[str] = []
    for idx, chunk in enumerate(chunks, 1):
        prompt = CHUNK_USER_TEMPLATE.format(title=title, idx=idx, total=len(chunks), body=chunk)
        parsed = _robust_json(prompt, CHUNK_SYSTEM, max_tokens=6000,
                              task=Task.STRUCTURIZE, scope=Scope.ITEM)
        if not parsed:
            continue
        for s in parsed.get("sections") or []:
            if s.get("heading") and s.get("body"):
                all_sections.append({"heading": s["heading"], "body": s["body"]})
        for t in parsed.get("takeaways") or []:
            if isinstance(t, str) and t.strip():
                all_takeaways.append(t.strip())

    if not all_sections:
        _set_status(conn, eid, "inserted", error="articlize produced no sections", bump_attempts=True)
        return f"fail articlize-nosecs {eid}"

    headings_str = "\n".join(f"- {s['heading']}" for s in all_sections)
    takeaways_str = "\n".join(f"- {t}" for t in all_takeaways[:40])
    summary = _robust_json(
        SUMMARY_USER_TEMPLATE.format(title=title, headings=headings_str, takeaways=takeaways_str),
        SUMMARY_SYSTEM, max_tokens=1000,
        task=Task.STRUCTURIZE, scope=Scope.ITEM,
    ) or {}
    subtitle = (summary.get("subtitle") or all_sections[0]["heading"])[:120]
    highlights = [h for h in (summary.get("highlights") or []) if isinstance(h, str) and h.strip()][:5]
    if not highlights:
        highlights = all_takeaways[:5]

    body_parts = []
    for s in all_sections:
        b = s["body"].strip()
        if not b.lstrip().startswith("#"):
            b = f"## {s['heading']}\n\n{b}"
        body_parts.append(b)
    structured_body = "\n\n".join(body_parts).strip()

    conn.execute(
        """UPDATE articles SET subtitle=?, structured_body=?, highlights_json=?,
                  word_count=?, model_id=?, updated_at=datetime('now') WHERE id=?""",
        (subtitle, structured_body, json.dumps(highlights, ensure_ascii=False),
         len(structured_body), f"omlx:{ARTICLIZE_MODEL}", article_id),
    )
    _set_status(conn, eid, "done", article_id=article_id)
    return f"done {row['stem']} aid={article_id}"


# ───────────────────────── status ─────────────────────────

def status(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT source_key, status, COUNT(*) AS n
           FROM xyz_episode_queue GROUP BY source_key, status
           ORDER BY source_key, status"""
    ).fetchall()
    by_source: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    for r in rows:
        by_source.setdefault(r["source_key"], {})[r["status"]] = r["n"]
        totals[r["status"]] = totals.get(r["status"], 0) + r["n"]
    return {"by_source": by_source, "totals": totals}
