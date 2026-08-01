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

## 5. Review findings (HALT 2), carried into the Outcome

Saif's review of the memo model, the citation resolver and the RECOMMEND gate
checker, before D4 or D6 existed. The three guarantees under review were proved
from quoted code: `verify_claims` is STEP-02's reused function against
`pack.record_ids` and not a reimplementation, `pack_digest` is checked first and
suppresses the rest on mismatch, and the excerpt match is whitespace-normalised
only, so a paraphrase cannot pass.

Four findings, surfaced by reviewing the code adversarially against its own
claims rather than by any test.

| # | Finding | Disposition |
|---|---|---|
| 1 | `_check_citations` iterated sentences *carrying* a citation, not POLICY_GROUND sentences. The sets are equal through the constructor, so a POLICY_GROUND reaching the gate without a citation on a constructor-bypassed memo was **silently skipped** and the memo passed while asserting a ground it never cited. This broke the `pack_gate` precedent, which keeps unreachable checks precisely because the gate receives an `object` rather than a guaranteed artifact. | **Fixed** |
| 2 | The excerpt ceiling had no floor. `excerpt="spam"` is a true substring of the comment-spam clause and identifies no rule, so a memo could satisfy Article 17(3)(e) with one common word. | **Fixed**: `MIN_EXCERPT_WORDS = 4` with its own `EXCERPT_TOO_SHORT` code, distinct because a too-short excerpt is a *true* quotation that identifies nothing, which is a different failure from a false one. |
| 3 | Substring matching was not word-aligned: `"omment spam: Using high-volume,"` is contiguous in the clause and is a quotation of something it does not say. | **Fixed**: word-sequence matching, so alignment is structural rather than something a regex is trusted to get right. Rewrapping still passes and a changed word still fails, both pinned. |
| 4 | The gate never checks `memo.status`, so a `SIGNED` memo would pass the RECOMMEND gate. | **Deferred to D6**, deliberately: unreachable in this build, since an agent cannot reach `governance.signature` and `with_sentences` resets a revised memo to DRAFT. Intended semantics recorded in `orchestrator/memo_gate.py`. |

The intended D6 semantics, recorded so that phase implements a decision rather
than inventing one: a DRAFT memo is what this gate is for, because it judges
agent output. A SIGNED memo reaching it must be **refused** as a category error,
not because the memo is bad but because re-gating answers the wrong question:
what makes a signed memo trustworthy is that its `content_digest` recomputes and
the signature over it verifies, which is signature verification rather than claim
verification. Accepting one would let a signed memo be re-laundered through the
agent path and emerge with a fresh `VERIFICATION_PASS` that says nothing about
the signature.

Also applied: `resolve_policy_citation`'s docstring now states that the name is
the tool's and the verb is wrong. It attaches; it does not resolve, and it has no
access to `citation_resolver` at all.

## 6. Exit Checklist
- [ ] Overclaim fixtures: every planted defect caught, correct reason codes
- [ ] Citation resolver rejects phantom anchors and overrun excerpts
- [ ] Signed vs draft rendering verified; signature path requires analyst id
- [ ] Corpus manifest + pinning round-trip test green
- [ ] mypy --strict, ruff, coverage floor green; CHANGELOG updated
