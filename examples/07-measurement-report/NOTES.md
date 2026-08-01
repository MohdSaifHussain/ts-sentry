# 07: the measurement report, both lenses

**What this is.** The stamped measurement report over example 01's triage
session. Two lenses: the platform lens replicating YouTube's published Violative
View Rate methodology against the synthetic platform, and the workflow lens
asking whether the workbench helps.

Reproduce:

```bash
ts-sentry report --session ../01-triage-queue --build build --out .
```

## What it demonstrates

| Claim | Value in this report |
|---|---|
| A VVR estimate with a 95% confidence interval | **0.0953%**, CI 0.0720% to 0.1186%, n=9000 of N=18780 |
| The estimate is close to the truth this corpus carries | True baseline VVR on the seed-42 build is 0.0958%, inside the interval |
| Optimal (Neyman) allocation, not proportional | `allocation=optimal`; the verb draws a pilot so the estimator has a prior |
| The bootstrap cross-check agrees | 0.0476% to 0.1497% over 2000 replicates, half-width ratio 2.194 against an expected 2.143 |
| Validity conditions are reported even when they fail | Two of five conditions **FAIL** and are printed, not suppressed |
| Every number is stamped | git SHA, measurement seed, dataset digest, dataset seed/scale, corpus version, active prompt digests |
| Sensitivity curves are generated | CI width vs sample size, bias vs rater quality, policy scope expansion, as CSV, JSON and PNG |

The failing validity conditions are the point, not an embarrassment. The normal
approximation is invalid at every realistic sample size on this corpus and
becomes valid only at a full census, and the report says so on every estimate
rather than quietly printing an interval that does not hold.

## What this deliberately does not claim

The report itself carries ten honest limits, asserted entry by entry into every
rendering. The load-bearing ones:

- **The baseline estimand is narrow, and narrower still on this corpus.** The
  published method omits spam from the metric, so T-01 and T-06 are held out.
  The 18 violative views that carry the whole rate come entirely from T-02 and
  T-07; every other class contributes zero, and **T-04 receives no view events
  at all** despite being the class whose name most suggests it should dominate a
  provenance-stratified estimate.
- **The interval covers sampling error only.** That is a faithful replication of
  the published method, which states its intervals do not take rater quality
  into account. At realistic rater accuracy the rater-induced bias is *larger*
  than the interval. A wider interval would be a better estimate and a worse
  replication.
- **The risk proxy is not a detector.** There is no production classifier here,
  so content-provenance features stand in as the stratifier, described as an
  analog rather than as a classifier. It has no measured precision or recall.
- **Only two of five strata hold any views**, because viewed videos take only
  two distinct observable profiles on this build. No choice of cut points can
  manufacture strata the data does not contain.
- **Rater error is modelled as independent, and correlated error is not modelled
  at all.** A three-rater majority suppresses independent error quadratically
  and buys **nothing** against a policy misreading a whole panel shares.
- **Every analyst-minutes figure is an assumption, not a measurement.** There is
  no published per-case review-time benchmark to cite: TSPA defines review time
  and then warns there is considerable industry variation in both definitions and
  naming. The model reports a **break-even** rather than "minutes saved",
  because a delta over assumed inputs is a property of the assumption table.
  `MinutesResult` has no `minutes_saved` attribute, and that is enforced by test.
- **The PNG figures are byte-identical within one environment only.** The CSV
  and JSON curve data are byte-stable across runs and machines, and they are what
  a reader regenerates numbers from. Cross-version PNG stability is explicitly
  not claimed.
- **Synthetic data throughout.** The coincidental closeness of 0.0953% to the
  real platform's published 0.16 to 0.20% is a property of this generator's
  parameters and evidences nothing about the real platform.
