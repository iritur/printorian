# ADR-0009 — No runtime plugin loading

**Status:** Accepted · Phase 0 · 2026-08-05

## Context
Dynamic plugin loading is worth its cost — a manifest, load contexts, restart-to-toggle,
unload semantics that rarely work — only when it buys real isolation.

It buys none when the plugins are *screens* and the business logic they call sits behind
one shared data context. The coupling everyone wanted to break lives in that shared layer,
which every plugin references anyway; what the boundary actually produces is a copy of the
same UI helper in each plugin, diverging quietly.

Modularity applied to the dimension that does not need it, and withheld from the one that
does, costs twice.

## Decision
Feature modules are ordinary Python packages, all present, toggled by configuration.
Isolation is enforced statically by `import-linter` and `tools/check_context_isolation.py`,
not dynamically by a loader.

Only three things are genuinely pluggable, via entry points: **printer drivers**, **payment
providers**, **shipping carriers**.

## Consequences
* No restart-to-toggle, no load-context debugging, no plugin manifest.
* A context boundary violation fails CI instead of being invisible.
