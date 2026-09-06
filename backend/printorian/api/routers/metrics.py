"""The Prometheus scrape — three of INFRASTRUCTURE §5's ten domain metrics.

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

**What this serves, and what it does not.** Three: printers offline, telemetry
partition headroom, WAL archive failures — the three whose data *this process*
already measures. The other seven of §5 are not here: four need queries no route
runs yet, two need a machine-readable stamp neither `scripts/backup.sh` nor
`scripts/restore_drill.py` writes today, and the tenth is the money one above. Read
three series as three series, not as §5.

Unauthenticated, for the reason the three health probes are: a scraper has no
session. Like them it carries no farm data — ids, brand names and counters — but it
does describe the shape of the deployment, so **the storefront's edge must not
forward it** when Stage 3 is built. `deploy/storefront.Caddyfile`'s
`handle_path /api/*` forwards this exactly as it already forwards `/health/*`, and
that file is deliberately untouched here rather than edited blind: this branch has
no way to run `caddy validate`, and a config change nobody verified is worse than a
recorded gap.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse

from printorian.api.deps import AppClock
from printorian.contexts.fleet import brands_for, retention
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

    # The roster is what the *worker* published, never the `printers` table. An
    # empty report means nothing was observed, and it produces a family with no
    # samples — a farm nobody is collecting from must not read as a farm with no
    # printers offline (root CLAUDE.md §1).
    states: list[DriverHealth] = await request.app.state.driver_states.report()

    brands: dict[str, str] = {}
    months_ahead: int | None = None
    wal_failures: int | None = None
    try:
        async for session in request.app.state.database.session():
            brands = await brands_for(session, [state.printer_id for state in states])
            months_ahead = await retention.months_provisioned_ahead(session, now=clock.now())
            wal_failures = await archiver_failures(session)
    except Exception:
        # A scrape that fails entirely is a scrape that also stops reporting the
        # readings that *did* work — including the printer states, which come from
        # Redis and are unaffected by a database that is down. So this answers 200
        # with what is known, and the two database-backed series stay absent
        # rather than zero. `/health/ready` is the endpoint whose job is to say
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

    body, content_type = metrics.render(metrics.build_registry(offline, headroom, archive_failures))
    return Response(content=body, media_type=content_type)
