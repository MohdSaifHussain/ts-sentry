# 06: a degraded prompt refused activation

**What this is.** The prompt wind tunnel. A candidate version of
`classify.threat_class` is evaluated against the incumbent over the committed
59-item eval set, the regression gate reads the result, and activation is
**refused**. The command exits 7.

Reproduce:

```bash
ts-sentry eval-prompts \
    --candidate 334dd1b70e9ee0d722d4ee25e19192cd7334da93aaf1c6ae65d9ec7fa89bd61d \
    --registry ../registries/degraded-classify \
    --evals ../../evals/threat_class --out . --analyst-id saif
```

## The degradation is one visible edit

The candidate is the shipped prompt with a single rule changed. Diff
`registries/degraded-classify/334dd1b7....txt` against
`prompts/71610d32....txt` and the whole difference is:

```
- If no coordinated abuse signal is present, the answer is benign. Benign is a
  real answer, not a failure to decide.
+ When in any doubt at all, answer benign. Prefer benign whenever the case is
  not overwhelming.
```

That is the classic silent-drift shape. Nothing about it looks broken, and it
destroys recall on every threat class. The candidate lives in a registry
**beside** the examples rather than in the fleet's `prompts/`, because
`--registry` is a flag precisely so a candidate can be evaluated before anyone
decides it belongs in the fleet's registry.

## What it demonstrates

| Claim | Value |
|---|---|
| Activation was refused | `decision: refused`, exit code **7** |
| The refusal is per class, with numbers | 4 recall breaches: t01 (0.667 to 0.000 on 6 items), t02 (0.500 to 0.000 on 12), t04 (0.500 to 0.000 on 4), t07 (0.500 to 0.000 on 6) |
| Plus an aggregate breach | `macro_f1_regression`: macro F1 fell 0.287 (0.338 to 0.051) beyond the tolerated 0.100 |
| The gate decides on the interval's lower bound, not the point estimate | Each recall breach reports the 95% lower bound against the tolerance |
| The refusal is ledgered | `ledger.jsonl`, 124 entries, `verify-ledger` exits 0 |
| The run is pinned to the limits it ran under | `tolerances_sha256` binds into `session_open` |
| The report stamps what produced it | seed, item digest, label digest, tolerances digest, git SHA |

Exit **7** rather than 5 is deliberate. 5 is `EXIT_INPUT_ERROR` throughout this
CLI, and a regression refusal is a governance outcome, not a broken call. A
degraded candidate must not be indistinguishable from a mistyped `--candidate`.

## What this deliberately does not claim

- **This gate detects a class collapse, not a few-point drift.** That bound
  comes from the data, not the gate's design, and no tolerance setting moves it.
  The generator plants 4 to 12 entities per threat class and **that number does
  not grow with `--scale`**: benign goes from 450 to 18,000 between scale 1 and
  40 while every threat class stays identical. At n=6 a point estimate would
  report a single-item difference as a 17-point regression, which is why the
  gate reads an interval instead and why the eval set is 59 items rather than
  something impressive-looking.
- **A candidate that is genuinely equal but noisy is also refused.** That is the
  chosen fail-closed posture, not an accident: activation requires evidence of
  non-regression, not absence of evidence of regression. The cost is accepted
  rather than tuned away.
- **Labels are generator plants, not human labels.** No adjudication, no
  inter-rater reliability, no rater-disagreement modelling.
- **Precision on this set is not deployment precision.** The set deliberately
  over-samples rare classes against a platform that is more than 97% benign.
- **The wind tunnel tests an aircraft the fleet does not fly.**
  `classify.threat_class` is versioned, evaluated and gated, and **no session
  consumes its output**. The registry lifecycle is real; the classifier's
  usefulness to this workbench is not demonstrated.
- **The stub cannot be persuaded and cannot reason.** It responds to the system
  prompt text, which is what makes this a test of the *gate* rather than of a
  mock, but it is not evidence about how a model would classify.
