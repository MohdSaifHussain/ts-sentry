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
- [x] Overclaim fixtures: every planted defect caught, correct reason codes
- [x] Citation resolver rejects phantom anchors and overrun excerpts
- [x] Signed vs draft rendering verified; signature path requires analyst id
- [x] Corpus manifest + pinning round-trip test green
- [x] mypy --strict, ruff, coverage floor green; CHANGELOG updated

## 7. Outcome

Shipped: D1-D7, in `src/ts_sentry/data/` (policy_corpus, policy_fetch,
policy_sources), `policies/`, `src/ts_sentry/agents/memo/`,
`src/ts_sentry/orchestrator/` (citation_resolver, citation_tool, memo_gate,
draft_check, memo_turn, memo_export, signing), and the `fetch-policies`,
`run-session --agent memo` and `sign-memo` CLI verbs. 923 tests green, mypy
`--strict` and ruff clean, 93.9% line coverage against a 90 floor. Fully offline
except `fetch-policies`, which CI never runs.

### Deliverable order, and the two halts

Executed D1, HALT 1, D2, D3, D5, gate checker, HALT 2, then D4, D6, D7. Saif's
instruction placed the review stop "before the signature/finalization path is
built", which is before D6, while the deliverables it named establish the corpus
and the verification surface. Raised rather than guessed; his decision was to
stop before D4 as well, on the STEP-04 principle that the constraint machinery
is reviewed before the agent it constrains.

A second halt was added inside D2 at his instruction: the three source documents
were presented with their fetched titles and every extracted clause in full
verbatim text, and nothing was hashed until he confirmed them. That is recorded
because the corpus is immutable in practice: memos pin a corpus digest, so
re-hashing later would invalidate every pinned memo and fixture.

### What the phase's exit criterion actually showed

ARCHITECTURE 11 asks for "verification pass/fail demonstrably catching planted
overclaims". On a real session against `t02_chan_000_000`:

| Run | Result |
|---|---|
| Faithful stub | 1 attempt, verified, memo DRAFT, chain intact, head `7:8ff8ef0b...` |
| Overclaim stub, 3 attempts | 3 `gate_rejection` + 3 `verification_fail`, memo stays DRAFT, chain intact |
| `sign-memo` on the verified memo | watermark absent from `memo.md` and `memo.html`, signature `04fda5dd...` over digest `b0f1c39d...` |

The drafted memo cites `prov-0000` (a real pack record) and quotes
`comment-spam` verbatim from the corpus. It is the first memo this system has
produced.

### Findings that changed the design

Six, none of them caught by a test that already existed.

1. **Raw-byte document hashing cannot detect policy change.** Two fetches of the
   YouTube spam page in one process returned different digests, differing only
   in the CSP `nonce` Google regenerates per request. `content_digest` over the
   committed clauses became the citation identity and `retrieval_sha256` was
   demoted to provenance. Verified on real artifacts: two independent fetches
   produced identical content digests for all three documents and different
   retrieval digests for all three.
2. **Heading-only anchoring was too coarse.** On the spam page every violation
   type is an `<li><strong>Comment spam:</strong>` item inside one 486-word
   section, so a memo about a T-01 ring could have cited nothing narrower than
   that section. The extractor now anchors labelled list items, which is why
   `comment-spam`, `off-platform-diversion` and `scams` exist as anchors.
3. **A callout carried the wrong policy into a clause.** The fake-engagement
   page opens with a `tip` box containing 76 words of *impersonation* policy, so
   the clause named `fake-engagement-policy` opened with a different rule and a
   citation would have resolved perfectly to the wrong thing. Clause boundaries
   now follow policy subject rather than page layout.
4. **A sentence could display a citation it did not record.** Found by a gate
   test that expected three failures and got two: a sentence built directly
   rather than through `from_text` carried an id in its prose while its
   `evidence_ids` stayed empty, so the claim checker never submitted it and the
   sentence read as supported while resolving nothing.
5. **The memo-integrity metric was inflated.** The first overclaim run reported
   "3 corrections before human review" for one unchanged sentence rejected three
   times. `MemoTurn` now reports `rejected_attempts`, `distinct_defects` and
   `revised` separately; the same run reads 3 / 1 / False. Counting a repeated
   refusal as repeated corrections would have reported the governance layer as
   busier than it was, which is the flattering direction.
6. **Signing would have invalidated its own signature.** `content_digest`
   originally covered `status`, so the DRAFT-to-SIGNED flip changed the digest
   the signature was taken over. `status` is now excluded.

### HALT-2 review findings

See section 5. Findings 1-3 fixed with tests, finding 4 implemented in D6 as
recorded.

### Readings and deviations, recorded

1. **STEP-05 3.1's four roles do not cover DSA Art. 17(3)(c).** The
   automated-means disclosure is carried structurally on `AutomatedMeans` and
   the agent cannot write it, on the `reviewer_kind` argument: a disclosure
   about how automated a decision was is worthless if the automated component
   composes it. Vocabulary from the Commission's DSA Transparency Database.
2. **No `LEGAL_GROUND` role**, so Art. 17(3)(d) is unreachable. Every case here
   is a terms-and-conditions matter and a legal-ground role would invite a memo
   to assert illegality nothing here can assess.
3. **Art. 17(3)(a)'s territorial scope and duration are not modelled.**
4. **Corpus updates are not a new `EventType`.** A re-fetch is build-time
   provenance and happens with no session open. `SESSION_OPEN` binds
   `corpus_version` and `corpus_sha256` instead, keeping ARCHITECTURE 3.2's
   eleven event types a closed surface.
5. **`orchestrator.signing` is a third entry in
   `LEGITIMATE_SIGNATURE_CONSUMERS`**, added by a visible edit in the same
   commit as the module.
6. **`run-session --agent memo` and `sign-memo` are CLI surfaces STEP-05 does
   not enumerate**, added on the STEP-04 precedent: the exit checklist requires
   signed-versus-draft rendering to be verified, and nothing runnable by hand
   would otherwise produce it.
7. **`MEMO_MANDATE` grants no data scopes at all.** The memo agent reaches no
   platform table; it works from an accepted pack and the hashed corpus.

### Cross-phase effect, recorded because it looks like tampering

Adding a third mandate changes `mandate_set_hash` for **every** session type:
`684b49b9...` becomes `02ed4726...`, while the per-agent triage and evidence
mandate hashes are untouched. `SESSION_OPEN` carries the set hash, so chain
heads recorded before STEP-05 no longer reproduce, **including Saif's STEP-04
phase-close head `cb23...cf39`**. Same class as STEP-04's `dataset_digest` v2
note.

### Honest limits

- **The memo agent's competence is untested.** Every result here was produced by
  a deterministic stub that cannot be persuaded and cannot reason. What is
  tested is that a claim is checked rather than trusted, that a citation must
  resolve to text that exists, and that nothing is final without a signature.
  None of it is evidence about how a model would draft.
- **The stub does not revise.** Told exactly what was wrong, it re-sends the
  same draft. The revise loop's success path is covered by a purpose-built
  responder that corrects itself, not by the stub, and the difference is
  reported as `revised`.
- **DSA Art. 17(2) may exclude most of this caseload.** "Paragraph 1 shall not
  apply where the information is deceptive high-volume commercial content",
  which plausibly covers T-01 comment spam rings and T-06 slop farms. The memos
  are regulation-shaped best-practice documentation; "these are DSA Article 17
  statements of reasons" is wider than the truth.
- **The corpus is three documents at one version, fetched once.** No re-fetch
  has been performed since v1 was pinned, so the corpus-update path is built and
  exercised only in tests.
- **A signature proves integrity, not identity.** Unchanged from STEP-02: it
  binds five fields together and does not authenticate the analyst.
- **Anchor stability has a stated limit.** Inserting a *duplicate* heading above
  an existing one moves that one's anchor, and no heading-derived scheme avoids
  it. Asserted as a passing test.
- **`policy_fetch` is 85% covered**, the uncovered lines being the network path,
  deliberately unrun in tests as `LiveAdapter.complete` is.
- **The 3.12 gap persists.** Local Python is 3.14 and CI pins 3.12, so every
  green result here is a 3.14 result, as in STEP-02 through STEP-04.
