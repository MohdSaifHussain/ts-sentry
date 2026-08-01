# Curated example sessions

Seven runs of the shipped CLI against the seed-42 scale-1 synthetic build, with
their full artifacts committed. Every directory carries an `inputs.json` saying
what produced it, a ledger that `verify-ledger` accepts, and a `NOTES.md` stating
what the example demonstrates **and what it deliberately does not claim**.

Two of the seven exist to show the governance layer refusing something. That is
the point of them: a control that has never fired is a control nobody has
tested.

| # | Example | Shows | Exit |
|---|---|---|---|
| 01 | [`01-triage-queue`](01-triage-queue/) | Ranked queue with score decomposition; rationales verified against citable components | 0 |
| 02 | [`02-evidence-t02-ring`](02-evidence-t02-ring/) | A T-02 fake-engagement ring reconstructed over 20 analyst-approved pivots; **4 of 8 members, a recorded-unmet obligation** | 0 |
| 03 | [`03-signed-memo`](03-signed-memo/) | An Article 17 style memo, verified, then human-signed | 0 |
| 04 | [`04-evidence-t07-cluster`](04-evidence-t07-cluster/) | The same investigation path on a T-07 influence cluster; the structural recovery ceiling | 0 |
| 05 | [`05-overclaim-refused`](05-overclaim-refused/) | **The gate refusing an agent that overclaims.** 8 attempts, 8 rejections, memo held at DRAFT | 0 |
| 06 | [`06-prompt-eval-refused`](06-prompt-eval-refused/) | **A degraded prompt refused activation**, with per-class breach numbers | **7** |
| 07 | [`07-measurement-report`](07-measurement-report/) | VVR estimate with a 95% CI, sensitivity curves, and ten honest limits | 0 |

Read in order they tell one story: find the case, investigate it, write the
memo, sign it. Then 05 and 06 show what happens when the agent or the prompt is
wrong.

## Regenerating

```bash
python examples/regenerate.py
```

It builds the dataset into a temporary directory and re-runs all seven through
the same CLI verbs the documentation teaches. Nothing reaches past the CLI into
the orchestrator.

## What regenerating does and does not reproduce, measured

Running it twice does **not** produce byte-identical output, and the tests do
not pretend otherwise:

| Reproducible | Not reproducible |
|---|---|
| `session_id` (derived from inputs, reads no clock) | Chain heads and `entry_hash` values |
| `ranked_queue.json`, byte for byte | `ledger.jsonl`, `session_manifest.json`, `session_events.json` |
| Event counts per type | `evidence_pack.json`, `evidence_graph.graphml` (they carry `retrieval_ts_ist`) |
| Exit codes, verdicts, breach codes | The measurement report's `generated` stamp |
| Recovery numbers, VVR estimate at a fixed seed | |

A ledger records *when* things happened. A ledger that was byte-stable across
runs would be a worse artifact, not a better one. `tests/test_examples.py`
therefore checks the invariants that are real rather than byte-identity that
honestly cannot be.

`ledger.duckdb` is deliberately **not committed**: 780 KB of binary per session,
`verify-ledger` reads the JSONL export just as well, and STEP-03 measured that
the DuckDB store is not byte-stable even when its contents are.

## Checking them yourself

```bash
# every chain intact
ts-sentry verify-ledger 01-triage-queue/ledger.jsonl

# and matching the anchor its own manifest recorded
ts-sentry verify-ledger 01-triage-queue/ledger.jsonl \
    --expect-head-from 01-triage-queue/session_manifest.json
```

Truncate any of these ledgers and the bare verify still exits 0, because chain
verification cannot see entries removed from the end. Only the anchor catches
it, with exit 6. That limitation is asserted as a passing test rather than
described, and it has been carried in every phase since STEP-02.

## Standing limits on all seven

Synthetic data only; no claim of real-platform efficacy. Every model output here
came from a deterministic offline stub that cannot be persuaded and cannot
reason, so **nothing in this directory is evidence about how a model behaves**.
What these examples evidence is the pipeline: that outputs are checked rather
than trusted, that refusals are recorded with countable reasons, and that every
step is traceable.
