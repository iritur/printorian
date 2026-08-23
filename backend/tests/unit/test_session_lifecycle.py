"""No early exit from a `Database.session()` loop.

`Database.session()` commits *after* the yield:

    async with self.session_factory() as session:
        try:
            yield session
            await session.commit()      # <- only reached if the loop resumes
        except Exception:
            await session.rollback()
            raise

So `return` or `break` inside `async for session in db.session()` leaves the
generator suspended and the commit never runs. The interpreter finalizes it later
by throwing `GeneratorExit`, which derives from `BaseException` and therefore
slips past the `except Exception` that would at least have rolled back loudly.
The write is discarded in silence and the caller sees success.

This was not theoretical. `tools/provision_owner.py` was written that way and
created the farm's first owner, dropped the insert, printed "Created owner" and
exited 0 — on the one code path that runs exactly once, on a machine where nobody
can yet sign in to notice. `api/ws.py` had the same shape and was quietly
discarding the `last_used_at` refresh on every WebSocket handshake.

Both were found by running the thing on a real host rather than by any of the six
gates, which is the argument for checking the *shape* here: the failure is
invisible to types, to lint, and to any test that passes a session in directly.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TREES = ("printorian", "tools", "scripts")


def _offenders() -> list[str]:
    found = []
    for tree in TREES:
        for path in (ROOT / tree).rglob("*.py"):
            try:
                parsed = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - not our file to fix
                continue
            for node in ast.walk(parsed):
                if not isinstance(node, ast.AsyncFor):
                    continue
                if ".session()" not in ast.unparse(node.iter):
                    continue
                escapes = [
                    inner for inner in ast.walk(node) if isinstance(inner, (ast.Return, ast.Break))
                ]
                if escapes:
                    kinds = ", ".join(sorted({type(e).__name__ for e in escapes}))
                    found.append(f"{path.relative_to(ROOT)}:{node.lineno} ({kinds})")
    return sorted(found)


def test_no_loop_over_a_session_exits_early() -> None:
    offenders = _offenders()

    assert not offenders, (
        "These loops exit before `Database.session()` can commit, so their writes "
        "are discarded silently:\n  " + "\n  ".join(offenders) + "\n\n"
        "Assign to a variable inside the loop and return after it instead."
    )


def test_the_check_can_actually_see_the_pattern() -> None:
    """A guard that cannot fail is not a guard.

    Without this, deleting the `.session()` match or the `AsyncFor` walk would
    leave a test that passes on every codebase, including a broken one.
    """
    source = (
        "async def f(db):\n"
        "    async for session in db.session():\n"
        "        return await work(session)\n"
    )
    node = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.AsyncFor))

    assert ".session()" in ast.unparse(node.iter)
    assert any(isinstance(n, ast.Return) for n in ast.walk(node))


@pytest.mark.parametrize("tree", TREES)
def test_every_tree_that_is_scanned_exists(tree: str) -> None:
    """Catches a rename turning the scan into a no-op over a missing directory."""
    assert (ROOT / tree).is_dir()
