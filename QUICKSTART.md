# Quickstart

From a fresh clone to a measurement report. Target: **under 10 minutes**, which
is this release's exit criterion. The measured run is recorded in
[docs/quickstart-timing.md](docs/quickstart-timing.md), with the machine it was
measured on stated, because a timing without a machine is not a measurement.

## What you need

- Python 3.12 or newer
- Network access **for the install step only** (see below)
- No credentials, no API key, no cloud account, no Docker

## Two install paths

They are two paths and this document does not pretend one command gives both.

### Fast path: `pip install -e .`

```bash
git clone https://github.com/MohdSaifHussain/ts-sentry.git
cd ts-sentry
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate elsewhere
pip install -e ".[dev]"
```

This resolves dependencies fresh. Two installs a month apart can pick up
different versions of `duckdb`, `numpy`, `pandas` or `matplotlib`, because those
carry floors rather than pins.

### Reproducible path: `uv sync`

```bash
uv sync --frozen                # to run it
uv sync --frozen --extra dev    # to run the tests too
```

Installs exactly what `uv.lock` records. Requires
[uv](https://docs.astral.sh/uv/) as a prerequisite, which the fast path does
not.

`--extra dev` is needed explicitly because `dev` here is an *extra*
(`project.optional-dependencies`) rather than a PEP 735 dependency group, and
`uv sync` special-cases only the latter.

What the lockfile actually contains, measured rather than quoted from the
documentation: **50 packages**, of which 48 come from PyPI with **802 recorded
sha256 hashes**, one is the project itself as an editable install, and one is
`analystkit` pinned to the exact git commit
`ab98ee6a3c309f57134d48787aa604b1d1044f62`.

**Why the split is stated rather than smoothed over.** Reproducibility is one of
this project's stated optimization targets and until this release it held for
the *data* and not for the *environment*. The lockfile closes that, and the
honest framing is that you now choose which property you want: fewest
prerequisites, or a resolvable environment.

### The one thing that needs network

`analystkit` backs the data-quality gate, is pinned to an exact git revision,
and is not published to PyPI. Both paths fetch it from GitHub on first install.

**Everything after install runs offline.** The deterministic stub adapter is the
default and the CI path. No step below opens a socket.

## The path

### 1. Build the dataset

```bash
ts-sentry build-dataset --seed 42 --scale 1 --out build
```

Generates the synthetic platform (channels, videos, comments, engagement events,
account metadata, infrastructure hints) plus sealed ground-truth labels in a
schema no agent can reach. Runs the AnalystKit data-quality gate and a
build-time leakage self-check.

Same seed and scale gives byte-identical Parquet exports, every time.

### 2. Triage: what to look at first

```bash
ts-sentry run-session --agent triage --seed-dataset build --out session-triage
```

Produces `ranked_queue.json`: one row per case with its full score components
(`severity_class`, `spread`, `velocity`, `recidivism`), the weighted priority,
and a one-line model rationale that passed verification. A rationale may cite
only that row's own components; anything else is rejected and the rejection is
ledgered.

### 3. Investigate the top case

```bash
ts-sentry run-session --agent evidence --seed-dataset build \
    --out session-evidence --case case-0000 --subject t02_chan_000_000
```

Twenty pivots, each proposed by the agent, validated by the orchestrator,
approved by the analyst, and only then run as a reviewed parameterized query.
Produces `evidence_pack.json` with an entity graph, a timeline, and a provenance
record per hop.

### 4. Draft and sign the memo

```bash
ts-sentry run-session --agent memo --seed-dataset build \
    --pack session-evidence/evidence_pack.json --out session-memo

ts-sentry sign-memo session-memo --analyst-id you \
    --pack session-evidence/evidence_pack.json
```

The memo is a DSA Article 17 style statement of reasons. Every factual sentence
must resolve to an evidence-record id the pack carries, and every policy
citation must resolve to a real anchor in the hashed corpus. Signing is the only
route to a final memo, and it re-renders the exports without the AI-DRAFT
watermark.

### 5. The measurement report

```bash
ts-sentry report --session session-triage --build build --out report
```

Violative View Rate with a 95% confidence interval, sensitivity curves, workflow
metrics, and ten honest limits carried into every rendering.

### 6. Check the audit trail yourself

```bash
ts-sentry verify-ledger session-evidence/ledger.jsonl \
    --expect-head-from session-evidence/session_manifest.json
```

Exit 0 means the chain is intact **and** its head matches the anchor the session
recorded at close.

Try truncating the last line of that `ledger.jsonl` and running it again. The
bare `verify-ledger` still exits 0, because chain verification cannot see
entries removed from the end: what remains is a shorter chain whose every link
still recomputes. The anchor catches it, with exit 6. That limitation is
asserted as a passing test rather than described.

## See it without running it

Every command above has already been run, with the artifacts committed:
[examples/](examples/). Three of the eight show the governance layer refusing
something, which is the half worth reading first.

## If something goes wrong

| Symptom | Cause |
|---|---|
| `pip install` fails on `analystkit` | No network, or GitHub unreachable. It is a git dependency by necessity. |
| `import duckdb` fails with an Application Control error | A Windows host security policy blocking the native extension, not a code fault. |
| Garbled characters in `cmd.exe` | Use PowerShell. The output is UTF-8. |
| `run-session --agent evidence` exits 5 | The `--subject` does not exist in the build. That guard is deliberate: without it the system will happily produce a complete, valid audit trail for an investigation of nothing. |
| Any command exits 5 with a usage message | A malformed invocation. Exit 5 is input error across the whole CLI. |

## What you have not proved by doing this

The quickstart shows the pipeline working. It does not show that any agent is
good at its job. Every model output you just produced came from a deterministic
stub that cannot be persuaded and cannot reason. See the Honest Limits in
[README.md](README.md#honest-limits) before drawing conclusions from anything
above.
