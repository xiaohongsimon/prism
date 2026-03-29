#!/bin/bash
set -euo pipefail
cd /Users/leehom/work/prism
source .venv/bin/activate
[ -f .env ] && source .env
LOG=data/daily.log
mkdir -p data
echo "=== $(date) ===" >> "$LOG"
prism enrich-youtube --limit 20 >> "$LOG" 2>&1
prism analyze --daily >> "$LOG" 2>&1
prism trends >> "$LOG" 2>&1
prism generate-slides --limit 50 >> "$LOG" 2>&1
prism briefing --save >> "$LOG" 2>&1
prism publish --notion >> "$LOG" 2>&1
prism cleanup >> "$LOG" 2>&1
