# STEP-03: Orchestrator + Triage Agent

**Project:** Trust & Safety Sentry | **Phase:** 3 of 8 | **Date:** 31 July 2026 (IST)
**Status:** Specified, not started
**Depends on:** STEP-01, STEP-02

## 1. Objective
First end-to-end ledgered session: analyst opens a session, triage agent ranks
the queue under an OBSERVE mandate, rationale verification passes, session
closes with an intact chain.

## 2. Deliverables

| ID | Deliverable | Governing standard(s) |
|---|---|---|
| D1 | `orchestrator.core`: synchronous state machine (SessionState StrEnum; explicit transitions table) | Deterministic FSM design; single-executor kill path (ARCHITECTURE 5) |
| D2 | `orchestrator.firewall`: input firewall for case content | OWASP LLM01 prompt-injection mitigation: delimited inert data blocks, instruction-stripping pass, no case text in system role |
| D3 | `orchestrator.dispatch`: mandate check -> allowlisted tool table -> execute -> schema check -> gate -> ledger | Least privilege; OWASP LLM06 |
| D4 | Model adapter: single boundary module for LLM calls (provider-agnostic, retries with jitter, token accounting against mandate budget) | 12-factor config (env-only credentials, never in repo); OWASP LLM02 output handling |
| D5 | `agents.triage`: deterministic scorer + LLM rationale | Score decomposition published; rationale constrained to score components (verifier-checked) |
| D6 | `cli: ts-sentry run-session --agent triage --seed-dataset PATH` | Session artifacts: ledger JSONL, ranked queue JSON, manifest |

## 3. Requirements
- 3.1 Priority score `f(severity_class, spread, velocity, recidivism)`:
  weights in a versioned config; property test: monotonicity in each component
  holding others fixed; every output row carries the component vector.
- 3.2 Firewall tests: fixture corpus of injection attempts embedded in comment
  text (instruction phrasing, tool-call mimicry, delimiter escapes); assert
  none alter agent behavior contract (rationale still cites only components)
  and all are preserved verbatim as data.
- 3.3 Token/step budget exhaustion ends turn cleanly with `SESSION_CLOSE`
  reason code; partial results are delivered, ledgered.
- 3.4 LLM offline mode: deterministic stub adapter for CI (no network in CI);
  live adapter behind env flag. All tests pass fully offline.
- 3.5 Rationale verifier reuse: STEP-02 verifier with evidence-ids = score
  component ids.

## 4. Out of Scope
- Evidence pivots, memos, prompt registry evaluation.

## 5. Exit Checklist
- [ ] Full session on seed-42 dataset produces intact ledger (verify-ledger 0)
- [ ] Injection fixture corpus: 0 behavioral deviations
- [ ] Monotonicity property green; component vectors present on every row
- [ ] CI fully offline green; live-mode smoke documented
- [ ] mypy --strict, ruff, coverage floor green; CHANGELOG updated
