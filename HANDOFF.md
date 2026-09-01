# Handoff

Where the work stands, what is deliberately unfinished, and what needs a person.
Standing rules are in [CLAUDE.md](CLAUDE.md); this file is the part that changes.

**Update this before finishing a session.** A stale handoff is worse than none —
it is read as current, and this repository has already been bitten twice by
status documents that described built features as missing.

**As of:** 2026-09-01 · **1 381 passed, 8 skipped, `exit=0` in 993.04s
(0:16:33)** on `feat/58-reprice-cache-hit`, alongside the six backend gates each
run separately and each `exit=0`. Those are pytest's own trailing summary line,
read out of the redirect rather than counted off the progress characters — a
previous session's run lost that line and said so, and `-p no:cacheprovider
--tb=short` is what brought it back. Eighteen of those tests are the fourth
review's, and ten mutations were applied, run and reverted against them.

**That suite figure was measured against this branch merged with `main` at
[#89](https://github.com/iritur/printorian/pull/89), and `main` has since moved.**
[#90](https://github.com/iritur/printorian/pull/90) landed while the pull request
sat open and is merged in below; it changes `production/planning.py`,
`production/models.py` and `tests/unit/test_queue_position.py`, so the suite it
composes is not the suite that produced 1 381. The six gates *were* re-run after
that merge and are current. The suite was not, and CI on the pull request is its
evidence. The number is kept with its referent named rather than quietly
re-pointed at a tree it never saw — that substitution is the error the paragraph
three below exists to record, and it is no more permissible when the newer tree
is likely to agree.

The frontend was neither built nor tested: this worktree has no `node_modules`,
so that half of the ten gates is CI's evidence and not this file's.

**The frontend count is deliberately absent rather than carried forward.** The
line arrived on `main` saying 249 with the #37 entry directly beneath it saying
**276 across 28 files** — a paragraph disagreeing with the paragraph under it,
which is exactly the drift CLAUDE.md §4 warns about, and it is not repaired by
copying either number up. Neither describes this tree anyway: 249 predates #88,
and 276 predates the `SettingsPage` tests #89 adds underneath this branch. This
worktree has no `node_modules`, so the figure was not measured and is therefore
not claimed — and nothing under `frontend/` changed on this branch, so there was
nothing here to measure it for. CI is the frontend evidence.

What ran locally on the merged tree, after `main` was brought up to #90: the six
backend gates, each separately and each `exit=0` — `ruff check`, `ruff format
--check` (**386** files), `mypy --strict` (**223** source files), `lint-imports`
(6 contracts kept, 0 broken), `check_context_isolation.py` and
`check_file_length.py`, run from this worktree's own `.venv`. The figures moved
this session — 369 and 217 before — because the fourth review of #92 added
`core/geometry.py`, `workers/plate_admission.py` and three test files; #90 added
no file, so it moved neither count. They are quoted because a gate whose file
count nobody reads is a gate that can quietly stop covering a tree.

**The full-suite figure this line used to carry was never about this branch.**
1 333 passed / 7 skipped, `exit=0`, 24m43s was measured on
[#87](https://github.com/iritur/printorian/pull/87)'s branch, said "on this
branch", and travelled onto `main` and then onto this one unchanged — which is
the pronoun quietly changing referent under a number, and is why "the suite
passed" and "the suite passed on *this* tree" are the two claims this file has
already been corrected for confusing once. It describes none of the trees since:
not #89's, which adds `tests/api/test_settings_api.py`, not its merge with #88,
and not this one. **No full-suite pass is claimed here.** CI on the pull request
is that evidence.

> **Four local attempts, no pass, and the diagnosis was wrong once.** Recorded in
> full because the wrong diagnosis is the useful part.
>
> The first run failed ~200 tests across `tests/api` files this branch does not
> touch, and each of those files passed on its own seconds later. That was written
> down here as the concurrent-session trap `backend/CLAUDE.md` names — plausible,
> three other worktrees are live on this machine, and **not measured**. The second
> reached 19% clean and was killed by a session restart. The third was killed by a
> merge committed into the working tree while it ran, which left `planning.py`
> holding conflict markers; that one is nobody's fault but the author's.
>
> The fourth errored from 5% onward, and the cause turned out to be that
> **PostgreSQL was gone**: nothing listening on 5433, `docker` unable to reach its
> daemon, a direct `asyncpg.connect` refused. Docker Desktop had stopped. Which
> makes a restarting container the better-supported explanation for run one as
> well — the DB dying mid-run and coming back fits both the mass failures *and*
> the single file passing immediately after, and no evidence was ever gathered for
> the concurrency story that was written here first.
>
> The lesson worth keeping is not about Docker. A run whose failures land
> everywhere *except* the change has an environmental cause, and there is more
> than one candidate; `pg_stat_activity` or a bare `connect` settles it in one
> command, and guessing costs a paragraph of confident prose that has to be
> retracted. Check that the database is up **before** reading a failure list.

**A wait-list row now ends when the wait does, and not one pass later.**
`planning._refresh_wait_list` discarded rows only for the jobs in
`result.wait_list` — the ones *still* waiting. A job wait-listed on one pass and
assigned on the next fell out of that set and kept its `wait_list_entries` row
for ever: nothing else deletes one except the owner's farm-wide clear and the
cascade from the job or its order. The discard now covers the whole claimed batch
(`by_id.values()`), which is the honest set, because the planner decides every
job it looks at — assigned or wait-listed, never neither.

> **It was three wrong answers, not one.** `queue.queue_position` read the stale
> row and told a customer whose job was `assigned` why it had been stuck three
> passes earlier. Worse, a position is counted by comparing `predicted_start`
> across the *whole table*, so the phantom sat one place in front of every other
> customer and inflated their numbers too. And `reads.wait_list` and
> `schedule.wait_list_size` count rows straight out of the same table, so the
> floor's list and the dashboard chip carried it as well. Neither of those two
> needed a change of their own — they were reading a table that was lying.
>
> **Both are held to tests that fail without the fix**, in
> `tests/unit/test_queue_position.py`. `test_a_job_that_gets_assigned_leaves_the_wait_list`
> runs two passes — busy machine, then free — and asserts no row, no `reason`, and
> zero from `wait_list_size`; it failed on the row assertion before the change.
> `test_a_job_that_stopped_waiting_stops_counting_against_others` puts a second
> customer behind the first and asserts their position falls from 2 to 1; it
> failed with `assert 2 == 1`. The second test is the one worth keeping: a
> per-job assertion cannot see a defect whose damage lands on somebody else's row.
>
> **It was written against [#31](https://github.com/iritur/printorian/issues/31)'s
> branch rather than `main`**, because the fix goes through `wait_list.discard`
> and that branch introduced it; writing a second delete here to dodge the
> dependency would have re-created exactly the divergence #31 exists to prevent.
> #31 has since merged as #89 and `main` is merged in here, so the dependency is
> now history rather than a merge order to remember.
>
> **`WaitListEntry`'s own docstring stated the invariant that was false** — "re-
> planning replaces the row rather than appending" describes half a rule. It now
> says the other half, next to the column definitions, because that is where the
> next person adding a reader of this table will be standing.

**A paid order whose configuration is already sliced now reaches `QUEUED` by
itself** ([#58](https://github.com/iritur/printorian/issues/58)). The intake sweep
attaches the cached `PreparedPlate` and applies ADR-0013's band with nobody in the
loop, so ROADMAP Phase 4's exit criterion — payment to a machine starting the job
with no human action — holds for the cached half instead of ending in an engineer
clicking «attach» on work the farm already has. **Read the reachability paragraph
below before repeating that sentence anywhere else:** the mechanism is built,
tested and correct, and nothing the storefront can place reaches it yet, because
`OrderingService.place` never writes `OrderLine.model_asset_id`.

> **The whole issue was one `NOT NULL` column.** `EstimateVariance.prepared_cost`
> had no source: pricing happens once, at quote time, and nothing in the system
> prices a plate. #41 stopped here rather than pass a zero. `pricing/reprice.py`
> is that source, and it is deliberately a **difference** rather than a fresh
> total — `line_total + (price with the plate's minutes and grams − price with the
> numbers that were quoted)`, both under the order's own pinned `RateSnapshot`.
> Two inputs of the original quote cannot be recovered from the order and each
> would land on the money column: the **customer tier**, resolved from spend at
> checkout and held by no column, and the **per-line quote**, which does not exist
> at all, because `place` prices the order and apportions the total by quantity. A
> difference cancels both. Do not "simplify" it into `price(spec).total`.
>
> **The tier half of that argument was overstated and has been corrected.** This
> file, `reprice.py` and ADR-0013 all used to say that rebuilding the tier from
> `price_breakdown` "is a second loyalty ladder". It is not: the *applied*
> percents land on `Basis.percent` — `tier.discount_percent` on the discount line,
> the effective margin on the margin line — and `breakdown_from_dict` reads them
> back. The per-line quote is the input that really is unrecoverable, and it is
> the whole argument on its own. The difference stays; the sentence defending it
> is now the true one. Its cost is recorded too: a tier whose
> `margin_percent_override` is *above* the snapshot margin makes the difference
> **understate** an overrun, so one that should have gone to `PRICE_REVIEW` can
> land inside the band — 15.48% true against 13.88% recorded on the farm's own
> defaults. `tests/unit/test_reprice_tier.py` pins the sign and that straddle.
>
> **How many copies are on the plate is now a column, and the guard that stood in
> for it was one-sided.** Nothing on a `PreparedPlate` recorded it —
> `layout_hash` is opaque — and two things depend on it: `attach_plate` takes the
> plate's minutes and grams as the job's *whole* work, and the reprice divides
> those same totals by the line's quantity. A one-up plate on a line of three
> therefore under-prints and underprices at once, comfortably **inside** the band.
>
> The first answer was to refuse any line whose quantity was not one. That covered
> one direction and left the other wide open, and the other is the *normal* one: a
> `PrintJob` is one plate holding a whole line's work, so the first order for two
> keychains leaves a **two-up plate** in the library, and the repeat order for one
> attached it, repriced against the whole bed, came out 4.26% over — inside the
> band — printed two, shipped one, and recorded an accurate estimate. Reviewed and
> reproduced end to end against real PostgreSQL before it was fixed.
>
> `PreparedPlate.copies` (migration `0023`) is the answer instead: **nullable,
> never defaulted, never backfilled**, because a `1` written in for the plates
> already in the table is an invented number that happens to be the one that makes
> the common case attach. The unattended path attaches only when the plate's
> recorded copies equal the line's quantity, and the `quantity != 1` refusal is
> gone. The tripwires are `test_a_two_up_plate_is_not_attached_to_a_line_of_one`,
> `test_a_one_up_plate_is_not_attached_to_a_line_of_three` and
> `test_a_plate_that_does_not_say_how_many_copies_it_holds_is_refused`, all in
> `tests/unit/test_intake_plate_selection.py`.
>
> **A line of three still does not queue itself, and the reason is the fourth
> review's.** The count now agrees; the bed's *footprint* is recorded nowhere, and
> the only geometry the planner ever sees is the job's — one part's box. So a
> three-up bed was being judged against the size of one part, and a machine too
> small for the plate was eligible for it. `plate_admission` refuses any plate
> holding more than one copy; what lifts it is recording the plate's own bed
> extent, two columns and a console field, exactly the shape `copies` took. The
> per-quantity arithmetic `copies` earned is still pinned, by
> `tests/unit/test_reprice_quantity.py`, which is pure pricing and needed no sweep.
>
> **What that costs today, and it needs a person.** The console's plate upload
> does not send `copies` — the field is a query parameter on
> `POST /jobs/{id}/plate/file` and a body field on `POST /jobs/{id}/plate`, and
> nothing in `frontend/` fills either. Until it does, plates recorded through the
> console carry NULL and the unattended path declines them, which is last week's
> behaviour rather than a regression. It was **not** parsed out of the 3MF:
> `plate_file.py` says in its own docstring that the sliced `<plate>` element is
> implemented from documentation and has never been seen from this farm's slicer,
> and a miscount there would not fail loudly — it would attach the wrong plate
> quietly. Asking is the conservative half of that trade, and the frontend field
> is the follow-up.
>
> **Three reviews found three unguarded dimensions one at a time, so the set is
> now written down in one place.** `backend/printorian/workers/plate_admission.py`
> is that place: one pure predicate, one refusal code per dimension, and a
> docstring that says what must match, what is checked elsewhere, what is
> deliberately absent (finishes cancel on both sides of the difference), and what
> is still open with the cost of closing each. The cause it names is worth
> repeating here: `plate_key` answers *"have we sliced this before"* and every
> term in it — geometry, scale, priced material, a profile string, an opaque
> layout — describes the **order**. Nothing in the schema describes the **bed**,
> and every hole the reviews found was a bed-side property. `copies` was the first
> bed-side column ever added.
>
> Four refusals were added there and each was mutation-proved: a plate whose
> **filament count** is not the line's distinct colour count (round three's
> blocking finding — colour is in no key term, and `line.material_code` is the
> *dearest* chosen spec rather than the filament set); a plate holding **more than
> one copy**, because the bed's footprint is unrecorded; a line whose **part was
> never measured**, because a zero box reads as "fits every machine"; and a plate
> that is **numbers with no file behind it**, which would otherwise reach `QUEUED`
> and then be re-queued by dispatch for ever without ever returning to `PREP`.
> `tests/unit/test_intake_bed_admission.py` is the file, and deleting any one
> clause makes exactly its own test queue an order it should not.
>
> **A moved engine version now refuses too** (`_rates_for`, ADR-0020's amendment).
> `prepared_cost` is `line_total` — priced by the engine of the day — plus a
> difference priced by today's, so a changed formula makes that sum a hybrid
> nobody quoted, recorded as measured. The third review offered "refuse or
> document"; refusing costs a click.
>
> **The part's bounding box was being read at 1× everywhere.** `OrderLine.mesh`
> holds the *unscaled* box — `_pricing_spec` writes `analysis.bounding_box`
> verbatim while `estimate()` scales only volume, mass and time — and both readers
> of it had forgotten the multiplication. A 40 mm mesh at scale 3 was handed to the
> planner as 40 mm, whose only geometric test is `job.width_mm > printer.width_mm`,
> and to `workers/packaging` as the carton size. `core.geometry.scaled_box` is the
> one multiplication now, and `tests/unit/test_scaled_box.py` holds both readers to
> it.
>
> **Two cheap ones from the third review, both now held by a test.**
> `PlateLibrary.record` overwrites `copies` rather than merging, and the comment
> saying that is load-bearing was true and untested — the merge passed thirty-nine
> tests. And `POST /jobs/{id}/plate/file?copies=0` raised a bare pydantic error
> *after* `storage.put`: a 500 with no code (ADR-0012) and an orphaned blob. The
> query parameter carries `Query(ge=1)` now, which is the bound the model always
> had.
>
> **The path is not reachable from the product at all, and that is the finding to
> carry forward.** `OrderingService.place` is the only constructor of an
> `OrderLine` and it never writes `model_asset_id` — the field is accepted by
> `POST /orders` and dropped — so `model_hash` is `""` and the unattended path
> declines at its first line on every order the storefront can place. The web
> checkout sends no `mesh` either. So the guards on this branch have never been
> exercised by a real order, the whole feature lives in the test suite, and the
> two small changes that would switch it on are exactly the changes that would
> make every open dimension in `plate_admission`'s docstring live at once. That is
> a decision for a person, and it is why the pull request now says `Refs #58`
> rather than `Closes #58`.
>
> **A payload that will not rebuild must cost one order, not the pass.**
> `IntakeSweep.sweep` rescues `PrintorianError` per order and nothing else, and
> `Decimal("no longer a number")` raises `InvalidOperation`, which is not one — so
> a single unreadable `RateSnapshotRecord` escaped the whole pass and left every
> *other* paid order in the batch jobless. It is caught where it happens now, and
> the test asserts on the healthy order standing beside the broken one.
>
> **The snapshot-id check is what keeps ADR-0020 true.** `rates_from_dict` fills a
> field a stored row lacks with today's default, so a rebuilt snapshot whose hash
> no longer matches its row was completed from numbers that were never in force,
> and is refused. Without it, adding one rate to `RateSnapshot` would silently
> re-rate every older order this path touches, and nothing would say so.
>
> **`PAID → PRICE_REVIEW` is a legal transition now**, because a cache hit can
> exceed the band before any engineer has touched the order. The alternative was
> recording a `PREP` the order never entered.
>
> **`intake.py` hit the 400-line gate and split into two files.**
> `intake.py` still answers "which paid orders have no jobs, and what jobs do they
> need" — #41's question, and the one that runs whether or not the farm has a
> plate library. `intake_routing.py` answers the one #58 added: the jobs exist,
> may the farm start them itself? That is the seam, not the line the counter
> tripped on, and it is the same cut `runner.py` took. The second review's tests
> pushed `test_intake_cache_refusals.py` over the same gate, and it took the same
> kind of cut: `test_intake_plate_selection.py` is "which plate, if any", and what
> stayed is "the plate is right and the money cannot be worked out honestly".
>
> **`CachedPlates` is an optional collaborator of `IntakeSweep`, wired in
> `workers/passes.py`.** Withheld, every order goes to prep exactly as before — so
> deleting that one line returns the farm to clicking, silently, which is why one
> test pins that the fallback is the safe one and another pins that the
> collaborator is actually supplied.
>
> **`DATABASE-REVIEW.md` §1 now says twenty-five migrations**, because
> `0022_plate_model_hash_index` and then `0023_prepared_plate_copies` moved it and
> `test_the_migration_count_matches_the_versions_directory` failed the whole suite
> until it did. That test is the reason a doc figure moved on the same commit as
> the schema instead of six weeks later.
>
> **Measured, not asserted, and every round was measured.** All six backend gates
> run separately on the final tree, each `exit=0`; the whole suite **1 363 passed
> / 8 skipped, `exit=0`, in 1052.37s (0:17:32)** — pytest's own summary line, which
> reached the redirect this time. Every claim above was mutation-proved, each
> mutation applied, run and reverted.
>
> Re-measured on this tree: `prepared_cost=Decimal(0)` fails five of the seven
> cache-hit tests and `prepared_cost=line.line_total` fails those five and the
> journal one; a fresh `RateSnapshot()` in place of the pinned one fails the
> ADR-0020 test alone. Measured earlier and not re-run: dropping the snapshot-id
> check, the `PRICE_REVIEW` branch, the "never picks between two plates" rule and
> the rebuild guard each failed exactly the test named for it, and forcing the
> routing back to `PREP` failed six.
>
> This round's own six: dropping the `copies` guard fails three selection tests;
> defaulting `copies` to one fails the plate-that-does-not-say test; dropping
> `status == VALID` fails the retired-plate test; replacing the plate_key
> comparison with `list(rows)` fails the wrong-material test; removing `cached`
> from `passes.py` fails the wiring test; and setting the tier's margin override
> equal to the snapshot margin fails two of the three tier tests. Both migrations
> were upgraded, `alembic check`ed, downgraded and re-upgraded against the **dev**
> database; `alembic current` said `0022_plate_model_hash_index` beforehand this
> round.
>
> **The reprice also had to be told where it may not write the number.**
> `attach_plate` put `quoted_cost` and `prepared_cost` into the job's *journal*
> as well as onto the `EstimateVariance` row, and a `JobEvent`'s `details` rides
> out on `JobView.events` — which `GET /jobs/{job_id}` serves under
> `VIEW_PRODUCTION` alone, the permission an operator holds. `GET /jobs/variances`
> gates the identical pair on `VIEW_FINANCIALS`, so the journal was a second,
> ungated copy: CLAUDE.md §1's money split, defeated by the other door. It read
> `"0"` for as long as the console's plate upload was the only caller, because
> those two costs are query parameters the console does not send — so the leak
> existed and was empty, and this branch is what fills it. The pair is out of
> `details` now; `overrun_ratio` stays, because a ratio is not money and it is
> what the floor reads an `ON_HOLD` against.
> `test_the_job_journal_does_not_carry_money_to_the_floor` reads the job as an
> **operator** and `test_the_sweeps_journal_entry_carries_no_money` guards the
> write; putting the two costs back fails both, which was run.
>
> **The wiring is covered now, and it was the one hole between "the code exists"
> and "the farm uses it".** `workers/passes.py` supplies `CachedPlates` to the
> sweep, and deleting that argument returned the farm to clicking with every test
> still green — the two cache-hit files build the sweep themselves, and the one
> test that goes near it asserts the *fallback* is safe, which is the opposite
> claim. `tests/unit/test_intake_pass_wiring.py` drives `IntakePass` itself against
> a stand-in runtime (the real one opens three Redis connections), and removing
> `cached` from `passes.py` now fails it. It is still the only worker pass in this
> tree with a wiring test.

**The third irreversible operation exists, and it shares its delete with the
planner** ([#31](https://github.com/iritur/printorian/issues/31)).
`POST /settings/clear-wait-list` empties the wait list, audited per row into each
job's own journal. Every removal of a `wait_list_entries` row now goes through
`contexts/production/wait_list.py`: the planner's per-job replace calls
`discard`, the owner's «Очистить» calls `clear_wait_list`, and the arrangement is
the one `drop-telemetry` already has with the maintenance worker — one function,
so «сделать сейчас» and the scheduled path cannot come to mean different things.

> **The kit's hint describes a transition the state machine refuses, and it was
> not implemented.** `design/settings.html` says clearing returns waiting orders
> to «Подготовка». `production.policies.TRANSITIONS` gives `READY` only
> `ASSIGNED` and `CANCELLED`, and the note beside `ON_HOLD` says why nothing goes
> back to `PENDING` — the plate exists, and re-slicing it would not be the fix. So
> the operation removes the *record of the wait* and leaves the job where it was,
> the hint on the button says that instead, and
> `test_clearing_the_wait_list_does_not_move_the_job` is what fails if somebody
> later implements the kit literally. Which of the two is wrong is a question for
> a person; the code does not guess.
>
> **What is irreversible here is the reasons, not the queue.** A still-blocked
> job is written back onto the list by the next planning pass seconds later. Why
> it was stuck, what was blocking it and when it was predicted to start are held
> nowhere else, so `clear_wait_list` copies each row into the job journal before
> dropping it — an audit reading only "the list was cleared" would answer none of
> the questions the list was answering. `by` travels in `JobEvent.details`
> because that table has no actor column, unlike `OrderEvent`; a migration on the
> busiest history table for one caller was the alternative and was not taken.
>
> **Three mutations were applied, run and reverted.** Deleting the journal write
> left «cleared: 1» and a green endpoint — the audit test failed. Reporting the
> rows without deleting them left the body reading `{"cleared": 1}` with the row
> still in the table, which is precisely the 200-proves-nothing shape CLAUDE.md §2
> warns about, and only the read-back assertion noticed. Reopening the blank-farm-
> name hole in `ConfirmAction` failed the wait-list confirm test as well as the
> reset-rates one, which is the evidence that the third operation went through the
> shared gate rather than growing its own.

**The three design-kit conventions that were never ported now exist once each,
and the row that said so had been wrong about one of them**
([#37](https://github.com/iritur/printorian/issues/37)). `FilterChips` is the
counter-chip row, and there had been three of them — the console's table tags, the
account's four order groups, the journal's sections — each with its own copy of the
markup and its own answer to whether pressing the chip already in force clears it.
Two said yes and one said no, which is the same control behaving differently
depending on where you met it. The rule is settled in the component now, and the
count is `number | null`: the journal's row had been printing «—» for the total it
had not received and `0` for every section beside it, in the same line of pixels.

> **`Tabs` is built with no caller, deliberately.** The strip is what the purchase
> order, the shipment and every detail popup after them are drawn with in the kit,
> and the argument for porting it before those screens exist is that otherwise each
> of the four invents its own — the failure ROADMAP names under "Management tables
> are not a phase". It shares `shell/tablist.ts` with the existing `TabRail` so a
> rail and a strip cannot drift into two controls: one stop in the page's tab order
> for the whole list, arrow keys on the axis it is drawn on, and selection following
> focus. Fourteen settings sections used to be fourteen Tab presses.
>
> **`AuthDialog` got its opener from the masthead, which had none.** `AppShell`'s
> right-hand group rendered only for a signed-in actor, so a signed-out visitor's
> only way in was to leave for a screen that happened to render an `AuthPanel` —
> and reaching one from the catalogue costs the catalogue, which is the exact cost
> `design/js/auth.js` was written to avoid. It is the same `AuthPanel` in the kit's
> popup chrome, not a second sign-in form, and it stands itself down once there is
> a session: the control it was opened from is gone by then.
>
> **That opener put two copies of one form in one document, and a review caught
> it.** The three screens «Войти» is most likely to be pressed on — the checkout,
> the cabinet, the account — each already render an inline `AuthPanel` for exactly
> the signed-out state the masthead offers it in, so the popup mounted a second
> panel over the first. `AuthPanel` wrote its field ids as literals, from when only
> one of it could exist, and `<label for>` resolves to the **first** match in tree
> order: the popup's «Электронная почта» drove the input on the page underneath,
> which the overlay covers and `aria-modal` declares inert. Focus landed outside
> the dialog, where `Modal`'s Tab trap cannot reach it — that trap only intervenes
> on the first and last focusable *inside* the dialog — so Tab then walked the page
> behind the popup, and autofill and screen readers read the same wrong pairing.
> The ids come from `useId()` now. The other hardcoded `htmlFor` ids in the tree
> (`co-city`, `cfg-material`, `j-title`) are left alone deliberately: each belongs
> to a form only one of which can be on screen, and this one stopped being that the
> day the shell grew a way in.
>
> **DESIGN-KIT §3 said "CSS section 22 not ported" and section 22 had been ported.**
> `packages/ui/src/harvester/tabs.css` is it, near enough verbatim, and `TabRail`
> and `TabView` had been built, exported and in use on two screens. The row had been
> carried forward from a plan rather than read off the file — the mistake that
> document's own opening warns about, made in that document. What was genuinely
> missing was the horizontal strip and the keyboard behaviour. `data-bind` is now
> written down as **deliberately not ported** with the reason, so the next reader
> does not build it out of completeness.
>
> **The kit's attributes are not all worth keeping.** `data-tab-panel` survives
> because the ported CSS selects on it; `data-tabs` and `data-tab-target` were how
> the static kit's JavaScript found a button's panel, and React state is that
> wiring. Emitting them anyway would have been a hook with no handler.
>
> **Measured on this branch, not asserted.** All four frontend gates run separately:
> `typecheck` `exit=0`, `lint` `exit=0`, `test` `exit=0` with **276 passed** across
> 28 files (249 before this branch), `build` `exit=0`. Each new behaviour was
> mutation-proved — the em dash, the clearing rule, the arrow keys, the dialog
> standing down, the masthead's way in and the two panels not colliding were each
> broken deliberately, the test watched to fail, and the change restored. The bundles were then read rather than
> grepped: `hv-tabs__btn` appears **zero** times in either `apps/*/dist/assets/*.js`,
> which is what "no caller yet" means, and `hv-tags` appears exactly once in each,
> which is what "one chip row" means.

**The delete rules are held to a test, and one way this suite can lie is now
written down** ([#47](https://github.com/iritur/printorian/issues/47)).
`backend/tests/test_referential_integrity.py` is the inventory — all forty-eight
foreign keys grouped by rule, read back out of `pg_constraint` rather than off the
models — and `backend/tests/unit/test_delete_rules.py` exercises one representative
of each rule against real rows.

> **`SET session_replication_role = replica` is the finding worth carrying
> forward.** A session that runs it leaves every constraint sitting in
> `pg_constraint` and enforces none of them. Applied to the `db_session` fixture as
> a mutation, the catalogue file passed every assertion and all six behaviour tests
> failed. That is why there are two files rather than one, and it is the shape to
> expect from any test that reads a catalogue and concludes something is being
> enforced: present and enforced are different facts. Measured, not reasoned about
> — the mutation was applied, run and reverted, along with three others.
>
> **Every delete in the behaviour file is issued as SQL rather than through
> `session.delete`.** `Order.lines` and `Order.events` carry `cascade="all,
> delete-orphan"`, so the ORM would do the deleting itself, in Python, and every
> assertion would pass against a database holding no constraints at all — precisely
> the state these tests exist to detect. Do not "simplify" them onto the ORM.
>
> **The issue's premise had already expired, and its last clause had not.** #47 was
> filed as "66 tests build against a fabricated parent id, so foreign keys are off
> in the fast suite". ADR-0021 moved the suite onto real PostgreSQL,
> `conftest.clean_database` emits every key through `create_all`, and
> `tests/factories.py` already gives those tests real parents — 29 calls across 18
> files. Measured before anything was written: 48 foreign keys in `printorian_test`,
> and a `PrintJob` inserted against an invented `order_id` refused with
> `IntegrityError`. What was *not* true is that a wrong cascade would fail anything.
>
> **`docs/DATABASE-REVIEW.md` §3 said "twenty-eight foreign keys" and named "the two
> references to `model_assets`".** There are forty-eight — 26 `CASCADE`, 15 `SET
> NULL`, 7 `RESTRICT` — and three `RESTRICT` references to `model_assets`, plus a
> fourth that is `SET NULL` on purpose (`prepared_plates`: a plate can be re-sliced,
> a job cannot). §3 is corrected and now points at the test as the enumeration
> instead of restating a list, and §10 no longer carries #47 as outstanding work.
> The count had been describing the schema of the initial commit through four
> further contexts.
>
> **The full backend suite was run locally for this branch** — not only in CI, and
> unlike the session the As-of line above describes: `1333 passed, 7 skipped in
> 1483.44s`, `exit=0`, against the compose PostgreSQL on 5433. The document-only
> follow-up that corrected §3 re-ran the six backend gates and the two new test
> files rather than the whole suite again.
**The farm can now be asked what it thinks of itself**
([#30](https://github.com/iritur/printorian/issues/30)). The settings screen grew
the kit's fourteenth section, «Диагностика», and it is the one section where
nothing is a setting: `/health/ready`, `/health/workers` and the driver roster,
drawn as `.hv-health` rows with the backend's own `ok` / `degraded` / `failed`
distinction kept intact. The signals have been real for a while — a beat is
recorded at the *end* of a pass, so a wedged loop is distinguishable from a
running one — and until now their only consumer was a person with `curl`. It is
**not** a substitute for Stage 5 monitoring and should not be argued as one: a
dashboard somebody has to open is not an alert.

> **`api.get` could not be used, and that is the whole shape of the file.** Both
> endpoints answer **503 with a full body** when something is wrong — readiness
> when a check has failed, workers whenever a loop is not beating — and
> `ApiClient` throws on any non-2xx, funnelling the body through `readErrorBody`,
> which keeps only `{code}`-shaped payloads and discards the rest. Going through
> it would have blanked the panel in precisely the state it exists to explain.
> `DiagnosticsPanel` therefore probes with a bare `fetch` and reads the body
> whatever the status code was. There is a test whose only job is to notice.
>
> **A fourth state exists, and it is the point.** `unknown` is not a backend
> verdict: it is what the panel says when it could not measure — the probe did
> not answer, the roster names a printer whose reading has lapsed, or the value
> is one this build has never heard of. The mapping is a **whitelist**, so a
> verdict added on the server tomorrow renders grey and unnamed rather than green
> and wrong. A `!== 'failed'` would have been the flattering error CLAUDE.md §1
> is about, and there is a mutation test for exactly that.
>
> **`degraded` says a different word, not only a different colour.** `paused`
> (amber) against `error` (red) is the visual half; the pill's text is the half
> that survives a reader who cannot separate the two. The distinction is
> load-bearing — `wal_archiving` degraded means every request is being served and
> the backup guarantee behind it has stopped holding.
>
> **Every denominator is what answered.** «1 из 2 проверок» is counted from the
> checks the probe returned, because `event_relay` is reported only where a relay
> is configured and a fixed total would have left a deployment without one
> permanently reading one check short. The same rule is why an empty driver
> roster gets its own sentence rather than a blank list: `core.driver_health` is
> explicit that empty means *nothing was published*, and a blank panel there
> would have said "this farm has no printers". **And the numerator over it is
> withheld when nothing answered.** A row whose verdict is `unknown` was not a
> reading, so it counts on neither side of the fraction: `Heartbeat.report()`
> returns all seven loops with `state="unknown"` when the store cannot be read,
> and a tile keyed on the length of that list drew «0 из 7 циклов» — seven loops
> reported stopped, on the evidence that nobody looked. It now reads an em dash
> over «НЕ ИЗМЕРЕНО», and a partly-measured group carries its shortfall
> («ИЗ 2 НАБЛЮДАЕМЫХ · 1 НЕ ИЗМЕРЕНО») so that all-fine, partly-known and
> measured-nothing stay three different things on the tile.
>
> **Two of the kit's four stat tiles were dropped rather than filled.** Nothing
> in the system measures uptime or the event-queue depth, and a tile reading
> `0 events queued` on a farm whose relay is down is an invented number with a
> nicer font. «Версии» and «Журнал» are absent for the same reason — no endpoint
> serves either.
>
> **The tab is inserted by the console, not served.** `SECTION_ORDER` carries
> fourteen sections where the kit's rail draws fifteen, because a read-only page
> has nothing for a settings *catalogue* to describe — that is still right, and
> it left the rail one entry short. The rail's own length is now what the
> «Разделы» count reports, and the section number in each heading is read from
> the rail, so «Обслуживание системы» is section 15 as the kit has it rather than
> 14. The tab is in the rail as soon as that load has *settled*, whatever it
> settled as: `GET /settings/sections` reads the database, so gating it on the
> catalogue hid «Диагностика» in exactly the outage it explains, and a failed
> catalogue now leaves the diagnostics tab standing alone rather than an empty
> rail under an error banner.
**DESIGN-KIT §4 is empty for the first time, and the materials popup is why**
([#38](https://github.com/iritur/printorian/issues/38)). `GET /materials/{code}`
was the last endpoint the API served that nothing called. The issue allowed either
answer — build the popup or delete the route — and the popup was built:
`apps/console/src/MaterialDetail.tsx` reads the spec by code when a row is opened,
so the window now shows density, tensile strength, heat-deflection temperature and
the flexible/outdoor flags. None of those five is a column of the materials table,
which is why the old popup — rendered from the row it was opened from — could say
where a spool is and never what the plastic is.

> **The purchase price is behind `VIEW_FINANCIALS`, and the route is not.**
> `GET /materials/{code}` carries `purchase_price_per_1000m` and has no permission
> dependency of its own, because the storefront configurator needs the catalogue.
> The console declines to draw the farm's buying price for anyone without the
> money permission (root CLAUDE.md §1), which is a decision about a screen and
> **not** an access control — anyone who can call the route still gets the field.
> The measured fact behind that sentence, because it is worse than "the console
> is being careful": `GET /materials` and `GET /materials/{code}` both return
> `MaterialSpecView` with `purchase_price_per_1000m` populated, neither carries a
> permission dependency, and nothing above them requires a session — so the
> farm's buying price is served to an anonymous caller. Whether the route should
> withhold it is a backend decision and it needs an issue, which is a person's to
> file: open work belongs in the tracker and not in this document
> (docs/WORKFLOW.md), so what is recorded here is the state, not the task. It was
> raised in the review of #85 and is waiting on that issue.
>
> **An empty §4 broke the gate that watches §4**, and the fix is worth knowing
> before the next entry is closed. `test_section_4_still_parses_into_the_entries_it_carries`
> asserted `len(parsed) == len(bullets) and parsed` — the trailing term was there so
> that a broken `DOC_ENTRY` regex could not make the two parametrized gates collect
> zero cases and report green. But it also required §4 to carry a bullet for ever,
> and §4 is *meant* to reach zero; emptying it failed the gate rather than the
> section. The parser is now proven against a `SAMPLE_BULLET` constant instead, and
> the spelled-count assertion flips to "an empty section must not still spell a
> count". Both directions were mutated and both fail. The two parametrized gates
> now collect nothing and pytest reports that as `6 passed, 2 skipped`; the
> module docstring says so in as many words, because an unexplained skip in the
> file whose whole subject is gates that stop running without anyone noticing is
> the last thing that file should leave to inference.
>
> **What measures that the issue is closed** is `MaterialsPage.test.tsx`: it opens
> a row on the table a person opens a material from and watches the request for
> `/materials/{code}` go out. The docs gate is the weaker half of that pair and
> the difference is worth knowing — deleting the path literal from
> `MaterialDetail.tsx` does make
> `test_every_unconsumed_endpoint_is_named_in_the_doc_or_exempted` fail naming
> `GET /materials/{code}` and nothing else, and that was run, but it is a scan of
> the source text: a detail window no screen mounted would keep it green.
> Reachability is a question about the bundle (`frontend/CLAUDE.md`), and the
> built console bundle does carry the detail read.

**The rates an order was priced at can now be looked at**
([#40](https://github.com/iritur/printorian/issues/40)).
`GET /orders/{order_id}/rate-snapshot` serves the pinned bundle, and the order
desk gained a «Тарифы заказа» panel. ADR-0020's guarantee has always *held* —
a rate edit changes the next quote and nothing already sold — and nothing could
show it: an owner changes seventeen pricing rates, a customer asks why a repeat
order costs more than last month's, and the system held both snapshots and could
display neither.

> **The payload is served as stored, and deliberately not rebuilt.**
> `pricing.rates_from_dict` skips fields absent from a stored row and
> `RateSnapshot` then supplies today's defaults for them — so a snapshot written
> before a rate existed would come back carrying a number that was never in force,
> indistinguishable from a measured one. The row goes out verbatim,
> `schema_version` included, so its vintage is legible.
>
> **The id is the answer, not the table.** It is a content hash, so two orders
> showing the same id were priced from identical rates and the difference between
> them is in the configuration. The panel shows it abbreviated for exactly that
> comparison.
>
> **An order that pinned nothing gets its own code.**
> `error.ordering.rates_not_recorded`, distinct from `not_found`: the order exists
> and its rates were never recorded. The panel renders that sentence and does not
> even make the request — a table of zeros would be a claim about rates nobody was
> charged.
>
> **The read lives in `ordering/snapshots.py`**, not on `OrderingService`, which is
> at the 400-line gate. It wants none of what the service carries — no clock, no
> bus, no transaction — so the split costs nothing.

**The variance queue ADR-0013 feeds is no longer invisible**
([#39](https://github.com/iritur/printorian/issues/39)). `GET /jobs/variances`
serves what slicing found against what was quoted, and the order desk gained a
«Пересмотр цены» panel that reads it. The detection has always worked — every
plate attach writes an `EstimateVariance` and one beyond tolerance holds the job
— and nothing served the rows, so the mechanism that stops a mis-estimated plate
printing at a losing price was half-built.

> **The route carries `VIEW_FINANCIALS` on top of the router's
> `VIEW_PRODUCTION`.** A variance is a measurement and this one carries money;
> CLAUDE.md §1 keeps the two apart precisely so a response about seconds does not
> quietly start carrying rubles. The engineer who *records* the plate may not read
> what it cost. The route is refused whole rather than answered with the money
> blanked — a null means "not measured" (ADR-0007), and using it for "not
> permitted" would make the two indistinguishable.
>
> **`exceeded_only` is opt-in, and that default is load-bearing.** The in-band
> rows are the farm absorbing small differences and are the dataset ROADMAP Phase
> 6 calibrates the estimator against. A read that dropped them would look like a
> filter and take that dataset with it.
>
> **The route is declared above `GET /jobs/{job_id}`.** FastAPI matches in
> declaration order, so the same route written below resolves as
> `job_id="variances"` and fails as a 422 about a UUID nobody asked for, with no
> warning. There is a test whose only job is to notice.
>
> **The desk had no `price_review` chip at all.** It was in none of the three
> filters — not `awaiting_payment`, not `in_production`, not `shipped` — so the
> one status that needs a person was reachable only by reading every row.

**The farm can now be asked which printers it is actually connected to**
([#21](https://github.com/iritur/printorian/issues/21)). `/health/workers` grew a
`drivers` key: one entry per printer the worker was asked to drive, with its
state, the error code behind an `unavailable`, and when that state began. The
worker publishes it (`core.driver_health`) down the same Redis-with-an-expiry
channel the heartbeat uses, because the connection pool lives in the worker and
the API holds no connection state of its own.

> **The original filing's premise had already been corrected, and the gap
> survived it.** ARCHITECTURE §10 says readiness must not report on drivers, and
> that is right — the API cannot see a connection, so a check there would invent
> one. But "which process owns the fact" is not an argument about whether the
> fact should be observable, and a driver unreachable for six hours is still a
> printer the farm believes it can dispatch to.
>
> **Two keys with two windows, and the difference is the design.** The readings
> expire with the loop that writes them; the roster — the printers the worker
> last said it was driving — is written afterwards and lives four times as long.
> Without that gap a silent worker takes the roster with it and the report goes
> quietly empty, which reads as "this farm has no printers". With it, the
> readings lapse first and every printer the roster still names reports
> `unknown`. That is the ADR-0007 case, and it has its own test.
>
> **No driver changes a status code**, and both halves of that were in the issue.
> Not readiness, where one unreachable printer would take the whole API out of
> rotation. And not `/health/workers` either: a farm with a machine switched off
> is normal, and a probe that is permanently red is a probe nobody reads. Only
> the loops decide the code; an alert about a driver keys on the body.
>
> **The roster is what the worker observed, never the `printers` table.** Reading
> the table for it would report on machines this process never tried to reach —
> root CLAUDE.md §1's denominator rule, in the one place it would have been
> easiest to get wrong.
**The SLA credit has a ledger, so a figure that left the farm can be checked**
([#75](https://github.com/iritur/printorian/issues/75)). Every movement of
`orders.sla_credit` appends a row to the new `sla_credit_entries`, carrying the
previous value, the new one, the promise and the three decay terms it was derived
from — the sweep's accruals and the freeze at dispatch alike. The column used to
be its own only record: each pass overwrote it, `SlaCreditAccrued` went to a bus
whose sinks persist nothing, and no prior value survived. Money leaves through
`PaymentsService.refund_sla_credit` and revenue is reported net of the figure, so
that was a gap rather than untidiness.

> **It is a table of its own, and the issue asked for `order_events` rows.** The
> deviation is recorded on the issue. `OrderView` eagerly loads `Order.events` on
> every read, `table()` included — and the credit moves on *every* sweep: at the
> default `sla_sweep_seconds=300` a `standard` promise moves 1 728 times before it
> hits the 30% cap, so a page of twenty late orders would have carried
> thirty-four thousand event rows in one response. The ledger is written far more
> often than an order's history and is read by query, so it is deliberately not
> reachable from `Order`.
>
> **`sla_sweep_seconds` is now also the ledger's resolution.** Worth knowing
> before it is tuned for some other reason: halving it doubles the rows.
>
> **Nothing is backfilled and nothing can be.** The previous values were never
> written down, and a ledger opening with rows reconstructed from today's column
> would be an invented history (ADR-0007). An order already late when 0021 ran
> gets its first entry on its next movement.

**A promise now carries the terms it was sold under**
([#74](https://github.com/iritur/printorian/issues/74)). `orders` gained
`decay_percent_per_day`, `decay_grace_seconds` and `decay_max_percent`, copied out
of `POLICIES` at placement; `_credit_for` computes from that copy. Before this the
order recorded only the policy *code*, so raising `standard` from 5%/day to 10%/day
did not price the next sale — it doubled the credit owed on every promise already
sold, on the next pass of `workers/sla.py`.

> **The test is the part worth keeping.** It sweeps, edits `POLICIES` between two
> sweeps, and sweeps again: without the fix the second sweep moves an existing
> order's credit from 183.27 ₽ to 366.54 ₽, which is the defect stated as a number.
> All six gates passed both before and after — this is the D13 case again, where
> shape was green and behaviour was wrong.
>
> **Migration 0020 backfills the three known codes with the values they hold
> today**, and leaves any other code null. That is not an invented number: today's
> values are what every existing order is *already* being priced at, so writing
> them down changes nothing anyone is owed and only stops the next edit reaching
> backwards. A row with an unrecognised code has no recorded terms and gets none;
> `_terms_for` falls back to the live lookup for those, exactly as before.

**The two listings that were never paged now say when they need to be**
([#45](https://github.com/iritur/printorian/issues/45)). `GET /printers` and
`GET /materials` still return everything, which `DATABASE-REVIEW` §9 records as a
deliberate gap and argues correctly: both are bounded by the size of the farm, not
by history. `contexts/fleet/listings.py` and `contexts/inventory/listings.py` count
their own listing on every readiness probe, and `/health/ready` reports
`printers_listing` and `materials_listing` separately, `degraded` past 500 rows.

> **#45 stays open too**, for the reason #44 does — `docs/WORKFLOW.md` §3, a
> `deferred` issue closes when its trigger fires *and* the work is done. Nothing
> here pages anything. What changed is that the trigger stopped being a sentence in
> a document, which matters more here than it did for #44: the growth #45 names as
> the dangerous one is the purchasing screen adding spare parts, packaging and
> printers to the materials listing. That arrives as a *feature*, not as traffic,
> and nobody is looking at row counts on the day a feature ships.
>
> **The count deliberately stops at the trigger.** `capped_count` issues
> `count(*)` over a `LIMIT`, so the check costs the same on a farm with a million
> lots as on one with six — a plain `count(*)` would be a scan growing at exactly
> the rate of the problem it watches, at its most expensive on the farm that most
> needs the answer. The catalogue trick #44 used is not available: these readings
> have a predicate (active specs, lots with material left) and `pg_class` counts
> whole relations. The price is that a reading past the line is a *floor*, and
> `ListingSize.is_exact` says so rather than passing a capped count off as a
> measurement.
>
> **Three counting decisions read as arbitrary and are not.** Retired printers are
> counted, because `include_inactive=true` returns them and nothing ever deletes a
> printer row — that is the half that only climbs. Inactive material specs are
> *not*, because `/materials` has no such parameter and cannot be asked for them.
> And the materials reading counts the live lots nested inside each spec as well as
> the specs, because the response grows on both axes.
>
> **These two clear on their own**, unlike `assignment_records` beside them. They
> read a set that can shrink; that check marks a threshold crossed once. Both
> comments say which they are, because the reasoning does not transfer.
>
> **One of #45's three triggers is not measurable as the endpoint stands, and the
> issue is worth amending.** It watches lots accumulating "without being retired",
> citing `/materials/lots` — but there is no such route, and the lots that ride
> inside `/materials` are only the ones with material left in them, because
> `_to_view` drops a spent spool. So that half tracks material on hand, not
> receiving history, and it will not climb the way the issue expects until the
> route it names exists. `contexts/inventory/listings.py` says this where the next
> reader will be standing.
>
> Seven mutations were run and each failed: dropping the wiring, removing the cap,
> calling a saturated reading exact, filtering the printers count to active rows,
> dropping the lot half, dropping the active-spec filter, and replacing the reading
> with a confident zero.

**The table ADR-0018 said would be "watched" is now actually watched**
([#44](https://github.com/iritur/printorian/issues/44)).
`contexts/production/growth.py` measures `assignment_records` against the trigger
`DATABASE-REVIEW` §9 states — 10 million rows or 20 GiB — and `/health/ready`
reports it as a check of its own, `degraded` once either half is past. Two `pg_class`
columns, so a readiness probe pays nothing; `count(*)` over ten million rows on a
path a container runtime calls every few seconds would have made the check the
outage.

> **#44 stays open, and that is the point of it.** It is a `deferred` issue and
> `docs/WORKFLOW.md` §3 says one closes only when its trigger fires and the work is
> done. Nothing here partitions anything — the deferral is still correct for the
> reasons ADR-0018 gives. What changed is that the trigger is measured rather than
> remembered, which is the half of "indexed and watched" that was not true.
>
> **Two things about it read wrong at first and are deliberate.** The row figure is
> `pg_class.reltuples`, an estimate that is **absent** — stored as `-1` — until
> something analyses the table; it is reported as unknown rather than as zero, so
> the exact byte figure decides alone in that state (root CLAUDE.md §1, and the
> reason `estimated_rows` exists as its own function). And this check **does not
> clear on its own**: it marks a threshold crossed once, so it stays lit until the
> table is split. That is the opposite of `wal_archiving` two screens down, which
> compares watermarks precisely so a fault that has passed stops showing red — the
> reasoning there does not transfer, and both comments now say so.
>
> The four gates that would have let this ship broken were each mutated and each
> failed: dropping the wiring, collapsing "never analysed" to zero, dropping the
> byte half, and replacing the reading with a confident zero.

**Three documents now fail CI when their inventories go stale, and two overstated
sections are corrected** ([#10](https://github.com/iritur/printorian/issues/10),
[#11](https://github.com/iritur/printorian/issues/11),
[#13](https://github.com/iritur/printorian/issues/13)). `DATABASE-REVIEW` §1,
`DESIGN-KIT` §1 and `DESIGN-KIT` §4 are checked against `__tablename__`
declarations, the console's routes, and the OpenAPI schema plus the frontend's client
calls. Each gate was proven to *fail* — nine mutations, every one caught with a
message naming the entry that drifted.

> **Writing the corrections produced three false claims, which is the finding.** An
> adversarial pass caught them before the commit, and each is worth knowing because
> each was believable:
>
> - **The SLA credit is not audited.** `refresh_sla_credit` writes no `order_events`
>   row, `SlaCreditAccrued` goes to a bus that persists nothing, and the sweep
>   overwrites `orders.sla_credit` in place. **No prior value of the credit exists
>   anywhere.** On a money path, worth fixing — not filed yet. *(Filed as #75, and
>   fixed below.)*
> - **The order does not pin the terms it was sold under.** Only the policy *code* is
>   stored; `POLICIES` holds the rates and `_credit_for` re-reads them every sweep, so
>   editing `standard` re-prices every unshipped promise. This is exactly what ADR-0020
>   exists to prevent, and the paragraph asserting otherwise cited ADR-0020 four lines
>   later. The comment at `ordering/policies.py:173` still carries the same wrong claim.
> - `/health/ready` reports `event_relay` only where a relay is configured, so "the
>   four health checks" overcounts a deployment without one.
>
> The prose now states both gaps plainly instead of reassuring. The first two are
> the issues [#75](https://github.com/iritur/printorian/issues/75) and
> [#74](https://github.com/iritur/printorian/issues/74) were filed from, and both
> are fixed below; the `policies.py` comment no longer carries the wrong claim.

**Every enum column now has a database-level CHECK**
([#43](https://github.com/iritur/printorian/issues/43)). Twenty-three columns across
eighteen tables were bare `VARCHAR` in PostgreSQL, guarded only by the Python type and
by Pydantic — both of which live inside the API process. Migration
`0019_enum_check_constraints` puts a constraint on each, `telemetry_samples` included
(a CHECK on a partitioned parent recurses into every partition and is inherited by
every future one, so `fleet.retention` keeps creating months that carry it).

> **The issue was filed as deferred, and the reason it gave was real but not the
> obstacle it looked like.** SQLAlchemy names a generated enum constraint after the
> enum *type*, so `order_events.from_status` and `to_status` — both `OrderStatus` —
> produce two constraints with the same name and the schema will not build. The
> recorded escape was a hand-picked name at every call site, which is a rule that
> decays. Naming the constraint after the *column* instead removes the collision by
> construction: a column is unique within its table by definition, so the twenty-fourth
> enum column is safe without anybody remembering anything. That is what
> `core.db.enum_column` does now.
>
> **`create_constraint=True` is still off, and turning it on will break `alembic
> check`.** Alembic skips CHECK constraints marked `_type_bound` on the metadata side,
> so a real constraint in the database reads as one the models dropped and every check
> reports drift. `_CheckedEnum` builds an ordinary constraint instead. Migration 0006
> hit this exact wall and recorded it; its comment now points forward.
>
> **What the gate does not cover.** Alembic matches CHECK constraints **by name
> only** — measured, not assumed: adding a member to an enum with no migration behind
> it passes `alembic check` clean. `test_every_enum_column_is_checked_in_the_database`
> compares the permitted value sets against the migrated database and is the only
> thing that catches it. Adding an enum member is now a migration.

**A paid order now becomes print jobs without anybody clicking anything**
([#41](https://github.com/iritur/printorian/issues/41)). `workers/intake.py` is a
seventh worker loop, reconciling rather than reactive for the reason
`workers/postproduction.py` argues: it asks "which paid orders have no jobs yet"
every thirty seconds, so a tick missed during a restart costs latency and never an
order.

> **The gap was wider than the issue said.** #41 recorded that jobs were created by
> "the jobs API, i.e. a person". They were not created by anything —
> `grep -rn "create_job" printorian/` returns the definition and no caller, and
> there is no create-job endpoint. What existed was a *test helper*, in
> `tests/scenarios/test_repeat_order_skips_prep.py`, whose docstring reads "what a
> caller does when an order is paid". This is that helper promoted into the
> product, which is worth knowing because the scenario test has been green the
> whole time the product could not do it.
>
> **Two things it will not do, both deliberate.** A cache *hit* still goes to prep
> rather than straight to the queue: attaching a plate writes an `EstimateVariance`
> whose `prepared_cost` is `NOT NULL`, and nothing prices a plate — a zero there
> would record "the estimate was perfect" for a variance nobody measured, which is
> §1 of CLAUDE.md in the flattering direction. Repricing from slicer truth is
> [#58](https://github.com/iritur/printorian/issues/58) — **and that half is no
> longer true; the entry at the top of this section is what replaced it.** And a
> line carrying an asset whose digest will not resolve **refuses the whole order**
> instead of making the job: a job with an asset but no `model_hash` slices, prints
> and ships correctly, and quietly sends every repeat of that configuration back
> through an engineer for ever, because `plate_key` can never match it.

**Open work has moved into GitHub issues, and this changes where to look first.**
Forty-seven issues across twelve milestones, with the labels and the process in
[docs/WORKFLOW.md](docs/WORKFLOW.md). Nothing in the code changed; what changed is
that §3 of this file is no longer the place a session finds its next task.

> The reason is the one in §4 of CLAUDE.md. Four documents each carried a list of
> outstanding work — §3 here, `ROADMAP.md`, `DESIGN-KIT.md` §2 and §4, and
> `DATABASE-REVIEW.md` §10 — and building the backlog found them disagreeing with
> each other and, twice, with the code.
>
> **Both status tables named there have now been corrected**, each verified against
> the code rather than against the document that reported it
> ([#8](https://github.com/iritur/printorian/issues/8),
> [#9](https://github.com/iritur/printorian/issues/9)).
> `INFRASTRUCTURE.md` §1 was stale in seven rows and is re-derived, dated, and split
> so that what is *built* and what is *scheduled* are different claims; Stage 0 is
> marked done. `DESIGN-KIT.md` §1 and §2.1 said the settings screen was unbuilt while
> `SettingsPage.tsx` served 102 parameters across fourteen sections — §2.1 now
> records only what is still owed and links it, because a second description of a
> finished screen is a second thing to keep in step.
>
> One finding recorded there has since been closed rather than corrected:
> **`main` now has branch protection** — `backend`, `frontend` and `image` required,
> linear history, no force-push, no deletion
> ([#4](https://github.com/iritur/printorian/issues/4)). The other was new rather
> than transcribed: the timestamp-sort flake class §2 estimates at "about a dozen"
> is **fifteen**, measured, with the file and line of each in the issue.

**The README is now the front door rather than a summary.** Same facts, reorganised
around two rendered diagrams — the container topology (who talks to whom, and over
what) and the order state machine, drawn from `contexts/ordering/policies.py` rather
than described. `docs/assets/banner.svg` and `banner-light.svg` are generated from the
design kit's own tokens and swap on `prefers-color-scheme`. Three things it corrected
while being rewritten: the context list was missing `account`, `packaging`,
`postproduction` and `settings`; `printorian/workers/` was absent from the layout
altogether; and [docs/RUNBOOK-FIRST-BOOT.md](docs/RUNBOOK-FIRST-BOOT.md) had never been
added to the document table. Volatile counts were deliberately left out of it and
pointed here instead — a badge reading `tests 1227` is the staleness trap in §4 of
CLAUDE.md with a nicer font.

> **`docs/DATABASE-REVIEW.md` §1 was stale in every figure it carried**, and is fixed.
> It said "**22 tables** across seven contexts, built by nine Alembic migrations"; the
> ORM has 42 across twelve, and `backend/alembic/versions/` holds twenty. Its table was
> missing `account`, `journal`, `packaging`, `postproduction` and `settings` entirely,
> plus `catalog_models`, `catalog_model_materials` and `metric_rollups`. The list is now
> diffed against every `__tablename__` under `contexts/` and matches exactly. "Single
> linear head" was the one true part — one root, one head, no branch points. The rest of
> the document already discussed the newer tables; only the summary had drifted, which is
> the failure mode §4 of CLAUDE.md warns about: the part everyone reads first is the part
> nobody re-derives.

**The settings screen is built, and the settings take effect.** `contexts/settings`
now serves the whole kit's catalogue — about a hundred parameters across fourteen
sections (diagnostics is read-only, so it has no fields) — through `GET /settings`,
`GET /settings/sections` and the existing `PUT/DELETE /settings/{key}`, gated on a
new `MANAGE_SETTINGS` permission (owner only, replacing `MANAGE_PRICING` on the
router). The console has a `SettingsPage` (owner-only nav) that renders one control
per `kind` — number/unit, select, switch, string, and a **write-only, encrypted
secret** (`finance.yookassa_secret_key` is stored under `PRINTORIAN_SECRET_KEY` and
never read back). Editing marks a row dirty, counts into a save bar, and each save
is a separate audited «было · стало», shown in «Обслуживание системы».

> Three settings now **take effect at the read edge**, the same shape
> `resolve_rates` always had: `resolve_promise()` (lead times — a changed
> `sla.min_lead_hours` moves the next quote) and `resolve_scheduling()` (planner
> weights, resolved per scheduler pass). The loop intervals (`scheduler_tick_seconds`,
> `telemetry_poll_seconds`, `sla_sweep_seconds`) are still read at worker startup,
> so they take effect on restart — recorded rather than wired, because a per-pass
> re-read is a worker-loop change with little payoff. Of the kit's table-valued
> settings, the **volume ladder** and the **customer tiers** are built (both
> stored as JSON and parsed back into `DiscountLadder` / `CustomerTier`); the
> tiers' discount and margin override reach the engine through `resolve_tiers()`,
> while the loyalty `from_spend` thresholds that *earn* a tier stay in
> `loyalty.py`. The rest — maintenance intervals, zones, event matrix, API keys,
> webhooks — are **not built** — see §3; the diagnostics panel now is, above.
> Two of the three irreversible operations are wired: `POST /settings/reset-rates` drops every
> `pricing.*` override (audited per row), and `POST /settings/drop-telemetry` runs
> retention now through the **shared clamp** — `retention.drop_telemetry_past_retention`,
> which the maintenance worker also uses, so «drop now» and the scheduled sweep
> cannot drift apart. The third (clear waitlist) is not built.

> **A review pass fixed four defects in that screen**, each now covered by a test
> that fails without the fix. The save bar's «Отмена» was markup copied from the
> kit with no handler — it discards every draft now. `ConfirmAction` compared the
> typed farm name against the stored one, so a farm with a *blank* name matched an
> empty box and armed the irreversible operations on one click; the confirm is
> withheld and explains why. Clearing a number box saved `0` rather than being
> refused, because `Number('')` is `0` and the guard tested for `NaN` — an emptied
> numeric field now blocks the whole save and names itself. And `groups.in_group_order`
> keeps a panel's fields contiguous: `pricing.material` and `scheduling.normalization`
> were each split across their section, so the screen drew the same heading twice
> and gave two React siblings one key. That last one is structural on purpose —
> the pricing fields come off `RateSnapshot`'s declaration order, so hand-sorting
> would only hold until the next field was added there.

**The farm can change its own pricing rates.** `contexts/settings` is a key/value
store with an audit, serving the seventeen scalar rates through
`GET/PUT/DELETE /settings`, gated on `MANAGE_PRICING` (owner only). Two properties
are load-bearing and both are tested:

> A key with no row resolves to the **code default**, so an empty table prices
> exactly as the farm always did — nothing is seeded, and the migration moves no
> prices on the day it runs. And an order keeps the rate snapshot it was agreed at
> (ADR-0020), so raising a margin changes the next quote and nothing already sold.

The catalogue is derived from `dataclasses.fields(RateSnapshot)` rather than
hand-listed, so a rate added later appears in the screen without a second place to
remember. The other ~85 kit parameters are still constants on `core.config.Settings`
and are a bigger job than they look: they are read once at process start, so moving
them changes *when* they are read as well as where from.

`pricing.py` reached the 400-line gate and split — spec assembly moved to
`_pricing_spec.py`, and with it the mesh-analysis cache.

**Telemetry is summarised, and retention is on.** `metric_rollups` holds one row
per printer per hour. `telemetry_retention_days` ships at 90 for the first time —
it had been 0 since the table existed, because dropping raw samples with nothing
summarising them destroys the only copy.

> The safety property is a **clamp**, not an ordering. The drop cutoff is
> `min(now − retention, watermark)`, where the watermark is the hour rollups have
> actually reached. A farm whose summarising stalls stops dropping raw samples
> with it, and one that has never summarised an hour drops nothing at all. If you
> touch `workers/maintenance.py` or `contexts/fleet/retention.py`, this is the
> invariant to preserve — it is the only irreversible path in the system.

**The dashboard's occupancy figures changed source, and some numbers moved.**
`run_hours` / `capacity_hours` / `idle_hours` and the 7 × 24 load map used to be
derived from `print_jobs` — booked time, not running time. A paused or errored
print counted as run time; a job row never closed counted from its start to `now`
for ever; machine time with no job behind it was invisible; idle was a residual
against the roster. They are now measured from `metric_rollups`, and the
job-derived versions were deleted rather than left alongside.

Two consequences are visible on purpose: the note reads «ИЗ N ИЗМЕРЕННЫХ» rather
than «ВОЗМОЖНЫХ», and an unmeasured window shows an em dash where it used to show
`0.0`. On the load map, an hour nobody polled is **hatched** — distinct from both
bright and the outlined zero that means "measured, and idle".

**`GET /fleet/metrics` and `/fleet/metrics/{printer_id}` serve it.** Seconds only;
money and energy stay behind `VIEW_FINANCIALS`. Deliberately two routes with
different shapes, under their own prefix — `/printers/metrics` would collide with
`/printers/{printer_id}` by declaration order.

**The legacy `--pr-*` design tokens are gone**, along with 119 dead CSS rules.
Reachability was decided against the built bundle, not the source. One real bug
fell out: `.prep__done` asked for an undefined token, so its hard-coded fallback
always won — a light-palette green on a near-black panel at ~2.2:1.

**A farm can be provisioned, and can no longer be served from a developer dump.**
`tools/provision_owner.py` creates the first owner — the only account in the system
made without an authenticated actor behind it. It reads the password from the
terminal rather than an argument (a password in `argv` is in the shell history and
in `ps`), and refuses outright if an owner already exists: provisioning is first
boot, not a password reset, and the two want opposite behaviour.

> The guard that matters is `contexts/identity/reserved.py`. `DEVELOPMENT.md`
> publishes two account passwords, one an owner, and restoring a dump into another
> environment is routine — so the API now **refuses to start in production** while
> any account sits in a domain reserved for documentation (RFC 2606 / RFC 6761).
> Not a warning: a warning about credentials is read after the incident.
> `docs/RUNBOOK-FIRST-BOOT.md` is the procedure. `scripts/create_owner.py` was
> deleted — it was referenced by nothing and defaulted to the published password.

**Backup and the restore drill have now actually been run.** Both were correct and
had never once been executed. `backup.sh` produced a verified dump and a base
backup; the dump restored into a scratch database at schema head. Two real defects
fell out and are fixed:

> The drill demanded rows in `payment_notifications`, so it **failed on any farm
> that had not yet taken a payment** — which is every farm in its first week,
> exactly when the first drills run. It now compares restored counts against the
> live database, which keeps the failure it was built to catch (a backup pointed
> at the wrong database, producing a valid empty dump nightly) and drops the false
> alarm. And `backup.sh` wrote the dump under its final name while still filling
> it, so a drill overlapping a slow backup would pick a half-written file; the dump
> is now renamed into place only after it verifies.

**`deploy/systemd/` holds the units that run the farm** — the stack, a nightly
backup, a weekly drill — verified with `systemd-analyze`. This is the piece of
INFRASTRUCTURE Stage 2 that closes a measured risk rather than a theoretical one:
`pg_archivecleanup` runs only inside `backup.sh`, so with nothing scheduling it,
archived WAL grows without bound. The dev stack reached **847 segments / 13.9 GB in
four days**, and `compose.prod.yml` already carries a comment recording the earlier
version of this failure at 23 GB.

**There is a farm, and it survives being mistreated.** `192.168.29.148`, Ubuntu
26.04 under VMware, root grown to 96 GB and `/mnt/backup` on a **second physical
disk** — the ADR-0019 separation the compose default violates. Console on
`:8080`; the API is reachable only through its Caddy at `/api`, and postgres and
redis are not published at all.

The **storefront** runs on `:8081`, but only when asked for:

```bash
docker compose -f deploy/compose.prod.yml --profile storefront up -d
```

It sits behind a Compose profile because this is not where it belongs — ADR-0016
puts it on the rented edge VPS, with TLS there and WireGuard back, and that is
Stage 3. Without it a farm-only deployment cannot exercise ordering, quoting or
uploading at all, which is most of what a customer does. `web-dist` stays the
bundle the edge will receive; the new `storefront` target is the same bundle
behind a Caddy using the identical `/api` prefix, so nothing needs rebuilding to
move. `deploy/storefront.Caddyfile` names every difference from the edge.

Measured on that host, not asserted: a reboot brings back the mount, the stack,
the timers and the data unattended; `SIGKILL` to the API is healthy again in ten
seconds; `SIGKILL` to postgres keeps the data through WAL replay and the API
recovers its pool without restarting. Backup and drill both run green through
systemd.

> **Do not trust `systemctl is-active printorian.service`.** `Type=oneshot` with
> `RemainAfterExit=yes` means "ExecStart returned 0 once", not "the farm is up".
> Observed within the hour: `systemd says: active | containers running: 0`, and
> `systemctl start` will not fix it — only `restart` re-runs ExecStart. The honest
> checks are `/health/ready` and the container count.
> `printorian-ensure.timer` reconciles every five minutes and logs loudly when it
> has to act; it reconciles toward systemd's *intent*, so a farm deliberately
> stopped stays stopped.

**Nine defects that only a real host could surface.** Four of them were in units
committed hours earlier: the image copied `tools/` but not `scripts/`; nothing
mounted `backup.sh` into postgres; the api container had no `/backup`; the api
image had no `pg_restore`. Three more were worse:

> **A write that was never committed.** `Database.session()` commits *after* the
> yield, so `return` from inside `async for session in db.session()` leaves the
> generator suspended and the commit never runs — the interpreter finalizes it
> with `GeneratorExit`, a `BaseException`, which slips past the `except Exception`
> that would at least have rolled back loudly. `provision_owner.py` created the
> farm's first owner, discarded the insert, printed "Created owner" and exited 0.
> `api/ws.py` had the same shape. `tests/unit/test_session_lifecycle.py` now walks
> the AST and fails on the pattern anywhere.
>
> **The drill could never have run on a farm.** Synchronous SQLAlchemy resolved a
> bare `postgresql://` URL to psycopg2 — declared only in the *dev* group, for one
> migration test. Exactly the failure INFRASTRUCTURE §6 predicts. Now async.
>
> **One full backup disk wedged WAL archiving permanently.** `archive_command`
> copied to the final name, so a full disk left a 786 KB fragment of a 16 MB
> segment there; `test ! -f` then saw a file and the `&&` chain never ran again —
> and freeing the disk did *not* help. It now writes `%f.tmp` and renames.

**The farm now says when its backup guarantee stops holding.** With the disk full,
`/health/ready` used to answer `{"status":"ok"}` while archiving failed and
`pg_wal` grew toward filling the *data* disk; `systemctl --failed` listed nothing.
It now reports `wal_archiving: degraded` — degraded rather than failed, because
serving is unaffected and taking the API out of rotation would turn a broken
backup into a broken farm. It compares the two watermarks rather than
`failed_count`, which never resets and would leave a farm red for ever over one
bad night in March.

**Archived WAL is gzipped, which changes the disk arithmetic.** `archive_timeout`
is 1 min, so segments are switched on *time* rather than fullness — 288 segments a
day on an idle farm, nearly all empty, at 16 MB each. Measured: 80 MiB of segments
compress to 2.95 MiB (**27×**), and the near-empty ones go 16 MiB → 16 KiB. That is
a 98 GiB backup disk filling in **22 days** versus over a year. PITR therefore needs
`restore_command = gunzip -c /backup/wal/%f.gz > %p`; a `cp`-based one finds nothing
and PostgreSQL reports recovery *complete* rather than failing.

**The console was rendering without its fonts.** Caddy's CSP said `font-src 'self'`
while Vite inlines the interface font as a `data:` URI, so every Harvester face was
blocked and the console fell back. Invisible in development, where the dev server
sends no CSP at all.

**A host-readiness check now exists as a runnable script.** `deploy/readiness-check.sh`
evaluates a candidate farm server — OS family, systemd, RAM/CPU/disk, the ADR-0019
backup-disk separation, clock and timezone, Docker + the compose plugin, the `.env` and
its required secrets, port bindings, printer reachability, and the systemd units — and
prints a PASS/WARN/FAIL tally with the fix for each failure. It exits non-zero on any
FAIL, so it can gate the Stage 2 Ansible role instead of deploying onto a host that is
missing a disk or a secret. This is the first executable half of the "host configuration
is prose" row in INFRASTRUCTURE §1 (provisioning, not checking, is still Ansible).

## 2. Deliberately unfinished

Not oversights. Changing any of them is a decision, not a cleanup.

| | Why it is like that |
|---|---|
| `customer_storage_quota_bytes` displayed, not enforced | Refusing a quote mid-configuration is the wrong UX. Growth is bounded by `model_retention_days` instead. |
| Rate limiting and sign-in lockout are in-process | Correct for one API process (ADR-0003). Counters reset on restart; a second replica would get its own allowance. `docs/DATABASE-REVIEW.md` §9. |
| No `/metrics` endpoint | Stage 5. `/health/workers` gives the honest liveness signal meanwhile — it reads beats each worker loop records at the *end* of a pass, so it distinguishes wedged from working. |
| Off-site backup sync has a recipe, no committed job | Needs farm-specific credentials. |
| `assignment_records` is not partitioned | ADR-0018's deferral still holds — bounded by planning frequency, not by the clock. `/health/ready` now reports when the trigger fires; [#44](https://github.com/iritur/printorian/issues/44) stays open until it does. |
| Storefront `body` lifts the page ground | Predates Harvester; `--hv-bg` vs `--hv-void` is six values out of 255 in dark, identical in light. A visual call, not a cleanup. See `apps/web/src/app.css`. |
| TypeScript held at 5.x | `openapi-typescript` crashes on TS 7. Reason and three failed workarounds are in `.github/dependabot.yml`. |
| Six queries still sort on a timestamp alone | Read in one pass and left that way on purpose. See below. |

**The single-column time sort has been triaged, once, across the whole tree**
([#42](https://github.com/iritur/printorian/issues/42)). It started with
`SettingsService.history`, which ordered by `changed_at DESC` and nothing else: every
row a test writes shares one timestamp, so the sort tied and CI and a dev machine
disagreed about the same two rows. Ordering by `id` as well settles it *correctly*
rather than merely consistently — `core.ids.new_id` builds a UUIDv7 from
`time.time_ns()`, the real clock, so ids stay chronological where `changed_at` is
frozen.

**The idiom now lives in `core/pagination.py`**, next to the argument about sort keys
that was already there, along with the two things that make it a rule rather than a
habit. The tie is not a test artifact: `Entity.created_at` is a `server_default` of
`now()`, and PostgreSQL's `now()` is the *transaction's* start, so every row one pass
writes carries the same timestamp in production too. And the rule has an exception —
`JobEvent` and `OrderEvent` carry an explicit `sequence`, because UUIDv7 orders only to
the millisecond and a job passes three statuses inside one.

**Where the second term is worth having, and where it is churn.** A sort under a
`LIMIT` decides *membership*: a tie at the boundary moves rows in and out of the answer,
so unchanged data reads differently twice. Fourteen queries were fixed: nine of the
measured fifteen, plus five of the same shape that a *second* term had hidden — the
scheduler among them, which sorted by priority and `created_at` and still tied. A sort
that returns a whole set for a screen to render decides only *presentation*, and a term
there is churn; the remaining six were read and left, each saying so at the line.
`production/prep.py` is that same call and unbounded, so it stands as it was.

| Fixed | Left single-term |
|---|---|
| `production/planning.py` (the ready batch), `production/reads.py` (both), `production/queue.py` (both), `production/throughput.py`, `packaging/board.py` (both), `packaging/catalogue.py`, `packaging/service.py`, `postproduction/board.py`, `account/service.py` (both), `workers/postproduction.py` | `catalog/assets.py`, `identity/service.py`, `identity/sessions.py`, `ordering/history.py` (both), `payments/service.py` |

`production/queue.py`'s `_first_entered` was the one that wanted `sequence` rather than
`id`, and `packaging/board.py`'s pickup roll-up wanted the rest of its group key —
there is no `id` in a grouped result. `tests/unit/test_production_ordering.py` covers
the planner and the assignment record under `FixedClock`; six of its eight tests fail
on every run against the code as it was, which is the part worth knowing.

## 3. What is actually next

**Open work lives in [GitHub issues](https://github.com/iritur/printorian/issues),** grouped by [milestone](https://github.com/iritur/printorian/issues?q=is%3Aopen) and described in [docs/WORKFLOW.md](docs/WORKFLOW.md). Take one from a milestone rather than from this section. Where an issue and a document disagree, the issue is right.

## 4. The first real print — started, and blocked on hardware

`printorian.drivers.bambu` has still never talked to a printer. Phase 4's exit
criterion was demonstrated with the `mock` driver, and `tools/bambu_spike.py`
proved the *protocol* in standalone code importing nothing from Printorian. The
product's own path between the two is the largest unproven assumption in the
system, and the one that could still say the design is wrong.

What exists now so that proving it is one command rather than a project:

- **`tests/contract/test_bambu_hardware.py`** — the same contract the mock driver
  is held to, run against a real machine. Credentials come from the git-ignored
  `printers.local.toml`; without it every test **skips**, so CI is untouched and
  nobody needs a printer to work on the rest of the system. Read-only by default —
  connect, capabilities, telemetry, and that wrong credentials are *refused* rather
  than answered plausibly. The half that physically prints is behind a second
  opt-in, because a suite that can start a print by accident is one nobody runs.
- **[docs/RUNBOOK-FIRST-PRINT.md](docs/RUNBOOK-FIRST-PRINT.md)** — the procedure,
  in order, with what each failure *means*. Step 1 separates "the network or the
  credentials" from "our code", which are indistinguishable from a distance and
  have completely different fixes.

**This needs you and a printer.** Nothing further can be verified without one.

## 5. Needs a person, not an agent

- **A cancelled job keeps its wait-list row, and that is a second defect on a
  different path — it needs an issue.** Measured, not reasoned about: a probe run
  against the fixed tree wait-listed a job, cancelled it, and
  `queue_position` still answered `job_status=cancelled` with
  `reason='waitlist.awaiting_capacity'`, `position=1` and a predicted start.
  `ProductionService.cancel` does not touch `wait_list_entries`, and a cancelled
  job is never claimed by another planning pass, so unlike the defect §1 fixes
  this row is stale *permanently* and keeps inflating other customers' positions.
  It was left out deliberately: the fix asked for was scoped to
  `_refresh_wait_list`, and folding an unrelated behaviour change into it is what
  WORKFLOW §3 means by one issue closing on one demonstrated criterion. The shape
  of the fix is one call to `wait_list.discard(db, job_ids=[job.id])` in `cancel`,
  plus the test that fails without it. Editing the tracker is not an agent's to do.
- **Dev account passwords have drifted from the docs.** `DEVELOPMENT.md` lists
  `floor@printorian.example` / `shop-floor-pass-1`; the stored hash does not match.
  `boss@printorian.example` was reset to the documented `owner-pass-12345` and
  works. Also: `floor@` is `engineer` in the database, `operator` in the docs —
  a role is an authorization decision, so it was left alone.
- **Stage 2 is now half done by hand, and that is the argument for Ansible.**
  A farm host exists and works (§1), but every step of getting there was manual and
  is recorded nowhere executable. The exit criterion is "a wiped machine reaches a
  running production farm from `ansible-playbook` plus one SOPS key", and the
  cheapest time to write that role is now, against a host whose correct end state
  is known and reproducible. Ansible cannot run on a Windows control node, so it
  needs WSL or a container.
- **Stage 3 still needs hardware nobody has.** "The storefront serves over HTTPS on
  the real domain" needs a VPS, DNS and an object-storage bucket. Until then there
  is no customer-facing site anywhere — only the console, on the LAN.
- **Off-site backup is still the largest single gap.** Every copy of the farm's
  data is on one machine. §1's compression buys time on the local disk; it does
  nothing about fire, theft or that VM being deleted.
- **The storefront ground colour** (§2) — keep the lift or take Harvester's void.
- **`ruff format --check` was failing on `main`** before this run, on a migration
  committed as raw alembic output. Fixed in passing; worth knowing the gate can
  drift without anyone noticing, because a red CI on `main` is easy to live with.
- **#47 needs amending before it closes, and the `drift` record needs deciding.**
  Its "Done when" opens with "the 66 tests build real parents" and "foreign keys
  are enforced for the whole fast suite", both of which ADR-0021 and
  `tests/factories.py` had already made true; only the third clause was the work
  (§1). WORKFLOW §3 says a "Done when" that turned out to be the wrong criterion is
  amended *in the issue before closing*, so the record says what was delivered —
  and WORKFLOW §5 says the stale document gets its own `drift` issue rather than a
  silent close. The correction to `DATABASE-REVIEW` §3 and §10 landed with the same
  pull request, so what is left is a filing decision: amend #47, and decide whether
  a `drift` issue closed by its own merge is still worth opening for the record.
  Editing the tracker is not an agent's to do.

## 6. If you are picking up mid-flight

Two things this repository will not tell you and that cost an afternoon each:

1. **Check `git status` first.** Agent work has been interrupted mid-write more
   than once, leaving a tree that compiles, passes lint, and has no tests for the
   part that matters. All six gates passed on a change whose only irreversible
   path was untested.
2. **Do not trust an agent's report of its own verification.** Reproduce it. Two
   separate reports of "all gates green" were produced by runs where the gate had
   not been executed at all.
3. **The repository moved.** `origin` is now
   `git@github.com:iritur/printorian.git`; the former home,
   `dimmus/printorian`, is still reachable as the remote `dimmus` and still
   holds the old Dependabot branches. Nothing was deleted there — if a clone
   or a CI job on some machine still fetches `dimmus`, it will keep working
   and will quietly fall behind, which is the failure mode to watch for.
   Push over SSH: the HTTPS credential helper has nothing cached for GitHub,
   and `core.sshCommand` points the key at `C:/gitssh/` because a Cyrillic
   home directory defeats Git's bundled ssh.
