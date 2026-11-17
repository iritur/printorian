# The Printorian harness (`.zcode/`)

A development studio for this repo: role agents, knowledge skills, saved
dynamic-workflows, an evidence memory, and one guard hook. The architecture is
ported from the lumen project's `.zcode/` harness and rebuilt around
Printorian's own rules — the six gates instead of ctest, the ADRs instead of a
layer graph, GitHub issues instead of a TODO ledger, HANDOFF.md as the
session state.

## Layout

```
.zcode/
├── config.json                  hook wiring (PreToolUse → the guard below)
├── hooks/protect-generated-paths.sh
├── agents/                      9 role agents (planner, architect, researcher,
│                                implementer, reviewer, tester, test-analyst,
│                                gatekeeper, doc-writer)
├── skills/                      build-test, decisions, testing, studio (router),
│                                memory-writer (format)
├── memories/                    evidence archive + MEMORY.md index
├── workflows/                   printorian-gated-change, printorian-development-studio,
│                                printorian-docs-audit (.dwf.ts, saved)
└── commands/                    /gated-change, /studio, /docs-audit — slash
                                 commands that launch the workflows above
```

## Precedence

`AGENTS.md` (binding session rules) → `docs/` (what the system *is*; ADRs are
locked) → `.zcode/skills/` (settled operational knowledge) →
`.zcode/memories/` (historical evidence — explains a rule, never proves
current behavior). Open work lives in GitHub issues only; there is
deliberately no TODO file here.

## Running the workflows

With `CreateWorkflow`, `saved` source:

- **printorian-gated-change** — one task end to end:
  `args: { task: "…", issue: 27, known_red: [] }`. Preflight → baseline six
  gates → branch → implement → fix loops (≤3) → independent review → bounded
  parallel confirmation (Flash) → confirmed-fix round → final gate with the
  full ~15-min suite (+ frontend gates and client regen when touched) →
  change report + PR text. Never pushes.
- **printorian-development-studio** — a multi-slice request: planner → task
  cards on a board → per task (sequential; shared test DB) a worktree under
  `~/printorian-wt/<id>` with resume, then the same gated pipeline plus a
  gatekeeper audit → one full-suite run for the whole run → memory + HANDOFF
  record → studio report. Linux host only. Never pushes.
- **printorian-docs-audit** — "are the docs still true?": one Flash auditor
  per document verifies status claims/counts/issue refs against the code,
  findings confirmed and ranked, `drift` issues drafted via `gh` only after
  explicit approval.

Test gating (owner choice, 2026-11-17): fast gates per task, scoped pytest
per task, the full suite once at the final gate. Never two pytest sessions at
once. Environment missing → the workflows ask (hole) before bootstrapping;
`allow_bootstrap: true` opts into the documented DEVELOPMENT.md setup.

## What the hook blocks

Hand edits of generated or credential paths — `frontend/packages/api-client/
src/generated/`, `backend/openapi.json`, `docs/assets/banner*.svg`,
`backend/printers.local.toml` — each with its regeneration command (see
`hooks/protect-generated-paths.sh`). Regeneration through Bash is untouched.

## Two repos

This harness serves the application. Host-layer work (the custom minimal
Arch server) lives in `printorian-server` (GitHub origin, gitverse mirror)
— which now carries its own port of this harness (`psrv-*`, commit
`c28a328`, 2026-11-17) built around the real-packages principle: PKGBUILD →
clean-chroot build → repo-add → throwaway-root verification, with system
installs reserved to the owner and `psrv-package-pipeline` as the
automation. Its charter is recorded here in
`memories/printorian-server-charter.md` (and, self-contained, in the server
repo's own memories). The application stays containerized regardless
(`memories/printorian-deployment-shape.md`).

## How memories grow

At the end of a studio run (or any session that learned something the hard
way): a record per measured lesson, the format in
`skills/printorian-memory-writer/`, indexed in `memories/MEMORY.md`, committed
as `memory: <topic>` in the main checkout. Extend, never fork; cite sources;
disproven theories are the point.
