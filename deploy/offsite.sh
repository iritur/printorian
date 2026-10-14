#!/bin/sh
# Copy the farm off the farm: the blob store, then the backups, into a restic
# repository somewhere else (ADR-0019 §3, issue #16).
#
# Everything `scripts/backup.sh` produces — the nightly dump, the base backups,
# the archived WAL — sits on `/mnt/backup`, a second disk in the *same box*. That
# protects against a disk dying and against corruption. It does nothing about a
# fire, a theft, or the machine being wiped, and the farm's whole history is one
# such event from gone.
#
# Runs inside the pinned restic image (`printorian-offsite.service`), with the
# blob store at /data/blobs and the backup disk at /data/backup, both read-only:
# this job copies and never alters what it copies. The repository and its
# credentials arrive as restic's own environment variables — `RESTIC_REPOSITORY`,
# `RESTIC_PASSWORD_FILE` (or `RESTIC_PASSWORD`) and, for object storage,
# `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` — rendered from the SOPS store,
# never typed into a shell. `deploy/tests/offsite.test.sh` runs this exact file
# against a local repository in CI.
#
# **Blobs first, then the database — the order is the point.** ADR-0019: blobs
# are written before the rows that name them, and snapshotted before the
# database. A database restored from a snapshot taken *after* the blobs can only
# name blobs that snapshot holds; the other order restores rows pointing at
# files that are not there, which is the half-restore discovered mid-incident.
# So a failed blob snapshot stops the run before the database is copied: a
# database copy with no matching blob copy is not a recovery point.
#
# **What it refuses, and why.** It does not create the repository: `restic init`
# is a one-time step a person runs (RUNBOOK §2, Off-site), because a script that
# initialises on demand turns a mistyped bucket name into a fresh, empty
# repository that every night's run then reports success into. It also refuses
# to run when there is no dump to copy: a backup disk that is not mounted looks
# exactly like an empty one, and a successful off-site copy of nothing is the
# worst kind of green.

set -eu

BLOBS="${PRINTORIAN_OFFSITE_BLOBS:-/data/blobs}"
BACKUP="${PRINTORIAN_OFFSITE_BACKUP:-/data/backup}"

# A fixed host name, because restic groups snapshots by host and the container
# this runs in gets a new random one every night. Without it `forget` sees each
# night as a different machine and keeps everything for ever.
HOST="${PRINTORIAN_OFFSITE_HOST:-printorian-farm}"

# How many snapshots survive `forget`. A judgement, not a measurement: a week of
# dailies to undo last week, then a monthly back half a year for the corruption
# nobody noticed. restic de-duplicates, so the WAL and the blobs that did not
# change cost almost nothing to keep.
KEEP="${PRINTORIAN_OFFSITE_KEEP:---keep-daily 7 --keep-weekly 4 --keep-monthly 6}"

log() { printf '%s offsite: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
fail() { log "FAILED: $*"; exit 1; }

# ------------------------------------------------------------- preconditions
#
# All checked before anything is written, so a run that refuses leaves the
# repository exactly as it found it.

[ -n "${RESTIC_REPOSITORY:-}" ] || fail "RESTIC_REPOSITORY is not set"
[ -n "${RESTIC_PASSWORD_FILE:-}${RESTIC_PASSWORD:-}" ] \
  || fail "neither RESTIC_PASSWORD_FILE nor RESTIC_PASSWORD is set"

[ -d "${BLOBS}" ] || fail "blob store ${BLOBS} is not mounted"
for part in dumps base wal; do
  [ -d "${BACKUP}/${part}" ] || fail "${BACKUP}/${part} is missing — is the backup disk mounted?"
done
# `ls` rather than a glob test: a glob that matches nothing stays literal in sh.
ls "${BACKUP}/dumps/"*.dump > /dev/null 2>&1 \
  || fail "no dump in ${BACKUP}/dumps — nothing worth copying, and copying nothing is not a success"

# `cat config` reads the repository without changing it: it fails on a wrong
# address, a wrong password, or a repository nobody initialised.
restic cat config > /dev/null \
  || fail "cannot open ${RESTIC_REPOSITORY} — wrong address or password, or never initialised (restic init)"

# ------------------------------------------------------------------ the copy

log "blobs: ${BLOBS}"
restic backup --host "${HOST}" --tag blobs "${BLOBS}"

# The two exclusions are files still being written: `backup.sh` fills a dump as
# `.partial` and renames it once it verifies, and `archive_command` writes a
# segment as `.tmp` before moving it into place. Copying either is copying a
# file that is not one yet.
log "database: ${BACKUP}"
restic backup --host "${HOST}" --tag database \
  --exclude '*.partial' --exclude '*.tmp' \
  "${BACKUP}/dumps" "${BACKUP}/base" "${BACKUP}/wal"

# ------------------------------------------------------------- retention

log "forget: ${KEEP}"
# shellcheck disable=SC2086 # KEEP is a list of flags, split on purpose.
restic forget --host "${HOST}" --group-by host,tags --prune ${KEEP}

# Structure only, not `--read-data`: reading every pack back is a download of
# the whole repository each night, which object storage charges for. Whether the
# data restores is what the off-site drill is for (RUNBOOK §5).
restic check

log "complete"
