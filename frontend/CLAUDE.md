# Working in `frontend/`

Applies to the two React apps and the shared packages. Cross-cutting rules —
ADR-0007, D13, the comment style, the exit-code trap — are in the root
[CLAUDE.md](../CLAUDE.md) and are not repeated here.

## Gates

```bash
cd frontend
npm run typecheck && npm run lint && npm run test && npm run build
```

Run them separately when something fails, so you read the failing command's own
exit code rather than the chain's.

Prettier is **not** a CI gate. `npm run format:check` exists and root markdown does
not pass it; do not reformat files you are not otherwise editing, or the diff
stops being about the change.

## The generated client (ADR-0005)

`packages/api-client/src/generated/` is produced from the backend's OpenAPI
document and **never hand-edited**. After any backend route or response change:

```bash
cd backend && ./.venv/Scripts/python.exe tools/export_openapi.py --out openapi.json
cd frontend && npm run generate:api
```

**But regenerating does not cover every screen**, and this is the trap that has
already shipped a broken dashboard:

> `apps/console/src/dashboard/types.ts` mirrors `GET /dashboard` **by hand**, by
> convention — the generated client covers the transport, and the console screens
> type their own rows. So removing a field from the backend response leaves `tsc`
> perfectly green while the tile renders `undefined`. When you change a dashboard
> response, change both, and let the type error tell you what else to fix.

## Two apps, one language

`apps/web` is the storefront (public realm), `apps/console` is the farm console
(control realm), and `packages/ui` is shared. A component in `packages/ui` is
imported by both — `OrdersScreen` is the one that catches people out, because it
lives in the shared package and is imported only by the console.

**Harvester is the design language.** Everything is `--hv-*` tokens and `.hv-*`
classes; the legacy `--pr-*` set is gone and `packages/ui/src/tokens.test.ts` fails
if one returns. The kit in `design/*.html` is the source of truth for what a screen
shows — match its class names, because `packages/ui/src/harvester/` already styles
them. If you find yourself writing new CSS, check the kit first.

## ADR-0007 on this side

The root file states the rule. What it means in a component:

- A null figure renders as an em dash, never `0`.
- An unmeasured hour needs a **third** treatment, distinct from both busy and idle —
  the load map hatches it. Drawing it dark says "measured, and quiet", which is a
  claim the farm never made.
- A denominator shown to a person should be what was observed, not the roster.

## Traps

**Reachability is a question about the bundle, not the source.** Grepping for a
class name cannot tell a live `className` from the same word in a message catalogue
or a route key — that mistake said five dead selectors were in use. Build, then
read the `className` values out of `apps/*/dist/assets/*.js`.

**Message keys are typed.** A key missing from either catalogue in
`packages/ui/src/i18n/messages.ts` is a type error, not a blank string — RU and EN
must carry exactly the same keys.

**jsdom decides accessible names.** Whether two adjacent inline elements join with a
space depends on the jsdom version, so an assertion like `/Label 2/` is a hostage to
a dependency bump. Match `/Label\s*2/`.

## Dependency hold

`.github/dependabot.yml` holds **TypeScript major** and nothing else. TS 7 is the Go
rewrite; `openapi-typescript` crashes on it (`ts.factory` is undefined) and it is the
only route to the API client. Three workarounds were tried and all fail — they are
listed in the config so nobody repeats the search. Lift it when
`npm view openapi-typescript peerDependencies` accepts 7.

## Do not "fix" this

**The storefront's `body` rule lifts the page ground** from `--hv-void` to
`--hv-bg` — six values out of 255, dark theme only. It predates Harvester, and
changing it is a visual decision rather than a cleanup. `apps/web/src/app.css`
carries the reasoning; the file exists for that one rule.
