# Technology and Approach Decisions

A living record of what this project chose, what it did not choose, and why.

**Every entry cites where the decision was actually made** (commit SHA, STEP
file, or ARCHITECTURE section). Entries harvested from an existing record say
so. Entries where no rationale was ever written down say *that*, and the
tradeoff is then given as a clearly marked retrospective rather than as
invented history. A decision log that manufactures reasons after the fact is
worse than no decision log, because it reads as evidence.

Each phase appends its decisions here (CLAUDE.md, Process).

---

## What this project optimizes for

**Auditability, reproducibility, and honest bounds. In that order, ahead of
raw performance and ahead of minimal code.** It is a governance system: its
output is a claim about what an AI fleet did and did not do, and a claim
nobody can check is worth nothing regardless of how fast it was produced.

That target is visible in the ARCHITECTURE's central inversion (Section 1.1):
detective controls race to catch a harmful action, preventive controls remove
it from the action space. Preventing costs more code and more refusal paths
than detecting does.

### What it trades away, explicitly

| Given up | For |
|---|---|
| **Performance.** The ledger hashes every entry; the firewall re-serializes and re-scans every record; dispatch validates before every call. | Tamper-evidence and a refusal that happens before the action, not after. |
| **Minimal code.** Exhaustive `match` closed by `assert_never`, structured outcome objects instead of booleans, two hashing conventions instead of one. | A new enum member breaks the function that failed to handle it, loudly, instead of falling through silently. |
| **Convenience.** No wall-clock reads, no ambient randomness, no default gate checkers, no implicit credentials. Every one is injected and named at the call site. | A session replays identically, and an unconfigured component refuses rather than guessing. |
| **Feature velocity.** Phases are gated, review stops are contractual, and implementing ahead of the current STEP is forbidden (CLAUDE.md). | Each phase's claims are verified before the next one depends on them. |
| **Impressive numbers.** Gate rejections and verification failures are reported prominently as evidence the layer works (ARCHITECTURE 3.2). Honest Limits sections are mandatory and carried forward. | Claims that survive contact with a reviewer. |

The recurring test in this repository is not "does it work" but **"is the
claim we are making about it exactly true"**. Several entries below exist
because a claim was found to be wider than the behavior, and the claim was
narrowed rather than the behavior oversold.

---

## Phase 1: Data foundation

| # | Decision | Alternative(s) not taken | Reason | Recorded in |
|---|---|---|---|---|
| 1.1 | **DuckDB** as the store | Postgres, SQLite, plain Parquet | *Chosen by default; rationale not previously recorded.* Named in ARCHITECTURE 6.1 and 9 as a given, never argued. **Retrospective:** it is the right default here. Embedded, so a dataset is a file a reviewer can copy; columnar, so the analytical queries the agents run are native; reads Parquet directly, which the quality gate depends on. Postgres would add a service to reproduce, which is the opposite of the target. The cost is real and has been paid twice (entries 2.5, 3.9). | ARCHITECTURE 6.1, 9 |
| 1.2 | **Poisson-burst mixture** for temporal clustering | Hawkes self-exciting process | Deterministic under the seeded generator and simple to verify, at the cost of a less naturalistic clustering shape. Saif's confirmed choice; STEP-01 3.4 explicitly permitted either provided the choice was recorded. | STEP-01 Outcome, `d1fe931` |
| 1.3 | **Wrap AnalystKit** for the DAMA quality gate | Reimplement completeness/uniqueness/validity/consistency checks | Wrapping an existing tool over reimplementing its logic. Surfaced two integration findings that only direct invocation revealed (CSV-only `reconcile`, a Windows `cp1252` crash), both recorded rather than worked around silently. | STEP-01 D6, Outcome, `f666d00` |
| 1.4 | **Accuracy gated via `reconcile` against `sealed._labels`** | A profile percentage for accuracy | Confirmed by direct invocation that AnalystKit deliberately never scores accuracy: "no tool can measure accuracy without an authoritative source... scoring it from the dataset alone is fabrication." Adopting that position rather than inventing a number. | STEP-01 Outcome |
| 1.5 | **Timeliness reported, never gated** | Gate on all six DAMA dimensions as D6's text says | A deviation from the literal spec, called out rather than left implicit. Timeliness is wall-clock-relative decay; this dataset's window is fixed historical *for reproducibility*, so it scores 0% by construction forever, on a dimension no improvement could fix. | STEP-01 Outcome |
| 1.6 | **Allowlist semantics: absence is denial** | Blocklist of forbidden tables | `DataScope` has no member resolving to `sealed`, and both resolvers are exhaustive `match` closed by `assert_never`. Saif's red-team proved the payoff: adding a sealed member failed 10 tests, only 3 of which were written for leakage. The exhaustiveness structure caught it independently. | STEP-01 3.3, Outcome, `941d4a2` |
| 1.7 | **`DataScope` lives in `governance/scopes.py`**, not `data/` | Define it under `data/` where the tables are | It is a mandate concept that STEP-02 imports, not a data concept. Per Saif's direction. | STEP-01 Outcome |
| 1.8 | **pandas as a direct dependency** for bulk insert | `duckdb.executemany` | Measured: ~25s to load one scale-1 dataset versus well under a second for register + `INSERT ... SELECT`. Promoted from transitive to direct rather than relied on implicitly. | STEP-01 Outcome, `3e3c96c` |

---

## Phase 2: Governance core

| # | Decision | Alternative(s) not taken | Reason | Recorded in |
|---|---|---|---|---|
| 2.1 | **Separator-joined field encoding** for the entry hash | ARCHITECTURE 3.2's literal `a \|\| b` concatenation | Recorded as an **erratum against the specification**, not an implementation preference. Bare concatenation is ambiguous: `("ab","c")` and `("a","bc")` produce identical bytes, so two different entries can collide on the one digest whose whole job is telling entries apart. | STEP-02 Outcome dev. 4, `10a8923` |
| 2.2 | **Two hashing conventions**, kept apart | Force everything through one encoding | Structured objects hash as canonical JSON; flat field sequences use the separator encoding. Contorting one shape to fit the other's encoding buys nothing. A STEP-02 review correction fixed a docstring that had overclaimed a single shared convention. | STEP-02 Outcome, `10a8923` |
| 2.3 | **Timestamps stored twice**: `TIMESTAMPTZ` plus a canonical IST ISO string | Hash the `TIMESTAMPTZ` directly | The first application of CLAUDE.md's official-sources rule, and it changed the design. Per DuckDB's docs, a `TIMESTAMPTZ` stores only epoch microseconds and renders in the *reader's* session time zone. Hashing a rendered timestamp would have made an intact ledger verify in IST and report a **false broken chain** in a UTC CI runner. | STEP-02 Outcome, `10a8923` |
| 2.4 | **Failures returned, never raised** (gates, verdicts, dispatch) | Raise on rejection | A governance layer that signals rejection by throwing is one whose rejections can be swallowed by an `except`. Illegal *state transitions* still raise, because those are caller bugs rather than governed outcomes. | ARCHITECTURE 3.3, `fcae121` |
| 2.5 | **`GateChecks` has no defaults** | Default to permissive or no-op checkers | There must be no way to run a gate without naming the checks it runs, so an unconfigured gate cannot silently auto-approve. | `fcae121` |
| 2.6 | **Fail-closed on checker error** | Propagate the exception | A crashing validator must never yield an *accepted* artifact, and must not skip the ledger write either. | `fcae121` |
| 2.7 | **`Mandate.version` added** (SemVer, inside the hash) | Follow 3.1's dataclass sketch, which has no version field | 3.1's prose requires mandates be versioned while its sketch omits the field; the explicit field is the faithful reading. Per Saif's direction. | STEP-02 Outcome dev. 1 |
| 2.8 | **Unsigned ENFORCE ledgers `MANDATE_VIOLATION_ATTEMPT` + `GATE_REJECTION`** | 3.3's `VERIFICATION_FAIL` + `GATE_REJECTION` pair | Nothing was verified and failed; something reached for a level it may never reach. Recorded as an interpretation of 3.3, which does not enumerate the ENFORCE case. | STEP-02 Outcome dev. 5 |
| 2.9 | **Dual-mechanism proof** of ENFORCE unreachability | Either the in-place `type: ignore` or the subprocess test alone | Neither subsumes the other: the in-place ignore *suppresses* the error, so running mypy on the unmodified fixture would prove nothing. Correction supplied by Saif mid-implementation; the plan as approved had it wrong. | STEP-02 Outcome, `13dec26` |
| 2.10 | **Tail truncation asserted as a passing test** | Document the limitation in prose | So the day an anchor lands, the test fails and forces the limitation to be rewritten rather than quietly outliving its own truth. It worked: STEP-03 rewrote it. | STEP-02 Outcome, `71edd67` |
| 2.11 | **`--expect-head` is a comparison verb; storage deferred** | Build the anchor store in STEP-02 | Split per Saif's decision so the STEP-02 contract was not widened mid-phase. Storage landed in STEP-03 (entry 3.4). | STEP-02 Outcome, `83bd88c` |
| 2.12 | **`verify-ledger` never exits 2** | Keep argparse's stock exit code | Found by CI on the pinned 3.12: argparse resolves a dash-prefixed option *value* differently across versions, so one input exited 5 on 3.14 and 2 on 3.12. Fixed in the **contract** rather than the test, per Saif. Also removed a latent collision, since 2 means quality-gate-fail elsewhere in this CLI. | STEP-02 Outcome, `a155422`, `816a040` |
| 2.13 | **Official sources for framework behavior** | Rely on training-data recall | Adopted as a standing rule after the DuckDB finding (2.3) demonstrated the cost of guessing. | CLAUDE.md, `08bfc30` |

---

## Phase 3: Orchestrator and triage agent

| # | Decision | Alternative(s) not taken | Reason | Recorded in |
|---|---|---|---|---|
| 3.1 | **Detection stub runs orchestrator-side** from allowlisted scopes | (a) a build-time `flagged_entity` table with sealed-derived severity; (b) a hand-authored CLI input file | ARCHITECTURE 4.1 assumes a flagged queue STEP-01 never built. (a) would reopen a closed phase and let ground truth influence the queue; (b) makes the exit criterion depend on a hand-made file. Saif's choice, with two conditions: a test asserts only `DataScope`-resolvable tables are queried, and severity is documented as a heuristic stand-in with no sealed influence. | STEP-03 Outcome, `24e4012` |
| 3.2 | **No-orphan `ToolId`: entry per ID now, handler per ID by its own phase** | Remove the three unimplemented members and re-add them per phase | Removal is the purist reading but leaves a one-member enum and guts STEP-02's refusal tests. Saif's choice, with conditions: a distinct `TOOL_HANDLER_NOT_IN_BUILD` refusal code so a build limitation is never counted as a mandate violation, and a countdown test forcing the pending set to shrink each phase. | STEP-03 Outcome, `fcaa58c` |
| 3.3 | **Firewall keeps two copies**: verbatim block, redacted model copy | (a) verbatim only with detection annotations; (b) destructive stripping everywhere | D2 requires an instruction-stripping pass and 3.2 requires fixtures preserved verbatim. Two copies satisfies both rather than trading one off. Destructive stripping loses evidence. Saif's choice. | STEP-03 Outcome, `a819ca1` |
| 3.4 | **Content-derived fence nonce** | (a) a fixed fence like `<data>...</data>`; (b) a random nonce | A fixed fence is closed by writing the token in a comment. A random nonce fixes that but makes output irreproducible, which this project does not accept anywhere. A digest *of the content it fences* means closing the fence early is a preimage problem. | `a819ca1` |
| 3.5 | **Redaction markers carry the block nonce** | An unadorned marker string | Found by Saif's adversarial fixture: case content containing the literal marker produced a block where a planted marker was byte-identical to a real one. Marker text is orchestrator-authored text inside the data channel, so the fence's failure mode reappeared one level down; and a payload wrapped in a fake marker claims to have already been neutralized. | STEP-03 Outcome, `3392df0` |
| 3.6 | **Escape every line-breaking character** JSON does not | Rely on `json.dumps` escaping newlines | Found by a hypothesis property. `splitlines` breaks on U+2028, U+2029, NEL, VT, FF, FS, GS and RS, which JSON does not escape, so one comment could forge a **second record inside one fenced block**. | STEP-03 Outcome, `a819ca1` |
| 3.7 | **One retry authority**: the adapter's, with the SDK's disabled | Let both the SDK and the adapter retry | Two retry layers multiply attempts and make step and token accounting wrong. Full jitter over a seeded generator, so delays are uncorrelated between clients while a session still replays identically. | `768ab87` |
| 3.8 | **Stub adapter default; live gated twice** | A single `--llm-mode` flag | The intent must be expressed in two places (`--llm-mode live` *and* `TS_SENTRY_LLM_MODE=live`) so a shell alias or stray argument cannot start spending money. Credentials are checked for *presence* only; the value is never read by this repository. | `768ab87`, `cd637e4` |
| 3.9 | **Select `epoch_ms(...)`**, not `TIMESTAMPTZ` or a text cast | Cast timestamps to text | The same class as 2.3, found in a new place. Reading `TIMESTAMPTZ` into Python needs `pytz`; casting to text renders in the reader's session time zone, which would have made the recidivism component compute different priorities on different machines with neither looking wrong. | STEP-03 Outcome, `24e4012` |
| 3.10 | **The model call sits after the tool, not inside it** | A handler that prompts | The ranking is the product and must be reproducible from the dataset alone. A tool that could prompt would be an agent wearing an allowlist entry. | `24e4012` |
| 3.11 | **Rationale verification is orchestrator-side** | Verify inside `agents.triage` | The import-graph test failed on its first run: the agent reached `governance.signature` through `verifier -> gates`. The rule could have been widened; it was not, because an agent holding its own verifier is an agent nobody is verifying. | STEP-03 Outcome, `24e4012` |
| 3.12 | **Monotonicity stated in two parts** | Assert strict monotonicity, as 3.1's text implies | Hypothesis found that raising a component by ~1e-16 leaves the weighted priority bit-identical. Not fixable in the scorer, so the claim was narrowed: never lower always, strictly higher once the change survives the weighting. | STEP-03 Outcome, `24e4012` |
| 3.13 | **Recidivism is pattern persistence**; undisclosed media does not flag; inbound signals count | Add planted variance to the stub, as instructed | Saif's finding 1 asked for planted variance. **Declined and recorded**: planting variance would make the queue not derived from the data, contradicting the module's central claim. Measuring first showed the variance was present and suppressed. Each of the three changes is grounded in a measurement. | STEP-03 Outcome, `9058ce9` |
| 3.14 | **Rationales cite the discriminating component** | Cite the largest component | Saif's finding 2, from reading a real ranked queue: every rationale cited `severity_class` because it was largest, and at equal severity it was the one thing that explained nothing. Fixed in the builder, not the verifier, which was correct to accept any resolvable citation. | STEP-03 Outcome, `9058ce9` |

---

## Choices made by default, with no recorded rationale

Listed because a decision record that quietly omits its unexamined choices is
a decision record that flatters itself. Each tradeoff below is **retrospective
and written now**, not recovered from a prior discussion.

| Choice | Alternative(s) | Status | Retrospective tradeoff |
|---|---|---|---|
| **stdlib `argparse`** | Click, Typer | *Chosen by default; rationale not previously recorded.* | Gains: zero dependency at the CLI boundary, and full control over exit codes, which mattered more than expected (2.12 required subclassing the parser to stop argparse exiting 2). Costs: verbose wiring, no automatic completion, and the version-dependent parsing behavior that bit us. Typer would have given better ergonomics and taken the exit-code control away. Would choose the same again for this reason. |
| **Hand-rolled frozen slots dataclasses** with `__post_init__` validation | Pydantic, attrs | Mandated by ARCHITECTURE 9 and CLAUDE.md as a style rule; the *reason* was never argued. | Gains: no runtime dependency in the governance core, validation logic visible at the point it applies, and `slots=True` keeps entries cheap. Costs: every validator is hand-written and could be forgotten; Pydantic would give serialization, JSON schema, and coercion free, all of which are hand-rolled here (`to_json_object` appears on a dozen types). The honest summary is that this trades convenience for a governance core with fewer moving parts, and the repetition is a real cost being paid every phase. |
| **DuckDB** | Postgres, SQLite | See 1.1. Named as a given in ARCHITECTURE, never argued. | Covered in 1.1. Two timezone defects (2.3, 3.9) are directly attributable to it, both caught. |
| **numpy `Generator`, single and seeded** | `random`, per-module generators | Mandated by CLAUDE.md; the *reason* is stated ("no bare random") but not argued. | Gains: one seed reproduces a whole build, and `numpy` is already a dependency. Costs: threading a generator explicitly through every function is verbose, and it is a heavy dependency if it were the only use. |
| **hypothesis** alongside example tests | Example tests only | Mandated by ARCHITECTURE 9 for the hash chain and gate logic. | It has repeatedly earned its place: the U+2028 fence escape (3.6), the floating-point monotonicity limit (3.12), and STEP-01's three budget defects were all found by properties, not by inspection. Cost is runtime; the suite takes about 75 seconds, most of it hypothesis. |
| **ruff** for lint and format | black + flake8 + isort | *Chosen by default; rationale not previously recorded.* | Gains: one tool, one config, fast. Costs: a younger ecosystem and occasional rule divergence from the tools its rules are modelled on. Low-stakes and easily reversed. |
| **mypy `--strict`** | pyright, no type checking | Mandated by ARCHITECTURE 9. | It has caught real defects, including a wrong `RankedRow` construction during D5. Cost: `type: ignore` pressure at test boundaries, and one genuine escape hatch was needed for the optional vendor import. |
| **hatchling** | setuptools, poetry, pdm | *Chosen by default; rationale not previously recorded.* | Gains: PEP 621 metadata, minimal config, and `allow-direct-references` for the AnalystKit git pin. Costs: fewer lockfile and environment features than poetry or pdm, which is directly relevant to the supply-chain gaps below. |
| **JSONL for the ledger export** | JSON array, CSV, a binary format | Named in ARCHITECTURE 3.2 as a given. | Gains: append-friendly, line-per-entry so a truncation is visible by eye, and diffable. Costs: no schema enforcement in the file itself, and the reader must tolerate a tampered line while still parsing it. |

---

## Supply chain and dependency posture

### How dependencies are pinned

| Dependency | Constraint | Why |
|---|---|---|
| `analystkit` | `@ git+...@v2.1.0`, an **exact git tag** | The tightest pin in the project, and deliberately so. v2.1.0 is the first release with the Parquet support the D6 quality gate depends on, and it is not on PyPI, so a direct reference is the only way. Requires `allow-direct-references` in hatchling. |
| `duckdb` | `>=1.5` | A floor, not a pin. Behavior verified against 1.5.5 (entries 2.3, 3.9). |
| `numpy` | `>=2.0` | A floor. |
| `pandas` | `>=2.0` | A floor. Promoted to a direct dependency for a measured reason (1.8). |
| `anthropic` | **unversioned**, in the optional `live` extra | Deliberately without a floor, and the reason is recorded in `pyproject.toml`: the offline environment cannot resolve or verify one, and asserting a version nobody checked is exactly the guess CLAUDE.md's official-sources rule exists to prevent. The version actually exercised is to be recorded in the live-mode smoke note, **which has not been run**. |

### What is stdlib-only, and why

The governance core touches no third-party code beyond the store. `hashlib`,
`hmac`, `json`, `re`, `dataclasses`, `enum`, `datetime`, `zoneinfo`,
`argparse`, `subprocess`, `ast` and `pathlib` carry the hashing, the canonical
encoding, the signature path, the firewall, the CLI, and the import-graph
test. That is deliberate for the parts whose correctness is the product: the
fewer third parties inside the chain of custody, the fewer parties a reviewer
has to trust. `hmac.compare_digest` is used for the signature comparison
rather than `==`.

The model boundary is the one place a vendor client appears, and it is
imported *inside the call* so an offline install never loads it. A test parses
every module with `ast` and fails on any module-scope import of it.

### Recorded gaps, not hidden ones

- **No SBOM.** Nothing generates a CycloneDX or SPDX bill of materials. A
  reviewer cannot get a machine-readable inventory of what ships.
- **No hash-locked lockfile.** There is no `requirements.lock`,
  `poetry.lock`, or `uv.lock` with hashes. Floors like `duckdb>=1.5` mean two
  installs a month apart can resolve to different versions, so **the
  environment is not reproducible even though the dataset is.** This is the
  most substantive gap on the list: reproducibility is a stated optimization
  target, and it currently holds for data and not for dependencies.
- **No upper bounds.** A major release of duckdb, numpy, or pandas is
  installable and would be picked up silently.
- **No dependency vulnerability scanning** in CI (no `pip-audit`, no
  Dependabot).
- **The AnalystKit pin is a git tag, not a commit SHA or hash.** A tag can be
  moved. A SHA cannot.
- **The `live` extra is unpinned and unexercised.** See the entry above and
  the STEP-03 Honest Limits: `LiveAdapter.complete` has never been run.

None of these are hard to close, and none are closed. They are listed here so
that "reproducible" is never read as a claim about the environment.

---

## Appendix: decisions that were reversed or narrowed

Kept because a record showing only decisions that survived is a record that
teaches nothing about how they were reached.

| What changed | From | To | Trigger |
|---|---|---|---|
| The tail-truncation claim | "undetectable" | "invisible to chain verification alone", with an anchor covering the rest | The anchor landing in STEP-03, as the STEP-02 test was written to force |
| Rationale verification's home | `agents.triage` | `orchestrator.rationale_check` | The import-graph test failing on its first run (3.11) |
| The monotonicity property | strict | non-strict, plus strict above a stated resolution | Hypothesis (3.12) |
| The detection stub's flag criterion | undisclosed synthetic media triggers | coordination artifacts trigger; undisclosed media aggravates | Saif reading a real ranked queue (3.13) |
| Redaction markers | plain text | nonce-bound | Saif's adversarial fixture (3.5) |
| `verify-ledger`'s usage-error exit code | argparse's 2 | 5 | CI on the pinned 3.12 (2.12) |
| Session id reproducibility claim | "two runs of the same inputs comparable" | true within one build directory; **not** across rebuilds | Saif's re-run producing a different id, then measuring that `build.duckdb` is not byte-stable while the Parquet exports are (STEP-03 Honest Limits) |
