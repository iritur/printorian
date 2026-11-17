# Printorian harness memories

Provenance: seeded 2026-11-17 when the `.zcode/` harness was created. The
records fall into two groups:

1. **Doc-sourced traps** — facts already measured and written down in
   `docs/` (ADRs, runbooks, CLAUDE.md files), re-recorded here because the
   harness greps this archive before anyone touches a module. Each record
   cites its doc; the doc remains the authority.
2. **Owner rulings from harness sessions** — decisions made in conversation
   with the project owner and recorded here so agents stop re-deriving
   them (`printorian-deployment-shape`, `printorian-server-charter`, both
   2026-11-17).

Rules (full spec in `.zcode/skills/printorian-memory-writer/`): memories
are **historical** — they explain rules, they never prove current behavior;
extend existing records with dated sections instead of forking; every fact
carries its source; commit subject `memory: <topic>`, main checkout, never
push.

`MEMORY.md` is the index — keep it dense, one row per record, newest last.
