# STEP-01: Data Foundation

**Project:** Trust & Safety Sentry | **Phase:** 1 of 8 | **Date:** 31 July 2026 (IST)
**Status:** Specified, not started
**Standing rule:** every implementation follows the top applicable standard for
what is being built. Each requirement below names its governing standard.

---

## 1. Objective

Build the synthetic platform generator and sealed ground-truth store that every
other phase consumes. Exit criteria: seeded rebuilds are byte-stable, and the
label-leakage test proves agents cannot reach ground truth.

## 2. Deliverables

| ID | Deliverable | Governing standard(s) |
|---|---|---|
| D1 | `ts_sentry.data.generator`: seeded synthetic platform builder | Reproducible-research practice: seed + version stamped in every output; byte-stable rebuild verified in CI |
| D2 | Entity schemas: channel, video, comment, engagement_event, account_meta, infra_hint | PEP 621 project metadata; PEP 695 type aliases; frozen slots dataclasses; ISO 8601 timezone-aware IST timestamps |
| D3 | Threat generators T-01..T-07 with parameter dataclasses | Threat classes traceable to cited public policy texts (policy corpus anchors, Phase 5 dependency noted) |
| D4 | Sealed ground-truth table `_labels` in DuckDB | Access control by mandate data-scope exclusion (ARCHITECTURE 3.1); leakage test required |
| D5 | Data dictionary `docs/data-dictionary.md` | DAMA-DMBOK data dictionary practice; one row per column: name, type, unit, nullability, provenance |
| D6 | Data quality gate on every build | DAMA six dimensions via AnalystKit: completeness, uniqueness, validity, consistency, timeliness, accuracy; build fails below declared thresholds |
| D7 | `cli: ts-sentry build-dataset --seed N --scale S` | CLI contract documented in README; exit codes: 0 pass, 2 quality-gate fail, 3 leakage fail |

## 3. Requirements

### 3.1 Reproducibility (top standard: deterministic builds)
- Single `numpy.random.Generator` seeded from CLI; no bare `random`, no time-based entropy.
- Build manifest JSON: seed, generator version, git SHA, row counts per table, SHA-256 per exported table.
- CI job rebuilds with the same seed and diffs table hashes; any drift fails.

### 3.2 Schema and typing (top standard: strict static typing)
- mypy --strict clean; ruff clean (lint + format).
- StrEnum for all categoricals (ThreatClass, EntityKind, EngagementKind).
- All timestamps `datetime` tz-aware Asia/Kolkata; serialized ISO 8601.

### 3.3 Ground-truth sealing (top standard: least privilege)
- `_labels` lives in a separate DuckDB schema `sealed`.
- Mandate DataScope enum contains no member for `sealed`; scope resolution is
  an allowlist, so absence is denial.
- Leakage test (pytest): construct every agent mandate, attempt resolution of
  `sealed._labels`, assert structural refusal and ledgered
  `MANDATE_VIOLATION_ATTEMPT` once governance core lands (Phase 2); Phase 1
  interim: direct scope-resolver unit test.

### 3.4 Realism envelope (top standard: documented assumptions, no fabrication)
- Benign majority >= 97% of entities; documented in data dictionary.
- Burst shaping via hawkes-style self-exciting approximation or documented
  simpler Poisson-burst mix; choice recorded in docs/decisions with rationale.
- No claim of statistical fidelity to real YouTube distributions; assumptions
  section mandatory (Honest Limits discipline).

### 3.5 Testing (top standard: property-based + example-based)
- hypothesis properties: (a) rebuild determinism per seed, (b) referential
  integrity (every FK resolves), (c) label completeness (every planted abusive
  entity labeled; every labeled entity exists).
- Coverage floor declared in pyproject and enforced in CI.

### 3.6 Repo hygiene (top standards: SemVer 2.0.0, Keep a Changelog 1.1.0, Conventional Commits 1.0.0)
- CHANGELOG entry under Unreleased.
- Commits: `feat(data): ...`, `test(data): ...`, `docs(data): ...`.
- SPDX license identifier in file headers; LICENSE at root (MIT).

## 4. Out of Scope (this step)
- Any model call. Phase 1 is fully deterministic.
- Policy corpus fetching (Phase 5).
- VVR sampling (Phase 7) beyond ensuring view events carry the fields it needs:
  view_id, video_id, ts_ist, viewer_account_id.

## 5. Exit Checklist
- [ ] `build-dataset --seed 42` twice; manifests identical (table hashes equal)
- [ ] AnalystKit quality gate green at declared thresholds
- [ ] Leakage test red-teamed: a deliberately added sealed-scope member makes it fail
- [ ] mypy --strict, ruff, pytest, coverage floor all green in CI
- [ ] Data dictionary complete; assumptions section written
- [ ] CHANGELOG updated; STEP-01 moved to docs/decisions/ with outcome notes
