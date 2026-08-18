"""Fail when one context reaches into another context's internals.

``import-linter`` cannot express "a context may import another context's public
``__init__`` but nothing deeper" without wildcard self-import false positives, so
this gate covers it.

Allowed::

    from printorian.contexts.identity import Actor, Role

Forbidden::

    from printorian.contexts.identity.models import User
    from printorian.contexts.identity.service import IdentityService

This is the boundary V1 never drew: it had a plugin system around *screens* while
every plugin reached into one shared 45-entity DbContext, so the modularity cost
was paid and none of the isolation was bought.

Usage::

    python tools/check_context_isolation.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

CONTEXTS_ROOT = Path("printorian/contexts")
PREFIX = "printorian.contexts."


def _context_of(path: Path) -> str | None:
    """The context a file belongs to, e.g. ``identity``."""
    try:
        relative = path.relative_to(CONTEXTS_ROOT)
    except ValueError:
        return None
    return relative.parts[0] if len(relative.parts) > 1 else None


def _violations_in(path: Path, own_context: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []

    for node in ast.walk(tree):
        module: str | None = None
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                _check(alias.name, own_context, path, node.lineno, found)
            continue

        if module:
            _check(module, own_context, path, node.lineno, found)

    return found


def _check(module: str, own_context: str, path: Path, lineno: int, found: list[str]) -> None:
    if not module.startswith(PREFIX):
        return
    remainder = module.removeprefix(PREFIX).split(".")
    target = remainder[0]
    if target == own_context:
        return  # a context may import its own submodules freely
    if len(remainder) > 1:
        found.append(
            f"  {path}:{lineno}\n"
            f"      imports {module}\n"
            f"      -> import from 'printorian.contexts.{target}' instead"
        )


def main() -> int:
    if not CONTEXTS_ROOT.exists():
        print(f"no contexts directory at {CONTEXTS_ROOT}; nothing to check")
        return 0

    violations: list[str] = []
    for path in sorted(CONTEXTS_ROOT.rglob("*.py")):
        own = _context_of(path)
        if own is None:
            continue
        violations.extend(_violations_in(path, own))

    if not violations:
        print("context isolation OK")
        return 0

    print("Contexts may only import another context's public interface:\n")
    print("\n".join(violations))
    return 1


if __name__ == "__main__":
    sys.exit(main())
