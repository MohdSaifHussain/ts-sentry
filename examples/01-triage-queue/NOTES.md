# 01: the triage queue

**What this is.** One OBSERVE session. The orchestrator queries the flagged-entity
queue, scores every case with a published-weight decomposable function, asks the
model for a one-line rationale per case, and verifies that each rationale cites
only components that case actually has.

Reproduce:

```bash
ts-sentry build-dataset --seed 42 --scale 1 --out build
ts-sentry run-session --agent triage --seed-dataset build --out . --analyst-id saif
```

## What it demonstrates

| Claim | Where to see it |
|---|---|
| Every score renders as its components, never as a bare number | `ranked_queue.json`, each row's `components` block |
| 23 cases, priorities from 0.482 down to 0.203 | `ranked_queue.json` |
| The ranking discriminates on more than one component | The top case by severity (`case-0000`, severity 0.8) ranks **third**, because its velocity is 0.033 against the leaders' 1.0 |
| Rationales cite the *discriminating* component, not the largest | Cited across the 23 rows: velocity 16, spread 4, severity_class 3 |
| Every step is ledgered and the chain is intact | `ledger.jsonl`, 8 entries; `verify-ledger` exits 0 |
| The chain is anchored | `session_manifest.json` carries `expected_head`; `--expect-head-from` exits 0 |
| The model path is recorded, in the chain | `session_open` payload carries `model_mode: stub`, `stub_mode: faithful` |

The cited-component distribution is the thing worth looking at. Before the
STEP-03 fix, all 25 rows cited `severity_class`, because the builder picked the
largest component and severity was largest everywhere. Every rationale verified
and none of them explained anything: at equal severity, the cited component was
the one thing that could not account for the ordering.

## What this deliberately does not claim

- **Severity is a heuristic stand-in, not ground truth, and not detection
  performance.** The flagged-entity queue comes from a deterministic stub
  standing in for the enterprise detector that would sit upstream in a real
  deployment. It reads only allowlisted tables, has no access to the sealed
  ground truth direct or derived, and **has no measured precision or recall**.
  Nothing here says the right cases were flagged.
- **The scorer is transparent, not accurate.** The weights are analyst judgment,
  not fitted parameters. There is no measured outcome on this synthetic data to
  fit them against, so "priority 0.482" means "this function returns 0.482",
  not "this case is 48% urgent".
- **The rationales are not evidence about model quality.** They were produced by
  a deterministic stub that cannot be persuaded and cannot reason. What is
  demonstrated is the *pipeline*: that a model's output is checked against a
  resolvable id set rather than trusted. That property holds whichever model
  sits behind the adapter, and this session is not evidence about any of them.
- **Zero rationales failed verification here**, which is not a claim that the
  verifier is lenient or that it never fires. See `05-overclaim-refused` for the
  same machinery rejecting eight drafts in a row.
- Synthetic data throughout. No claim of real-platform efficacy.
