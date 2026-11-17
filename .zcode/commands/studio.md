---
description: Run a multi-slice request through the printorian-development-studio (plan → task cards → per-task gated pipeline → one full-suite run → memory + HANDOFF)
argument-hint: <the overall request or issue text>
---

Launch the saved dynamic-workflow `printorian-development-studio` with the
CreateWorkflow tool, `saved` source, and these args:

```json
{ "task": "$ARGUMENTS", "base": "HEAD", "branch_prefix": "zcode/task", "max_tasks": 5, "issue": 0, "known_red": [], "allow_bootstrap": false }
```

The task text is the user's argument verbatim. If the user named an issue
number, set `"issue"` to it. Linux host only (git worktrees + Docker).

If the CreateWorkflow tool is not available in this session, say exactly
that and stop — no manual orchestration, no substitutes. Report the run id
when submitted; do not poll it.
