# Trust & Safety Sentry

Governed agentic workbench for Trust & Safety scaled-abuse analysis. See
[ARCHITECTURE.md](ARCHITECTURE.md) for the full design and
[docs/decisions/](docs/decisions/) for the per-phase build log.

Status: Phase 1 (data foundation) in progress. See
[docs/decisions/STEP-01-data-foundation.md](docs/decisions/STEP-01-data-foundation.md).

## Install

```bash
python -m venv .venv
.venv/Scripts/activate   # Windows
pip install -e ".[dev]"
```

Requires network access on first install: `analystkit` (the D6 quality-gate
dependency) is pinned to a git tag, not published to PyPI.

## CLI

### `ts-sentry build-dataset`

Builds the seeded synthetic platform dataset (channels, videos, comments,
engagement events, account metadata, infrastructure hints) plus the sealed
ground-truth labels, into a DuckDB file with Parquet exports and a build
manifest.

```bash
ts-sentry build-dataset --seed 42 --scale 1 [--out DIR] [--quality-thresholds PATH]
```

| Flag | Required | Meaning |
|---|---|---|
| `--seed N` | yes | Seeds the single `numpy.random.Generator` driving the entire build. Same seed + same scale => byte-identical output. |
| `--scale S` | yes | Integer multiplier on the base dataset size. |
| `--out DIR` | no | Output directory (DuckDB file, Parquet exports, manifest). Defaults to `./build/`. |
| `--quality-thresholds PATH` | no | JSON file overriding one or more of `completeness`/`uniqueness`/`validity`/`consistency` (percent, 0-100). Any key omitted keeps its default. |

Exit codes:

| Code | Meaning |
|---|---|
| `0` | Build succeeded; quality gate and leakage checks passed. |
| `2` | AnalystKit quality gate failed: a profile dimension (completeness/uniqueness/validity/consistency) is below its declared threshold, a `validate` rule found exceptions, or a `reconcile` check against `sealed._labels` found orphans (the accuracy dimension). |
| `3` | Sealed-label leakage check failed (build-time defense-in-depth; the primary guarantee is the `DataScope` allowlist, tested in `tests/test_scope_leakage.py`). |

### Quality gate (D6)

Wraps [AnalystKit](https://github.com/MohdSaifHussain/analystkit) rather
than reimplementing DAMA checks. Two dimensions are deliberately not
gated on a threshold, both documented in `docs/data-dictionary.md`:

- **Timeliness** is reported but never gated - it is a wall-clock-relative
  recency score, and this dataset's build window is a fixed historical
  range for reproducibility, not "now".
- **Accuracy** has no percentage score in AnalystKit's `profile` by design
  ("no tool can measure accuracy without an authoritative source"); it is
  instead gated via `analystkit reconcile` against the sealed ground
  truth (`sealed._labels`), checked for zero orphans in either direction.

## Honest Limits

Synthetic data only; no claim of real-platform efficacy. See
[ARCHITECTURE.md Section 12](ARCHITECTURE.md#12-honest-limits-standing-section-carried-into-readme)
for the full standing limits section.

## Windows notes

Developed and run on Windows with PowerShell. LF/CRLF warnings from git are
benign. UTF-8 output can misrender in CMD; prefer PowerShell for file
inspection.
