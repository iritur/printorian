---
description: Run one task through the printorian-gated-change pipeline (preflight, six gates, implement, review, confirmations, full suite, PR text — never pushes)
argument-hint: <task text — what to change and the acceptance>
---

Launch the saved dynamic-workflow `printorian-gated-change` with the
CreateWorkflow tool, `saved` source, and these args:

```json
{ "task": "$ARGUMENTS", "branch": "zcode/gated-change", "issue": 0, "known_red": [], "allow_bootstrap": false }
```

The task text is the user's argument verbatim. If the user named an issue
number (e.g. "#27" or "issue 27"), set `"issue"` to that number and remove
it from the task text. If the user asked for a specific branch name, use it.

If the CreateWorkflow tool is not available in this session, say exactly
that and stop — do not run the pipeline manually, do not improvise a
substitute. Report the run id when submitted; do not poll it.
