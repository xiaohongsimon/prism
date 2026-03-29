"""YouTube subtitle extraction via yt-dlp — zero cost, no API key needed."""

import re
import subprocess
import tempfile
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_subtitles(video_url: str) -> str | None:
    """Extract and clean subtitles from a YouTube video using yt-dlp.

    Tries auto-generated captions (zh then en fallback).
    Returns cleaned text or None if unavailable.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        out_template = str(Path(tmpdir) / "sub")

        for write_flag in ("--write-subs", "--write-auto-subs"):
            cmd = [
                "yt-dlp",
                "--skip-download",
                write_flag,
                "--sub-langs", "zh.*,en.*",
                "--sub-format", "srt/vtt/best",
                "--convert-subs", "srt",
                "-o", out_template,
                video_url,
            ]
            try:
                subprocess.run(cmd, capture_output=True, timeout=60)
            except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
                logger.warning("yt-dlp failed for %s: %s", video_url, exc)
                continue

            srt_files = list(Path(tmpdir).glob("*.srt"))
            vtt_files = list(Path(tmpdir).glob("*.vtt"))
            found = srt_files or vtt_files
            if found:
                raw = found[0].read_text(encoding="utf-8")
                return _clean_srt(raw)

    return None


def _clean_srt(raw: str) -> str:
    """Clean SRT subtitle text into flowing paragraphs."""
    lines = raw.split("\n")
    cleaned = []
    prev = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if re.match(r"^\d+$", line):
            continue
        if re.match(r"^[\d:.,\-\s>]+$", line):
            continue
        if line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:"):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"\{[^}]+\}", "", line)
        if line == prev:
            continue
        prev = line
        cleaned.append(line)

    # Join into paragraphs (~5 sentences each)
    text = " ".join(cleaned)
    sentences = re.split(r"(?<=[。！？.!?])\s*", text)
    paragraphs = []
    for i in range(0, len(sentences), 5):
        para = " ".join(sentences[i : i + 5]).strip()
        if para:
            paragraphs.append(para)

    return "\n\n".join(paragraphs)
