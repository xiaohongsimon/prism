#!/bin/bash
set -euo pipefail
cd /Users/leehom/work/prism
source .venv/bin/activate
[ -f .env ] && source .env
LOG=data/sync.log
mkdir -p data
echo "=== $(date) ===" >> "$LOG"
prism sync >> "$LOG" 2>&1
prism cluster >> "$LOG" 2>&1
prism analyze --incremental >> "$LOG" 2>&1
