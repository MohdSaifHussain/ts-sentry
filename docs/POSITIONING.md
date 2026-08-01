# Capability traceability matrix

**What this document is.** A map from the representative responsibilities of a
Trust & Safety Engineering Analyst role family to the artifact in this
repository that evidences each one, with the metric or test that proves the
artifact does what it says.

**What this document is not.** It is **not** a response to any specific job
posting, and the left column is **not** quoted from one. This is a personal
project built against a generalized capability profile for the role family,
assembled from the responsibility areas referenced in
[ARCHITECTURE.md](../ARCHITECTURE.md): section 4.4's prompt engineering, data
labeling and performance analysis, and the threat-model, measurement and
regulatory-alignment sections. Nothing here is presented as a verbatim
quotation.

**Rows with no honest artifact say so.** A stated gap beats a stretched claim,
and there are five of them at the bottom. Reading only the first table would
give a misleading picture of what this repository demonstrates.

---

## Covered

| Capability | Sentry artifact | Path | Metric or test proving it |
|---|---|---|---|
| **Triage and prioritise a queue of flagged entities** | Decomposable priority score with published weights; every score renders as its components, never a bare number | `src/ts_sentry/agents/triage/`, `src/ts_sentry/orchestrator/detection_stub.py` | `tests/test_detection_discrimination.py`; on seed 42, priorities span 0.482 to 0.203 across 23 cases and the top case by severity ranks third, so the ranking discriminates on more than one component ([example 01](../examples/01-triage-queue/)) |
| **Investigate: pivot across data sources to reconstruct a network** | Evidence agent proposing from a fixed pivot vocabulary; work-list traversal chaining discovered entities as new seeds | `src/ts_sentry/orchestrator/pivots.py`, `evidence_turn.py` | `tests/test_pivot_tool.py`, `tests/test_recovery.py`; [example 02](../examples/02-evidence-t02-ring/) reconstructs a planted T-02 ring through the shared device fingerprint and signup IP bucket, 4 of 8 members, with four distinct pivot kinds used |
| **Write enforcement rationales that survive review** | DSA Article 17 style statements of reasons: facts, policy ground with a precise citation, proposed measure, redress | `src/ts_sentry/agents/memo/`, `src/ts_sentry/orchestrator/memo_gate.py` | `tests/test_memo.py`, `tests/test_overclaim_fixtures.py`; [example 03](../examples/03-signed-memo/) cites `prov-0000` and quotes the `comment-spam` clause verbatim from the hashed corpus |
| **Prompt engineering, with a way to know a prompt got worse** | Versioned content-addressed prompt registry, eval harness, regression gate reading a confidence interval's lower bound | `src/ts_sentry/prompt_registry/`, `src/ts_sentry/orchestrator/regression_gate.py` | `tests/test_degraded_prompts.py`, `tests/test_regression_gate.py`; [example 06](../examples/06-prompt-eval-refused/) refuses a degraded candidate with exit 7 and four per-class recall breaches plus a macro-F1 breach |
| **Data labeling and label integrity** | Sealed ground-truth schema no agent scope resolves to; eval-set builder with opaque, shuffled item ids | `src/ts_sentry/data/sealed.py`, `src/ts_sentry/governance/scopes.py`, `src/ts_sentry/data/eval_build.py` | `tests/test_scope_leakage.py`, `tests/test_import_graph.py`, `tests/test_eval_set.py`; Saif's red-team added a sealed member to `DataScope` and 10 tests failed, only 3 of which were written for leakage |
| **Performance analysis: precision, recall, F1, confusion by class** | Eval report with per-class confusion and bootstrap intervals | `src/ts_sentry/orchestrator/prompt_eval.py`, `eval_report.py` | `tests/test_prompt_eval_turn.py`; [example 06](../examples/06-prompt-eval-refused/)'s `eval_report.json` carries per-class precision, recall and F1 with intervals |
| **Measure platform-level harm prevalence** | VVR methodology replication: stratified sampling, Neyman allocation, 95% CI, bootstrap cross-check, sensitivity curves | `src/ts_sentry/measurement/vvr.py`, `frame.py`, `sensitivity.py` | `tests/test_vvr_estimator.py`; Barnett's published Table 2B allocation (2098/828/584/256/234) and standard error (0.054pp) both reproduce exactly from his population, which is external validation rather than self-consistency |
| **Query data correctly and safely at analyst scale** | Reviewed parameterized query templates; no dynamic SQL anywhere; the agent never composes SQL | `src/ts_sentry/orchestrator/pivots.py` | `tests/test_pivots.py` asserts over the SQL text; every projection is aliased and no query orders by position, so a reordered SELECT cannot silently mislabel evidence fields |
| **Interpret and apply written policy** | Hashed, anchored policy corpus with clause boundaries following policy subject rather than page layout | `src/ts_sentry/data/policy_corpus.py`, `policies/` | `tests/test_policy_corpus.py`; citation resolution is checked against real anchors, and a memo quoting fewer than four words of a clause is refused with its own reason code |
| **Build tooling that removes analyst toil** | Eight CLI verbs, offline by default, artifacts written for every run | `src/ts_sentry/cli/main.py` | `tests/test_cli.py`, `tests/test_run_session_cli.py`; the quickstart path runs end to end with zero credentials |
| **Auditability: prove what a system did** | Append-only hash-chained trajectory ledger with a stored head anchor | `src/ts_sentry/governance/ledger.py`, `src/ts_sentry/orchestrator/manifest.py` | `tests/test_ledger_properties.py` (hypothesis), `tests/test_examples.py`; all **six** example chains verify and match their anchors, and a truncated copy is caught only by the anchor |
| **AI governance: constrain what an agent may do** | Mandates as frozen hashed code; ENFORCE unreachable at type level; prohibited agent-to-agent topology | `src/ts_sentry/governance/mandate.py`, `signature.py` | `tests/test_enforce_unreachable.py` uses two independent mechanisms, neither subsuming the other: an in-place `type: ignore` with `warn_unused_ignores`, and a subprocess run that strips the ignore and asserts the error |
| **Adversarial thinking and red-teaming** | Input firewall with a content-derived fence nonce and nonce-bound redaction markers; an adversarial fixture set with results recorded as measured | `src/ts_sentry/orchestrator/firewall.py` | `tests/test_firewall.py`; **four fixtures are asserted as UNDETECTED**, which is the honest half. A hypothesis property found that `splitlines` breaks on U+2028 where JSON does not escape it, so one comment could forge a second record inside a fenced block |
| **Reproducibility and provenance discipline** | Seeded builds, content-addressed prompts, stamped reports, dataset digests derived from byte-stable exports | `src/ts_sentry/provenance.py`, `src/ts_sentry/data/generator.py` | `tests/test_persistence.py`; two builds in separate directories produce different `build.duckdb` files and the **same** dataset digest, both directions pinned by tests |
| **Documenting decisions so others can audit the reasoning** | A decisions log that cites where each decision was actually made, and lists the choices made by default with no recorded rationale | `docs/DECISIONS.md`, `docs/decisions/STEP-01..08` | Every cited SHA is resolved against `git log` at phase close. This became part of the process because two SHAs in the Phase 5 table were invented during drafting and caught before the commit landed |

---

## Not covered, stated plainly

These are capabilities the role family involves that this repository does
**not** evidence. Listing them is the point; a matrix showing only the covered
rows would misrepresent what a reader is looking at.

| Capability | Why there is no artifact |
|---|---|
| **Working with real user data at production scale** | Everything here is synthetic except 1,956 public YouTube comments used for one firewall demonstration. No real user data, no PII handling, no retention or deletion workflow, no access-control model beyond the mandate allowlist. |
| **Real-time or high-volume operations** | One analyst, one session at a time, synchronous by design. No concurrency, no queueing, no throughput measurement, no service. The orchestrator is a state machine in a CLI process. |
| **Cross-functional stakeholder communication** | The memo format is regulation-shaped and the report is written for a reader, but there is no artifact evidencing working with legal, policy, engineering or comms partners, because that is not a thing a repository can contain. |
| **Incident response and escalation** | No on-call artifact, no severity model, no escalation path, no postmortem. The `GATE_REJECTION` path is a governance control, not an incident workflow. |
| **Measured judgment quality on real cases** | Every agent output here came from a deterministic stub. There is no inter-rater reliability, no adjudication, no human-label agreement study, and no evidence about how a real model or a real analyst would perform. The workbench's own uplift figure is a modelled break-even, not a measurement. |

---

## How to check any row

Every path above is real and every test named runs in the standard suite:

```bash
pip install -e ".[dev]"
pytest tests/test_enforce_unreachable.py -v     # or any row's test
```

The examples are committed with their artifacts, so the claims that reference
them can be checked by reading a file rather than by running anything:
[examples/](../examples/).
