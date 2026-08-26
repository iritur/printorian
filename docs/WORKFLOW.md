# How work is tracked

The backlog lives in [GitHub issues](https://github.com/iritur/printorian/issues).
Before this document there were four places that described outstanding work —
`HANDOFF.md` §3, `docs/ROADMAP.md`, `docs/DESIGN-KIT.md` §2 and
`docs/DATABASE-REVIEW.md` §10 — and they disagreed with each other and with the
code. That is not a filing problem; it is the specific failure
[CLAUDE.md](../CLAUDE.md) §4 warns about, and it has cost this repository three
documents and a review section already.

So there is now one rule about where status lives, and the rest of this document
is consequences of it.

> **An issue is the only place open work is tracked. A document says what the
> system *is*; the tracker says what is *missing*.**

The prose documents keep their jobs and lose one. `ROADMAP.md` stays the plan and
the argument for the sequencing. `DESIGN-KIT.md` stays the specification of the
screens — for the five that are unbuilt, the kit *is* the spec, and an issue links
to it rather than restating it. `DATABASE-REVIEW.md` stays the analysis of the
schema. `HANDOFF.md` stays the narrative of what landed and why it matters. None
of them should carry a list of what is still to do; where they do, that list has
been moved into issues and the section now points here.

---

## 1. Labels

Four axes, and an issue normally carries one of each of the first three.

**Type** — what kind of work it is.

| Label | Means |
|---|---|
| `type:bug` | The system does something other than what it says it does |
| `type:task` | Work to build: a feature, an infrastructure slice, tech debt to repay |
| `type:docs` | A document is wrong, missing, or about to become wrong |
| `type:security` | Auth, secrets, payments, uploads, the exposed edge |

**Area** — where it lands, so a session can filter to what it can actually touch.

`area:backend` · `area:frontend` · `area:database` · `area:infra` · `area:ci` ·
`area:drivers` · `area:docs` · `area:design-kit`

**Priority** — how long it can wait, and why.

| Label | Means |
|---|---|
| `P0` | An irreversible path is unguarded, or the farm is losing data now |
| `P1` | Blocks other work, or a stated guarantee is not holding |
| `P2` | Real, scheduled, not urgent |
| `P3` | Housekeeping |

Priority is about consequence, not effort. The retention clamp is one function and
would have been `P0`; three merged branches are a five-second delete and are `P3`.

**State** — the modifiers that change how an issue should be read.

| Label | Means |
|---|---|
| `blocked:hardware` | Cannot progress without a physical machine — a printer, a VPS, a second disk |
| `blocked:external` | Waiting on an upstream release or a third party |
| `needs-person` | A decision or a credential an agent must not make or hold |
| `deferred` | Deliberately not built; the issue carries the trigger that reopens it |
| `drift` | A document describes the system as it no longer is |

`deferred` issues **stay open**. That is the point of them: an open issue with a
written trigger is a tripwire, and closing it as `wontfix` throws the trigger away.
They are excluded from milestones so they do not read as scheduled work.

---

## 2. Milestones

Milestones follow the vocabulary the repository already uses, so that an issue and
the document that argues for it are filed under the same name.

| Milestone | Source |
|---|---|
| `Governance` | Repository and process hygiene — the half of INFRASTRUCTURE Stage 0 that was never finished |
| `Documentation accuracy` | Every `drift` issue that has a correction to make |
| `Phase 4 — the first real print` | ROADMAP Phase 4's remaining exit criterion |
| `Infra Stage 2 — farm host as code` | INFRASTRUCTURE §7 Stage 2 |
| `Infra Stage 3 — the edge` | INFRASTRUCTURE §7 Stage 3 |
| `Infra Stage 4 — continuous deployment` | INFRASTRUCTURE §7 Stage 4 |
| `Infra Stage 5 — observability` | INFRASTRUCTURE §7 Stage 5 |
| `Infra Stage 6 — supply-chain autonomy` | INFRASTRUCTURE §7 Stage 6 |
| `Infra Stage 7 — proving it` | INFRASTRUCTURE §7 Stage 7, which closes ROADMAP Phase 7 |
| `Design kit — the remaining screens` | DESIGN-KIT §2, in the build order of §5 |
| `Backend capability with no consumer` | DESIGN-KIT §4 — things persisted or served that nothing reads |
| `Tech debt` | Latent defect classes and accepted trade-offs that have come due |

A milestone with no due date is a grouping, not a commitment. That is deliberate:
INFRASTRUCTURE's own estimates are for one person working part-time, and a date
attached to that is a fiction with a calendar icon.

---

## 3. What closes an issue

The same rule as everywhere else here — [CLAUDE.md](../CLAUDE.md) §2. **A feature
is done when a test proves it.** So:

- A `type:bug` closes when a test fails without the fix and passes with it. The
  issue names that test. "Could not reproduce" closes it as `invalid` with what
  was tried, not silently.
- A `type:task` closes when its own "Done when" is demonstrated. If that criterion
  turned out to be the wrong one, amend it in the issue *before* closing, so the
  record says what was actually delivered.
- A `type:docs` issue closes when the correction is merged **and** the same change
  says how the claim was verified. A status corrected from another document rather
  than from the code has only moved the drift.
- A `deferred` issue closes only when its trigger fires and the work is done, or
  when the trigger becomes impossible. Not when it gets old.

Reference the issue from the pull request (`Closes #12`), not the other way round,
so the merge does the closing and there is no second step to forget.

**Irreversible paths are triaged first.** Retention drops, WAL archiving, backup,
migrations, refunds and provisioning: these are where a defect cannot be walked
back, and where this repository has already been bitten by a change that passed all
six gates untested. The bug template asks about this explicitly.

---

## 4. Filing something new

Use a template — blank issues are turned off, because the fields are the parts
people leave out. Then, in order:

1. **Check it against the code, not against a document.** Half of the first
   backlog written for this tracker was work the documents said was outstanding
   and the code showed was finished.
2. **Search first.** The five unbuilt screens, the timestamp-sort flake class and
   the off-site backup gap each already have an issue, and each is the kind of
   thing that gets filed twice.
3. **Say where the expectation comes from.** An ADR, a doc section, a screen in
   the kit, a scenario line. An issue whose expectation is only the filer's
   assumption is a design question, and should be asked as one.

---

## 5. What this changes for an agent session

`HANDOFF.md` is still the first thing to read, and still has to be updated before
finishing — it carries the things a tracker is bad at: what a change *cost*, what
was tried and rejected, and which invariant not to break.

What moves is the "what is next" half. A session picking up work should read the
open issues, take one, and put its result in that issue. Two rules follow from the
repository's history and are not negotiable:

- **Do not close an issue on your own report of success.** Reproduce it, and paste
  what the reproduction printed. Two separate reports of "all gates green" here
  were produced by runs where the gate had not been executed at all.
- **If you find that an issue describes something already built, do not just close
  it.** File the `drift` issue for whichever document said it was missing. The
  stale document is the defect; the issue was only a symptom of it.
