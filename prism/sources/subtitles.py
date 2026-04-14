"""YouTube subtitle extraction — youtube-transcript-api (fast) with yt-dlp fallback."""

import re
import subprocess
import tempfile
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _extract_video_id(url: str) -> str | None:
    """Extract video ID from YouTube URL."""
    m = re.search(r'(?:v=|youtu\.be/|/embed/|/v/)([a-zA-Z0-9_-]{11})', url)
    return m.group(1) if m else None


def _fetch_via_api(video_id: str) -> str | None:
    """Fast path: youtube-transcript-api (no subprocess, no file I/O)."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        # Try Chinese first, then English
        transcript = YouTubeTranscriptApi.get_transcript(
            video_id, languages=["zh-Hans", "zh-Hant", "zh", "en"]
        )
        lines = [entry["text"] for entry in transcript if entry.get("text")]
        if not lines:
            return None
        return _join_paragraphs(lines)
    except Exception as exc:
        logger.debug("youtube-transcript-api failed for %s: %s", video_id, exc)
        return None


def _fetch_via_ytdlp(video_url: str) -> str | None:
    """Fallback: yt-dlp subprocess (slower but handles edge cases)."""
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
                proc = subprocess.run(cmd, capture_output=True, timeout=60)
                if proc.returncode != 0:
                    continue
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue

            found = list(Path(tmpdir).glob("*.srt")) or list(Path(tmpdir).glob("*.vtt"))
            if found:
                raw = found[0].read_text(encoding="utf-8")
                return _clean_srt(raw)

    return None


def extract_subtitles(video_url: str) -> str | None:
    """Extract subtitles: try youtube-transcript-api first, fall back to yt-dlp."""
    video_id = _extract_video_id(video_url)

    # Fast path
    if video_id:
        result = _fetch_via_api(video_id)
        if result:
            return result

    # Fallback
    result = _fetch_via_ytdlp(video_url)
    if result:
        return result

    logger.warning("No subtitles found for %s", video_url)
    return None


def _join_paragraphs(lines: list[str]) -> str:
    """Join transcript lines into readable paragraphs (~5 sentences each)."""
    text = " ".join(l.strip() for l in lines if l.strip())
    # Deduplicate consecutive identical phrases
    text = re.sub(r'\b(\w{2,})\s+\1\b', r'\1', text)
    sentences = re.split(r"(?<=[。！？.!?])\s*", text)
    paragraphs = []
    for i in range(0, len(sentences), 5):
        para = " ".join(sentences[i : i + 5]).strip()
        if para:
            paragraphs.append(para)
    return "\n\n".join(paragraphs)


def _clean_srt(raw: str) -> str:
    """Clean SRT/VTT subtitle text into flowing paragraphs."""
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

    return _join_paragraphs(cleaned)
