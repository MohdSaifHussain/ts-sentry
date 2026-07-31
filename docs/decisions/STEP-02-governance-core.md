# STEP-02: Governance Core

**Project:** Trust & Safety Sentry | **Phase:** 2 of 8 | **Date:** 31 July 2026 (IST)
**Status:** Specified, not started
**Depends on:** STEP-01 (DataScope enum, sealed schema)

## 1. Objective
Implement Mandate, Consequence Gates, Trajectory Ledger, and the symbolic
verifier. Exit criteria: ENFORCE unreachability proven at type level and in
tests; ledger hash chain property-tested; a tampered ledger is detected.

## 2. Deliverables

| ID | Deliverable | Governing standard(s) |
|---|---|---|
| D1 | `governance.mandate`: Mandate frozen dataclass, AgentId/ToolId/DataScope/Consequence StrEnums | PEP 695, frozen slots; least privilege (allowlist semantics) |
| D2 | ENFORCE construction restricted to `HumanSignature` factory requiring analyst_id + decision + SHA-256 signature hash | Type-level safety invariant; NIST AI RMF MANAGE; EU AI Act Art. 14 human-oversight pattern |
| D3 | `governance.ledger`: append-only hash-chained store (DuckDB + JSONL export) | Tamper-evident logging practice (hash chain per RFC 6234 SHA-256); EU AI Act Art. 12 logging pattern; ISO/IEC 42001 traceability |
| D4 | `governance.gates`: OBSERVE / ASSEMBLE / RECOMMEND gate pipeline | ARCHITECTURE 3.3; OWASP LLM06 excessive-agency control |
| D5 | `governance.verifier`: claim-to-evidence symbolic verifier | NIST AI 600-1 confabulation control; every claim sentence must resolve >=1 evidence-record ID |
| D6 | `cli: ts-sentry verify-ledger PATH` | Exit codes: 0 intact, 4 broken chain (first broken seq printed) |

## 3. Requirements
- 3.1 Mandate validation is pure and total: `validate(action, mandate) -> Verdict`
  with exhaustive match on Consequence (mypy strict exhaustiveness).
- 3.2 Ledger entry fields per ARCHITECTURE 3.2; `entry_hash` recomputation
  round-trips; hypothesis properties: (a) chain valid after N random appends,
  (b) any single-field mutation breaks verification at or before that entry,
  (c) append is O(1) lookups (no full-chain rescan on write).
- 3.3 Gate behavior: ASSEMBLE runs schema + referential-integrity + provenance
  checks; RECOMMEND invokes verifier; failures produce `VERIFICATION_FAIL` +
  `GATE_REJECTION` ledger events and return structured failure objects, never
  exceptions to the caller.
- 3.4 Verifier contract: input = memo AST (sentence, claimed_evidence_ids[]);
  output = per-sentence pass/fail with reason codes; zero tolerance: one
  failing sentence fails the memo.
- 3.5 Negative tests are mandatory: attempt to construct a Mandate with
  ENFORCE (must not typecheck: enforced via `assert_type` + a compile-check
  test file excluded from runtime), attempt sealed-scope resolution (ledgered
  MANDATE_VIOLATION_ATTEMPT), attempt gate bypass by direct ledger write
  (rejected: ledger writes only via orchestrator token).

## 4. Out of Scope
- Any model call; dispatch loop (STEP-03); UI.

## 5. Exit Checklist
- [x] ENFORCE unreachability: type-check test + runtime factory test green
- [x] hypothesis ledger properties green; tamper test detects mutation
- [x] verify-ledger CLI detects a fixture with a broken link at correct seq
- [x] Gate rejection paths ledgered and structurally returned
- [x] mypy --strict, ruff, coverage floor green; CHANGELOG updated
- [x] Saif's personal phase-close verification: verify-ledger against an
      intact export, a corrupted fixture at a known seq, and a truncated
      export with and without `--expect-head`
      - Run personally by Saif, post-implementation. All four scenarios
        behaved as specified. See "Phase close, verified" below.

## 6. Outcome

Shipped: D1-D6, in `src/ts_sentry/governance/` plus the `verify-ledger`
subcommand in `src/ts_sentry/cli/main.py`. Review stop after D1/D2 was
observed and passed on source, with two documentation corrections applied
before commit.

### D1/D2 review-stop corrections (Saif)

1. `canonical.py` claimed to be "shared by every hash this package computes"
   and that signature and ledger "both speak one hashing convention". False:
   `mandate_hash` uses canonical JSON and does not go through that module.
   The docstring now states both conventions accurately (structured objects
   via canonical JSON, flat field sequences via the separator-joined
   encoding) and names `mandate_hash` as the JSON convention's only user.
   The same overclaim had leaked into the CHANGELOG and was corrected there.
2. Typo `reden` -> `redden`.

### The ENFORCE guarantee, stated at its true width

Deliberately narrower than the convenient claim, because the tests only
support the narrow version:

- No `Mandate` can carry ENFORCE. Type-level via the `AgentConsequence`
  PEP 695 alias, and again at runtime in `__post_init__`.
- `validate` refuses every ENFORCE action under every mandate,
  unconditionally, and before any other refusal check, so the invariant can
  never be shadowed by an incidental refusal.
- The D4 gate refuses ENFORCE without an approving `HumanSignature`.
- A valid `HumanSignature` is unconstructible without an analyst identity
  and an explicit decision.

Not claimed: that `Consequence.ENFORCE` is unmentionable. Any module
importing the enum can name the member and Python offers no way to prevent
that. The documented claim is "no agent action can reach the ENFORCE gate".

Signature integrity is *binding*, not authentication: it proves the five
fields belong together and have not drifted apart. Real analyst
authentication is out of scope and named in Honest Limits rather than
implied by the word "signature".

### Dual-mechanism design for the type-level proof (D2)

Two complementary guards, neither subsuming the other. The subprocess test
exists because the in-place ignore *suppresses* the error, so running mypy
on the unmodified fixture would prove nothing (correction supplied by Saif
mid-implementation; the plan as approved had this wrong).

| Mechanism | Catches |
|---|---|
| In-place `# type: ignore[arg-type]` + `warn_unused_ignores` in the CI mypy step | ENFORCE construction silently *becoming legal*: the ignore goes unused and CI reddens |
| `test_enforce_unreachable.py`, which strips the ignore into a temp copy and asserts the `arg-type` error | The fixture being deleted, gutted, or made vacuous |

### Deviations from ARCHITECTURE, recorded

1. **`Mandate.version` (3.1).** 3.1's prose requires mandates be versioned;
   its dataclass sketch has no version field. Added as an explicit SemVer
   2.0.0 field, validated in `__post_init__` and inside the canonical hash
   form, with a test proving two mandates differing only in version hash
   differently. Per Saif's direction.
2. **`agent_id` is nullable (3.2).** 3.2's entry tuple does not contemplate
   `SESSION_OPEN` / `SESSION_CLOSE`, which are orchestrator events with no
   agent behind them.
3. **`output_schema: type[object]` rather than bare `type` (3.1).**
   Mechanical: `mypy --strict` rejects unparameterized generics. Same
   accepted value set.
4. **ARCHITECTURE 3.2 `||` erratum.** 3.2 specifies the entry hash as
   `SHA256(a || b || ...)`. Read literally, `||` is bare concatenation,
   which is ambiguous: distinct field splits produce identical byte strings,
   so two materially different entries can collide on one digest, in the one
   structure whose whole job is telling entries apart. Replaced with a
   `\x1f`-separated encoding that rejects any field containing the
   separator. `tests/test_canonical.py` makes the collision concrete:
   `("ab", "c")` and `("a", "bc")` concatenate identically and hash
   differently under this encoding. Recorded as an erratum against the
   specification, not as an implementation preference, per Saif.
5. **Unsigned ENFORCE ledgers `MANDATE_VIOLATION_ATTEMPT` + `GATE_REJECTION`
   rather than `VERIFICATION_FAIL` + `GATE_REJECTION`.** 3.3 specifies the
   latter pair for gate failures but does not enumerate the ENFORCE case.
   Nothing was verified and failed there; something reached for a level it
   may never reach, and the event type should say which of those happened.
   An ENFORCE carrying a *declining* signature does use the 3.3 pair, since
   a decision genuinely was evaluated.

### DuckDB TIMESTAMPTZ: a defect avoided at design time (D3)

The first application of CLAUDE.md's official-sources rule, and it changed
the design. Consulted:

- https://duckdb.org/docs/current/sql/data_types/timestamp.html
- https://duckdb.org/docs/current/sql/statements/create_schema.html

A `TIMESTAMPTZ` "only stores the `INT64` number of non-leap microseconds
since the Unix epoch", and "string formatting for this type [is] performed
in a configured time zone, which defaults to the system time zone". So it
does not preserve the offset it was written with, and its rendered string
depends on who reads it. Verified against DuckDB 1.5.5: one instant written
as `2026-07-31T14:30:00+05:30` renders three ways.

| Session TZ | Rendered |
|---|---|
| Asia/Kolkata | `2026-07-31 14:30:00+05:30` |
| UTC | `2026-07-31 09:00:00+00` |
| America/New_York | `2026-07-31 05:00:00-04` |

Had `entry_hash` covered a DuckDB-rendered timestamp, an intact ledger would
have verified on Saif's machine (IST) and reported a **false broken chain**
in CI (UTC). STEP-01 never hit this because it stores timestamps but never
hashes them.

Resolution: the hash covers a canonical IST ISO 8601 string in its own
`VARCHAR` column, with `TIMESTAMPTZ` retained for SQL-side querying. Tests
assert the two columns never drift and that the same chain verifies under
three reader time zones. It also removed a would-be dependency:
materializing a `TIMESTAMPTZ` through the DuckDB Python client requires
`pytz`, which this project does not have.

### Honest limit: tail truncation is undetectable

Surfaced by a hypothesis property, not by inspection. Chain verification
detects modification, reordering, and interior deletion. It cannot detect
entries removed from the *end*: what remains is a shorter chain whose every
link still recomputes.

The property was not weakened to hide this.
`test_truncating_the_tail_is_undetectable` asserts the limitation as a
passing test, so the day an anchor lands the test fails and forces the
limitation to be rewritten rather than quietly outliving its own truth.

**Split, per Saif's decision:** comparison in D6, storage in STEP-03. The
STEP-02 contract was not widened mid-phase. `verify-ledger` reports the
chain head (count + final `entry_hash`) and accepts `--expect-head
COUNT:HASH`, which compares against an expectation the caller already holds
and exits 6 on mismatch. That is a comparison verb; there is no storage, no
manifest stub, and no anchor derivation. The session manifest that will hold
a trustworthy anchor is a STEP-03 obligation.

### Findings outside the deliverables

- **`py.typed` was missing.** `ts_sentry` shipped as an untyped
  distribution, so any mypy run outside the repository's own configuration
  reported "module is installed, but missing library stubs or py.typed
  marker" and skipped analysis entirely. Surfaced by D2's compile-check
  test, which typechecks a fixture from a temporary directory. Added.
- **Local Python is 3.14.0; CI pins 3.12.** ARCHITECTURE 9 claims container
  parity on 3.12+. Every local green result in this phase is a 3.14 result.
  The new code was reviewed for 3.12 compatibility (PEP 695 `type`,
  `StrEnum`, `assert_never`, `datetime.UTC` are all 3.12-or-earlier
  features), but CI is the first actual 3.12 execution. Pre-existing; it
  applies to STEP-01's results equally.
- **Two branches are unreachable through their public paths, which is the
  invariant working rather than a coverage gap.**
  `_consequence_rank(ENFORCE)` never runs via `validate`, which refuses
  ENFORCE before ranking; `signature.py`'s separator guard never runs via
  `sign`, where the canonical encoder refuses one layer earlier. Both have
  direct tests with docstrings explaining why they have no public caller.

### Obligations carried into STEP-03

1. **No orphan `ToolId`s.** A member may only be added in the same commit
   that lands its corresponding allowlisted-tool-table entry, and from
   STEP-03 onward a test must assert every `ToolId` has a table entry. Per
   Saif's direction; recorded at the definition site in `mandate.py`.
2. **Import-graph test for the signature path.** `ts_sentry.agents.*` must
   not import `governance.signature`. Not shipped now because `agents/` does
   not exist and a vacuously green test is worse than an absent one.
3. **Anchor storage** for chain-head expectations, in the session manifest,
   per the split above.
4. **STEP-07's sealed-consumer wording**, carried forward from STEP-01's
   Outcome and unchanged by this phase: "measurement code is the only
   consumer of `sealed._labels`" must be read as "the only agent- or
   orchestrator-side consumer" before STEP-07's import-graph test is
   written. Phase 2 added no sealed consumer.

### Phase close, verified

Saif ran the phase-close verification personally, mirroring the STEP-01
pattern where his own red-team pass was the closing step rather than a
green test suite.

| Scenario | Expected | Observed |
|---|---|---|
| Intact JSONL export | exit 0, head reported | exit 0, head `6:3650...` |
| Corrupted fixture | exit 4 at the exact tampered seq | exit 4 at seq 3, `entry_hash_mismatch`, both digests shown |
| Truncated export, bare | exit 0 (limitation) | exit 0 |
| Truncated export, `--expect-head` | exit 6, heads reported | exit 6, expected and actual heads reported |

The third row is the one worth keeping in view: it is a *passing* result
that confirms a real limitation. Chain verification alone accepted a
truncated export, exactly as the D3 Honest Limit says it must, and only the
caller-supplied expectation caught it. The limitation was demonstrated on a
real artifact rather than only asserted in a test.

Pushes are checkpoint-gated (CLAUDE.md Process, added in `2982b77`): commits
were held locally per deliverable and pushed only after this confirmation.

### CI on Python 3.12: an argparse behavioral divergence

The pinned-version run was the first actual 3.12 execution of this code, and
it earned its keep. 276 of 277 passed. The DuckDB timezone test passed under
a genuinely UTC runner, clearing the phase's substantive risk: the
false-broken-chain defect avoided in D3 stays avoided in the environment
where it would have appeared.

One failure, and it was a real contract defect rather than a test artifact:
`test_malformed_expect_head_exits_five[-1:...]`.

Cause, per the official documentation
(https://docs.python.org/3/library/argparse.html): argparse notes that "some
situations are inherently ambiguous", and resolves them with the rule that
"positional arguments may only begin with `-` if they look like negative
numbers and there are no options in the parser that look like negative
numbers". The value `-1:<64 hex>` does not look like a negative number, so
3.12.13 classifies it as an option token, leaving `--expect-head` without a
value and raising argparse's own error, which "terminates the program with a
status code of 2" before `parse_expect_head` ever runs. Python 3.14 consumes
the same token as a value and reaches our validation, exiting 5. Same input,
two exit codes, decided by the interpreter.

Fixed in the contract rather than in the test, per Saif's direction, and the
`-1` case stays in the parametrize. `verify-ledger` now translates argparse's
usage errors into `EXIT_INPUT_ERROR`, so malformed input exits 5 on every
supported interpreter. This also removes a latent collision that predated the
divergence: argparse's status 2 is `EXIT_QUALITY_GATE_FAIL` elsewhere in this
CLI, so a mistyped flag was indistinguishable from a failed data-quality
gate. `build-dataset` keeps argparse's stock behaviour, so the STEP-01 CLI
contract is unchanged.

Worth recording: the first fix was incomplete, and a test written for it
caught the gap. Unrecognized arguments are reported by the *root* parser even
when a subcommand was named, because `parse_args()` collects leftovers from
`parse_known_args()` and errors on them itself. Translating only the
subparser left `verify-ledger FILE --not-a-flag` escaping as exit 2, which is
precisely the collision the change existed to remove. The root parser raises
too now.

Local verification cannot cover this class of finding: no 3.12 interpreter is
installed on the development machine (`py --list` shows 3.14 only). The
regression is therefore pinned by a test that drives argparse's error path
with an option value that is absent on every version, plus a test asserting
no `verify-ledger` invocation returns exit 2 at all.
