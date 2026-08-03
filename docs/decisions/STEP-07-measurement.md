# STEP-07: Measurement Layer

**Project:** Trust & Safety Sentry | **Phase:** 7 of 8 | **Date:** 31 July 2026 (IST)
**Status:** Specified, not started
**Depends on:** STEP-01 (sealed labels), consumes artifacts from 03-06

## 1. Objective
Deterministic measurement module with two lenses: VVR-methodology replication
(platform lens) and workbench-effect metrics (workflow lens). Exit criterion:
CI-stamped report with 95% CI and sensitivity plots.

## 2. Deliverables

| ID | Deliverable | Governing standard(s) |
|---|---|---|
| D1 | `measurement.vvr`: stratified view sampling, review simulation with configurable rater accuracy/disagreement, aggregate rate + 95% CI | YouTube's published VVR methodology (sample views -> review -> aggregate; 95% CI reporting); ASA-compliant language on uncertainty (delivery-engine precedent) |
| D2 | Sensitivity analysis: CI width vs sample size; estimate bias vs rater quality; policy-scope expansion simulation | Documented-methodology practice: every curve reproducible from seed |
| D3 | `measurement.workflow`: analyst-minutes model (baseline vs assisted, assumptions table), evidence recovery @ budget (from STEP-04), memo verification pass rate, governance activity counts (gate rejections, violation attempts, prompt refusals) | Honest Limits discipline: modeled estimates labeled as modeled; no fabricated user studies |
| D4 | Report generator: md + html, stamped with dataset seed, git SHA, corpus version, prompt version pointer | Reproducible-research stamping |
| D5 | `cli: ts-sentry report --session PATH` | Single-command report from session artifacts |

## 3. Requirements
- 3.1 Statistical implementation: stratified estimator with finite-population
  correction where applicable; CI via normal approximation with documented
  validity conditions and a bootstrap cross-check; disagreement modeled as a
  confusion matrix per simulated rater.
- 3.2 Sealed-label access: measurement code is the only consumer of
  `sealed._labels`; import-linted so no agent or orchestrator module imports
  measurement internals (architecture test via import-graph check).
- 3.3 Governance-activity metrics are mandatory sections; zero values render
  with an explicit note that zero indicates untested gates in that session.
- 3.4 Plotting: matplotlib, deterministic (fixed seeds, fixed dpi); every
  figure regenerable byte-stable or hash-stable (documented which).
- 3.5 Language rules: no causal claims from the workflow lens; comparative
  statements carry assumption references.

## 4. Out of Scope
- Real-user timing studies; dashboards (roadmap: static html is v1).

## 4a. Review Stop
- Halt after D1+D2 (the statistical core), before D3. D2's curves are computed
  from D1's estimator, so the two are one reviewable unit, and the statistical
  validity is where review attention is worth spending; D3-D5 consume a
  validated core. Added to this contract during the phase, after the stop was
  agreed and observed in session; recorded here so the halt is contractual
  rather than only instructed. Saif, 1 August 2026.

## 5. Exit Checklist
- [x] VVR estimate + 95% CI reproduces exactly under fixed seed
- [x] Bootstrap cross-check within documented tolerance of analytic CI
- [x] Import-graph test: agents cannot import measurement or sealed access
- [x] Sensitivity plots generated; policy-expansion simulation shows scope
      effect direction with explanation
- [x] mypy --strict, ruff, coverage floor green; CHANGELOG updated

## 6. Outcome

Status: **shipped and verified**, D1 through D5. 1,172 tests pass;
`mypy --strict`, `ruff check` and `ruff format --check` clean on 161 files;
coverage 93.09% against a 90% floor.

### Phase close, verified

Saif verified this phase personally on 1 August 2026. He read the footprint
(7,045 insertions, closed-phase touches all expected), ran the `report` verb by
hand and confirmed all twelve artifacts plus the partial-run "Not computed"
path, read `report.md` in full against its claims, and verified all fifteen log
SHAs as real commits.

His read of the report found a real defect that no test caught: the bootstrap
cross-check's expected width ratio was computed from the wrong quantity, so
exit criterion 2 did not in fact hold at the report verb's default sample size.
Recorded and fixed below. Pushes are checkpoint-gated (CLAUDE.md Process): the
commits were held locally per deliverable and pushed only after this
confirmation and the fix it produced.

### Sources, consulted before implementing

The Google Transparency Report help centre (answer/9209072), the YouTube blog
post introducing the metric, and the independent statistical assessment Google
commissioned from Arnold Barnett (MIT, September 2021), reproduced at
`docs/barnett-vvr-assessment.txt`. Barnett is the only source describing the
stratification and the allocation, and the design changed materially once it was
readable. For D3, TSPA's content-moderation metrics curriculum and DTSP's
Best Practices Framework.

### Exit checklist, evidenced

1. **Reproduces under fixed seed.** Two runs at one seed give identical
   estimate, interval and bootstrap bounds; changing the replicate count leaves
   the drawn sample untouched, which is what the named child streams buy.
2. **Bootstrap within tolerance, in the documented direction.** The bootstrap
   ignores the finite population correction so it comes out wider in
   expectation. Verified across four seeds and three sample sizes: every
   applicable case agrees within 0.25. At a point estimate of zero the check
   reports itself *not applicable* rather than passing vacuously.

   **This criterion did not hold when Saif first verified it**, and the defect
   was mine rather than his run's. See "The defect found at phase-close
   verification" below.
3. **Import graph.** No agent or orchestrator module reaches any module under
   `measurement`. The test was generalised from pinning `measurement.recovery`
   by name, which would have left the five new modules unguarded.
4. **Sensitivity plots and scope direction.** Three curves, byte-stable as data
   and byte-identical as PNGs within one environment. Direction comes from arm B.
5. **Green and recorded.** CHANGELOG and DECISIONS updated.

### The external validation worth naming

Barnett's published Table 2B optimal allocation (2098/828/584/256/234 at a
4,000-view sample) reproduces **exactly** from his population shares and rates,
and his published standard error of 0.054 percentage points reproduces from the
variance formula independently of the allocator. Two numbers from an outside
statistician, checking arithmetic that would otherwise only have been checked
against our own expectations.

### Deviations, recorded

1. **The approved plan's strata and allocation were both wrong**, and were
   changed once Barnett was readable. The plan stratified on observable metadata
   and declared Neyman allocation out of scope; the published method bands a
   risk score and allocates optimally. On Barnett's own population the plan's
   choice would have produced intervals 30% wider than the method it claimed to
   replicate. See DECISIONS 7.3 and 7.5.
2. **The risk proxy is an analog, not a classifier.** There is no production
   classifier here, so content-provenance features stand in and the module says
   so in those words rather than claiming a detection capability (7.3).
3. **Arm B changes the attribution rule, not the class set**, so it is a
   policy-scope illustration and not a VVR. Carried in the type, the curve note,
   the figure and the report (7.8).
4. **`report --session` takes an optional `--build`.** D5 names only
   `--session`; the workflow lens needs nothing else, the platform lens needs
   the dataset. Without it the report says the lens was not computed (7.17).
5. **`IMPLEMENTATION_PHASE` reached 7 with no tool to land**, so
   `test_this_phase_landed_the_handler_it_owed` was rewritten over the finished
   countdown rather than deleted or exempted (7.12).
6. **The D1+D2 review stop was added to this contract during the phase**, after
   it had been agreed and observed in session (section 4a).

### Defects found by running it, not by inspection

1. **A zero-width interval that all four validity conditions passed.** At 14,000
   of 18,780 views the allocation censused one stratum while another returned no
   violative calls, so its `p(1-p)` contribution vanished. The Wald collapse at
   `p_hat = 0`, invisible to aggregate conditions because in aggregate the sample
   did find violative views. Fixed with a per-stratum degeneracy condition (7.9).
2. **An unsampled non-empty stratum silently biased the estimate.** Weights are
   shares of the whole frame, so estimating over the sampled strata alone asserts
   the rest hold nothing; at 40 views with the minimum relaxed the estimate
   covered 99.48% of the population weight while looking healthy. Refused now.
3. **The report verb produced proportionally-allocated intervals.** No pilot, so
   no optimal allocation, so an interval half again as wide as the method gives.
   Found by reading the first generated report (7.18).
4. **The 3.3 zero-note contradicted its own table**, saying "every control count
   above is zero" while forty passing verifications were printed above it.
5. Plus three smaller ones fixed at the review stop: a curve-generation crash at
   small sample sizes, a lossy disagreement count reconstructed from a rate, and
   unquoted CSV labels.

### The central finding: this corpus bounds every rate the lens reports

The frame holds 18,780 views and the true baseline VVR is **0.0958%**, carried by
**18 violative views**, all from T-02 and T-07. T-04, the class whose name most
suggests it should dominate a provenance-stratified estimate, receives **no view
events at all**, which corrected a wrong planning assumption before any code was
written against it. Three consequences, all limits rather than defects: the
honest operating regime is large sampling fractions where the finite population
correction is load-bearing; the normal approximation is invalid at every
realistic sample size and valid only at a census; and only two of five strata
hold any views. Full measurements in DECISIONS under Phase 7.

### Obligations discharged, and the one that was not

The Phase 4 traversal obligation is **met in general and unmet on its named
target**. A work-list construction solves both recorded defects at once (7.13),
and four of seven threat classes now recover strictly more at every budget with
T-06 becoming budget-sensitive. `t02_chan_000_000`, which STEP-04 named
concretely, went from 3 to 4 of 8 members and did **not** become
budget-sensitive. Recorded as unmet rather than counted discharged because a
different class moved (7.14). Saif accepted it as-is: the plateau is a bounded
limit of a metadata-pivot strategy, the same shape as the structural recovery
ceiling, and reaching the remaining members is future work with a named blocker,
not a STEP-07 gap.

### The defect found at phase-close verification

Saif read a generated `report.md` and reported a bootstrap half-width ratio of
**2.194 against an expected 1.386** at a 48% sampling fraction. He read it as the
bootstrap being wider in the documented direction, which it was. It was also
**outside the documented tolerance of 0.15**, which meant exit criterion 2 did
not hold at the report verb's default sample size.

The defect was in `expected_ratio`, not in the bootstrap and not in his run. It
predicted `1 / sqrt(1 - f)` from the *overall* sampling fraction, which is
correct only when every stratum is sampled at the same rate. Optimal allocation
guarantees they are not: at 9,000 of 18,780 views the middle stratum, which
carries all the signal, sits at a 0.78 sampling fraction while the others sit
near 0.16. Its analytic variance contribution nearly vanishes under its own FPC
while the bootstrap still gives it full weight.

Reproduced before fixing: ratios of 2.194, 2.422 and 1.971 across three seeds at
n=9000, all against a predicted 1.386, and all within tolerance at n=2000 and
n=5000 where the allocation is still nearly even. That pattern is the signature
of the predictor being wrong rather than the bootstrap being unstable.

Corrected to `sqrt(V_without_fpc / V_with_fpc)` over the same strata, which is
exact, reduces to the old form when fractions are equal, and now tracks the
observed ratio to within Monte Carlo noise: 12 of 12 applicable cases agree
within 0.25 across four seeds and three sample sizes. `BootstrapCheck` also
carries `observed_successes` now, because a percentile bootstrap over three
successes is discrete enough that no tolerance setting makes the comparison
meaningful, and a reader seeing a disagreement should look there first.

Widening the tolerance to fit the observed numbers was the alternative and was
not taken: it would have hidden a wrong formula behind a looser test.

**This is the third phase-close defect found by Saif reading an artifact rather
than by any test** (STEP-04's non-traversing pack, STEP-03's ranked queue,
this). The pattern is worth naming: the tests check that the arithmetic is
self-consistent, and a human reading the output checks that it means anything.

### Honest limits

Ten, carried in `measurement.report.HONEST_LIMITS` and asserted entry by entry
into every rendering. The load-bearing ones: the baseline estimand is narrow and
narrower still on this corpus; the interval covers sampling error only and at
realistic rater accuracy the rater-induced bias is larger than the interval; the
risk proxy is not a detector and has no measured precision or recall; rater error
is modelled as independent and correlated error is not modelled at all; no
published per-case review-time benchmark exists, so every minute figure is an
assumption; evidence recovery plateaus; and PNG figures are byte-identical within
one environment only.
