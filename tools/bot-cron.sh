#!/usr/bin/env bash
# One polling pass for the mention bot, safe to run from cron.
#
# The bot renders serially and a render takes about a minute, so a pass carrying
# several requests outlives the interval between passes. Overlap is already safe
# — the ledger claims a request before it posts, and a claim under 30 minutes old
# makes the request invisible to a second poller — but flock keeps two ffmpeg
# encodes off the same box for no benefit.
#
# Exit codes are the bot's failure channel and they mean different things here:
# a 3x is the operator's configuration and will fail identically on every pass,
# so it is worth waking someone up for. Everything else is this pass's problem
# and the next pass will retry.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/bot-cron.log"

# The account this bot must run as, and where to shout if it cannot. Both come
# from the environment: the handle so a fork does not inherit ours, and the
# alert URL because it is a channel anyone who reads it could post to.
ACCOUNT="${SKEETIO_ACCOUNT:?set SKEETIO_ACCOUNT to the bot handle}"
ALERT_URL="${SKEETIO_ALERT_URL:-}"

exec 9>"$ROOT/.bot-cron.lock"
flock -n 9 || exit 0   # a pass is still running; not an error

{
  echo "--- $(date -Is) ---"
  timeout 1800 python3 "$ROOT/render/bot.py" \
      --env "$ROOT/.env" --expect-account "$ACCOUNT" --once --dur 8
  code=$?
  echo "exit $code"

  if [ $code -ge 30 ] && [ $code -lt 40 ]; then
    echo "OPERATOR ERROR — the bot cannot run until this is fixed"
    [ -n "$ALERT_URL" ] && curl -s -m 20 \
      -d "skeetio bot halted: exit $code (operator config)" "$ALERT_URL" >/dev/null 2>&1 || true
  fi
} >> "$LOG" 2>&1

# Keep the log from growing without bound; it is chatty per pass.
if [ "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt 5000000 ]; then
  tail -c 1000000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
