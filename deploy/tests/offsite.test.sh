#!/usr/bin/env bash
# Proves `deploy/offsite.sh` against a real restic repository (CI job `offsite`).
#
# The repository is a local directory rather than object storage, which is the
# one difference from the farm: restic speaks to both through the same code
# above its backend, and what is being proven here — what is copied, in what
# order, what is refused, and that it restores byte for byte — lives above it.
# The script runs in the same pinned image, with the same mounts and the same
# environment variables, as `printorian-offsite.service`.
#
#   RESTIC_IMAGE=restic/restic:0.18.0 bash deploy/tests/offsite.test.sh
#
# Runs from Git Bash on Windows too: paths handed to Docker are converted, and
# MSYS's own path mangling is switched off.

set -euo pipefail

IMAGE="${RESTIC_IMAGE:?RESTIC_IMAGE must name the restic image to test with}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="${HERE}/../offsite.sh"
export MSYS_NO_PATHCONV=1

host_path() { if command -v cygpath > /dev/null; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

WORK="$(mktemp -d)"
# The repository is written by root inside the container, so the runner's user
# may not be able to delete it; the container that wrote it can.
cleanup() { docker run --rm -v "$(host_path "${WORK}"):/w" --entrypoint /bin/sh "${IMAGE}" -c 'rm -rf /w/*' || true; rm -rf "${WORK}" || true; }
trap cleanup EXIT

mkdir -p "${WORK}/blobs/models" "${WORK}/backup/dumps" "${WORK}/backup/base/20261014T030000Z" \
  "${WORK}/backup/wal" "${WORK}/repo" "${WORK}/restore"
printf 'solid' > "${WORK}/blobs/models/a.stl"
printf 'plate' > "${WORK}/blobs/models/b.3mf"
head -c 65536 /dev/urandom > "${WORK}/backup/dumps/printorian-20261014T030000Z.dump"
printf 'half' > "${WORK}/backup/dumps/printorian-20261015T030000Z.dump.partial"
printf 'base' > "${WORK}/backup/base/20261014T030000Z/base.tar.gz"
printf 'wal' > "${WORK}/backup/wal/000000010000000000000001.gz"
printf 'wip' > "${WORK}/backup/wal/000000010000000000000002.tmp"
printf 'correct horse' > "${WORK}/password"

W="$(host_path "${WORK}")"
MOUNTS=(
  -v "${W}/blobs:/data/blobs:ro"
  -v "${W}/backup:/data/backup:ro"
  -v "${W}/repo:/repo"
  -v "${W}/password:/run/secrets/restic:ro"
  -v "$(host_path "${SCRIPT}"):/offsite.sh:ro"
)
ENV=(-e RESTIC_REPOSITORY=/repo -e RESTIC_PASSWORD_FILE=/run/secrets/restic)

offsite() { docker run --rm "${MOUNTS[@]}" "${ENV[@]}" "$@" --entrypoint /bin/sh "${IMAGE}" /offsite.sh; }
# Anything run against the repository afterwards, to look at what it holds.
inspect() {
  docker run --rm "${MOUNTS[@]}" -v "${W}/restore:/restore" "${ENV[@]}" \
    --entrypoint /bin/sh "${IMAGE}" -c "$1"
}
snapshot_count() { inspect 'restic snapshots --json | jq length'; }

failures=0
check() {
  local name="$1"; shift
  if "$@"; then echo "ok   - ${name}"; else echo "FAIL - ${name}"; failures=$((failures + 1)); fi
}
refuses() { ! offsite "$@" > "${WORK}/out.log" 2>&1; }

# ---------------------------------------------------------------- refusals

repo_is_empty() { [ -z "$(ls -A "${WORK}/repo")" ]; }
check "refuses a repository nobody initialised" refuses
check "  ...and does not create one" repo_is_empty

inspect 'restic init' > /dev/null 2>&1

mv "${WORK}/backup/dumps/printorian-20261014T030000Z.dump" "${WORK}/held.dump"
check "refuses when there is no dump to copy" refuses
check "  ...and writes no snapshot at all, not even the blobs" test "$(snapshot_count)" = 0
mv "${WORK}/held.dump" "${WORK}/backup/dumps/printorian-20261014T030000Z.dump"

check "refuses a wrong password before writing anything" refuses -e RESTIC_PASSWORD_FILE= -e RESTIC_PASSWORD=wrong
check "  ...so the repository is still empty" test "$(snapshot_count)" = 0

# ---------------------------------------------------------------- the copy

check "copies when everything is in place" offsite
check "one snapshot of the blobs and one of the database" \
  test "$(inspect 'restic snapshots --json | jq -r "[.[].tags[0]] | sort | join(\",\")"')" = "blobs,database"
check "the blobs were snapshotted before the database (ADR-0019)" \
  test "$(inspect 'restic snapshots --json | jq -r "sort_by(.time) | map(.tags[0]) | join(\",\")"')" = "blobs,database"
check "half-written files are not copied" \
  test "$(inspect 'restic ls latest --tag database | grep -cE "\.(partial|tmp)$" || true')" = 0

inspect 'restic restore latest --tag database --target /restore/db && restic restore latest --tag blobs --target /restore/blobs' > /dev/null
check "the dump restores byte for byte" \
  cmp "${WORK}/backup/dumps/printorian-20261014T030000Z.dump" \
      "${WORK}/restore/db/data/backup/dumps/printorian-20261014T030000Z.dump"
check "the blobs restore byte for byte" \
  diff -r "${WORK}/blobs" "${WORK}/restore/blobs/data/blobs"

# ---------------------------------------------------------------- retention

# Three runs on one day keep two snapshots of each kind, not three: within a day
# `forget --keep-daily` keeps the newest, and restic (0.17 onwards) also keeps the
# oldest while there are fewer days than the policy asks for. So the middle run
# is forgotten. If each night's container counted as a new host — the random
# container hostname — every snapshot would be alone in its group, nothing would
# ever be forgotten, and this would read six.
twice() { offsite > /dev/null 2>&1 && offsite > /dev/null 2>&1; }
check "a second and a third run the same day succeed" twice
check "  ...and retention forgets the middle one, so the host is fixed" test "$(snapshot_count)" = 4

echo
if [ "${failures}" -ne 0 ]; then
  echo "${failures} check(s) failed"
  exit 1
fi
echo "all checks passed"
