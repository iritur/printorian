# Working in this repository

Standing instructions for Claude, and only the ones that apply everywhere.
[README.md](README.md) is the map and [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) is
the setup — neither is repeated here.

**Read [HANDOFF.md](HANDOFF.md) before starting** — current state, what is
deliberately unfinished, and what needs a person rather than an agent. Update it
before finishing.

**Area rules live with the area:**

| Working in | Read |
|---|---|
| `backend/` | [backend/CLAUDE.md](backend/CLAUDE.md) — gates, layering, Alembic, the ADRs that bite there |
| `frontend/` | [frontend/CLAUDE.md](frontend/CLAUDE.md) — gates, the generated client, Harvester, the hand-written dashboard types |

Those load automatically when work happens in their directory. If you are touching
one of the two trees and have not seen its file, open it — everything below assumes
you have.

---

## 1. Never invent a number the farm did not measure

ADR-0007 is written about drivers and governs the whole system. It is the rule most
often broken here, and each of these has already been got wrong once:

- A null reading is "not measured", not `0`.
- An hour with no telemetry is not an idle hour.
- An unknown id must 404, not answer an empty grid — an all-null response reads as
  "this thing did nothing".
- A denominator must be what was **observed**, never the roster. Otherwise the worse
  the coverage, the healthier the farm looks, and the error is silent and flattering.

Money is the same rule in different clothes: `VIEW_FINANCIALS` is kept separate from
every production permission, so a response carrying seconds must not quietly start
carrying rubles or kilowatt-hours.

## 2. A feature is done when a test proves it

D13, and it holds for changes that look obvious and for anything an agent produced.
The corollary is that the *irreversible* path is the one that most needs a test —
the retention clamp passed all six gates while untested, because gates check shape
and a test checks behaviour.

## 3. How code is written here

Read a neighbouring file before writing a new one. This codebase is unusually
prose-heavy **on purpose**: comments explain *why* a decision was made, what the
alternative was, and what it cost. A comment restating the line above it is noise;
one naming the failure the line prevents is the point.

Put the explanation where the reader will be standing when they need it — not in a
commit message they will not be reading.

## 4. Two traps that cost time on both sides

**Piping hides failure.** `cmd | tail` returns *tail's* exit code. `npm ci | tail`
once reported success while the install had failed, and every check after it ran
against a stale `node_modules`. Always:

```bash
cmd > /tmp/out.log 2>&1; echo "exit=$?"
```

**The docs go stale.** Three design-kit documents described built features as
missing for long enough that they were merged into one and the rest deleted; a
database review listed four completed tasks as outstanding. Verify a status against
the code before repeating it from any document — including this one.

## 5. Prefer removing the cause to silencing the symptom

`pydantic-core` was pinned in `constraints.txt` when `pydantic` already fixes it
exactly, so Dependabot could raise one without the other into a state no resolver
could satisfy. The fix was deleting the redundant pin, not adding an ignore. Reach
for the same order generally: an ignore, a `noqa` or a skipped test hides a thing
that will come back.

## 6. Verify, then say so

Report what happened, not what should have happened. If a check did not run, say it
did not run. If tests fail, quote the output. "Should work" is not a result — and an
agent's report of its own success is not evidence, so reproduce it.
