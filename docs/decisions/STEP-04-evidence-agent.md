# STEP-04: Evidence Agent + Evidence Packs

**Project:** Trust & Safety Sentry | **Phase:** 4 of 8 | **Date:** 31 July 2026 (IST)
**Status:** Closed. D1-D6 implemented; SQL template review passed by Saif with
zero required changes. Awaiting his phase-close verification.
**Depends on:** STEP-03

## 1. Objective
Investigation support under an ASSEMBLE mandate: agent proposes pivots from a
fixed vocabulary, analyst approves, orchestrator executes parameterized
queries, Evidence Pack passes assembly gates. Exit criterion: ground-truth
network recovery metric reportable at fixed pivot budget.

## 2. Deliverables

| ID | Deliverable | Governing standard(s) |
|---|---|---|
| D1 | Pivot vocabulary: PivotKind StrEnum (SHARED_METADATA, TEMPORAL_CORRELATION, ENGAGEMENT_EDGE, INFRA_OVERLAP, ACCOUNT_LINK) each bound to one parameterized DuckDB query template | Injection-surface elimination: LLM never composes SQL; parameterized queries only (OWASP LLM01/SQLi discipline) |
| D2 | `agents.evidence`: pivot proposer (LLM ranks next pivot with reason) + human approve/reject loop | Human-in-command pattern (EU AI Act Art. 14 style); every hop is a ledgered HUMAN_DECISION |
| D3 | EvidencePack model: nodes, edges, timeline, per-record provenance (source table, query template id, param hash, retrieval ts IST) | Provenance completeness (NIST AI 600-1 information integrity); W3C PROV-inspired fields, documented mapping |
| D4 | Assembly gate: referential integrity, provenance completeness, schema conformance | ARCHITECTURE 3.3 ASSEMBLE gate |
| D5 | Recovery metric: fraction of ground-truth network membership recovered vs pivot budget, computed by measurement-side code with sealed-label access | Sealed-scope boundary respected: metric runs outside agent mandates |
| D6 | Graph export: GraphML + JSON for report rendering | Interoperable graph format |

## 3. Requirements
- 3.1 Pivot templates reviewed line-by-line; no string interpolation of user
  or model text into SQL; params typed and bounds-checked.
- 3.2 Proposal contract: agent output = (pivot_kind, params, one-line reason
  citing existing pack record ids); verifier checks the citation.
- 3.3 Rejection handling: analyst reject is terminal for that proposal;
  agent may propose an alternative; max proposals per turn bounded by mandate.
- 3.4 hypothesis properties: pack integrity invariants hold after arbitrary
  approved-pivot sequences; provenance is total (no orphan records).
- 3.5 Benchmark fixture: seed-42 networks; report recovery @ 5/10/20 pivots
  per threat class; numbers land in the measurement report (STEP-07 format).

## 4. Out of Scope
- Free-form SQL exploration (roadmap, behind its own gate); memos.

## 5. Exit Checklist
- [x] Zero dynamic SQL paths (grep + review note in decisions log)
      - Saif read all five templates line by line and passed them with zero
        required changes. The AST sweep and its honest reading are below.
- [x] Pack invariants property-tested green
      - Two hypothesis properties over arbitrary approved-pivot sequences.
- [x] Recovery @ budget table generated for seed-42, per threat class
      - All seven classes. Table below, including what it does not show.
- [x] All hops present as HUMAN_DECISION events in ledger
      - Asserted against the exported JSONL of a real session, not a return
        value, and each carries `reviewer_kind`.
- [x] mypy --strict, ruff, coverage floor green; CHANGELOG updated
      - 780 tests, 98% line coverage against a 90 floor.

## 6. Outcome

Shipped: D1-D6, in `src/ts_sentry/orchestrator/` (pivots, pivot_tool,
proposal_check, review, evidence_turn, pack_gate, pack_export),
`src/ts_sentry/agents/evidence/`, `src/ts_sentry/measurement/`, and the
`run-session --agent evidence` verb. 780 tests green, mypy `--strict` and ruff
clean, 98% line coverage against a 90 floor. Fully offline.

### Deliverable order, and the review stop

The instruction to stop "after D1 and D2 (the pivot vocabulary with its query
templates, and the EvidencePack model with provenance)" named D1 and D3, and
also said the review must happen "before the agent that proposes them exists",
which is D2. Raised rather than guessed. Saif's decision: vocabulary and pack
first, then halt. Implemented D1, D3, review stop, then D2, D4, D5, D6.

D4 landed before D2 within the second half, because the ASSEMBLE gate is a
dependency of a working hop rather than a consumer of one: dispatch runs the
consequence gate over whatever a tool returns, so the pivot handler cannot be
exercised end to end until the checker exists.

### The review stop, and the one change taken after it

Saif read every template line by line and passed them with **zero required
changes**, confirming by hand what the tests assert: table names from
`resolve_table`, every runtime value bound, placeholder and binding counts
agreeing, and the column-selection-by-value construction correct in all three
places it appears.

He left one optional note: positional `ORDER BY` is safe as reviewed literals
but brittle if a SELECT list is later edited, mitigated by the `template_sha256`
bump on any edit. It was taken up, because the brittleness has a half that
mitigation does not cover. Reordering a projection would change what
`ORDER BY 2` sorts by *and* leave `PivotTemplate.columns` mislabelling every
field of every evidence record built from that query, with the row count
unchanged and nothing failing. Every projection is now aliased, ordering is by
name, and a test compares `columns` against the names DuckDB reports for the
actual result set, so the declared contract is checked against the query rather
than maintained beside it.

### Zero dynamic SQL, at its true width

The checklist asks for a grep. A line-based grep is close to useless here,
because a template's SQL keyword sits on the line after the opening quote, so
the sweep was done over the AST instead. Every f-string in `src/` carrying a SQL
keyword, with what it interpolates:

| Module | SQL f-strings | Interpolates | Agent-reachable |
|---|---|---|---|
| `orchestrator/pivots.py` | 5 | the six `resolve_table(DataScope.X)` constants | no |
| `orchestrator/detection_stub.py` | 8 | the same construction (STEP-03) | no |
| `data/store.py` | 1 | a module constant and a fixed literal from the call site (STEP-01) | no |
| `cli/main.py` | 1 | a path from `resolve_export_path` in the build-time leakage check (STEP-01) | no |

The phase's own claim holds exactly: nothing in the pivot path interpolates
anything but an allowlist-resolved table name, and no value an agent or a model
supplies reaches SQL text. The three pre-existing sites are named rather than
excluded, because "zero dynamic SQL" read as a blanket claim would be wider
than the sweep supports.

The property is asserted structurally as well as grepped. An AST test proves
the only thing a template f-string can interpolate is a name bound to
`resolve_table`, and a companion proves no SQL is built inside a function,
which is the hole that scoping the first test to module level would otherwise
open. Both were red-teamed against sabotaged copies: interpolating a parameter,
interpolating an arbitrary expression, and moving query construction into a
function each redden the suite, and the third is caught only by the companion,
so neither test subsumes the other.

Two rules were added beyond what 3.1 requires, both test-enforced: no template
selects a free-text column, and entity-id parameters must resolve to a node
already in the pack.

### Recovery @ budget, seed 42

```
threat class                    cases  @5           @10          @20
t01_comment_spam_ring           1      0.25/1.00    0.25/1.00    0.25/1.00
t02_fake_engagement_network     1      0.38/0.38    0.38/0.38    0.38/0.38
t03_off_platform_diversion      1      0.25/1.00    0.25/1.00    0.25/1.00
t04_undisclosed_synthetic_media 1      0.25/0.25    0.25/0.25    0.25/0.25
t05_ai_persona_authority        1      0.25/0.25    0.25/0.25    0.25/0.25
t06_slop_farm                   1      0.06/0.06    0.06/0.06    0.06/0.06
t07_coordinated_influence_op    1      0.27/0.38    0.27/0.38    0.27/0.38

cells: mean recovery of the ring / of what a pack can structurally hold
cases whose subject carried no planted ring: 0
```

Read the second number for agent performance. The first is bounded by a
structural ceiling the agent cannot affect: a comment enters a pack as a
timeline event rather than as a node, so a ring that is mostly comments has a
recovery ceiling well below 1.0 however well the investigation goes. T-01 and
T-03 recover **everything reachable** while showing 0.25 of the ring, and
reporting only the first number would have read as a failure.

Three things this table does not say, stated because the number is otherwise
easy to over-read:

- **The budget axis measures nothing here.** The three columns are identical
  because the offline stub reaches the accounts, asks the two questions it
  knows, and has then finished; every later hop re-runs a query whose answer is
  already in the pack. That is a fact about the stub, not about the pivot
  vocabulary. It is asserted as a passing test, in the shape STEP-02 used for
  tail truncation, so a better strategy makes the test fail and forces this
  paragraph to be rewritten.
- **One case per threat class.** Seven investigations is a demonstration that
  the metric is reportable, which is the exit criterion, and not a sample from
  which anything is inferred.
- **Precision is not measured.** A pack that dragged in the whole platform
  would score 1.0. The budget is the intended counterweight, and precision
  against ground truth is STEP-07's.

T-01 and T-03 are seeded on an account rather than a channel, because those
rings publish nothing and operate through commenting accounts. A channel-only
benchmark dropped them silently, which is the STEP-03 finding about the queue
being blind to exactly the rings that matter most, appearing again.

### Defects found by running it, not by inspection

1. **A booked step could not be used.** `begin_turn` books a step, then
   `call_model` re-checked the step budget, saw the ceiling it had just
   reached, and refused work the session had already authorized. Every
   mandate's last step was unusable and `max_steps` quietly meant one fewer
   than it said. One turn per session hid it for the whole of STEP-03. Fixed
   with `require_step`; the ceiling is unchanged and still enforced at
   `begin_turn`.
2. **Two sessions shared an id.** The first evidence session run through the
   CLI came back carrying the triage session's id, because analyst plus dataset
   identified a session only while there was one kind of session. Session ids
   appear in the `OrchestratorToken` and in every manifest, so this is an
   ambiguous audit trail rather than a cosmetic clash. `derive_session_id` now
   takes discriminators.
3. **The stub's opening move found nothing, forever.** Infrastructure hints
   attach to accounts and an investigation seeds on a channel, so
   `INFRA_OVERLAP` on the subject returned zero rows on every hop while every
   test about the loop passed. The STEP-03 finding in a new place: the
   machinery is right and the product finds nothing. The stub now reads the
   pack out of the prompt and reaches the accounts first.
4. **The stub then found nothing *new*.** Its second version pivoted on one
   account every hop, which made recovery identical at 5, 10 and 20 and the
   budget axis meaningless. Fixed to walk the accounts it has found; the
   residual saturation is recorded above rather than engineered away.
5. **Two of five row mappings had never executed.** The stub proposes three
   pivots, so `TEMPORAL_CORRELATION` and `ENGAGEMENT_EDGE` had mappings whose
   column order was a guess. `tests/test_pivot_tool.py` now runs all five
   against a real build and gates each result.
6. **The sealed-name check flagged eight innocent modules.** A substring grep
   cannot tell a query from a docstring, and `scopes.py` documenting "no member
   resolves to `sealed._labels`" is the guarantee rather than a breach of it.
   Rewritten over the AST. Red-teamed against six synthetic modules; the one
   gap it cannot close, a name assembled from computed pieces, is stated in the
   test rather than left implied.

### Readings and deviations, recorded

1. **`run-session --agent evidence` is a CLI surface STEP-04 does not
   enumerate.** Added per Saif's decision because the phase's own exit
   checklist requires a ledgered session to inspect, and nothing runnable by
   hand would otherwise produce one.
2. **The handler returns the grown pack**, and the pack reaches it through
   `ToolResources` rather than `params`. Dispatch gates whatever a tool
   returns, so returning rows would have meant the gate validated a fragment.
   Resources rather than params because an agent that could supply the pack
   could supply one containing entities it invented.
3. **`ToolResources.pack` is typed `object`.** `toolspec` defines what a tool
   is for every tool, and naming one agent's artifact there would make the
   general contract depend on a particular agent. Paid for with an `isinstance`
   check at the handler's own boundary, which is a fail-closed refusal worth
   having anyway.
4. **Two ASSEMBLE-gate checks are unreachable through the constructor**, and
   kept, following STEP-02's handling of its two unreachable branches. Tested
   directly against packs built by bypassing `__post_init__`.
5. **`EdgeRelation.PUBLISHED_ON` was added to D3 after the review stop**, when
   the row mappings needed a channel-to-video relation. An enum member, not a
   change to any reviewed SQL.
6. **`EVIDENCE_MANDATE.max_steps` is 20**, because 3.5 reports recovery at 20
   pivots and a reported budget the mandate forbids is not a measurement.

### The carried STEP-03 gap, closed

`dataset_digest` now derives from the build manifest's `table_hashes` under a
`ts-sentry/dataset-digest/v2` domain separator. Demonstrated on real artifacts
rather than asserted: two `--seed 42 --scale 1` builds in separate directories
produce **different** `build.duckdb` files and the **same** dataset digest, and
therefore the same session id. Both directions are pinned by tests that build
two real datasets, including the one asserting the store is still not
byte-stable, so if DuckDB ever changes that the claim gets rewritten rather
than silently outliving its reason.

**Pre-fix session ids are not comparable with post-fix ones.** The `v2`
separator makes that structural rather than a note: a digest from before this
change and one from after cannot collide for the same build. Saif's STEP-03
phase-close values (`session-2486b1224b54`, head `8:75474aad...`) belong to the
previous era and should not be compared with anything produced now.

`derive_session_id`'s docstring is rewritten to its now-true width: the claim
STEP-03 narrowed to "one build directory only" is earned rather than restated,
and what the id does *not* survive (a change of content) is stated too.

A build with no `build_manifest.json` is now an input error. There is
deliberately no fallback to hashing the store, because a silent fallback would
restore the defect in the case where it is hardest to notice. The STEP-03 CLI
test fixture was building a dataset without exports or a manifest, which is a
build `build-dataset` cannot produce; it now writes both.

### The `ToolId` countdown

`IMPLEMENTATION_PHASE` is 4, `RUN_PARAMETERIZED_PIVOT` has its handler, and the
pending set shrank three to two (`RESOLVE_POLICY_CITATION`, `RUN_PROMPT_EVAL`).
The "landed the handler it owed" test is now written against
`IMPLEMENTATION_PHASE` rather than a named tool, so it does not need rewriting
each phase and cannot be quietly retargeted at whichever tool happens to be
executable.

### Honest limits

- **The interactive reviewer is written and unrun.** It is marked `no-cover`
  and its manual procedure is documented in `orchestrator/review.py`. Covering
  it means mocking a terminal, and a mock would assert only that the code
  matches the shape its author imagined for a person. Same treatment as
  `LiveAdapter.complete`, which also remains unrun.
- **`reviewer_kind` proves which mechanism decided, not who.** It records that
  a human was present, not which human. Real analyst authentication is out of
  scope, as it has been since STEP-02's signature note.
- **Recovery is measured against planted ground truth on synthetic data.** It
  is not a claim about real investigations, and the sample is one case per
  threat class.
- **The recovery budget axis is uninformative on this build**, for the reason
  recorded above.
- **The agent's competence is untested.** Every number here was produced by a
  deterministic stub that cannot be persuaded and cannot reason. What is tested
  is the pipeline: that a proposal is checked rather than trusted, that nothing
  runs without an analyst approving it, and that every hop is traceable. None
  of it is evidence about how a model would investigate.
- **The 3.12 gap persists.** Local Python is 3.14 and CI pins 3.12, so every
  green result here is a 3.14 result, as in STEP-02 and STEP-03.
