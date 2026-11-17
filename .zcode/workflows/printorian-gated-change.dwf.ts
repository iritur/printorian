/* zcode-workflow
description: Single-task gated pipeline for Printorian — preflight, baseline gates, implement, fix loops, independent review with bounded confirmation, full-suite final gate, PR text. Never pushes.
whenToUse: One bug fix or small feature with a clear scope. Not for multi-task efforts (printorian-development-studio), doc drift checks (printorian-docs-audit), or questions (no workflow needed).
args:
  - name: task
    type: string
    description: The task text — what to change and the acceptance. An issue number inside the text is used for the commit/PR reference.
    required: true
  - name: branch
    type: string
    description: Working branch to create (must not already exist).
    default: zcode/gated-change
  - name: issue
    type: number
    description: GitHub issue this work closes, for the commit/PR reference. 0 = none.
    default: 0
  - name: known_red
    type: json
    description: JSON array of test node ids tolerated as red on main, each with its issue. A known_red test that goes green is reported, never silently kept.
    default: []
  - name: allow_bootstrap
    type: boolean
    description: Let the workflow run the documented DEVELOPMENT.md bootstrap (venv, pip -c constraints, npm ci, docker compose) when preflight finds the environment missing. false = ask first.
    default: false
*/

// ---------------------------------------------------------------------------
// Shared plumbing. Everything runs in the MAIN checkout (this workspace); the
// multi-worktree variant is printorian-development-studio. Commands run as
// bash -c so each gate resolves the host's venv layout itself
// (.zcode/skills/printorian-build-test SKILL §1).

const PYTEST_ENV =
  'PRINTORIAN_ENVIRONMENT=test PRINTORIAN_DATABASE_URL="postgresql+asyncpg://printorian:printorian@localhost:5433/printorian"';

const RESOLVE_PY =
  'PY=""; if [ -x backend/.venv/bin/python ]; then PY=backend/.venv/bin/python; LI=backend/.venv/bin/lint-imports; ' +
  'elif [ -f backend/.venv/Scripts/python.exe ]; then PY=backend/.venv/Scripts/python.exe; LI=backend/.venv/Scripts/lint-imports.exe; fi; ' +
  'if [ -z "$PY" ]; then echo "NO_VENV: bootstrap first (.zcode/skills/printorian-build-test SKILL §8)" >&2; exit 3; fi; ';

interface GateStep {
  name: string;
  script: string;
  timeoutMs: number;
}

/** The six source gates (backend/CLAUDE.md), each with its own exit code. */
function backendGates(): GateStep[] {
  return [
    { name: "ruff check", script: RESOLVE_PY + "cd backend && $PY -m ruff check .", timeoutMs: 180_000 },
    { name: "ruff format --check", script: RESOLVE_PY + "cd backend && $PY -m ruff format --check .", timeoutMs: 180_000 },
    { name: "mypy (strict)", script: RESOLVE_PY + "cd backend && $PY -m mypy", timeoutMs: 900_000 },
    { name: "lint-imports", script: RESOLVE_PY + "cd backend && $LI", timeoutMs: 300_000 },
    { name: "context isolation", script: RESOLVE_PY + "cd backend && $PY tools/check_context_isolation.py", timeoutMs: 180_000 },
    { name: "file length (400)", script: RESOLVE_PY + "cd backend && $PY tools/check_file_length.py", timeoutMs: 180_000 },
  ];
}

const BOOTSTRAP_SCRIPT =
  "set -e; " +
  'docker compose up -d postgres redis || echo "compose could not start services — start them another way (.zcode/skills/printorian-build-test SKILL §2) and rerun preflight"; ' +
  "if [ ! -x backend/.venv/bin/python ] && [ ! -f backend/.venv/Scripts/python.exe ]; then python3.13 -m venv backend/.venv; fi; " +
  "if [ -x backend/.venv/bin/python ]; then backend/.venv/bin/python -m pip install -e backend/.[dev] -c backend/constraints.txt && backend/.venv/bin/python -m alembic -c backend/alembic.ini upgrade head; fi; " +
  "cd frontend && npm ci";

const PREFLIGHT_SCRIPT =
  'V=ok; PG=ok; RD=ok; ' +
  'if [ ! -x backend/.venv/bin/python ] && [ ! -f backend/.venv/Scripts/python.exe ]; then V=missing; fi; ' +
  'if ! timeout 3 bash -c "exec 3<>/dev/tcp/127.0.0.1/5433" 2>/dev/null; then PG=down; fi; ' +
  'if ! timeout 3 bash -c "exec 3<>/dev/tcp/127.0.0.1/6380" 2>/dev/null; then RD=down; fi; ' +
  'echo "venv=$V postgres=$PG redis=$RD"; ' +
  'if [ "$V" = missing ] && [ "$PG" != ok ]; then exit 5; fi; ' +
  'if [ "$V" = missing ]; then exit 3; fi; ' +
  'if [ "$PG" != ok ] || [ "$RD" != ok ]; then exit 4; fi; exit 0';

interface EnvDecision {
  /** What to do about the missing environment. */
  action: "bootstrap" | "retry" | "abort";
  /** Optional note from the owner. */
  note?: string;
}

interface ImplResult {
  /** One paragraph: what was done. */
  summary: string;
  /** Paths changed (workspace-relative). */
  changedPaths: string[];
  /** Commit hashes created, oldest first. */
  commits: string[];
  /** Test files/dirs that prove the change; run scoped before the full suite. */
  testPaths: string[];
  /** Where the implementation deviated from the task text, and why. */
  deviations: string;
  /** What the reviewer should look at hardest. */
  hardest: string;
}

interface ReviewFinding {
  /** CRITICAL = data loss / money / security. MAJOR = wrong behavior or broken gate. */
  severity: "CRITICAL" | "MAJOR" | "MINOR" | "NOTE";
  /** path:line the finding is about. */
  where: string;
  /** One sentence: what is wrong, not how to fix it. */
  problem: string;
  /** What to do instead. */
  suggestion: string;
  /** How to see it yourself: files/lines to read and/or the one command that shows it. */
  readingList: string;
}

interface Review {
  /** Findings in severity order; an empty list means a clean review that read the diff. */
  findings: ReviewFinding[];
  /** One paragraph verdict. */
  verdict: string;
}

interface Confirmation {
  /** verdict on the finding as stated. */
  verdict: "confirmed" | "refuted" | "inconclusive";
  /** true when the defect exists at the diff base (not introduced by this change). */
  preexisting: boolean;
  /** One or two sentences of evidence: what was read or run. */
  evidence: string;
}

interface FixReport {
  /** What was fixed, one line per finding. */
  fixed: string[];
  /** New commits (hashes), oldest first. */
  commits: string[];
  /** What was not fixed and why (deferred, misread, needs owner). */
  notFixed: string[];
}

interface ReportFinding {
  where: string;
  what: string;
  evidence: string;
  status: "verified" | "unconfirmed";
  severity: "low" | "medium" | "high";
}

interface WorkflowReport {
  conclusion: string;
  findings: ReportFinding[];
  verified: string[];
  notCovered: string[];
}

interface GateOutcome {
  name: string;
  exitCode: number;
  tail: string;
}

/** Run one gate step; a nonzero exit is a value the caller branches on. */
async function runGate(step: GateStep): Promise<GateOutcome> {
  const r = await world.run("bash", ["-c", step.script], {
    timeoutMs: step.timeoutMs,
    output: { tail: 16_384 },
  });
  return { name: step.name, exitCode: r.exitCode, tail: r.stderr + r.stdout };
}

/** Keep implementer-provided test paths from carrying shell syntax into bash -c. */
function safePaths(paths: string[]): string[] {
  return paths.filter((p) => /^[A-Za-z0-9_\/.\- ]+$/.test(p.trim()) && p.trim().length > 0).map((p) => p.trim());
}

function sevToReport(sev: ReviewFinding["severity"]): "low" | "medium" | "high" {
  if (sev === "CRITICAL") return "high";
  if (sev === "MAJOR") return "medium";
  return "low";
}

const task = String(args.task ?? "").trim();
const branch = /^[A-Za-z0-9_\/.\-]+$/.test(String(args.branch ?? "")) ? String(args.branch) : "zcode/gated-change";
const issue = Number(args.issue ?? 0);
const knownRed = Array.isArray(args.known_red) ? args.known_red.map(String) : [];
const allowBootstrap = args.allow_bootstrap === true;

if (task === "") {
  return {
    conclusion: "No task text was given; nothing ran.",
    findings: [],
    verified: [],
    notCovered: ["everything — the task argument is required"],
  };
}

const notCovered: string[] = [];
const verified: string[] = [];
const reportFindings: ReportFinding[] = [];

// ---------------------------------------------------------------------------
phase("Check the environment is ready");
const pre = await world.run("bash", ["-c", PREFLIGHT_SCRIPT], { timeoutMs: 60_000 });
let envReady = pre.exitCode === 0;
if (!envReady) {
  if (allowBootstrap) {
    log(`preflight not green (${pre.stdout.trim()}) — allow_bootstrap is set, running the documented bootstrap`);
    await world.run("bash", ["-c", BOOTSTRAP_SCRIPT], { timeoutMs: 1_800_000, output: { tail: 8_192 } });
  } else {
    const decision = await hole<EnvDecision>(
      "Decide how to get a working environment",
      `Preflight says: ${pre.stdout.trim()}. Bootstrap commands are documented in ` +
        `.zcode/skills/printorian-build-test SKILL §8 (docker compose up, venv, pip -c constraints.txt, npm ci), ` +
        `or fix it manually and choose retry.`,
    );
    if (decision.action === "bootstrap") {
      await world.run("bash", ["-c", BOOTSTRAP_SCRIPT], { timeoutMs: 1_800_000, output: { tail: 8_192 } });
    } else if (decision.action === "retry") {
      const again = await world.run("bash", ["-c", PREFLIGHT_SCRIPT], { timeoutMs: 60_000 });
      envReady = again.exitCode === 0;
      if (!envReady) {
        return {
          conclusion: `Environment still not ready after retry (${again.stdout.trim()}); nothing ran.`,
          findings: [],
          verified: ["environment preflight ran twice"],
          notCovered: ["everything — environment missing"],
        };
      }
    }
  }
  if (envReady || allowBootstrap) {
    const recheck = await world.run("bash", ["-c", PREFLIGHT_SCRIPT], { timeoutMs: 60_000 });
    envReady = recheck.exitCode === 0;
    if (!envReady) {
      return {
        conclusion: `Bootstrap ran but preflight is still red: ${recheck.stdout.trim()}. Nothing else ran.`,
        findings: [],
        verified: ["bootstrap", "post-bootstrap preflight"],
        notCovered: ["everything — environment still missing"],
      };
    }
  }
}
if (!envReady && pre.exitCode === 0) envReady = true;

// ---------------------------------------------------------------------------
phase("Verify a clean tree and a green baseline");
const status = await git.status();
if (!status.clean) {
  return {
    conclusion:
      `Working tree is dirty (${status.unstaged.length + status.untracked.length} unstaged/untracked paths); ` +
      `commit or stash first — the pipeline refuses to mix with unrelated changes.`,
    findings: [],
    verified: ["git status"],
    notCovered: ["everything — dirty tree"],
  };
}
const baseLog = await git.log(1);
const baseSha = baseLog[0]?.hash ?? "HEAD";
const baseSubject = baseLog[0]?.subject ?? "";

for (const gate of backendGates()) {
  const g = await runGate(gate);
  verified.push(`baseline gate ${gate.name}: exit ${g.exitCode}`);
  if (g.exitCode !== 0) {
    return {
      conclusion:
        `Baseline is red before any change: ${gate.name} exited ${g.exitCode}. Fix main first.\n${g.tail.slice(-2000)}`,
      findings: [
        {
          where: "(baseline)",
          what: `${gate.name} fails on ${baseSubject}`,
          evidence: g.tail.slice(-2000),
          status: "verified",
          severity: "high",
        },
      ],
      verified,
      notCovered: ["the task — refused to start on a red baseline"],
    };
  }
}

// ---------------------------------------------------------------------------
phase("Create the working branch");
const branchResult = await world.run("bash", ["-c", `git checkout -b ${branch}`], { timeoutMs: 60_000 });
if (branchResult.exitCode !== 0) {
  return {
    conclusion: `Could not create branch ${branch} (it may already exist): ${branchResult.stderr.slice(0, 500)}`,
    findings: [],
    verified,
    notCovered: ["the task — branch creation failed"],
  };
}

// ---------------------------------------------------------------------------
phase("Implement the task");
const implementer = agent("Implementer", {
  system:
    "You are the implementation stage of Printorian's development studio. Read first, binding: " +
    "AGENTS.md (§1 owner rules, §4 session protocol); backend/CLAUDE.md and/or frontend/CLAUDE.md for your scope; " +
    ".zcode/skills/printorian-build-test/SKILL.md for exact commands. " +
    "Rules: write only what the task asks (no unasked surface, no drive-by refactors); read a neighbouring file " +
    "before writing a new one and match the prose-heavy comment voice (why + alternative + cost); new functionality " +
    "arrives with its test, a bug fix with the failing test first; run only narrow self-checks (ruff, mypy, the one " +
    "test file) — the script re-gates the full suite after you; Alembic touches the dev DB, check alembic current " +
    "before any downgrade; commit as a sentence with the issue reference, never push, never amend reviewed commits. " +
    "If blocked by a rule, return with deviations saying so instead of improvising. " +
    "If a check is impossible to pass or your instructions contradict each other, escalate and say so plainly rather than working around it.",
});
const impl = await implementer.ask<ImplResult>(
  `Task: ${task}\nBranch: ${branch} (already checked out). Issue reference for commits: ${issue > 0 ? `#${issue}` : "none"}.\n` +
    `Implement it, add the proving test, commit. Return the contract.`,
);
log(`Implementation: ${impl.commits.length} commits, ${impl.changedPaths.length} paths changed`);
if (impl.deviations.trim() !== "") {
  reportFindings.push({
    where: "(plan)",
    what: `Implementation deviated: ${impl.deviations}`,
    evidence: "implementer handoff",
    status: "unconfirmed",
    severity: "low",
  });
}

const scopedTests = safePaths(impl.testPaths);
if (scopedTests.length !== impl.testPaths.length) {
  notCovered.push(
    `${impl.testPaths.length - scopedTests.length} test path(s) from the handoff were dropped as unsafe for shell interpolation`,
  );
}

// ---------------------------------------------------------------------------
phase("Run the fast gates and fix what they catch");
const fixer = agent("Fixer", {
  system:
    "You repair a Printorian change so a specific gate passes, without widening the diff: fix the cause the gate " +
    "names, never the gate (no noqa, no threshold change, no skipped test — removing the cause beats silencing the " +
    "symptom). Read the area's CLAUDE.md first. Commit each fix as its own sentence. Never push. " +
    "If the gate is wrong rather than the code, return fixed: [] and say so in notFixed. " +
    "If a fix is impossible or instructions contradict, escalate rather than working around them.",
});
let gatesGreen = false;
for (let round = 1; round <= 3; round++) {
  const failures: string[] = [];
  for (const gate of backendGates()) {
    const g = await runGate(gate);
    verified.push(`round ${round} gate ${gate.name}: exit ${g.exitCode}`);
    if (g.exitCode !== 0) failures.push(`--- ${gate.name} (exit ${g.exitCode}) ---\n${g.tail.slice(-3000)}`);
  }
  if (failures.length === 0) {
    gatesGreen = true;
    break;
  }
  if (round === 3) break;
  await fixer.ask<FixReport>(
    `Fast gates failed on round ${round}:\n${failures.join("\n")}\n` +
      `Fix the causes and commit. Task context: ${impl.summary}`,
  );
}
if (!gatesGreen) {
  return {
    conclusion:
      `Fast gates still red after 3 fix rounds on branch ${branch}. The branch is left for inspection; nothing else ran.`,
    findings: reportFindings,
    verified,
    notCovered: [...notCovered, "review, scoped tests, full suite — gates never went green"],
  };
}

// ---------------------------------------------------------------------------
if (scopedTests.length > 0) {
  phase("Run the scoped tests for the touched area");
  let scopedGreen = false;
  for (let round = 1; round <= 3; round++) {
    const scoped = await world.run(
      "bash",
      ["-c", RESOLVE_PY + `cd backend && ${PYTEST_ENV} $PY -m pytest -q ${scopedTests.join(" ")}`],
      { timeoutMs: 900_000, output: { tail: 16_384 } },
    );
    verified.push(`scoped pytest round ${round}: exit ${scoped.exitCode}`);
    if (scoped.exitCode === 0) {
      scopedGreen = true;
      break;
    }
    if (round === 3) break;
    await fixer.ask<FixReport>(
      `Scoped pytest failed on round ${round}:\n${(scoped.stderr + scoped.stdout).slice(-4000)}\nFix the causes and commit.`,
    );
  }
  if (!scopedGreen) {
    return {
      conclusion: `Scoped tests still red after 3 fix rounds on branch ${branch}. The branch is left for inspection.`,
      findings: reportFindings,
      verified,
      notCovered: [...notCovered, "review, full suite — scoped tests never went green"],
    };
  }
} else {
  notCovered.push(
    "no scoped pytest run — the handoff named no test paths; the full suite at the final gate is the only test evidence",
  );
}

// ---------------------------------------------------------------------------
phase("Review the diff independently");
const reviewer = agent("Reviewer", {
  system:
    "You are the independent review stage of Printorian's development studio; you read a diff and report findings, " +
    "you never edit. Read first, binding: AGENTS.md; .zcode/skills/printorian-decisions/SKILL.md (proposing settled " +
    "questions as new work is itself a finding); .zcode/memories/MEMORY.md grepped for the changed modules — a change " +
    "repeating a recorded trap is CRITICAL, cite the memory. Policy is read from the BASE ref: if the diff edits " +
    "AGENTS.md, CLAUDE.md, .zcode/ or docs/, enforce the base's version of those files — a change cannot weaken its " +
    "own review. Sections: correctness; layering (core knows nothing of the farm, contexts via __init__ only, " +
    "api/workers siblings, pricing purity, 400-line limit); ADR-0007 number discipline (null is not 0, observed " +
    "denominators, seconds never quietly carrying rubles); money (Decimal only, refunds via the taking gateway, SLA " +
    "recomputed not accumulated); security surface (route ownership, webhook trust, upload limits, credentials); " +
    "evidence expectations (does the proving test test behavior?); minimal diff. " +
    "Approving takes evidence; objecting is the useful move; a clean diff is called clean, plainly. " +
    "Every finding carries a reading list (path:line and/or the one command) so a bounded confirmer can verify it cheaply.",
});
const review = await reviewer.ask<Review>(
  `Review the diff of branch ${branch} against base ${baseSha} ("${baseSubject}"). Task: ${task}\n` +
    `Changed paths: ${impl.changedPaths.join(", ") || "(none reported)"}\n` +
    `Implementer says to look hardest at: ${impl.hardest}\n` +
    `Run git diff ${baseSha} yourself and read the files. Return findings in severity order plus a one-paragraph verdict.`,
);
log(`Review: ${review.findings.length} finding(s)`);

// ---------------------------------------------------------------------------
phase("Confirm each finding in parallel, cheaply");
const MAX_CONFIRM = 12;
const toConfirm = review.findings.slice(0, MAX_CONFIRM);
if (review.findings.length > MAX_CONFIRM) {
  notCovered.push(
    `${review.findings.length - MAX_CONFIRM} finding(s) beyond the confirmation cap of ${MAX_CONFIRM} are reported unconfirmed`,
  );
}
// Join: the dispositions below need every confirmation of this change together.
const confirmations = await Promise.all(
  toConfirm.map((f, i) =>
    agent(`Confirmer ${i + 1}`, {
      model: "GLM-5.3-Flash",
      budget: { webSearch: 0 },
      system:
        "You verify ONE review finding against the Printorian workspace, from its evidence alone. Read exactly what " +
        "the reading list names (and run its command if it names one — read-only; never edit anything). Decide: " +
        "confirmed, refuted, or inconclusive — an honest inconclusive beats a grind. Check preexisting by looking at " +
        "the base ref (git show <base>:<path>) when it matters. At most ONE follow-up read beyond the reading list; " +
        "then conclude.",
    }).ask<Confirmation>(`Finding: ${JSON.stringify(f)}\nBase ref: ${baseSha}. Verify it from the reading list alone.`),
  ),
);
const confirmedOnChange: ReviewFinding[] = [];
const confirmedPreexisting: ReviewFinding[] = [];
toConfirm.forEach((f, i) => {
  const c = confirmations[i];
  if (c.verdict === "confirmed") {
    (c.preexisting ? confirmedPreexisting : confirmedOnChange).push(f);
    reportFindings.push({
      where: f.where,
      what: `${f.problem} → ${f.suggestion}`,
      evidence: `confirmed (${c.evidence})${c.preexisting ? " — pre-existing at base" : ""}`,
      status: "verified",
      severity: sevToReport(f.severity),
    });
  } else if (c.verdict === "refuted") {
    reportFindings.push({
      where: f.where,
      what: `Review finding refuted: ${f.problem}`,
      evidence: c.evidence,
      status: "verified",
      severity: "low",
    });
  } else {
    reportFindings.push({
      where: f.where,
      what: `${f.problem} → ${f.suggestion}`,
      evidence: `inconclusive confirmation (${c.evidence}); kept for human eyes`,
      status: "unconfirmed",
      severity: sevToReport(f.severity),
    });
  }
});

// ---------------------------------------------------------------------------
phase("Fix the confirmed findings and re-check");
if (confirmedOnChange.length > 0) {
  const fixReport = await fixer.ask<FixReport>(
    `Fix these confirmed findings (they are in the change, not pre-existing):\n` +
      confirmedOnChange.map((f) => `- [${f.severity}] ${f.where}: ${f.problem} → ${f.suggestion}`).join("\n") +
      `\nCommit each fix as its own sentence on ${branch}.`,
  );
  log(`Fixer: ${fixReport.fixed.length} fixed, ${fixReport.notFixed.length} not fixed`);
  let regreen = false;
  for (let round = 1; round <= 2; round++) {
    const failures: string[] = [];
    for (const gate of backendGates()) {
      const g = await runGate(gate);
      if (g.exitCode !== 0) failures.push(`--- ${gate.name} ---\n${g.tail.slice(-3000)}`);
    }
    if (scopedTests.length > 0) {
      const scoped = await world.run(
        "bash",
        ["-c", RESOLVE_PY + `cd backend && ${PYTEST_ENV} $PY -m pytest -q ${scopedTests.join(" ")}`],
        { timeoutMs: 900_000, output: { tail: 16_384 } },
      );
      if (scoped.exitCode !== 0) failures.push(`--- scoped pytest ---\n${(scoped.stderr + scoped.stdout).slice(-3000)}`);
    }
    if (failures.length === 0) {
      regreen = true;
      verified.push("post-fix re-gate: fast gates + scoped pytest green");
      break;
    }
    if (round === 2) break;
    await fixer.ask<FixReport>(`Post-fix gates still red:\n${failures.join("\n")}\nFix the causes and commit.`);
  }
  if (!regreen) {
    return {
      conclusion:
        `Confirmed findings were applied but the gates did not recover within 2 rounds on ${branch}. Branch left for inspection.`,
      findings: reportFindings,
      verified,
      notCovered: [...notCovered, "full suite — post-fix gates never went green"],
    };
  }
  const delta = await reviewer.ask<Review>(
    `Delta re-review on ${branch} versus base ${baseSha}: the commits after the findings were fixed. ` +
      `Check ONLY that the fixes hold and break nothing adjacent — do not re-raise findings you already reported.`,
  );
  delta.findings
    .filter((f) => f.severity === "CRITICAL" || f.severity === "MAJOR")
    .forEach((f) =>
      reportFindings.push({
        where: f.where,
        what: `Delta re-review: ${f.problem}`,
        evidence: f.readingList,
        status: "unconfirmed",
        severity: sevToReport(f.severity),
      }),
    );
}
if (confirmedPreexisting.length > 0) {
  notCovered.push(
    `${confirmedPreexisting.length} confirmed pre-existing defect(s) recorded, not fixed (fixing would widen the diff): ` +
      confirmedPreexisting.map((f) => `${f.where} — ${f.problem}`).join("; "),
  );
}

// ---------------------------------------------------------------------------
phase("Run the final gate: full suite and the rest of CI's shape");
for (const gate of backendGates()) {
  const g = await runGate(gate);
  verified.push(`final gate ${gate.name}: exit ${g.exitCode}`);
  if (g.exitCode !== 0) {
    return {
      conclusion:
        `Final gate red: ${gate.name} exited ${g.exitCode} on ${branch}. Branch left for inspection.\n${g.tail.slice(-2000)}`,
      findings: reportFindings,
      verified,
      notCovered: [...notCovered, "full pytest suite — a fast gate failed first"],
    };
  }
}
const touchesBackend = impl.changedPaths.some((p) => p.startsWith("backend/"));
const touchesFrontend = impl.changedPaths.some((p) => p.startsWith("frontend/"));
const touchesSchema = touchesBackend && impl.changedPaths.some((p) => p.includes("alembic/") || p.endsWith("models.py"));
const touchesApi = touchesBackend && impl.changedPaths.some((p) => p.includes("/api/") || p.includes("schemas.py"));

if (touchesSchema) {
  const mig = await world.run("bash", ["-c", RESOLVE_PY + "cd backend && $PY -m alembic check"], {
    timeoutMs: 300_000,
    output: { tail: 8_192 },
  });
  verified.push(`alembic check: exit ${mig.exitCode}`);
  if (mig.exitCode !== 0) {
    return {
      conclusion:
        `Schema changed but alembic check is red (${(mig.stderr + mig.stdout).slice(-1500)}); the migration is not finished (ADR-0008).`,
      findings: reportFindings,
      verified,
      notCovered: [...notCovered, "full pytest suite — migration gate failed first"],
    };
  }
}

const full = await world.run("bash", ["-c", RESOLVE_PY + `cd backend && ${PYTEST_ENV} $PY -m pytest -q`], {
  timeoutMs: 1_800_000,
  output: { tail: 16_384 },
});
const summaryLine =
  (full.stdout + full.stderr).split("\n").filter((l) => /passed|failed|error/i.test(l)).pop() ?? "(no summary line printed)";
verified.push(`full pytest suite: exit ${full.exitCode} — ${summaryLine.trim()}`);
let suiteGreen = full.exitCode === 0;
if (!suiteGreen) {
  const failing = (full.stdout + full.stderr)
    .split("\n")
    .filter((l) => /^(FAILED|ERROR)/.test(l))
    .map((l) => l.split(" ")[1] ?? l);
  const novel = failing.filter((id) => !knownRed.includes(id));
  const wentGreen = knownRed.filter((id) => !failing.includes(id));
  if (wentGreen.length > 0) {
    notCovered.push(`known_red entries now green (stale — remove them): ${wentGreen.join(", ")}`);
  }
  if (novel.length === 0 && failing.length > 0) {
    suiteGreen = true;
    notCovered.push(`full suite red only in known_red tests: ${failing.join(", ")}`);
  } else {
    return {
      conclusion:
        `Full suite red on ${branch}: ${novel.slice(0, 10).join(", ")}${novel.length > 10 ? ` (+${novel.length - 10} more)` : ""}. ` +
        `Branch left for inspection.\n${summaryLine}`,
      findings: reportFindings,
      verified,
      notCovered: [...notCovered, touchesFrontend ? "frontend gates — suite failed first" : "frontend gates (not applicable)"],
    };
  }
}

if (touchesApi) {
  const regen = await world.run(
    "bash",
    [
      "-c",
      RESOLVE_PY +
        "cd backend && $PY tools/export_openapi.py --out openapi.json && cd ../frontend && npm run generate:api && npm run typecheck",
    ],
    { timeoutMs: 900_000, output: { tail: 16_384 } },
  );
  verified.push(`api-client regen + frontend typecheck: exit ${regen.exitCode}`);
  if (regen.exitCode !== 0) {
    return {
      conclusion:
        `API surface changed but client regeneration/typecheck failed (${(regen.stderr + regen.stdout).slice(-1500)}). ` +
        `Remember apps/console/src/dashboard/types.ts mirrors GET /dashboard by hand.`,
      findings: reportFindings,
      verified,
      notCovered: [...notCovered, "frontend lint/test/build — typecheck failed first"],
    };
  }
}
if (touchesFrontend) {
  const frontendGateScripts: [string, string][] = [
    ["typecheck", "npm run typecheck"],
    ["lint", "npm run lint"],
    ["test", "npm run test"],
    ["build", "npm run build"],
  ];
  for (const fg of frontendGateScripts) {
    const g = await world.run("bash", ["-c", `cd frontend && ${fg[1]}`], { timeoutMs: 900_000, output: { tail: 8_192 } });
    verified.push(`frontend ${fg[0]}: exit ${g.exitCode}`);
    if (g.exitCode !== 0) {
      return {
        conclusion: `Frontend gate ${fg[0]} red on ${branch}.\n${(g.stderr + g.stdout).slice(-1500)}`,
        findings: reportFindings,
        verified,
        notCovered: [...notCovered, `frontend gates after ${fg[0]}`],
      };
    }
  }
}

// ---------------------------------------------------------------------------
phase("Write the handoff and let a fresh reader check it");
const proving = scopedTests.length > 0 ? scopedTests.join(", ") : "the full suite (no scoped tests were named)";
const prText = [
  `## ${task.split("\n")[0]}`,
  "",
  impl.summary,
  "",
  `**Proving test:** ${proving} — full suite: ${summaryLine.trim()}`,
  ...(issue > 0 ? ["", `Closes #${issue}`] : []),
  "",
  `**Commits:** ${impl.commits.join(", ") || "(see branch)"}`,
  ...(impl.deviations.trim() !== "" ? ["", `**Deviations:** ${impl.deviations}`] : []),
  ...(confirmedPreexisting.length > 0
    ? [
        "",
        `**Pre-existing defects found** (issue-worthy, not fixed here): ` +
          confirmedPreexisting.map((f) => `${f.where} — ${f.problem}`).join("; "),
      ]
    : []),
].join("\n");

const reader = agent("Handoff reader", {
  model: "GLM-5.3-Flash",
  budget: { webSearch: 0 },
  system:
    "You read a handoff document for unsupported claims — from the text alone, without checking the code. " +
    "List: any claim the text does not support with evidence it names, any number without a source, anything a " +
    "reviewer will ask that the text does not answer. Plain list; an empty list means it holds.",
});
const handoffCheck = await reader.ask<string>(`Handoff to check:\n${prText}`);
const handoffFixes = handoffCheck.trim() === "" ? "" : `\n\n**Reader notes (unaddressed):** ${handoffCheck}`;

await artifact.markdown(
  "change-report",
  [
    `# Gated change: ${task.split("\n")[0]}`,
    "",
    `Branch \`${branch}\` from \`${baseSha}\` ("${baseSubject}").`,
    "",
    impl.summary,
    "",
    "## Gates",
    "",
    ...verified.map((v) => `- ${v}`),
    "",
    "## Findings ledger",
    "",
    ...(reportFindings.length === 0 ? ["- none"] : reportFindings.map((f) => `- [${f.severity}/${f.status}] ${f.where}: ${f.what}`)),
    ...(notCovered.length > 0 ? ["", "## Not covered", "", ...notCovered.map((n) => `- ${n}`)] : []),
  ].join("\n"),
  { title: "Change report", description: "Gates, findings and dispositions for this change.", primary: true },
);
await artifact.markdown("pr-text", prText + handoffFixes, {
  title: "Pull-request text (not pushed)",
  description: "Ready to paste; the proving test is named per the PR template.",
});

return {
  conclusion:
    `READY: ${task.split("\n")[0]} on branch ${branch} (${impl.commits.length} commits). ` +
    `Full suite: ${summaryLine.trim()}. ${review.findings.length} review finding(s): ${confirmedOnChange.length} fixed, ` +
    `${confirmedPreexisting.length} pre-existing recorded, the rest refuted or unconfirmed. PR text is in the artifact — nothing was pushed.`,
  findings: reportFindings,
  verified,
  notCovered,
};
