"""Background processes: telemetry polling, scheduler ticks, notifications, rollups.

Workers run in their own process against the same contexts as the API. They are
added in the phase that needs them; Phase 0 defines only the package boundary.
"""
