"""`docs/DATABASE-REVIEW.md` §1 counts tables; this fails when the code stops agreeing.

§1 is the paragraph everyone reads first and the one nobody re-derives, so it drifted
the furthest. Until it was corrected it claimed "**22 tables** across seven contexts,
built by nine Alembic migrations" while the ORM held 42 across twelve and
`alembic/versions/` held twenty — and its inventory table omitted `account`, `journal`,
`packaging`, `postproduction` and `settings` entirely, plus `catalog_models`,
`catalog_model_materials` and `metric_rollups`. Five whole contexts were invisible to a
reader deciding where a new table belongs.

That was not one bad edit. Three predecessor design documents answered the same question
from three angles and disagreed with each other and with the code; `DESIGN-KIT.md` §1 and
§2.1 called the settings screen unbuilt while `SettingsPage.tsx` was already serving 102
parameters. Correcting a number by hand fixes the number for one day. This fixes it for
every day after, by deriving the figures from `contexts/**/__tablename__` and failing
when the prose and the declarations part company — the failure §4 of CLAUDE.md is about.

**Scope: counts and inventories only. Prose is out of scope by design.** Whether §1
*explains* the shape well is a judgement no test can make, and a gate that tried would
either be unfalsifiable or would fight every rewording. §1.1's foreign-key diagram is
likewise not checked here: it describes structure rather than a count, and
`tests/test_schema_contracts.py` already holds the real keys to their properties. The
section slice therefore stops at the `### 1.1` heading rather than at `## 2.`

`ROOT`, the section slicer and `NUMBER_WORDS` below are duplicated in the sibling
doc-drift gates rather than shared. Three of these were written in parallel and a common
helper module would have been a merge conflict in every one of them; roughly ten lines
each is the cheaper side of that trade. Extract `tests/unit/_docs_support.py` — the
naming this suite already uses for shared test helpers — if a fourth gate is ever added.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import printorian.models  # noqa: F401 - registers every table on the metadata
from printorian.core.db import Base

# `parents[2]` is the backend root, which is where the source-scanning gates stop.
# This one also reads `docs/`, so it needs the repository root one level further up.
ROOT = Path(__file__).resolve().parents[3]
DOC = ROOT / "docs" / "DATABASE-REVIEW.md"
CONTEXTS = ROOT / "backend" / "printorian" / "contexts"
VERSIONS = ROOT / "backend" / "alembic" / "versions"

_UNITS = (
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
)

#: Spelled-out numbers, because §1 spells out every figure except the bolded table
#: count. The assertions below compare against the *string* an author would have to
#: type, so a failure can print it rather than making them work out the wording.
NUMBER_WORDS: dict[int, str] = {index + 1: word for index, word in enumerate(_UNITS)}
NUMBER_WORDS[20] = "twenty"
NUMBER_WORDS.update({20 + n: f"twenty-{_UNITS[n - 1]}" for n in range(1, 10)})
NUMBER_WORDS[30] = "thirty"


def _section() -> str:
    """§1 down to §1.1, which is where the counts stop and the diagram starts."""
    text = DOC.read_text(encoding="utf-8")
    start = text.index("## 1. Shape")
    end = text.index("### 1.1", start)
    return text[start:end]


def _flat(text: str) -> str:
    """Collapse the hard line wrapping, which splits "built / by twenty" mid-sentence."""
    return " ".join(text.split())


def _parse_context_table(section: str) -> dict[str, set[str]]:
    """The `| Context | Tables |` grid as a mapping, ignoring the header and rule."""
    parsed: dict[str, set[str]] = {}
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 2:
            continue
        contexts = re.findall(r"`([^`]+)`", cells[0])
        if len(contexts) != 1:  # the header row and the `|---|---|` rule
            continue
        parsed[contexts[0]] = set(re.findall(r"`([^`]+)`", cells[1]))
    return parsed


#: The run of backticked names in front of "own no tables at all". Anchored to the
#: names themselves rather than to "everything since the last full stop": the table
#: above the sentence contains no full stop, so a looser pattern swallows all
#: forty-two table names and compares them against two context directories.
_OWNS_NOTHING = re.compile(r"((?:`[^`]+`(?:,\s+|\s+and\s+)?)+)\s*own no tables at all")


def _contexts_declared_empty(section: str) -> set[str]:
    """The contexts §1 says own nothing, read out of the sentence that says it."""
    sentence = _OWNS_NOTHING.search(_flat(section))
    return set(re.findall(r"`([^`]+)`", sentence.group(1))) if sentence else set()


def _tablenames_by_context() -> dict[str, set[str]]:
    """Every `__tablename__ = "..."` under `contexts/`, keyed by its context directory.

    Read from the source rather than from the metadata on purpose: a context whose
    models module is missing from `printorian/models.py` never registers a table, so
    the metadata cannot see it and neither can `alembic check`. Only the files can.
    """
    found: dict[str, set[str]] = {}
    for path in CONTEXTS.rglob("*.py"):
        context = path.relative_to(CONTEXTS).parts[0]
        parsed = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(parsed):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
                continue
            if not isinstance(node.value.value, str):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__tablename__":
                    found.setdefault(context, set()).add(node.value.value)
    return found


def _context_directories() -> set[str]:
    return {
        entry.name
        for entry in CONTEXTS.iterdir()
        if entry.is_dir() and not entry.name.startswith((".", "_"))
    }


def test_the_context_table_lists_every_table_and_no_others() -> None:
    """The inventory that was missing five contexts and three tables at once.

    Reported per context rather than as a set difference, because the thing a reader
    has to act on is "`fleet` gained `metric_rollups`" — a symmetric difference of
    forty-two names makes them find that themselves.
    """
    documented = _parse_context_table(_section())
    declared = _tablenames_by_context()

    drift: list[str] = []
    for context in sorted(set(documented) | set(declared)):
        missing = sorted(declared.get(context, set()) - documented.get(context, set()))
        extra = sorted(documented.get(context, set()) - declared.get(context, set()))
        if context not in documented:
            drift.append(f"  {context}: not in the table at all — declares {missing}")
        elif context not in declared:
            drift.append(f"  {context}: listed in the table but declares no tables ({extra})")
        else:
            if missing:
                drift.append(f"  {context}: declared in code, absent from the table — {missing}")
            if extra:
                drift.append(f"  {context}: in the table, declared nowhere in code — {extra}")

    assert not drift, (
        "docs/DATABASE-REVIEW.md §1 disagrees with the `__tablename__` declarations "
        f"under {CONTEXTS.relative_to(ROOT).as_posix()}:\n" + "\n".join(drift) + "\n\n"
        "Edit the `| Context | Tables |` table in §1 of docs/DATABASE-REVIEW.md."
    )


def test_the_stated_table_and_context_counts_match_the_table() -> None:
    """The sentence said **22 tables** across seven contexts, directly above 42 of them.

    Two figures a reader takes on trust because they sit above the evidence that
    contradicts them, and nothing made anyone look down.
    """
    section = _section()
    declared = _tablenames_by_context()
    n_tables = sum(len(tables) for tables in declared.values())
    n_contexts = len(declared)

    contexts_word = NUMBER_WORDS.get(n_contexts)
    assert contexts_word, f"extend NUMBER_WORDS in {__file__} to cover {n_contexts}"

    assert f"**{n_tables} tables**" in _flat(section), (
        f"docs/DATABASE-REVIEW.md §1 does not say `**{n_tables} tables**`, and "
        f"{n_tables} is what `contexts/` declares. Correct the sentence at the top of §1."
    )
    assert f"across {contexts_word} contexts" in _flat(section), (
        f"docs/DATABASE-REVIEW.md §1 does not say `across {contexts_word} contexts`, and "
        f"{n_contexts} contexts declare a table. Correct the sentence at the top of §1."
    )


def test_the_migration_count_matches_the_versions_directory() -> None:
    """A migration count is the one figure in §1 that changes on somebody else's branch.

    ADR-0008 makes Alembic the only schema mechanism, so this number moves every time
    the schema does — which is exactly why nobody thinks to re-read §1 when it happens.
    """
    count = len(list(VERSIONS.glob("*.py")))
    word = NUMBER_WORDS.get(count)
    assert word, f"extend NUMBER_WORDS in {__file__} to cover {count}"

    assert f"by {word} Alembic migrations" in _flat(_section()), (
        f"docs/DATABASE-REVIEW.md §1 does not say `by {word} Alembic migrations`, and "
        f"{VERSIONS.relative_to(ROOT).as_posix()} holds {count} files. "
        "Correct the sentence at the top of §1."
    )


def test_the_contexts_said_to_own_no_tables_own_none() -> None:
    """`pricing` and `scheduling` owning nothing is a design claim, not a coincidence.

    ADR-0002 makes them pure functions and import-linter stops `pricing` importing
    SQLAlchemy at all. A `__tablename__` appearing in either is the design changing,
    and §1 asserting the opposite in prose is how that change goes unremarked.
    """
    section = _section()
    said_empty = _contexts_declared_empty(section)
    assert said_empty, (
        "could not read the 'own no tables at all' sentence out of §1 — if it was "
        f"reworded, update the regex in {__file__} rather than dropping the check."
    )

    declared = _tablenames_by_context()
    actually_empty = _context_directories() - set(declared)

    assert said_empty == actually_empty, (
        f"docs/DATABASE-REVIEW.md §1 says {sorted(said_empty)} own no tables; the "
        f"contexts that actually declare none are {sorted(actually_empty)}. "
        f"Contexts that gained a table: {sorted(said_empty - actually_empty)}. "
        f"Contexts now empty and unmentioned: {sorted(actually_empty - said_empty)}."
    )


def test_the_orm_metadata_agrees_with_the_declarations_under_contexts() -> None:
    """A models module missing from `printorian/models.py` is invisible to `alembic check`.

    The AST scan reads the files; the metadata only knows what got imported. When they
    disagree, the table exists in the code, has no migration, and every gate is green —
    which is the failure `tests/test_schema_contracts.py` opens by warning about. This
    also keeps the two figures §1 carries honest: if the counts above were derived from
    the metadata alone, an unimported context would simply vanish from the document.
    """
    from_source = {table for tables in _tablenames_by_context().values() for table in tables}
    from_metadata = set(Base.metadata.tables)

    unregistered = sorted(from_source - from_metadata)
    unsourced = sorted(from_metadata - from_source)

    assert not unregistered, (
        f"declared under contexts/ but absent from the ORM metadata: {unregistered}. "
        "Add the models module to printorian/models.py — until then `alembic check` "
        "cannot see the table either."
    )
    assert not unsourced, (
        f"on the ORM metadata but declared nowhere under contexts/: {unsourced}. "
        f"If a table now lives outside contexts/, the scan in {__file__} has to learn "
        "about it, or §1's inventory silently stops covering the schema."
    )


def test_the_check_can_actually_see_a_stale_table() -> None:
    """A guard that cannot fail is not a guard.

    A regex that matches nothing passes on every codebase, and this whole file exists
    because silent agreement is what let §1 sit wrong for months. So the parser is run
    against a known-bad section: if the table slicer or the sentence regex stops
    working, this fails here instead of turning the gate above into a no-op.
    """
    synthetic = (
        "## 1. Shape\n\n"
        "One PostgreSQL database. **2 tables** across one context, built\n"
        "by three Alembic migrations on a single linear head.\n\n"
        "| Context | Tables |\n"
        "|---|---|\n"
        "| `identity` | `users`, `sessions` |\n\n"
        "`pricing` and `scheduling` own no tables at all.\n"
    )

    assert _parse_context_table(synthetic) == {"identity": {"users", "sessions"}}
    assert _contexts_declared_empty(synthetic) == {"pricing", "scheduling"}

    # Both directions, because a substring check that matched anything would pass the
    # first half of each pair while accepting a document that had gone stale.
    assert "**2 tables**" in _flat(synthetic)
    assert "**3 tables**" not in _flat(synthetic)
    assert f"by {NUMBER_WORDS[4]} Alembic migrations" not in _flat(synthetic)
    assert f"by {NUMBER_WORDS[3]} Alembic migrations" in _flat(synthetic)


@pytest.mark.parametrize(
    "target",
    [DOC, CONTEXTS, VERSIONS],
    ids=["docs/DATABASE-REVIEW.md", "printorian/contexts", "alembic/versions"],
)
def test_every_tree_that_is_scanned_exists(target: Path) -> None:
    """Catches a rename turning the scan into a no-op over a missing path.

    Asserted rather than skipped. A `pytest.skip` on a missing directory is how a gate
    stops running with nobody noticing, which is the same silence this file was written
    to end (CLAUDE.md §5). CI checks out the whole repository, so nothing is lost.
    """
    assert target.exists(), f"{target} is gone — the gate above is now checking nothing"


def test_the_section_parser_found_something_in_the_real_document() -> None:
    """§1 renamed or rewrapped must fail loudly, not quietly parse to an empty grid."""
    assert (ROOT / "docs").is_dir(), f"ROOT resolved to {ROOT}, which has no docs/"
    parsed = _parse_context_table(_section())
    assert parsed, "could not read a single row out of §1"
