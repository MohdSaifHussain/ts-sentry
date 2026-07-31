# STEP-02: Governance Core

**Project:** Trust & Safety Sentry | **Phase:** 2 of 8 | **Date:** 31 July 2026 (IST)
**Status:** Specified, not started
**Depends on:** STEP-01 (DataScope enum, sealed schema)

## 1. Objective
Implement Mandate, Consequence Gates, Trajectory Ledger, and the symbolic
verifier. Exit criteria: ENFORCE unreachability proven at type level and in
tests; ledger hash chain property-tested; a tampered ledger is detected.

## 2. Deliverables

| ID | Deliverable | Governing standard(s) |
|---|---|---|
| D1 | `governance.mandate`: Mandate frozen dataclass, AgentId/ToolId/DataScope/Consequence StrEnums | PEP 695, frozen slots; least privilege (allowlist semantics) |
| D2 | ENFORCE construction restricted to `HumanSignature` factory requiring analyst_id + decision + SHA-256 signature hash | Type-level safety invariant; NIST AI RMF MANAGE; EU AI Act Art. 14 human-oversight pattern |
| D3 | `governance.ledger`: append-only hash-chained store (DuckDB + JSONL export) | Tamper-evident logging practice (hash chain per RFC 6234 SHA-256); EU AI Act Art. 12 logging pattern; ISO/IEC 42001 traceability |
| D4 | `governance.gates`: OBSERVE / ASSEMBLE / RECOMMEND gate pipeline | ARCHITECTURE 3.3; OWASP LLM06 excessive-agency control |
| D5 | `governance.verifier`: claim-to-evidence symbolic verifier | NIST AI 600-1 confabulation control; every claim sentence must resolve >=1 evidence-record ID |
| D6 | `cli: ts-sentry verify-ledger PATH` | Exit codes: 0 intact, 4 broken chain (first broken seq printed) |

## 3. Requirements
- 3.1 Mandate validation is pure and total: `validate(action, mandate) -> Verdict`
  with exhaustive match on Consequence (mypy strict exhaustiveness).
- 3.2 Ledger entry fields per ARCHITECTURE 3.2; `entry_hash` recomputation
  round-trips; hypothesis properties: (a) chain valid after N random appends,
  (b) any single-field mutation breaks verification at or before that entry,
  (c) append is O(1) lookups (no full-chain rescan on write).
- 3.3 Gate behavior: ASSEMBLE runs schema + referential-integrity + provenance
  checks; RECOMMEND invokes verifier; failures produce `VERIFICATION_FAIL` +
  `GATE_REJECTION` ledger events and return structured failure objects, never
  exceptions to the caller.
- 3.4 Verifier contract: input = memo AST (sentence, claimed_evidence_ids[]);
  output = per-sentence pass/fail with reason codes; zero tolerance: one
  failing sentence fails the memo.
- 3.5 Negative tests are mandatory: attempt to construct a Mandate with
  ENFORCE (must not typecheck: enforced via `assert_type` + a compile-check
  test file excluded from runtime), attempt sealed-scope resolution (ledgered
  MANDATE_VIOLATION_ATTEMPT), attempt gate bypass by direct ledger write
  (rejected: ledger writes only via orchestrator token).

## 4. Out of Scope
- Any model call; dispatch loop (STEP-03); UI.

## 5. Exit Checklist
- [ ] ENFORCE unreachability: type-check test + runtime factory test green
- [ ] hypothesis ledger properties green; tamper test detects mutation
- [ ] verify-ledger CLI detects a fixture with a broken link at correct seq
- [ ] Gate rejection paths ledgered and structurally returned
- [ ] mypy --strict, ruff, coverage floor green; CHANGELOG updated
