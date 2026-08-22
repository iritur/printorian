"""The two statements :mod:`printorian.contexts.fleet.measures` reads through.

Split out for the reason `samples.py` was split out of `service.py` — the 400-line
gate doing its job rather than being an inconvenience — and the seam is a real one:
everything here is a string, with no session, no models and no absence rule. The
rule about what a missing row *means* lives entirely next door, where it can be
stated once and applied to both shapes.

**Both grains come out of one builder.** ``grain=total`` differs from ``grain=hour``
only in what the rows collapse onto; the aggregates are character-for-character the
same. That is what makes «Наработка за сутки» equal the sum of the 24 cells beside
it — the first thing anyone checks, and a discrepancy nobody could explain if the
total were a second query written separately.
"""

from __future__ import annotations

from enum import StrEnum

from printorian.contexts.fleet.history import STATE_COLUMNS
from printorian.contexts.fleet.rollups import TABLE


class Grain(StrEnum):
    """The two shapes the console draws, and deliberately not a third.

    A 168-cell hourly ruler, and a single windowed figure. **Not ``day``**: a daily
    bucket needs a timezone parameter to mean anything on a Moscow farm, which
    reopens exactly the UTC-versus-farm-local boundary `MetricRollup` closed on
    purpose — a bucket keyed on local time is keyed on a boundary that stops
    existing when `farm_timezone` changes. Presentation converts; storage does not,
    and neither does this.
    """

    HOUR = "hour"
    TOTAL = "total"


def _group_key(grain: Grain) -> str:
    """What the rows collapse onto.

    At ``total`` the key is a constant, so the whole window becomes one group and
    the single bucket is stamped at the window's start.
    """
    return "bucket_start" if grain is Grain.HOUR else "CAST(:since AS timestamptz)"


def _shared_measures() -> str:
    """The aggregates both routes emit, spelled once and generated from the enum.

    A ninth printer state is then a migration and a failing test rather than a
    column that quietly stops being summed.
    """
    columns = ("observed_seconds", *STATE_COLUMNS.values(), "state_changes", "error_sample_count")
    return ",\n    ".join(f"SUM({column}) AS {column}" for column in columns)


#: The temperature averages: present at ``hour``, absent at ``total``.
#:
#: The stored average is over the samples that *had* a reading, and the row carries
#: no count of those — only `sample_count`, which counts all of them — so there is
#: no correct weight, and an average of hourly averages is not the average. Absent
#: rather than approximated (ADR-0007). If phase 6 wants it, the honest fix is a
#: `nozzle_reading_count` column on the rollup, not arithmetic here.
#:
#: At ``hour`` each group is exactly one row, so ``MAX`` is that row's own value
#: rather than an aggregate over anything.
_AVERAGES: dict[Grain, str] = {
    Grain.HOUR: (
        "MAX(nozzle_temp_avg_c) AS nozzle_temp_avg_c,\n"
        "        MAX(bed_temp_avg_c) AS bed_temp_avg_c"
    ),
    Grain.TOTAL: "NULL::numeric AS nozzle_temp_avg_c,\n        NULL::numeric AS bed_temp_avg_c",
}


def fleet_statement(grain: Grain) -> str:
    """Every printer that reported, summed.

    `ix_metric_rollups_bucket_start` serves this; the primary key leads with
    `printer_id` and cannot. No temperatures and no `error_codes`: an average
    nozzle temperature across fifty machines is a number about nothing, and merging
    fifty jsonb maps per bucket is a lateral join per bucket for a farm-wide code
    histogram no panel in the kit draws.
    """
    return f"""
SELECT
    {_group_key(grain)} AS bucket_start,
    count(DISTINCT printer_id) AS printers_reporting,
    {_shared_measures()}
FROM {TABLE}
WHERE bucket_start >= CAST(:since AS timestamptz)
  AND bucket_start < CAST(:until AS timestamptz)
GROUP BY 1
ORDER BY 1
"""


def printer_statement(grain: Grain) -> str:
    """One machine, read on the primary key, which leads with `printer_id`.

    The codes are merged inside the same statement rather than fetched by a second
    one: at ``hour`` there is a single row per bucket and the merge is a
    pass-through, and at ``total`` it sums per key across at most 744 maps.
    ``COALESCE`` distinguishes the two things ``null`` must not be allowed to mean
    at once — an hour that was summarised and carried no codes is ``{{}}``; an hour
    that was never summarised has no row here at all and is filled in next door.
    """
    key = _group_key(grain)
    return f"""
WITH picked AS (
    SELECT * FROM {TABLE}
    WHERE printer_id = CAST(:printer_id AS uuid)
      AND bucket_start >= CAST(:since AS timestamptz)
      AND bucket_start < CAST(:until AS timestamptz)
),
totals AS (
    SELECT
        {key} AS bucket_start,
        {_shared_measures()},
        SUM(sample_count) AS sample_count,
        {_AVERAGES[grain]},
        MAX(nozzle_temp_max_c) AS nozzle_temp_max_c,
        MAX(bed_temp_max_c) AS bed_temp_max_c
    FROM picked
    GROUP BY 1
),
per_code AS (
    SELECT {key} AS bucket_start, entry.key AS code, SUM((entry.value)::int) AS occurrences
    FROM picked
    CROSS JOIN LATERAL jsonb_each(picked.error_codes) AS entry
    GROUP BY 1, 2
),
merged AS (
    SELECT bucket_start, jsonb_object_agg(code, occurrences) AS error_codes
    FROM per_code
    GROUP BY 1
)
SELECT totals.*, COALESCE(merged.error_codes, '{{}}'::jsonb) AS error_codes
FROM totals
LEFT JOIN merged USING (bucket_start)
ORDER BY 1
"""


__all__ = ["Grain", "fleet_statement", "printer_statement"]
