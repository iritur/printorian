"""Application configuration.

Everything tunable is here, injected, and never a module-level constant in a
feature module (ADR-0010: single-tenant now, tenant-safe seams). No farm rate,
threshold or interval may be hardcoded inside a context.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Root settings, populated from environment variables and ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PRINTORIAN_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    environment: Environment = Environment.LOCAL
    debug: bool = False

    # -- infrastructure --------------------------------------------------
    database_url: str = "postgresql+asyncpg://printorian:printorian@localhost:5433/printorian"
    redis_url: str = "redis://localhost:6380/0"
    secret_key: SecretStr = SecretStr("dev-only-not-a-real-secret")

    # -- connection pool ---------------------------------------------------
    #: Persistent connections per process. The API serves requests and the workers
    #: sweep from the same pool, and the telemetry poller alone wants eight at once.
    db_pool_size: int = Field(default=10, ge=1)
    #: Burst capacity above `db_pool_size`, discarded when returned.
    db_max_overflow: int = Field(default=20, ge=0)
    #: How long a caller waits for a connection before giving up. Failing here is
    #: better than queueing behind a pool that is not going to free up.
    db_pool_timeout_seconds: int = Field(default=30, ge=1)
    #: Recycle ahead of the network quietly dropping a long-idle connection.
    db_pool_recycle_seconds: int = Field(default=1800, ge=60)

    # -- server-side guards (PostgreSQL only) ------------------------------
    #: Ceiling on any single statement. Generous enough for a report, short enough
    #: that a runaway query releases its connection instead of holding it all day.
    db_statement_timeout_ms: int = Field(default=30_000, ge=1000)
    #: How long a statement waits for a lock. Kept well under the statement timeout
    #: so a migration blocked behind a transaction fails fast and names the problem.
    db_lock_timeout_ms: int = Field(default=10_000, ge=100)
    #: Kills transactions left open with no work in flight — the failure mode that
    #: blocks VACUUM and lets table bloat grow unbounded.
    db_idle_in_transaction_timeout_ms: int = Field(default=60_000, ge=1000)

    # -- blocking work -----------------------------------------------------
    #: How many mesh analyses may occupy a worker thread at once (`core.cpu`).
    #:
    #: Analysis is seconds of NumPy on a large model, so it runs off the event
    #: loop or the whole process stalls. Threads are not free either — each holds
    #: the mesh and its intermediate arrays — so the pool is bounded rather than
    #: unbounded, and past the limit callers queue. Four is a small multiple of
    #: the cores a farm server has to spare while it is also serving requests.
    cpu_workers: int = Field(default=4, ge=1)

    # -- rate limits -------------------------------------------------------
    #: Quotes and previews per minute, per client address.
    #:
    #: `POST /pricing/quote` takes an *optional* actor, so it is reachable without
    #: signing in, and each call costs a mesh analysis. Without a ceiling here a
    #: laptop can saturate the farm's API from the public internet. Generous
    #: enough that a person configuring a model never notices: the configurator
    #: re-quotes on option changes, not on every keystroke.
    quote_rate_per_minute: int = Field(default=30, ge=1)
    #: Uploads per minute, per client address, on the endpoints that store bytes.
    upload_rate_per_minute: int = Field(default=10, ge=1)
    #: Failed sign-ins tolerated per account and per address before the pair is
    #: locked out. `SignInFailed` has always called itself "the raw material for
    #: lockout"; this is the lockout.
    signin_max_attempts: int = Field(default=10, ge=1)
    #: How long a locked-out account/address pair stays locked.
    signin_lockout_minutes: int = Field(default=15, ge=1)
    #: Calls per minute, per address, to the authentication endpoints. The lockout
    #: above counts *failures* against one account; this bounds the traffic itself,
    #: including registration — Argon2 is expensive on purpose, so an unthrottled
    #: sign-in endpoint is a way to spend the API's CPU as well as to guess.
    auth_rate_per_minute: int = Field(default=20, ge=1)

    # -- live events -------------------------------------------------------
    #: Redis channel the event relay fans out on (`core.relay`).
    #:
    #: The API and the workers are separate processes (`deploy/compose.prod.yml`),
    #: and the event bus is in-process — so without a relay every event raised by
    #: a sweep rather than by a request dies in the worker, and the console's
    #: "live" boards are live only for what a person clicked. This is that relay.
    events_channel: str = "printorian.events"
    #: Turn the relay off for a single-process deployment or a test that wants no
    #: Redis. Local events still reach local WebSocket clients either way.
    events_relay_enabled: bool = True

    # -- farm ------------------------------------------------------------
    farm_timezone: str = "Europe/Moscow"
    farm_open_hour: int = Field(default=9, ge=0, le=23)
    farm_close_hour: int = Field(default=21, ge=1, le=24)

    # -- scheduling ------------------------------------------------------
    scheduler_tick_seconds: int = Field(default=30, ge=1)
    telemetry_poll_seconds: int = Field(default=5, ge=1)
    #: How often lateness credits are recomputed. The credit is a smooth function
    #: of time, so this sets how stale the *displayed* figure may be, not whether
    #: it is right: at the standard 5%/day a five-minute gap is worth ~0.017% of
    #: the order. Frequent enough that a customer refreshing a late order sees it
    #: move, cheap enough to ignore.
    sla_sweep_seconds: int = Field(default=300, ge=1)
    #: How often finished prints become tasks on the post-production board, and
    #: drying timers run out. Sixty seconds because the first of those is what an
    #: operator is standing there waiting for — a part off the bed that takes five
    #: minutes to appear on the board is five minutes of somebody watching a
    #: screen. The pass reconciles, so it is cheap to run often.
    postproduction_sweep_seconds: int = Field(default=60, ge=1)
    packaging_sweep_seconds: int = Field(default=60, ge=1)

    # -- pricing guardrails ----------------------------------------------
    #: Fraction by which a sliced plate may exceed the quoted cost before the job
    #: is held for review instead of dispatched (ARCHITECTURE §5).
    price_variance_tolerance: Decimal = Decimal("0.15")

    # -- sessions --------------------------------------------------------
    session_ttl_hours: int = Field(default=12, ge=1)
    #: How long an expired session row is kept before the reaper deletes it. Long
    #: enough that "your session ended" is still distinguishable from "that token
    #: was never real" while anyone is still asking.
    session_retention_days: int = Field(default=7, ge=0)

    # -- object storage ----------------------------------------------------
    #: Where model meshes and prepared plates live on disk. On the farm this points
    #: at the NAS or the data disk; it must not be inside the container's writable
    #: layer, or a rebuild takes every model with it.
    storage_root: str = "./var/storage"
    #: Largest upload accepted, in bytes. A mesh above this is refused before it is
    #: read into memory rather than after.
    max_upload_bytes: int = Field(default=200 * 1024 * 1024, ge=1024)
    #: How much uploaded geometry one customer may keep, shown on «Мои модели».
    #:
    #: Not enforced at upload — a customer who has hit it should be told which of
    #: their files to remove, not have a quote refused mid-configuration — so this
    #: is the figure the account screen measures against and nothing else. Making
    #: it a rule is a change to the configurator, not to this line.
    customer_storage_quota_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=1024)
    #: How long an uploaded model is kept after the last time anything used it.
    #: Counted from `last_used_at`, not from upload, so a model reprinted every
    #: month is never collected while an experiment from last year is.
    #:
    #: Zero disables collection. Models still referenced by an order line are
    #: protected by a `RESTRICT` foreign key whatever this says.
    model_retention_days: int = Field(default=180, ge=0)

    # -- housekeeping ------------------------------------------------------
    #: How often partitions are provisioned, retention applied and sessions reaped.
    #: Hourly: none of it is urgent, all of it is unbounded if skipped.
    maintenance_sweep_seconds: int = Field(default=3600, ge=60)
    #: How far ahead telemetry partitions are created. Two months means the job can
    #: fail silently for weeks before an insert has nowhere to go.
    telemetry_partition_months_ahead: int = Field(default=2, ge=1)
    #: How long a worker loop may go without recording a pass before it counts as
    #: wedged (`contexts.journal` is not involved — see `core.heartbeat`).
    #:
    #: Compared against the *slowest* loop's own interval, not against a single
    #: number: the maintenance sweep runs hourly and the telemetry poller every
    #: five seconds, so one threshold that suits both does not exist. This is the
    #: multiple of a loop's own interval it may miss before it is called stale.
    worker_stale_intervals: int = Field(default=4, ge=2)
    #: How long raw telemetry is kept, in days. **Zero disables dropping entirely**,
    #: which is the default on purpose: partitions are dropped whole and
    #: irreversibly, and until rollups exist (Slice G) the raw samples are the only
    #: copy of what the farm measured. Turn this on in the same change that starts
    #: summarising them, not before.
    telemetry_retention_days: int = Field(default=0, ge=0)

    # -- cross-origin callers ---------------------------------------------
    #: Origins allowed to call the API from a browser context, comma-separated.
    #:
    #: Empty by default, which means same-origin only: the storefront sits behind
    #: the same reverse proxy as the API (ADR-0003) and needs nothing here. The
    #: desktop console does — it is never same-origin — so a deployment that runs
    #: one lists its origin explicitly rather than the API opening itself to
    #: everything.
    #:
    #: Credentials are deliberately *not* allowed across origins: the console
    #: authenticates with a bearer token, so the session cookie never needs to
    #: travel cross-site, and a stolen origin cannot ride on someone's cookie.
    cors_origins: str = ""

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    # -- payments --------------------------------------------------------
    #: Which gateway collects money. "mock" is refused in production.
    payment_provider: str = "mock"
    #: YooKassa merchant credentials. Secrets, so they arrive from the environment
    #: and are never written to the database or a committed file (ADR-0014).
    yookassa_shop_id: str = ""
    yookassa_secret_key: SecretStr = SecretStr("")
    #: Where the gateway returns the customer after payment.
    payment_return_url: str = "http://localhost:5173/orders"

    #: Where the storefront lives, for links that leave the building.
    #:
    #: The RSS feed needs absolute article URLs, and only the deployment knows
    #: what they are. Deriving them from the request looks tempting and is wrong
    #: exactly when it matters: a reverse proxy rewrites the Host, so the API sees
    #: itself rather than the address a reader would type — and the feed then
    #: hands every subscriber a link into the JSON API.
    #:
    #: Defaulted to the dev storefront for the same reason `payment_return_url`
    #: is: a developer should not have to configure anything to see it work, and
    #: a real deployment already sets both.
    site_url: str = "http://localhost:5173"

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @property
    def is_test(self) -> bool:
        return self.environment is Environment.TEST


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton. Call ``get_settings.cache_clear()`` in tests."""
    return Settings()
