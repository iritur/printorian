<!--
Kept short on purpose. The six gates already check shape; what a reviewer cannot
get from CI is what behaviour changed and what proves it — CLAUDE.md §2, and the
retention clamp that passed all six gates while untested.
-->

Closes #

## What changed, and why

<!-- The decision and its alternative, not a restatement of the diff. -->

## What proves it

<!--
Name the test that fails without this change. If the change is not testable —
infrastructure, a document, a generated asset — say what was run instead and
paste the result. "Should work" is not a result (CLAUDE.md §6).
-->

## Checks

- [ ] Gates run locally, output pasted or linked — not piped through `tail` (CLAUDE.md §4)
- [ ] `HANDOFF.md` updated if this changes what the next person should know
- [ ] Any status table this contradicts is corrected in the same change
- [ ] Touches an irreversible path (retention, backup, migrations, refunds) — if so, say which and what covers it
