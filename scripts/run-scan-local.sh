#!/bin/bash
# Local scan runner — invoked by launchd on weekday mornings.
# Pulls latest, runs the scanner, commits + pushes if there are changes.
# All output goes to logs/scan.log so launchd doesn't lose it.

set -euo pipefail

REPO="/Users/rajnikanthkota/INVESTING MODE"
PYTHON="/Library/Frameworks/Python.framework/Versions/3.11/bin/python3"
LOG_DIR="$REPO/logs"
LOG="$LOG_DIR/scan.log"

mkdir -p "$LOG_DIR"

# Rotate log when it crosses ~1MB
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG")" -gt 1000000 ]; then
  mv "$LOG" "$LOG.1"
fi

{
  echo
  echo "════════════════════════════════════════════════════════════"
  echo "  Run started: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "════════════════════════════════════════════════════════════"

  cd "$REPO"

  # Make sure /usr/bin & /usr/local/bin are on PATH (launchd strips them)
  export PATH="/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:$PATH"

  echo "▸ Pulling latest..."
  git pull --rebase --autostash || {
    echo "  git pull failed — aborting"
    exit 1
  }

  echo "▸ Running scanner..."
  if "$PYTHON" run.py; then
    SETUPS=$("$PYTHON" -c "import json; print(json.load(open('output/meta.json'))['setups_total'])" 2>/dev/null || echo "?")
    echo "  ✅ scan returned $SETUPS setups"
  else
    echo "  ❌ scanner exited non-zero — not pushing"
    exit 2
  fi

  echo "▸ Committing + pushing..."
  git add output/
  if git diff --cached --quiet; then
    echo "  no changes to commit"
  else
    git commit -m "scan: $(date '+%d %b %Y %H:%M') IST"
    git push
    echo "  ✅ pushed — Vercel will redeploy in ~30s"
  fi

  echo "Run finished: $(date '+%Y-%m-%d %H:%M:%S %Z')"
} >> "$LOG" 2>&1
