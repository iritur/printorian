---
name: printorian-pipe-tail-trap
description: "Piping command output through tail returns tail's exit code — `npm ci | tail` once reported success while the install had failed and every later check ran against stale node_modules; the house pattern is `cmd > /tmp/out.log 2>&1; echo \"exit=$?\"`."
metadata:
  node_type: memory
  type: project
---

**Why:** The exit code of a pipeline is the last command's. An agent (or
human) that greps a log for "error", finds none in the surviving lines,
and moves on has just green-lit a broken environment — and the failure
surfaces much later, somewhere unrelated.

**How to apply:** Never gate anything on a piped command. Capture to a
file, echo the exit code, read the file for the counts:

```bash
cmd > /tmp/out.log 2>&1; echo "exit=$?"
```

This is binding for the harness's tester agent and both change workflows
(`world.run` returns real exit codes — do not wrap its output in shell
pipes either).

Evidence: CLAUDE.md §4 ("Two traps that cost time on both sides"),
frontend/CLAUDE.md gates note. The incident: `npm ci | tail` reported
success on a failed install; all subsequent checks ran against stale
`node_modules`.

Related: [[printorian-api-client-regen]].
