"""§4 of the design kit claims backend capability nothing consumes. Measured here.

`docs/DESIGN-KIT.md` §4 once listed **fourteen** endpoints. Thirteen of them
gained a consumer while the list sat still, so for an unknown stretch the section
described a backlog that had almost entirely been built — and the section is
short, so nobody re-derived it. That is the drift this file closes.

It is the same failure the document's own preamble records against its three
predecessors, which "answered the same question from three angles and disagreed
with each other and with the code": §1 and §2.1 called the settings screen unbuilt
while `SettingsPage.tsx` was already serving 102 parameters across fourteen
sections. And the same one `docs/DATABASE-REVIEW.md` §1 suffered — every figure in
its summary wrong (22 tables across seven contexts built by nine migrations, for
an ORM with 42 across twelve and a `versions/` holding twenty) while the body of
the document beneath it was right. The part everyone reads first is the part
nobody re-derives, which is why it has to be CI that re-derives it.

Two sets are derived from the tree, never from a document:

* the operations the API actually serves, from `tools.export_openapi.build_schema()`
  called **in process**. Not from `backend/openapi.json`: that file is gitignored,
  absent on a fresh checkout, and in CI is written only *after* pytest has run, so
  a gate reading it would be a no-op exactly where it is meant to bite.
* the API paths the two frontend apps and the three shared packages reference.

Both directions are asserted, because both have already gone wrong. An endpoint
§4 names that has since gained a consumer fails; an endpoint with no consumer that
§4 does not name fails unless `NOT_A_SCREEN_CONSUMER` says why.

**Scope: this gate checks an inventory, not the prose around it.** Three claims in
§4 are deliberately outside it, and saying so here is cheaper than the next reader
wondering whether they were missed:

* "Thirteen of the fourteen endpoints this section once carried now have
  consumers" is a claim about a list that exists nowhere in the tree any more.
  Nothing can derive it. A gate that appeared to would only be asserting the
  document against itself.
* "`/fleet/metrics` serves it" is prose about `TelemetrySample`, a different
  subject from the bullet list above it.
* `EstimateVariance` and `RateSnapshotRecord` are ORM classes, not routes. All
  that is checked of them is that the named class is still mapped. "Persisted and
  not served" is a claim about response models, and inferring intent from Pydantic
  would be guessing dressed as measurement — ADR-0007 in miniature.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pytest

import printorian.models  # noqa: F401 - registers every mapped class on the registry
from printorian.core.db import Base
from tools.export_openapi import build_schema

# `ROOT`, the section slicer and `NUMBER_WORDS` are duplicated across the sibling
# doc-drift gates rather than shared. Three of them were written at once, and one
# helper module would have been three writers editing one file; ten repeated lines
# is the cheaper mistake. A fourth gate is the point to extract
# `tests/unit/_docs_support.py`, following `_dashboard_support.py`.
ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT.parent
DESIGN_KIT = REPO / "docs" / "DESIGN-KIT.md"
SECTION_HEADING = "## 4. Backend capability nothing consumes"

#: The two frontend glob shapes, `frontend/<tree>/*/src/**`. Chosen over an rglob
#: from `frontend/` with an ignore list for the reason `packages/ui/src/tokens.test.ts`
#: gives at its top: this excludes `node_modules` and `dist` — and `vite.config.ts`,
#: which sits beside `package.json` — "by construction rather than by an ignore
#: list that can drift".
FRONTEND_TREES = ("apps", "packages")

#: Small on purpose: §4 counts its own bullets in words, and this gate needs no
#: number the section could plausibly reach.
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}

#: Endpoints with no frontend consumer that §4 deliberately does not name. Adding
#: to this list is the point: it forces the choice — document it as owed capability
#: or state why it is not — to be made and written down rather than made by
#: omission. Three of these can never have a screen; the rest are real gaps, named
#: so that a person triaging them starts from what is missing rather than from zero.
NOT_A_SCREEN_CONSUMER: dict[str, str] = {
    "POST /payments/webhook/{provider_name}": (
        "The payment gateway calls this, not the console. A frontend consumer here "
        "would mean the farm forging its own settlement notifications."
    ),
    "POST /journal/unsubscribe/{token}": (
        "Reached from a link in a sent email, so the consumer is the mail body, not "
        "a screen. The token is the whole of the authentication."
    ),
    "GET /health/workers": (
        "An operations probe. HANDOFF §2 records it as the honest liveness signal "
        "while there is no `/metrics` endpoint: it reads the beat each worker loop "
        "writes at the *end* of a pass, so it separates wedged from working. "
        "Surfacing that is Stage 5's job."
    ),
    "GET /settings": (
        "`SettingsPage.tsx` builds the screen from `/settings/sections` and the "
        "audit from `/settings/history`. The flat key/value dump has no caller and "
        "no screen currently wants one."
    ),
    "GET /fleet/metrics": (
        "Measured occupancy in seconds. `DashboardPage.tsx` receives its occupancy "
        "inside the `GET /dashboard` payload, so this route serves nothing on any "
        "screen. Of everything in this list it has the best claim to belong in §4 "
        "itself instead."
    ),
    "GET /fleet/metrics/{printer_id}": ("The per-printer shape of the same gap."),
    "PUT /printers/{printer_id}/slots": (
        "Replacing a printer's whole slot map in one call. `MaterialsPage.tsx` edits "
        "one slot at a time through `/printers/{id}/slots/{unit}/{index}`, so the "
        "bulk route has no caller."
    ),
    "POST /packaging/tara": (
        "Stocking a box type. There is no shelf-management screen — `PackagingPage.tsx` "
        "reads the board and `ParcelDetail.tsx` drives one parcel."
    ),
    "DELETE /packaging/tara/{tara_id}": ("Retiring a box type, and the same missing screen."),
    "POST /packaging/instruction": (
        "Publishing a packing instruction. The same missing screen: instructions are "
        "read back through a parcel and never written from one."
    ),
    "POST /packaging/parcels": (
        "Raising a parcel by hand. `workers/packaging.py` reconciles them into "
        "existence instead — it asks which orders have finished every finishing task "
        "and have no parcel yet — so this is the exception path and nothing offers it."
    ),
    "POST /postproduction/tasks": (
        "The same shape one context over: `workers/postproduction.py` asks which "
        "succeeded jobs have no task yet and makes the missing ones."
    ),
    "GET /jobs/{job_id}": (
        "There is no job detail screen. `PrepPage.tsx` works from the prep queue and "
        "goes straight to the model and the plate file."
    ),
    "GET /jobs/{job_id}/decisions": (
        "The planner's assignment record for one job — every candidate considered and "
        "the grounds each was rejected on. Nothing reads it, which is the entry worth "
        "regretting: `assignment_records` exists to answer exactly this question, and "
        "the route's own docstring says V1 could not."
    ),
    "POST /jobs/{job_id}/plate": (
        "Recording an already-sliced plate by reference. `PrepPage.tsx` uploads the "
        "file to `/jobs/{id}/plate/file`, which records and attaches one on the way "
        "through, so only the upload half has a consumer."
    ),
    "POST /jobs/{job_id}/release": (
        "Letting a price-held job through once somebody has settled the difference. "
        "Nothing on a screen lifts a hold."
    ),
    "GET /jobs/plates/find": (
        "Looking a cached plate up by its configuration key. Its docstring calls it "
        "'what the console asks before opening a slicer' — the console does not ask."
    ),
    "GET /jobs/wait-list": (
        "Work nothing can take yet, and why. `PrepPage.tsx` shows `/jobs/prep-queue` "
        "and not this, so the half of the backlog that needs a person rather than "
        "time is invisible."
    ),
    "GET /orders/overdue": (
        "The SLA breach list. `OrderDesk.tsx` reads `/orders` and advances one at a "
        "time; nothing on the desk lists what is late."
    ),
    "GET /orders/{order_id}": (
        "No order detail screen on the console — the desk is a table plus an advance "
        "action, and the customer's own view comes from `/orders/mine`."
    ),
    "GET /payments/{payment_id}": (
        "`OrderDesk.tsx` reaches payments through `/payments/order/{order_id}`, so a "
        "payment is always fetched by the order it belongs to, never by its own id."
    ),
    "POST /payments/{payment_id}/settle-manually": (
        "Marking a payment settled by hand, the bank-transfer path. Both refund "
        "routes are wired into the desk and this one is not, so it is a gap in a "
        "screen that already exists rather than a missing screen."
    ),
    "GET /users/{user_id}": (
        "`UsersPage.tsx` lists everyone and edits role and active state in place, so "
        "one user is never fetched on its own."
    ),
}

HTTP_METHODS = ("get", "post", "put", "patch", "delete")

#: One pattern per delimiter, so a backtick template containing a single quote is
#: still read whole rather than truncated at the quote.
QUOTED_PATH = (
    re.compile(r"'(/[^'\n]*)'"),
    re.compile(r'"(/[^"\n]*)"'),
    re.compile(r"`(/[^`\n]*)`"),
)
INTERPOLATION = re.compile(r"\$\{[^{}]*\}")
OPENAPI_PARAM = re.compile(r"\{[^/{}]+\}")
DOC_ENTRY = re.compile(r"^- \[#(\d+)\]\([^)]+\) — \*\*`([^`]+)`\*\*")
DOC_ENDPOINT = re.compile(r"^(GET|POST|PUT|PATCH|DELETE) (/\S*)$")
REMAINING_COUNT = re.compile(r"The (\w+) that remain")


# --------------------------------------------------------------- the frontend side


def _is_a_consumer(path: Path) -> bool:
    if path.name.endswith((".test.ts", ".test.tsx")):
        # A mocked fetch URL proves a test exists, not that a screen calls anything.
        return False
    # `packages/api-client/src/generated/` is the OpenAPI schema turned back into
    # TypeScript: it names every path the API serves, so counting it would make this
    # gate answer "nothing is unconsumed" for ever. It is also gitignored, so on a
    # fresh checkout it is simply absent — the gate's answer would otherwise depend
    # on whether whoever ran it had generated the client.
    return "generated" not in path.parts


def _frontend_sources() -> list[Path]:
    found: list[Path] = []
    for tree in FRONTEND_TREES:
        for area in sorted((REPO / "frontend" / tree).glob("*")):
            source_root = area / "src"
            if not source_root.is_dir():
                continue
            for suffix in ("*.ts", "*.tsx"):
                found.extend(sorted(source_root.rglob(suffix)))
    return [path for path in found if _is_a_consumer(path)]


def _as_api_path(literal: str) -> str | None:
    """Reduce one source literal to the API path it would request."""
    path = INTERPOLATION.sub("{}", literal)
    for cut in ("?", "#"):
        path = path.partition(cut)[0]
    # `new ApiClient({ baseUrl: '/api' })`, so the generated client's own paths carry
    # no prefix while raw `<a href>` and `fetch` call sites — six of them, including
    # `/api/account/orders.csv` and `/api/pricing/quote` — spell it out.
    if path.startswith("/api/"):
        path = path[len("/api") :]
    if not path.startswith("/"):
        return None
    return path.rstrip("/") or "/"


@lru_cache(maxsize=1)
def _referenced_paths() -> frozenset[str]:
    found = set()
    for path in _frontend_sources():
        text = path.read_text(encoding="utf-8")
        for pattern in QUOTED_PATH:
            for literal in pattern.findall(text):
                reduced = _as_api_path(literal)
                if reduced:
                    found.add(reduced)
    return frozenset(found)


def _covers(spec_path: str, literal: str) -> bool:
    """Does one frontend literal reach `spec_path`? The wildcard rule is asymmetric.

    A frontend `{}` came from an interpolation and may stand for anything, a
    literal API segment included — ``api.post(`/packaging/parcels/${id}/${path}`)``
    in `ParcelDetail.tsx` is one call site covering nine endpoints. An OpenAPI
    `{param}` may **not**: it has to line up with a `{}` and never with an arbitrary
    literal. Read symmetrically, `/materials/recommend` "consumes"
    `/materials/{code}` — which deletes the one endpoint §4 actually names from the
    derived set and leaves this gate green while proving nothing. That false
    positive happened while this file was being written.
    """
    spec = spec_path.strip("/").split("/")
    seen = literal.strip("/").split("/")
    if len(spec) != len(seen):
        return False
    return all(
        ("{}" in part) if OPENAPI_PARAM.fullmatch(want) else (part == want or "{}" in part)
        for want, part in zip(spec, seen, strict=True)
    )


# --------------------------------------------------------------- the backend side


@lru_cache(maxsize=1)
def _served_operations() -> tuple[str, ...]:
    paths = build_schema()["paths"]
    assert isinstance(paths, dict)
    return tuple(
        f"{method.upper()} {path}"
        for path in sorted(paths)
        for method in HTTP_METHODS
        if method in paths[path]
    )


@lru_cache(maxsize=1)
def _unconsumed_operations() -> tuple[str, ...]:
    literals = _referenced_paths()
    consumed = {
        path
        for path in {operation.split(" ", 1)[1] for operation in _served_operations()}
        if any(_covers(path, literal) for literal in literals)
    }
    return tuple(
        operation
        for operation in _served_operations()
        if operation.split(" ", 1)[1] not in consumed
    )


# --------------------------------------------------------------- the document side


def _section_4() -> list[str]:
    lines = DESIGN_KIT.read_text(encoding="utf-8").splitlines()
    assert SECTION_HEADING in lines, (
        f"{DESIGN_KIT} no longer carries a heading reading {SECTION_HEADING!r}. If the "
        f"section was renamed, update SECTION_HEADING in {__file__}; if it was deleted, "
        "delete this gate with it rather than leaving it scanning nothing."
    )
    start = lines.index(SECTION_HEADING)
    for offset, line in enumerate(lines[start + 1 :], start=start + 1):
        if line.startswith("## "):
            return lines[start:offset]
    return lines[start:]


def _section_4_entries() -> tuple[list[str], list[str]]:
    """§4's bullets, split into (endpoints, ORM class names)."""
    endpoints: list[str] = []
    classes: list[str] = []
    for line in _section_4():
        entry = DOC_ENTRY.match(line)
        if not entry:
            continue
        route = DOC_ENDPOINT.fullmatch(entry.group(2))
        if route:
            endpoints.append(f"{route.group(1)} {route.group(2)}")
        else:
            classes.append(entry.group(2))
    return endpoints, classes


DOC_ENDPOINTS, DOC_CLASSES = _section_4_entries()


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
    collect zero cases and report green. This is what fails instead. It also holds
    §4's own spelled count against the bullets under it, which is the one number in
    the section derivable from the section.
    """
    bullets = [line for line in _section_4() if line.startswith("- [#")]
    parsed = DOC_ENDPOINTS + DOC_CLASSES
    assert len(parsed) == len(bullets) and parsed, (
        f"§4 of {DESIGN_KIT.name} has {len(bullets)} issue bullets and DOC_ENTRY in "
        f"{__file__} read {len(parsed)} of them. The expected shape is "
        "``- [#NN](url) — **`SUBJECT`**, prose``."
    )
    spelled = REMAINING_COUNT.search("\n".join(_section_4()))
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
