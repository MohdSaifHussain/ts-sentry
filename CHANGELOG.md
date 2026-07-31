# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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

### Changed

- `ChainHead` and `chain_head` moved from `ts_sentry.cli.main` to
  `ts_sentry.governance.ledger`. No behavior change: the session manifest and
  `verify-ledger` need the identical spelling of a chain head, and
  `orchestrator` must not import from `cli` to get it. `Ledger.head` now
  answers from the cached tail without rescanning the chain.

### Fixed

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
