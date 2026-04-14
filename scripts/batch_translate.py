#!/usr/bin/env python3
"""Batch translate raw_items body → body_zh for X/follow_builders sources."""

import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# Load env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from prism.config import settings


def call_llm_translate(texts: list[str]) -> list[str]:
    """Translate a batch of texts to Chinese using LLM."""
    if len(texts) == 1:
        prompt = f"将以下英文推文翻译为简洁流畅的中文，只输出译文：\n\n{texts[0]}"
    else:
        numbered = "\n".join(f"[{i+1}] {t}" for i, t in enumerate(texts))
        prompt = (
            f"将以下 {len(texts)} 条英文推文分别翻译为简洁流畅的中文。\n"
            f"每条译文用 [N] 标记对应编号，一行一条，只输出译文：\n\n{numbered}"
        )

    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": "你是翻译助手。忠实翻译，语言简洁自然。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2048,
        "repetition_penalty": 1.2,
    }

    result = subprocess.run(
        ["curl", "-sS", "--max-time", "120",
         url,
         "-H", f"Authorization: Bearer {settings.llm_api_key}",
         "-H", "Content-Type: application/json",
         "-d", json.dumps(payload, ensure_ascii=False)],
        capture_output=True, text=True, timeout=130,
    )
    body = json.loads(result.stdout)
    if "error" in body:
        raise RuntimeError(f"API error: {body['error']}")

    text = body["choices"][0]["message"]["content"]
    # Strip think tags
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    if len(texts) == 1:
        return [text]

    # Parse numbered responses
    translations = []
    lines = text.split("\n")
    current = []
    current_idx = None

    for line in lines:
        m = re.match(r'\[(\d+)\]\s*(.*)', line)
        if m:
            if current_idx is not None:
                translations.append("\n".join(current).strip())
            current_idx = int(m.group(1))
            current = [m.group(2)]
        elif current_idx is not None:
            current.append(line)

    if current_idx is not None:
        translations.append("\n".join(current).strip())

    # Fallback: if parsing failed, split by empty lines
    if len(translations) != len(texts):
        # Just return the whole response as single item for each
        return [text] * len(texts)  # Will be overwritten one-by-one below

    return translations


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=None, help="source_key to filter")
    parser.add_argument("--days", type=int, default=7, help="only translate items from last N days")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()

    conn = sqlite3.connect(str(settings.db_path))
    conn.row_factory = sqlite3.Row

    where_parts = []
    params = []

    if args.source:
        where_parts.append("AND s.source_key = ?")
        params.append(args.source)
    else:
        where_parts.append("AND s.type IN ('x', 'follow_builders')")

    if args.days:
        where_parts.append(f"AND ri.published_at >= datetime('now', '-{args.days} days')")

    where = " ".join(where_parts)

    rows = conn.execute(f"""
        SELECT ri.id, ri.body, s.source_key
        FROM raw_items ri
        JOIN sources s ON ri.source_id = s.id
        WHERE (ri.body_zh IS NULL OR ri.body_zh = '')
        AND ri.body IS NOT NULL AND ri.body != ''
        {where}
        ORDER BY ri.published_at DESC
        LIMIT ?
    """, (*params, args.limit)).fetchall()

    total = len(rows)
    print(f"Found {total} items to translate (last {args.days} days)" + (f" for {args.source}" if args.source else ""))

    translated = 0
    failed = 0
    i = 0

    while i < total:
        batch = rows[i:i + 1]
        texts = [(r["body"] or "")[:500] for r in batch]
        ids = [r["id"] for r in batch]

        try:
            if 1 == 1 or len(batch) == 1:
                results = call_llm_translate(texts)
            else:
                results = call_llm_translate(texts)
                # If batch parsing failed, fall back to one-by-one
                if len(results) != len(texts) or any(r == results[0] for r in results[1:] if len(set(results)) == 1 and len(results) > 1):
                    raise ValueError("Batch parsing unreliable, falling back")

            for item_id, zh_text in zip(ids, results):
                if zh_text and zh_text.strip():
                    conn.execute("UPDATE raw_items SET body_zh = ? WHERE id = ?", (zh_text.strip(), item_id))
                    translated += 1

            conn.commit()
            i += len(batch)
            print(f"  [{translated}/{total}] translated", end="\r")

        except Exception as e:
            # Fall back to one-by-one for this batch
            for r in batch:
                try:
                    results = call_llm_translate([(r["body"] or "")[:500]])
                    zh = results[0].strip() if results else ""
                    if zh:
                        conn.execute("UPDATE raw_items SET body_zh = ? WHERE id = ?", (zh, r["id"]))
                        conn.commit()
                        translated += 1
                except Exception as e2:
                    print(f"\n  Failed id={r['id']}: {e2}")
                    failed += 1
            i += len(batch)
            print(f"  [{translated}/{total}] translated (some fallback)", end="\r")

        # Small delay to avoid overwhelming the LLM
        time.sleep(0.5)

    print(f"\nDone: {translated} translated, {failed} failed out of {total}")
    conn.close()


if __name__ == "__main__":
    main()
