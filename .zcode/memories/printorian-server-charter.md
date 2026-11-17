---
name: printorian-server-charter
description: "Owner charter 2026-11-17: printorian-server — a custom, minimal, hardened Arch Linux system purpose-built to host Printorian, in its own repo (GitHub origin, mirrored to gitverse.ru/printorian/server). Kernel config, custom pacman repo, hardware probe, ISO+TUI installer, backup service with drills, web monitoring; all tooling in Python with an SSH-friendly TUI. Supersedes INFRASTRUCTURE §3's Debian farm-host choice."
metadata:
  node_type: memory
  type: project
---

**Why:** The host is the one layer the farm cannot pull as a verified
image — so the owner chose to build it as a product instead of configuring
it by hand: reproducible from a repo, updatable through a package channel,
and minimal enough to audit. This is a charter, not an implementation: it
records intent and constraints until the repo exists and grows its own
docs and ADRs.

**How to apply:** Server-host work happens in `printorian-server`, never
in this repo (the application stays containerized —
[[printorian-deployment-shape]]). When that repo starts, port this harness
to its `.zcode/`. Nothing here licenses weakening the app repo's gates.

## The charter, verbatim from the owner (2026-11-17)

Goal: the fastest, most secure, most stable server on the Linux kernel +
Arch for Printorian servicing.

1. **Rebuild on dev (current machine), easy update on production.**
2. **A GitHub-hosted pacman repository** for our packages — updates land on
   the server via `pacman` (model on gnome-git's custom-repo flow, "but
   better and more secure").
3. **A universal probe script** that investigates and gathers everything
   about the target installation — hardware, environment, virtualization
   (it may be a VPS) — to correctly optimize the ISO to be built.
4. Repo lives at `printorian-server`, **origin on GitHub** (locally
   initialized 2026-11-17 at `~/printorian-server` with
   `git@github.com:iritur/printorian-server.git` as origin — the GitHub repo
   itself did not exist yet under either `dimmus` or `iritur` when checked
   via `gh`; create it before the first push), with a script **mirroring to
   gitverse** (`gitverse.ru/printorian/server`, already created empty). The
   close example the owner named — **`iritur/gnome-git`** — exists on GitHub,
   alongside **`iritur/pkgs`** (likely the package infrastructure to study).
5. **All scripts in Python.** TUI: nice, modern, technological — and
   correctly functional **through SSH** (Textual-class).
6. **Kernel configured and compiled for a printorian-only system**:
   minimal for security, latest kernel features, components the farm does
   not need switched off, taking the provided hardware specification into
   account.
7. **Best scheduler** for the server's purpose and the hardware's
   abilities.
8. **Best filesystem** for the server's purpose and the hardware's
   abilities.
9. **Optimization flags for the provided hardware** during all
   compilations (kernel and all software).
10. A **list of tools and accompanying software to recompile** for the
    purpose, plus the package installation list.
11. A script to **download new packages and rebuild them**.
12. A script to **create ISO images** for first installation, with a
    modern TUI for the install process and progress.
13. **All server configuration** for correct Printorian functionality.
14. **Database created and migrated, empty** — the app's
    RUNBOOK-FIRST-BOOT state (migrated schema, then a person provisions
    the owner).
15. A script to **check systems and set up health/status after
    installation**.
16. A **backup tool** on the installed server: different backup kinds,
    periodic stress/restore tests, a service to run backups with status,
    notifications and logs. Security matters (ADR-0019's drill discipline
    applies: an untested backup is not a backup).
17. The **best web tool to monitor** server system, component health and
    security status, configured the best way — aligned with, not
    duplicating, the app repo's documented observability target
    (VictoriaMetrics/Grafana/Alertmanager/Loki, INFRASTRUCTURE §3).

## Standing disciplines the charter inherits

- "Fastest/most secure" is a claim with a measurement plan or it is an
  assertion (ADR-0007 culture; the lumen no-degradation rule).
- Scripts that make requests: http/https only, host validated, localhost /
  loopback / private / reserved addresses refused. Scripts that touch the
  database: parameter-bound statements, never string-assembled SQL.
- Security choices (kernel hardening, minimality) are deliberate
  trade-offs recorded where they are made, so a future session can weigh
  them rather than re-litigate blindly.

## Amendments

- **2026-11-17 (later the same day):** the server repo now carries its own
  ported harness (`~/printorian-server/.zcode/`, all names `psrv-*`,
  commit `c28a328`) built around the real-packages principle, with two
  workflows — `psrv-gated-change` and `psrv-package-pipeline` (chroot
  build → repo-add → throwaway-root install verification, signing handed
  to the owner). Same-day owner rulings recorded there and binding here:
  **only latest upstream versions** ("install only latest packages"), and
  **the owner installs all system packages themselves** — agents surface
  exact `pacman -S` lines, never execute.

## Drift this creates on purpose

INFRASTRUCTURE.md §3 names Debian as the farm host. The owner's pivot to
Arch (`printorian-server`) supersedes that host choice; containers under
systemd and pull-based delivery are unchanged. File a `type:docs` / `drift`
issue for INFRASTRUCTURE.md rather than editing it unasked.

Related: [[printorian-deployment-shape]], [[printorian-host-layouts]].
