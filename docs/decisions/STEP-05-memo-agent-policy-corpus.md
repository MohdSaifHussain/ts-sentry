# STEP-05: Memo Agent + Policy Corpus

**Project:** Trust & Safety Sentry | **Phase:** 5 of 8 | **Date:** 31 July 2026 (IST)
**Status:** Specified, not started
**Depends on:** STEP-04

## 1. Objective
RECOMMEND-mandate memo drafting against a hashed public-policy corpus, in DSA
Article 17 statement-of-reasons structure, with symbolic verification
demonstrably catching planted overclaims. Human signature is the only path to
a final memo.

## 2. Deliverables

| ID | Deliverable | Governing standard(s) |
|---|---|---|
| D1 | Policy corpus tooling: fetch-once script, SHA-256 per document, stable section anchors, corpus manifest | Content integrity by hash; corpus updates are explicit ledgered events (no silent drift) |
| D2 | Corpus v1: verbatim public YouTube policy texts: Spam, Deceptive Practices & Scams; Fake Engagement; synthetic-media disclosure | Fair-use scoped: stored for citation resolution, quoted in memos only as clause references (anchor + <=15-word excerpts) |
| D3 | Memo model: DSA Art. 17 statement-of-reasons structure: facts relied upon (evidence ids), policy ground (doc hash + anchor), proposed measure, redress note | EU DSA Art. 17; audit-grade documentation |
| D4 | `agents.memo`: drafts memo from EvidencePack + corpus | RECOMMEND gate: STEP-02 verifier, zero-tolerance per sentence |
| D5 | Citation resolver: (doc_hash, anchor) -> exists check + excerpt bounds check | NIST AI 600-1 confabulation control extended to citations |
| D6 | Signature path: `HumanSignature(analyst_id, decision, memo_hash)` finalizes; AI-drafted label persists until signed | EU AI Act transparency pattern; ENFORCE human-only invariant |
| D7 | Overclaim fixture suite: memos with planted unsupported claims, phantom citations, excerpt overruns | Negative-path proof: the gate must be seen failing correctly |

## 3. Requirements
- 3.1 Memo AST: typed sentences with role (FACT, POLICY_GROUND, MEASURE,
  REDRESS); FACT requires evidence ids; POLICY_GROUND requires resolvable
  citation; MEASURE limited to a fixed measure vocabulary (StrEnum), no
  free-text sanctions.
- 3.2 Draft-revise loop: verifier failures return flagged sentences; agent may
  revise within mandate step budget; unresolved failures leave memo in DRAFT.
- 3.3 Unsigned memos render with a persistent AI-DRAFT watermark in all
  exports (md, html).
- 3.4 Corpus governance: manifest records fetch date, source URL, hash;
  re-fetch produces a new corpus version; memos pin the corpus version they
  cited.
- 3.5 hypothesis property: a memo that passes verification contains no
  sentence whose evidence ids are absent from the pack (verifier soundness on
  generated memo ASTs).

## 4. Out of Scope
- Prompt registry lifecycle (STEP-06); any automatic enforcement.

## 5. Exit Checklist
- [ ] Overclaim fixtures: every planted defect caught, correct reason codes
- [ ] Citation resolver rejects phantom anchors and overrun excerpts
- [ ] Signed vs draft rendering verified; signature path requires analyst id
- [ ] Corpus manifest + pinning round-trip test green
- [ ] mypy --strict, ruff, coverage floor green; CHANGELOG updated
