/* zcode-workflow
description: Multi-task orchestrator for Printorian — plans the request into task cards, then per task (sequentially, one git worktree each) runs baseline gates, implementation, fix loops, independent review with bounded confirmation, and a gatekeeper audit; ends with one full-suite run, a memory+HANDOFF record, and per-task PR text. Never pushes.
whenToUse: A multi-slice effort — an issue with several deliverables, a refactor with steps, a feature spanning backend and frontend. Linux host only (git worktrees + Docker). Not for single small changes (printorian-gated-change), docs drift (printorian-docs-audit), or research.
args:
  - name: task
    type: string
    description: The overall request or issue text. Its "Done when" (if any) is the acceptance target.
    required: true
  - name: base
    type: string
    description: Base ref every task chain starts from.
    default: HEAD
  - name: branch_prefix
    type: string
    description: Branch prefix per task card; the card id is appended.
    default: zcode/task
  - name: max_tasks
    type: number
    description: Upper bound of task cards executed (planner may propose more; the rest are reported as not covered).
    default: 5
  - name: issue
    type: number
    description: GitHub issue this work closes, added to each PR text. 0 = none.
    default: 0
  - name: known_red
    type: json
    description: JSON array of test node ids tolerated as red, each with its issue. Reported if they go green.
    default: []
  - name: allow_bootstrap
    type: boolean
    description: Let the workflow run the documented bootstrap when preflight finds the environment missing. false = ask first.
    default: false
*/

// ---------------------------------------------------------------------------
// The studio. Tasks run STRICTLY SEQUENTIALLY (they share the printorian_test
// database — AGENTS.md rule 5) in one git worktree each under WT_ROOT, reusing
// the MAIN checkout's venv with the worktree as cwd (import-resolution is
// verified per task; see .zcode/skills/printorian-build-test SKILL §4).

const WT_ROOT = "/home/dimmus/printorian-wt";
const PYTEST_ENV =
  'PRINTORIAN_ENVIRONMENT=test PRINTORIAN_DATABASE_URL="postgresql+asyncpg://printorian:printorian@localhost:5433/printorian"';

const PREFLIGHT_SCRIPT =
  'V=ok; PG=ok; RD=ok; ' +
  'if [ ! -x backend/.venv/bin/python ]; then V=missing; fi; ' +
  'if ! timeout 3 bash -c "exec 3<>/dev/tcp/127.0.0.1/5433" 2>/dev/null; then PG=down; fi; ' +
  'if ! timeout 3 bash -c "exec 3<>/dev/tcp/127.0.0.1/6380" 2>/dev/null; then RD=down; fi; ' +
  'echo "venv=$V postgres=$PG redis=$RD"; ' +
  'if [ "$V" = missing ] && [ "$PG" != ok ]; then exit 5; fi; ' +
  'if [ "$V" = missing ]; then exit 3; fi; ' +
  'if [ "$PG" != ok ] || [ "$RD" != ok ]; then exit 4; fi; exit 0';

const BOOTSTRAP_SCRIPT =
  "set -e; " +
  'docker compose up -d postgres redis || echo "compose could not start services — start them another way (.zcode/skills/printorian-build-test SKILL §2) and rerun preflight"; ' +
  "python3.13 -m venv backend/.venv; " +
  "backend/.venv/bin/python -m pip install -e backend/.[dev] -c backend/constraints.txt && " +
  "backend/.venv/bin/python -m alembic -c backend/alembic.ini upgrade head; " +
  "cd frontend && npm ci";

interface EnvDecision {
  action: "bootstrap" | "retry" | "abort";
  note?: string;
}

interface TaskCard {
  /** Short slug, e.g. "payments-holds". */
  id: string;
  /** One sentence: what this task delivers. */
  title: string;
  /** Paths this task may write (prefixes are fine). */
  scope: string[];
  /** "Done when" for this task, from the issue when there is one. */
  acceptance: string;
  /** ids of tasks that must be READY before this one starts. */
  dependsOn: string[];
  /** Test files/dirs that will prove this task. */
  testPaths: string[];
  /** Named risks with their stop-points (where to ask instead of decide). */
  risks: string;
}

interface Plan {
  /** Restated objective and acceptance criteria. */
  objective: string;
  /** Minimal topologically-ordered tasks. */
  tasks: TaskCard[];
  /** Decisions the plan makes that the owner should veto. */
  ownerDecisions: string[];
  /** Explicit omissions. */
  omissions: string[];
}

interface ImplResult {
  summary: string;
  changedPaths: string[];
  commits: string[];
  testPaths: string[];
  deviations: string;
  hardest: string;
}

interface ReviewFinding {
  severity: "CRITICAL" | "MAJOR" | "MINOR" | "NOTE";
  where: string;
  problem: string;
  suggestion: string;
  readingList: string;
}

interface Review {
  findings: ReviewFinding[];
  verdict: string;
}

interface Confirmation {
  verdict: "confirmed" | "refuted" | "inconclusive";
  preexisting: boolean;
  evidence: string;
}

interface FixReport {
  fixed: string[];
  commits: string[];
  notFixed: string[];
}

interface GateVerdict {
  verdict: "READY" | "BLOCKED" | "NOT_READY";
  /** One line per check: required / present / status. */
  checks: string[];
  /** Findings still lacking a disposition, if any. */
  gaps: string[];
  /** What the owner is being asked to decide, if anything. */
  ownerAsk: string;
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

interface GateStep {
  name: string;
  script: string;
  timeoutMs: number;
}

function safeSlug(s: string): string {
  return /^[A-Za-z0-9_\/.\-]+$/.test(s) ? s : "";
}

function safePaths(paths: string[]): string[] {
  return paths.filter((p) => /^[A-Za-z0-9_\/.\- ]+$/.test(p.trim()) && p.trim().length > 0).map((p) => p.trim());
}

function sevToReport(sev: ReviewFinding["severity"]): "low" | "medium" | "high" {
  if (sev === "CRITICAL") return "high";
  if (sev === "MAJOR") return "medium";
  return "low";
}

const task = String(args.task ?? "").trim();
const baseRef = safeSlug(String(args.base ?? "")) || "HEAD";
const branchPrefix = safeSlug(String(args.branch_prefix ?? "")) || "zcode/task";
const maxTasks = Math.max(1, Math.min(Number(args.max_tasks ?? 5) || 5, 10));
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
const prTexts: string[] = [];

artifact.board("tasks", {
  key: "id",
  status: "status",
  columns: ["planned", "baseline", "implementing", "review", "verify", "gate", "done", "blocked"],
  cardTitle: "title",
  detail: [{ field: "branch", label: "Branch" }, { field: "note", label: "Note" }],
});

// ---------------------------------------------------------------------------
phase("Check the environment is ready");
const pre = await world.run("bash", ["-c", PREFLIGHT_SCRIPT], { timeoutMs: 60_000 });
if (pre.exitCode !== 0) {
  if (allowBootstrap) {
    log(`preflight not green (${pre.stdout.trim()}) — allow_bootstrap is set, running the documented bootstrap`);
    await world.run("bash", ["-c", BOOTSTRAP_SCRIPT], { timeoutMs: 1_800_000, output: { tail: 8_192 } });
  } else {
    const decision = await hole<EnvDecision>(
      "Decide how to get a working environment",
      `Preflight says: ${pre.stdout.trim()}. Bootstrap commands: .zcode/skills/printorian-build-test SKILL §8. ` +
        `The studio also needs a Linux host (git worktrees + Docker).`,
    );
    if (decision.action === "abort") {
      return {
        conclusion: "Aborted at preflight by owner decision; nothing ran.",
        findings: [],
        verified: ["environment preflight"],
        notCovered: ["everything — environment missing"],
      };
    }
    if (decision.action === "bootstrap") {
      await world.run("bash", ["-c", BOOTSTRAP_SCRIPT], { timeoutMs: 1_800_000, output: { tail: 8_192 } });
    }
  }
  const recheck = await world.run("bash", ["-c", PREFLIGHT_SCRIPT], { timeoutMs: 60_000 });
  if (recheck.exitCode !== 0) {
    return {
      conclusion: `Environment still not ready (${recheck.stdout.trim()}); nothing ran.`,
      findings: [],
      verified: ["environment preflight (and bootstrap if run)"],
      notCovered: ["everything — environment missing"],
    };
  }
}
const pwdRun = await world.run("bash", ["-c", "pwd"], { timeoutMs: 30_000 });
const MAIN = pwdRun.stdout.trim();
const MAIN_PY = `${MAIN}/backend/.venv/bin/python`;
const MAIN_LI = `${MAIN}/backend/.venv/bin/lint-imports`;
log(`main checkout: ${MAIN}`);

// ---------------------------------------------------------------------------
phase("Plan the work into tasks");
const planner = agent("Planner", {
  system:
    "You are the planning stage of Printorian's development studio; planning is read-only. Read first, binding: " +
    "AGENTS.md; docs/WORKFLOW.md (what closes an issue); .zcode/skills/printorian-testing/SKILL.md (evidence per " +
    "change surface — a task without its evidence named is not planned). Inspect the code so the plan starts from " +
    "reality, not from HANDOFF.md's last mood. Decompose into minimal topologically-ordered tasks, one write scope " +
    "each; backend tasks serialize on the shared test database, so order matters. Triage irreversible paths first " +
    "(retention, WAL, backups, migrations, refunds, provisioning). Every task names its proving test. " +
    "If the request touches an ADR-locked decision, that is an ownerDecision, not a task.",
});
const plan = await planner.ask<Plan>(`Plan this request into tasks:\n${task}\n\nBase ref: ${baseRef}.`);
const cards = plan.tasks.slice(0, maxTasks);
if (plan.tasks.length > maxTasks) {
  notCovered.push(`${plan.tasks.length - maxTasks} planned task(s) beyond max_tasks=${maxTasks} were not executed`);
}
if (plan.ownerDecisions.length > 0) {
  plan.ownerDecisions.forEach((d) =>
    reportFindings.push({
      where: "(plan)",
      what: `Owner decision the plan needs: ${d}`,
      evidence: "planner output",
      status: "unconfirmed",
      severity: "medium",
    }),
  );
}
for (const card of cards) {
  report({ id: card.id, title: card.title, status: "planned", branch: "", note: "" }, "tasks");
}
log(`Planned ${cards.length} task(s)`);

/** Gates for a worktree, run with the MAIN checkout's venv (import check first). */
function worktreeGates(wt: string): GateStep[] {
  const importCheck =
    `cd ${wt}/backend && OUT=$(${MAIN_PY} -c "import printorian, sys; print(printorian.__file__)") || exit 6; ` +
    `case "$OUT" in *"${wt}"*) ;; *) echo "WRONG_TREE: import resolves to $OUT, not the worktree" >&2; exit 6;; esac; `;
  return [
    { name: "import resolution", script: importCheck + "echo ok", timeoutMs: 120_000 },
    { name: "ruff check", script: importCheck + `${MAIN_PY} -m ruff check .`, timeoutMs: 180_000 },
    { name: "ruff format --check", script: importCheck + `${MAIN_PY} -m ruff format --check .`, timeoutMs: 180_000 },
    { name: "mypy (strict)", script: importCheck + `${MAIN_PY} -m mypy`, timeoutMs: 900_000 },
    { name: "lint-imports", script: importCheck + `cd ${wt}/backend && ${MAIN_LI}`, timeoutMs: 300_000 },
    { name: "context isolation", script: importCheck + `${MAIN_PY} tools/check_context_isolation.py`, timeoutMs: 180_000 },
    { name: "file length (400)", script: importCheck + `${MAIN_PY} tools/check_file_length.py`, timeoutMs: 180_000 },
  ];
}

async function runGate(step: GateStep): Promise<GateOutcome2> {
  const r = await world.run("bash", ["-c", step.script], { timeoutMs: step.timeoutMs, output: { tail: 16_384 } });
  return { name: step.name, exitCode: r.exitCode, tail: r.stderr + r.stdout };
}

interface GateOutcome2 {
  name: string;
  exitCode: number;
  tail: string;
}

const branchOf: Record<string, string> = {};
const ready: Record<string, boolean> = {};
let lastDoneBranch = "";
let lastDoneCard = "";

// ---------------------------------------------------------------------------
for (const card of cards) {
  const cardSlug = safeSlug(card.id) || `t${Object.keys(branchOf).length + 1}`;
  const branch = `${branchPrefix}-${cardSlug}`;
  branchOf[card.id] = branch;
  const wt = `${WT_ROOT}/${cardSlug}`;
  const unreadyDeps = card.dependsOn.filter((d) => ready[d] !== true);
  if (unreadyDeps.length > 0) {
    report({ id: card.id, title: card.title, status: "blocked", branch, note: `unready deps: ${unreadyDeps.join(", ")}` }, "tasks");
    reportFindings.push({
      where: `task ${card.id}`,
      what: `Skipped: dependencies not READY (${unreadyDeps.join(", ")})`,
      evidence: "dependency chaining",
      status: "verified",
      severity: "medium",
    });
    continue;
  }
  const depBranches = card.dependsOn.map((d) => branchOf[d]).filter((b) => typeof b === "string" && b !== "");
  const effectiveBase = depBranches.length > 0 ? depBranches[depBranches.length - 1] : baseRef;

  phase("Prepare the task's worktree");
  report({ id: card.id, title: card.title, status: "baseline", branch, note: `base ${effectiveBase}` }, "tasks");
  const wtExists = await world.run("bash", ["-c", `test -d ${wt} && echo yes || echo no`], { timeoutMs: 30_000 });
  let resumeNote = "";
  if (wtExists.stdout.trim() === "yes") {
    const count = await world.run(
      "bash",
      ["-c", `git -C ${wt} rev-list --count ${effectiveBase}..HEAD 2>/dev/null || echo 0`],
      { timeoutMs: 60_000 },
    );
    const n = Number(count.stdout.trim()) || 0;
    if (n === 0) {
      await world.run("bash", ["-c", `git worktree remove --force ${wt} && git worktree add ${wt} -b ${branch} ${effectiveBase}`], { timeoutMs: 120_000 });
    } else {
      resumeNote = `The worktree already has ${n} commit(s) on this branch — continue from where they left off.`;
      log(`task ${card.id}: resuming worktree with ${n} commit(s)`);
    }
  } else {
    const add = await world.run("bash", ["-c", `git worktree add ${wt} -b ${branch} ${effectiveBase}`], { timeoutMs: 120_000 });
    if (add.exitCode !== 0) {
      report({ id: card.id, title: card.title, status: "blocked", branch, note: "worktree add failed" }, "tasks");
      notCovered.push(`task ${card.id} (${card.title}): git worktree add failed — ${add.stderr.slice(0, 300)}`);
      continue;
    }
  }
  const baseShaRun = await world.run("bash", ["-c", `git -C ${wt} rev-parse ${effectiveBase}`], { timeoutMs: 60_000 });
  const baseSha = baseShaRun.stdout.trim() || effectiveBase;
  if (card.scope.some((p) => p.startsWith("frontend/"))) {
    const npmci = await world.run("bash", ["-c", `cd ${wt}/frontend && npm ci`], { timeoutMs: 900_000, output: { tail: 8_192 } });
    verified.push(`task ${card.id} frontend npm ci: exit ${npmci.exitCode}`);
  }

  phase("Verify the task's baseline");
  let baselineOk = true;
  for (const gate of worktreeGates(wt)) {
    const g = await runGate(gate);
    if (g.exitCode !== 0) {
      baselineOk = false;
      report({ id: card.id, title: card.title, status: "blocked", branch, note: `baseline red: ${gate.name}` }, "tasks");
      notCovered.push(`task ${card.id} (${card.title}): baseline gate ${gate.name} red — ${g.tail.slice(-500)}`);
      break;
    }
  }
  if (!baselineOk) continue;
  ready[card.id] = false;

  phase("Implement the task");
  report({ id: card.id, title: card.title, status: "implementing", branch, note: "" }, "tasks");
  const implementer = agent(`Implementer ${card.id}`, {
    system:
      "You are the implementation stage of Printorian's development studio. Read first, binding: AGENTS.md " +
      "(§1 owner rules, §4 session protocol); backend/CLAUDE.md and/or frontend/CLAUDE.md for your scope; " +
      ".zcode/skills/printorian-build-test/SKILL.md — including §4 (you are in a worktree: use the MAIN checkout's " +
      "venv with the worktree as cwd, never docker compose from the worktree). " +
      "Rules: write only inside the task's scope; read a neighbouring file before writing a new one and match the " +
      "prose-heavy comment voice (why + alternative + cost); new functionality arrives with its test, a bug fix with " +
      "the failing test first; run only narrow self-checks — the script re-gates after you; commit as a sentence " +
      `with the issue reference${issue > 0 ? ` (#${issue})` : ""}, never push, never amend reviewed commits. ` +
      "If blocked by a rule, return with deviations saying so. If a check is impossible to pass or instructions " +
      "contradict, escalate and say so plainly rather than working around it.",
  });
  const impl = await implementer.ask<ImplResult>(
    `Task card: ${JSON.stringify(card)}\nOverall objective: ${plan.objective}\n` +
      `Worktree: ${wt} (branch ${branch}, base ${effectiveBase}). Work THERE.\n${resumeNote}\n` +
      `Risks named by the planner: ${card.risks}\nImplement, add the proving test, commit. Return the contract.`,
  );
  log(`task ${card.id}: ${impl.commits.length} commits, ${impl.changedPaths.length} paths`);
  if (impl.deviations.trim() !== "") {
    reportFindings.push({
      where: `task ${card.id}`,
      what: `Deviation: ${impl.deviations}`,
      evidence: "implementer handoff",
      status: "unconfirmed",
      severity: "low",
    });
  }
  const scopedTests = safePaths(impl.testPaths.length > 0 ? impl.testPaths : card.testPaths);

  phase("Run the fast gates and fix what they catch");
  const fixer = agent(`Fixer ${card.id}`, {
    system:
      "You repair a Printorian change in its worktree so a specific gate passes, without widening the diff: fix the " +
      "cause the gate names, never the gate (no noqa, no threshold change, no skipped test). Read the area's " +
      "CLAUDE.md first. Commit each fix as its own sentence. Never push. If the gate is wrong rather than the code, " +
      "return fixed: [] and say so in notFixed. Escalate rather than improvise around a block.",
  });
  let gatesGreen = false;
  for (let round = 1; round <= 3; round++) {
    const failures: string[] = [];
    for (const gate of worktreeGates(wt)) {
      const g = await runGate(gate);
      verified.push(`${card.id} round ${round} ${gate.name}: exit ${g.exitCode}`);
      if (g.exitCode !== 0) failures.push(`--- ${gate.name} (exit ${g.exitCode}) ---\n${g.tail.slice(-3000)}`);
    }
    if (failures.length === 0) {
      gatesGreen = true;
      break;
    }
    if (round === 3) break;
    await fixer.ask<FixReport>(
      `Fast gates failed in ${wt} on round ${round}:\n${failures.join("\n")}\nFix the causes and commit. Context: ${impl.summary}`,
    );
  }
  if (!gatesGreen) {
    report({ id: card.id, title: card.title, status: "blocked", branch, note: "gates red after 3 rounds" }, "tasks");
    notCovered.push(`task ${card.id} (${card.title}): fast gates never went green; branch ${branch} left for inspection`);
    continue;
  }

  if (scopedTests.length > 0) {
    phase("Run the scoped tests");
    let scopedGreen = false;
    for (let round = 1; round <= 3; round++) {
      const scoped = await world.run(
        "bash",
        ["-c", `cd ${wt}/backend && ${PYTEST_ENV} ${MAIN_PY} -m pytest -q ${scopedTests.join(" ")}`],
        { timeoutMs: 900_000, output: { tail: 16_384 } },
      );
      verified.push(`${card.id} scoped pytest round ${round}: exit ${scoped.exitCode}`);
      if (scoped.exitCode === 0) {
        scopedGreen = true;
        break;
      }
      if (round === 3) break;
      await fixer.ask<FixReport>(
        `Scoped pytest failed in ${wt}/backend on round ${round}:\n${(scoped.stderr + scoped.stdout).slice(-4000)}\nFix and commit.`,
      );
    }
    if (!scopedGreen) {
      report({ id: card.id, title: card.title, status: "blocked", branch, note: "scoped tests red" }, "tasks");
      notCovered.push(`task ${card.id} (${card.title}): scoped tests never went green`);
      continue;
    }
  } else {
    notCovered.push(`task ${card.id}: no scoped pytest (no test paths named)`);
  }

  phase("Review the diff independently");
  report({ id: card.id, title: card.title, status: "review", branch, note: "" }, "tasks");
  const reviewer = agent(`Reviewer ${card.id}`, {
    system:
      "You are the independent review stage of Printorian's development studio; you read a diff and report " +
      "findings, you never edit. Read first, binding: AGENTS.md; .zcode/skills/printorian-decisions/SKILL.md; " +
      ".zcode/memories/MEMORY.md grepped for the changed modules — repeating a recorded trap is CRITICAL, cite the " +
      "memory. Policy is read from the BASE ref — a change cannot weaken its own review. Sections: correctness; " +
      "layering (core knows nothing of the farm, contexts via __init__ only, pricing purity, 400-line limit); " +
      "ADR-0007 number discipline; money rules; security surface; evidence expectations; minimal diff. " +
      "Approving takes evidence; objecting is the useful move; clean is called clean, plainly. " +
      "Every finding carries a reading list for a bounded confirmer.",
  });
  const review = await reviewer.ask<Review>(
    `Review branch ${branch} in worktree ${wt} against ${effectiveBase} (${baseSha}).\n` +
      `Card: ${JSON.stringify(card)}\nChanged paths: ${impl.changedPaths.join(", ") || "(none reported)"}\n` +
      `Look hardest at: ${impl.hardest}\nRun git -C ${wt} diff yourself. Findings in severity order + verdict.`,
  );

  phase("Confirm each finding in parallel, cheaply");
  const MAX_CONFIRM = 12;
  const toConfirm = review.findings.slice(0, MAX_CONFIRM);
  if (review.findings.length > MAX_CONFIRM) {
    notCovered.push(`${card.id}: ${review.findings.length - MAX_CONFIRM} finding(s) beyond cap reported unconfirmed`);
  }
  // Join: dispositions need every confirmation of this task together.
  const confirmations = await Promise.all(
    toConfirm.map((f, i) =>
      agent(`Confirmer ${card.id}-${i + 1}`, {
        model: "GLM-5.3-Flash",
        budget: { webSearch: 0 },
        system:
          "You verify ONE review finding in a Printorian worktree, from its evidence alone; read-only, never edit. " +
          "Decide confirmed / refuted / inconclusive — an honest inconclusive beats a grind. Check preexisting via " +
          "the base ref when it matters. At most ONE follow-up read beyond the reading list.",
      }).ask<Confirmation>(`Finding: ${JSON.stringify(f)}\nWorktree: ${wt}. Base ref: ${baseSha}.`),
    ),
  );
  const confirmedOnChange: ReviewFinding[] = [];
  const confirmedPreexisting: ReviewFinding[] = [];
  toConfirm.forEach((f, i) => {
    const c = confirmations[i];
    if (c.verdict === "confirmed") {
      (c.preexisting ? confirmedPreexisting : confirmedOnChange).push(f);
      reportFindings.push({
        where: `${card.id}: ${f.where}`,
        what: `${f.problem} → ${f.suggestion}`,
        evidence: `confirmed (${c.evidence})${c.preexisting ? " — pre-existing" : ""}`,
        status: "verified",
        severity: sevToReport(f.severity),
      });
    } else if (c.verdict === "refuted") {
      reportFindings.push({
        where: `${card.id}: ${f.where}`,
        what: `Refuted: ${f.problem}`,
        evidence: c.evidence,
        status: "verified",
        severity: "low",
      });
    } else {
      reportFindings.push({
        where: `${card.id}: ${f.where}`,
        what: `${f.problem} → ${f.suggestion}`,
        evidence: `inconclusive (${c.evidence})`,
        status: "unconfirmed",
        severity: sevToReport(f.severity),
      });
    }
  });

  phase("Fix the confirmed findings and re-check");
  report({ id: card.id, title: card.title, status: "verify", branch, note: "" }, "tasks");
  if (confirmedOnChange.length > 0) {
    await fixer.ask<FixReport>(
      `Fix these confirmed findings in ${wt}:\n` +
        confirmedOnChange.map((f) => `- [${f.severity}] ${f.where}: ${f.problem} → ${f.suggestion}`).join("\n") +
        `\nCommit each fix as its own sentence on ${branch}.`,
    );
    let regreen = false;
    for (let round = 1; round <= 2; round++) {
      const failures: string[] = [];
      for (const gate of worktreeGates(wt)) {
        const g = await runGate(gate);
        if (g.exitCode !== 0) failures.push(`--- ${gate.name} ---\n${g.tail.slice(-3000)}`);
      }
      if (scopedTests.length > 0) {
        const scoped = await world.run(
          "bash",
          ["-c", `cd ${wt}/backend && ${PYTEST_ENV} ${MAIN_PY} -m pytest -q ${scopedTests.join(" ")}`],
          { timeoutMs: 900_000, output: { tail: 16_384 } },
        );
        if (scoped.exitCode !== 0) failures.push(`--- scoped pytest ---\n${(scoped.stderr + scoped.stdout).slice(-3000)}`);
      }
      if (failures.length === 0) {
        regreen = true;
        verified.push(`${card.id} post-fix re-gate green`);
        break;
      }
      if (round === 2) break;
      await fixer.ask<FixReport>(`Post-fix gates still red in ${wt}:\n${failures.join("\n")}\nFix and commit.`);
    }
    if (!regreen) {
      report({ id: card.id, title: card.title, status: "blocked", branch, note: "post-fix gates red" }, "tasks");
      notCovered.push(`task ${card.id}: post-fix gates never went green`);
      continue;
    }
    const delta = await reviewer.ask<Review>(
      `Delta re-review on ${branch} in ${wt} versus ${baseSha}: only that the fixes hold; do not re-raise reported findings.`,
    );
    delta.findings
      .filter((f) => f.severity === "CRITICAL" || f.severity === "MAJOR")
      .forEach((f) =>
        reportFindings.push({
          where: `${card.id}: ${f.where}`,
          what: `Delta re-review: ${f.problem}`,
          evidence: f.readingList,
          status: "unconfirmed",
          severity: sevToReport(f.severity),
        }),
      );
  }
  if (confirmedPreexisting.length > 0) {
    notCovered.push(
      `${card.id}: ${confirmedPreexisting.length} confirmed pre-existing defect(s) recorded, not fixed: ` +
        confirmedPreexisting.map((f) => `${f.where} — ${f.problem}`).join("; "),
    );
  }

  phase("Certify the task at the gate");
  report({ id: card.id, title: card.title, status: "gate", branch, note: "" }, "tasks");
  const gatekeeper = agent(`Gatekeeper ${card.id}`, {
    system:
      "You own the final per-task gate decision of Printorian's development studio; read-only. Audit the packet: " +
      "identity (branch + base tree hash, not labels); independence (implementer did not review; repeated " +
      "invocations of one agent are not one stage); required evidence per .zcode/skills/printorian-testing/SKILL.md " +
      "— with the documented studio exception that the FULL suite runs once at the end of the whole run, so per-task " +
      "evidence is fast gates + scoped tests; every finding dispositioned (fixed/refuted/pre-existing/deferred). " +
      "Missing evidence is a hold, not a pass; environmental unavailability never converts to success. " +
      "READY certifies the gate, never permission to merge or push.",
  });
  const verdict = await gatekeeper.ask<GateVerdict>(
    `Packet for task ${card.id} (${card.title}):\n` +
      `branch=${branch} base=${effectiveBase} (${baseSha}) worktree=${wt}\n` +
      `impl: ${JSON.stringify({ summary: impl.summary, commits: impl.commits, changed: impl.changedPaths })}\n` +
      `review findings: ${review.findings.length}; confirmations: ${JSON.stringify(confirmations.map((c, i) => ({ i, verdict: c.verdict, preexisting: c.preexisting })))}\n` +
      `scoped tests: ${scopedTests.join(", ") || "none named"}\ngate runs so far this task: see verified ledger (fast gates green, scoped green)\n` +
      `Return the verdict.`,
  );
  verified.push(...verdict.checks.map((c) => `${card.id} gate: ${c}`));
  if (verdict.verdict !== "READY") {
    report({ id: card.id, title: card.title, status: "blocked", branch, note: `gatekeeper: ${verdict.verdict}` }, "tasks");
    notCovered.push(`task ${card.id}: gatekeeper said ${verdict.verdict} — ${verdict.gaps.join("; ")}`);
    continue;
  }
  ready[card.id] = true;
  lastDoneBranch = branch;
  lastDoneCard = card.id;
  prTexts.push(
    [
      `## ${card.title}`,
      "",
      impl.summary,
      "",
      `**Proving test:** ${scopedTests.join(", ") || "the run's final full suite"}`,
      `**Branch:** \`${branch}\` (base \`${effectiveBase}\`)`,
      ...(issue > 0 ? [`Closes #${issue}`] : []),
      ...(impl.deviations.trim() !== "" ? ["", `**Deviations:** ${impl.deviations}`] : []),
    ].join("\n"),
  );
  report({ id: card.id, title: card.title, status: "done", branch, note: "READY" }, "tasks");
  await artifact.markdown("pr-texts", prTexts.join("\n\n---\n\n"), {
    title: "Pull-request texts (not pushed)",
    description: "One section per READY task; the proving test is named per the PR template.",
  });
}

// ---------------------------------------------------------------------------
if (lastDoneBranch !== "") {
  phase("Run the full suite once for the whole run");
  const wt = `${WT_ROOT}/${safeSlug(lastDoneCard)}`;
  for (const gate of worktreeGates(wt)) {
    const g = await runGate(gate);
    verified.push(`final ${gate.name}: exit ${g.exitCode}`);
    if (g.exitCode !== 0) {
      return {
        conclusion: `Final fast gate ${gate.name} red on ${lastDoneBranch}.\n${g.tail.slice(-2000)}`,
        findings: reportFindings,
        verified,
        notCovered: [...notCovered, "full suite — a fast gate failed first"],
      };
    }
  }
  const full = await world.run("bash", ["-c", `cd ${wt}/backend && ${PYTEST_ENV} ${MAIN_PY} -m pytest -q`], {
    timeoutMs: 1_800_000,
    output: { tail: 16_384 },
  });
  const summaryLine =
    (full.stdout + full.stderr).split("\n").filter((l) => /passed|failed|error/i.test(l)).pop() ?? "(no summary line)";
  verified.push(`full pytest suite on ${lastDoneBranch}: exit ${full.exitCode} — ${summaryLine.trim()}`);
  if (full.exitCode !== 0) {
    const failing = (full.stdout + full.stderr)
      .split("\n")
      .filter((l) => /^(FAILED|ERROR)/.test(l))
      .map((l) => l.split(" ")[1] ?? l);
    const novel = failing.filter((id) => !knownRed.includes(id));
    if (!(novel.length === 0 && failing.length > 0)) {
      return {
        conclusion:
          `Full suite red on ${lastDoneBranch}: ${novel.slice(0, 10).join(", ")}. Branches left for inspection.\n${summaryLine}`,
        findings: reportFindings,
        verified,
        notCovered: [...notCovered, "memory/HANDOFF record — suite failed"],
      };
    }
    notCovered.push(`full suite red only in known_red tests: ${failing.join(", ")}`);
  }
  const knownGreen = knownRed.filter(
    (id) => !(full.stdout + full.stderr).includes(id),
  );
  if (knownGreen.length > 0 && knownRed.length > 0) {
    notCovered.push(`known_red entries now green (stale — remove): ${knownGreen.join(", ")}`);
  }
} else {
  notCovered.push("full suite — no task reached READY");
}

// ---------------------------------------------------------------------------
phase("Record what the run learned");
const memoryWriter = agent("Memory writer", {
  model: "GLM-5.3-Flash",
  budget: { webSearch: 0 },
  system:
    "You close out a Printorian studio run in the MAIN checkout. Read .zcode/skills/printorian-memory-writer/SKILL.md " +
    "first — it is the format. Get today's date with `date +%F`. Write (only if the run earned it — routine work " +
    "gets no memory): a memory record for each measured lesson, trap, or owner decision this run produced (extend an " +
    "existing record with a dated section instead of forking; cite commits/tests), plus the MEMORY.md index row(s). " +
    "Then append to HANDOFF.md (top, after the header) a dated `## YYYY-MM-DD — title` section with an **As of:** " +
    "line (branch, base) covering what was done, what is deliberately unfinished, what needs a person. " +
    "Commit memory files as `memory: <topic>` and the HANDOFF change as `handoff: <one-line>` — in the MAIN " +
    "checkout, never the worktrees, and never push.",
});
const memoryNote = await memoryWriter.ask<string>(
  `Run summary (main checkout ${MAIN}):\n` +
    `objective: ${plan.objective}\ncards done: ${Object.keys(ready).filter((k) => ready[k]).join(", ") || "none"}\n` +
    `findings ledger:\n${reportFindings.map((f) => `- [${f.severity}/${f.status}] ${f.where}: ${f.what}`).join("\n")}\n` +
    `notCovered:\n${notCovered.map((n) => `- ${n}`).join("\n")}\n` +
    `Write the memory/HANDOFF record and return one line saying what you wrote (or why nothing was worth a memory).`,
);
verified.push(`memory/HANDOFF record: ${memoryNote.trim().slice(0, 300)}`);

// ---------------------------------------------------------------------------
phase("Write the studio report and let a fresh reader check it");
const doneIds = Object.keys(ready).filter((k) => ready[k]);
const studioReport = [
  `# Studio run: ${plan.objective.split("\n")[0]}`,
  "",
  `${cards.length} task card(s); READY: ${doneIds.join(", ") || "none"}. Final branch: ${lastDoneBranch || "(none)"}.`,
  "",
  "## Gates",
  "",
  ...verified.slice(-40).map((v) => `- ${v}`),
  "",
  "## Findings ledger",
  "",
  ...(reportFindings.length === 0 ? ["- none"] : reportFindings.map((f) => `- [${f.severity}/${f.status}] ${f.where}: ${f.what}`)),
  ...(notCovered.length > 0 ? ["", "## Not covered", "", ...notCovered.map((n) => `- ${n}`)] : []),
].join("\n");

const reader = agent("Report reader", {
  model: "GLM-5.3-Flash",
  budget: { webSearch: 0 },
  system:
    "You read a run report for unsupported claims — from the text alone. List anything unsupported, any number " +
    "without a source, any question the owner will ask that it does not answer. Empty list means it holds.",
});
const readerNotes = await reader.ask<string>(`Report to check:\n${studioReport}`);

await artifact.markdown("studio-report", studioReport, {
  title: "Studio report",
  description: "Per-task gates, findings, dispositions and the final full-suite run.",
  primary: true,
});

return {
  conclusion:
    `${doneIds.length}/${cards.length} task(s) READY${lastDoneBranch ? `, final branch ${lastDoneBranch}` : ""}. ` +
    `${reportFindings.length} finding(s) across the run; PR texts are in the artifact; nothing was pushed. ` +
    `Memory/HANDOFF: ${memoryNote.trim().slice(0, 200)}` +
    (readerNotes.trim() === "" ? "" : ` Reader notes on the report: ${readerNotes.trim().slice(0, 300)}`),
  findings: reportFindings,
  verified,
  notCovered,
};
