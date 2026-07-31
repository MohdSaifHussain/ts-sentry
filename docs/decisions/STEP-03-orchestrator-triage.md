# STEP-03: Orchestrator + Triage Agent

**Project:** Trust & Safety Sentry | **Phase:** 3 of 8 | **Date:** 31 July 2026 (IST)
**Status:** Closed. D1-D6 implemented; phase-close verification run personally
by Saif; two product findings from his review of the ranked queue addressed
before close.
**Depends on:** STEP-01, STEP-02

## 1. Objective
First end-to-end ledgered session: analyst opens a session, triage agent ranks
the queue under an OBSERVE mandate, rationale verification passes, session
closes with an intact chain.

## 2. Deliverables

| ID | Deliverable | Governing standard(s) |
|---|---|---|
| D1 | `orchestrator.core`: synchronous state machine (SessionState StrEnum; explicit transitions table) | Deterministic FSM design; single-executor kill path (ARCHITECTURE 5) |
| D2 | `orchestrator.firewall`: input firewall for case content | OWASP LLM01 prompt-injection mitigation: delimited inert data blocks, instruction-stripping pass, no case text in system role |
| D3 | `orchestrator.dispatch`: mandate check -> allowlisted tool table -> execute -> schema check -> gate -> ledger | Least privilege; OWASP LLM06 |
| D4 | Model adapter: single boundary module for LLM calls (provider-agnostic, retries with jitter, token accounting against mandate budget) | 12-factor config (env-only credentials, never in repo); OWASP LLM02 output handling |
| D5 | `agents.triage`: deterministic scorer + LLM rationale | Score decomposition published; rationale constrained to score components (verifier-checked) |
| D6 | `cli: ts-sentry run-session --agent triage --seed-dataset PATH` | Session artifacts: ledger JSONL, ranked queue JSON, manifest |

## 3. Requirements
- 3.1 Priority score `f(severity_class, spread, velocity, recidivism)`:
  weights in a versioned config; property test: monotonicity in each component
  holding others fixed; every output row carries the component vector.
- 3.2 Firewall tests: fixture corpus of injection attempts embedded in comment
  text (instruction phrasing, tool-call mimicry, delimiter escapes); assert
  none alter agent behavior contract (rationale still cites only components)
  and all are preserved verbatim as data.
- 3.3 Token/step budget exhaustion ends turn cleanly with `SESSION_CLOSE`
  reason code; partial results are delivered, ledgered.
- 3.4 LLM offline mode: deterministic stub adapter for CI (no network in CI);
  live adapter behind env flag. All tests pass fully offline.
- 3.5 Rationale verifier reuse: STEP-02 verifier with evidence-ids = score
  component ids.

## 4. Out of Scope
- Evidence pivots, memos, prompt registry evaluation.

## 5. Exit Checklist
- [x] Full session on seed-42 dataset produces intact ledger (verify-ledger 0)
      - Verified personally by Saif against the fixed build: 23 cases ranked,
        23 rationales accepted, closed `completed`, intact chain, head
        `8:75474aad...`. See "Phase close, verified".
- [x] Injection fixture corpus: 0 behavioral deviations
      - True and narrow. See "What the 3.2 result does and does not say".
- [x] Ranked queue demonstrably discriminates (added at phase close)
      - Saif's finding 1. Severity spans 0.4-0.8; spread nonzero on 14 of 23
        cases, recidivism on 10, velocity on 19.
- [x] Rationales cite the differentiating component (added at phase close)
      - Saif's finding 2. Cited components went from severity_class 25 of 25
        to velocity 16, spread 4, severity 3.
- [x] Monotonicity property green; component vectors present on every row
      - Property restated at floating-point precision after hypothesis found a
        counter-example; see below.
- [x] CI fully offline green; live-mode smoke documented
      - 572 tests, no network. Verified by running the whole suite with
        `socket.connect`, `create_connection` and `connect_ex` patched to
        raise: zero attempts. The live-mode smoke is documented as a procedure
        and **not run**; see Honest limits.
- [x] mypy --strict, ruff, coverage floor green; CHANGELOG updated
      - 99% line coverage against a 90 floor.

## 6. Outcome

Shipped: D1-D6, in `src/ts_sentry/orchestrator/`, `src/ts_sentry/agents/`, and
the `run-session` subcommand. The D1/D2 review stop was observed; Saif reviewed
the firewall on source and supplied an adversarial fixture set, which produced
one real finding. 572 tests green, mypy `--strict` and ruff clean, 99% line
coverage against a 90 floor.

### Phase close, verified

Saif ran the phase-close verification personally, continuing the pattern from
STEP-01 and STEP-02 where his own pass is the closing step rather than a green
suite.

| Scenario | Expected | Observed |
|---|---|---|
| `build-dataset --seed 42 --scale 1` | succeeds | succeeded |
| `run-session --agent triage` | exit 0, intact chain | exit 0, 23 cases, 23 rationales, intact |
| `verify-ledger --expect-head-from` the manifest | exit 0, head matches | exit 0, head matches |
| Truncated copy, bare | exit 0 (the limitation) | exit 0 |
| Truncated copy, `--expect-head-from` | exit 6, both heads printed | exit 6 |

Session `session-2486b1224b54`, head
`8:75474aad1d0e23480f518b9cccff5456c489c8c505df3ce196a50ace3140f286`.

Run twice in total. The first pass was against the pre-fix build and produced
the two product findings below; this record is the re-run against the fixed
build, which is the one that stands.

The fourth row is the one worth keeping in view, as it was in STEP-02: a
*passing* result that confirms a real limitation. Chain verification alone
accepted a truncated export, and only the stored anchor caught it.

**The session id and chain head are not reproducible across rebuilds, and that
is a narrower guarantee than the code claimed.** Saif's head and session id
differ from every locally observed pair, which prompted a check rather than an
assumption. Measured: two `--seed 42 --scale 1` builds produce byte-identical
Parquet exports for all six tables, which is exactly what STEP-01 verified, but
*different* `build.duckdb` files. The store's internal layout is not
byte-stable even when its contents are.

`dataset_digest` is the SHA-256 of `build.duckdb`, and `session_id` is derived
from it, so a rebuild changes both, and the chain head changes with the
timestamps regardless. `derive_session_id`'s docstring claims the derivation
"makes two runs of the same inputs comparable"; that holds for two runs against
one build directory and not for two rebuilds, so the claim is wider than the
behavior.

Deliberately not fixed in this phase. Deriving the digest from the Parquet
exports or the build manifest's `table_hashes`, both of which are byte-stable,
would make session ids reproducible across rebuilds, but it would change every
manifest and invalidate the verification Saif has just completed twice.
Recorded as a gap and carried to STEP-04 rather than churned now.

### Two product findings from Saif's human review of `ranked_queue.json`

Both were found by reading the actual output, not by any test. That is the
finding behind the findings: **the suite was green, every assertion was true,
and the product was not useful.** A ranking where every case scores the same
and every rationale cites the same component passes a correctness test and
fails an analyst. Nothing in this phase's tests was positioned to notice,
because they all checked that the machinery was right rather than that the
result said anything.

**Finding 1: the queue did not discriminate.** Every case scored severity 0.7
with spread and recidivism at zero, so the ranking collapsed to a velocity
sort. Root cause, measured rather than guessed: undisclosed synthetic media
was a *flag trigger*, and it holds for 64 of 66 channels on the seed-42 build.
A property held by 97% of the population cannot say which case to open first,
and flagging on it filled all 25 slots with benign channels before any real
ring was reached.

Saif's instruction was to add planted variance to the stub. **I did not do
that, and the reason is worth recording.** Planting variance into the detection
stub would make the queue no longer derived from the data, which contradicts
the module's central honest claim and would have made every downstream number
a fiction. Measuring the dataset first showed the variance was already there
and being suppressed, so the same outcome was reachable honestly. Three
changes, each grounded in a measurement:

- Undisclosed synthetic media stops being a flag trigger and drops to the
  lowest severity weight. It still aggravates a case that something else
  flagged.
- **Inbound signals.** Measured: every holder of a link-domain-reuse or
  shared-device signal owns *no channel* and comments on eleven. A comment-spam
  ring operates through commenting accounts, so a channel-centric queue was
  structurally blind to exactly the rings that matter most. Signals carried by
  accounts commenting on a channel now count, gated on at least two distinct
  accounts sharing one value: one spammer is one spammer, several sharing a
  device fingerprint is coordination.
- **Recidivism had no basis and now has one.** Measured: every subject carries
  exactly one hint and every account owns exactly one channel, so counting a
  subject's own observation days was structurally zero forever. Redefined as
  *pattern persistence*, the days on which the entity's shared signal values
  were seen anywhere. A ring whose device fingerprint keeps reappearing is a
  ring that came back, which is what recidivism means for an infrastructure
  signal.

Result on the same seed-42 build: severity now spans 0.4 to 0.8 across four
levels, spread is nonzero on 14 of 23 cases, recidivism on 10, velocity on 19,
and priorities range 0.482 down to 0.304. The top case by severity ranks third
overall because its velocity is low, which is the ranking discriminating on
more than one component rather than sorting on one.

**Finding 2: rationales were uninformative.** Every rationale cited
`severity_class`, because it was the largest component on every row and the
citation builder picked the largest. It verified perfectly and explained
nothing: at equal severity, the cited component was the one thing that could
not account for the ordering.

Fixed in the rationale builder, not the verifier, which was correct to accept
any resolvable citation. `discriminating_component` now picks the component
whose value deviates most from the same component on the rank-neighbouring
rows, weighted by what that component is worth in the priority, with ordered
fallbacks (queue-wide widest, then largest weighted) so the function is total
and deterministic. The citation menu names it per case while still listing the
full legal set, so this steers the model rather than constraining it. Cited
components across the seed-42 queue went from `severity_class` 25 of 25 to
velocity 16, spread 4, severity 3.

Both fixes are pinned by `tests/test_detection_discrimination.py`, built on
purpose-made fixtures rather than a snapshot of one dataset, so they state the
rules rather than the numbers of a particular build.

### The four carried obligations, discharged

1. **Signature import-graph test.** In `tests/test_import_graph.py`, worded per
   the sealed two-consumer model and enforced over the *transitive* first-party
   closure rather than direct imports. It failed on its first run, and the
   failure was a design defect rather than a false positive:
   `agents.triage.rationale` reached `governance.signature` through
   `verifier -> gates`. The rule could have been widened; it was not, because
   an agent holding its own verifier is an agent nobody is verifying.
   Verification moved to `orchestrator/rationale_check.py` and the agent kept
   only the citation format it writes to. The agent's output schema likewise
   carries accepted rationale *text* rather than the verifier's verdict type.
2. **No-orphan `ToolId`.** Entry per ID now, handler per ID by its own phase,
   per Saif's chosen reading. `RefusalCode.TOOL_HANDLER_NOT_IN_BUILD` keeps a
   build limitation from ever being counted as a mandate violation, and it
   ledgers `GATE_REJECTION` rather than `MANDATE_VIOLATION_ATTEMPT`. The
   countdown binds at full strength: the pending set shrank from four to three
   when the triage handler landed, and bumping `IMPLEMENTATION_PHASE` without
   landing the handler that phase owes reddens the suite.
3. **Session-manifest head anchor.** `orchestrator/manifest.py` records the
   expected head after `SESSION_CLOSE` is appended, and
   `verify-ledger --expect-head-from` reads it back. Demonstrated on a real
   artifact: a truncated export verifies clean (exit 0) and the anchor refuses
   it (exit 6).
4. **Refusal ledgering.** `validate()` is unchanged, still pure and total with
   no I/O. `orchestrator/dispatch.py` is the caller that ledgers, through a
   single `_refuse` helper so the property is checkable by reading one
   function. Scope refusals reuse `gates.guard_scope_request` rather than a
   reimplementation.

### The truncation test, rewritten precisely

`test_truncating_the_tail_is_undetectable` was rewritten as its own docstring
demanded, but the framing needs stating accurately: landing the anchor did
**not** make it fail. `verify_chain` is untouched and still cannot see a
truncated tail, so its assertion stays true. What the anchor falsified was the
docstring's claim *about the system*. It is now
`test_tail_truncation_is_invisible_to_chain_verification_alone`, narrowed to a
statement about that function, with the companion in
`tests/test_session_manifest.py` asserting what the anchor catches.

The system's own limit moved rather than disappeared, and is asserted: an
anchor is only as independent as its custody. A test shows a rewritten manifest
agreeing with a truncated ledger. Co-located files catch accidents and partial
tampering; the anchor becomes a real control when a copy is held where the
ledger's writer cannot reach it.

### Findings from Saif's adversarial fixture set (D2 review stop)

Seven fixtures, placed by measured result with no pattern tuned to force a
pass. Two DETECTED: case/spacing evasion, and non-breaking-space separators
(Python's `re` is Unicode-aware, so `\s` matches U+00A0). Five UNDETECTED and
asserted as such: zero-width space inside a keyword, `ftp://` and UNC-path
exfiltration, redaction-marker forgery, and CRLF record forgery.

**Redaction markers were forgeable, and that was a real finding.** Case content
containing the literal marker string produced a model-facing block in which an
attacker-planted marker was byte identical to one the firewall wrote. Two
things were wrong at once: marker text is orchestrator-authored text living
inside the data channel, so the fence's own failure mode reappeared one level
down; and it read in the attacker's favour, because a payload wrapped in a fake
marker claims to have already been neutralized. Fixed by binding markers to the
block nonce, which closes it by the same preimage argument the fence rests on.

### Defects found by tests rather than by inspection

- **U+2028 fence escape (D2).** Found by a hypothesis property. `json.dumps`
  escapes newline and carriage return but not U+2028, U+2029, NEL, VT, FF, FS,
  GS or RS, while `str.splitlines` breaks on all of them, so a comment carrying
  U+2028 plus a forged JSON object appeared as *two records inside one fenced
  block*. That is case content writing a record into the analyst's evidence.
  Fixed by escaping those characters.
- **DuckDB timestamp rendering, again (D5).** Reading `TIMESTAMPTZ` into Python
  needs `pytz`, which this project does not have, and casting to text would
  have been quieter and worse: DuckDB renders in the *reader's* session time
  zone. The recidivism component counts distinct observation days, so two
  machines would have computed different priorities from one dataset and
  neither would have looked wrong. Timestamps are now selected as
  `epoch_ms(...)`, verified identical under three reader time zones. Same class
  of defect STEP-02 D3 avoided in the ledger, found in a new place.
- **Monotonicity is non-strict in floating point (D5).** Hypothesis immediately
  found that raising `severity_class` from 0.9999999999999999 to 1.0 leaves the
  priority bit-identical, because the weighted difference falls below the sum's
  resolution. Not fixable in the scorer and not a defect in it, so 3.1's
  property is now two parts: priority is never lower when a component rises
  (always), and strictly higher once the change survives the weighting.
- **`PREV_HASH_MISMATCH` had no test (D1).** Every other tampering shape fires
  an earlier check, so reaching it takes rewriting a `prev_hash` specifically.
  Pre-existing gap, now covered.

### What the 3.2 result does and does not say

"0 behavioral deviations" is true and narrow. The corpus proves that **no case
content can change what a rationale may cite**, because the resolvable id set
comes from the scored queue and the verifier checks against it. It is **not**
evidence that a model resists injection: the stub is deterministic and cannot
be persuaded, so no assertion in this phase is about model behavior. The claim
is about the pipeline, that output is checked rather than trusted, and it holds
regardless of which model sits behind the adapter.

### Recorded readings and deviations

1. **The flagged-entity queue (`orchestrator/detection_stub.py`).**
   ARCHITECTURE 4.1 gives triage a queue STEP-01 never built, and Honest Limits
   says this system does not detect abuse. Resolved per Saif: a deterministic
   stub standing in for the upstream enterprise detector. **Severity is a
   heuristic stand-in signal, not ground truth**, with no sealed influence
   direct or derived, and a test asserts that against the SQL rather than the
   docstring. It has no measured precision or recall and must never be reported
   as detection performance.
2. **The model call sits after the tool, not inside it.** Handlers are
   deterministic and make no model call, so a ranking is reproducible from the
   dataset alone; rationales are a separate, separately verified, separately
   ledgered step. A tool that could prompt would be an agent wearing an
   allowlist entry.
3. **Two event-type readings**, in the style STEP-02 used for its ENFORCE
   reading: a declared tool with no handler ledgers `GATE_REJECTION` alone
   (nothing was violated), and a handler that raised ledgers `TOOL_RESULT`
   carrying the failure with no gate run (a gate over a nonexistent artifact
   would be manufacturing a verdict).
4. **`ScopeGuardResult` and `GateOutcome` gained payload fields**, bridged by
   `Session.attach_event`. The chain stores only digests, so an artifact that
   cannot show the body behind a `GATE_REJECTION` cannot evidence it. The
   bridge verifies the body against the digest before filing it.
5. **Module moves, no behavior change.** `ChainHead`/`chain_head` moved to
   `governance.ledger` and `git_sha`/`sha256_file` to `ts_sentry.provenance`,
   because two consumers now need the identical spelling and `orchestrator`
   must not import `cli`. `orchestrator.toolspec` was split from
   `orchestrator.tools` to break a cycle between the table and its handlers.

### Cost and credentials

The system builds, tests, and runs a full session with **zero credentials and
zero cost**. `TS_SENTRY_LLM_MODE` gates the live path and is absent by default;
anything but exactly `live` resolves to the stub. `LiveAdapter` refuses to
construct without both that variable and `ANTHROPIC_API_KEY`, whose *value* is
never read: only its presence is checked, and the vendor client reads it
itself. That package is an optional extra and is not installed.
`tests/conftest.py` strips all three variables session-wide, so the guarantee
does not depend on anyone's shell. Verified by running the suite with the live
variables exported, and again with the socket layer patched to raise.

### Honest limits

- **`LiveAdapter.complete` is untested and unrun.** It is marked `no-cover`;
  covering it means a network call in CI or a mock, and a mock would assert
  only that the code matches the shape its author imagined for the SDK. The
  live-mode smoke run is documented as a procedure and **not performed**, so
  the live path was written against the official API reference and never
  executed. That is a real gap, stated rather than implied.
- **Prompt-pattern injection detection is incomplete by construction.** Four
  fixtures are asserted as undetected to keep that honest. The load-bearing
  controls are structural.
- **The triage scorer is transparent, not accurate.** Weights are analyst
  judgment, not fitted parameters; there is no measured outcome on synthetic
  data to fit against.
- **The 3.12 gap persists.** Local Python is 3.14 and CI pins 3.12, so every
  green result here is a 3.14 result, as in STEP-02.
- **Session ids are not reproducible across dataset rebuilds**, because
  `build.duckdb` is not byte-stable even though the Parquet exports are. See
  the note under "Phase close, verified". Carried to STEP-04.
