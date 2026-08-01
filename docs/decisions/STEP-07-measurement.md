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
- [ ] VVR estimate + 95% CI reproduces exactly under fixed seed
- [ ] Bootstrap cross-check within documented tolerance of analytic CI
- [ ] Import-graph test: agents cannot import measurement or sealed access
- [ ] Sensitivity plots generated; policy-expansion simulation shows scope
      effect direction with explanation
- [ ] mypy --strict, ruff, coverage floor green; CHANGELOG updated
