# Trust & Safety Sentry

Governed agentic workbench for Trust & Safety scaled-abuse analysis. See
[ARCHITECTURE.md](ARCHITECTURE.md) for the full design and
[docs/decisions/](docs/decisions/) for the per-phase build log.

Status: Phase 1 (data foundation) closed; Phase 2 (governance core) complete;
Phase 3 (orchestrator + triage agent) complete, pending phase-close review.
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
ts-sentry verify-ledger PATH [--expect-head COUNT:HASH | --expect-head-from MANIFEST]
```

| Flag | Required | Meaning |
|---|---|---|
| `PATH` | yes | `.jsonl` verifies an exported session chain; `.duckdb` verifies the stored `governance.ledger` table. Both readers feed one shared verification function, so an export and the store it came from cannot disagree. |
| `--expect-head COUNT:HASH` | no | Compare the chain head against an expectation you already hold. See the note below on what this is and is not. |
| `--expect-head-from MANIFEST` | no | Read that expectation out of a session manifest instead. Mutually exclusive with `--expect-head`. |

Output always reports `entries` (chain length) and `head` (final
`entry_hash`), whether the chain verifies or not.

Exit codes:

| Code | Meaning |
|---|---|
| `0` | Chain intact. If `--expect-head` was given, the head matched too. |
| `4` | Broken chain. The first broken sequence number is printed to stdout and summarized on stderr. |
| `5` | Input error: no such file, unsupported extension, unreadable or malformed content, a malformed `--expect-head` value, a missing argument, or an unrecognized flag. Deliberately distinct from `4`, so "wrong file" is never mistaken for "tampered file". |
| `6` | Chain links are intact but the head does not match the expectation. |

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
(`test_tail_truncation_is_invisible_to_chain_verification_alone`) rather than
only documented.

`--expect-head` is a comparison verb, not an anchor system. It compares
against an expectation the caller already holds; it does not store, derive,
or manage one. Anchor storage is the STEP-03 session manifest, which records
the expected head at `SESSION_CLOSE`, and `--expect-head-from` is what reads
it back.

What that anchor does and does not buy you: an anchor is only as independent
as its custody. A manifest sitting next to the ledger it describes can be
rewritten by anyone who can truncate that ledger, so co-located files catch
accidents and partial tampering, not a determined editor with write access to
the whole session directory. The anchor becomes a real control when a copy of
the manifest is held where the ledger's writer cannot reach it. Both halves
are asserted in `tests/test_session_manifest.py`, including the one that shows
a rewritten manifest agreeing with a truncated ledger.

### `ts-sentry run-session`

Opens an analyst session, runs one agent turn under a mandate, closes with an
anchored manifest, and writes the session artifacts (STEP-03 D6).

```
ts-sentry run-session --agent triage|evidence --seed-dataset PATH [--out DIR]
                      [--analyst-id ID] [--llm-mode stub|live]
                      [--limit N] [--seed N] [--session-id ID]
                      [--case ID] [--subject ID] [--review scripted|interactive]
                      [--max-hops N]
```

| Argument | Required | Meaning |
|---|---|---|
| `--agent` | yes | `triage` or `evidence`. Memo and prompt-eval arrive in STEP-05 and STEP-06. |
| `--seed-dataset PATH` | yes | A `build-dataset` output directory, or the `build.duckdb` inside it. Opened **read-only** for both agents: a triage session is OBSERVE by mandate, and an ASSEMBLE ceiling is authority to assemble evidence from the platform, not to change it. |
| `--out DIR` | no | Where artifacts are written. Defaults to `session`. |
| `--llm-mode` | no | `stub` (default) or `live`. See below. |
| `--subject ID` | evidence only | The entity to investigate, typically a channel id taken from a triage session's `ranked_queue.json`. |
| `--case ID` | no | Case id for an evidence session. Defaults to `case-0000`. |
| `--review` | no | Where analyst decisions come from: `scripted` (default) or `interactive`. See below. |
| `--max-hops N` | no | Pivot ceiling for one evidence turn. Defaults to the mandate's `max_steps`. |

Artifacts, triage: `ledger.jsonl`, `ledger.duckdb`, `ranked_queue.json`,
`session_events.json`, `session_manifest.json`.

Artifacts, evidence: `ledger.jsonl`, `ledger.duckdb`, `evidence_pack.json`,
`evidence_graph.graphml`, `session_events.json`, `session_manifest.json`.

`--seed-dataset` requires the build's `build_manifest.json` to be present
beside the store. A session's dataset identity comes from the manifest's
Parquet table hashes, and there is deliberately no fallback to hashing
`build.duckdb`: see "Session ids and dataset identity" below.

Exit codes:

| Code | Meaning |
|---|---|
| `0` | Session closed with an intact chain. |
| `4` | The session produced a broken chain. Self-verified before the run reports success, so this is caught at the end of the run rather than weeks later. |
| `5` | Input error: no such dataset, an unreadable store, a missing or unknown argument, an unrecognized flag, or `--llm-mode live` without the environment to match. |

Like `verify-ledger`, `run-session` **never exits `2`**. Argparse's own usage
errors would exit `2`, which means "quality gate failed" for `build-dataset`;
they are translated to `5` so no exit code carries two meanings. This was a
STEP-03 follow-up: `run-session` originally let argparse exit `2` while this
table already claimed otherwise. `build-dataset` keeps argparse's stock exit
`2`, unchanged since STEP-01.

#### It runs offline and costs nothing

The deterministic stub adapter is the default and the CI path. A run with no
environment configured at all, no credential, and without the optional vendor
package installed is a complete, valid session. Live mode requires the intent
to be expressed **twice**: `--llm-mode live` *and* `TS_SENTRY_LLM_MODE=live`
in the environment, plus `ANTHROPIC_API_KEY`, whose value this repository
never reads - it only checks that the variable exists and lets the vendor
client read it. A shell alias or a stray argument cannot start spending money
on its own.

The full test suite has been run with `socket.connect`, `socket.create_connection`
and `socket.connect_ex` patched to raise: zero network attempts.

#### What the session produces

`ranked_queue.json` carries one row per case with its full score component
vector (`severity_class`, `spread`, `velocity`, `recidivism`), the weighted
priority, the channel the case is about, and the model's one-line rationale if
it passed verification. A rationale may cite only that row's own component ids;
anything else is rejected, the rejection is ledgered as `VERIFICATION_FAIL`,
and the row keeps its deterministic score. Losing the explanation does not lose
the work.

**Severity is a heuristic stand-in, not ground truth.** The flagged-entity
queue comes from a deterministic stub standing in for the enterprise detector
that would sit upstream in a real deployment. It reads only allowlisted tables,
has no access to the sealed ground truth (direct or derived), and has no
measured precision or recall. It must never be read as detection performance.

#### The evidence session, hop by hop (STEP-04)

An evidence session investigates one case. The agent proposes a pivot from a
fixed vocabulary of five, the orchestrator checks it, the analyst approves or
rejects, and only then does a query run. Every hop is a ledgered
`HUMAN_DECISION`.

The agent never writes SQL. Each pivot is a reviewed, parameterized DuckDB
template; the agent chooses which one runs next and on what, its parameters are
typed and bounds-checked, and any entity id it supplies must already be in the
evidence pack, so an investigation expands from the analyst's chosen seed rather
than reaching anywhere it likes.

`evidence_pack.json` is the full artifact: entity graph, timeline, and a
provenance record per hop naming the source table, the query template, that
template's digest, the parameter hash and the retrieval timestamp.
`evidence_graph.graphml` is the entity graph for a drawing tool, and it carries
each element's provenance id too, because an export that dropped provenance
would be a picture rather than evidence.

**Who approves matters, and the ledger records which.** `--review scripted` is
the default and the CI path: decisions are declared before the session runs and
every ledgered decision says `"reviewer_kind": "scripted"`, inside the payload
the hash chain covers. `--review interactive` prompts a real person. No output
of this system renders a scripted approval as a human one. The interactive path
is written and documented but **has not been run**; see Honest Limits.

#### Session ids and dataset identity

A session id is a function of the analyst, the dataset's content, the agent,
and (for evidence) the case and subject. It is never random and never derived
from the clock.

The dataset's identity comes from the build manifest's `table_hashes`, which are
the digests of the Parquet exports STEP-01 verified byte-stable. It was
previously the SHA-256 of `build.duckdb`, which is **not** byte-stable across
rebuilds even when its contents are, so session ids changed every time anyone
rebuilt the same seed. That is fixed as of STEP-04, and the digest carries a `v2`
domain separator so a pre-fix and a post-fix identity for the same build cannot
be mistaken for each other. Session ids from before that change are not
comparable with ones after it.

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
