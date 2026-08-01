# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [1.0.0] - 2026-08-02

### Added

- **STEP-08 D1: eight curated example runs** in `examples/`, with full
  artifacts committed. Three of them exist to show something being refused:
  the memo gate rejecting an overclaiming agent (8 attempts, 8
  `gate_rejection` + 8 `verification_fail`, memo held at DRAFT), the
  regression gate refusing a degraded prompt (exit 7, four per-class recall
  breaches plus macro-F1), and the input firewall refusing a real published
  corpus on duplicate record ids. Each directory carries an inputs manifest
  and a `NOTES.md` stating what it demonstrates **and what it deliberately
  does not claim**. `examples/regenerate.py` rebuilds them from the shipped
  CLI verbs.
- **STEP-08 D1: the only third-party data in this repository.** The UCI
  YouTube Spam Collection (CC BY 4.0, DOI 10.24432/C58885), 1,956 real
  YouTube comments, redistributed unmodified and attributed, read by example
  08 and by nothing else.
- **STEP-08 D2: the documentation set.** `README.md` rewritten (it was stale
  at "Phase 3 complete" and documented four of the eight verbs), plus
  `QUICKSTART.md`, `docs/USER_GUIDE.md`, `docs/model-card.md`,
  `docs/diagrams.md` (three Mermaid diagrams), and `docs/quickstart-timing.md`.
- **STEP-08 D6: `docs/POSITIONING.md`**, a capability traceability matrix.
  Five capabilities have no honest artifact and get rows saying so.
- **STEP-08 D4: the release workflow.** `release.yml` builds the sdist and
  wheel, generates a CycloneDX 1.6 SBOM from the locked runtime environment,
  publishes the container image to GHCR, and attests build provenance for the
  distributions, the SBOM and the image digest through Sigstore. GITHUB_TOKEN
  only; no PAT and no stored signing key.
- **STEP-08 D4: `Dockerfile`**, two-stage so the runtime image carries no git,
  no compiler and no uv, running as a non-root user and installing from
  `uv.lock`. CI builds it on every push and runs a complete session inside it
  with `--network none`.
- **`run-session --stub-mode {faithful,overclaim}`**, and the chosen mode is
  provenance rather than a hidden switch: it binds into the hash-chained
  `SESSION_OPEN` entry and is stamped in the session manifest, read off the
  adapter that actually served the calls so a declaration cannot disagree with
  what ran.
- **Supply chain.** `uv.lock` (50 packages, 802 recorded sha256 hashes, the
  git dependency pinned to its resolved commit), the AnalystKit pin tightened
  from a tag to commit `ab98ee6a`, every GitHub Action pinned by commit SHA,
  Dependabot on three ecosystems, and CodeQL with `security-extended`.
- **Community and repository metadata.** `SECURITY.md`, `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, `CITATION.cff`, issue and pull-request templates.
- Project scaffolding: `pyproject.toml` (hatchling, Python 3.12+), `LICENSE`
  (MIT), GitHub Actions CI (lint, type, test, rebuild-determinism job),
  `src/ts_sentry` package layout.
- STEP-01 D1: `ts_sentry.data.generator`, a seeded synthetic platform
  builder. Single `numpy.random.Generator` threaded explicitly through
  every function; base population covers accounts, channels, videos,
  comments, and engagement events in FK-safe order.
- STEP-01 D2: frozen-slots entity dataclasses (`ts_sentry.data.schema`),
  StrEnum categoricals (`ts_sentry.data.enums`), PEP 695 ID aliases
  (`ts_sentry.data.ids`), the sealed ground-truth schema
  (`ts_sentry.data.sealed`), the DuckDB DDL (`ts_sentry.data.store`), and
  the `DataScope` allowlist stub pre-seeding STEP-02
  (`ts_sentry.governance.scopes`). Every timestamp-bearing dataclass
  enforces IST tz-awareness in `__post_init__`; `EngagementEvent` enforces
  its video/channel target invariant the same way.
- STEP-01 D3: threat generators T-01..T-07
  (`ts_sentry.data.threats`), each with a parameter dataclass and a
  budget-respecting `plant()` function. Burst timing uses a documented
  Poisson-burst mixture (not a Hawkes process); a shared abuse budget
  keeps the benign-majority floor (>=97%) structurally true for any seed
  or scale.
- STEP-01 D4: sealed ground-truth persistence and Parquet export
  (`ts_sentry.data.store.persist_dataset` / `export_dataset`), with
  `sealed._labels` physically segregated under its own export directory.
- STEP-01 D5: `docs/data-dictionary.md`.
- STEP-01 D6: the AnalystKit-backed quality gate
  (`ts_sentry.data.quality`), wrapping `profile`/`validate`/`reconcile`
  rather than reimplementing DAMA checks. Timeliness is reported but not
  gated (wall-clock-relative recency has no meaning against a fixed
  historical build window); Accuracy is gated via `reconcile` against
  `sealed._labels` instead of a profile percentage.
- STEP-02 D1: `ts_sentry.governance.mandate` - the `Mandate` frozen-slots
  dataclass plus the `AgentId`, `ToolId`, `Consequence`, `VerdictKind`, and
  `RefusalCode` StrEnums, the `mandate_hash` canonical SHA-256, and
  `validate(action, mandate) -> Verdict` (pure, total, never raising).
  `DataScope` is imported from `ts_sentry.governance.scopes`, not redefined.
  `Mandate` carries an explicit SemVer 2.0.0 `version` field, validated in
  `__post_init__` and included in the canonical hash form.
- STEP-02 D2: `ts_sentry.governance.signature` - the human-only ENFORCE
  construction path. `Consequence.ENFORCE` is excluded from mandate ceilings
  at type level by the `AgentConsequence` PEP 695 alias and again at runtime
  in `Mandate.__post_init__`; `enforce_consequence` is the only function
  producing ENFORCE for use, and requires a `HumanSignature` whose digest
  recomputes from (analyst_id, decision, subject_hash, signed_ts).
- STEP-02: `ts_sentry.governance.canonical` - a separator-joined encoding for
  hashing flat field sequences, used by `governance.signature` and, from D3,
  by the ledger chain. Replaces ARCHITECTURE 3.2's literal `a || b`
  concatenation, which is ambiguous (distinct field splits collide on one
  digest); recorded as an ARCHITECTURE erratum. Structured objects keep a
  separate canonical-JSON convention: `mandate_hash` is its only user today.
- STEP-02 D3: `ts_sentry.governance.ledger` - the append-only, hash-chained
  trajectory ledger (DuckDB `governance` schema plus JSONL export) with the
  eleven ARCHITECTURE 3.2 event types, an `OrchestratorToken` required for
  every write, and `verify_chain` as the single verification core both D6
  readers use. Appends are O(1) in lookups (cached tail, no rescan on write).
  The hashed timestamp is a canonical IST ISO 8601 string in its own column,
  not a `TIMESTAMPTZ`: DuckDB renders `TIMESTAMPTZ` in the reader's session
  time zone, which would have made an intact ledger verify locally and report
  a false broken chain in CI.
- STEP-02 D4: `ts_sentry.governance.gates` - the consequence-gate pipeline.
  OBSERVE auto-approves; ASSEMBLE and RECOMMEND run injected checkers
  (`ArtifactCheck` protocol, no defaults, so an unconfigured gate cannot
  auto-approve); ENFORCE opens only for an approving `HumanSignature`.
  Failures are returned, never raised, and are ledgered as
  `VERIFICATION_FAIL` + `GATE_REJECTION`. `guard_scope_request` completes
  STEP-02 3.5: a sealed-scope request is refused and ledgered as
  `MANDATE_VIOLATION_ATTEMPT`.
- STEP-02 D5: `ts_sentry.governance.verifier` - the claim-to-evidence
  symbolic verifier. Per-claim reason codes, zero tolerance (one failing
  claim fails the report), and an adapter plugging it into the RECOMMEND
  gate. Deliberately generic so STEP-03 can reuse it for triage rationales.
- STEP-02 D6: `ts-sentry verify-ledger PATH [--expect-head COUNT:HASH]`.
  Dispatches by extension (`.jsonl`, `.duckdb`) onto one shared verification
  core, always reports the chain head, and exits 0 intact / 4 broken chain
  (first broken seq printed) / 5 input error / 6 head mismatch.
  `verify-ledger` never exits 2: argparse's own usage errors are translated
  to 5, so malformed input behaves identically across supported Python
  versions and no exit code carries two meanings. `build-dataset` keeps
  argparse's stock exit 2, unchanged.
- PEP 561 `py.typed` marker for `ts_sentry`. The package was previously
  installed untyped, so mypy runs outside the repo's own config could not see
  its annotations at all.
- STEP-03 D1: `ts_sentry.orchestrator.core` - the synchronous session state
  machine. `SessionState` transitions come from one exhaustive `match` closed
  by `assert_never`, and the published `TRANSITIONS` table is derived from it
  rather than written beside it. Illegal transitions raise (a caller bug);
  budget exhaustion returns a `CloseReason` and closes the session cleanly
  with partial results ledgered (STEP-03 3.3). Clocks are injected, so no
  component reads wall time except through `SystemClock`. `SESSION_OPEN`
  carries a mandate-set hash binding the entry to the exact fleet
  configuration the session opened with.
- STEP-03 D1: `ts_sentry.orchestrator.manifest` - the session manifest, which
  records the ledger's expected head (entry count plus final `entry_hash`) at
  `SESSION_CLOSE`. This is the anchor STEP-02 deliberately did not build, and
  it discharges the third obligation that phase carried forward.
- `ts_sentry.provenance` - `git_sha()` and `sha256_file()`, shared by the
  build manifest and the session manifest so both stamp provenance the same
  way.
- STEP-03 D2: `ts_sentry.orchestrator.firewall` - the input firewall (OWASP
  LLM01). Case content enters model context as fenced, JSON-encoded data and
  never as an instruction. The fence nonce is a digest of the content it
  fences, so closing the fence from inside the data is a preimage problem
  rather than a guess. Two copies are kept, which is how D2's
  instruction-stripping pass and 3.2's verbatim-preservation requirement are
  both satisfied: the verbatim block is what artifacts store, and a redacted
  copy with markers naming each detected pattern is what reaches a model.
  `SystemPrompt` is a hash-identified type the adapter takes instead of a
  bare string, so case content cannot reach the system role by concatenation.
  Detection runs a versioned, hashed pattern set across seven families and
  emits `InjectionSignal` records for ledgering.
- STEP-03 3.2: the injection fixture corpus (`tests/test_firewall.py`), split
  into fixtures the pattern set catches and fixtures it does not. The second
  group is asserted as undetected, so the module's honest limit (pattern
  matching cannot be complete) is tested rather than only written down.
- STEP-03 D3: `ts_sentry.orchestrator.tools` and `ts_sentry.orchestrator.dispatch`
  - the allowlisted tool table and the dispatch pipeline (mandate check ->
  tool table -> execute -> schema check -> consequence gate -> ledger). A
  proposal carries a tool *name* and scope *names*, resolved through
  allowlists where absence is denial, because that is the real shape of the
  agent boundary. Consequence comes from the table, never from the proposal,
  so an agent cannot understate what an action costs to fit under its ceiling.
  `validate` stays pure; dispatch is the caller that ledgers every refusal,
  and scope refusals reuse `gates.guard_scope_request` rather than
  reimplementing it.
- STEP-03 D3: `RefusalCode.TOOL_HANDLER_NOT_IN_BUILD`, distinct from
  `TOOL_NOT_ALLOWED`. A tool declared in the table whose handler lands in a
  later phase is refused as a build limitation and ledgered as
  `GATE_REJECTION`, never as `MANDATE_VIOLATION_ATTEMPT`: counting a build
  limitation as a governance violation would inflate the exact metric this
  system showcases.
- STEP-03 D4: `ts_sentry.orchestrator.adapter` - the single model boundary.
  `StubAdapter` is deterministic, seeded, and the default; `LiveAdapter` is
  gated on `TS_SENTRY_LLM_MODE=live`, checks only that `ANTHROPIC_API_KEY`
  exists (never its value), and imports the vendor client inside the call so
  an offline run never loads it. Retries use exponential backoff with full
  jitter over a seeded generator, and the vendor SDK's own retries are
  switched off so there is exactly one retry authority. `call_model` checks
  the mandate budget before sending, ledgers `PROMPT_SENT` before the call,
  and books actual usage after. A provider refusal (`stop_reason: refusal`)
  is its own error class and is never retried.
- `tests/conftest.py` strips `TS_SENTRY_LLM_MODE`, `TS_SENTRY_LLM_MODEL`, and
  `ANTHROPIC_API_KEY` for the whole test session, so "the suite costs nothing"
  is a property of the repository rather than of a developer's shell.
- STEP-03 D5: `ts_sentry.agents.triage` - the triage agent. A deterministic
  weighted-sum scorer over severity_class, spread, velocity and recidivism
  with published weights (`WEIGHTS_VERSION`), every row rendering as its
  component vector rather than a bare number. Rationales cite bracketed
  component ids namespaced by case, so a rationale citing another case's
  evidence fails.
- STEP-03 D5: `ts_sentry.orchestrator.detection_stub` - the flagged-entity
  queue ARCHITECTURE 4.1 assumes and STEP-01 never shipped. Deterministic and
  seeded, reading only `DataScope`-resolvable tables. Severity here is a
  heuristic stand-in signal, not ground truth: there is no sealed influence,
  direct or derived, and a test asserts that against the SQL.
- STEP-03 D5: `ts_sentry.orchestrator.rationale_check`, reusing the STEP-02
  symbolic verifier with evidence ids = score component ids (STEP-03 3.5),
  and `ts_sentry.orchestrator.triage_turn`, which drives the ARCHITECTURE 3.3
  pipeline once end to end.
- STEP-03 D5: `ts_sentry.orchestrator.toolspec` splits the tool contract from
  the tool table, and `ToolResources` gives handlers what the orchestrator
  holds, kept separate from the agent-supplied params.
- STEP-03: the signature import-graph test (`tests/test_import_graph.py`),
  the first obligation carried from STEP-02. Worded per the sealed
  two-consumer model and enforced over the transitive first-party closure.
- STEP-03: the no-orphan ToolId countdown now binds at full strength. The
  pending-handler set shrank from four to three when the triage handler
  landed, and a phase that passes its deadline without landing a handler
  reddens the suite.
- STEP-03 D6: `ts-sentry run-session --agent triage --seed-dataset PATH`, plus
  `ts_sentry.orchestrator.session_runner` and `ts_sentry.orchestrator.fleet`.
  Opens a session, runs one agent turn, closes, and writes `ledger.jsonl`,
  `ledger.duckdb`, `ranked_queue.json`, `session_events.json`, and
  `session_manifest.json`. The dataset is opened read-only, matching the
  OBSERVE ceiling. Offline and free by default: live mode needs `--llm-mode
  live` *and* `TS_SENTRY_LLM_MODE=live`, so the intent is expressed twice.
  Exit codes 0 intact / 4 broken chain / 5 input error.
- STEP-03 D6: `verify-ledger --expect-head-from MANIFEST` reads the anchor out
  of a session manifest, mutually exclusive with `--expect-head`. This joins
  the comparison verb STEP-02 shipped to the storage STEP-03 D1 built; without
  it the anchor would exist but nothing would read it.
- STEP-04 D1: `ts_sentry.orchestrator.pivots` - the pivot vocabulary and its
  five reviewed, parameterized DuckDB templates (`SHARED_METADATA`,
  `TEMPORAL_CORRELATION`, `ENGAGEMENT_EDGE`, `INFRA_OVERLAP`, `ACCOUNT_LINK`).
  Zero dynamic SQL: table names come from `resolve_table`, every runtime value
  is bound with `?`, and no parameter ever selects a column or a table. Where a
  pivot spans two metadata fields or filters a category, the template covers
  every case in its own fixed text and the parameter is a value compared
  against an `'any'` sentinel. Parameters are typed and bounds-checked, and
  entity-id parameters must resolve to a node already in the pack, so a pivot
  expands from the analyst-selected seed rather than naming arbitrary entities.
  No template selects a free-text column: user-authored text reaches a model
  through the input firewall or not at all.
- STEP-04 D3: `ts_sentry.agents.evidence.pack` - the Evidence Pack (entity
  graph, timeline, per-record provenance). Referential integrity and provenance
  completeness are enforced in `__post_init__`, so a dangling edge or an
  untraceable record cannot be constructed at all; the D4 gate judges
  well-formed packs rather than malformed ones. A pivot returning zero rows
  keeps its provenance record, because a question asked and answered in the
  negative is an investigative step and a pack that forgot it could not be told
  apart from one where nobody ran the pivot. Carries a documented W3C PROV
  mapping (records as `Entity`, one pivot execution as `Activity`, agency left
  to the ledger) without claiming PROV conformance.
- STEP-04 D2: the evidence agent (`ts_sentry.agents.evidence`) and the ledgered
  approve/reject loop. The agent proposes `(pivot_kind, params, reason citing
  pack record ids)`; `orchestrator.proposal_check` verifies the citation with
  STEP-02's symbolic verifier and the parameters with the D1 bounds checker;
  `orchestrator.review` is the analyst boundary; `orchestrator.pivot_tool`
  executes; `orchestrator.evidence_turn` sequences them. All checks run
  *before* the analyst is asked, so an unsupported or malformed proposal never
  reaches a human. Rejection is terminal for a proposal and the agent may
  propose an alternative; proposals are bounded by the mandate's `max_steps`,
  and a rejected proposal costs a step exactly as an approved one does.
  `SessionState.AWAITING_ANALYST` gets its first driver.
- STEP-04 D2: `reviewer_kind` is recorded **inside** the ledgered
  `HUMAN_DECISION` payload, so the hash chain covers it and a body edited
  afterwards no longer digests to the entry already in the chain.
  `ScriptedReviewer` is the deterministic CI path and says so in every record
  it produces; `InteractiveReviewer` prompts a real person, is marked
  `no-cover`, and has not been run. No rendering anywhere shows an approval
  without showing what made it.
- STEP-04 D4: `ts_sentry.orchestrator.pack_gate` - the ASSEMBLE gate's checker,
  filling the `ArtifactCheck` STEP-02 shipped and deliberately left
  unimplemented. Runs over the whole pack after every hop. Adds three checks
  the type cannot make: the artifact is a pack, every cited query template
  exists in this build with the exact text the pack recorded, and hop indices
  are contiguous from zero.
- STEP-04 D5: `ts_sentry.measurement.recovery` - ground-truth network recovery
  at a pivot budget, computed measurement-side with sealed-label access and
  outside every agent mandate. Reports the structural ceiling alongside the raw
  fraction, because a ring that is mostly comments cannot be fully recovered
  into a pack however well the agent performs. Cases whose subject carries no
  planted ring are counted separately rather than folded in as zeros.
- STEP-04 D6: `ts_sentry.orchestrator.pack_export` - GraphML and JSON export.
  Written with `xml.etree.ElementTree` rather than a graph library, against the
  GraphML specification. Every node and edge carries its provenance id.
- STEP-04 D6: `ts-sentry run-session --agent evidence --subject ID` runs an
  evidence session end to end, with `--case`, `--review scripted|interactive`
  and `--max-hops`. A surface STEP-04 does not enumerate, added because its own
  exit checklist requires a ledgered session to inspect.

- STEP-05 D1: `ts_sentry.data.policy_corpus` - the hashed, anchored policy
  corpus (ARCHITECTURE 6.2). Anchors derive from clause **headings**, never
  from position, so inserting a clause cannot renumber the citations below it.
  Two digests with different jobs: `content_digest` covers what the repository
  committed and is the identity a memo citation pins; `retrieval_sha256` covers
  the raw bytes one fetch received and is provenance only. `load_corpus`
  re-derives both and refuses a corpus that does not match its manifest, so an
  edited clause file cannot resolve a citation.
- STEP-05 D1: `ts_sentry.data.policy_fetch` - the fetch-once script. stdlib
  `urllib.request` and `html.parser`, no new dependency, following STEP-04's
  hand-written GraphML. Anchors headings *and* labelled list items, because on
  the YouTube spam page every individual violation type is an `<li><strong>`
  item inside one 486-word section: heading-only anchoring would have meant a
  memo about a comment-spam ring could cite nothing narrower than that section.
  Callouts (`<div class="tip">`) become their own clauses and must be named by
  the operator; `name_callouts` refuses an unnamed callout, and a title matching
  zero or several.
- STEP-05 D2: `policies/` - corpus v1. Three verbatim public YouTube policy
  documents, 30 clauses, plus `ts_sentry.data.policy_sources` recording which
  documents, which boilerplate was dropped, and which headings are
  operator-supplied. **Clause-level text only; whole pages are not committed**,
  and the manifest carries the fair-use posture and per-document retrieval
  provenance. `ts-sentry fetch-policies` is the operator verb that produces it;
  CI never runs it and every test loads the committed corpus offline.
- STEP-05 D3: `ts_sentry.agents.memo.memo` - the memo as a DSA Article 17
  statement of reasons, built against the Regulation's retrieved text rather
  than from recall (EUR-Lex CELEX 32022R2065). Four sentence roles mapping to
  Art 17(3)(b), (e), (a) and (f); `Measure` is a fixed StrEnum drawn from Art
  17(1)(a)-(d), so no memo can invent a sanction. A `FACT` with no evidence and
  a `POLICY_GROUND` with no citation are **unconstructible**, and a memo missing
  any of the four roles is refused: Art 17(3) requires all four, so three of
  them is prose rather than a statement of reasons.
- STEP-05 D3: Art 17(3)(c), the automated-means disclosure, is carried
  **structurally** on `AutomatedMeans` and cannot be written by the agent. A
  disclosure about how automated a decision was is worthless if the automated
  component composes it, which is the argument `ReviewOutcome.reviewer_kind`
  already makes about who decided. A signed memo claiming a fully automated
  decision is refused, because ENFORCE is human-only and a signature is exactly
  the human step; the vocabulary follows the Commission's DSA Transparency
  Database schema.
- STEP-05 D5: `ts_sentry.orchestrator.citation_resolver` and
  `ts_sentry.orchestrator.citation_tool`. Four reason codes, because they are
  four different findings about an agent: unknown document, phantom anchor,
  excerpt not in clause, excerpt too long. The third is the one that matters -
  a real document, a real anchor and words the clause does not contain is a
  fabricated quotation with a valid address, which is more dangerous than a
  phantom anchor because everything checks out except the part a reader relies
  on. Whitespace is normalised and nothing else is, so a rewrapped quotation
  passes and a paraphrase does not.
- STEP-05: `ts_sentry.orchestrator.memo_gate` fills the RECOMMEND
  `ArtifactCheck` STEP-02 shipped unimplemented and `fleet` has been failing
  closed ever since. Two resolution surfaces, both zero-tolerance: claims
  through STEP-02's `verify_claims` against the pack's record ids, and
  citations through the D5 resolver. A memo names the pack it was drafted from
  and the corpus it was checked against, so the gate cannot verify a memo
  against evidence it never saw.
- STEP-05: `MEMO_MANDATE` (ceiling RECOMMEND, `max_steps` 8) with **no data
  scopes at all**. The memo agent reaches no platform table: it works from an
  accepted Evidence Pack and the hashed corpus, both lent by the orchestrator.
  `RESOLVE_POLICY_CITATION` has its handler, so the pending-handler set shrinks
  from two to one and `IMPLEMENTATION_PHASE` is 5.
- STEP-05 D4: the memo agent (`ts_sentry.agents.memo.draft`, `.prompts`) and its
  turn (`orchestrator.draft_check`, `orchestrator.memo_turn`). Flat prefixed
  lines, one sentence per line, for the reason STEP-04 chose them over JSON. The
  agent names an **anchor** and never a document digest: the orchestrator
  resolves which document carries the anchor, because a digest the agent
  supplied would let it point a citation at a document nobody checked it
  against. An anchor two documents share is refused rather than resolved to the
  first, which is a real case in corpus v1.
- STEP-05 D4: the memo goes through `dispatch(RESOLVE_POLICY_CITATION)` rather
  than calling the gate directly, so the mandate check, `TOOL_CALLED`, the
  schema check and the RECOMMEND gate all run on the governed path. No analyst
  decision is taken during drafting: a pivot acts and a draft does not, and the
  analyst's decision on a memo is the signature.
- STEP-05 D4: `ts-sentry run-session --agent memo --pack PATH` and
  `orchestrator.pack_export.read_pack_json`. STEP-04 wrote packs and never read
  one; the round trip preserves `content_digest`, which the memo's `pack_digest`
  binding depends on.
- STEP-05 D6: `ts_sentry.orchestrator.signing` and
  `ts_sentry.orchestrator.memo_export`, plus `ts-sentry sign-memo`. The AI-DRAFT
  watermark has **no suppression parameter**: rendering takes a memo and an
  optional signature and nothing else, so the only way to remove the label is to
  produce a signature that covers the memo. A signature over a *different* memo
  leaves the watermark in place and says so. A test asserts the parameter list
  itself, so a future `watermark=False` fails rather than quietly working.
- STEP-05 D6: `Memo.content_digest` **excludes `status`**. Signing sets the
  status to SIGNED, and if that flip changed the digest the signature would be
  over a value the signed memo no longer has, so the artifact would fail its own
  verification the instant it was produced. A DRAFT and a SIGNED memo with
  identical content share a digest; what distinguishes them is the existence of
  a signature.
- STEP-05 D6: the RECOMMEND gate now **refuses a SIGNED memo** (HALT-2 finding
  4). Re-gating answers the wrong question: what makes a signed memo trustworthy
  is that its digest recomputes and the signature verifies, and accepting one
  here would let it be re-laundered through the agent path and emerge with a
  fresh `VERIFICATION_PASS` that says nothing about the signature.
- STEP-05 D6: `orchestrator.signing` is a **deliberate third entry** in
  `LEGITIMATE_SIGNATURE_CONSUMERS`. A new consumer required editing that named
  list in the same commit as the module rather than appearing unnoticed, which
  is the allowlist working. `agents.*` still cannot reach it.
- STEP-05 D7: the overclaim fixture suite. Every planted defect asserts its
  **reason code**, not merely that something failed: a suite checking only "this
  was rejected" would pass if the gate rejected everything for the wrong reason,
  and refusals that cannot be counted by cause make `GATE_REJECTION` counts
  meaningless. Covers unsupported claims, phantom anchors, unknown documents,
  fabricated quotations, excerpt overruns and underruns, mid-word quotations,
  and wrong pack and corpus bindings, each as a one-defect mutation of a passing
  control.
- STEP-05 3.5: the hypothesis property. A memo that passes verification carries
  no sentence whose evidence ids are absent from the pack, generated over
  arbitrary id sets. Checked in the soundness direction, which is the claim the
  gate makes about itself, rather than as a claim about coverage.
- STEP-04 follow-up: `ts_sentry.orchestrator.subject_check` - an evidence
  session refuses a `--subject` that does not exist in the dataset, exiting `5`
  and producing no session and no chain. The check runs before the output
  directory is created and before any ledger connection exists, because a
  refusal after the session opened would already have written the chain,
  manifest and anchor it exists to prevent. Found at phase close: a session on a
  nonexistent subject had produced a fully valid audit trail for an
  investigation of nothing. The assembly gate validates the artifact's internal
  consistency, not its correspondence to reality, and seed-existence is the
  boundary check that ties the audit trail to a real subject. Asked of the
  entity tables through `resolve_table`, never of `sealed._labels`: whether an
  entity is *planted* is ground truth.
- STEP-06 D1: the versioned prompt registry (`ts_sentry.prompt_registry`), plus
  the committed `prompts/` directory. Files are content-hash-named, so a version
  edited in place no longer hashes to its own name and the tampering is visible
  with `sha256sum` alone. Two identities are recorded per version and both are
  re-derived on load rather than believed: `content_digest` over the text's bytes
  (the file name) and `system_prompt_sha256` from `firewall.system_prompt` (what
  a session's `PROMPT_SENT` payload already carries). Following DECISIONS 5.2,
  where the policy corpus learned the same distinction.
- STEP-06 D1: activation is an **append-only pointer history**
  (`prompt_registry.activation`), not a field on a version record. A mutable
  `active` flag would mean activating one prompt rewrites another prompt's
  record, which is the overwrite STEP-06 3.4 forbids; the active version of a
  task is therefore derived from the log, and version records are written once.
  Rollback is another entry, so a history never describes a system that did not
  make the mistake it made, and its target must be a version the task has
  actually run.
- STEP-06 D1: `classify.threat_class.v1`
  (`ts_sentry.agents.prompt_eval.prompts`) - the classification prompt this
  phase evaluates, and the only registry task whose output is a class label.
  **No session consumes its output** (decision A); see Known limitations.
- STEP-06 D1: the three shipped prompts (`triage.rationale.v1`,
  `evidence.pivot.v1`, `memo.statement.v1`) are registered at the exact bytes
  they have run under since their own phases, closing the deferral
  `orchestrator.firewall.SystemPrompt` recorded ("a versioned registry with
  content-addressed files is STEP-06"). Record-only, with **zero behaviour
  effect**: digests unchanged, module constants still the runtime source, and a
  test asserting every registered version reproduces its constant's `prompt_id`,
  text and digest so the two cannot drift.
- STEP-06 D2: the labeled eval set. `ts_sentry.data.eval_build` (build-time,
  reads `sealed._labels`, on `LEGITIMATE_SEALED_CONSUMERS`),
  `ts_sentry.data.eval_set` (the item side, no labels anywhere in it), and
  `ts_sentry.orchestrator.eval_labels` (the label store, in
  `FORBIDDEN_FOR_AGENTS`), plus the committed `evals/threat_class/` artifact:
  59 items stratified across T-01..T-07 with benign controls at 25%.
- STEP-06 3.2: contamination discipline made structural rather than procedural.
  `EvalItem` carries `item_id` and `content` and **has no third field**, so a
  label has nowhere to sit; item ids are opaque (`item-0000`) because planted
  entity ids name their own class in three characters (`t02_chan_000_000`); the
  renderer never emits an entity id, a ring id, or an infrastructure signal
  *value*, and `_refuse_leaky_item` fails the build if one appears. The label
  store exposes no `label_of`: it grades and returns counts.
- STEP-06 D3: the eval harness (`ts_sentry.orchestrator.prompt_eval`), the
  `RUN_PROMPT_EVAL` handler (`orchestrator.eval_tool`) and the turn that drives
  it (`orchestrator.prompt_eval_turn`). Reports precision, recall, F1 and
  per-class confusion for both versions, plus a **paired percentile bootstrap**
  on every per-class recall delta. Paired because the question is whether the
  candidate is worse *than the incumbent on the same items*; percentile rather
  than a normal approximation because at these sizes the approximation reports
  uncertainty about a class collapse the data settles completely, and puts part
  of the interval below -1.
- STEP-06 D3: `PROMPT_EVAL_MANDATE`, the fourth and last agent mandate. The
  fleet is complete, `IMPLEMENTATION_PHASE` is 6, and `pending_handlers` is
  **empty for the first time**, discharging `orchestrator.tools`' standing claim
  that "by STEP-06 the table is fully executable".
- STEP-06 D4: the regression gate (`ts_sentry.orchestrator.regression_gate`).
  `decide(report, tolerances)` is pure and total, with the hypothesis property
  for 3.5 asserting same-inputs-same-verdict over generated reports and an AST
  test asserting it reads nothing else. Fail-closed per decision D: activation
  requires the confidence interval to *exclude* a drop beyond tolerance, so
  absence of evidence of regression is not evidence of absence.
- STEP-06 D4: `RECALL_REGRESSION` and `REGRESSION_NOT_EXCLUDED` are distinct
  breach codes. The first says the candidate is measurably worse; the second
  says the eval set cannot tell. Conflating them would blame a prompt for the
  eval set's resolution.
- STEP-06 D4: declared tolerances in `evals/threat_class/tolerances.json`,
  hashed, with the rationale for each number in the file an operator edits.
  `recall_drop` is 0.25 because that is one item on the coarsest class (support
  4), which is the finest change the instrument can register there. No defaults
  and no fallback: an unconfigured gate refuses to run.
- STEP-06 D7 (added deliverable, not in the STEP-06 table): the degraded-prompt
  fixture suite. Three real degraded prompt versions - class collapse, broken
  output contract, and a noisier-but-not-worse variant - each asserting its
  **breach code**, plus the strongest available passing control: the incumbent
  evaluated against itself, which must be activatable or every refusal proves
  nothing. The noisy fixture is the one that proves decision D earns its cost.
- STEP-06 D5: `ts-sentry eval-prompts --candidate HASH`, plus
  `ts_sentry.orchestrator.eval_session`. Exit `0` when the candidate may be
  activated and **`EXIT_REGRESSION_REFUSED = 7`** when the gate refuses it, both
  printing the report path. Joins `TRANSLATES_USAGE_ERRORS` from the start, so a
  usage error exits 5 and never argparse's 2.
- STEP-06 D6: the eval report artifact (`ts_sentry.orchestrator.eval_report`),
  in Markdown and JSON, stamped with dataset seed and scale, eval-set item and
  label digests, both prompt digests, adapter and model id, bootstrap seed,
  tolerances digest and git SHA. Carries the precision-is-not-a-deployment-
  estimate caveat and the class-collapse-not-drift resolution caveat **in the
  artifact itself**, and no per-item rows.
- STEP-06 3.3: `tolerances_sha256` binds into `SESSION_OPEN` rather than
  becoming a twelfth `EventType`, on DECISIONS 5.8's precedent for corpus
  updates. ARCHITECTURE 3.2's eleven event types stay a closed surface.
- STEP-07 D1: the VVR lens. `ts_sentry.measurement.frame` (the view frame,
  risk-proxy strata and the sealed truth join), `.raters` (a confusion matrix
  per simulated reviewer, with panel aggregation) and `.vvr` (stratified
  SRSWOR sampling with a finite population correction, the 95%
  normal-approximation interval, checked validity conditions, and a stratified
  bootstrap cross-check). The sampling unit is the **view**, strata are bands
  of an observable risk proxy plus a "no score available" stratum, and
  allocation is **optimal rather than proportional**.
- STEP-07 D1: allocation and variance validated against **external published
  reference numbers**, not against our own expectations. Barnett's Table 2B
  optimal allocation (2098/828/584/256/234 at a 4,000-view sample) reproduces
  exactly from his population shares and rates, and his published standard
  error of 0.054 percentage points reproduces from the variance formula
  independently of the allocator.
- STEP-07 D1: the optimal-allocation prior comes from a **pilot sample's rater
  decisions**, never from `sealed._labels`. `allocate_optimal` takes two
  mappings of numbers, asserted by signature and by a test that inverted ground
  truth leaves the allocation byte-identical; the pilot is discarded from the
  estimate it shaped.
- STEP-07 D2: `ts_sentry.measurement.sensitivity`, the three curves as
  byte-stable JSON and CSV, and `.plots`, deterministic Agg rendering. Curve
  data is identical across runs and machines; PNGs are asserted byte-identical
  across two renders **in the same environment** only.
- STEP-07 D2: the policy-scope simulation reports **both arms**. Widening the
  class set is measured to be exactly null on this corpus and the null is
  reported with its explanation; the comment-attribution arm moves the rate and
  supplies the required direction, and carries `is_faithful_vvr = False`
  because it changes the attribution rule rather than the class set.
- `matplotlib>=3.8` as a runtime dependency (STEP-07 D2 renders figures, and
  D5's `report` verb renders them at runtime rather than only under test).
- STEP-07 D3: `ts_sentry.measurement.workflow`, the workflow lens. Measured
  counts (governance activity, verification pass rate) and the **modelled**
  analyst-minutes comparison are separated in the types and in the rendering.
  The minutes model is a TSPA-grounded sensitivity model over a visible
  assumption table, reporting a delta with its one-way sensitivity and a
  **break-even** rather than a headline figure, because **no published per-case
  review-time benchmark exists** to compare against. Governance activity is a
  mandatory section and renders when every count is zero, with a note that zero
  means nothing exercised the gates.
- STEP-07 D3: a **traversing** evidence strategy
  (`orchestrator.evidence_turn`), discharging the two defects STEP-04 carried
  into this phase. A work list of `(pivot, entity)` pairs built from the pack in
  pack order chains discovered entities as new seeds, varies pivot kind, and
  makes an empty pivot advance to a different question with no special case.
  Four of seven threat classes now recover strictly more at every budget, and
  T-06 becomes budget-sensitive.
- STEP-07 D4: `ts_sentry.measurement.report`, the stamped measurement report in
  Markdown, HTML and JSON. Stamped with dataset seed and scale, dataset digest,
  git SHA, policy corpus version and hash, and the active prompt version per
  task. Honest Limits is mandatory and asserted entry by entry into every
  rendering; the comment-attribution arm is labelled NOT a VVR everywhere it
  appears; and causal language is asserted absent from the rendered document.
- STEP-07 D5: `ts-sentry report --session PATH --out PATH`, with an optional
  `--build` for the platform lens. Without it the report states that the lens
  was not computed rather than omitting the section.

### Changed

- **`test_recovery_saturates_before_the_smallest_reported_budget` replaced by
  `test_the_budget_axis_carries_information`.** It was written to fail the day a
  strategy traversed, it did, and its own instruction was to rewrite rather than
  delete it. The STEP-04 Outcome's saturation note is rewritten with the
  measured before/after table.
- **`IMPLEMENTATION_PHASE` is 7**, and
  `test_this_phase_landed_the_handler_it_owed` is rewritten as
  `test_every_phase_that_owed_a_handler_landed_exactly_one`. The old form
  asserted that the current phase owes exactly one handler, which is false by
  construction from STEP-07 onward because measurement is not a tool an agent
  may invoke. The guarantee is restated over the finished countdown; all three
  failure directions survive.
- **The measurement-reachability import test quantifies over the whole
  `measurement` package** instead of pinning `measurement.recovery` by name,
  which would have left the five new modules unguarded.
- **`dataset_digest` now derives from the build manifest's `table_hashes`**
  rather than from `sha256(build.duckdb)`, closing the gap STEP-03 recorded and
  carried. The store is not byte-stable across rebuilds even when its contents
  are, so session ids changed on every rebuild of the same seed; the Parquet
  exports the manifest hashes are byte-stable, which STEP-01 verified and CI
  re-verifies. Carries a `v2` domain separator, so a pre-fix and a post-fix
  identity for one build cannot collide. **Session ids from before this change
  are not comparable with ones after it.** A build without a
  `build_manifest.json` is now an input error; there is deliberately no
  fallback to hashing the store, because a silent fallback would restore the
  defect in the case where it is hardest to notice.
- `derive_session_id` takes discriminators, so a triage session and an evidence
  session over one dataset no longer share an id. Found by running the CLI: with
  one kind of session in the world, analyst plus dataset identified a session,
  and with two it stopped doing so.
- `BudgetTracker.check` takes `require_step`, so a turn is not refused for the
  step `begin_turn` already booked. Every mandate's last step was unusable and
  `max_steps` quietly meant one fewer than it said; one turn per session hid it
  for the whole of STEP-03. The step ceiling is unchanged and still enforced at
  `begin_turn`.
- `EVIDENCE_MANDATE.max_steps` is 20, because STEP-04 3.5 reports recovery at 20
  pivots and a reported budget the mandate forbids is not a measurement.
- **`mandate_set_hash` changes for every session type**, because the fleet
  gained a third mandate. Measured: `684b49b9...` becomes `02ed4726...`, while
  the per-agent triage and evidence mandate hashes are untouched. `SESSION_OPEN`
  carries the set hash, so **chain heads recorded before STEP-05 do not
  reproduce**, including Saif's STEP-04 phase-close head. The same class of note
  as STEP-04's `dataset_digest` v2 change, and stated for the same reason: a
  recorded head that silently stops reproducing looks like tampering.
- `require_ist_iso` moved from a private helper in `agents.evidence.pack` to
  `ts_sentry.data.tz`, now that the policy corpus is the second store keeping
  timestamps as text. Same reason `ist_from_epoch_ms` lives there: a second
  spelling of the check is a second chance to accept a UTC-rendered timestamp.
- The bracketed citation syntax moved to `ts_sentry.agents.citations`, now that
  two agents parse it, and is re-exported from `agents.triage.rationale` so
  STEP-03's callers are unchanged. The epoch-to-IST conversion moved to
  `data.tz` for the same reason.
- `fleet.PHASE_THREE_CHECKS` is now `PHASE_FOUR_CHECKS`, with a real ASSEMBLE
  checker. RECOMMEND still fails closed until the memo agent needs it.

- `ChainHead` and `chain_head` moved from `ts_sentry.cli.main` to
  `ts_sentry.governance.ledger`. No behavior change: the session manifest and
  `verify-ledger` need the identical spelling of a chain head, and
  `orchestrator` must not import from `cli` to get it. `Ledger.head` now
  answers from the cached tail without rescanning the chain.

### Fixed

- `run-session` exited `2` on argparse usage errors, colliding with
  `EXIT_QUALITY_GATE_FAIL`, so a mistyped flag was indistinguishable from a
  failed data-quality gate. STEP-02 removed that collision for
  `verify-ledger`; `run-session` reintroduced it by arriving in STEP-03
  without the translation, while the README already documented it as exiting
  0, 4 or 5. Its usage errors now route through the same `_UsageError`
  translation and exit `5`. `build-dataset` keeps argparse's stock exit `2`,
  which is its published STEP-01 contract.
- Detection stub: the flagged queue did not discriminate. Undisclosed synthetic
  media was a flag trigger and holds for 64 of 66 channels on the seed-42
  build, so every case scored an identical severity and the ranking collapsed
  to a velocity sort while the real rings never reached the queue. Found by
  Saif reading a real `ranked_queue.json`. Undisclosed media is now a severity
  contributor rather than a trigger; signals carried by accounts *commenting*
  on a channel now count when at least two share one value (measured: every
  link-domain and shared-device holder owns no channel and comments on eleven,
  so a channel-centric queue was blind to comment-side rings); and recidivism
  is redefined as pattern persistence across days, because counting a
  subject's own observation days is structurally zero on this data.
- Triage rationales were uninformative: every one cited `severity_class`,
  the largest component on every row and therefore the one that explained
  nothing about the ordering. The builder now cites the component that most
  differentiates a case from its rank-neighbours, with deterministic
  fallbacks. An informativeness fix in the rationale builder, not a change to
  what the verifier accepts.
- Detection stub: reading `TIMESTAMPTZ` columns into Python failed on the
  missing optional `pytz` dependency, and casting them to text would have been
  worse - DuckDB renders a `TIMESTAMPTZ` in the reader's session time zone, so
  the recidivism component (which counts distinct observation days) would have
  produced different priorities on a Kolkata machine than in a UTC CI runner,
  with neither looking wrong. Timestamps are now selected as `epoch_ms(...)`,
  which carries no rendering at all; verified identical under three reader
  time zones and pinned by a test. Same class of defect STEP-02 D3 avoided in
  the ledger, found in a new place.
- Input firewall: redaction markers were forgeable. Case content containing
  the literal string `[ts-sentry: instruction-shaped text removed: ...]`
  produced a model-facing block in which an attacker-planted marker was byte
  identical to one the firewall wrote, so a reader could not tell which
  annotations were the orchestrator's, and a payload could claim to have
  already been neutralized. Found by an adversarial fixture Saif constructed
  for exactly this. Markers now carry the block's nonce, which content cannot
  hold without a preimage, so genuine markers are decidable.
- Input firewall: case content containing U+2028, U+2029, NEL, VT, FF, FS, GS
  or RS could forge an extra record inside a fenced block. `json.dumps`
  escapes newline and carriage return, but not those, and `str.splitlines`
  breaks on all of them, so a one-object-per-line encoding was not in fact one
  object per line. Found by a hypothesis property during D2, not by
  inspection. Those characters are now escaped in the encoded record.

### Known limitations

- The VVR lens's baseline estimand is **narrow by construction and narrower
  still on this corpus**. It counts a view as violative only when the viewed
  video's own label is a non-benign, non-spam class, which is what the
  published method judges. On the seed-42 build that is 18 views out of 18,780
  (0.0958%), and all 18 come from T02 and T07; every other class contributes
  zero because only those two threat modules plant view events. T04, the class
  whose name most suggests it should dominate a provenance-stratified estimate,
  receives **no views at all**.
- **The normal approximation is invalid at every realistic sample size on this
  corpus** and becomes valid only at a full census, because 18 violative views
  cannot supply the ~10 expected successes it needs. Every estimate reports the
  failed condition rather than suppressing it, and the bootstrap cross-check
  exists for exactly this reason. The honest operating regime here is large
  sampling fractions, where the finite population correction is load-bearing;
  that is the opposite of the published method's regime.
- **Only two of the five risk strata hold any views.** Viewed videos take just
  two distinct observable profiles, so no choice of band cut points can
  populate the others. The cuts are left at untuned equal quarters and the
  result is reported rather than engineered around.
- **Rater error is modelled as independent per rater.** Correlated error, such
  as a policy misreading that a whole panel shares, is not modelled and would
  not be suppressed by majority voting. Since a three-rater majority suppresses
  independent error quadratically, the D2 bias curve understates what a real
  panel with shared training could get wrong.
- **The risk proxy is not a detector and has no measured precision or recall.**
  It is an analog of the published method's classifier-score stratification
  built from content-provenance features, and it is used only to form strata.
  Nothing here should be read as a detection result.
- **Arm B of the policy-scope simulation is not a VVR** and is flagged as such
  in the type, the curve note and the rendered figure. It changes the
  attribution rule so a video counts when it hosts a violating comment, which
  the published method does not do.
- **PNG figures are byte-identical across two renders in one environment
  only.** Cross-version stability is not claimed and not tested. The byte-stable
  reproducibility artifact is the curve data in JSON and CSV.
- **The analyst-minutes model is modelled, not measured.** No published
  per-case review-time benchmark exists to compare against: TSPA, which defines
  the metric, notes that review time may mean the queue wait or only active
  handling and warns of "considerable industry variation in both precise
  definitions and naming conventions", and DTSP assesses maturity qualitatively
  rather than publishing handling-time figures. Every minute figure is a stated
  assumption and the section reports a sensitivity range and a break-even, never
  a saving.
- **Evidence recovery plateaus.** The metadata-pivot strategy recovers the
  shared-registration-linked core and cannot reach ring members connected only
  by looser signals: on `t02_chan_000_000` it reaches 4 of 8 members and the
  budget curve is flat from 5 pivots onward. A bounded limit of a metadata-pivot
  strategy rather than a defect. Reaching the rest needs behavioural or temporal
  pivots, and `TEMPORAL_CORRELATION` cannot be proposed while the evidence
  prompt carries no timestamp anchor. Recorded as future work, not a STEP-07 gap.
- **Rater error is modelled as independent per rater.** Correlated error, such
  as a policy misreading a whole panel shares, is not modelled and would not be
  suppressed by majority voting, so the bias curve understates what a real panel
  with shared training could get wrong.
- Firewall detection does not survive zero-width characters inside a keyword
  (U+200B and similar). They break regex tokens without breaking lines, and
  matching through them would mean normalizing text the analyst never sees,
  which is a normalization decision rather than a pattern fix. Exotic
  *whitespace* is covered, since Python's `\s` is Unicode-aware and matches
  U+00A0; exotic *invisibles* are not. Asserted as an undetected fixture.
- Firewall exfiltration detection anchors on `https?://`, so other schemes and
  UNC paths are not matched. Widening it to any scheme-like token would fire
  on benign comments discussing links. Asserted as undetected fixtures.
- Firewall instruction detection is pattern-based and cannot be complete. The
  load-bearing controls are structural: case content is fenced JSON data, the
  system role is a hash-identified constant no case text can reach, and agent
  output is checked by the symbolic verifier rather than trusted. The pattern
  pass is defense in depth, and its product is a ledgered signal that an
  attempt happened, whether or not it would have worked.
- Ledger chain verification still cannot detect entries removed from the *end*
  of a chain: what remains is a shorter chain whose every link still
  recomputes. The STEP-03 session manifest now stores the anchor that catches
  it, but an anchor is only as independent as its custody: a manifest written
  beside the ledger it describes can be rewritten by anyone able to truncate
  that ledger. It catches accidents and partial tampering; it becomes a real
  control once a copy is held where the ledger's writer cannot reach it.
- STEP-01 D7: `ts-sentry build-dataset` CLI
  (`ts_sentry.cli.main`), wiring D1-D6 together plus a build manifest
  (seed, generator version, git SHA, row counts, per-table SHA-256) and a
  build-time leakage self-check. Exit codes: 0 pass, 2 quality-gate fail,
  3 leakage fail.

[Unreleased]: https://github.com/MohdSaifHussain/ts-sentry/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/MohdSaifHussain/ts-sentry/releases/tag/v1.0.0
