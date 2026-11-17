---
name: printorian-deployment-shape
description: "Owner ruling 2026-11-17: the app stays a containerized modular monolith under systemd compose — no microservices, no host-level installs of the app; the CI digest/cosign/release-gate chain is the trust boundary. Answered the question 'microservices with images vs install directly into the server system' with: neither — containers on a managed host."
metadata:
  node_type: memory
  type: project
---

**Why:** Microservices would break ADR-0001 (one backend, one database, one
domain model) and multiply deployable units for a solo operator, while the
boundaries they'd buy are already enforced statically (import-linter,
`tools/check_context_isolation.py`, code-owner review on pricing and
scheduling). A direct system install would discard the trust chain: CI
builds, scans (Trivy), signs (cosign), and gates (migrate round-trip,
readiness, virtual-farm E2E) every image before any host sees it —
`pip install` on a host runs code CI never proved.

**How to apply:** Proposals are rejected on sight if they: split the
backend into services; install the application (as opposed to the host
layer) onto the OS; or bypass digest-pinned images for delivery. Rollback
stays "deployment is a commit" — revert the digest in
`deploy/production.yaml`. See INFRASTRUCTURE §2 ("Microservices: no"),
§6 (the pipeline), §11 (non-goals).

Owner decision recorded 2026-11-17 in conversation (dimmus), consistent
with the existing docs — this memory is the harness's record so agents
stop re-deriving the answer. The host the containers run *on* is a
separate question, settled the same day: [[printorian-server-charter]].

Related: [[printorian-backup-uid70]] (what quiet failure looks like when
the delivery path is opaque).
