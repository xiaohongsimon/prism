#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
source .venv/bin/activate
[ -f .env ] && source .env
LOG=data/sync.log
mkdir -p data
echo "=== $(date) ===" >> "$LOG"
# Health check: auto-reset stuck sources before sync
python scripts/health_check.py >> "$LOG" 2>&1 || true
prism sync >> "$LOG" 2>&1
prism expand-links --limit 20 >> "$LOG" 2>&1
prism cluster >> "$LOG" 2>&1
prism analyze --incremental >> "$LOG" 2>&1
prism generate-slides --limit 20 >> "$LOG" 2>&1
prism quality-scan >> "$LOG" 2>&1 || true
