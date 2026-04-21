# prism-lens

> Last 30 days — from **YOUR graph**, not the internet's.

A Claude Code skill that answers "what are the people I follow saying?" using your own Prism database.

## The difference

[`last30days-skill`](https://github.com/mvanhorn/last30days-skill) asks: **"what's the internet saying about X?"**
Same input, same output for everyone. Ranks by global engagement.

`prism-lens` asks: **"what's MY graph saying about X?"**
Different output for every user. Ranks by who *you* trust — X's For You algorithm, your YouTube subscriptions, people you follow on GitHub, podcasts you chose, handles you added.

The internet's hot takes are already everywhere. What's missing is your own signal filter.

## How it works

```
/prism-lens MCP servers

→ python3 lens.py topic "MCP servers"
→ SQLite FTS over raw_items + signals, last 30d
→ Claude synthesizes a brief showing:
  - What your follows said (grouped by cluster)
  - What X's For You surfaced (`via: x_home`)
  - What Prism's LLM judge already flagged as strategic
```

No LLM calls in the script — the orchestrating Claude Code session does the synthesis. Zero extra API cost beyond what you already pay CC.

## Requirements

- A local [Prism](https://github.com/xiaohongsimon/prism) instance with `data/prism.sqlite3` populated
- Python 3.9+ (stdlib only — no deps)
- Claude Code

## Install

```bash
# Option A: symlink from ~/.claude/skills
ln -s /path/to/prism/prism-lens ~/.claude/skills/prism-lens

# Option B: copy
cp -r prism-lens ~/.claude/skills/
```

Then in Claude Code, the skill auto-triggers on graph-centric questions, or you can invoke it explicitly.

## Commands

| Command | What you'll ask |
|---|---|
| `lens.py topic "<query>"` | "最近大家在聊什么 X" / "research Y from my feeds" |
| `lens.py daily --hours 24` | "今天我的圈子在说什么" / "daily brief" |
| `lens.py creator <source_key>` | "karpathy 最近在干嘛" / "catchup on @simonw" |
| `lens.py sources` | "我都订了哪些源" |

Flags:
- `--days N` — lookback window for topic/creator (default 30)
- `--limit N` — max rows returned (default 40)
- `--db <path>` — override DB location (also via `$PRISM_DB_PATH`)

## Example

```
$ python3 lens.py --limit 3 topic "Claude Code"
```

Returns a JSON payload like:

```json
{
  "mode": "topic",
  "query": "Claude Code",
  "window_days": 30,
  "raw_item_matches": [
    {
      "url": "https://x.com/garrytan/status/...",
      "author": "garrytan",
      "body": "Yes you can use Claude Code on its own and it is amazing...",
      "source_key": "x:garrytan",
      "via": ""
    }
  ],
  "signal_matches": [
    {
      "summary": "Claude Code skills marketplace reaches critical mass...",
      "signal_layer": "strategic",
      "signal_strength": 8,
      "tl_perspective": "...",
      "tags": ["Claude Code", "skills", "marketplace"]
    }
  ]
}
```

Claude then turns that into a markdown brief — see `SKILL.md` for the synthesis rules.

## Why Claude Code skill format?

Because it's **one prompt away**. Everyone with Claude Code can try it without hosting anything beyond their own Prism. No dashboards, no auth flows, no "sign up." The skill is the UI.

## Roadmap

- [x] Local-DB mode (this)
- [ ] Standalone fallback: if no Prism DB, live-fetch via `bird` / `yt-dlp` / `gh` using the user's own cookies (BYOK)
- [ ] `compare` mode: run same query through `last30days-skill` + `prism-lens`, show the delta — "what your graph adds over the internet's consensus"

## License

MIT. Part of the [Prism](https://github.com/xiaohongsimon/prism) project.
