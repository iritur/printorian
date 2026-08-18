# ADR-0012 — The backend emits codes, never localized prose

**Status:** Accepted · Phase 0 · 2026-08-05

## Context
The system is RU + EN from day one. If the backend formats user-facing sentences, every
message becomes a deployment to change and a second locale becomes a rewrite.

## Decision
Errors and events carry a machine-readable `code` plus structured `details` for
interpolation. Clients own the RU/EN message catalogues. `Money.__str__` is a machine
representation; localized formatting belongs to the client.

## Consequences
* Error bodies are exactly `{"code", "details"}` - asserted in the API tests.
* `details` carries values, never a pre-composed sentence.
* Adding a language is a frontend-only change.
