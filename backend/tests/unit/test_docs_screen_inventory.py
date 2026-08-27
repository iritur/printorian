"""`docs/DESIGN-KIT.md` §1 counts screens; this fails when the apps stop agreeing.

§1 is a table of twenty-one screens and three spelled-out numbers about them, and it is
the first thing a session reads when deciding whether a screen needs building. It has
already been wrong in the direction that costs the most: §1 and §2.1 called the settings
screen **not built** while `SettingsPage.tsx` was already serving 102 parameters across
fourteen sections. A reader believing the document would have built it twice.

That was not one bad edit. Three predecessor documents — `DESIGN-KIT-PLAN.md`,
`DESIGN-KIT-INTEGRATION.md`, `DESIGN-KIT-BACKEND-GAPS.md` — answered the same question
from three angles and disagreed with each other and with the code, which is why they
were merged into this one and deleted. `DATABASE-REVIEW.md` §1 failed the same way,
stale in every figure it carried: 22 tables across seven contexts built by nine
migrations, while the ORM held 42 across twelve and `versions/` held twenty, with five
whole contexts missing from its table. Correcting a number by hand fixes it for one day.

So the figures are derived here instead, from three sources that cannot drift from the
product: the kit's own `design/*.html`, and the `type Screen` union in each app's
`App.tsx`. The converse direction is the half that makes this a drift gate rather than a
proofreader — a twelfth console screen with no row in §1 fails
`test_every_route_is_a_kit_screen_or_a_stated_exception`, so the table cannot fall
behind the app in silence.

**Scope: counts and inventories only. Prose is out of scope by design.** Column 3 is
parsed exactly as far as its leading `**built**` / `**not built**` token; the sentence
after the em dash is an author's, and a gate over it would either be unfalsifiable or
fight every rewording. §2.2–§2.5 are untouched for the same reason: they are preserved
kit inventories, not claims about what exists.

§1's "102 parameters across fourteen sections" is gated here too, rather than left to
the sibling guarding §2.1's copy of it. It sits in the lines this gate owns and it is
the exact claim that was wrong before; a section half-guarded is one nobody can trust at
a glance. `ROOT`, the section slicer and `NUMBER_WORDS` are likewise duplicated across
the three doc-drift gates rather than shared — they were written in parallel and a
common module would have been a merge conflict in each. Extract
`tests/unit/_docs_support.py`, the naming this suite already uses, if a fourth arrives.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import pytest

from printorian.contexts.settings.sections import FIELDS, SECTIONS

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
_UNITS = (
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
).split()
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


def test_the_table_lists_exactly_the_kits_screens() -> None:
    """§1's table is the kit's contents page in prose; this keeps it one.

    Catches a screen added to `design/` and never given a row — the quiet half of the
    drift, because a table that is merely *incomplete* still reads as authoritative. It
    equally catches §1 listing a screen whose HTML is gone, which is how the three
    predecessor documents each ended up describing a slightly different kit.
    """
    on_disk = _kit_screens()
    # Two empty sets compare equal, so a kit moved elsewhere would leave the comparison
    # below passing over nothing at all.
    assert len(on_disk) > 1, f"{KIT} holds no screens besides `{CONTENTS_PAGE}`"

    rows = _rows(_lines())
    unparsed = [f"line {row.line}: {row.names}" for row in rows if row.state is None]
    assert not unparsed, (
        "these rows do not begin with `**built**` or `**not built**`, so their state was "
        "not read at all:\n  " + "\n  ".join(unparsed) + f"\n\nin {DOC}"
    )

    listed = [name for row in rows for name in row.names]
    twice = sorted({name for name in listed if listed.count(name) > 1})
    assert not twice, f"{DOC} §1 names these screens more than once, so its counts are off: {twice}"

    missing = sorted(on_disk - set(listed))
    invented = sorted(set(listed) - on_disk)
    assert not missing and not invented, (
        f"{DOC} §1's table no longer matches `design/`.\n"
        f"  in design/ but not in the table: {missing}\n"
        f"  in the table but not in design/: {invented}\n"
        "Add or remove the rows, and re-count the figures above the table with them."
    )


def test_every_screen_called_built_has_a_route() -> None:
    """"Built" has to mean an app renders it, not that somebody drew it.

    This is the check that was failing, unwritten, while §1 called `settings` unbuilt —
    in the other direction, and that is the direction that cost the time:
    `SettingsPage.tsx` was in the console's union and serving 102 parameters, and the
    document said no.
    """
    unions = {realm: _union(app) for realm, app in APPS.items()}
    offenders = []
    for row in _rows(_lines()):
        if row.state != "built":
            continue
        for name in row.names:
            mapped = SCREEN_ROUTES.get(name, Route(name, True, ""))
            if mapped.key is None or mapped.key in unions.get(row.realm, set()):
                continue
            offenders.append(
                f"line {row.line}: `{name}` ({row.realm}) -> expected route "
                f"'{mapped.key}' in {APPS[row.realm].name}, which has no such key"
            )

    assert not offenders, (
        f"{DOC} §1 calls these screens built and no app routes to them:\n  "
        + "\n  ".join(offenders)
        + "\n\nEither the row is stale, or the app spells the screen differently and it "
        f"needs an entry in SCREEN_ROUTES in {__file__}."
    )


def test_every_screen_called_not_built_has_none() -> None:
    """A screen someone has since built must stop being listed as owed work.

    §2.2–§2.5 hold the kit inventories for exactly these four and each is read as a
    specification for work outstanding, so a built screen left in that list sends the
    next session to write a second one.
    """
    routed = set().union(*(_union(app) for app in APPS.values()))
    offenders = []
    for row in _rows(_lines()):
        if row.state != "not built":
            continue
        for name in row.names:
            mapped = SCREEN_ROUTES.get(name, Route(name, True, ""))
            if mapped.key is not None and mapped.key in routed:
                offenders.append(f"line {row.line}: `{name}` -> route '{mapped.key}' exists")

    assert not offenders, (
        f"{DOC} §1 calls these screens not built, but an app routes to them:\n  "
        + "\n  ".join(offenders)
        + "\n\nMark the row **built**, re-count the figures above the table, and cut the "
        "matching §2.x inventory down to what is still owed."
    )


def test_every_route_is_a_kit_screen_or_a_stated_exception() -> None:
    """The converse, and the half that makes this a drift gate rather than a proofread.

    A screen shipped in an app with no row in §1 is invisible to every reader of the
    document, and nothing else in CI notices: the app compiles, the kit is unchanged,
    and the table is merely a little shorter than the truth.
    """
    expected = _expected_routes()
    offenders = []
    for realm, app in APPS.items():
        for key in sorted(_union(app) - expected[realm]):
            if f"{realm}.{key}" not in NOT_IN_THE_KIT:
                offenders.append(f"{app.name}: '{key}' ({realm})")

    assert not offenders, (
        f"these screens are routed by an app and have no row in {DOC} §1:\n  "
        + "\n  ".join(offenders)
        + "\n\nAdd the row and re-count the figures above the table, or add an entry to "
        f"NOT_IN_THE_KIT in {__file__} saying why the kit has no screen for it."
    )


def test_every_nav_route_key_is_a_screen_or_a_cross_realm_link() -> None:
    """A nav tile pointing at a screen the union has never heard of.

    TypeScript permits it — `onNavigate` casts with `key as Screen` — so the tile
    compiles, renders, and does nothing when clicked. This is the only check that sees
    it, and it lives here because the same scan already has both files open.
    """
    for realm, app in APPS.items():
        keys = _nav_keys(app)
        assert keys, (
            f"no `key: '...'` literals found in {app}. The nav scan in {__file__} has "
            "stopped matching, which leaves this test passing over nothing."
        )
        unknown = sorted(keys - _union(app) - CROSS_REALM_LINKS)
        assert not unknown, (
            f"{app.name} ({realm}) has nav entries whose keys are not in its "
            f"`type Screen` union: {unknown}. `key as Screen` hides this from the "
            f"compiler. Add them to the union, or to CROSS_REALM_LINKS in {__file__} if "
            "they are `href` links into the other bundle."
        )


def test_the_stated_counts_match_the_table() -> None:
    """The sentences above the table, checked against the table itself.

    A row can be added correctly and the sentence above it left alone; that is how "22
    tables across seven contexts" survived twenty migrations and twelve contexts in
    `DATABASE-REVIEW.md` §1. Each figure is compared as the *string* an author would
    have to type, so a failure names the wording rather than a number in a vacuum.
    """
    rows = _rows(_lines())
    section = _flat(_lines())
    total = sum(len(row.names) for row in rows)
    built = sum(len(row.names) for row in rows if row.state == "built")
    not_built = [row for row in rows if row.state == "not built"]

    kit_claim = f"The kit is {_word(total)} screens of static HTML"
    assert kit_claim in _preamble(), (
        f'{DOC} line 3 should say "{kit_claim}" — `design/` holds {total} screens, '
        f"{total + 1} files less `{CONTENTS_PAGE}`."
    )

    headline = f"**{_word(built).capitalize()} of {_word(total)} are built.**"
    assert headline in section, (
        f'{DOC} §1 should open "{headline}"; the table says {built} of {total} are built.'
    )

    owed = f"the {_word(len(not_built))} that do not are all control-realm"
    assert owed in section, (
        f'{DOC} §1 should say "{owed}"; the table has {len(not_built)} not-built rows.'
    )
    misfiled = [f"line {row.line}: `{row.names[0]}` is {row.realm}" for row in not_built]
    assert all(row.realm == "control" for row in not_built), (
        f"{DOC} §1 says every unbuilt screen is control-realm, and these are not:\n  "
        + "\n  ".join(misfiled)
        + "\n\nThe same sentence claims every public screen ships, so one of the two "
        "halves is now false."
    )

    public_row = next(row for row in rows if row.realm == "public")
    cross_check = f"**built** — all {_word(len(public_row.names))}"
    assert cross_check in DOC.read_text(encoding="utf-8"), (
        f"the public row of {DOC} §1 (line {public_row.line}) should end "
        f'"{cross_check}"; it names {len(public_row.names)} screens.'
    )

    settings_claim = f"{len(FIELDS)} parameters across {_word(len(SECTIONS))} sections"
    assert settings_claim in section, (
        f'{DOC} §1 should say "{settings_claim}" — that is `len(FIELDS)` and '
        "`len(SECTIONS)` in `contexts/settings/sections.py`, which the preamble already "
        "names as the source. §2.1 carries the same figures and needs the same edit."
    )


def test_the_mapping_has_no_stale_entries() -> None:
    """An exemption that no longer applies is a rule nobody is following.

    Both dicts document a real difference between the kit's vocabulary and the apps'. An
    entry that has quietly become untrue — `auth` gaining a route, `prep` gaining a kit
    screen — turns the reason beside it into misinformation with a test defending it.
    """
    kit = _kit_screens()
    unions = {realm: _union(app) for realm, app in APPS.items()}
    realms = {name: row.realm for row in _rows(_lines()) for name in row.names}
    routed = set().union(*unions.values())
    expected = _expected_routes()

    stale = []
    for name, mapped in SCREEN_ROUTES.items():
        realm = realms.get(name, "")
        if name not in kit:
            stale.append(f"SCREEN_ROUTES['{name}']: no such screen in design/")
        elif mapped.key is None:
            if name in routed:
                stale.append(f"SCREEN_ROUTES['{name}']: said to have no route, and one exists")
        elif mapped.key not in unions.get(realm, set()):
            stale.append(f"SCREEN_ROUTES['{name}']: '{mapped.key}' is not in the {realm} union")
        elif (mapped.key in _nav_keys(APPS[realm])) != mapped.in_nav:
            stale.append(
                f"SCREEN_ROUTES['{name}']: in_nav={mapped.in_nav}, and the {realm} nav "
                f"says otherwise about '{mapped.key}'"
            )

    for entry in NOT_IN_THE_KIT:
        realm, _, key = entry.partition(".")
        if key not in unions.get(realm, set()):
            stale.append(f"NOT_IN_THE_KIT['{entry}']: the {realm} app no longer routes to it")
        elif key in expected[realm]:
            stale.append(f"NOT_IN_THE_KIT['{entry}']: §1 now has a screen for it")

    assert not stale, (
        "entries that are no longer exemptions:\n  "
        + "\n  ".join(stale)
        + f"\n\nDelete them from {__file__}; the reason beside each is now false."
    )


def test_the_check_can_actually_see_the_patterns() -> None:
    """A guard that cannot fail is not a guard.

    Every matcher here is a regex over someone else's file and each fails *open*: a
    renamed `type Screen`, a reformatted union, a table drawn with different spacing all
    produce zero matches, which the assertions above read as "nothing is wrong". Both
    union shapes are exercised on purpose — the console wraps one `| 'name'` per line and
    the storefront puts all seven on one, and a pattern handling only the shape you
    happened to look at silently stops guarding the other app.
    """
    multiline = "type Screen =\n  | 'dashboard'\n  | 'orders'\n\nconst x = 1\n"
    block = _UNION.search(multiline)
    assert block and set(re.findall(r"'([a-z-]+)'", block.group(1))) == {"dashboard", "orders"}

    block = _UNION.search("type Screen = 'promo' | 'catalog' | 'blog-post'\n")
    assert block and set(re.findall(r"'([a-z-]+)'", block.group(1))) == {
        "promo",
        "catalog",
        "blog-post",
    }

    assert _NAV_KEY.findall("  {\n      key: 'prep',\n      label: t('prep.title'),\n") == ["prep"]

    parsed = _rows(
        [
            (10, "| Screen | Realm | State |"),
            (11, "|---|---|---|"),
            (12, "| `promo` `blog-post` | public | **built** — all two |"),
            (13, "| `store` | control | **not built** — §2.4 |"),
            (14, "| `mystery` | control | built |"),
        ]
    )
    assert [row.names for row in parsed] == [("promo", "blog-post"), ("store",), ("mystery",)]
    assert [row.state for row in parsed] == ["built", "not built", None]
    assert [row.realm for row in parsed] == ["public", "control", "control"]
    assert [row.line for row in parsed] == [12, 13, 14]

    assert _flat([(1, "the four that do"), (2, "not are all control-realm")]) == (
        "the four that do not are all control-realm"
    )


@pytest.mark.parametrize("path", [DOC, KIT, *APPS.values()], ids=["doc", "kit", "web", "console"])
def test_every_file_that_is_read_exists(path: Path) -> None:
    """Catches a rename turning the whole gate into a no-op over a missing file.

    Deliberately an assertion rather than the `pytest.skip("frontend not present")` that
    `tests/api/test_events_ws.py` uses. A skip is how a gate stops running without anyone
    noticing, which is §5 of CLAUDE.md and is the failure this issue exists to close. CI
    checks out the whole repository, so nothing is lost by insisting.
    """
    assert path.exists(), f"{path} is read by {__file__} and is not there"
