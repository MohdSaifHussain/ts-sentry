# Trust & Safety Sentry

Governed agentic workbench for Trust & Safety scaled-abuse analysis. See
[ARCHITECTURE.md](ARCHITECTURE.md) for the full design and
[docs/decisions/](docs/decisions/) for the per-phase build log.

Status: Phase 1 (data foundation) closed; Phase 2 (governance core) complete.
See [docs/decisions/](docs/decisions/) for the per-phase build log.

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

### `ts-sentry verify-ledger`

Recomputes a trajectory-ledger hash chain and reports the first broken link,
plus the chain head.

```bash
ts-sentry verify-ledger PATH [--expect-head COUNT:HASH]
```

| Flag | Required | Meaning |
|---|---|---|
| `PATH` | yes | `.jsonl` verifies an exported session chain; `.duckdb` verifies the stored `governance.ledger` table. Both readers feed one shared verification function, so an export and the store it came from cannot disagree. |
| `--expect-head COUNT:HASH` | no | Compare the chain head against an expectation you already hold. See the note below on what this is and is not. |

Output always reports `entries` (chain length) and `head` (final
`entry_hash`), whether the chain verifies or not.

Exit codes:

| Code | Meaning |
|---|---|
| `0` | Chain intact. If `--expect-head` was given, the head matched too. |
| `4` | Broken chain. The first broken sequence number is printed to stdout and summarized on stderr. |
| `5` | Input error: no such file, unsupported extension, unreadable or malformed content, a malformed `--expect-head` value, a missing argument, or an unrecognized flag. Deliberately distinct from `4`, so "wrong file" is never mistaken for "tampered file". |
| `6` | Chain links are intact but the head does not match `--expect-head`. |

Precedence: chain integrity is checked before the head comparison, because a
broken chain makes any head claim meaningless.

`verify-ledger` never exits `2`. Argparse's own usage errors would normally
exit `2`, which means "quality gate failed" for `build-dataset`; they are
translated to `5` so no exit code carries two meanings and so malformed
input behaves identically across supported Python versions. `build-dataset`
usage errors still exit `2` through argparse, unchanged.

#### What `--expect-head` is, and what it is not

Hash-chain verification detects modification, reordering, and interior
deletion. It **cannot** detect entries removed from the *end*: what remains
is a shorter chain whose every link still recomputes, indistinguishable from
a session that ended earlier. This limitation is asserted by a test
(`test_truncating_the_tail_is_undetectable`) rather than only documented.

`--expect-head` is a comparison verb, not an anchor system. It compares
against an expectation the caller already holds; it does not store, derive,
or manage one. Anchor storage belongs to the STEP-03 session manifest, so
until that lands you supply the expected `COUNT:HASH` yourself from a record
you trust.

### Quality gate (STEP-01 D6)

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
