# STEP-08: Examples, Documentation, Release v1.0.0

**Project:** Trust & Safety Sentry | **Phase:** 8 of 8 | **Date:** 31 July 2026 (IST)
**Status:** Specified, not started
**Depends on:** STEP-01..07 complete

## 1. Objective
Curated example sessions, complete documentation set, and a reproducible
release. Exit criterion: fresh-clone quickstart under 10 minutes, offline.

## 2. Deliverables

| ID | Deliverable | Governing standard(s) |
|---|---|---|
| D1 | 4+ curated example sessions (one per agent focus; at least one showing a gate rejection and one showing a prompt-regression refusal) | delivery-engine examples precedent: all verified honest, negative paths showcased |
| D2 | Documentation set: README (with Honest Limits), QUICKSTART, USER_GUIDE, ARCHITECTURE (final), model card for LLM usage, data dictionary, decisions/ STEP-01..08 with outcome notes | Model-card practice (documented model deps, offline stub disclosure); Diataxis-informed doc split (tutorial/how-to/reference/explanation) |
| D3 | CHANGELOG finalized; version 1.0.0 tagged | SemVer 2.0.0; Keep a Changelog 1.1.0; annotated git tag |
| D4 | CI release workflow: lint, type, tests, ledger-verify on all example sessions, dataset rebuild determinism check; Docker image to GHCR on GitHub Release | Dockerfile mirrors CI (delivery-engine pattern); GITHUB_TOKEN only, no PAT |
| D5 | AI-collaboration release notes: build directed by Mohd Saif Hussain with Claude as AI collaborator, per documented STEP files | Honest AI-assisted framing (standing resume rule extended to releases) |
| D6 | Interview one-pager: docs/POSITIONING.md mapping every JD responsibility line to a repo artifact | Traceability matrix practice |

## 3. Requirements
- 3.1 Quickstart path: clone -> `pip install -e .` -> `ts-sentry build-dataset
  --seed 42` -> `ts-sentry run-session --agent triage` -> `ts-sentry report`;
  fully offline via stub adapter; timed on a clean machine, time recorded.
- 3.2 Every example directory: inputs manifest, ledger JSONL (verify-ledger
  clean), outputs, and a NOTES.md stating what the example demonstrates and
  what it deliberately does not claim.
- 3.3 POSITIONING.md table columns: JD line (verbatim), Sentry artifact,
  file path, metric or test proving it.
- 3.4 Windows notes section: known CMD/PowerShell/UTF-8 behaviors (carried
  from delivery-engine operational learnings).
- 3.5 Repository metadata: SPDX headers, LICENSE, CITATION.cff, topics set
  (trust-and-safety, ai-governance, agentic, python).

## 4. Out of Scope
- v1.1 roadmap items: free-form pivot exploration behind a gate, dashboard,
  concurrency, automated prompt optimization with contamination review.

## 5. Exit Checklist
- [ ] Fresh-clone offline quickstart timed under 10 minutes, recorded
- [ ] verify-ledger green across every example in CI
- [ ] Gate-rejection and regression-refusal examples present and documented
- [ ] POSITIONING.md covers 100% of JD responsibility lines
- [ ] GHCR image published on release; tag annotated; CHANGELOG cut
