# Runbook — backup, restore, disaster recovery

ARCHITECTURE §10 and ROADMAP phase 7 both promise this and neither had it. What
existed was a named Docker volume.

The rule this document exists to enforce is the one already written into the
architecture: **untested backups are not backups.** Everything below is arranged so
that a restore has been performed recently by a machine, not merely planned by a
person.

---

## 1. What is backed up, and why twice

| Artifact | Made by | Recovery point | Survives |
|---|---|---|---|
| **WAL archive** + base backup | PostgreSQL `archive_command`, `pg_basebackup` | ~1 minute | disk loss, accidental `DELETE`, a bad deploy |
| **Logical dump** (`pg_dump -Fc`) | `scripts/backup.sh`, nightly | last night | block corruption, a botched major-version upgrade, *and* the physical backup itself being bad |
| **Blob store** (once Slice A lands) | file sync, nightly | last night | the same |

Two database artifacts because they fail differently. A physical backup is a copy
of the files, so it carries any corruption in them; a logical dump is a
re-materialisation, so it does not — but it can only be restored to the moment it
was taken.

**The minute matters.** A nightly dump alone means an outage at 23:00 loses a day
of orders and, worse, a day of `payment_notifications` — the record of what the
payment gateway actually told us. Reconstructing money movements from a
provider's dashboard by hand is the kind of week nobody plans for.

---

## 2. Daily operation

WAL archiving is on in `docker-compose.yml` and in `deploy/compose.prod.yml`. It is
configured there rather than left to the operator because `archive_mode` cannot be
changed without a restart, and a farm that has been running for a year is exactly
where nobody wants to discover that.

**Check it once, on the first day.** This paragraph used to end "and needs nothing
further", which was wrong in the worst possible way: the archive target is mounted
into a container that runs as uid 70, and the mount is owned by root until somebody
changes it. The server then cannot write a single segment. Nothing surfaces — the
farm runs normally, `archive_command` fails silently on every segment, and because
a segment cannot be recycled until it is archived, `pg_wal` grows until the data
disk fills and PostgreSQL stops. Meanwhile the recovery point this table promises
does not exist. On the development stack that ran to 1 385 consecutive failures,
zero successes and 23 GB of WAL before anybody looked.

The `backup-init` service now chowns the mount before PostgreSQL starts, so a fresh
deployment is correct by construction. Verify anyway — it costs one command, and it
is the only thing that distinguishes a working backup from a believed one:

```bash
docker exec printorian-postgres psql -U printorian -d printorian   -c "select archived_count, failed_count, last_failed_error from pg_stat_archiver;"
```

`failed_count` must be 0 and `archived_count` must climb. If it does not, nothing
below this line will save the farm, and §6's monitoring signal is the alert that
should have told you.

Nightly, on the farm's server. The script runs on the **host** and connects over
the network — it needs `pg_dump`, `pg_basebackup` and `pg_archivecleanup` from a
matching PostgreSQL client package, and `PRINTORIAN_BACKUP_ROOT` pointing at the
same directory the container's `archive_command` writes into:

```bash
PRINTORIAN_BACKUP_ROOT=/srv/printorian/backup PRINTORIAN_DB_PORT=5433 backend/scripts/backup.sh
```

If the client tools are not installed on the host, run it through the image that
already has them, mounting the script and the backup directory:

```bash
docker run --rm --network host -v /srv/printorian/backup:/backup -v "$PWD/backend/scripts:/scripts" -e PGPASSWORD -e PRINTORIAN_DB_PORT=5433 postgres:17-alpine /scripts/backup.sh
```

Weekly, a drill (see §5). Both belong in cron or a systemd timer, and both should
mail their output on failure.

`/backup` must be on a **different disk** from the data directory. A backup that
dies with the disk it is protecting against is decoration. The `docker-compose.yml`
default is a named volume, which is right for a developer machine and wrong for the
farm — replace it with a bind mount onto the backup disk there.

### Off-site

Local-only backups on a single on-prem box are one fire from total loss.

```bash
restic -r <remote> backup /backup/dumps /backup/base /backup/wal
```

**Encrypt before it leaves.** The dump contains every `password_hash` and every
`printers.access_code_encrypted`. `restic` encrypts by default; `rclone` needs
`crypt` configured. Store that repository password somewhere other than the farm —
see §6.

---

## 3. On-prem PostgreSQL settings

`docker-compose.yml` carries dev-scale numbers, because developers run it too.
On the farm's dedicated box, derive them from its RAM:

| Setting | Value | Why |
|---|---|---|
| `shared_buffers` | 25% of RAM | the standard starting point; the rest is left to the OS page cache |
| `effective_cache_size` | 60–75% of RAM | not an allocation — it tells the planner how much cache to *assume*, and getting it wrong makes it choose sequential scans over index scans |
| `work_mem` | RAM ÷ (`max_connections` × 3) | per sort, per node — a generous value times a busy moment is how a box runs out of memory |
| `maintenance_work_mem` | 512 MB – 2 GB | index builds and autovacuum; higher is materially faster |
| `max_connections` | `db_pool_size + db_max_overflow` per process, plus headroom | must exceed what `core.config` lets the application open, or the pool exhausts against the server rather than against itself |

The application's own guards — `statement_timeout`, `lock_timeout`,
`idle_in_transaction_session_timeout` — are set per-connection in
`core/db.py`, so a restore onto a fresh box inherits them without anyone
remembering to.

---

## 4. Restoring

### 4.1 From the logical dump — wrong data, right disk

The common case: something was deleted or corrupted by the application, the disk
is fine. Restore into a scratch database first and copy across; do **not** restore
over the live one.

```bash
createdb -U printorian printorian_recovered
pg_restore -U printorian -d printorian_recovered --no-owner /backup/dumps/printorian-<stamp>.dump
```

### 4.2 Point-in-time — "undo the last twenty minutes"

The case a nightly dump cannot serve: a bad migration or a bad deploy at a known
time.

1. Stop the API and the workers. Leaving them running means new writes racing a
   recovery, and it is the workers that matter — the scheduler will happily
   dispatch against a half-restored queue.
2. Move the current data directory aside. Do not delete it: if the recovery
   target turns out to be wrong you will want it back, and it is also the only
   remaining copy of anything written after the last WAL segment was archived.
3. Restore the most recent base backup into a clean data directory.
4. Write `postgresql.auto.conf`:
   ```
   restore_command = 'cp /backup/wal/%f %p'
   recovery_target_time = '2026-08-11 14:20:00+03'
   recovery_target_action = 'promote'
   ```
5. `touch recovery.signal` in the data directory, then start PostgreSQL and watch
   the log until it reports consistent recovery.
6. **Verify before letting anything write.** Check the row you know was lost is
   back and the row you know was bad is gone. Then start the API, then the workers
   — in that order, so a human sees the state before the scheduler acts on it.

### 4.3 Bare metal — the box is gone

1. Install PostgreSQL of the **same major version**. A physical backup will not
   restore across major versions; this is exactly the case the logical dump covers
   if the version is unavailable.
2. Restore the base backup and replay WAL as in §4.2, with no
   `recovery_target_time` — replay everything.
3. Restore the blob store to the path `PreparedPlate.storage_path` refers to.
4. **Restore `PRINTORIAN_SECRET_KEY` from wherever it is escrowed** (§6).
5. Run `alembic check` before starting the application. If it reports drift, the
   backup predates a migration and `alembic upgrade head` comes first.

---

## 5. The drill

```bash
python -m scripts.restore_drill /backup/dumps/printorian-<latest>.dump
```

It restores last night's dump into a throwaway database, runs `alembic check`
against it, and asserts that `users`, `orders` and `payment_notifications` are not
empty.

That last check is the one that earns its place. A backup script pointed at the
wrong database name produces a perfectly valid, perfectly restorable, **empty**
dump every night, and every other check passes. It is the failure most likely to
go unnoticed for months and the one most catastrophic to discover during an
incident.

Run it weekly. A failing drill is an incident in its own right — the farm is
currently running without a usable backup, whatever the file listing suggests.

---

## 6. Secrets, and the half-restore

The dump contains `password_hash` for every account and
`access_code_encrypted` for every printer. Two consequences:

- **Backups are secret material.** Encrypt them at rest and in transit. Anyone who
  can read the archive can attack every password in it offline.
- **`PRINTORIAN_SECRET_KEY` must be escrowed separately from the backups.** The
  printer access codes are encrypted with it (ADR-0014), so a restored database
  without that key gives back every order, every price and every job — and a fleet
  that cannot be driven until someone walks to each machine and reads its code off
  the panel. That is a half-restore, and it is discovered during the incident.

  Escrow it somewhere that does not depend on the farm: a password manager, a
  sealed envelope, anywhere at all that is not the box being restored.

---

## 7. Blob store and database must be consistent

Once Slice A lands, the database holds `storage_path` references and the disk holds
the bytes. A restored database naming a plate that is not in the restored blob
store is a broken cache — discovered at dispatch, silently, when a job uploads
nothing to a printer.

Two rules keep them consistent, and they are cheap:

1. **Write the blob before the row.** Then a row can only ever reference a blob
   that already existed.
2. **Snapshot the blobs before the database.** Combined with (1), the database can
   only reference blobs already in the snapshot. The reverse order guarantees the
   opposite.

Content-addressing (SHA-256 paths) makes the blob sync cheaply incremental and
makes a partial sync detectable: the name *is* the checksum.

---

## 8. Degraded mode

ROADMAP lists it as a standing risk: printers keep printing while the server is
down and reconcile on reconnect. Worth confirming during any recovery — a job the
database believes is `PRINTING` may have finished an hour ago, and the reconnect
has to be able to absorb that rather than treat it as an impossible transition.

---

## 9. What is not covered yet

- **Anything that runs these scripts on a schedule.** Both work — `backup.sh` was
  run end to end on 2026-08-22 and produced a verified dump and a base backup, and
  `restore_drill.py` restored that dump into a scratch database with the schema at
  head. Nothing invokes either of them, and nothing would notice if a run failed.
  Stage 2 of `INFRASTRUCTURE.md` is where the systemd timers live.

  **This is a disk-fill risk, not just a missing backup.** `pg_archivecleanup`
  runs *inside* `backup.sh` and nowhere else, so until something schedules it,
  archived WAL accumulates without bound. The development stack reached 847
  segments — 13.9 GB — in four days of light use, and the comment on
  `deploy/compose.prod.yml` records the earlier version of this failure at 23 GB.
  A farm left unscheduled fills its backup disk and then stops archiving, which is
  the state in which the backup guarantee silently stops holding.

- **Automated off-site sync.** The recipe is in §2; the destination and its
  credentials are farm-specific and not committed here.
- **Telemetry rollups.** `telemetry_retention_days` defaults to `0` — retention is
  *off* — because dropping a partition is irreversible and, until rollups exist
  (Slice G), the raw samples are the only copy of what the farm measured. Turn
  retention on in the same change that starts summarising them.
