"""Deriving the console's real screens, for the §1 gate next door.

Split out when `check_file_length` refused the combined file. The division is the
one the file already drew in comments: everything here answers *what the code
actually renders*, and `test_docs_screen_inventory.py` holds only the assertions
that compare that to what `docs/DESIGN-KIT.md` §1 claims.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

# `parents[2]` is the backend root, where the source-scanning gates stop. This one reads
# `docs/`, `design/` and `frontend/`, so it needs the repository root one level further.
ROOT = Path(__file__).resolve().parents[3]
DOC = ROOT / "docs" / "DESIGN-KIT.md"
KIT = ROOT / "design"

#: The realm column of §1's table, mapped to the bundle that would hold the screen. This
#: is what turns the document's own claim into a file to read, so a row filed under the
#: wrong realm is checked against the wrong app's union and says which one.
APPS: dict[str, Path] = {
    "public": ROOT / "frontend" / "apps" / "web" / "src" / "App.tsx",
    "control": ROOT / "frontend" / "apps" / "console" / "src" / "App.tsx",
}

#: The kit's contents page. §1 says so at the foot of the table; this is that sentence
#: made executable, so deleting it there without deleting it here is the smaller mistake.
CONTENTS_PAGE = "index.html"

#: Spelled-out numbers, because §1 spells out every figure except the parameter count.
#: The assertions compare against the *string* an author would have to type, so a failure
#: can print the wording rather than making them work it out from a number.
_UNITS = [
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
NUMBER_WORDS: dict[int, str] = {n + 1: word for n, word in enumerate(_UNITS)}
NUMBER_WORDS[20] = "twenty"
NUMBER_WORDS.update({20 + n: f"twenty-{_UNITS[n - 1]}" for n in range(1, 10)})
NUMBER_WORDS[30] = "thirty"


class Route(NamedTuple):
    """Where a kit screen ended up in an app, when the answer is not its own name."""

    #: The key in that app's `type Screen` union, or `None` for a screen with no route.
    key: str | None
    #: Whether that key also appears in the app's `routes: NavRoute[]` array.
    in_nav: bool
    why: str


#: Kit screens whose relationship to the apps is not "a route key spelled the same way,
#: reachable from the nav". Adding to this list is the point: it forces the choice to be
#: made and written down rather than made by omission.
SCREEN_ROUTES: dict[str, Route] = {
    "configurator": Route(
        "configure",
        True,
        "The same screen under a shorter name. The file is `configurator.html` and the "
        "path is still `/configurator`; only the union key was shortened.",
    ),
    "checkout": Route(
        "checkout",
        False,
        "In the `Screen` union but deliberately absent from `PATHS` and from the nav: "
        "App.tsx says it exists only after a configurator handoff, so a bookmark to it "
        "would open a page with no order behind it. `in_nav=False` is the assertion — "
        "checkout appearing in the masthead means that reasoning has been lost.",
    ),
    "blog": Route("journal", True, "Renamed: the kit's `blog.html` is the journal index."),
    "blog-post": Route(
        "journal",
        True,
        "The article is the same screen with a `report` slug set — `JournalPostPage` "
        "rather than `JournalPage` — so two kit files share one key. This is why the "
        "table can list twenty-one screens against a seven-key union.",
    ),
    "auth": Route(
        None,
        False,
        "Not a screen either app routes to: `AuthPanel` from `@printorian/ui`, drawn as "
        "the console's door and inside the storefront's cabinet and account. The one "
        "screen §1 calls built with no route key anywhere, which is why it needs an "
        "entry rather than looking like a missing one.",
    ),
}

#: Route keys that exist in an app and have no screen in the kit, keyed `realm.key` the
#: way `UNLINKED_IDS` keys `table.column`. These make the gate bite in the direction that
#: matters: a console screen added without a row in §1 lands here or fails.
NOT_IN_THE_KIT: dict[str, str] = {
    "control.prep": (
        "The slicing queue (ADR-0006): model out, finished plate back. Designed after "
        "the kit was drawn, so there is no `prep.html` to be its spec."
    ),
    "control.library": (
        "Curation of the storefront's model library. The kit drew the customer's side "
        "of this (`catalog.html`) and never the operator's."
    ),
    "control.journal": (
        "The editor behind the public journal, not the public journal. `blog.html` and "
        "`blog-post.html` are the reading screens and map to the storefront's "
        "`journal`; this is a different screen sharing the word, which is why these "
        "entries are keyed by realm."
    ),
}

#: Nav entries that are `href` links into the *other* bundle rather than screens. They
#: sit in a `NavRoute[]` like everything else, so the key scan sees them and would
#: otherwise report both as screens no union has heard of.
CROSS_REALM_LINKS = frozenset({"storefront", "console"})

_UNION = re.compile(r"type Screen\s*=\s*((?:\s*\|?\s*'[a-z-]+')+)")
_NAV_KEY = re.compile(r"^\s*key: '([a-z-]+)',", re.MULTILINE)
_STATE = re.compile(r"^\*\*(built|not built)\*\*")


class Row(NamedTuple):
    """One body row of §1's table, with the line it is on so a failure can name it."""

    line: int
    names: tuple[str, ...]
    realm: str
    state: str | None


def _word(count: int) -> str:
    assert count in NUMBER_WORDS, (
        f"{count} has no spelling in NUMBER_WORDS in {__file__}. Add it — the document "
        "spells its figures out, so the check has to know the word to look for."
    )
    return NUMBER_WORDS[count]


def _lines() -> list[tuple[int, str]]:
    """§1, as 1-based (lineno, text) pairs so a failure can point at a line of the doc."""
    text = DOC.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(text) if line.startswith("## 1. Where"))
    end = next(i for i, line in enumerate(text[start + 1 :], start + 1) if line.startswith("## "))
    return [(i + 1, line) for i, line in enumerate(text[start:end], start)]


def _flat(lines: list[tuple[int, str]]) -> str:
    """Collapse the hard wrapping, which splits "the four that do / not" mid-sentence."""
    return " ".join(" ".join(line for _, line in lines).split())


def _preamble() -> str:
    """Everything above §1, flattened. Line 3's twenty-one lives here, not in §1."""
    text = DOC.read_text(encoding="utf-8")
    return " ".join(text[: text.index("## 1. Where")].split())


def _rows(lines: list[tuple[int, str]]) -> list[Row]:
    """The `| Screen | Realm | State |` grid, header and `|---|` rule dropped."""
    parsed: list[Row] = []
    for lineno, line in lines:
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3:
            continue
        names = tuple(re.findall(r"`([a-z-]+)`", cells[0]))
        if not names:  # the header row and the `|---|---|---|` rule
            continue
        state = _STATE.match(cells[2])
        parsed.append(Row(lineno, names, cells[1], state.group(1) if state else None))
    return parsed


def _union(app: Path) -> set[str]:
    """The `type Screen = 'a' | 'b'` keys, whichever way the file wraps the union."""
    block = _UNION.search(app.read_text(encoding="utf-8"))
    assert block, (
        f"no `type Screen` union found in {app}. Either it was renamed or the regex in "
        f"{__file__} stopped matching, and a doc gate whose regex matches nothing is the "
        "exact failure this file exists to prevent."
    )
    return set(re.findall(r"'([a-z-]+)'", block.group(1)))


def _nav_keys(app: Path) -> set[str]:
    """Every `key: '...'` literal in the file's `NavRoute[]` arrays.

    Scanned over the whole file rather than inside `routes` alone, because the
    cross-realm links are a second `NavRoute[]` at module scope. TypeScript does not
    catch a stray key: `onNavigate` casts with `key as Screen`, so a nav entry naming a
    screen the union has never heard of compiles and renders a dead tile.
    """
    return set(_NAV_KEY.findall(app.read_text(encoding="utf-8")))


def _kit_screens() -> set[str]:
    return {path.stem for path in KIT.glob("*.html") if path.name != CONTENTS_PAGE}


def _expected_routes() -> dict[str, set[str]]:
    """Route keys each realm's app should hold, per §1's table read through the mapping."""
    expected: dict[str, set[str]] = {realm: set() for realm in APPS}
    for row in _rows(_lines()):
        if row.state != "built" or row.realm not in expected:
            continue
        for name in row.names:
            mapped = SCREEN_ROUTES.get(name, Route(name, True, ""))
            if mapped.key is not None:
                expected[row.realm].add(mapped.key)
    return expected
