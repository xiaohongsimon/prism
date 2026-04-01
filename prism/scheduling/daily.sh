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

# Adjust source weights based on pairwise win rates
.venv/bin/python -c "
from prism.config import settings
from prism.db import get_connection
from prism.web.pairwise import adjust_source_weights
conn = get_connection(settings.db_path)
adjust_source_weights(conn)
print('Source weights adjusted')
" >> "$LOG" 2>&1
