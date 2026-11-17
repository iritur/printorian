/* zcode-workflow
description: Drift sweep over Printorian's docs/ — one bounded auditor per document verifies its status claims, counts and issue references against the code; findings are deduplicated, severity-ranked, confirmed where they matter, and optionally drafted as drift issues (gh, only with approval). Read-only on the repo except the issue drafts.
whenToUse: "Are the docs still true?" — periodic verification, or after a change that outdates documentation. Not for editing docs (printorian-doc-writer agent or a gated change), and not for ADR decisions (they change by superseding ADR, not by audit).
args:
  - name: docs
    type: json
    description: JSON array of doc paths to audit (workspace-relative, e.g. ["docs/ARCHITECTURE.md"]). Empty = all top-level docs/*.md.
    default: []
  - name: include_adr
    type: boolean
    description: Also audit docs/adr/*.md (status sections drift too, but they are locked decisions — findings there are reported, never auto-drafted as fixes).
    default: false
  - name: file_issues
    type: boolean
    description: Draft drift issues via gh after approval. false = findings live in the report only. Issues are never filed without the owner's explicit go-ahead.
    default: false
*/

// ---------------------------------------------------------------------------
// Printorian's own rule (CLAUDE.md §4): "The docs go stale... Verify a status
// against the code before repeating it from any document." This workflow is
// that rule, mechanized: every checkable claim in a doc is checked against the
// code, and what fails becomes a drift finding with its evidence.

interface DriftFinding {
  /** Doc path (workspace-relative). */
  doc: string;
  /** The claim as the doc states it, quoted short. */
  claim: string;
  /** Where in the doc (section or line). */
  docLocation: string;
  /** What the code actually shows. */
  reality: string;
  /** How to see it: the files read and/or the one command that counts it. */
  evidence: string;
  /** high = misleads an operator into a wrong action; medium = factual staleness; low = cosmetic. */
  severity: "low" | "medium" | "high";
}

interface DocAudit {
  /** Findings from this document, or an empty list when it held. */
  findings: DriftFinding[];
  /** Claims checked, so coverage is a number and not a vibe. */
  claimsChecked: number;
  /** What could not be checked and why (no code counterpart, needs a live farm...). */
  notChecked: string[];
}

interface Confirmation {
  verdict: "confirmed" | "refuted" | "inconclusive";
  evidence: string;
}

interface IssueDecision {
  action: "file" | "skip";
  /** Optional scope note (e.g. "only high severity"). */
  note?: string;
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

const requested = Array.isArray(args.docs) ? args.docs.map(String).filter((p) => p.startsWith("docs/")) : [];
const includeAdr = args.include_adr === true;
const fileIssues = args.file_issues === true;

const notCovered: string[] = [];
const verified: string[] = [];

artifact.table("drift", {
  columns: [
    { field: "doc", label: "Doc" },
    { field: "claim", label: "Drift" },
    { field: "severity", label: "Severity" },
    { field: "status", label: "Status" },
  ],
  key: "key",
});

// ---------------------------------------------------------------------------
phase("Audit each document against the code");
let docPaths: string[] = [];
if (requested.length > 0) {
  docPaths = requested;
} else {
  const topLevel = await files.glob("docs/*.md");
  docPaths = topLevel.filter((p) => p !== "docs/README.md" && !p.startsWith("docs/adr/"));
  if (includeAdr) {
    const adrs = await files.glob("docs/adr/*.md");
    docPaths = [...docPaths, ...adrs.filter((p) => p !== "docs/adr/README.md")];
  }
}
if (docPaths.length === 0) {
  return {
    conclusion: "No documents matched; nothing was audited.",
    findings: [],
    verified: [],
    notCovered: ["everything — no docs to audit"],
  };
}
log(`Auditing ${docPaths.length} document(s) against the code`);

// Chained per document: each doc's high/medium findings go to their
// confirmers the moment its audit lands. The join at the end is the
// dedup-and-rank barrier, which needs every finding.
const audited = await Promise.all(
  docPaths.map(async (doc) => {
    const audit = await agent(`Auditor ${doc.replace("docs/", "").replace(/\//g, "-")}`, {
      model: "GLM-5.3-Flash",
      budget: { webSearch: 0 },
      system:
        "You audit ONE Printorian document for drift against the code; you read and grep, you never edit. " +
        "Read the doc; extract its CHECKABLE claims: status vocabulary (works / scaffolded / stubbed), counts " +
        "(tables, screens, tests, rules, endpoints), file/command references, issue numbers described as open, " +
        "and behavior descriptions. Verify each against the code — grep for the symbol, count the files, run the " +
        "read-only command. A claim is a finding only when the code contradicts it; 'could not verify' is not a " +
        "finding, it goes to notChecked. House rules: null is 'not measured', never 0; status words have exactly " +
        "three values; the English glossary term is the identifier in code. Every finding's evidence names the " +
        "file:line read and/or the one command that counts it.",
    }).ask<DocAudit>(
      `Audit ${doc} against the code in this workspace. The repo is a FastAPI modular monolith ` +
        `(backend/printorian/<context>/...) with two React apps (frontend/apps/*). Return findings, claimsChecked, ` +
        `notChecked.`,
    );
    const reportable = audit.findings.map((f, i) => ({
      key: `${doc}#${i}`,
      doc,
      claim: `${f.claim} → ${f.reality}`,
      severity: f.severity,
      status: "unconfirmed",
    }));
    reportable.forEach((r) => report(r, "drift"));
    const toConfirm = audit.findings.filter((f) => f.severity !== "low");
    const confirmations = await Promise.all(
      toConfirm.map((f, i) =>
        agent(`Confirmer ${doc.replace("docs/", "").replace(/\//g, "-")}-${i + 1}`, {
          model: "GLM-5.3-Flash",
          budget: { webSearch: 0 },
          system:
            "You verify ONE drift finding from its evidence alone; read-only, never edit. Read what the evidence " +
            "names and run its command if it names one. confirmed / refuted / inconclusive — an honest inconclusive " +
            "beats a grind; at most ONE follow-up read.",
        }).ask<Confirmation>(`Finding: ${JSON.stringify(f)}\nVerify it from the evidence alone.`),
      ),
    );
    return { doc, audit, confirmations, toConfirm, reportable };
  }),
);
// Join: deduplication and the severity ranking below need every finding.

const reportFindings: ReportFinding[] = [];
const confirmedDrafts: DriftFinding[] = [];
for (const entry of audited) {
  verified.push(
    `${entry.doc}: ${entry.audit.claimsChecked} claims checked` +
      (entry.audit.notChecked.length > 0 ? `, ${entry.audit.notChecked.length} not checkable` : ""),
  );
  entry.audit.notChecked.forEach((n) => notCovered.push(`${entry.doc}: ${n}`));
  entry.audit.findings
    .filter((f) => f.severity === "low")
    .forEach((f) =>
      reportFindings.push({
        where: `${f.doc} ${f.docLocation}`,
        what: `Drift (low): ${f.claim} — code says ${f.reality}`,
        evidence: f.evidence,
        status: "unconfirmed",
        severity: "low",
      }),
    );
  entry.toConfirm.forEach((f, i) => {
    const c = entry.confirmations[i];
    const status = c.verdict === "confirmed" ? "verified" : "unconfirmed";
    reportFindings.push({
      where: `${f.doc} ${f.docLocation}`,
      what: `Drift: ${f.claim} — code says ${f.reality}`,
      evidence: `${c.verdict} (${c.evidence})`,
      status,
      severity: f.severity,
    });
    if (c.verdict === "confirmed") confirmedDrafts.push(f);
    const row = entry.reportable.find((r) => r.claim.startsWith(f.claim.slice(0, 40)));
    if (row) report({ ...row, status: c.verdict }, "drift");
  });
}

// ---------------------------------------------------------------------------
phase("Publish the drift report and let a fresh reader check it");
const bySeverity = [...reportFindings].sort((a, b) => {
  const rank = { high: 0, medium: 1, low: 2 };
  return rank[a.severity] - rank[b.severity];
});
const reportMarkdown = [
  `# Docs drift audit — ${audited.length} document(s)`,
  "",
  `${reportFindings.length} finding(s): ${reportFindings.filter((f) => f.status === "verified").length} confirmed, ` +
    `${reportFindings.filter((f) => f.status === "unconfirmed").length} unconfirmed.`,
  "",
  ...bySeverity.map((f) => `- **[${f.severity}/${f.status}]** \`${f.where}\` — ${f.what}\n  evidence: ${f.evidence}`),
  ...(notCovered.length > 0 ? ["", "## Not checkable", "", ...notCovered.map((n) => `- ${n}`)] : []),
  "",
  "Fix path: a `drift`/`type:docs` issue per confirmed finding (drafted only with approval); the correction itself",
  "is doc-writer work riding with the change that caused it, or its own small gated change.",
].join("\n");

const reader = agent("Report reader", {
  model: "GLM-5.3-Flash",
  budget: { webSearch: 0 },
  system:
    "You read a drift report for unsupported claims — from the text alone. List any finding whose evidence line " +
    "does not actually support it, any claim counted twice, anything the owner will ask that the report does not " +
    "answer. Empty list means it holds.",
});
const readerNotes = await reader.ask<string>(`Report to check:\n${reportMarkdown}`);

await artifact.markdown("docs-drift", reportMarkdown, {
  title: "Docs drift report",
  description: "Every checked claim, its status and evidence.",
  primary: true,
});

// ---------------------------------------------------------------------------
phase("Draft drift issues if the owner approves");
if (fileIssues && confirmedDrafts.length > 0) {
  const ghCheck = await world.run(
    "bash",
    ["-c", "gh --version >/dev/null 2>&1 && gh auth status >/dev/null 2>&1 && echo ok || echo no"],
    { timeoutMs: 60_000 },
  );
  if (ghCheck.stdout.trim() !== "ok") {
    notCovered.push(
      `gh CLI unavailable or unauthenticated; ${confirmedDrafts.length} confirmed drift finding(s) remain as report drafts only`,
    );
  } else {
    const decision = await hole<IssueDecision>(
      "File the drift issues?",
      `${confirmedDrafts.length} confirmed drift finding(s) are ready to file as type:docs/drift issues in ` +
        `github.com/iritur/printorian (titles and bodies drafted in the report). Filing is public tracker activity.`,
    );
    if (decision.action === "file") {
      let filed = 0;
      for (const f of confirmedDrafts) {
        const title = `Drift: ${f.doc} ${f.docLocation}`.replace(/"/g, "'").slice(0, 240);
        const body = `The doc says: ${f.claim}\n\nThe code says: ${f.reality}\n\nEvidence: ${f.evidence}\n\n(Via printorian-docs-audit; correction is doc-writer work per docs/WORKFLOW.md.)`
          .replace(/"/g, "'")
          .replace(/\n/g, " ")
          .slice(0, 3000);
        const created = await world.run(
          "bash",
          [
            "-c",
            `gh issue create --repo iritur/printorian --title "${title}" --body "${body}" --label "type:docs" --label "drift"`,
          ],
          { timeoutMs: 60_000, output: { tail: 2_048 } },
        );
        verified.push(`issue drafted for ${f.doc} ${f.docLocation}: exit ${created.exitCode}`);
        if (created.exitCode === 0) filed++;
      }
      log(`Filed ${filed}/${confirmedDrafts.length} drift issue(s)`);
    } else {
      notCovered.push(`${confirmedDrafts.length} confirmed drift finding(s) left as report drafts (owner chose skip)`);
    }
  }
} else if (!fileIssues) {
  notCovered.push(
    `${reportFindings.filter((f) => f.status === "verified").length} confirmed finding(s) left as report drafts (file_issues=false)`,
  );
}

return {
  conclusion:
    `Audited ${audited.length} document(s): ${reportFindings.length} drift finding(s) ` +
    `(${reportFindings.filter((f) => f.status === "verified").length} confirmed). The report artifact is the ` +
    `deliverable;${fileIssues ? " issues were drafted only where approved" : " no issues were filed"}.` +
    (readerNotes.trim() === "" ? "" : ` Reader notes: ${readerNotes.trim().slice(0, 300)}`),
  findings: reportFindings,
  verified,
  notCovered,
};
