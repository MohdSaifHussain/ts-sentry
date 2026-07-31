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

### Known limitations

- Ledger chain verification cannot detect entries removed from the *end* of a
  chain: what remains is a shorter chain whose every link still recomputes.
  `verify-ledger --expect-head` compares against an expectation the caller
  supplies; storing that anchor is a STEP-03 session-manifest concern.
- STEP-01 D7: `ts-sentry build-dataset` CLI
  (`ts_sentry.cli.main`), wiring D1-D6 together plus a build manifest
  (seed, generator version, git SHA, row counts, per-table SHA-256) and a
  build-time leakage self-check. Exit codes: 0 pass, 2 quality-gate fail,
  3 leakage fail.
