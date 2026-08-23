#!/usr/bin/env bash
# Bring the farm back to the state systemd says it should be in.
#
# `printorian.service` is `Type=oneshot` with `RemainAfterExit=yes`, which means
# "ExecStart returned 0 once" and *not* "the farm is running". The two come apart,
# and it was observed on the first farm host within an hour of the unit existing:
#
#     systemd says: active | containers running: 0
#
# Anything reading `systemctl is-active printorian.service` gets a green light for
# a farm that is serving nothing, and `systemctl start` will not fix it — systemd
# believes the job is already done, so only `restart` re-runs ExecStart.
#
# Docker's own `restart: unless-stopped` handles a *crashed* container and handles
# it well (measured: SIGKILL to the API, healthy again in ten seconds). What it
# does not handle is a container that is absent rather than dead — after a
# `compose down`, a failed image pull mid-deploy, or a partial `up`. That gap is
# what this closes.
#
# Run by `printorian-ensure.timer`. Idempotent: on a healthy farm it is a no-op.

set -euo pipefail

COMPOSE_FILE="${PRINTORIAN_COMPOSE_FILE:-deploy/compose.prod.yml}"

# The services that are supposed to be up at all times. `migrate` and
# `backup-init` are deliberately absent: they are one-shots, and a correct farm
# has both of them exited 0. Counting "all services" would make the healthy state
# look broken forever.
EXPECTED_RUNNING=5

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

# Reconcile toward systemd's *intent*, not toward "always up". An operator who ran
# `systemctl stop printorian.service` has said the farm should be down; a timer
# that restarts it five minutes later is not self-healing, it is a service that
# cannot be switched off. Silent exit rather than a failure: a deliberately
# stopped farm is not an error condition.
if ! systemctl is-active --quiet printorian.service; then
  exit 0
fi

running() { docker compose -f "${COMPOSE_FILE}" ps -q --status running 2>/dev/null | wc -l; }

before="$(running)"
if [[ "${before}" -ge "${EXPECTED_RUNNING}" ]]; then
  exit 0
fi

# Deliberately loud. A farm that quietly repairs itself every five minutes looks
# identical to a farm that never breaks, and the difference is the whole point:
# this line is the only evidence that something is wrong underneath.
log "RECONCILING: ${before}/${EXPECTED_RUNNING} services running, bringing the stack up"

docker compose -f "${COMPOSE_FILE}" up -d --wait

after="$(running)"
if [[ "${after}" -lt "${EXPECTED_RUNNING}" ]]; then
  # Exit non-zero so the unit lands in `systemctl --failed` and, once alerting
  # exists, pages. A reconcile that cannot reconcile is the actual incident.
  log "RECONCILE FAILED: ${after}/${EXPECTED_RUNNING} running after up --wait"
  exit 1
fi

log "reconciled: ${before} -> ${after}/${EXPECTED_RUNNING} running"
