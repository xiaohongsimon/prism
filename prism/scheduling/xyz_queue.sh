#!/bin/bash
# xiaoyuzhou backfill queue tick (every 2min via launchd).
# Each tick drains as many stages as possible until busy/idle; xyz_queue.py
# internally throttles on omlx/GPU busy flags.
#
# Lock: articlize can take 10+ minutes per episode, longer than the tick
# interval. `mkdir` is atomic on macOS and serves as a simple mutex (flock
# from util-linux is not available on macOS by default). A stale lock from a
# crashed tick is auto-cleared after 2h.
set -euo pipefail
cd "$(dirname "$0")/../.."
source .venv/bin/activate
[ -f .env ] && source .env
LOG=data/xyz_queue.log
LOCK=data/xyz_queue.lockdir
mkdir -p data

# Clear stale lock (>2h old) left by a crashed previous run.
if [ -d "$LOCK" ]; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
    echo "=== $(date) XYZ-QUEUE STALE LOCK (>2h), clearing ===" >> "$LOG"
    rmdir "$LOCK" 2>/dev/null || rm -rf "$LOCK"
  fi
fi

if ! mkdir "$LOCK" 2>/dev/null; then
  echo "=== $(date) XYZ-QUEUE SKIP (previous run still holding lock) ===" >> "$LOG"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT
echo "=== $(date) XYZ-QUEUE TICK ===" >> "$LOG"
prism xyz-queue tick --max 99 >> "$LOG" 2>&1
