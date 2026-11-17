# Working in this repository (ZCode harness)

Standing instructions for ZCode agents. They carry the same rules as
[CLAUDE.md](CLAUDE.md) — that file governs Claude Code sessions, this one
governs `.zcode/` harness sessions — and add what the harness needs to know
about itself. Where the two disagree, this file wins for ZCode sessions;
neither overrides [docs/](docs/) for what the system *is*.

**Read [HANDOFF.md](HANDOFF.md) before starting** — current state, what is
deliberately unfinished, and what needs a person rather than an agent. Update
it before finishing, as a dated `## YYYY-MM-DD — title` section with an
**As of:** line naming the branch and base commit.

**Open work lives in [issues](https://github.com/iritur/printorian/issues),
not in a document.** [docs/WORKFLOW.md](docs/WORKFLOW.md) is the authority on
the tracker: the label axes (`type:`, `area:`, `P0`–`P3`, `blocked:*`,
`needs-person`, `deferred`, `drift`), the milestones, and what closes an
issue. There is deliberately no TODO file in this harness — a second list is
how four documents each carrying a to-do list happened once before.

## 1. Binding owner rules

1. **Never push without explicit approval.** When approved, push over SSH —
   `git push git@github.com:iritur/printorian.git <branch>` — because the
   https remote has no credential helper. Never force-push `main`.
2. **Ask when deviating.** If a task's write scope, an ADR, or these rules
   block the obvious path, surface the decision instead of improvising around
   it. `needs-person` exists precisely for credentials and decisions an agent
   must not hold.
3. **Never invent a number the farm did not measure** (ADR-0007, repo-wide).
   A null reading is "not measured", not `0`. No synthesized telemetry, no
   fallback data, no "should be about" in anything a person will read.
4. **A feature is done when a test proves it** (D13) — and an agent's own
   report of success is not evidence. Reproduce it, then say so.
5. **Never run two backend test sessions at once.** They share the
   `printorian_test` database and truncate each other's tables.

## 2. Deployment shape — settled, do not re-litigate

- The application ships as **containers under systemd compose** — a modular
  monolith, one image per app, digest-pinned and cosign-signed, delivered
  pull-based. **No microservices** (boundaries are bought statically with
  import-linter; "the release unit of this system is the commit, not the
  service" — INFRASTRUCTURE §2).
- **No host-level installs of the application.** The digest/cosign/release-gate
  chain in CI is the trust boundary; `pip install` on a host runs code CI never
  proved.
- **Host-layer work lives in the sibling repo `printorian-server`** (custom
  minimal hardened Arch system: kernel, pacman repo, ISO installer, backup
  service, monitoring; mirrored to gitverse). Server work happens there, not
  here; its charter is recorded in
  `.zcode/memories/printorian-server-charter.md`. The pivot from the Debian
  farm-host choice in INFRASTRUCTURE §3 is an owner decision (2026-11-17) that
  still needs the doc amended — file a `drift`/`type:docs` issue rather than
  editing the doc unasked.

## 3. The harness map

| | |
|---|---|
| `AGENTS.md` | this file — binding session rules |
| `docs/` | what the system *is*; ADRs are locked (changing one needs a superseding ADR, not a commit) |
| `.zcode/skills/` | settled operational knowledge — commands, evidence rules, decided questions |
| `.zcode/memories/` | historical evidence archive — never citable as proof of current behavior |
| `.zcode/agents/` | role agents: planner, architect, researcher, implementer, reviewer, tester, test-analyst, gatekeeper, doc-writer |
| `.zcode/workflows/` | saved dynamic-workflows: `printorian-gated-change`, `printorian-development-studio`, `printorian-docs-audit` |
| `.zcode/hooks/` | the generated-path guard, wired from `.zcode/config.json` |

Precedence: this file → `docs/` → skills → memories. A memory can *explain* a
rule; it can never *be* one.

**Area rules live with the area** and are binding here too:
[backend/CLAUDE.md](backend/CLAUDE.md) (gates, layering, Alembic traps),
[frontend/CLAUDE.md](frontend/CLAUDE.md) (generated client, Harvester,
hand-written dashboard types). The operational commands behind both are in
`.zcode/skills/printorian-build-test/`.

## 4. Session protocol

1. Read HANDOFF.md; check the issue you are working (labels tell you whether
   it is even yours to move — `needs-person` and `blocked:*` are not).
2. Verify status claims against code before repeating them from any document.
   The docs go stale; three design-kit documents once described built features
   as missing long enough to cause real damage.
3. Work one task in one scope. Commit as a sentence referencing the issue
   (`(#NN)`); never amend what has been reviewed; never close an issue on your
   own report of success.
4. Quote real command output. `cmd > /tmp/out.log 2>&1; echo "exit=$?"` — a
   pipe to `tail` returns *tail's* exit code and has already hidden a failed
   `npm ci` here once.
5. Before finishing: update HANDOFF.md, record anything hard-won as a memory
   (`.zcode/skills/printorian-memory-writer/` is the format), and leave the
   tracker more truthful than you found it.
