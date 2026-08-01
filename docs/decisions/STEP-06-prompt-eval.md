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
- [x] Degraded-prompt fixture refused with per-class breach report; ledgered
- [x] Rollback pointer-move test green
- [x] Contamination discipline verified: no per-item label egress in tooling
- [x] Bootstrap CI deltas present in report; seed-stamped
- [x] mypy --strict, ruff, coverage floor green; CHANGELOG updated

## 6. Outcome

Shipped: D1-D6 plus an added D7, in `src/ts_sentry/prompt_registry/`,
`src/ts_sentry/agents/prompt_eval/`, `src/ts_sentry/data/eval_build.py` and
`eval_set.py`, `src/ts_sentry/orchestrator/` (eval_labels, eval_tool,
prompt_eval, prompt_eval_turn, regression_gate, eval_report, eval_session), the
committed `prompts/` and `evals/threat_class/` artifacts, and the
`eval-prompts` CLI verb. 1023 tests green, mypy `--strict` and ruff clean,
92.28% line coverage against a 90 floor. Fully offline; the suite passes with
the live environment variables exported.

### The four decisions taken before implementation

Raised as numbered questions before any code, because each changed the shape of
the work. Recorded in `docs/DECISIONS.md` 6.1 through 6.4: the evaluated task is
a new classification prompt no session consumes; `EXIT_REGRESSION_REFUSED` is 7
rather than D5's literal 5; the three shipped prompts migrate record-only; and
the gate reads the interval's lower bound rather than the point estimate.

### What the exit criterion actually showed

On the committed eval set, through the CLI, against a real chain:

| Run | Result |
|---|---|
| Neutral candidate (wording change, same answers) | exit 0, activatable, every interval `[0.000, 0.000]` |
| Degraded candidate (talks itself into benign) | exit 7, 4 per-class recall breaches + 1 macro-F1 breach, ledgered |
| `verify-ledger` on the refused run, bare | exit 0, 124 entries, intact |
| `verify-ledger --expect-head-from` the manifest | exit 0, head matches |
| Truncated copy, `--expect-head-from` | exit 6, both heads printed |

The refusal names the classes and the numbers behind them, for example
`t02_fake_engagement_network: recall fell 0.500 (0.500 to 0.000) on 12 item(s),
and the 95% interval lower bound -0.750 is beyond the tolerated drop of 0.250`.

The truncation row is what it has been since STEP-02: a *passing* result that
confirms a real limitation. Chain verification alone accepts a truncated export,
and only the stored anchor catches it.

### The central finding: the eval set has a ceiling the generator sets

Full measurements and the reasoning are in `docs/DECISIONS.md` under Phase 6.
In short: threat entities per class are 4 to 12 and **do not vary with
`--scale`** (benign grew 450 to 18,000 across scale 1 to 40 while every threat
class stayed identical), because `RING_COUNT` and `MEMBERS_PER_RING` are fixed
constants that `for_budget` only shrinks. Content is byte-identical across
seeds, so pooling seeds would add duplicates and narrow the bootstrap interval
by replication.

The consequence is stated plainly rather than left to be inferred: **this gate
detects a class collapse, not a few-point drift.** That bound comes from the
data, not from the gate's design, and no tolerance setting can move it. It is
written into the report artifact itself, in the shape DECISIONS 4.9 used for the
recovery ceiling.

This is **not** a STEP-01 defect. STEP-01's exit criterion was byte-stable
rebuilds and a passing leakage test; it met both and neither is touched here.

### Defects found by running it, not by inspection

1. **The eval item ordinal leaked the entire answer key.** The builder emitted
   subjects in entity-id order and planted ids are class-prefixed, so items
   arrived in contiguous per-class blocks: 0-5 t01, 6-11 t02, 44-58 benign.
   `items.json` carries no labels and is the artifact safe to hand a prompt
   author; on its own it was sufficient to reconstruct every label. Every "no
   item names its own class" test passed throughout, because the content check
   never looked at ordering. Found at the review stop by asking what an ordinal
   leaks. Fixed by shuffling with the seeded generator before ids are assigned:
   54 contiguous label runs across 59 items, against 9 before.
2. **The classification stub was keyed on a mode flag, not on the prompt.**
   `StubMode.OVERCLAIM` belongs to the adapter, so it degraded incumbent and
   candidate alike, every delta came out zero, and the degraded candidate was
   reported **activatable**. The deeper problem is that a stub keyed on a flag
   answers identically whatever prompt it is given, so the harness would have
   produced a report, an interval and a verdict all describing the flag.
3. **An unparseable answer would have flattered the failing version.** It
   shortened one version's list and misaligned the pairing; dropping the item
   from both sides would have deleted the failure from the version that failed.
4. **The sealed-name check fired on `__slots__ = ("_labels",)`.** The needle is
   deliberately broad and `__slots__` puts an attribute name in a string
   literal. Renamed to `_answers` rather than widening the allowlist, which
   would have recorded that the module reads `sealed._labels` when it must not.
5. **`core.autocrlf=true` broke the content-addressing claim on checkout.**
   Measured with `git checkout-index` rather than assumed: all four prompt files
   came back CRLF and none matched its own name by raw bytes, while the registry
   still loaded fine because `read_text` normalizes newlines. The independent
   check had stopped being independent on the development platform. Fixed with
   `.gitattributes` and pinned by an assertion over raw bytes.

### Readings and deviations, recorded

1. **D7 is an added deliverable.** STEP-06's table has six IDs. The
   degraded-prompt fixture suite is the phase's exit criterion and was given
   deliverable standing rather than smuggled into D4's tests, per Saif.
2. **`EXIT_REGRESSION_REFUSED = 7`** deviates from D5's literal "exit 5". See
   DECISIONS 6.2.
3. **`eval-prompts` is a CLI surface STEP-06 enumerates but whose flags it does
   not specify.** `--registry`, `--evals`, `--out`, `--analyst-id`, `--seed`
   and `--session-id` follow the shapes `run-session` established.
4. **The eval session's `dataset_digest` comes from the eval-set manifest**,
   not from a build directory, because a prompt evaluation opens no dataset:
   `PROMPT_EVAL_MANDATE` grants no scopes at all.
5. **`tolerances_sha256` binds into `SESSION_OPEN`** rather than becoming a
   twelfth `EventType`, on DECISIONS 5.8's precedent.
6. **The `ToolResources` eval fields are typed `object`**, following `pack` and
   `memo`. Here it does a second job: naming `EvalLabelStore` in `toolspec`
   would put the eval answers one import away from every tool contract in the
   system.

### The `ToolId` countdown, discharged

`IMPLEMENTATION_PHASE` is 6 and `pending_handlers(TOOL_TABLE)` is **empty for
the first time since the table was written**. That discharges the claim
`orchestrator/tools.py` has carried since STEP-03: "by STEP-06 the table is
fully executable or the build is broken". Every declared tool now runs.

### Cross-phase effect, recorded because it looks like tampering

Adding `PROMPT_EVAL_MANDATE` changes `mandate_set_hash` for **every** session
type, so chain heads recorded before STEP-06 no longer reproduce, including
Saif's STEP-05 phase-close head. Same class as the effect STEP-05 recorded when
the memo mandate landed. The per-agent mandate hashes are untouched.

### Honest limits

- **The wind tunnel tests an aircraft the fleet does not fly.**
  `classify.threat_class.v1` is versioned, evaluated and gated, and no session
  consumes its output. The registry lifecycle is real; the classifier's
  usefulness to this workbench is not demonstrated.
- **The gate detects class collapse, not drift**, bounded by the generator as
  recorded above. A candidate that is genuinely equal but noisy is refused, and
  that is the chosen fail-closed posture rather than an accident.
- **Labels are generator plants, not human labels.** No adjudication, no
  inter-rater reliability, no rater-disagreement modelling.
- **Precision on this set is not deployment precision**, because the set
  deliberately over-samples rare classes against a >97% benign platform.
- **The AST purity test is narrower than it looks.** It walks `ast.Name` calls
  only, so it catches `open(...)` or a module-level helper and would not catch a
  method call on an argument. The hypothesis properties are the real check;
  the AST test closes one specific hole.
- **`write_registry` does not refuse a shrinking version set.** A caller passing
  a registry with a version dropped rewrites the manifest without it.
  `load_registry` then fails on the now-unrecorded file, so it fails closed, but
  the manifest's index record for that version is gone and recoverable only from
  git. Recorded, not fixed.
- **`test_this_phase_landed_the_handler_it_owed` has outlived its shape.** It
  asserts exactly one entry has `handler_due_step == IMPLEMENTATION_PHASE`; the
  table is now fully executable, so at phase 7 no entry does and the test fails.
  STEP-07 inherits rewriting it.
- **The prompt-eval agent's competence is untested**, as every agent's has been
  since STEP-03. The stub cannot be persuaded and cannot reason. What is tested
  is that a prompt must earn activation.
- **The 3.12 note, at its true width.** Local Python is 3.14.0 (verified this
  session), so local green results are 3.14 results. That is a limit on **local
  runs**, not an open risk for phases already proven green on the pinned 3.12 CI
  interpreter.
