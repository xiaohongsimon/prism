# Prism v2 Source Adapters — Implementation Plan (2 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Add 4 new source adapters: YouTube transcript, HN /best, GitHub org releases, model economics

**Architecture:** Each adapter implements the existing `SourceAdapter` protocol (async def sync(config) -> SyncResult). Registered in `prism/sources/__init__.py` ADAPTERS dict.

**Spec:** `docs/specs/2026-03-29-prism-v2-entity-system.md` Section 4

---

### Task 1: HN /best Adapter (simplest, establishes pattern)

**Files:** Create `prism/sources/hackernews.py`, Test `tests/sources/test_hackernews.py`, Modify `prism/sources/__init__.py`

Config: `{type: hackernews, key: "hn:best", feed_url: "https://hnrss.org/best", max_items: 15}`

Implementation: fetch RSS via httpx, parse XML (xml.etree.ElementTree), extract title/link/description/pubDate, return as RawItems. Limit to max_items.

### Task 2: GitHub Org Releases Adapter

**Files:** Create `prism/sources/github_releases.py`, Test `tests/sources/test_github_releases.py`, Modify `prism/sources/__init__.py`

Config: `{type: github_releases, key: "github:releases", orgs: [vllm-project, deepseek-ai, ...]}`

Implementation: For each org, GitHub API `GET /orgs/{org}/repos?sort=pushed&per_page=5` → for each repo `GET /repos/{owner}/{repo}/releases?per_page=1` → filter last 24h by published_at. Uses httpx. Optional GITHUB_TOKEN from env for rate limits.

### Task 3: YouTube Transcript Adapter

**Files:** Create `prism/sources/youtube.py`, Test `tests/sources/test_youtube.py`, Modify `prism/sources/__init__.py`

Config: `{type: youtube, key: "youtube:ai-interviews", channels: [channel_ids...]}`

Implementation: RSS feed `https://www.youtube.com/feeds/videos.xml?channel_id=XXX` → detect new videos → `yt-dlp --write-auto-sub --sub-lang en --skip-download` to get subtitles → if subtitle available, include in raw_json. Body = video title + description (transcript processing deferred to entity_link with 27B).

### Task 4: Model Economics Adapter

**Files:** Create `prism/sources/model_economics.py`, Test `tests/sources/test_model_economics.py`, Modify `prism/sources/__init__.py`

Config: `{type: model_economics, key: "economics:models"}`

Implementation: Fetch from Artificial Analysis API (public JSON endpoint) or OpenRouter /models endpoint. Compare with previous day's snapshot (stored in raw_json). Generate signal only when price changes >10% or new model appears.
