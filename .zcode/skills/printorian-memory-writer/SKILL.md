---
name: printorian-memory-writer
description: The format and rules for Printorian's .zcode/memories archive — when to write a memory record (and when not to), the exact template with frontmatter, how to index it in MEMORY.md, the HANDOFF.md dated-entry protocol, and the commit discipline (memory: subject, main checkout, never push). Use whenever a session learned something the next session should not have to re-learn the hard way.
---

# Writing memory records

The archive at `.zcode/memories/` is the harness's long-term evidence: hard
numbers, incidents, environment facts, disproven theories. It is
**historical** — a memory explains a rule, it never proves current behavior
(cite code for that).

## 1. When to write

Write when: a change landed with evidence worth quoting (measurements,
test counts, commit hashes); a number was measured that docs will want
later; research produced facts not recorded in `docs/`; an environment
quirk was discovered; **a mistake was made and fixed — these are the most
valuable records in the archive**; an owner decision was made that is not
yet (or never will be) an ADR.

Do not write when: the work was routine; the fact already lives in `docs/`
(link it from a memory only when the archive needs it for context); a
memory already covers it — **extend the existing record with a dated
section instead of forking a new one**.

## 2. Template (exact)

```markdown
---
name: printorian-<topic>
description: "One dense sentence with the numbers/dates/hosts a search would key on."
metadata:
  node_type: memory
  type: project
---

**Why:** <the general lesson>

**How to apply:** <imperative guidance>

<dated evidence sections; disproven theories listed explicitly>
Cross-reference with [[printorian-other-memory]] wiki-links.
```

Rules:

- The `description` is a search snippet — dates, numbers, host names,
  commit hashes belong *in it*, not only in the body.
- Disproven theories get written down; that is most of the archive's value.
- Cite the source of every fact (doc path, command, commit). A memory with
  an uncited number violates ADR-0007's spirit.
- 10–40 lines is the archive's voice; a memory that needs more is two
  memories (charters and closeout narratives are the accepted exceptions).

## 3. Indexing

One row in `.zcode/memories/MEMORY.md`, newest last:

```markdown
- [Title](printorian-<topic>.md) — one dense line with the dates/numbers/verdicts.
```

Keep rows dense; the index is grepped before anyone opens a record.

## 4. HANDOFF.md protocol

`HANDOFF.md` is the session-state document the whole repo reads (CLAUDE.md
makes it binding). When a studio run (or any harness session) finishes, it
appends — near the top, after the header — a dated section:

```markdown
## YYYY-MM-DD — <what this entry is about>

**As of:** YYYY-MM-DD · branched from `main` at <short-sha> (<subject>).

<what was done, verified how; what is deliberately unfinished; what needs
a person; issues filed or proposed.>
```

A stale handoff is worse than none — it is read as current, and this
repository has already been bitten twice by status documents that described
built features as missing. Write only what was verified, and name what was
not.

## 5. Committing

Memory and handoff changes are committed in the **main checkout** (not a
worktree), subject `memory: <topic>`, never pushed — they ride to the
remote with the owner's next approved push. When both a memory and a
HANDOFF entry are written, two commits: `memory: <topic>` and
`handoff: <one-line summary>`.
