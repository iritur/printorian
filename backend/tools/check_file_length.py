"""Fail when a source file grows past the limit.

V1's worst files were a 1,096-line pricing service, a 956-line inventory view model
and a 765-line file holding every service interface. Nobody decided to write those;
they accreted. This gate makes the accretion visible on the commit that causes it.

Usage::

    python tools/check_file_length.py [--limit 400] [paths...]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_LIMIT = 400

#: Generated or mechanical files that a length rule would only fight with.
EXEMPT_PARTS = frozenset({".venv", "__pycache__", "node_modules", "versions"})


def offenders(roots: list[Path], limit: int) -> list[tuple[Path, int]]:
    found: list[tuple[Path, int]] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if EXEMPT_PARTS & set(path.parts):
                continue
            lines = len(path.read_text(encoding="utf-8").splitlines())
            if lines > limit:
                found.append((path, lines))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("paths", nargs="*", default=["printorian", "tests", "tools"])
    args = parser.parse_args()

    roots = [Path(p) for p in args.paths if Path(p).exists()]
    found = offenders(roots, args.limit)

    if not found:
        print(f"file length OK (limit {args.limit})")
        return 0

    print(f"Files over {args.limit} lines — split them by responsibility:\n")
    for path, lines in sorted(found, key=lambda item: -item[1]):
        print(f"  {lines:>5}  {path}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
