# systemd units for the farm host

What runs the farm when nobody is logged in. Installed by the `printorian-farm`
Ansible role (INFRASTRUCTURE Stage 2); the units are kept here, as files, so they
are reviewable and testable without the role.

Everything here assumes the layout Stage 2 creates:

| Path | What |
|---|---|
| `/srv/printorian` | the checkout: `deploy/compose.prod.yml`, `.env`, scripts |
| `/mnt/backup` | dumps, base backups, archived WAL — **a different physical disk** (ADR-0019) |

## Install

```bash
sudo install -m 0644 deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now printorian.service
sudo systemctl enable --now printorian-backup.timer printorian-drill.timer printorian-ensure.timer
```

`deploy/reconcile.sh` must stay executable — it is committed `100755`, and a mode
lost in transit turns the reconciliation timer into a unit that fails every five
minutes.

## `printorian.service` reports `active` more readily than it should

`Type=oneshot` with `RemainAfterExit=yes` means *"ExecStart returned 0 once"*, not
*"the farm is running"*. The two come apart, and did so on the first farm host
within an hour of the unit existing:

```
systemd says: active | containers running: 0
```

`systemctl start` will **not** fix that — systemd believes the job is already
done, so only `systemctl restart` re-runs ExecStart. Two consequences worth
carrying:

- Never treat `systemctl is-active printorian.service` as a health signal. The
  honest checks are `/health/ready` on the API and the running container count.
- `printorian-ensure.timer` closes the gap: every five minutes it compares the
  number of running services against what should be up, and brings the stack back
  if it is short. It reconciles toward systemd's *intent* — a farm deliberately
  stopped stays stopped, because a service that cannot be switched off is a worse
  problem than the one being fixed.

Docker's own `restart: unless-stopped` already covers a *crashed* container
(measured on this host: SIGKILL to the API, healthy again in ten seconds). The
reconciler is for containers that are **absent** rather than dead — after a
`compose down`, a failed pull mid-deploy, or a partial `up`.

## Check it took

```bash
systemctl list-timers 'printorian-*'
```

Both timers should show a next elapse. A timer that is enabled but has no next
elapse is a timer that will never fire — usually a `Persistent=true` unit whose
`OnCalendar` never matches.

## Why timers rather than cron

`OnCalendar` with `Persistent=true` catches up a run the machine slept through,
which cron does not. A farm host is powered down more often than a server, and a
backup silently skipped because the shop was closed on Monday is the failure mode
this whole directory exists to prevent.

The other half is that a failed timer unit is visible to `systemctl --failed` and
carries the exit status and the journal with it. Cron's answer to a failed job is
an email to a mailbox nobody reads.

## What is deliberately not here

`printorian-deploy.timer` — the pull-based deploy loop of INFRASTRUCTURE §6. It
needs `deploy/production.yaml` and `cosign` verification, which are Stage 4. A
deploy timer without signature verification just moves the trust rather than
establishing it, so it waits for the piece that establishes it.
