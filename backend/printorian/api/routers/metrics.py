"""The Prometheus scrape — eleven of INFRASTRUCTURE §5's twelve domain metrics.

Composition only. The mechanics are `core.metrics` (a registry per scrape, a text
writer, and the rule that a reading nobody took is an *absent* series), and each
reading lives beside the code that owns the fact. This module awaits them and hands
the families over, because nothing async may run inside a collector's `collect`.

**The collision, first, because it is the thing to get wrong here.**
`GET /fleet/metrics` and `GET /fleet/metrics/{printer_id}` already exist and are
something else entirely: the farm's *measured occupancy in seconds*, behind
`VIEW_PRODUCTION`, for a screen. This is the instrumentation scrape a monitoring
agent reads. The paths do not conflict — `fleet.py` carries `prefix="/fleet"` — and
neither is a better version of the other. ARCHITECTURE §10 keeps the same warning.

**No money on this surface, ever.** It is unauthenticated, so
`printorian_sla_credit_accrued_rub` — §5's tenth series — is deliberately not here
and does not arrive until the scrape has an identity (a token in Settings, or
mTLS). That is a decision, not an omission: `VIEW_FINANCIALS` is kept apart from
every production permission precisely so a response carrying seconds cannot quietly
start carrying rubles, and an endpoint with no caller identity at all inherits the
argument in its strongest form. `tests/api/test_metrics_api.py` forbids any metric
name containing `sla_credit` or ending `_rub`, as a rule over parsed names rather
than a substring check, so it goes on holding when a fourth series is added.

**What this serves, and what it does not.** Three readings this process takes
itself — printers offline, telemetry partition headroom and WAL archive
failures — and the rest from the database: the oldest job wedged in ASSIGNED or
DISPATCHING, the variance band's mean with its sample count beside it, the prep
queue's depth, payments the gateway reported paid that are not settled, payments
held because they moved no order, and the last success of the backup, of the
restore drill and of the off-site copy, each of which leaves a row in
`maintenance_runs` when it succeeds. Each is read where the fact is owned
(`production.vitals`, `payments.reconciliation`, `payments.holding`,
`settings.maintenance_runs`) and only composed here. Not here: the money series
above.

Unauthenticated, for the reason the three health probes are: a scraper has no
session. Like them it carries no farm data — ids, brand names, counts, ages and
a dimensionless ratio — but it does describe the shape of the deployment, so the
storefront's edge answers it 404 (`deploy/storefront.Caddyfile`, checked with
`caddy adapt`, security review F20).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse

from printorian.api.deps import AppClock
from printorian.contexts.fleet import brands_for, retention
from printorian.contexts.payments import held_payment_count, unreconciled_payments
from printorian.contexts.production import (
    STUCK_WATCHED,
    VarianceReading,
    awaiting_prep,
    stuck_seconds,
    variance_ratio,
)
from printorian.contexts.settings import RunKind, last_successes
from printorian.core import metrics
from printorian.core.db import archiver_failures
from printorian.core.driver_health import CONNECTED, UNAVAILABLE, DriverHealth

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["metrics"])


def _offline_reading(state: str) -> float | None:
    """`1` observed unreachable, `0` observed connected, nothing otherwise.

    The most consequential application of ADR-0007 in this file, and both wrong
    answers are inventions with a cost. `UNKNOWN` exported as `1` pages somebody at
    three in the morning about a printer nobody has looked at; exported as `0` it
    hides a machine that has been dead all day. The honest exposition of "the
    worker named this printer and then stopped publishing readings for it" is no
    sample at all, so the graph shows a gap and the alert stays quiet.
    """
    if state == UNAVAILABLE:
        return 1.0
    if state == CONNECTED:
        return 0.0
    return None


@router.get("/metrics", response_class=PlainTextResponse)
async def scrape(request: Request, clock: AppClock) -> Response:
    """One scrape: measure what can be measured, publish only that."""
    offline = metrics.gauge(
        metrics.PRINTERS_OFFLINE,
        "1 when the worker observed a printer it could not connect to (ADR-0007).",
        "printer",
        "brand",
    )
    headroom = metrics.gauge(
        metrics.TELEMETRY_PARTITION_MONTHS_AHEAD,
        "Whole months past this one that already have a telemetry partition.",
    )
    archive_failures = metrics.counter(
        metrics.WAL_ARCHIVE_FAILURES,
        "pg_stat_archiver.failed_count, since the statistics were last reset.",
    )
    stuck = metrics.gauge(
        metrics.JOB_STUCK_SECONDS,
        "Seconds the oldest job has sat in this state; 0 when no job is in it.",
        "state",
    )
    variance = metrics.gauge(
        metrics.ESTIMATE_VARIANCE_RATIO,
        "Mean prepared/quoted cost over the variances of the last 30 days (ADR-0013).",
    )
    variance_samples = metrics.gauge(
        metrics.ESTIMATE_VARIANCE_SAMPLES,
        "Variances the ratio is the mean of. No ratio is published when this is 0.",
    )
    awaiting = metrics.gauge(
        metrics.ORDERS_AWAITING_PREP,
        "Jobs waiting for an engineer to slice them (ADR-0006).",
    )
    unreconciled = metrics.gauge(
        metrics.PAYMENT_NOTIFICATIONS_UNRECONCILED,
        "Payments the gateway reported succeeded that are not settled here.",
    )
    held = metrics.gauge(
        metrics.PAYMENTS_HELD,
        "Settled payments that moved no order, waiting for a person to refund or keep.",
    )
    backup_stamp = metrics.gauge(
        metrics.BACKUP_LAST_SUCCESS,
        "Unix time of the newest successful backup. Absent if none was ever recorded.",
    )
    drill_stamp = metrics.gauge(
        metrics.RESTORE_DRILL_LAST_SUCCESS,
        "Unix time of the newest successful restore drill. Absent if none was recorded.",
    )
    offsite_stamp = metrics.gauge(
        metrics.OFFSITE_LAST_SUCCESS,
        "Unix time of the newest completed off-site copy. Absent if none was recorded.",
    )

    # The roster is what the *worker* published, never the `printers` table. An
    # empty report means nothing was observed, and it produces a family with no
    # samples — a farm nobody is collecting from must not read as a farm with no
    # printers offline (root CLAUDE.md §1).
    states: list[DriverHealth] = await request.app.state.driver_states.report()

    brands: dict[str, str] = {}
    months_ahead: int | None = None
    wal_failures: int | None = None
    ages: dict[str, float] = {}
    variances: VarianceReading | None = None
    prep_depth: int | None = None
    unsettled: int | None = None
    held_count: int | None = None
    stamps: dict[RunKind, float] = {}
    try:
        async for session in request.app.state.database.session():
            brands = await brands_for(session, [state.printer_id for state in states])
            months_ahead = await retention.months_provisioned_ahead(session, now=clock.now())
            wal_failures = await archiver_failures(session)
            ages = {state.value: age for state, age in (await stuck_seconds(session)).items()}
            variances = await variance_ratio(session)
            prep_depth = await awaiting_prep(session)
            unsettled = await unreconciled_payments(session)
            held_count = await held_payment_count(session)
            stamps = {kind: at.timestamp() for kind, at in (await last_successes(session)).items()}
    except Exception:
        # A scrape that fails entirely is a scrape that also stops reporting the
        # readings that *did* work — including the printer states, which come from
        # Redis and are unaffected by a database that is down. So this answers 200
        # with what is known, and the two database-backed series stay absent
        # rather than zero — all of them, so a half-read database cannot publish
        # some series and silently omit others. `/health/ready` is the endpoint whose job is to say
        # the database is unreachable; saying it twice, badly, helps nobody.
        logger.warning("metrics_database_readings_unavailable", exc_info=True)

    for state in states:
        observation = _offline_reading(state.state)
        # An id with no printer row keeps its reading and loses only its label:
        # the measurement is real, and it is the decoration that is unknown.
        # Dropping the sample instead would delete an observed failure over a
        # missing brand name.
        metrics.observe(
            offline,
            observation,
            printer=state.printer_id,
            brand=brands.get(state.printer_id, ""),
        )
    metrics.observe(headroom, months_ahead)
    metrics.observe(archive_failures, wal_failures)
    # A state with no job in it is a measured 0 — the query ran and found nothing
    # wedged. A database that could not be read publishes no state at all.
    for job_state in STUCK_WATCHED:
        metrics.observe(stuck, ages.get(job_state.value), state=job_state.value)
    metrics.observe(awaiting, prep_depth)
    metrics.observe(unreconciled, unsettled)
    metrics.observe(held, held_count)
    # A job that never succeeded has no row and so no sample: "never" is not a
    # timestamp, and the alert has to say `absent()` as well as "too old".
    metrics.observe(backup_stamp, stamps.get(RunKind.BACKUP))
    metrics.observe(drill_stamp, stamps.get(RunKind.RESTORE_DRILL))
    metrics.observe(offsite_stamp, stamps.get(RunKind.OFFSITE))
    if variances is not None:
        # The mean of no variances is not 1.0 and not 0: no sample, with the
        # count of 0 beside it saying why (ADR-0007).
        metrics.observe(variance, variances.ratio)
        metrics.observe(variance_samples, variances.samples)

    body, content_type = metrics.render(
        metrics.build_registry(
            offline,
            headroom,
            archive_failures,
            stuck,
            variance,
            variance_samples,
            awaiting,
            unreconciled,
            held,
            backup_stamp,
            drill_stamp,
            offsite_stamp,
        )
    )
    return Response(content=body, media_type=content_type)
