---
name: printorian-testing
description: Printorian's verification discipline — the change-surface to required-evidence table (pricing, scheduler, drivers, migrations, API routes, frontend, docs), the evidence contract every run must satisfy (argv, cwd, exit code, counts, sha256, who ran it), the exact status vocabulary, and the independence/no-weakening/serialization rules. Use when planning what proof a change needs, when executing gates, or when judging someone else's results.
---

# Verification discipline

"A feature is done when a test proves it" (D13). This file defines what
*proves* means per change surface, what evidence a run must leave behind,
and the words used to report status. Tester, analyst, gatekeeper, and the
workflows all use these definitions — do not invent synonyms.

## 1. Change surface → required evidence

| Change surface | Required evidence (beyond the six gates) |
|---|---|
| Any backend code | Six gates green on the final diff. |
| New functionality | A test that fails without the change (break it on purpose once) and passes with it. |
| `contexts/pricing` | Purity gates (import-linter) + pinned-snapshot tests + property tests; quote reproducibility asserted against a persisted `RateSnapshot`. |
| `contexts/scheduling` | Assignment-audit tests: every plan() pass writes `AssignmentDecision`/`AssignmentRecord`; wait-list only when time alone fixes the wait. |
| `drivers/*` | Contract tests against recorded fixtures; `-m hardware` runs only with a real printer (`PRINTORIAN_HARDWARE`), else `UNSUPPORTED`. |
| Migration | `upgrade head` → `downgrade base` → `upgrade head` → `alembic check` + `tests/test_migrations.py`; expand/contract so app rollback never needs schema rollback. |
| API route or DTO | `tests/api/` coverage + regenerated client (`tools/export_openapi.py` → `npm run generate:api`) + `apps/console/src/dashboard/types.ts` mirror updated when `GET /dashboard` changed + frontend `typecheck`. |
| Frontend | The four gates (`typecheck`, `lint`, `test`, `build`), separately. |
| Docs-only | Every status claim verified against code; numbers cite the command that produced them. |
| Events / WebSocket | Contract of ADR-0015 preserved: invalidation not state; refetch-on-resync covered by a test. |
| Money paths | Decimal-only assertions; refund ≤ amount; SLA credit recomputed-not-accumulated, capped at total. |

Release-gate-only checks (not per-change): `python -m printorian.workers
--check` and `tools/relay_probe.py`.

## 2. Evidence contract

Every run that gates anything records:

- the **exact argv** and the **working directory**;
- the **exit code** — read from the command itself, never through a pipe
  (`cmd > /tmp/out.log 2>&1; echo "exit=$?"`);
- the **counts the output actually printed**, quoted (`37 passed, 1 failed
  in 412.33s`), not summarized;
- **artifact paths** (logs, exported OpenAPI, screenshots) with **sha256**
  when they gate anything;
- **who ran it** — agent identity and model — because independence is
  checked at the gate.

The database a pytest run used is part of the evidence: PostgreSQL on 5433
with `PRINTORIAN_ENVIRONMENT=test`, one session at a time (shared
`printorian_test`, truncated between sessions — an overlapping run is
`INVALID`, see §3).

## 3. Status vocabulary (exact)

| Status | Meaning |
|---|---|
| `PASS` | Ran to completion, exit 0, evidence complete. |
| `FAIL` | Ran to completion, exit non-zero. Quote the failures. |
| `NOT RUN` | Deliberately not executed (out of scope, budgeted out). Name it in `notCovered`. |
| `UNSUPPORTED` | Cannot run on this host by design (`-m hardware` without a printer). |
| `COULD NOT RUN` | Environmental blocker (Docker down, database unreachable, no venv). Never converts to success. |
| `INVALID` | Evidence contaminated: overlapping pytest sessions, output lost to a pipe, wrong database, wrong base. Rerun or discard — never interpret. |

## 4. Discipline rules

- **Independence.** The agent that implemented does not review; the agent
  that ran does not analyze; repeated invocations of one agent are not
  independent stages. The gate checks this from the evidence's "who ran
  it".
- **Serialize pytest.** One session at a time, host-wide. A pipeline runs
  backend suites sequentially even when its other stages fan out.
- **No weakening to pass.** No `noqa`, skip marker, threshold change, or
  `known_red` addition made to make a gate green. Weakening is a change
  with its own review. Prefer removing the cause to silencing the symptom
  (CLAUDE.md §5).
- **Environmental failure policy.** `COULD NOT RUN` → fix the environment,
  rerun once; still blocked → surface to the owner with what ran and what
  did not.
- **Regression stop.** A red test you did not touch stops the line: report
  it; fixing, tolerating, or bisecting is the owner's call.
- **Scoped ≠ full.** A scoped pytest run is labeled with its paths; only
  `python -m pytest -q` (the whole suite, ~15 min) may be reported as the
  suite.
