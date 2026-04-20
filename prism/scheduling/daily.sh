#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
source .venv/bin/activate
[ -f .env ] && source .env
LOG=data/daily.log
mkdir -p data
echo "=== $(date) ===" >> "$LOG"
# Health check + auto-repair before sync (reset stuck sources, detect issues)
python scripts/health_check.py >> "$LOG" 2>&1 || true
# Pull latest X follows into sources.yaml (best-effort; bird may lack cookies)
# Source X cookies from a local-only env file (see ~/.config/prism/x_cookies.env.example)
[ -f "$HOME/.config/prism/x_cookies.env" ] && source "$HOME/.config/prism/x_cookies.env"
prism sync-follows --apply --max-new 30 >> "$LOG" 2>&1 || true
# Fresh sync before daily analysis to ensure latest content
prism sync >> "$LOG" 2>&1
prism cluster >> "$LOG" 2>&1
prism analyze --incremental >> "$LOG" 2>&1
prism enrich-youtube --limit 20 >> "$LOG" 2>&1
prism articlize >> "$LOG" 2>&1
prism analyze --daily >> "$LOG" 2>&1
prism trends >> "$LOG" 2>&1
prism generate-slides --limit 50 >> "$LOG" 2>&1
prism briefing --save >> "$LOG" 2>&1
prism publish --notion >> "$LOG" 2>&1
prism publish-videos --limit 10 >> "$LOG" 2>&1
prism cleanup >> "$LOG" 2>&1

# Adjust source weights based on pairwise win rates
.venv/bin/python -c "
from prism.config import settings
from prism.db import get_connection
from prism.web.pairwise import adjust_source_weights
conn = get_connection(settings.db_path)
adjust_source_weights(conn)
print('Source weights adjusted')
" >> "$LOG" 2>&1
