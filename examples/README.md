# Curated example sessions

Eight examples: seven runs of the shipped CLI against the seed-42 scale-1
synthetic build, plus one that pushes real third-party comment text through the
input firewall. Full artifacts committed.

Every directory carries an `inputs.json` saying what produced it and a
`NOTES.md` stating what the example demonstrates **and what it deliberately does
not claim**. The seven session examples also carry a ledger that `verify-ledger`
accepts; example 08 does not, for a reason given below.

Three of the eight exist to show something being refused. That is the point of
them: a control that has never fired is a control nobody has tested.

| # | Example | Shows | Exit |
|---|---|---|---|
| 01 | [`01-triage-queue`](01-triage-queue/) | Ranked queue with score decomposition; rationales verified against citable components | 0 |
| 02 | [`02-evidence-t02-ring`](02-evidence-t02-ring/) | A T-02 fake-engagement ring reconstructed over 20 analyst-approved pivots; **4 of 8 members, a recorded-unmet obligation** | 0 |
| 03 | [`03-signed-memo`](03-signed-memo/) | An Article 17 style memo, verified, then human-signed | 0 |
| 04 | [`04-evidence-t07-cluster`](04-evidence-t07-cluster/) | The same investigation path on a T-07 influence cluster; the structural recovery ceiling | 0 |
| 05 | [`05-overclaim-refused`](05-overclaim-refused/) | **The gate refusing an agent that overclaims.** 8 attempts, 8 rejections, memo held at DRAFT | 0 |
| 06 | [`06-prompt-eval-refused`](06-prompt-eval-refused/) | **A degraded prompt refused activation**, with per-class breach numbers | **7** |
| 07 | [`07-measurement-report`](07-measurement-report/) | VVR estimate with a 95% CI, sensitivity curves, and ten honest limits | 0 |
| 08 | [`08-firewall-real-comments`](08-firewall-real-comments/) | **The only real data here.** 1,956 real YouTube comments through the input firewall; the corpus was refused on duplicate ids | n/a |

Read in order they tell one story: find the case, investigate it, write the
memo, sign it. Then 05 and 06 show what happens when the agent or the prompt is
wrong, and 08 shows what happened the first time any of this met data nobody
here wrote.

**08 is shaped differently and says so.** It has no ledger and no session id,
because the input firewall is a library component and no session runs. That is a
deliberate deviation from requirement 3.2 rather than an oversight: inventing a
CLI verb to make an example look uniform would be adding product surface to
serve a demo, and manufacturing a session around a component call would mean
ledgering governance events that never happened.

## Regenerating

```bash
python examples/regenerate.py
```

It builds the dataset into a temporary directory and re-runs 01 through 07
through the same CLI verbs the documentation teaches, then runs 08 over the
committed CSVs. Nothing reaches past the CLI into the orchestrator except 08,
which calls `apply_firewall` directly and is labelled as doing so.

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

## Third-party data

Exactly one directory contains data this project did not generate:
`data/youtube-spam-collection/`, the UCI YouTube Spam Collection under CC BY 4.0,
redistributed unmodified with its citation in
[`ATTRIBUTION.md`](data/youtube-spam-collection/ATTRIBUTION.md). It is read by
example 08 and by nothing else, and it cannot feed the pivots, the measurement
lenses or the eval set. That file says why.

## Standing limits on all eight

Synthetic data except where example 08 says otherwise; no claim of
real-platform efficacy. Every model output here came from a deterministic
offline stub that cannot be persuaded and cannot
reason, so **nothing in this directory is evidence about how a model behaves**.
What these examples evidence is the pipeline: that outputs are checked rather
than trusted, that refusals are recorded with countable reasons, and that every
step is traceable.
