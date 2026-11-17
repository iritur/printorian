---
name: printorian-api-client-regen
description: "The API client is generated (ADR-0005): tools/export_openapi.py then npm run generate:api; regeneration does NOT cover apps/console/src/dashboard/types.ts which mirrors GET /dashboard by hand — that gap once shipped a broken dashboard with tsc green; TypeScript major held <7 because openapi-typescript crashes on TS 7."
metadata:
  node_type: memory
  type: project
---

**Why:** Two half-clients (generated transport + hand-mirrored dashboard
types) fail in opposite ways: the generated one goes stale silently, the
hand one stays green while rendering `undefined`.

**How to apply:** Any backend route/response change: regenerate
(`cd backend && python tools/export_openapi.py --out openapi.json` then
`cd frontend && npm run generate:api`), and when `GET /dashboard` changed,
edit `apps/console/src/dashboard/types.ts` in the same change — let the
type error tell you what else to fix. Never hand-edit
`packages/api-client/src/generated/` (the PreToolUse hook blocks it).

Evidence: frontend/CLAUDE.md ("The generated client"), docs/adr/0005.
Incident: a removed backend field left `tsc` green and a dashboard tile
rendering `undefined` — the recorded reason the mirror rule exists.

Holds: TypeScript 7 is the Go rewrite; `openapi-typescript` crashes
(`ts.factory` undefined) and is the only route to the client. Three
workarounds were tried and all fail (listed in `.github/dependabot.yml`).
Lift only when `npm view openapi-typescript peerDependencies` accepts 7.

Related: [[printorian-host-layouts]].
