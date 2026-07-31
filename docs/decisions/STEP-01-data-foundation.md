# STEP-01: Data Foundation

**Project:** Trust & Safety Sentry | **Phase:** 1 of 8 | **Date:** 31 July 2026 (IST)
**Status:** Implemented (D1-D7); byte-stability confirmed by Saif; leakage
red-team pending Saif's personal post-commit pass
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
- [x] `build-dataset --seed 42` twice; manifests identical (table hashes equal)
      - Verified independently twice: by Claude during implementation as a
        sanity check, and by Saif personally (`fc` diff of both manifests
        and all seven Parquet files, including `sealed/_labels.parquet`) -
        byte-identical.
- [x] AnalystKit quality gate green at declared thresholds
- [ ] Leakage test red-teamed: a deliberately added sealed-scope member makes it fail
      - Automated coverage is in place two ways: `tests/test_scope_leakage.py`
        red-teams the `DataScope` allowlist itself, and `tests/test_cli.py`
        `test_leakage_self_check_detects_a_real_leak` red-teams the
        build-time export check by deliberately denormalizing a label
        column onto an entity export and confirming detection. Left
        unchecked deliberately: Saif's own personal red-team pass, per his
        explicit request, happens **after** this commit series lands, not
        before - this box records that it is still outstanding, not that it
        was skipped.
- [x] mypy --strict, ruff, pytest, coverage floor all green in CI
- [x] Data dictionary complete; assumptions section written
- [x] CHANGELOG updated; STEP-01 moved to docs/decisions/ with outcome notes

## 6. Outcome

Shipped: D1-D7, all in `src/ts_sentry/`. 49 tests green (unit, hypothesis
property, and AnalystKit-integration), mypy `--strict` clean, ruff clean,
99% line coverage (90% floor). `ts-sentry build-dataset --seed 42 --scale 1`
runs end to end in ~9s; double-build byte-stability is independently
confirmed by both Claude (in-session sanity check) and Saif (`fc` diff of
manifests and all Parquet files, post-implementation).

### D3 budget-invariant bugs, caught by tests, not by inspection

The reviewer's insistence that "docstring-only invariants are not
invariants" (applied first to D2's IST/target-invariant enforcement, then
extended to D3's benign-majority budget invariant) caught three real
defects that code reading alone had missed:

1. T-01 and T-03's `for_budget()` shrink loops checked only
   `ring_count * accounts_per_ring` against budget, omitting the comment
   count entirely - both classes could and did exceed their labelable-row
   budget under real seeds (`ValueError: ... 18 > 17`).
2. T-02's `for_budget()` had no ring-count fallback once
   `members_per_ring` bottomed out at 1, an analogous gap.
3. T-07's cross-amplification logic requires at least two channels (each
   member comments on *another* member's video); `for_budget()` allowed
   shrinking to a single channel, which crashed with `StopIteration`
   rather than a clean budget violation.

All three were surfaced by `tests/test_generator_properties.py`'s
hypothesis-driven `test_referential_integrity`/`test_all_timestamps_are_ist_aware`
runs across random seeds (T-01/T-03) and by the dedicated
`tests/test_threat_budgets.py` suite added specifically to close this gap
(T-02/T-07), which now exercises every one of the seven classes at its true
minimum footprint - not just the budgets that happened to arise from real
seeds - so a regression here fails loudly again.

### AnalystKit integration findings (D6)

Both discovered by direct invocation of the installed `analystkit==2.1.0`,
not from its `--help` text or README, which are silent on both:

- **`reconcile` accepts CSV only, not Parquet.** `profile` and `validate`
  read Parquet directly (confirmed working); `reconcile` raises
  `"reconcile currently accepts CSV on both sides"` on a Parquet input.
  Resolution: `ts_sentry.data.quality.reconcile_entity_labels` exports two
  small CSV side-files per entity kind (a shared `entity_id` key column)
  just for that one step. This is a mechanical format adaptation, not a
  change to the design Saif approved (reconcile against `sealed._labels`
  for the accuracy dimension) - reconcile is still the tool doing the
  actual reconciliation.
- **Windows console `cp1252` crash.** `analystkit profile`'s Unicode
  progress-bar characters (`█`) raise `UnicodeEncodeError` under the
  default Windows console codec, before the DAMA scorecard section even
  prints. Fix: every subprocess invocation in `ts_sentry.data.quality` sets
  `PYTHONIOENCODING=utf-8` in the child environment - the standard fix for
  this class of issue, not a workaround of AnalystKit's DAMA logic.

### Environment incident: Windows Application Control blocked DuckDB

During the D1/D2 review stop, `import duckdb` failed on Saif's machine
with `DLL load failed while importing _duckdb: An Application Control
policy has blocked this file` - a Windows Application Control (WDAC-class)
policy blocking the native `_duckdb` extension, not a code defect. This
was flagged rather than worked around (no code change could fix a host
security policy), and blocked exactly one test
(`tests/test_store.py`) plus any real `build-dataset` execution. Saif
resolved it on his end (machine-level policy adjustment); `duckdb`
subsequently imported cleanly (`duckdb.__version__ == "1.5.5"`) and every
DuckDB-dependent test and manual CLI run in this phase has passed since.
No code in this repository works around or depends on the resolution -
it was purely a local environment fix.

### Accuracy-dimension / sealed-access boundary clarification

Confirmed via direct invocation that AnalystKit's `profile` deliberately
never scores accuracy ("no tool can measure accuracy without an
authoritative source... scoring it from the dataset alone is
fabrication"). Resolved per Saif's direction: `analystkit reconcile`
against `sealed._labels` (zero orphans in either direction, for each of
the four labelable `EntityKind`s) is the accuracy gate.

This means `sealed._labels` (DuckDB schema, and its segregated `sealed/`
Parquet export) has exactly two legitimate consumers in this codebase: the
build pipeline (write, and the D6 `reconcile` gate's build-time read) and,
from STEP-07 onward, measurement code. **STEP-07's own spec (3.2) says
"measurement code is the only consumer of `sealed._labels`"** - that line
needs to be read as "measurement code is the only *agent/orchestrator-side*
consumer" before STEP-07's import-graph test is written, or the test will
incorrectly fail on the build pipeline's own reconcile read. Recorded here
as the authoritative clarification for that phase.

### Spec deviation, flagged explicitly: Timeliness is reported, never gated

STEP-01 D6 names six DAMA dimensions to gate on: "completeness, uniqueness,
validity, consistency, timeliness, accuracy." This implementation gates on
four (completeness/uniqueness/validity/consistency) plus accuracy via
reconcile (above) - **five of six** - and deliberately excludes Timeliness
from `QualityThresholds` entirely. This is a real deviation from the
literal D6 text, not a rounding error, and is called out here rather than
left implicit.

Rationale: AnalystKit's Timeliness dimension is a wall-clock-relative
linear decay (`profiling.py`: `1 - age_days/90`, floored at 0 past 90
days). This dataset's row timestamps are drawn from a fixed historical
window (`ts_sentry.data.tz.WINDOW_START = 2024-01-01`) specifically so
that rebuilds are byte-stable regardless of when they run (STEP-01 3.1).
Any fixed-window synthetic corpus will therefore score exactly 0% on this
dimension by construction, forever, independent of actual data quality -
gating on it would mean the build can never pass, on a dimension no amount
of legitimate improvement could fix. The dimension is still computed and
reported in the manifest (`quality_gate.profiles[*].dimensions`) for
transparency; it simply carries no pass/fail threshold. Documented in
`docs/data-dictionary.md` Assumptions.

### Other deviations and tuning

- **DataScope pre-seed**: `DataScope` and its allowlist resolvers live in
  `ts_sentry/governance/scopes.py`, not under `ts_sentry/data/`, per Saif's
  direction (it is a mandate/allowlist concept STEP-02 `governance.mandate`
  will import, not redefine). Nothing else from STEP-02 (Mandate, gates,
  ledger, verifier) is implemented here.
- **Burst shaping**: Poisson-burst mixture (background uniform-in-window
  draws mixed with a short, coordinated per-ring burst window), not a
  Hawkes process, per Saif's confirmed choice - deterministic under the
  seeded Generator and simple to verify, at the cost of a less naturalistic
  clustering shape than a self-exciting kernel would give.
- **Engagement-volume constants tuned down mid-build**: initial per-video
  engagement means (500 views, etc.) made a single scale=1 build ~223k
  engagement-event rows, which made the hypothesis property suite (which
  builds the dataset repeatedly) take minutes rather than seconds.
  Reduced to 50/12/2/4/1 (views/likes/dislikes/shares/reports) - still a
  realistic-looking distribution, just lighter - documented in
  `docs/data-dictionary.md`.
- **Bulk-insert path**: `duckdb.executemany` took ~25s to load one
  scale=1 dataset (tens of thousands of rows); registering a `pandas`
  DataFrame per table and doing a set-based `INSERT ... SELECT` cut that
  to well under a second. `pandas` was accordingly added as a direct
  dependency (previously only a transitive one, via `analystkit`), not
  just relied on implicitly.
