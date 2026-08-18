# Architecture Decision Records

Locked decisions for Printorian. Changing one requires a new ADR that supersedes it,
not a commit. Each records the problem that motivated it, so a later reader can tell
whether the reason still holds.

| ADR | Decision |
|-----|----------|
| [ADR-0001](0001-one-backend-one-database.md) | One backend, one database, one domain model |
| [ADR-0002](0002-pricing-is-a-pure-function.md) | The pricing engine is a pure function |
| [ADR-0003](0003-on-prem-backend.md) | Backend runs on-premises on the farm LAN |
| [ADR-0004](0004-electron-is-the-farm-console.md) | ~~Electron is the farm console~~ — superseded by ADR-0016 |
| [ADR-0005](0005-generated-api-client.md) | Both clients consume a generated TypeScript API client |
| [ADR-0006](0006-human-gated-slicing.md) | Slicing is human-gated, and its output is cached |
| [ADR-0007](0007-drivers-never-simulate-silently.md) | Drivers never simulate silently |
| [ADR-0008](0008-alembic-only.md) | Alembic is the only schema mechanism |
| [ADR-0009](0009-no-runtime-plugins.md) | No runtime plugin loading |
| [ADR-0010](0010-single-tenant-with-seams.md) | Single-tenant now, tenant-safe seams |
| [ADR-0011](0011-brand-neutral-driver-interface.md) | The driver interface is brand-neutral |
| [ADR-0012](0012-backend-emits-codes-not-prose.md) | The backend emits codes, never localized prose |
| [ADR-0013](0013-estimate-variance-policy.md) | Quoted price is binding within a tolerance band |
| [ADR-0014](0014-printer-credentials.md) | Printer credentials are encrypted at rest, never in git |
| [ADR-0015](0015-live-events-are-invalidation-not-state.md) | Live events invalidate the client's view; they do not carry it |
| [ADR-0016](0016-two-web-apps-no-desktop.md) | Two web apps, no desktop app |
| [ADR-0017](0017-jsonb-not-json.md) | JSONB is the JSON storage type |
| [ADR-0018](0018-time-partitioned-high-volume-tables.md) | High-volume tables are time-partitioned, with explicit retention |
| [ADR-0019](0019-backup-is-wal-archived-and-drilled.md) | Backup is WAL-archived, off-site, and drilled |
| [ADR-0020](0020-rate-snapshots-are-persisted.md) | Rate snapshots are persisted, not merely hashed |
| [ADR-0021](0021-tests-run-on-postgresql.md) | The test suite runs on PostgreSQL, with no SQLite fallback |
