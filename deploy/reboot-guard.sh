#!/usr/bin/env bash
# Printorian — reboot guard.
#
# Asks the farm whether anything is on a machine, and answers with an exit code:
#
#     0  nothing is in flight — a reboot or a shutdown may proceed
#     1  do not interrupt (something is printing, or the farm could not be asked)
#
# It DECIDES AND REPORTS. It never reboots, never shuts anything down and never
# touches a container. Whatever calls it owns the irreversible half — that is a
# systemd unit or a UPS hook on the host, and neither exists in this repository
# yet (deploy/systemd/README.md, "What is deliberately not here").
#
# Usage:
#
#     bash deploy/reboot-guard.sh && systemctl reboot
#
#     PRINTORIAN_HEALTH_URL=http://127.0.0.1:8080/api/health/printing   what to ask
#     PRINTORIAN_GUARD_TIMEOUT=5                                       seconds
#
# The default URL goes through the console's Caddy proxy on 8080, not straight at
# the API. deploy/compose.prod.yml publishes 8080 and 8081 and deliberately does
# not publish the API port, so from the host's own shell the proxy is the only way
# in — the same route CI's "Console serves the bundle and proxies the API
# same-origin" step already exercises.
#
# TWO WAYS OF DEFERRING, both of them exit 1 and both of them deliberate:
#
#   * Something is printing. Real, and an operator has to clear it — this script
#     will not guess that a print is dead. A job that has been PRINTING since
#     Friday is a number the farm measured and nothing here is entitled to
#     overrule it; the fix is to cancel the job, not to weaken the guard.
#   * The farm could not be asked — the API is down, the proxy is down, the
#     network is down, the route was renamed. An unanswered question is not
#     permission. Every one of those failures is silent and would otherwise read
#     as "quiet", which is why they are all folded into the same non-zero.
#
# 'set -u' but deliberately NOT 'set -e': this script reads exit codes itself and
# reports them, rather than dying at the first non-zero and leaving the caller to
# guess what happened. And no 'cmd | tail' anywhere — a pipeline returns the exit
# status of its LAST command, so piping curl into anything would throw away the
# only answer this script has (the repo's own trap; see the root CLAUDE.md §4 and
# the same note in deploy/readiness-check.sh).

set -u

URL="${PRINTORIAN_HEALTH_URL:-http://127.0.0.1:8080/api/health/printing}"
TIMEOUT="${PRINTORIAN_GUARD_TIMEOUT:-5}"

# -f makes curl fail on 4xx/5xx, which is how the endpoint says "in flight" and
# how it says "unreadable" — both are 503 there, on purpose, so that a guard this
# simple defers without having to parse anything. -sS keeps it quiet while still
# printing the reason it failed.
body=$(curl -fsS --max-time "$TIMEOUT" "$URL" 2>&1)
status=$?

if [ "$status" -ne 0 ]; then
  echo "reboot-guard: DEFER — could not get an affirmative from $URL (curl exit $status)"
  echo "reboot-guard: ${body:-no response body}"
  echo "reboot-guard: 503 means the farm is printing or could not read its own database;"
  echo "reboot-guard: any other failure means the question never reached the farm."
  exit 1
fi

# A 200 is necessary and NOT sufficient. A captive portal, a proxy error page, a
# future rename of this route to something that still answers 200, or the health
# path being swallowed by a catch-all would each hand this script a cheerful 200
# carrying no verdict at all. So the affirmative has to be spelled out in the
# body before it counts as one.
if printf '%s' "$body" | grep -q '"status":"quiet"'; then
  echo "reboot-guard: PROCEED — nothing is on a machine"
  echo "reboot-guard: $body"
  exit 0
fi

echo "reboot-guard: DEFER — $URL answered 200 but did not say it was quiet"
echo "reboot-guard: $body"
exit 1
