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
- STEP-01 D7: `ts-sentry build-dataset` CLI
  (`ts_sentry.cli.main`), wiring D1-D6 together plus a build manifest
  (seed, generator version, git SHA, row counts, per-table SHA-256) and a
  build-time leakage self-check. Exit codes: 0 pass, 2 quality-gate fail,
  3 leakage fail.
