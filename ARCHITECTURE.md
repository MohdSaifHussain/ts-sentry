# Trust & Safety Sentry

## Governed Agentic Workbench for Trust & Safety Scaled Abuse Analysis

**Version:** 1.0.0 | **Date:** 31 July 2026 (IST), status updated 2 August 2026
**Author:** Mohd Saif Hussain
**Status:** Implemented through Phase 8. This document remains the **design
authority**: it is the specification the build was measured against, not a
description written afterwards to match it. Where the implementation departs
from it, the departure is recorded as a deviation or an erratum in the relevant
STEP file's Outcome and in `docs/DECISIONS.md`, and this text is left standing.
Two errata are noted inline below (3.2 and 4.4).
**License:** MIT

---

## 1. Positioning Statement

Enterprise detection systems find the signal. This workbench eliminates the manual
toil between signal and decision. Every step is auditable, and every enforcement
decision is human.

Trust & Safety Sentry is cognitive support automation for a Trust & Safety scaled abuse
analyst. It is explicitly **not** a detection platform, **not** a moderation
system, and **not** a replacement for enterprise tooling. It models the analyst
workflow that sits downstream of detection: triage, investigation, evidence
assembly, enforcement rationale drafting, prompt quality assurance, and impact
measurement.

The system is agentic in the 2026 sense: a small fleet of narrow AI agents
performs bounded cognitive work under a deterministic control layer, supervised
by one human analyst who holds sole enforcement authority.

### 1.1 Design Thesis

Industry consensus for 2026 is hybrid moderation: AI performs the heavy lifting
while humans handle edge cases and consequential decisions. Google DeepMind's
AI Control Roadmap (June 2026) formalizes the complementary security posture:
treat AI agents as potentially misaligned insiders and build monitoring,
containment, and shutdown infrastructure around them.

Trust & Safety Sentry accepts the insider-threat premise and answers it with a stricter
posture:

> **Detective controls assume the harmful action can occur and race to catch it.
> Preventive controls remove the action from the action space.**

Where the DeepMind roadmap monitors, alerts, and kill-switches, Trust & Safety Sentry
makes ungoverned actions structurally unexpressible. An agent cannot exceed its
mandate because the orchestrator will not dispatch any action outside it. This
is the same doctrine proven in delivery-engine v1.x: agent proposes,
deterministic tools dispose, human governs.

---

## 2. Threat Model (Dual)

The system carries two threat models simultaneously. Most T&S tooling carries
only the first. Carrying both is the point.

### 2.1 Platform Abuse Threats (the caseload)

Modeled in synthetic data, aligned to YouTube's 2026 enforcement surface:

| ID | Threat class | 2026 relevance |
|----|--------------|----------------|
| T-01 | Coordinated comment spam rings | Classic; still dominant by volume |
| T-02 | Fake engagement networks (sub-for-sub, view bursts, engagement pods) | Fake Engagement policy |
| T-03 | Off-platform diversion (malware, scam funnels) | Spam & Deceptive Practices policy |
| T-04 | Undisclosed synthetic media channels | 2026 AI disclosure rules; nondisclosure risks demonetization |
| T-05 | AI-persona authority channels (synthetic "experts" giving health, finance, legal advice) | Named 2026 enforcement priority |
| T-06 | Mass-produced inauthentic content networks ("slop farms") | July 2025 monetization update, tightened through 2026 |
| T-07 | Coordinated influence operation clusters | TAG / Influence Operations Bulletin patterns: many channels, one narrative, shared infrastructure |

### 2.2 Agent Threats (the fleet itself)

Following the insider-threat framing, each agent is assumed potentially
unreliable. Failure modes addressed by design, ordered by observed likelihood
(DeepMind's published trajectory analysis found most flagged events stem from
misinterpretation and overeagerness, not adversarial intent):

| ID | Failure mode | Primary control |
|----|--------------|-----------------|
| A-01 | Overclaiming: agent asserts facts not in evidence | Symbolic verification; claims must trace to evidence-record IDs |
| A-02 | Overreach: agent attempts actions beyond its task | Mandate-constrained dispatch; action not in mandate cannot execute |
| A-03 | Prompt injection via adversarial case content | Input firewall; all case content is data, never instructions (OWASP LLM01 mitigation) |
| A-04 | Scope creep across data boundaries | Data-scope allowlists per mandate |
| A-05 | Silent drift in classification quality | Prompt regression gate; no prompt version ships on regression |
| A-06 | Unauditable behavior | Hash-chained trajectory ledger; no ledger write, no result accepted |
| A-07 | Agent-to-agent collusion or cascade | Prohibited topology: agents never communicate directly |

---

## 3. Governance Model

Three constructs, native vocabulary, with an explicit crosswalk to external
frameworks in Section 8. This vocabulary predates and independently converges
with the DeepMind roadmap; the mapping table exists to make the convergence
inspectable, not to claim compliance.

### 3.1 Mandate

A Mandate is a frozen, declarative specification of everything an agent is
permitted to do. It is code, not policy prose.

```python
@dataclass(frozen=True, slots=True)
class Mandate:
    agent_id: AgentId                 # StrEnum
    consequence_ceiling: Consequence  # StrEnum: OBSERVE | ASSEMBLE | RECOMMEND
    allowed_tools: frozenset[ToolId]
    data_scopes: frozenset[DataScope] # tables/views the orchestrator may query for it
    output_schema: type               # structural contract on every agent output
    token_budget: int
    max_steps: int
```

Properties:

- **Enforcement is unreachable.** `Consequence.ENFORCE` exists in the enum and
  is constructible only by the human-signature path. No Mandate can carry it.
  This is a type-level guarantee, testable and shown in the test suite.
- Mandates are versioned, hashed (SHA-256), and recorded in the ledger at
  session start. A mandate change is itself an audited event.
- The orchestrator validates every proposed agent action against the mandate
  **before dispatch**. Violations are not "blocked"; they are never executed,
  and the attempt is ledgered as a governance event.

### 3.2 Trajectory Ledger

An append-only, hash-chained record of every step every agent takes.

- Each entry: `(seq, timestamp_ist, agent_id, mandate_hash, event_type,
  payload_digest, prev_hash, entry_hash)`.
- `entry_hash = SHA256(seq || timestamp || agent_id || mandate_hash ||
  payload_digest || prev_hash)`, giving tamper-evidence across the chain.

  > **Erratum, recorded in STEP-02 rather than silently corrected.** Read
  > literally, `||` is bare concatenation, which is ambiguous: `("ab", "c")` and
  > `("a", "bc")` produce identical bytes, so two materially different entries
  > can collide on the one digest whose whole job is telling entries apart. The
  > implementation uses a `\x1f`-separated encoding that rejects any field
  > containing the separator. See DECISIONS 2.1.
- Event types: `PROMPT_SENT`, `TOOL_CALLED`, `TOOL_RESULT`, `OUTPUT_PROPOSED`,
  `VERIFICATION_PASS`, `VERIFICATION_FAIL`, `GATE_REJECTION`, `HUMAN_DECISION`,
  `MANDATE_VIOLATION_ATTEMPT`, `SESSION_OPEN`, `SESSION_CLOSE`.
- Storage: DuckDB table plus exported JSONL per session. A `verify-ledger` CLI
  recomputes the chain and reports the first broken link, if any.
- The ledger is a first-class deliverable: `GATE_REJECTION` and
  `VERIFICATION_FAIL` counts are showcased metrics, not embarrassments. A
  governance layer that never fires is a governance layer that was never tested.

### 3.3 Consequence Gates

Every action in the system is classified by **consequence**, not by content.

| Level | Meaning | Gate behavior |
|-------|---------|---------------|
| OBSERVE | Read-only analysis; produces rankings, summaries, metrics | Auto-approved; ledgered |
| ASSEMBLE | Constructs evidence artifacts from source data | Deterministic validation before acceptance (schema, referential integrity, provenance completeness) |
| RECOMMEND | Produces a proposed enforcement rationale | Symbolic verification: every claim sentence must carry at least one resolvable evidence-record ID; unresolvable claims fail the memo |
| ENFORCE | An enforcement decision | **Human only.** Requires analyst identity, explicit decision, and signature hash. Structurally unreachable by any agent under any mandate |

The gate pipeline per agent turn:

```
input firewall -> mandate check -> dispatch -> output schema check
      -> consequence gate (per level) -> ledger append -> deliver to analyst
```

This is the delivery-engine architectural signature (input firewall, symbolic
verification, LLM generation, verification layer, role-aware output, audit log)
generalized from a single pipeline to a supervised fleet.

---

## 4. The Fleet

Four narrow agents. One supervisor: the human analyst. Prohibited topology:
no agent may address another agent; all handoffs pass through the
deterministic orchestrator, which validates, ledgers, and routes.

### 4.1 Triage Agent (mandate ceiling: OBSERVE)

- Input: the flagged-entity queue (synthetic detection output).
- Deterministic core: decomposable priority score
  `priority = f(severity_class, spread, velocity, recidivism)` with published
  weights; every score renders as its components, never as a bare number.
- LLM contribution: a one-line "why this case first" rationale constrained to
  cite only the score components. Rationales citing anything else fail
  verification.
- Output: ranked queue with score decomposition.
- Solves: the analyst's first-hour problem (where to look).

### 4.2 Evidence Agent (mandate ceiling: ASSEMBLE)

- Input: one case selected by the analyst.
- Behavior: proposes pivots (shared metadata, temporal correlation, engagement
  graph edges, infrastructure overlap). Each pivot is a **deterministic,
  parameterized query** the analyst approves or rejects; the LLM proposes which
  query to run next, it never composes free SQL (injection surface removed).
- Output: an Evidence Pack: entity graph, timeline, per-record provenance
  (source table, query hash, retrieval timestamp).
- Assembly gate: referential integrity (every edge resolves to two known
  nodes), provenance completeness (no orphan records), schema conformance.
- Solves: manual pivot toil across data sources.

### 4.3 Memo Agent (mandate ceiling: RECOMMEND)

- Input: an accepted Evidence Pack plus the policy corpus: verbatim public
  YouTube policy texts (Spam, Deceptive Practices & Scams; Fake Engagement;
  synthetic-media disclosure requirements) stored as versioned, hashed
  documents.
- Output: a draft enforcement memo structured as a **DSA Article 17 style
  statement of reasons**: facts relied upon, specific policy clause cited by
  document hash and section anchor, proposed action, redress note. This makes
  the memo format regulation-shaped, not ad hoc.
- Symbolic verification: each factual sentence must reference at least one
  evidence-record ID present in the pack; each policy citation must resolve to
  a real anchor in the hashed corpus. Failures return the memo to draft with
  the failing sentences flagged.
- The analyst edits, then signs (ENFORCE path) or rejects. The agent never
  touches the signature path.
- Solves: documentation burden, and produces audit-grade rationales by default.

### 4.4 Prompt-Eval Agent (mandate ceiling: OBSERVE)

- Purpose: policy-as-prompt with a wind tunnel. Classification prompts (used in
  memo drafting and triage rationale checking) live in a versioned registry;
  every version is evaluated before it can be activated.
- Eval harness: labeled synthetic set (stratified across T-01..T-07 plus benign
  controls); reports precision, recall, F1, and confusion by threat class;
  compares against the incumbent version.
- Regression gate: activation is refused if any monitored metric drops beyond
  a declared tolerance. Refusals are ledgered.
- Solves: safe iteration speed on prompts, and the prompt engineering, data
  labeling and performance analysis responsibilities of the target role,
  made rigorous.

  > **Erratum, STEP-08.** This line originally read "the JD's ... bullet". No job
  > description is committed to this repository and none is quoted anywhere in
  > it. The reference was to a generalized responsibility profile, and it is
  > reworded here so the document does not imply a source it does not have. The
  > traceability matrix built on that profile is `docs/POSITIONING.md`, which
  > labels its own left column as representative rather than quoted.
  >
  > **Honest limit carried from STEP-06:** `classify.threat_class` is versioned,
  > evaluated and gated, and **no session consumes its output**. The wind tunnel
  > is real and the aircraft does not yet fly.

---

## 5. Orchestrator

A deterministic, synchronous state machine. Not an agent. No model calls
originate here except on behalf of a mandated agent.

Responsibilities:

1. Session lifecycle: open, bind analyst identity, load mandates, seed ledger.
2. Dispatch: validate proposed action against mandate; execute tool calls via
   an allowlisted tool table; refuse and ledger anything else.
3. Input firewall: all case content (comments, titles, descriptions, channel
   metadata) enters model context wrapped as inert data with explicit
   delimiting and an instruction-stripping pass; content is never concatenated
   into system-level instructions (OWASP LLM01, LLM02 posture).
4. Gates: run the consequence-gate pipeline per Section 3.3.
5. Routing: deliver gated outputs to the analyst UI/CLI; route analyst
   decisions back as ledgered `HUMAN_DECISION` events.
6. Budget enforcement: token and step ceilings per mandate; exhaustion ends
   the agent turn cleanly and ledgers it.

The kill path is trivial by construction: the orchestrator is the only
executor, so halting it halts the fleet. There is no background autonomy to
chase down.

---

## 6. Data Foundation

### 6.1 Synthetic Platform Generator

- Entities: channels, videos, comments, engagement events, account metadata,
  infrastructure hints (shared upload patterns, template reuse markers).
- Planted abuse: parameterized generators for T-01..T-07 with ground-truth
  labels retained in a sealed table the agents cannot scope (mandate
  data-scope exclusion), used only by the measurement layer.
- Realism levers: benign majority (>97% of entities), noisy labels option,
  rater-disagreement simulation, temporal burst shaping.
- Determinism: every dataset build is seeded; the seed and generator version
  are recorded, making every downstream number reproducible.
- Store: DuckDB. Timestamps timezone-aware IST.

### 6.2 Policy Corpus

- Verbatim public policy documents, fetched once, hashed (SHA-256), versioned,
  and anchored (stable section IDs). Citations resolve against anchors, so a
  policy update is an explicit, ledgered corpus event, never a silent drift.

---

## 7. Measurement Layer

Deterministic module, not an agent. Two lenses:

### 7.1 Platform Lens: VVR-Methodology Replication

Implements YouTube's published Violative View Rate methodology against the
synthetic platform: stratified sampling of views, review simulation with
configurable rater accuracy and disagreement, aggregation to a rate with a
**95% confidence interval**, and sensitivity analysis (sample size vs CI
width; rater quality vs bias). Policy-expansion simulation demonstrates the
documented effect of scope changes moving the metric.

### 7.2 Workflow Lens: Does the Workbench Help

The cognitive-support thesis is a measurable claim:

- Analyst-minutes per case, modeled baseline vs assisted (assumptions
  documented, no fabricated user studies; presented as a modeled estimate).
- Evidence completeness: fraction of ground-truth network membership recovered
  per investigation at fixed pivot budget.
- Memo integrity: verification pass rate; count of claims corrected by the
  symbolic verifier before human review.
- Governance activity: gate rejections, mandate-violation attempts caught,
  prompt versions refused by the regression gate. Nonzero values are reported
  prominently as evidence the control layer works.

All measurement outputs carry the dataset seed and code version.

---

## 8. Framework Alignment (2026)

Trust & Safety Sentry claims **alignment**, demonstrated by artifact, never
certification. Each row names the artifact that evidences the row.

### 8.1 Crosswalk: Native Constructs to DeepMind AI Control Roadmap (June 2026)

| Trust & Safety Sentry construct | Roadmap concept | Difference in posture |
|---|---|---|
| Trajectory Ledger | Detection tiers D1-D2 (action evaluation, monitoring and alerting) | Equivalent coverage; ledger is tamper-evident by hash chain |
| Consequence Gates (OBSERVE/ASSEMBLE auto+validated) | D3 escalation of suspicious findings to human review | Gates are synchronous and blocking, not asynchronous review |
| Mandate-constrained dispatch | Access controls; R1-R2 limiting | Preventive, not detective: out-of-mandate actions cannot execute |
| Human-only ENFORCE, type-level | R3 real-time blocking / shutdown of high-risk actions | Stronger: the high-risk action is unexpressible by agents, no race to block |
| Orchestrator as sole executor | Kill-switch infrastructure | Halting one process halts the fleet; no autonomous residue |
| Prohibited agent-to-agent topology | Insider-threat containment | Removes collusion and cascade class A-07 entirely |

### 8.2 NIST AI RMF 1.0 + Generative AI Profile (NIST AI 600-1)

| Function | Realization |
|---|---|
| GOVERN | Mandates as codified accountability; versioned, hashed, ledgered |
| MAP | Dual threat model (Section 2) maintained as a living document |
| MEASURE | Section 7 metrics; prompt-eval harness with per-class confusion |
| MANAGE | Regression gates, consequence gates, ledgered human decisions |
| GAI Profile: confabulation | A-01 control: claim-to-evidence symbolic verification |
| GAI Profile: information integrity | Provenance-complete evidence packs; hashed policy corpus |

### 8.3 ISO/IEC 42001:2023 (AI Management Systems)

Selected controls demonstrated (not certified): documented AI policy
(this document), role clarity (analyst as accountable human), logging and
traceability (ledger), performance evaluation (measurement layer), continual
improvement loop (prompt registry lifecycle).

### 8.4 EU AI Act (Regulation 2024/1689)

Not in scope as a legal obligation (portfolio system, synthetic data), but
designed against its grain: human oversight by construction (Art. 14 pattern),
logging (Art. 12 pattern), transparency of AI-generated recommendation drafts
(memos are labeled as AI-drafted until human-signed). GPAI obligations enter
into force August 2026; the system consumes models via API and documents that
dependency in the model card.

### 8.5 EU Digital Services Act

Memo format modeled on Article 17 statements of reasons: facts, legal or
policy ground with precise citation, proposed measure, redress information.
This makes analyst output regulation-shaped by default, which is exactly the
direction platform T&S documentation has moved.

### 8.6 OWASP Top 10 for LLM Applications (2025)

| Risk | Control |
|---|---|
| LLM01 Prompt Injection | Input firewall; adversarial case content is delimited data; no free-text-to-instruction path; agents cannot compose SQL |
| LLM02 Insecure Output Handling | Output schema contracts; memos inert until human-signed |
| LLM06 Excessive Agency | Mandates; consequence ceilings; human-only ENFORCE |
| LLM08 Vector/Embedding weaknesses | N/A in v1 (no RAG store); noted for roadmap |
| LLM09 Overreliance | Verification-fail surfacing; workbench reports its own error catches |

### 8.7 Provenance (C2PA direction)

Synthetic-media threat classes (T-04, T-05) carry a `provenance_signal` field
in the synthetic schema modeling presence/absence of content credentials,
reflecting the industry's C2PA trajectory without claiming to implement the
spec.

---

## 9. Engineering Standard

Identical bar to delivery-engine v1.4:

- Python 3.12+ (container parity), StrEnum, frozen slots dataclasses, PEP 695
  type aliases, timezone-aware IST timestamps.
- DuckDB event store; JSONL ledger export.
- ruff (lint+format), mypy --strict, pytest with coverage floor declared in
  pyproject; property-based tests (hypothesis) for the ledger hash chain and
  gate logic.
- Reproducibility: seeded data builds; every report stamps seed + git SHA.
- CI: GitHub Actions (lint, type, test, ledger-verify on example sessions);
  Docker image published to GHCR on release, Dockerfile mirrors CI.
- Docs: PROJECT_CHARTER.md, ARCHITECTURE.md (this file), decisions/ STEP log
  (documented build steps, AI-collaboration framing consistent with
  delivery-engine's release notes), model card for LLM usage, honest-limits
  section (what this system deliberately does not do).
- No em-dashes in documentation. Verified metrics only. No inflated claims.

---

## 10. Repository Layout

As built. Two departures from the plan are marked.

```
ts-sentry/
  pyproject.toml  uv.lock
  README.md  QUICKSTART.md  ARCHITECTURE.md  CHANGELOG.md  LICENSE  CITATION.cff
  SECURITY.md  CONTRIBUTING.md  CODE_OF_CONDUCT.md
  src/ts_sentry/
    orchestrator/      # state machine, dispatch, firewall, gates, turns
    governance/        # Mandate, Consequence, ledger, signature, scopes
    agents/            # triage, evidence, memo, prompt_eval (thin: prompts + schemas)
    data/              # synthetic generator, policy corpus, eval set, quality gate
    measurement/       # vvr, sensitivity, recovery, workflow, report, plots
    prompt_registry/   # content-addressed versions + append-only activation log
    cli/               # the eight verbs
  prompts/             # versioned registry (hash-named)
  policies/            # hashed public policy corpus + anchors
  evals/               # committed eval set: items, labels, tolerances
  examples/            # eight curated runs with full artifacts + NOTES
  tests/
  docs/                # USER_GUIDE, POSITIONING, model-card, diagrams,
                       # DECISIONS, data-dictionary, decisions/STEP-NN
  .github/workflows/
```

- **`PROJECT_CHARTER.md` was never written.** Section 9 names it in the docs
  set; STEP-08 D2's documentation list does not, and inventing a charter at
  release time to satisfy a bullet would produce a document with no authority
  behind it. Recorded as an unshipped item rather than manufactured.
- **`prompt_registry/`** is a package this section did not plan. It arrived in
  STEP-06 to hold the versioned registry the design already required.

---

## 11. Build Phases

| Phase | Deliverable | Exit criterion |
|---|---|---|
| 1 | Data foundation: synthetic generator, T-01..T-07, sealed ground truth | Seeded rebuild byte-stable; label leakage test passes |
| 2 | Governance core: Mandate, gates, ledger, verifier | Property tests green; ENFORCE unreachability proven in tests |
| 3 | Orchestrator + Triage agent | First full ledgered session end to end |
| 4 | Evidence agent + Evidence Pack gates | Ground-truth recovery metric reportable |
| 5 | Memo agent + policy corpus + DSA-style memos | Verification pass/fail demonstrably catching planted overclaims |
| 6 | Prompt-eval agent + regression gate | A deliberately worse prompt version is refused, ledgered |
| 7 | Measurement layer (VVR + workflow) | CI-stamped report with 95% CI and sensitivity plots |
| 8 | Examples, docs, release v1.0.0, GHCR image | Fresh-clone quickstart under 10 minutes |

### 11.1 Exit criteria against what actually happened

Added at release. Every row was verified by Saif personally at that phase's
close, following the pattern STEP-01 set where his own pass is the closing step
rather than a green test suite. The full evidence is in each STEP file's Outcome.

| Phase | Exit criterion | Result |
|---|---|---|
| 1 | Seeded rebuild byte-stable; leakage test passes | **Met.** Verified twice, by `fc` diff of manifests and all Parquet files, and by a red-team that added a sealed `DataScope` member and failed 10 tests, only 3 of which were written for leakage |
| 2 | Property tests green; ENFORCE unreachability proven | **Met.** Two independent mechanisms, neither subsuming the other |
| 3 | First full ledgered session end to end | **Met**, and it produced two product findings from reading the output that no test was positioned to notice |
| 4 | Ground-truth recovery metric reportable | **Met.** The metric also surfaced that the strategy plateaued, which became STEP-07's central risk |
| 5 | Verification demonstrably catching planted overclaims | **Met.** 3 gate rejections and 3 verification failures on a real overclaim run, memo held at DRAFT |
| 6 | A deliberately worse prompt version is refused, ledgered | **Met.** Exit 7, four per-class recall breaches plus macro-F1, chain intact |
| 7 | CI-stamped report with 95% CI and sensitivity plots | **Met**, after a phase-close defect Saif found by reading a generated report: the bootstrap cross-check's expected width ratio was computed from the wrong quantity |
| 8 | Fresh-clone quickstart under 10 minutes | See `docs/quickstart-timing.md` |

**One obligation is recorded unmet and stays visible.** STEP-04 named
`t02_chan_000_000` reaching past its first shell as STEP-07's concrete target.
It went from 3 to 4 of 8 members and did not become budget-sensitive, so the
specific claim is not discharged. It is carried into the v1.1 roadmap as the
traversal-enrichment task, with its blocker named. Recording a partial as a pass
is how an obligation quietly stops constraining anything.

---

## 12. Honest Limits (standing section, carried into README)

- Synthetic data only; no claim of real-platform efficacy. One exception, scoped
  and labelled: `examples/08-firewall-real-comments` pushes 1,956 real public
  YouTube comments through the input firewall and states exactly what that does
  and does not show.
- Workflow uplift figures are modeled estimates, not user studies. There is no
  published per-case review-time benchmark to cite, so the model reports a
  break-even rather than "minutes saved".
- Framework rows in Section 8 evidence alignment by artifact; nothing here is
  a certification, audit, or legal compliance claim.
- Detection is simulated upstream; this system does not detect abuse.
- One analyst, one session at a time in v1; concurrency is roadmap.

Added as the build produced them. Each is recorded at the width the artifact
supports rather than the width that would be convenient.

- **Every agent's competence is untested.** Every model output in this
  repository came from a deterministic stub that cannot be persuaded and cannot
  reason. What is demonstrated is the pipeline, not the agent.
- **Two code paths are written and have never been run:** the live model adapter
  and the interactive reviewer. Both are marked and documented.
- **Prompt-injection detection is incomplete by construction.** Four adversarial
  fixtures are asserted as *undetected*. The load-bearing controls are
  structural, not the pattern set.
- **Tail truncation is invisible to chain verification alone**, and an anchor is
  only as independent as its custody. A manifest beside the ledger it describes
  catches accidents, not a determined editor with write access to both.
- **A signature proves integrity, not identity.** Real analyst authentication is
  out of scope.
- **The triage scorer is transparent, not accurate.** Weights are analyst
  judgment, not fitted parameters.
- **Evidence recovery plateaus at the ring core**, 4 of 8 on the named target,
  flat from 5 to 20 pivots. A recorded-unmet obligation with a named blocker.
- **The prompt regression gate detects class collapse, not drift**, bounded by
  the generator's fixed per-class threat volume rather than by the gate's design.
- **The VVR estimand is narrow**, carried by 18 violative views from two threat
  classes; the interval covers sampling error only, and at realistic rater
  accuracy the rater-induced bias exceeds it. Correlated rater error is not
  modelled at all.
- **`classify.threat_class` is gated but consumed by no session.**
- **PNG figures are byte-identical within one environment only.** The CSV and
  JSON curve data are byte-stable across runs and machines.
