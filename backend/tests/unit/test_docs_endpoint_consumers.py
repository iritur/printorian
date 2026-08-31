"""§4 of the design kit claims backend capability nothing consumes. Measured here.

`docs/DESIGN-KIT.md` §4 once listed **fourteen** endpoints; thirteen gained a
consumer while the list sat still, so for an unknown stretch the section described
a backlog that had almost entirely been built. That is the drift these close.

**Two of the gates here are skipped now, and that is the recorded state of §4.**
`test_every_endpoint_named_in_section_4_really_has_no_consumer` and
`test_every_class_named_in_section_4_is_still_mapped` are parametrized over what §4
names, §4 names nothing since #38, and pytest reports an empty parameter set as a
skip — the run reads `6 passed, 2 skipped`. `test_every_tree_that_is_scanned_exists`
says below that a skip is how a gate stops running without anyone noticing, and the
distinction is worth being explicit about rather than leaving a reader to reconcile
the two: that one would be a gate skipping itself over a condition it measured,
which is why it is an assertion instead. These two have no rows to check because
the document they check has no rows, and the thing that could make them *silently*
empty — `DOC_ENTRY` ceasing to match the bullet shape — is what `SAMPLE_BULLET` and
`test_section_4_still_parses_into_the_entries_it_carries` exist to catch. The other
direction, `test_every_unconsumed_endpoint_is_named_in_the_doc_or_exempted`, is not
parametrized and runs whatever §4 holds; it is the one that fails when an endpoint
quietly loses its consumer, and it is unaffected by all of this.

The three derivations live in `_docs_endpoint_support.py`.
"""

from __future__ import annotations

import pytest

from printorian.core.db import Base
from tests.unit._docs_endpoint_support import (
    DESIGN_KIT,
    DOC_CLASSES,
    DOC_ENDPOINTS,
    DOC_ENTRY,
    FRONTEND_TREES,
    NOT_A_SCREEN_CONSUMER,
    NUMBER_WORDS,
    REMAINING_COUNT,
    REPO,
    _as_api_path,
    _covers,
    _referenced_paths,
    _section_4,
    _served_operations,
    _unconsumed_operations,
)

#: The bullet shape §4 is written in, as one line the parser can be held against on
#: a day the section carries none of its own. It is the last entry §4 ever had, kept
#: verbatim after #38 built its consumer.
#:
#: The self-check used to be "§4 has at least one bullet", which is a guard with an
#: expiry date on it: the list is *meant* to reach zero, and reaching it failed this
#: gate rather than the section. What the assertion is actually for — noticing that
#: `DOC_ENTRY` has stopped matching, which would make the two parametrized gates
#: above collect nothing and report green — needs a bullet, not §4's bullet.
SAMPLE_BULLET = (
    "- [#38](https://github.com/iritur/printorian/issues/38) — "
    "**`GET /materials/{code}`**, the materials detail popup"
)

# --------------------------------------------------------------- the gates


@pytest.mark.parametrize("named", DOC_ENDPOINTS, ids=DOC_ENDPOINTS)
def test_every_endpoint_named_in_section_4_really_has_no_consumer(named: str) -> None:
    """An entry that has since gained a consumer is the drift §4 has already suffered.

    Thirteen of its fourteen entries went stale at once and the section went on
    presenting them as owed work. This is the direction that catches the fourteenth.
    """
    assert named in _served_operations(), (
        f"{DESIGN_KIT.name} §4 names `{named}`, which the API no longer serves. "
        "Either the route was renamed and the bullet should follow it, or the "
        "capability is gone and the bullet should go with it."
    )
    assert named in _unconsumed_operations(), (
        f"{DESIGN_KIT.name} §4 lists `{named}` as capability nothing consumes, and "
        "something now does — a path literal reaching it exists under "
        "`frontend/apps/*/src` or `frontend/packages/*/src`. Remove that bullet from "
        "§4 and close its issue."
    )


def test_every_unconsumed_endpoint_is_named_in_the_doc_or_exempted() -> None:
    """Capability that quietly acquires no consumer is what §4 exists to surface.

    Without this direction the section can only go stale in the flattering way:
    entries leave as they are built and nothing ever puts one in, so the list
    shrinks to nothing and reads as "the backend owes the console nothing".
    """
    unnamed = sorted(
        set(_unconsumed_operations()) - set(DOC_ENDPOINTS) - set(NOT_A_SCREEN_CONSUMER)
    )
    assert not unnamed, (
        "These endpoints have no path literal anywhere under `frontend/apps/*/src` or "
        "`frontend/packages/*/src`, and neither §4 of docs/DESIGN-KIT.md nor "
        "NOT_A_SCREEN_CONSUMER accounts for them:\n  " + "\n  ".join(unnamed) + "\n\n"
        f"Add a bullet to §4 (with an issue behind it), or an entry to "
        f"NOT_A_SCREEN_CONSUMER in {__file__} saying why no screen will ever call it."
    )


def test_the_exemption_list_has_no_stale_entries() -> None:
    """An exemption that no longer applies is a rule nobody is following."""
    served = set(_served_operations())
    unconsumed = set(_unconsumed_operations())
    gone = sorted(entry for entry in NOT_A_SCREEN_CONSUMER if entry not in served)
    consumed = sorted(entry for entry in NOT_A_SCREEN_CONSUMER if entry in served - unconsumed)
    assert not gone and not consumed, (
        f"NOT_A_SCREEN_CONSUMER entries in {__file__} that are no longer exemptions — "
        f"routes the API no longer serves: {gone}; routes something now calls: {consumed}."
    )


@pytest.mark.parametrize("named", DOC_CLASSES, ids=DOC_CLASSES)
def test_every_class_named_in_section_4_is_still_mapped(named: str) -> None:
    """`EstimateVariance` and `RateSnapshotRecord` are the two non-route entries.

    A renamed or deleted mapped class would leave §4 naming a thing that is not
    there. That much is mechanical; whether it is *served* is not — see the module
    docstring on why nothing here tries to decide it.
    """
    mapped = {mapper.class_.__name__ for mapper in Base.registry.mappers}
    assert named in mapped, (
        f"{DESIGN_KIT.name} §4 names `{named}`, which is not a mapped class on "
        "`Base.registry`. It was renamed or removed and the bullet did not follow."
    )


def test_section_4_still_parses_into_the_entries_it_carries() -> None:
    """A parametrized gate over an empty list is a gate that stopped running.

    If the bullet format changes and `DOC_ENTRY` stops matching, the two tests above
    collect zero cases and report green. This is what fails instead — against
    `SAMPLE_BULLET` rather than against §4's own first line, because §4 is empty now
    and an empty section must not be able to disarm the parser it is checked with. It
    also holds §4's own spelled count against the bullets under it, which is the one
    number in the section derivable from the section.
    """
    assert DOC_ENTRY.match(SAMPLE_BULLET), (
        f"DOC_ENTRY in {__file__} no longer matches the shape §4's bullets are "
        "written in, so both gates above would collect nothing and report green. The "
        "expected shape is ``- [#NN](url) — **`SUBJECT`**, prose``."
    )

    bullets = [line for line in _section_4() if line.startswith("- [#")]
    parsed = DOC_ENDPOINTS + DOC_CLASSES
    assert len(parsed) == len(bullets), (
        f"§4 of {DESIGN_KIT.name} has {len(bullets)} issue bullets and DOC_ENTRY in "
        f"{__file__} read {len(parsed)} of them. The expected shape is "
        "``- [#NN](url) — **`SUBJECT`**, prose``."
    )

    spelled = REMAINING_COUNT.search("\n".join(_section_4()))
    if not bullets:
        # A section that lists nothing and still counts something is the drift in
        # miniature, and `NUMBER_WORDS` deliberately has no word for zero: the
        # sentence should be gone rather than set to "none".
        assert spelled is None, (
            f"§4 of {DESIGN_KIT.name} carries no bullets and still spells a count: "
            f"{spelled.group(0) if spelled else ''}."
        )
        return
    assert spelled and NUMBER_WORDS.get(spelled.group(1).lower()) == len(bullets), (
        f"§4 of {DESIGN_KIT.name} says {spelled.group(1) if spelled else '(no count)'} "
        f"entries remain and lists {len(bullets)}."
    )


def test_the_check_can_actually_see_a_consumer() -> None:
    """A guard that cannot fail is not a guard.

    If `QUOTED_PATH` stops matching, or `_as_api_path` stops reducing, every path
    looks unconsumed and only this test says so — the rest would keep passing on any
    codebase. The asymmetry assertion is here for the same reason: it is the one
    that, read backwards, made `/materials/{code}` disappear from the derived set.
    """
    literals = _referenced_paths()
    resolving = {
        literal
        for literal in literals
        if any(_covers(operation.split(" ", 1)[1], literal) for operation in _served_operations())
    }
    assert len(resolving) > 60, f"only {len(resolving)} literals resolved to an API path"
    assert any(_covers("/dashboard", literal) for literal in literals)
    assert not any(_covers("/nothing/calls/this", literal) for literal in literals)

    assert _covers("/printers/{printer_id}/slots/{unit}/{index}", "/printers/{}/slots/{}/{}{}")
    assert _covers("/packaging/parcels/{task_id}/ship", "/packaging/parcels/{}/{}")
    assert not _covers("/materials/{code}", "/materials/recommend")
    assert _as_api_path("/api/pricing/quote?x=${y}") == "/pricing/quote"


@pytest.mark.parametrize("tree", FRONTEND_TREES)
def test_every_tree_that_is_scanned_exists(tree: str) -> None:
    """Catches a rename turning the scan into a no-op over a missing directory.

    Deliberately an assertion rather than `pytest.skip("frontend not present")`: a
    skip is how a gate stops running without anyone noticing, which is the failure
    this whole file exists to close. CI checks out the whole repository.
    """
    assert (REPO / "frontend" / tree).is_dir()
