#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
source .venv/bin/activate
[ -f .env ] && source .env
# X cookies for bird-backed XAdapter (private GraphQL via cookie auth)
[ -f "$HOME/.config/prism/x_cookies.env" ] && source "$HOME/.config/prism/x_cookies.env"
LOG=data/sync.log
mkdir -p data
echo "=== $(date) ===" >> "$LOG"
# Health check: auto-reset stuck sources before sync
python scripts/health_check.py >> "$LOG" 2>&1 || true
prism sync >> "$LOG" 2>&1
prism expand-links --limit 20 >> "$LOG" 2>&1
prism cluster >> "$LOG" 2>&1
# Analyze is now two stages (see analyze.py docstring):
#   triage = fast cheap-model pass over every new cluster
#   expand = reasoning-model deep-read for strength>=4 signals only (max 30/run)
# Splitting them in hourly.sh isolates errors and makes dashboard attribution clean.
prism analyze --triage >> "$LOG" 2>&1
prism analyze --expand --min-strength 4 --limit 30 >> "$LOG" 2>&1
prism quality-scan >> "$LOG" 2>&1 || true
