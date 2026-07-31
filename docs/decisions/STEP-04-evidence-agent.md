# STEP-04: Evidence Agent + Evidence Packs

**Project:** Trust & Safety Sentry | **Phase:** 4 of 8 | **Date:** 31 July 2026 (IST)
**Status:** Specified, not started
**Depends on:** STEP-03

## 1. Objective
Investigation support under an ASSEMBLE mandate: agent proposes pivots from a
fixed vocabulary, analyst approves, orchestrator executes parameterized
queries, Evidence Pack passes assembly gates. Exit criterion: ground-truth
network recovery metric reportable at fixed pivot budget.

## 2. Deliverables

| ID | Deliverable | Governing standard(s) |
|---|---|---|
| D1 | Pivot vocabulary: PivotKind StrEnum (SHARED_METADATA, TEMPORAL_CORRELATION, ENGAGEMENT_EDGE, INFRA_OVERLAP, ACCOUNT_LINK) each bound to one parameterized DuckDB query template | Injection-surface elimination: LLM never composes SQL; parameterized queries only (OWASP LLM01/SQLi discipline) |
| D2 | `agents.evidence`: pivot proposer (LLM ranks next pivot with reason) + human approve/reject loop | Human-in-command pattern (EU AI Act Art. 14 style); every hop is a ledgered HUMAN_DECISION |
| D3 | EvidencePack model: nodes, edges, timeline, per-record provenance (source table, query template id, param hash, retrieval ts IST) | Provenance completeness (NIST AI 600-1 information integrity); W3C PROV-inspired fields, documented mapping |
| D4 | Assembly gate: referential integrity, provenance completeness, schema conformance | ARCHITECTURE 3.3 ASSEMBLE gate |
| D5 | Recovery metric: fraction of ground-truth network membership recovered vs pivot budget, computed by measurement-side code with sealed-label access | Sealed-scope boundary respected: metric runs outside agent mandates |
| D6 | Graph export: GraphML + JSON for report rendering | Interoperable graph format |

## 3. Requirements
- 3.1 Pivot templates reviewed line-by-line; no string interpolation of user
  or model text into SQL; params typed and bounds-checked.
- 3.2 Proposal contract: agent output = (pivot_kind, params, one-line reason
  citing existing pack record ids); verifier checks the citation.
- 3.3 Rejection handling: analyst reject is terminal for that proposal;
  agent may propose an alternative; max proposals per turn bounded by mandate.
- 3.4 hypothesis properties: pack integrity invariants hold after arbitrary
  approved-pivot sequences; provenance is total (no orphan records).
- 3.5 Benchmark fixture: seed-42 networks; report recovery @ 5/10/20 pivots
  per threat class; numbers land in the measurement report (STEP-07 format).

## 4. Out of Scope
- Free-form SQL exploration (roadmap, behind its own gate); memos.

## 5. Exit Checklist
- [ ] Zero dynamic SQL paths (grep + review note in decisions log)
- [ ] Pack invariants property-tested green
- [ ] Recovery @ budget table generated for seed-42, per threat class
- [ ] All hops present as HUMAN_DECISION events in ledger
- [ ] mypy --strict, ruff, coverage floor green; CHANGELOG updated
