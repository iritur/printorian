"""``python -m printorian.workers``."""

from __future__ import annotations

import asyncio
import contextlib

from printorian.workers.runner import main

if __name__ == "__main__":
    # Ctrl+C on Windows arrives as KeyboardInterrupt rather than a signal handler;
    # `asyncio.run` cancels `main`, which runs the same shutdown path.
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
