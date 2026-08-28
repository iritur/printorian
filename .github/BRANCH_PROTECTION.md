Branch protection recommendations for `main`

This document proposes a minimal, actionable branch-protection policy for the `main` branch. Apply these settings in repository Settings → Branches → Branch protection rules, or use the API snippet below.

Recommended settings

- Protect branch: `main` (pattern: `main`)
- Require pull request reviews before merging:
  - Require 1 approving review (increase to 2 for higher-risk repos)
  - Require review from Code Owners (enable "Require review from Code Owners")
  - Dismiss stale pull request approvals when new commits are pushed
  - Require conversation resolution before merging
- Require status checks to pass before merging:
  - Add the CI job check names: `backend`, `frontend`, `image` (these are the job names in .github/workflows/ci.yml)
  - Optionally add any additional protected checks your org requires
  - Enable "Require branches to be up to date before merging" (ensures merges run CI on the merge commit)
- Require signed commits (optional but recommended for supply-chain integrity)
- Restrict who can push: limit push access to repository admins/automation accounts if appropriate
- Include administrators in the rule (recommended) so the same protections apply to admins

Rationale

- Reviews + CODEOWNERS ensure the right teams are asked to approve critical changes.
- Requiring CI checks and up-to-date branches prevents merging regressions that only fail on merge commits.
- Signed commits and restricted pushes harden the supply chain and reduce accidental direct pushes.

Applying via gh API (example)

Replace OWNER and REPO and update required_status_checks for your exact check names. This is an example; test in a safe repo first.

  gh api --method PUT \
    /repos/OWNER/REPO/branches/main/protection \
    -F "required_status_checks.strict=true" \
    -F "required_status_checks.contexts[]='backend'" \
    -F "required_status_checks.contexts[]='frontend'" \
    -F "required_status_checks.contexts[]='image'" \
    -F "enforce_admins=true" \
    -F "required_pull_request_reviews.dismiss_stale_reviews=true" \
    -F "required_pull_request_reviews.require_code_owner_reviews=true" \
    -F "required_pull_request_reviews.required_approving_review_count=1" \
    -F "restrictions.users='[]'" \
    -F "restrictions.teams='[]'"

Notes

- Confirm the status-check names visible under a PR's Checks tab; use those exact names when adding required checks.
- If your org uses branch rules as code (org policies or terraform), mirror these values there instead of manual UI changes.
- After applying, run an integration PR to validate the merge-flow and ensure automation (publishers, release gate) still function.

— Update this file if the CI job names change or more checks become required.
