"""Deriving served operations and their frontend consumers, for the §4 gate.

Split out when `check_file_length` refused the combined file. The original noted a
fourth gate as the moment to extract shared support; the length limit arrived
first. The banners this file keeps — the frontend side, the backend side, the
document side — were already the seam.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import printorian.models  # noqa: F401 - registers every mapped class on the registry
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
