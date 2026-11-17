---
description: Sweep docs/ for drift against the code (one bounded auditor per document, confirmed findings, optional drift-issue drafts with approval)
argument-hint: [doc paths as JSON array — empty = all top-level docs/*.md] [--adr] [--file]
---

Launch the saved dynamic-workflow `printorian-docs-audit` with the
CreateWorkflow tool, `saved` source, and these args:

```json
{ "docs": [], "include_adr": false, "file_issues": false }
```

Parse the user's argument: if it contains doc paths (e.g.
`docs/ARCHITECTURE.md`), put them in `"docs"` as a JSON array; `--adr` sets
`"include_adr": true`; `--file` sets `"file_issues": true` (issues are
still only drafted after explicit approval inside the run).

If the CreateWorkflow tool is not available in this session, say exactly
that and stop. Report the run id when submitted; do not poll it.
