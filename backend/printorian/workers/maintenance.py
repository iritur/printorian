"""Housekeeping the database cannot do for itself.

Three jobs that all have the same shape — cheap, idempotent, and catastrophic to
forget:

* **Provision telemetry partitions** ahead of the data. PostgreSQL will not create
  next month's partition on its own, and a sample with nowhere to go is a failed
  insert. Running this hourly means it is a no-op all but once a month, which is
  the correct cost for a job whose failure mode is "the farm stops recording what
  its printers are doing".
* **Drop telemetry past retention**, a whole partition at a time.
* **Reap expired sessions**, which nothing has ever deleted.
* **Collect unused models**, so a year of quotes nobody ordered does not fill
  the farm's disk. What is still needed is protected by a foreign key, not by
  this job's judgement.

Run on a long interval by design: none of it is urgent, all of it is unbounded if
skipped, and none of it should compete with the scheduler for a connection.

Split from the SLA sweep rather than folded into it because the two fail
differently and should be readable separately in the logs — a farm that stops
accruing late credits has a billing problem, one that stops provisioning
partitions has an outage coming on the first of the month.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import timedelta

import structlog

from printorian.contexts.catalog import ModelLibrary
from printorian.contexts.fleet import retention
from printorian.contexts.identity import IdentityService
from printorian.core.clock import Clock
from printorian.core.config import Settings
from printorian.core.errors import PrintorianError

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class MaintenanceOutcome:
    """What one pass did, for logging and the health endpoint."""

    partitions_created: tuple[str, ...] = ()
    partitions_dropped: tuple[str, ...] = ()
    sessions_purged: int = 0
    models_collected: int = 0
    #: Telemetry rows that landed in the default partition. Always zero when
    #: provisioning is healthy; anything else is worth an alert, not a log line.
    unroutable_samples: int = 0

    @property
    def is_noteworthy(self) -> bool:
        """Whether this pass did anything a person would want to read about."""
        return bool(
            self.partitions_created
            or self.partitions_dropped
            or self.sessions_purged
            or self.models_collected
            or self.unroutable_samples
        )


class MaintenanceSweep:
    """One housekeeping pass, over its own committed session."""

    def __init__(
        self,
        identity: IdentityService,
        models: ModelLibrary | None,
        session: object,
        clock: Clock,
        settings: Settings,
    ) -> None:
        self._identity = identity
        # Optional: a deployment with no object store configured still wants its
        # partitions provisioned and its sessions reaped.
        self._models = models
        self._db = session
        self._clock = clock
        self._settings = settings

    async def sweep(self) -> MaintenanceOutcome:
        now = self._clock.now()

        created = await retention.ensure_partitions(
            self._db,  # type: ignore[arg-type]
            now=now,
            months_ahead=self._settings.telemetry_partition_months_ahead,
        )

        dropped: tuple[str, ...] = ()
        if self._settings.telemetry_retention_days > 0:
            dropped = await retention.drop_partitions_before(
                self._db,  # type: ignore[arg-type]
                cutoff=now - timedelta(days=self._settings.telemetry_retention_days),
            )

        purged = await self._identity.purge_expired_sessions(
            grace=timedelta(days=self._settings.session_retention_days)
        )
        collected = 0
        if self._models is not None and self._settings.model_retention_days > 0:
            collected = await self._models.purge_unused(
                older_than=timedelta(days=self._settings.model_retention_days)
            )

        unroutable = await retention.unroutable_sample_count(self._db)  # type: ignore[arg-type]

        if unroutable:
            # Not an exception: telemetry is still being recorded, just into a
            # partition that retention cannot drop and queries cannot prune. That
            # is a degradation to fix today, not a reason to stop the worker.
            logger.error("telemetry_default_partition_not_empty", rows=unroutable)

        return MaintenanceOutcome(
            partitions_created=created,
            partitions_dropped=dropped,
            sessions_purged=purged,
            models_collected=collected,
            unroutable_samples=unroutable,
        )


async def run_forever(
    build_sweep: object,
    *,
    interval_seconds: int,
    stop: asyncio.Event | None = None,
) -> None:
    """Sweep on an interval until stopped.

    Same contract as the SLA loop: a fresh sweep — and so a fresh session and a
    fresh commit — per pass, because one session held across days of housekeeping
    would keep a transaction open for the life of the process.
    """
    stop = stop or asyncio.Event()

    while not stop.is_set():
        try:
            sweep = await build_sweep()  # type: ignore[operator]
            outcome = await sweep.sweep()
            if outcome.is_noteworthy:
                logger.info(
                    "maintenance_sweep",
                    partitions_created=list(outcome.partitions_created),
                    partitions_dropped=list(outcome.partitions_dropped),
                    sessions_purged=outcome.sessions_purged,
                    models_collected=outcome.models_collected,
                    unroutable_samples=outcome.unroutable_samples,
                )
        except (PrintorianError, Exception):
            # A failed pass is logged and the loop continues. Housekeeping that
            # gave up after one bad night would leave partitions unprovisioned and
            # nobody would find out until inserts started failing.
            logger.exception("maintenance_sweep_failed")

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)


__all__ = ["MaintenanceOutcome", "MaintenanceSweep", "run_forever"]
