"""A base image pinned by digest alone is one nobody can review, and nobody can bump.

Every `FROM` and `image:` in the two Dockerfiles and `deploy/compose.prod.yml` used to
read `python@sha256:ffb752e1...` — a digest and nothing else. Docker was perfectly happy:
`name:tag@sha256:...` resolves by the digest and never consults the tag, so the build was
as reproducible as it looked. Two other readers were not. A person reviewing the pin could
not tell `3.13-slim` from `latest`, and an updater — which is what issue #24 is about — has
no reference point to bump a bare digest against at all: Renovate reads one as `latest` and
would walk the farm's base images onto it.

So the tag went back beside every digest, and this file is what keeps it there. What it
checks is deliberately narrow, and it is worth being precise about the limit, because the
obvious stronger claim is one no test in this repository can make:

**Nothing here proves the digest was published under the tag written beside it.** Docker
does not check, the `image` job in CI cannot notice (it resolves by digest, so a wrong tag
builds green forever), and no digest was resolved against a registry when the tags were
added — they record the stream each pin is *required* to follow, taken from what the
repository already wrote down. Only a registry can turn that into a measured fact, and it
does so on the first bump, when the digest is replaced by that stream's head. Saying that
out loud is CLAUDE.md §1: the tag is a declared intent, not a measurement, and a gate that
implied otherwise would be worse than none.

What *is* checkable is agreement, and every check below is one file disagreeing with
another about a version — which is exactly how the failures these pins guard against
arrive: half-applied. The three python stages must be one interpreter (ADR-0005), the
postgres major must be the one the client and the restore runbook expect (ADR-0019), and
CI's service images must share a major with production (ADR-0021).

`ROOT` and the "every scanned path exists" guard follow the sibling doc-drift gates
(`test_docs_table_inventory.py` and its two neighbours) rather than sharing a helper with
them; the reasoning for the duplication is written out there.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# `parents[2]` is the backend root, where the source-scanning gates stop. This one reads
# `frontend/`, `deploy/` and `.github/`, so it needs the repository root above that.
ROOT = Path(__file__).resolve().parents[3]

BACKEND_DOCKERFILE = ROOT / "backend" / "Dockerfile"
FRONTEND_DOCKERFILE = ROOT / "frontend" / "Dockerfile"
COMPOSE_PROD = ROOT / "deploy" / "compose.prod.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
RESTORE_RUNBOOK = ROOT / "docs" / "RUNBOOK-BACKUP-RESTORE.md"

#: The files that describe what the farm actually runs. `docker-compose.yml` at the root is
#: the development stack and is deliberately *not* here: it runs plain tags on purpose, the
#: same way `.github/workflows/ci.yml` does, and a gate demanding digests everywhere would
#: be arguing with a decision rather than protecting one.
PINNED_FILES = (BACKEND_DOCKERFILE, FRONTEND_DOCKERFILE, COMPOSE_PROD)

_DOCKERFILE_BASE = re.compile(r"FROM\s+(\S+)")
_COMPOSE_IMAGE = re.compile(r"\s*image:\s*(\S+)")
_STAGE_NAME = re.compile(r"\bAS\s+([A-Za-z0-9._-]+)")

#: `name:tag@sha256:<64 hex>` and nothing looser. The digest length is spelled out because a
#: truncated one is the kind of copy-paste that Docker rejects at build time and a regex
#: written as `[0-9a-f]+` would wave through here.
PINNED = re.compile(
    r"^(?P<name>[a-z0-9][a-z0-9._/-]*)"
    r":(?P<tag>[A-Za-z0-9_][A-Za-z0-9._-]*)"
    r"@sha256:(?P<digest>[0-9a-f]{64})$"
)

#: Not a registry reference at all: the empty base, and the image the Stage 4 promotion step
#: substitutes. `${PRINTORIAN_IMAGE}` already carries a digest by the time compose reads it
#: — that is the whole point of the promotion — so there is nothing to pin in the file.
EXEMPT = ("scratch",)


def _image_references(path: Path) -> list[tuple[int, str]]:
    """Every base image named by a directive in `path`, with its line number.

    Anchored at the start of the line so the prose does not count. The comment above the
    python pins quotes `python@sha256:...` to explain what was wrong with it, and a scan
    that read comments would report the explanation as the defect.
    """
    pattern = _DOCKERFILE_BASE if path.name == "Dockerfile" else _COMPOSE_IMAGE
    text = path.read_text(encoding="utf-8")
    stages = set(_STAGE_NAME.findall(text))
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = pattern.match(line)
        if match is None:
            continue
        reference = match.group(1)
        # `FROM builder` inside the same file is a stage, not an image; `$` is a compose
        # variable, resolved outside the file and pinned where it is resolved.
        if reference in stages or reference in EXEMPT or "$" in reference:
            continue
        found.append((number, reference))
    return found


def _pins(path: Path) -> list[tuple[int, str]]:
    """The references that carry a digest — the only ones this file has an opinion on."""
    return [(number, ref) for number, ref in _image_references(path) if "@sha256:" in ref]


def _named(references: list[tuple[int, str]], name: str) -> list[str]:
    return [ref for _, ref in references if ref.split(":", 1)[0] == name]


def _major(tag: str) -> str:
    """`17-alpine` -> `17`. The leading number is the only part these comparisons mean."""
    return re.split(r"[^0-9]", tag, maxsplit=1)[0]


def _tag_of(reference: str) -> str:
    match = PINNED.match(reference)
    assert match, f"{reference} is not a `name:tag@sha256:...` pin"
    return match.group("tag")


def test_every_base_image_pin_carries_both_a_tag_and_a_digest() -> None:
    """The state of all nine pins before #24, and the one this file exists to prevent.

    Both halves, because losing either is the same failure from a different side: without
    the digest a rebuild silently changes the base, and without the tag nobody — human or
    updater — can say which base it was supposed to be.
    """
    broken: list[str] = []
    for path in PINNED_FILES:
        for number, reference in _image_references(path):
            if not PINNED.match(reference):
                broken.append(f"  {path.relative_to(ROOT).as_posix()}:{number}  {reference}")

    assert not broken, (
        "these base images are not pinned as `name:tag@sha256:<64 hex>`:\n"
        + "\n".join(broken)
        + "\n\nThe digest is what Docker resolves; the tag is what makes the bump "
        "reviewable and gives an updater something to aim at. Write both, and edit them "
        "together — see the note at the top of backend/Dockerfile."
    )


def test_the_python_base_is_the_same_interpreter_in_every_stage() -> None:
    """ADR-0005's failure, and the reason it would never be caught anywhere else.

    `frontend/Dockerfile`'s schema stage exports the OpenAPI the TypeScript client is
    generated from, and the backend's two stages are what actually serves it. Bump one file
    and not the other and the console is typechecked against a contract produced on an
    interpreter the backend never runs — with every gate in the repository green, because
    the two files are otherwise unrelated and nothing compares them.
    """
    backend = _named(_pins(BACKEND_DOCKERFILE), "python")
    frontend = _named(_pins(FRONTEND_DOCKERFILE), "python")

    assert backend, f"no python base found in {BACKEND_DOCKERFILE.relative_to(ROOT)}"
    assert frontend, (
        f"no python base found in {FRONTEND_DOCKERFILE.relative_to(ROOT)} — if the schema "
        "stage moved, this check moved with it or it is now checking nothing"
    )

    distinct = sorted(set(backend) | set(frontend))
    assert len(distinct) == 1, (
        "the python base differs between stages, which means the OpenAPI contract and the "
        "runtime are two different interpreters (ADR-0005):\n  " + "\n  ".join(distinct)
    )


def test_the_postgres_major_matches_the_client_and_the_restore_runbook() -> None:
    """The irreversible one: `pg_restore` refuses a dump taken by a newer server.

    Three places have to agree and only one of them is a Dockerfile a bump would touch —
    the server in `deploy/compose.prod.yml`, `postgresql-client-NN` in the backend image
    (which is what `scripts/restore_drill.py` shells out to), and the `postgres:NN-alpine`
    the runbook restores with. A major that arrives looking like a dependency update passes
    every other gate here and turns the ADR-0019 drill into a backup nobody can restore,
    discovered at restore time. This is the check that makes that a failing gate instead.
    """
    server = {_major(_tag_of(ref)) for ref in _named(_pins(COMPOSE_PROD), "postgres")}
    assert server, f"no postgres image found in {COMPOSE_PROD.relative_to(ROOT)}"

    client = set(
        re.findall(r"postgresql-client-(\d+)", BACKEND_DOCKERFILE.read_text(encoding="utf-8"))
    )
    runbook = set(
        re.findall(r"postgres:(\d+)-alpine", RESTORE_RUNBOOK.read_text(encoding="utf-8"))
    )

    assert client, "backend/Dockerfile no longer installs a versioned postgresql-client"
    assert runbook, f"no `postgres:NN-alpine` in {RESTORE_RUNBOOK.relative_to(ROOT)}"

    assert server == client == runbook, (
        f"postgres major disagrees across the restore path: server {sorted(server)}, "
        f"postgresql-client {sorted(client)}, runbook {sorted(runbook)}. Moving one of "
        "these alone is a backup-compatibility decision, not a version bump (ADR-0019)."
    )


def test_the_ci_service_images_share_a_major_with_production() -> None:
    """ADR-0021 says the suite runs on real PostgreSQL; drift makes it the wrong real one.

    CI's services are plain tags on purpose — they are torn down every run and pinning them
    by digest would be ceremony. What matters is that they are the same *major* the farm
    runs, because the moment they are not, ~1100 tests keep passing while proving something
    about an engine nobody deploys. Nothing else in the tree compares the two files.
    """
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    for name in ("postgres", "redis"):
        service = {_major(tag) for tag in re.findall(rf"image:\s*{name}:(\S+)", workflow)}
        production = {_major(_tag_of(ref)) for ref in _named(_pins(COMPOSE_PROD), name)}

        assert service, f"no {name} service image found in {CI_WORKFLOW.relative_to(ROOT)}"
        assert production, f"no {name} image found in {COMPOSE_PROD.relative_to(ROOT)}"
        assert service == production, (
            f"CI runs {name} {sorted(service)} and the farm runs {sorted(production)}. "
            "Either the suite's evidence is about the wrong engine or production is behind; "
            "both are decisions, and neither should arrive as a silent drift."
        )


def _documented_github_paths() -> list[tuple[Path, str]]:
    """Backticked `.github/...` paths in the prose, with the document that names them."""
    skip = {".git", ".venv", "node_modules", "dist", "worktrees", "__pycache__"}
    found: list[tuple[Path, str]] = []
    for document in sorted(ROOT.rglob("*.md")):
        if skip & set(document.parts):
            continue
        text = document.read_text(encoding="utf-8", errors="replace")
        for reference in re.findall(r"`(\.github/[^`\s]+)`", text):
            found.append((document, reference.rstrip(".,;:")))
    return found


def test_a_document_naming_a_dot_github_path_names_one_that_exists() -> None:
    """A tripwire for the half of #24 this branch does not do.

    The Renovate migration ends with `.github/dependabot.yml` deleted, and three documents
    point at it by path — `frontend/CLAUDE.md`, `HANDOFF.md`, and the project wiki, which
    nothing generates. On the day the file goes, all three quietly start describing a file
    that is not there, which is precisely the drift CLAUDE.md §4 calls the defect rather
    than a footnote. Cheap now; the only thing that will notice then.
    """
    references = _documented_github_paths()
    assert references, (
        "no backticked `.github/...` path found in any document — either the convention "
        "changed or the scan above is looking in the wrong place, and it is now a no-op"
    )

    missing = [
        f"  {document.relative_to(ROOT).as_posix()} names `{reference}`"
        for document, reference in references
        if not (ROOT / reference).exists()
    ]
    assert not missing, (
        "documents naming a `.github/` path that is not in the tree:\n"
        + "\n".join(missing)
        + "\n\nMoving or deleting a file under .github/ means editing the prose that "
        "points at it, in the same change."
    )


def test_the_scan_can_actually_see_an_unanchored_pin() -> None:
    """A guard that cannot fail is not a guard, and this one was written after the fix.

    D13 asks for a test that fails without the change. The nine real pins were anchored in
    the commit before this file existed, so the failing state is reproduced here instead:
    these are the exact strings `main` carried at 68e2bbe, run through the same parser. If
    the regex is ever loosened into something that accepts a bare digest, this fails rather
    than turning the gate above into a green no-op — the retention-clamp mistake, which
    passed six gates while proving nothing.
    """
    before = (
        "python@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a",
        "node@sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43",
        "caddy@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648",
        "postgres@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73",
        "redis@sha256:e7723ff73d963f5cc6d9c4643ea3d989527a402a319239054e9472a7fb9219a2",
    )
    for reference in before:
        assert not PINNED.match(reference), f"{reference} has no tag and was accepted"

    # And the other half: a tag with a plausible-looking but short digest, which is how a
    # truncated copy-paste would arrive.
    assert not PINNED.match("python:3.13-slim@sha256:ffb752e1")
    assert PINNED.match(
        "python:3.13-slim@sha256:"
        "ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a"
    )


def test_the_parser_ignores_prose_and_reads_directives(tmp_path: Path) -> None:
    """The comments explaining the old form must not be mistaken for the old form.

    `backend/Dockerfile` quotes `python@sha256:...` to say why a bare digest is wrong, and
    a scan that matched anywhere in the line would report that sentence as the defect —
    unfixable, because deleting the explanation is the only way to satisfy it.
    """
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "# A bare python@sha256:... is what this file stopped doing.\n"
        "FROM python:3.13-slim@sha256:"
        + "a" * 64
        + " AS builder\n"
        "FROM builder AS runtime\n"
        "FROM scratch AS cargo\n",
        encoding="utf-8",
    )
    assert [ref for _, ref in _image_references(dockerfile)] == [
        "python:3.13-slim@sha256:" + "a" * 64
    ]

    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n"
        "  a:\n"
        "    # image: redis@sha256:old — replaced\n"
        "    image: redis:7-alpine@sha256:" + "b" * 64 + "\n"
        "  b:\n"
        "    image: ${PRINTORIAN_IMAGE:?must be set}\n",
        encoding="utf-8",
    )
    assert [ref for _, ref in _image_references(compose)] == [
        "redis:7-alpine@sha256:" + "b" * 64
    ]


@pytest.mark.parametrize(
    "target",
    [BACKEND_DOCKERFILE, FRONTEND_DOCKERFILE, COMPOSE_PROD, CI_WORKFLOW, RESTORE_RUNBOOK],
    ids=[
        "backend/Dockerfile",
        "frontend/Dockerfile",
        "deploy/compose.prod.yml",
        ".github/workflows/ci.yml",
        "docs/RUNBOOK-BACKUP-RESTORE.md",
    ],
)
def test_every_file_that_is_scanned_exists(target: Path) -> None:
    """Asserted rather than skipped, for the reason the sibling doc gates give: a skip on a
    renamed path is how a gate stops running with nobody noticing."""
    assert target.exists(), f"{target} is gone — the checks above are now checking nothing"
