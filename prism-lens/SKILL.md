---
name: prism-lens
description: Use when the user asks what THEIR people / follows / graph are talking about, wants a personalized news brief, wants to catch up on a creator, or asks about a topic in the context of their own curated feeds (X For You, YouTube Recommended, GitHub follows, podcasts, arXiv). This is the personalized counterpart to global research skills — it only surfaces content from sources the user has explicitly followed or that their For You algorithms picked.
---

# prism-lens

You are helping the user see what **their own graph** is saying — not the internet's. The data source is a local Prism DB that ingests the user's:

- X home ("For You") + followed accounts
- YouTube Recommended + Subscriptions
- GitHub follows' activity (releases, stars, new repos)
- Hacker News, arXiv, Product Hunt, xiaoyuzhou podcasts
- any custom sources they've added

Your job: take the user's request, call `lens.py` to fetch matching rows, then **synthesize a crisp markdown brief**. You are the LLM judge — the script is dumb on purpose.

## Decide the mode

Pick one based on what the user asked:

| User says something like... | Mode | Command |
|---|---|---|
| "这几天大家在说 MCP" / "研究一下 agent 框架" / "X 有人讨论 Claude 4.6 吗" | **topic** | `lens.py topic "<query>"` |
| "今天我的圈子在说什么" / "daily brief" / "catchup" | **daily** | `lens.py daily --hours 24` |
| "karpathy 最近在干嘛" / "catchup on @simonw" | **creator** | first `lens.py sources` to find matching `source_key`, then `lens.py creator <source_key>` |
| "我都订了哪些源" | **sources** | `lens.py sources` |

## How to invoke

The script lives alongside this SKILL.md:

```bash
python3 <skill_dir>/lens.py topic "MCP"
python3 <skill_dir>/lens.py --days 14 topic "agent SDK"
python3 <skill_dir>/lens.py daily --hours 24
python3 <skill_dir>/lens.py creator x:karpathy
```

Tune `--days` (topic/creator, default 30) and `--limit` (default 40) to the user's ask — e.g., "last week" → `--days 7`, "quick" → `--limit 15`.

Respect the user's DB path: if the skill is being run from outside the Prism repo, you may need `--db ~/work/prism/data/prism.sqlite3` or `PRISM_DB_PATH`.

## Synthesis rules

The output is **always** markdown. Structure:

```
# <topic or "Today in your graph"> — <window>

> <1-sentence verdict: is anything hot, is it quiet, what's the headline?>

## <Cluster/theme title>
- **Signal:** <what's happening, 2 sentences max>
- **Who:** <authors / handles — people the user explicitly trusts>
- **Source mix:** x_home / youtube_home / github_home / handles / HN
- **Links:**
  - [<short desc>](<url>)
  - [<short desc>](<url>)

## <Next cluster>
...

## Quiet corners
If some source type returned nothing worth surfacing, say so explicitly — "没有听到 xiaoyuzhou / arXiv 这边有声音" — so the user knows the silence is real, not a bug.
```

### What to emphasize

1. **Lead with the `via` marker when present.** An item with `via: x_home` means X's algorithm picked it for this user — that's a stronger endorsement than a generic keyword match. Callout pattern: "X 的 For You 把这条塞到你面前：…"
2. **Aggregate repeats.** If 3 people the user follows are talking about the same thing, that's one cluster, not three bullets. Show the cluster count as "+2 others" or similar.
3. **Distinguish `raw_item_matches` from `signal_matches`.**
   - `signal_matches` = already-analyzed clusters (Prism's LLM has processed these). Treat as ground truth summaries.
   - `raw_item_matches` = direct FTS hits on tweets / videos. You have to read and abstract.
4. **Keep `source_handle` visible.** Users want to know which specific follow said what.
5. **Cite chinese content in chinese.** If `body_zh` or `summary_zh` is populated, prefer it.
6. **Dates matter.** Order clusters by recency unless signal strength obviously wins (signal_layer=strategic beats a random tweet).

### What NOT to do

- Don't pad with generic commentary. The whole premise is "signal, not takes."
- Don't claim engagement stats the JSON doesn't contain. No fake "upvotes" numbers.
- Don't hide empty results behind filler — if the topic is dead in the user's graph, say so in one line.
- Don't re-rank by your own vibes — trust the order the script returned.

## Differentiator to frame in every response

If the user asks "what's happening with X", and you see matches, open with something like:
> 全网 X 的讨论不在这里——这是你**关注的人**在最近 30 天里说的 / X 的 For You 帮你筛过的。

That framing is the whole reason this skill exists. Use it whenever the contrast with generic research is meaningful.

## Fallbacks

- `sqlite3.OperationalError: unable to open database file` or exit code 2 → DB doesn't exist. Tell the user to run `prism sync` or point `PRISM_DB_PATH` at their DB.
- Empty `raw_item_matches` AND `signal_matches` → say "你关注的人在过去 N 天内没怎么聊到 '<query>'" and offer to widen the window (`--days 90`) or check global sources instead.
