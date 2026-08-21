"""``python -m printorian.workers`` — and ``--check``, its healthcheck.

The container had no healthcheck at all, and `deploy/compose.prod.yml` explained
why: it serves no HTTP, and a process check "passes for a worker deadlocked
mid-sweep". That objection is about *process* checks. `--check` is not one: it
reads the beats each loop records at the end of every pass, so it fails for a
worker that is running and not working — which is the case that used to be
invisible.

Exit codes are the interface, because that is what a container healthcheck reads:
``0`` every loop is beating, ``1`` at least one is not.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys

from printorian.core.config import get_settings
from printorian.core.heartbeat import Heartbeat
from printorian.workers.runner import main


async def _check() -> int:
    """Report each loop's state, and fail if any of them is not beating."""
    settings = get_settings()
    heartbeat = Heartbeat(settings.redis_url)
    await heartbeat.start()
    try:
        report = await heartbeat.report()
    finally:
        await heartbeat.aclose()

    for loop in report:
        print(f"{loop.loop}: {loop.state}{f' ({loop.last_beat})' if loop.last_beat else ''}")
    return 0 if all(loop.is_healthy for loop in report) else 1


if __name__ == "__main__":
    if "--check" in sys.argv[1:]:
        raise SystemExit(asyncio.run(_check()))
    # Ctrl+C on Windows arrives as KeyboardInterrupt rather than a signal handler;
    # `asyncio.run` cancels `main`, which runs the same shutdown path.
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
