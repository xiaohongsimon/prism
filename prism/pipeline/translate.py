"""Batch translation of raw_items.body → body_zh.

Used by both:
- the `/translate/{item_id}` web route (on-demand, single item)
- the `prism translate-bodies` CLI (batch, runs after sync to pre-warm
  creator pages so users don't see English flashing)

Model: gemma-4-26b-a4b-it-8bit — fast, local, good enough for tweet-level
translation. Heavier models reserved for analysis/synthesis.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass

from prism.pipeline.llm import call_llm

logger = logging.getLogger(__name__)

TRANSLATE_MODEL = "gemma-4-26b-a4b-it-8bit"
TRANSLATE_SYSTEM = "你是翻译助手。忠实翻译，语言简洁自然。"
TRANSLATE_PROMPT = "将以下英文推文翻译为简洁流畅的中文，只输出译文，不要解释：\n\n{body}"

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
# Tokens that should not count toward "is this Chinese?" — they pass through
# translation untouched and otherwise dilute the CJK ratio.
_NOISE_RE = re.compile(
    r"https?://\S+"          # URLs
    r"|[@#][\w_]+"            # @mentions, #hashtags
    r"|\$[\d,]+"              # $25,000 prize amounts
    r"|[\U0001F300-\U0001FAFF\U00002600-\U000027BF\u200d\uFE0F]"  # emoji + ZWJ + VS
)


@dataclass
class TranslateOutcome:
    scanned: int = 0          # rows we considered
    translated: int = 0       # rows we successfully wrote body_zh for
    skipped: int = 0          # already-Chinese, empty, or other no-op cases
    failed: int = 0           # LLM/network errors


def _looks_chinese(text: str, threshold: float = 0.30) -> bool:
    """Heuristic: skip translation if text is already mostly Chinese.

    URLs are stripped before measuring — a long share-link can dilute the
    CJK ratio of an otherwise good translation below the salvage threshold.
    """
    if not text:
        return False
    stripped = _NOISE_RE.sub("", text)
    cjk = sum(1 for ch in stripped if "\u4e00" <= ch <= "\u9fff")
    return cjk / max(len(stripped), 1) >= threshold


def translate_one(body: str, *, max_tokens: int = 2048) -> str:
    """Translate a single body. Returns Chinese text, or '' on failure.

    Note: gemma-4-26b-a4b-it-8bit is a reasoning model — it spends most of
    its tokens on internal reasoning before emitting the final translation
    in `message.content`. max_tokens needs to be large enough (≥1500) for
    reasoning + translation to both fit, otherwise content comes back null.
    """
    body = (body or "").strip()
    if not body:
        return ""
    try:
        out = call_llm(
            prompt=TRANSLATE_PROMPT.format(body=body[:1500]),
            system=TRANSLATE_SYSTEM,
            model=TRANSLATE_MODEL,
            max_tokens=max_tokens,
            project="翻译",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("translate_one failed: %s: %s", type(exc).__name__, exc)
        return ""
    out = _THINK_RE.sub("", out or "").strip()
    # Salvage: if the model leaked reasoning text (English-heavy with markdown
    # bullets), don't store it. Caller treats '' as failure.
    if not _looks_chinese(out, threshold=0.20):
        return ""
    return out


def translate_pending(
    conn: sqlite3.Connection,
    *,
    limit: int = 100,
    source_types: tuple[str, ...] = ("x", "follow_builders"),
    since_days: int = 7,
    source_key: str = "",
) -> TranslateOutcome:
    """Translate up to `limit` raw_items rows whose body_zh is empty.

    Filters:
    - source type (default: X-like only — that's what the creator profile
      shows; YouTube items have their own articlize pipeline)
    - since_days: only consider items published in the last N days (default 7).
      Set to 0 to disable date filtering.
    - source_key: if non-empty, restrict to a single source (e.g. "x:karpathy").
    """
    placeholders = ",".join("?" * len(source_types))
    where = [
        "COALESCE(ri.body_zh, '') = ''",
        "COALESCE(ri.body, '') != ''",
        f"s.type IN ({placeholders})",
    ]
    params: list = list(source_types)

    if since_days > 0:
        # Either "recently published" or "recently fetched". X creators that
        # prism just discovered will have old published_at on the backfill
        # batch but a fresh created_at — those still belong in the window.
        where.append(
            "(ri.published_at >= datetime('now', ?) "
            " OR ri.created_at >= datetime('now', ?))"
        )
        params.append(f"-{since_days} days")
        params.append(f"-{since_days} days")

    if source_key:
        where.append("s.source_key = ?")
        params.append(source_key)

    sql = (
        "SELECT ri.id, ri.body FROM raw_items ri "
        "JOIN sources s ON ri.source_id = s.id "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY ri.id DESC LIMIT ?"
    )
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()

    outcome = TranslateOutcome(scanned=len(rows))

    for row in rows:
        body = row["body"] if isinstance(row, sqlite3.Row) else row[1]
        item_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]

        if _looks_chinese(body):
            # Mark as "already Chinese" by copying body to body_zh so we don't
            # re-scan this row next time.
            conn.execute(
                "UPDATE raw_items SET body_zh = ? WHERE id = ?",
                (body, item_id),
            )
            outcome.skipped += 1
            continue

        # If the body has no translatable text after stripping URLs/mentions/
        # hashtags/emoji (e.g. a tweet that is just a bare URL), there is
        # nothing to send to the LLM — mark it skipped so we stop re-scanning.
        if not _NOISE_RE.sub("", body).strip():
            conn.execute(
                "UPDATE raw_items SET body_zh = ? WHERE id = ?",
                (body, item_id),
            )
            outcome.skipped += 1
            continue

        zh = translate_one(body)
        if not zh:
            outcome.failed += 1
            continue

        conn.execute(
            "UPDATE raw_items SET body_zh = ? WHERE id = ?",
            (zh, item_id),
        )
        outcome.translated += 1
        # Commit per-row so partial runs aren't lost on Ctrl-C
        conn.commit()

    conn.commit()
    return outcome
