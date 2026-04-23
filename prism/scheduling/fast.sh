#!/bin/bash
# Fast pipeline (every 3h): high-velocity sources only + light analysis chain.
# Heavy / once-per-day work (sync-follows, articlize, trends, briefing,
# publish, cleanup, source-weight adjustment, full sync of slow types) lives
# in daily.sh.
set -euo pipefail
cd "$(dirname "$0")/../.."
source .venv/bin/activate
[ -f .env ] && source .env
# X cookies for bird-backed XAdapter (private GraphQL via cookie auth)
[ -f "$HOME/.config/prism/x_cookies.env" ] && source "$HOME/.config/prism/x_cookies.env"
LOG=data/sync.log
mkdir -p data
echo "=== $(date) FAST ===" >> "$LOG"
# Health check: auto-reset stuck sources before sync
python scripts/health_check.py >> "$LOG" 2>&1 || true
# High-velocity types only — slow sources (arxiv/youtube/xiaoyuzhou/...) wait for daily.sh
prism sync --type x --type follow_builders --type hackernews --type hn_search --type reddit --type producthunt >> "$LOG" 2>&1
prism expand-links --limit 20 >> "$LOG" 2>&1
# Translate raw bodies → body_zh so creator pages render Chinese instead of English
prism translate-bodies --limit 800 --since-days 7 >> "$LOG" 2>&1 || true
prism cluster >> "$LOG" 2>&1
prism analyze --incremental >> "$LOG" 2>&1
prism quality-scan >> "$LOG" 2>&1 || true
