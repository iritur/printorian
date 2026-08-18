"""Turning one driver observation into one stored row.

Separate from the service because it is a pure mapping with no session, no clock
and no events — and because `service.py` is at the file-length gate, which is the
gate doing its job rather than an inconvenience.
"""

from __future__ import annotations

from decimal import Decimal

from printorian.contexts.fleet.models import TelemetrySample
from printorian.core.ids import EntityId
from printorian.drivers import Telemetry


def sample_of(printer_id: EntityId, telemetry: Telemetry) -> TelemetrySample:
    """One observation, as a row that will not be overwritten.

    A straight copy of what the driver reported — no derived fields, no defaults
    standing in for absent readings. A machine that did not report a bed
    temperature stores null, not zero: ADR-0007's rule against inventing data
    applies to the history as much as to the live view, and a column of zeroes
    would be indistinguishable from a genuinely cold bed once the readings are old
    enough that nobody remembers which it was.
    """
    return TelemetrySample(
        printer_id=printer_id,
        observed_at=telemetry.observed_at,
        state=telemetry.state,
        job_handle=telemetry.job_handle,
        progress_percent=telemetry.progress_percent,
        layer_current=telemetry.layer_current,
        layer_total=telemetry.layer_total,
        remaining_minutes=(
            Decimal(telemetry.remaining.minutes) if telemetry.remaining is not None else None
        ),
        nozzle_temp_c=telemetry.nozzle_temp_c,
        bed_temp_c=telemetry.bed_temp_c,
        error_code=telemetry.error_code,
    )


__all__ = ["sample_of"]
