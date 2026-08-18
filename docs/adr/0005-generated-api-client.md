# ADR-0005 — Both clients consume a generated TypeScript API client

**Status:** Accepted · Phase 0 · 2026-08-05

## Context
A hand-written client restates the server's types in a second language. The restatement is
correct on the day it is written and drifts thereafter, and the drift is invisible until
runtime — nothing in either build knows the two files are meant to agree.

## Decision
The backend's OpenAPI schema is the contract. `packages/api-client` is **generated** from
it and never hand-edited.

The generated output is **not committed**. CI regenerates it from the schema the backend
job exports, then typechecks the frontend against it. Backend and frontend land in the same
commit, so a contract change that breaks a client is caught by the compiler rather than by
a checked-in snapshot going stale.

## Consequences
* A breaking backend change breaks the client build, at the commit that caused it.
* The frontend CI job depends on the backend job, because the contract has to exist first.
* Route operation ids must stay stable and readable - hence the explicit
  `generate_unique_id_function` in the app factory.
* `frontend/packages/api-client/src/generated/` is git-ignored; run
  `npm run generate:api` after pulling a backend change.
* Known wrinkle: `openapi-typescript` percent-encodes paths and then fails to read them
  back when they contain non-ASCII characters, which any Windows profile with a Cyrillic
  username has. `scripts/generate-api-client.mjs` stages the schema locally and passes
  relative paths to keep every argument ASCII.
