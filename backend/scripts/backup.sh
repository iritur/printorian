#!/usr/bin/env bash
# Nightly backup: a logical dump, plus a physical base backup.
#
# Two artifacts, on purpose, because they fail differently:
#
#   * pg_dump  — a logical dump. Restores onto any machine and any PostgreSQL
#                version, and survives the class of disaster a physical backup
#                does not (block corruption, a bad major-version upgrade). It is
#                a point-in-time snapshot: everything since it is lost.
#   * pg_basebackup + the archived WAL — physical, and the only thing that gives
#                point-in-time recovery. With WAL archiving on (docker-compose)
#                the recovery point is roughly the last minute rather than the
#                last midnight. For a system taking money through a payment
#                gateway that difference is a day of `payment_notifications` —
#                the exact rows needed to reconstruct what the gateway said.
#
# Neither is a backup until it has been restored. `restore_drill.py` does that,
# and the runbook explains why it is a scheduled job rather than a promise.
#
#   docs/RUNBOOK-BACKUP-RESTORE.md

set -euo pipefail

BACKUP_ROOT="${PRINTORIAN_BACKUP_ROOT:-/backup}"
DATABASE="${PRINTORIAN_DB_NAME:-printorian}"
DB_USER="${PRINTORIAN_DB_USER:-printorian}"
DB_HOST="${PRINTORIAN_DB_HOST:-localhost}"
DB_PORT="${PRINTORIAN_DB_PORT:-5432}"

# Retention. Kept generous: these compress well and the cost of one more week is
# nothing against the cost of discovering corruption that predates every copy.
KEEP_DAILY="${PRINTORIAN_KEEP_DAILY:-7}"
KEEP_WEEKLY="${PRINTORIAN_KEEP_WEEKLY:-4}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP_DIR="${BACKUP_ROOT}/dumps"
BASE_DIR="${BACKUP_ROOT}/base/${STAMP}"

mkdir -p "${DUMP_DIR}" "${BASE_DIR}" "${BACKUP_ROOT}/wal"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

# ---------------------------------------------------------------- logical

log "dumping ${DATABASE}"
# -Fc: the custom format. Compressed, and restorable selectively with pg_restore,
# which matters when the thing being recovered is one table somebody truncated
# rather than the whole database.
# Written under `.partial` and renamed only once it verifies, so a `*.dump` glob
# never matches a half-written file. The weekly restore drill picks its subject
# with `ls -t | head -1`; without the rename it would sometimes pick the dump this
# script is still filling and fail on it. A drill that cries wolf gets switched
# off, and then it is not a drill.
#
# `mv` within one directory is a rename, which is atomic. Copying across
# filesystems would not be.
DUMP="${DUMP_DIR}/${DATABASE}-${STAMP}.dump"

pg_dump \
  --host="${DB_HOST}" --port="${DB_PORT}" --username="${DB_USER}" \
  --format=custom --compress=9 \
  --file="${DUMP}.partial" \
  "${DATABASE}"

# Verified immediately rather than at restore time. A dump that cannot be listed
# is a dump that cannot be restored, and finding that out now costs seconds —
# finding it out during an incident costs the business.
pg_restore --list "${DUMP}.partial" > /dev/null
mv "${DUMP}.partial" "${DUMP}"
log "dump verified: ${DUMP}"

# --------------------------------------------------------------- physical

log "base backup"
pg_basebackup \
  --host="${DB_HOST}" --port="${DB_PORT}" --username="${DB_USER}" \
  --pgdata="${BASE_DIR}" --format=tar --gzip --checkpoint=fast \
  --write-recovery-conf --progress
log "base backup written: ${BASE_DIR}"

# -------------------------------------------------------------- retention

# Dumps first. WAL is pruned against the *oldest surviving base backup* below —
# never by age, because WAL older than the base backup it belongs to makes that
# backup unrestorable, and that is precisely the copy kept for the worst case.
find "${DUMP_DIR}" -name "${DATABASE}-*.dump" -mtime "+$((KEEP_DAILY + KEEP_WEEKLY * 7))" -delete

# Partials from runs that died mid-dump. They do not match the glob above — it
# ends in `.dump` — so without this they are the one thing here that grows
# forever, and each one is a full-sized dump. A day's grace so a currently
# running backup is never touched.
find "${DUMP_DIR}" -name "${DATABASE}-*.dump.partial" -mtime +1 -delete

find "${BASE_DIR%/*}" -maxdepth 1 -mindepth 1 -type d -mtime "+${KEEP_DAILY}" \
  | sort | head -n -"${KEEP_WEEKLY}" | xargs -r rm -rf

OLDEST_BASE="$(find "${BASE_DIR%/*}" -maxdepth 1 -mindepth 1 -type d | sort | head -n 1)"
if [[ -n "${OLDEST_BASE}" && -f "${OLDEST_BASE}/backup_manifest" ]]; then
  # `pg_archivecleanup` knows which segments the given backup still needs and
  # removes only what precedes them. Deleting WAL by age instead is the classic
  # way to end up with base backups that cannot be replayed.
  START_WAL="$(grep -oE '[0-9A-F]{24}' "${OLDEST_BASE}/backup_manifest" | head -n 1 || true)"
  if [[ -n "${START_WAL}" ]]; then
    pg_archivecleanup "${BACKUP_ROOT}/wal" "${START_WAL}"
    log "pruned WAL before ${START_WAL}"
  fi
fi

log "backup complete"

# ------------------------------------------------------------- off-site
#
# NOT done here, and that is a deliberate gap rather than an omission: an on-prem
# box whose only backups are on the same box is one fire from total loss, but the
# destination and its credentials are farm-specific. The runbook has the rclone
# and restic recipes, including why the archive must be encrypted before it
# leaves — it contains every password hash and every printer access code.
