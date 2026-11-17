---
name: printorian-studio
description: The router for Printorian's .zcode harness — the map of role agents, knowledge skills, and saved dynamic-workflows with exact paths; how to choose the orchestration for a request; workflow arguments; the binding owner rules every pipeline enforces; two-repo routing (app vs printorian-server); and the token-discipline / bounded-auditor contract learned from a measured 3.9M-token failure. Read this before dispatching harness work or editing any workflow.
---

# The Printorian development studio

## 1. Map

| Kind | Name | Path |
|---|---|---|
| Role agent | planner | `.zcode/agents/printorian-planner.md` |
| Role agent | architect | `.zcode/agents/printorian-architect.md` |
| Role agent | researcher | `.zcode/agents/printorian-researcher.md` |
| Role agent | implementer | `.zcode/agents/printorian-implementer.md` |
| Role agent | reviewer | `.zcode/agents/printorian-reviewer.md` |
| Role agent | tester | `.zcode/agents/printorian-tester.md` |
| Role agent | test-analyst | `.zcode/agents/printorian-test-analyst.md` |
| Role agent | gatekeeper | `.zcode/agents/printorian-gatekeeper.md` |
| Role agent | doc-writer | `.zcode/agents/printorian-doc-writer.md` |
| Knowledge | build & test | `.zcode/skills/printorian-build-test/SKILL.md` |
| Knowledge | decided questions | `.zcode/skills/printorian-decisions/SKILL.md` |
| Knowledge | verification discipline | `.zcode/skills/printorian-testing/SKILL.md` |
| Knowledge | this router | `.zcode/skills/printorian-studio/SKILL.md` |
| Knowledge | memory format | `.zcode/skills/printorian-memory-writer/SKILL.md` |
| Workflow | single gated change | `.zcode/workflows/printorian-gated-change.dwf.ts` |
| Workflow | multi-task studio | `.zcode/workflows/printorian-development-studio.dwf.ts` |
| Workflow | docs drift audit | `.zcode/workflows/printorian-docs-audit.dwf.ts` |
| Memory | archive index | `.zcode/memories/MEMORY.md` |

Launch a workflow with `CreateWorkflow` on its saved name plus args; repair
a running one with `AmendWorkflow`/`ResumeWorkflowRun`. Prefer a saved
workflow over hand-orchestrating the same stages — the guarantees
(independence, budgets, dispositions) are in the script, not in your
intentions.

## 2. Choosing the orchestration

| Request | Orchestration |
|---|---|
| One bug fix / small feature, scoped | `printorian-gated-change` with the task text |
| A multi-task effort (issue with several slices, refactor with steps) | `printorian-development-studio` |
| "Are the docs still true?" / periodic drift check | `printorian-docs-audit` |
| A question, not a change | `printorian-researcher` (or plain exploration) |
| A design decision, ADR impact | `printorian-architect` |
| Read docs/ before writing any code — always | every path starts from the code and the docs, in that order |

## 3. Workflow arguments (summary)

- **gated-change**: `task` (required), `branch` (default `zcode/gated-change`),
  `known_red` (json array of tolerated red tests), `allow_bootstrap`
  (default false — environment setup only with opt-in).
- **development-studio**: `task`, `base` (default HEAD), `branch_prefix`
  (`zcode/task`), `max_tasks` (5), `known_red`, `issue` (number — adds
  `Closes #N` to the PR text), `allow_bootstrap`.
- **docs-audit**: `docs` (json array of paths under `docs/`, default all),
  `file_issues` (default false — `drift` issues are drafted via `gh` only
  after approval).

Both change pipelines: fast gates per task, **scoped pytest per task, the
full ~15-min suite once at the final gate**; preflight escalates through
`hole()` when venv/Docker/node_modules are missing unless `allow_bootstrap`.

## 4. Binding rules (from the project owner)

1. Never push without explicit approval. PR text is produced, not pushed.
   When approved: SSH remote, `git@github.com:iritur/printorian.git`.
2. Ask when deviating — `hole()` exists for that; a workflow that cannot
   proceed honestly says so instead of improvising.
3. Never invent a number the farm did not measure (ADR-0007, repo-wide).
4. A feature is done when a test proves it (D13); the gatekeeper certifies
   evidence, never intent.
5. Never two backend test sessions at once — the pipelines run their suites
   strictly sequentially.
6. No issue is closed on an agent's own report of success (WORKFLOW.md);
   findings-worthy pre-existing defects are recorded (issue drafts with
   approval), never silently absorbed.
7. HANDOFF.md is updated at the end of any studio run (dated section).

## 5. Two repos

- **This repo (`printorian)** — the application: backend, frontend, docs,
  deploy. Everything in this harness targets it.
- **`printorian-server`** — the host layer: custom minimal hardened Arch
  system (kernel, pacman repo, ISO installer, backup service, monitoring),
  GitHub origin mirrored to gitverse.ru/printorian/server. Server work
  happens *there*, under its own ported harness (`~/printorian-server/
  .zcode/`, everything `psrv-*`, ported 2026-11-17) built around the
  real-packages principle — PKGBUILD → clean-chroot build → repo-add →
  throwaway-root verification, system installs reserved to the owner. The
  charter lives in `.zcode/memories/printorian-server-charter.md` (and,
  self-contained, in the server repo's own memories). The application
  remains containerized (decisions skill §3) regardless of what the host is
  built from.

## 6. Token discipline — the bounded-auditor contract

A verifier without bounds once burned 3.9M tokens confirming findings it
never read the code for. Every confirming/verifying stage in this harness
keeps these properties, and you keep them when editing the workflows:

- **Context in the ask**: the changed-file list and the exact command that
  shows the problem travel *with* the finding; the verifier never goes
  fishing.
- **Reading list or it does not count**: a finding without `path:line` and
  the one command that reproduces it is returned for refinement, not
  confirmed.
- **One follow-up, maximum** — an honest `inconclusive` beats a grind.
- **`webSearch: 0`** budget for repo-only stages.
- **Cheap model routing**: low-severity confirmations and memory writing go
  to GLM-5.3-Flash; judgment stages (review, gate) do not.
- **Parallel, capped**: confirmations fan out (`Promise.all`, ≤12), they do
  not queue behind one another.

## 7. Honesty fields

Every workflow returns `WorkflowReport { conclusion, findings, verified,
notCovered }`. `notCovered` is a first-class field populated on every early
exit: what ran, what never did, and why. A run that ends honestly red is a
working pipeline; one that ends vaguely green is not.
