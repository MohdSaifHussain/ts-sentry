# STEP-06: Prompt-Eval Agent + Regression Gate

**Project:** Trust & Safety Sentry | **Phase:** 6 of 8 | **Date:** 31 July 2026 (IST)
**Status:** Specified, not started
**Depends on:** STEP-05

## 1. Objective
Policy-as-prompt with a wind tunnel: versioned prompt registry, offline eval
harness, and a regression gate that refuses activation of worse prompts. Exit
criterion: a deliberately degraded prompt version is refused and the refusal
is ledgered.

## 2. Deliverables

| ID | Deliverable | Governing standard(s) |
|---|---|---|
| D1 | Prompt registry: content-hash-named files in `prompts/`, manifest with version, parent, task binding, activation state | Content-addressable versioning; Conventional Commits for changes |
| D2 | Labeled eval set: stratified across T-01..T-07 + benign controls; label provenance recorded | Stratified evaluation design; class balance documented |
| D3 | `agents.prompt_eval`: runs candidate vs incumbent; reports precision, recall, F1, per-class confusion, bootstrap CIs on deltas | Standard IR metrics; uncertainty reported, not just point estimates |
| D4 | Regression gate: declared per-metric tolerances in config; activation refused on breach; refusal ledgered | ARCHITECTURE 4.4; NIST AI RMF MEASURE->MANAGE loop |
| D5 | `cli: ts-sentry eval-prompts --candidate HASH` | Exit codes: 0 activatable, 5 regression refusal (report path printed) |
| D6 | Eval report artifact: md + JSON, stamped with dataset seed, eval-set hash, model adapter id | Reproducible evaluation practice |

## 3. Requirements
- 3.1 Offline-first: CI uses the deterministic stub adapter with recorded
  fixture responses; live evaluation is a documented manual mode.
- 3.2 No training on the eval set semantics: prompt authors (human or agent)
  never see per-item eval labels through the tooling; only aggregate reports
  (contamination discipline).
- 3.3 Gate tolerances: e.g. recall drop > 0.02 absolute on any threat class
  refuses activation; values live in config, changes are ledgered corpus-style
  events.
- 3.4 Incumbent immutability: activation swaps a pointer; prior versions
  retained forever (rollback is a pointer move, ledgered).
- 3.5 hypothesis property: gate decision is a pure function of (report,
  tolerances); same inputs, same verdict.

## 4. Out of Scope
- Automated prompt optimization loops (roadmap; would require its own mandate
  class and contamination review).

## 5. Exit Checklist
- [ ] Degraded-prompt fixture refused with per-class breach report; ledgered
- [ ] Rollback pointer-move test green
- [ ] Contamination discipline verified: no per-item label egress in tooling
- [ ] Bootstrap CI deltas present in report; seed-stamped
- [ ] mypy --strict, ruff, coverage floor green; CHANGELOG updated
