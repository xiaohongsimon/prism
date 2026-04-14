# HN Hotness Weighting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give HN high-point posts a ranking boost in the Web Feed so breaking news (like 1000+ point posts) surfaces to the top.

**Architecture:** hnrss.org RSS already embeds Points/Comments in `<description>` and provides `<comments>` URL + `<dc:creator>`. We parse these directly from RSS (no Algolia API needed). Then ranking.py applies a normalized boost based on hn_points for hot/recommend tabs.

**Tech Stack:** Python, xml.etree.ElementTree, regex, SQLite, pytest

---

### Task 1: Parse HN points/comments/author from RSS

**Files:**
- Modify: `prism/sources/hackernews.py`
- Test: `tests/sources/test_hackernews.py`

The hnrss.org RSS `<description>` contains lines like `Points: 523` and `# Comments: 187`. The `<comments>` tag has the HN URL (with item ID). The `<dc:creator>` tag has the author.

- [ ] **Step 1: Write failing tests for points/comments/author parsing**

Add a new RSS fixture with real hnrss.org format and test that `parse_hn_rss` extracts hn_points, hn_comments, hn_id, and author into raw_json. Add to `tests/sources/test_hackernews.py`:

```python
_HN_RSS_WITH_POINTS = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Hacker News: Best</title>
    <link>https://news.ycombinator.com/best</link>
    <item>
      <title>Claude Code source leaked</title>
      <link>https://example.com/leak</link>
      <description><![CDATA[
<p>Article URL: <a href="https://example.com/leak">https://example.com/leak</a></p>
<p>Comments URL: <a href="https://news.ycombinator.com/item?id=47584540">https://news.ycombinator.com/item?id=47584540</a></p>
<p>Points: 1944</p>
<p># Comments: 956</p>
]]></description>
      <pubDate>Tue, 31 Mar 2026 09:00:40 +0000</pubDate>
      <dc:creator>treexs</dc:creator>
      <comments>https://news.ycombinator.com/item?id=47584540</comments>
    </item>
    <item>
      <title>Some low-score post</title>
      <link>https://example.com/low</link>
      <description><![CDATA[<p>Just a small post</p>]]></description>
      <pubDate>Tue, 31 Mar 2026 08:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""


def test_parse_hn_rss_extracts_points_and_comments():
    import json
    items = parse_hn_rss(_HN_RSS_WITH_POINTS)
    assert len(items) == 2

    meta0 = json.loads(items[0].raw_json)
    assert meta0["hn_points"] == 1944
    assert meta0["hn_comments"] == 956
    assert meta0["hn_id"] == 47584540
    assert items[0].author == "treexs"

    # Item without points data → null values
    meta1 = json.loads(items[1].raw_json)
    assert meta1.get("hn_points") is None
    assert meta1.get("hn_comments") is None
    assert meta1.get("hn_id") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/sources/test_hackernews.py::test_parse_hn_rss_extracts_points_and_comments -v`
Expected: FAIL — raw_json currently lacks hn_points/hn_comments/hn_id fields, author is empty.

- [ ] **Step 3: Implement RSS enrichment in hackernews.py**

Add regex patterns and update `parse_hn_rss` in `prism/sources/hackernews.py`:

```python
# Add at top, after existing _HTML_TAG_RE
_HN_POINTS_RE = re.compile(r"Points:\s*(\d+)")
_HN_COMMENTS_RE = re.compile(r"#\s*Comments:\s*(\d+)")
_HN_ID_RE = re.compile(r"item\?id=(\d+)")

# XML namespace for dc:creator
_DC_NS = "http://purl.org/dc/elements/1.1/"
```

Update `parse_hn_rss` function body — replace the loop:

```python
    items: list[RawItem] = []
    for item_el in channel.findall("item")[:max_items]:
        title = (item_el.findtext("title") or "").strip()
        link = (item_el.findtext("link") or "").strip()
        description = item_el.findtext("description") or ""
        pub_date = (item_el.findtext("pubDate") or "").strip()
        author = (item_el.findtext(f"{{{_DC_NS}}}creator") or "").strip()

        # Extract HN metadata from description and comments tag
        comments_url = (item_el.findtext("comments") or "").strip()
        hn_id_match = _HN_ID_RE.search(comments_url) or _HN_ID_RE.search(description)
        hn_id = int(hn_id_match.group(1)) if hn_id_match else None

        points_match = _HN_POINTS_RE.search(description)
        hn_points = int(points_match.group(1)) if points_match else None

        comments_match = _HN_COMMENTS_RE.search(description)
        hn_comments = int(comments_match.group(1)) if comments_match else None

        body = _strip_html(description)

        items.append(
            RawItem(
                url=link,
                title=title,
                body=body,
                author=author,
                raw_json=json.dumps(
                    {
                        "title": title,
                        "link": link,
                        "pubDate": pub_date,
                        "hn_id": hn_id,
                        "hn_points": hn_points,
                        "hn_comments": hn_comments,
                    },
                    ensure_ascii=False,
                ),
            )
        )
    return items
```

- [ ] **Step 4: Run all HN tests to verify they pass**

Run: `.venv/bin/pytest tests/sources/test_hackernews.py -v`
Expected: ALL PASS. Existing tests should also pass since the old RSS fixtures lack `<dc:creator>` and points data — the new code handles missing fields gracefully (None/empty).

- [ ] **Step 5: Commit**

```bash
git add prism/sources/hackernews.py tests/sources/test_hackernews.py
git commit -m "feat(hn): parse points/comments/author from hnrss.org RSS"
```

---

### Task 2: Add HN boost to ranking

**Files:**
- Modify: `prism/web/ranking.py`
- Test: `tests/web/test_ranking.py`

- [ ] **Step 1: Write failing tests for HN boost**

Add tests to `tests/web/test_ranking.py`. We need a helper to seed HN items with hn_points in raw_json, then verify the boost affects ranking.

```python
import json


def _seed_with_hn_points(conn):
    """Insert 2 signals: one from HN with high points, one from X with same strength."""
    conn.execute("INSERT INTO sources (source_key, type, handle) VALUES ('hn:best', 'hackernews', '')")
    conn.execute("INSERT INTO sources (source_key, type, handle) VALUES ('x:karpathy', 'x', 'karpathy')")

    # HN item with 800 points
    conn.execute(
        "INSERT INTO raw_items (source_id, url, title, published_at, raw_json) "
        "VALUES (1, 'http://hn-post', 'HN Post', '2026-03-31T10:00:00', ?)",
        (json.dumps({"hn_points": 800, "hn_comments": 300}),),
    )
    # X item, no HN points
    conn.execute(
        "INSERT INTO raw_items (source_id, url, title, published_at, raw_json) "
        "VALUES (2, 'http://x-post', 'X Post', '2026-03-31T10:00:00', ?)",
        (json.dumps({}),),
    )

    conn.execute("INSERT INTO clusters (date, topic_label, item_count) VALUES ('2026-03-31', 'HN Topic', 1)")
    conn.execute("INSERT INTO clusters (date, topic_label, item_count) VALUES ('2026-03-31', 'X Topic', 1)")

    conn.execute("INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (1, 1)")
    conn.execute("INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (2, 2)")

    # Same signal_strength=3 for both
    conn.execute(
        "INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength, tags_json, is_current) "
        "VALUES (1, 'HN signal', 'actionable', 3, '[\"hn\"]', 1)"
    )
    conn.execute(
        "INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength, tags_json, is_current) "
        "VALUES (2, 'X signal', 'actionable', 3, '[\"x\"]', 1)"
    )
    conn.commit()


def test_ranking_hn_boost_on_hot_tab():
    """HN post with high points should rank above X post with same signal_strength."""
    conn = _fresh_db()
    _seed_with_hn_points(conn)
    items = compute_feed(conn, tab="hot", page=1, per_page=10)
    assert len(items) == 2
    assert items[0]["topic_label"] == "HN Topic"
    # The HN item should have a higher score due to hn_boost
    assert items[0]["score"] > items[1]["score"]


def test_ranking_hn_boost_on_recommend_tab():
    """HN boost also applies to recommend tab."""
    conn = _fresh_db()
    _seed_with_hn_points(conn)
    items = compute_feed(conn, tab="recommend", page=1, per_page=10)
    assert items[0]["topic_label"] == "HN Topic"


def test_ranking_follow_tab_no_hn_boost():
    """Follow tab should not apply HN boost."""
    conn = _fresh_db()
    _seed_with_hn_points(conn)
    # Make both sources follow-type for this test
    conn.execute("UPDATE sources SET type = 'x' WHERE source_key = 'hn:best'")
    conn.commit()
    items = compute_feed(conn, tab="follow", page=1, per_page=10)
    # With no boost and same strength, scores should be equal (within float tolerance)
    if len(items) >= 2:
        assert abs(items[0]["score"] - items[1]["score"]) < 0.01
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/web/test_ranking.py::test_ranking_hn_boost_on_hot_tab -v`
Expected: FAIL — currently no HN boost, both items get same score.

- [ ] **Step 3: Implement HN boost in ranking.py**

Add constant at module level:

```python
# HN hotness boost: max additional score for 500+ point HN posts
HN_BOOST_CAP = 0.15
HN_POINTS_CEILING = 500
```

In `compute_feed()`, after the `source_rows` loop (around line 140), add a section to collect HN points per cluster. Extend the existing `source_rows` query to also fetch `ri.raw_json`:

Replace the `source_rows` query (line 111-118):
```python
    source_rows = conn.execute(
        """
        SELECT ci.cluster_id, src.source_key, src.type, src.enabled, ri.author, ri.url, ri.raw_json
        FROM cluster_items ci
        JOIN raw_items ri ON ri.id = ci.raw_item_id
        JOIN sources src ON src.id = ri.source_id
        """
    ).fetchall()
```

After the existing `source_rows` loop (after line 140), add HN points collection:

```python
    # Collect max HN points per cluster
    cluster_hn_points: dict[int, int] = {}
    for sr in source_rows:
        if not sr["source_key"].startswith("hn:"):
            continue
        try:
            raw = json.loads(sr["raw_json"]) if sr["raw_json"] else {}
            pts = raw.get("hn_points")
            if pts is not None:
                cid = sr["cluster_id"]
                cluster_hn_points[cid] = max(cluster_hn_points.get(cid, 0), pts)
        except (json.JSONDecodeError, TypeError):
            pass
```

In the scoring section (around line 189), replace the score assignment:

```python
        item["score"] = w_heat * heat_norm + w_pref * pref + w_decay * decay

        # HN hotness boost (hot + recommend tabs only)
        if tab != "follow":
            hn_pts = cluster_hn_points.get(r["cluster_id"], 0)
            if hn_pts > 0:
                item["score"] += min(hn_pts / HN_POINTS_CEILING, 1.0) * HN_BOOST_CAP
```

- [ ] **Step 4: Run all ranking tests**

Run: `.venv/bin/pytest tests/web/test_ranking.py -v`
Expected: ALL PASS (new tests and existing tests).

- [ ] **Step 5: Commit**

```bash
git add prism/web/ranking.py tests/web/test_ranking.py
git commit -m "feat(ranking): add HN points boost for hot/recommend tabs"
```

---

### Task 3: Full integration test

**Files:**
- Test: run full test suite

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: ALL PASS. No regressions.

- [ ] **Step 2: Manual verification with live data**

Run a quick query to check that existing HN items in the DB don't have hn_points (confirming only new syncs will have enriched data):

```bash
.venv/bin/python -c "
import sqlite3, json
conn = sqlite3.connect('data/prism.sqlite3')
conn.row_factory = sqlite3.Row
rows = conn.execute('''
    SELECT ri.raw_json, ri.title
    FROM raw_items ri JOIN sources s ON ri.source_id = s.id
    WHERE s.source_key LIKE 'hn:%'
    ORDER BY ri.created_at DESC LIMIT 3
''').fetchall()
for r in rows:
    meta = json.loads(r['raw_json'])
    print(f'{r[\"title\"][:60]} | points={meta.get(\"hn_points\", \"N/A\")}')
"
```

- [ ] **Step 3: Sync HN to get enriched data and verify**

Run: `.venv/bin/prism sync --source hn:best`

Then verify the new items have hn_points:
```bash
.venv/bin/python -c "
import sqlite3, json
conn = sqlite3.connect('data/prism.sqlite3')
conn.row_factory = sqlite3.Row
rows = conn.execute('''
    SELECT ri.raw_json, ri.title
    FROM raw_items ri JOIN sources s ON ri.source_id = s.id
    WHERE s.source_key LIKE 'hn:%'
    ORDER BY ri.created_at DESC LIMIT 5
''').fetchall()
for r in rows:
    meta = json.loads(r['raw_json'])
    print(f'{r[\"title\"][:60]} | points={meta.get(\"hn_points\", \"N/A\")} | comments={meta.get(\"hn_comments\", \"N/A\")}')
"
```

Expected: New items show real points/comments values.

- [ ] **Step 4: Commit any remaining changes**

If all good, no further commits needed.
