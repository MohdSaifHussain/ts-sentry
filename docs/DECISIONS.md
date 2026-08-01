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

## Phase 4: Evidence agent and evidence packs

| # | Decision | Alternative(s) not taken | Reason | Recorded in |
|---|---|---|---|---|
| 4.1 | **Review stop after the pivot vocabulary and the pack model**, before the agent that proposes from them exists | The STEP file's literal D1-then-D2 order | Saif's reading of his own instruction when the two conflicted: "before the agent that proposes them exists" beats the numbering. The no-dynamic-SQL guarantee is established by a human reading the SQL, and reading it after the proposer shipped would review a surface already in use. | STEP-04 Outcome, `d8ff6b9`, `157fe21` |
| 4.2 | **The pivot parameter is a bound value compared against an `'any'` sentinel**; both metadata fields live in the template as `UNION ALL` branches | Selecting the column by parameter | The natural implementation is dynamic SQL. This is the same feature with the identifier taken out of the caller's hands, and it rests on a measured fact: DuckDB 1.5.5 rejects a parameter where an identifier belongs and evaluates one as a literal where a value belongs. | `d8ff6b9`, pivots.py docstring |
| 4.3 | **No pivot template selects a free-text column** | Return comment bodies and display names as evidence | Case text has exactly one route to a model, through the input firewall. A pivot returning it would open a second route into an artifact no firewall inspects. Enforced by a test over the SQL. | `d8ff6b9` |
| 4.4 | **Entity-id parameters must resolve to a node already in the pack** | Accept any well-formed id | An agent that can name any entity can walk the whole platform from one case. Pivots expand from the analyst-selected seed. Beyond what STEP-04 3.1 requires; recorded as a deliberate tightening. | `d8ff6b9` |
| 4.5 | **Every projection aliased; no query orders by position** | Keep positional `ORDER BY`, which Saif's review passed as safe | Taken up from his optional note. Positional ordering has a silent half arity checking cannot see: reordering a SELECT list changes the sort *and* leaves `columns` mislabelling every field of every evidence record built from it, with nothing failing. Aliases plus a test comparing `columns` to DuckDB's reported result names close both. | Saif's D1 review note, `2285a74` |
| 4.6 | **The handler returns the grown pack**, which arrives through `ToolResources` | Return the hop's rows and gate the pack separately | Dispatch runs the consequence gate over whatever a tool returns, so returning rows would mean the gate validated a fragment and nothing validated the pack. The pack is a *resource* rather than a param because an agent that could supply it could supply one containing entities it invented, and every "already in the pack" check would then check the agent's claims against themselves. | `e648f14` |
| 4.7 | **`reviewer_kind` inside the ledgered `HUMAN_DECISION` payload**, required by the type with no default | Record it in a session artifact beside the ledger | Saif's condition. A scripted stand-in produces a real `HUMAN_DECISION` entry, and the human in "human decision" is the thing ARCHITECTURE 3.3 says can never be automated. In the payload it is hash-covered; in a side file the one mechanism this system has for protecting claims would not have been protecting this one. | Saif, this session; `e648f14` |
| 4.8 | **Two of the three ASSEMBLE-gate checks are unreachable, and kept** | Delete them as redundant with the type's invariants | The same reasoning STEP-02 applied to its two unreachable branches. The gate receives an `object` from a handler, not a guaranteed pack; if a future change ever builds one by a route that skips `__post_init__`, these are what remain. Tested directly against packs built by bypassing the constructor. | `16f3965` |
| 4.9 | **Recovery reports a structural ceiling beside the raw fraction** | Report recovery over the ring alone | A ring that is mostly comments cannot be fully recovered into a pack however well the agent performs, because a comment enters as a timeline event rather than a node. Reporting 0.25 without saying 0.25 was the maximum reachable invites the reader to blame the agent for a structural bound. | `b6625b6` |
| 4.10 | **Cases with no planted ring are counted separately, never as zeros** | Fold them in with recovery 0.0 | "Nothing to find" and "failed to find it" are different results, and averaging them together understates the second. | `b6625b6` |
| 4.11 | **`dataset_digest` from the manifest's `table_hashes`, under a `v2` domain**, with no fallback | (a) keep hashing `build.duckdb`; (b) hash the store when no manifest is present | Closes the gap STEP-03 recorded. The `v2` separator makes the era change structural rather than a changelog note, so a pre-fix and a post-fix identity for one build cannot be compared by accident. A fallback would restore the defect exactly where it is hardest to notice. | Saif, this session; `STEP-04 Outcome` |
| 4.12 | **GraphML hand-written with `ElementTree`** | Add `networkx` for the serializer | GraphML is four element types, and a dependency added to serialize them would widen the supply-chain surface this document already lists gaps against. Written against the specification rather than from memory. | `graphml.ethz.ch` primer, STEP-04 D6 |
| 4.13 | **The saturation of the recovery table asserted as a passing test** | Note it in prose, or tune the stub until the numbers move | The shape STEP-02 used for tail truncation. The columns are identical because the stub exhausts its strategy in two hops, which is a fact about the stub; engineering it to look busy would have manufactured a measurement. The day a better strategy makes recovery grow, the test fails and forces the claim to be rewritten. Superseded in precision by Saif's phase-close finding (4.14), which names the mechanism rather than the symptom. | `b6625b6` |
| 4.14 | **The traversal defect recorded at the artifact's precision**, as STEP-07's central risk | Record it as "the recovery table is flat" | Saif's phase-close reading of a real `evidence_pack.json`. The vague version is a symptom that invites tuning a number; the precise version names two mechanisms that have to be built. See below. | STEP-04 Outcome, Saif's phase close |
| 4.15 | **Seed-existence checked before a session opens** | (a) leave it to STEP-05; (b) check it after opening and close the session cleanly | Found while confirming 4.14, and fixed at Saif's instruction before release because it is STEP-04's defect. (b) was rejected on the ordering: a refusal after opening would already have written the chain, manifest and anchor it exists to prevent, so the guard runs before the output directory exists. The gate validates artifact consistency, not correspondence to reality; seed-existence is the boundary check that ties the audit trail to a real subject. | `4a29640`, STEP-04 Outcome |

---

## Phase 5: Memo agent and policy corpus

| # | Decision | Alternative(s) not taken | Reason | Recorded in |
|---|---|---|---|---|
| 5.1 | **Clause-level text committed, not whole pages** | (a) commit the full fetched pages; (b) commit only hashes and materialize by fetch | Saif's decision. The clauses are the citation-resolution target and memos quote at most 15 words, so three whole policy pages would redistribute far more than resolution needs in a public MIT repository. (b) was rejected because the shipped repo could then not resolve a real citation offline. | `319e8cc`, `policy_corpus` docstring |
| 5.2 | **`content_digest` is the citation identity; `retrieval_sha256` is provenance** | Hash the fetched page and call that the document | Forced by a measurement. Two fetches of the spam page in one process returned different digests, differing only in the CSP `nonce` Google regenerates per request, so a raw-byte digest changes whether or not any policy changed and can never answer "has this policy changed". The clause text *is* stable: all 14 substantive sections came back byte-identical. Demonstrated on real artifacts, not argued. | Saif, HALT 1; `02972b9` |
| 5.3 | **Anchors derive from headings, never from position** | Include the ordinal in the anchor | ARCHITECTURE 6.2 forbids silent drift, and a positional anchor would renumber every citation below an inserted clause. The residual limit (a *duplicate* heading inserted above an existing one does move it) is asserted as a passing test rather than described. | `02972b9` |
| 5.4 | **Labelled list items are anchored, not just headings** | Anchor at h1/h2/h3 only | Found by reading the real page. On the spam page every violation type is an `<li><strong>Comment spam:</strong>` item inside one 486-word section, so heading-only anchoring would have left a memo about a T-01 ring citing nothing narrower than that section. These items are what a statement of reasons needs to point at, and they map onto ARCHITECTURE 2.1's threat classes. | HALT 1; `02972b9` |
| 5.5 | **Clause boundaries follow policy subject, not page layout** | Extract faithfully to the DOM and accept the result | Saif's principle, from a real defect: the fake-engagement page opens with a `tip` callout carrying 76 words of *impersonation* policy, so `fake-engagement-policy` opened with a different rule and a citation would have resolved perfectly to the wrong one. The callout is preserved verbatim under its own anchor; nothing is dropped. | Saif, HALT 1; `319e8cc` |
| 5.6 | **Callout headings are operator-supplied and fail closed** | Derive one from the text, or drop callouts | The page gives a callout no heading, so naming one is an editorial act. `name_callouts` refuses an unnamed callout and refuses a title matching zero or several, so a corpus cannot carry policy text under a heading nobody chose. | `02972b9` |
| 5.7 | **`Retrieval` is required with no default** | Default to `fetched_verified` | Following `ReviewOutcome.reviewer_kind`. The first attempt to retrieve DSA Article 17 returned only the preamble, and a manifest recording that as a clean fetch would have asserted something false about its own provenance. | Saif's condition; `02972b9` |
| 5.8 | **Corpus updates bind into `SESSION_OPEN`, not a twelfth `EventType`** | Add `CORPUS_UPDATE` to the ledger vocabulary | A re-fetch is build-time provenance rather than a session action, and it happens when no session is open, so there is no token to write it with. Binding gives the guarantee that matters: a memo is permanently tied to the corpus state its citations were checked against, hash-chained. Keeps ARCHITECTURE 3.2's eleven types closed. | Saif; `014c0c8` |
| 5.9 | **Art. 17(3)(c) is structural, not a fifth sentence role** | Add an `AUTOMATED_MEANS` role | A disclosure about how automated a decision was is worthless if the automated component composes it, which is the `reviewer_kind` argument. Keeps 3.1's four roles exactly as written; recorded as a deviation because 3.1's list does not cover (c). Vocabulary from the Commission's DSA Transparency Database. | Saif; `7da1d5a` |
| 5.10 | **No `LEGAL_GROUND` role**, so Art. 17(3)(d) is unreachable | Model both grounds | Every case here is a terms-and-conditions matter, and a legal-ground role would invite a memo to assert illegality nothing in this system can assess. | `7da1d5a` |
| 5.11 | **The agent names an anchor, never a document digest** | Let the draft carry the digest | A digest the agent supplied would let it point a citation at a document nobody checked it against, and asking a model to reproduce 64 hex characters makes the check about transcription. An anchor two documents share is refused rather than resolved to the first, which is a real case in corpus v1. | `f5e3bbb`, `draft_check` docstring |
| 5.12 | **The handler attaches; the gate judges** | Refuse a bad citation inside the handler | The same split as DECISIONS 4.6. A handler that refused would produce a `FAILED` dispatch, which reads as a defect, where the truthful outcome is a `GATE_REJECTION` carrying a reason code. A governance finding must not be recorded as a crash. | `f5e3bbb` |
| 5.13 | **`phase_five_checks` is a function where `PHASE_FOUR_CHECKS` is a constant** | Keep a module-level constant | The RECOMMEND checker has to be told what a claim may resolve against, which is a property of the pack and corpus in scope. A constant would have to find them for itself, and the only way to do that is to let the memo say what it should be checked against. | `3303bd8` |
| 5.14 | **`MIN_EXCERPT_WORDS`, with its own reason code** | Keep only the fair-use ceiling | HALT-2 finding 2. `excerpt="spam"` is a true substring of the comment-spam clause and identifies no rule, so a memo could satisfy Art. 17(3)(e) with one common word. `EXCERPT_TOO_SHORT` is distinct from `EXCERPT_NOT_IN_CLAUSE` because a too-short excerpt is a *true* quotation that identifies nothing, which is a different finding from a false one. Four is a judgment and is recorded as one. | Saif, HALT 2; `4af41d6` |
| 5.15 | **Word-sequence matching, not substring** | Regex word boundaries, or plain `in` | HALT-2 finding 3. `"omment spam: Using high-volume,"` is a contiguous substring of the clause and quotes something it does not say. Comparing word lists makes alignment structural rather than something a regex is trusted to get right. Whitespace is still the only thing forgiven. | Saif, HALT 2; `4af41d6` |
| 5.16 | **The gate keeps the unreachable missing-citation check** | Rely on the constructor's refusal | HALT-2 finding 1, and it broke the `pack_gate` precedent before it was fixed. The gate receives an `object` from a handler; the type is a guarantee only until something builds one by another route, and the gate is what remains when it does. | Saif, HALT 2; `4af41d6` |
| 5.17 | **`content_digest` excludes `status`** | Cover the whole memo | Found while building D6. Signing sets the status to SIGNED, so covering it meant the signature was over a value the signed memo no longer had, and the artifact failed its own verification the instant it was produced. The consequence (a DRAFT and a SIGNED memo with identical content share a digest) is stated rather than left to be found. | `29128dc` |
| 5.18 | **The watermark has no suppression parameter** | A `watermark=False` for internal copies | A label that can be switched off is a label that will be switched off, and the one moment it matters is the moment somebody wanted it gone. Asserted against the function's own parameter list, so adding one reddens the suite. A signature over a *different* memo does not remove it either. | `29128dc` |
| 5.19 | **The RECOMMEND gate refuses a SIGNED memo** | Accept it, or ignore status | HALT-2 finding 4. Re-gating answers the wrong question: a signed memo is trustworthy because its digest recomputes and the signature verifies, which is signature verification rather than claim verification. Accepting one would let it be re-laundered through the agent path and emerge with a fresh `VERIFICATION_PASS` that says nothing about the signature. | Saif, HALT 2; `29128dc` |
| 5.20 | **`rejected_attempts`, `distinct_defects` and `revised` reported separately** | One "corrections before human review" count | Found by reading the metric's own output: the first overclaim run reported 3 corrections for one unchanged sentence rejected three times, inflating exactly the metric ARCHITECTURE 7.2 showcases. Same discipline as the recovery-metric ceiling (4.9) and the raw-byte-hash finding (5.2). | Saif; `00cb625` |
| 5.21 | **Every overclaim fixture asserts a reason code** | Assert that the memo was rejected | A suite checking only "this was rejected" passes if the gate rejects everything for the wrong reason, and refusals that cannot be counted by cause make the `GATE_REJECTION` metric meaningless. A passing control is asserted too, so a fixture failing for an unrelated reason cannot look like a caught defect. | `f1330fc` |

---

## Phase 6: Prompt-eval agent and regression gate

| # | Decision | Alternative(s) not taken | Reason | Recorded in |
|---|---|---|---|---|
| 6.1 | **The evaluated task is a new `classify.threat_class.v1`, consumed by no session** | (a) wire it into triage; (b) evaluate the three existing prompts on a non-class metric | No prompt in the fleet emits a threat class, and the harness reports confusion by class. (a) would make a triage product change inside STEP-06 and would touch DECISIONS 3.1's guarantee that severity is data-derived; (b) contradicts D2 and D3's explicit T-01..T-07 stratification. Saif's decision A. The wind tunnel is real and the aircraft does not yet fly, which is carried in Honest Limits rather than left to be noticed. | Saif, this session; `be66b25` |
| 6.2 | **`EXIT_REGRESSION_REFUSED = 7`**, against D5's literal "exit 5" | Follow D5 as written | `EXIT_INPUT_ERROR` is already 5. A regression refusal is a *governance outcome* and must be distinguishable by exit code from a malformed `--candidate`, which is a broken call. Exactly the collision DECISIONS 2.12 found and removed when argparse's 2 shadowed `EXIT_QUALITY_GATE_FAIL`. Saif's decision B; recorded as a deviation from the STEP contract. | Saif, this session; `e1d3f69` |
| 6.3 | **The three shipped prompts migrate into the registry, record-only** | A registry holding only the new classification prompt | Closes the deferral `firewall.SystemPrompt` recorded ("a versioned registry with content-addressed files is STEP-06"), so it fulfils a documented promise rather than reaching into a closed phase. A registry holding one of four prompts while calling itself the fleet's registry is a claim wider than the behaviour. Digests unchanged and **asserted** unchanged, module constants still the runtime source. Saif's decision C. | Saif, this session; `be66b25` |
| 6.4 | **The gate reads the confidence interval's lower bound, not the point estimate** | Gate on the observed delta and report intervals for the reader | Activation requires *evidence of non-regression*, not absence of evidence of regression. Same posture as the seed guard (4.15) and the claim verifier. A candidate whose interval is wide is refused even when its point estimate looks fine, and that cost is accepted rather than tuned away. Saif's decision D. | Saif, this session; `a8012ec` |
| 6.5 | **`RECALL_REGRESSION` and `REGRESSION_NOT_EXCLUDED` are separate breach codes** | One refusal code for both | The first says the candidate is measurably worse; the second says the eval set cannot tell. On this project's data the second is the common case and is a fact about the generator, so reporting it as the first would blame a prompt for the eval set's resolution. Same argument as 5.21: refusals that cannot be counted by cause make the metric meaningless. | `a8012ec` |
| 6.6 | **Activation state lives in an append-only pointer history, never on the version record** | A mutable `active: bool` on `PromptVersion` | The obvious implementation is wrong in the exact way STEP-06 3.4 names: activating v2 would have to write `False` into v1's record, so one prompt's activation would rewrite another prompt's record. The active version is derived from the log; version records are written once. Rollback is another entry, so the history never describes a system that did not make the mistake it made. | `be66b25` |
| 6.7 | **The eval set is built at its honest ceiling and the tolerance is derived from what that ceiling can resolve** | (a) raise the generator's planted volume; (b) add content variety; (c) reverse 6.4 to a point estimate | Measured: threat entities per class are 4 to 12 and invariant to `--scale`; content is byte-identical across seeds. (a) and (b) substantially reopen STEP-01, a closed phase that met its own exit criterion. (c) is worse than refusing on uncertainty: at n=6 a point estimate reports a single-item difference as a 17-point regression. Saif's decision. The consequence, that this gate detects a class collapse and not a few-point drift, is stated in the report artifact and the Outcome rather than inferred. | Saif, this session; `97a2f76` |
| 6.8 | **`tolerances_sha256` binds into `SESSION_OPEN`** | A twelfth `EventType` for tolerance changes | Directly follows 5.8. A tolerance set is build-time policy that is declared when no session is open, so there is no orchestrator token to write it with. Binding gives the guarantee that matters: a verdict is permanently tied to the limits it was reached under, hash-chained. ARCHITECTURE 3.2's eleven types stay closed. | Saif, this session; `e1d3f69` |
| 6.9 | **Eval item ids are opaque and the item order is shuffled** | Key items by entity id; emit them in query order | Planted entity ids name their class in three characters, so an id-keyed item hands the model its answer through the firewall. The *ordering* leaks the same thing and was missed by the content check: the first build emitted contiguous per-class blocks, so `items.json`, which carries no labels at all, was on its own sufficient to reconstruct the answer key. Found at the review stop, not by any test written before it. | `0f5553e` |
| 6.10 | **The classification stub responds to the system prompt, never to a mode flag** | `StubMode.OVERCLAIM`, as the memo stub uses | Found by the end-to-end turn test. A mode belongs to the adapter, so it degraded incumbent and candidate alike and reported the degraded candidate **activatable**. The deeper problem: a stub keyed on a flag answers identically whatever prompt it is given, so an eval harness driven by one measures nothing about prompts while still producing a report, an interval and a verdict. | `a8012ec` |
| 6.11 | **An unparseable answer keeps its position with `predicted=None`** | Drop it from that version's list, or drop the item from both | Dropping from one side misaligns the pairing; dropping from both deletes the failure from the *failing* version's recall, which is the flattering direction. `None` is never equal to a true class, so it scores as a recall miss while counting toward no class's precision, and the unparseable count is reported separately because a broken output contract is a different finding from a wrong answer. | `a8012ec` |
| 6.12 | **`minimum_detectable_drop` is reporting only; `decide` never reads it** | Derive the tolerance from the report at gate time | A gate that set its own tolerance from the evidence would widen its acceptance criterion exactly when the evidence got weaker, which is a gate that passes everything and calls it rigour. The two are kept apart in code and asserted apart by test, not merely intended apart. | `a8012ec` |

### The finding that made 6.7 necessary

Recorded here with its measurements because it is the central STEP-06 finding
and because the next person to ask "why is the eval set only 59 items" will read
this file first.

**The generator's planted threat volume is fixed and does not scale.** Measured
on real builds during D2:

| | t01 | t02 | t03 | t04 | t05 | t06 | t07 | benign |
|---|---|---|---|---|---|---|---|---|
| entities, scale 1 | 6 | 12 | 6 | 4 | 4 | 6 | 6 | 450 |
| entities, scale 10 | 6 | 12 | 6 | 4 | 4 | 6 | 6 | 4,500 |
| entities, scale 40 | 6 | 12 | 6 | 4 | 4 | 6 | 6 | 18,000 |

Benign grows 40x; every threat class is identical. The mechanism is structural:
`RING_COUNT` and `MEMBERS_PER_RING` are fixed constants in each `t0N` module and
`for_budget` only ever *shrinks* them, so the per-class abuse budget is computed
from the population and then left almost entirely unspent. At scale 40 roughly
540 slots are available and 44 are used.

**Seeds do not vary content either.** Seed 42 and seed 7 return byte-identical
planted ids, display names, descriptions and comment text; the seed varies
timing and which base entities are targeted. Pooling across seeds would add
exact duplicates, inflating the item count while narrowing the bootstrap
interval by replication, which is the "a lucky sample must not read as
activatable" failure reached from the other side. It is not done.

**This is explicitly not a STEP-01 defect.** STEP-01's exit criterion was
byte-stable rebuilds and a passing leakage test; it met both, and neither is
touched by this. The fixed threat volume is a limit STEP-06 discovered by asking
the data for something STEP-01 never promised. Manufacturing a defect against a
closed phase that met its spec would be the same dishonesty this document opens
by warning against.

**Future work, owned by whichever phase next takes up the generator:** richer
per-class content templates for genuine item independence, plus volume that
spends the budget already computed. That is the only route to sub-tolerance
drift sensitivity, and it is out of scope for STEP-06.

---

## Phase 7: Measurement, the VVR lens (D1 and D2)

Sources consulted before any of this was implemented, per the official-sources
rule: the Google Transparency Report help centre
(<https://support.google.com/transparencyreport/answer/9209072>), the YouTube
blog post introducing the metric, and the independent statistical assessment
Google commissioned from Arnold Barnett (MIT, September 2021), reproduced at
`docs/barnett-vvr-assessment.txt`. The Barnett report is the load-bearing one:
it is the only source that describes the stratification and the allocation.

| # | Decision | Alternative(s) not taken | Reason | Recorded in |
|---|---|---|---|---|
| 7.1 | **The estimand is the viewed video's own sealed label, with the spam-shaped classes excluded** | (a) admit every non-benign class; (b) inherit violative status from the video's channel or its commenters | Fidelity is D1's entire value. The published method judges "whether each video does or does not violate our community guidelines" and states "we omit spam from the metric altogether", so T01 and T06 are held out. (b) would stop being a replication and become an invented metric. The cost is accepted and stated rather than softened: the baseline estimand is narrow. Saif's decision. | Saif, this session; `597b77c` |
| 7.2 | **The headline interval covers sampling error only; rater quality is never folded into it** | Widen the interval to absorb modelled rater error | Google states plainly that "the confidence intervals do not take into account rater quality", and Barnett's footnote 5 excludes rater quality from his assessment's scope. A wider interval would be a better estimate and a worse replication. The confusion-matrix modelling 3.1 requires is labelled a documented superset and surfaces only as the D2 bias curve. Saif's decision. | Saif, this session; `cd4a6f2` |
| 7.3 | **Strata are bands of an observable risk proxy, described as an analog rather than as a classifier** | (a) stratify on the sealed label; (b) claim to use "the classifier risk score"; (c) stratify on views-per-video | Barnett: strata are "non overlapping ranges for the video scores" built so violation probability "would not vary much within a given stratum but would vary appreciably across strata", plus a fifth "no score available" stratum. There is no production classifier here, so the *method* is replicated using content-provenance as the risk proxy and said so in those words. (a) is cheating. (b) claims a detection capability this project repeatedly says it does not have. (c) is rejected in 7.4. Saif's wording. | Saif, this session; `597b77c` |
| 7.4 | **views-per-video is rejected as a stratifier despite being the strongest one available** | Use it; it separates violative from benign almost perfectly | It is label leakage wearing an observable's clothing. The threat modules plant 2 to 6 views per video against the base generator's ~50, so "few distinct viewers" is the generator's engagement budget, not risk. Measured: it isolates 100% of violative views into 0.1% of the frame. Using it would produce a spectacular interval that measured nothing. Same defect class as 6.9, the eval ordinal that leaked its own answer key. | `597b77c` |
| 7.5 | **Allocation is optimal (Neyman), not proportional** | Proportional allocation, which the approved plan had declared sufficient | Barnett's Table 2B has the lowest-risk stratum holding 80% of views and receiving 52.5% of the sample. Measured on his own published population: optimal gives a standard error of 0.054 percentage points and proportional gives 0.070, while proportional stratification beats no stratification by under 1%. On a rare-event estimand essentially all of the benefit is in the allocation, so shipping proportional would have produced intervals 30% wider than the method being replicated. Cochran ch. 5 is the theory Barnett names. | `4dd0267` |
| 7.6 | **The allocation prior comes from a pilot sample's rater decisions, never from sealed labels** | (a) seed the Neyman variances from ground truth, used "only for allocation"; (b) skip optimal allocation for lack of a prior | Barnett describes YouTube revising sample sizes "based on actual VVR rates in those ranges over the 90 preceding days", so prior *measurements* are the source, not truth. (a) would optimise the design against answers the method does not have and would overstate achievable precision. `allocate_optimal` takes two mappings of numbers, asserted by signature and by a test that inverted ground truth leaves the allocation byte-identical. The pilot is discarded from the estimate it shaped. Saif's requirement. | Saif, this session; `4dd0267` |
| 7.7 | **`PRIOR_RATE_FLOOR` is a correctness requirement, not smoothing** | No floor | At this corpus's rate a 1,000-view pilot expects one violative view in total, so `p_h = 0` in every stratum is the *expected* outcome and Neyman's key collapses to 0/0. Without a floor the estimator would allocate zero views to strata and a stratum never sampled cannot contribute at all. The floor is the "educated guess" Barnett describes starting from. Its consequence is stated rather than hidden: an uninformative pilot degenerates optimal allocation to proportional, which is correct behaviour. | `4dd0267` |
| 7.8 | **D2 reports both expansion arms side by side, and arm B is flagged as not a VVR** | (a) report only the null arm; (b) report only the arm that moves; (c) widen the baseline estimand to make the arm work | Measured: the approved class-set expansion is *exactly* null on this corpus (0.0958% unchanged) because the classes it adds carry no views. That null is a true result about the generator and is reported with its explanation. Arm B, in which a video counts when it hosts a comment-spam-ring comment, moves the rate to 3.1097% and supplies the direction the exit checklist requires. Arm B changes the **attribution rule**, not the class set, and YouTube judges the video itself, so it is a policy-scope-question illustration and `is_faithful_vvr` is False for it in the type. Saif's decision. | Saif, this session; `635add7` |
| 7.9 | **A per-stratum degeneracy condition was added to the validity check** | Leave the four aggregate conditions | Found by running the D2 sample-size curve: at 14,000 of 18,780 views the interval collapsed to **zero width** while all four aggregate conditions passed. The optimal allocation had censused the middle stratum and the lowest returned no violative calls, so its `p(1-p)` contribution vanished. This is the Wald interval's known collapse at `p_hat = 0`, and the aggregate conditions cannot see it because in aggregate the sample did find violative views. Observing nothing in a stratum is not evidence the stratum holds nothing. Censused strata are excluded, because there the zero is real. | `635add7` |
| 7.10 | **matplotlib is a main dependency; curve data is byte-stable, PNGs are byte-identical in-environment only** | (a) dev-only extra with a data-only degraded path; (b) pin a version and assert PNG bytes against fixtures | D5's `report` verb renders at runtime, not only under test, so (a) would mean designing and testing a behaviour split for no gain. (b) claims a stability belonging to the pin rather than to the code and reddens the suite on every upgrade. The claim is stated at exactly its width: JSON/CSV identical across runs and machines and it is what a reader regenerates numbers from; two renders in one environment byte-identical; cross-version PNG stability explicitly not claimed. Saif's decision. | Saif, this session; `e8b31fc` |
| 7.11 | **The `NO_SCORE` window is seven days, and that is a choice rather than a finding** | 24 hours, which the source's "very close to the time that sampling was done" might suggest | Barnett's footnote 7 gives no number. On the seed-42 build the last publication falls 4.8 days before the last view, so a 24-hour window leaves the fifth stratum holding nothing and it would exist only on paper. Seven days puts 2 videos and 97 views in it. No label was consulted, and it is a parameter rather than a constant so another corpus can set its own. | `597b77c` |
| 7.13 | **Both Phase 4 traversal obligations are solved by one construction, not two branches** | (a) chain seeds, and separately special-case an empty pivot; (b) leave the strategy and report the saturation | A work list of `(pivot, entity)` pairs built from the pack in pack order, with hop `h` taking `work[h]`. It chains because the pack grows; it varies pivot kind because each entity contributes several; and an empty pivot needs no handling at all, because it adds nothing to the pack so `work[h+1]` is simply the next pair. A branch nobody has to remember to write cannot be forgotten. Pack order is what keeps `work[h]` stable as the pack grows, so sessions still replay. | `8480aec` |
| 7.14 | **The t02 recovery result is accepted as unmet rather than counted discharged because a different class moved** | Count the obligation met on the strength of T-06 growing | STEP-04 named `t02_chan_000_000` reaching past its first shell as the concrete target. It went 3 to 4 of 8 members and stayed flat across budgets, so that specific claim is not discharged and the STEP-04 Outcome says so. Recording a partial as a pass is how an obligation quietly stops constraining anything. Saif's decision, with the accompanying reading: the plateau is a bounded limit of a metadata-pivot strategy, the same shape as the structural recovery ceiling (4.9), not a defect. | Saif, this session; `8480aec` |
| 7.15 | **The analyst-minutes model is a TSPA-grounded sensitivity model, never a benchmarked measurement** | Cite a per-case handling-time figure from any platform or vendor report | There is no benchmark to cite. TSPA defines review time and then warns that it "may include time waiting in queues or going through automatic processes, or only the time when the reviewer is actively working on a specific review", and that "there is considerable industry variation in both precise definitions and naming conventions". DTSP assesses maturity on a five-level scale rather than publishing handling-time figures. A number lifted from one platform beside one from this workbench would not measure the same quantity. Saif's requirement. | Saif, this session; `680ea19` |
| 7.16 | **The minutes model's honest summary statistic is the break-even, not a delta** | Report "minutes saved" or a percentage improvement | A delta over assumed inputs is a property of the assumption table. The break-even, the assisted time at which a step stops contributing, converts "this saves time" into "this holds only if handling is under N minutes, which nobody measured". Enforced structurally: `MinutesResult` has no `minutes_saved` attribute, the assumptions table renders above the number and that ordering is asserted by index, and `BANNED_CAUSAL_PHRASES` is asserted absent from every rendering including the report. Prose drifts, and it drifts in the flattering direction. | `680ea19`, `a067097` |
| 7.17 | **`report --session` alone says the platform lens was "Not computed" rather than omitting it** | (a) require `--build`; (b) omit the section silently | STEP-07 D5 names only `--session`, and the workflow lens genuinely needs nothing else, so (a) would contradict the contract. (b) is worse: a report missing a whole lens without saying so lets a reader believe it covered more than it did. The section is present and states its own absence. | `c28161e` |
| 7.18 | **The report verb draws a pilot so the estimate uses optimal allocation** | Leave the default, which is proportional | Found by reading the first generated report: it said `allocation=proportional`. On seed 42 that is a 95% interval of 0.0445% to 0.1333% against 0.0720% to 0.1186% with the pilot, so the omission reported an interval half again as wide as the method being replicated gives. Pinned by a test on `allocation=optimal` rather than left to inspection. | `c28161e` |
| 7.19 | **The bootstrap's expected width ratio comes from the strata variance ratio, not the overall sampling fraction** | Keep `1 / sqrt(1 - f)`; or widen the tolerance until the observed ratios fit | `1 / sqrt(1 - f)` is only correct when every stratum shares one sampling fraction, and optimal allocation guarantees they do not: at 9,000 of 18,780 views the middle stratum sits near a 0.78 fraction while the others sit near 0.16, so its analytic variance nearly vanishes under its own FPC while the bootstrap keeps all of it. Predicted 1.386 against an observed 2.2 across three seeds, so the cross-check was reporting a disagreement that belonged entirely to the predictor. `sqrt(V_without_fpc / V_with_fpc)` is exact, reduces to the old form in the equal-fraction case, and tracks the observed ratio to within Monte Carlo noise. Widening the tolerance would have hidden a wrong formula behind a looser test. Found by Saif reading a generated report at phase close. | `af733e3` |
| 7.12 | **`test_this_phase_landed_the_handler_it_owed` was rewritten, not deleted or exempted** | (a) delete it; (b) leave `IMPLEMENTATION_PHASE` at 6 so it keeps passing | It asserted that the current phase owes exactly one handler, which became false by construction at phase 7 because STEP-07 adds no tool. (b) would make the phase constant lie about which STEP the build implements. (a) drops the guarantee that the countdown cannot pass vacuously. Restated over the finished countdown instead: steps 3..6 owe exactly one handler each, every declared tool executes, nothing is due after step 6. All three failure directions survive, including the new one of parking a tool past the deadline. | `659d14e` |

### What the corpus can and cannot support, measured

Recorded with its numbers because every claim the VVR lens makes is bounded by
it, and because the planning assumption it corrects was wrong in a specific way.

On the seed-42 scale-1 build the frame holds **18,780 views** and the true
baseline VVR is **0.0958%**, which is coincidentally close to the real
platform's published 0.16-0.20%. It is carried by **18 violative views**.

**Those 18 views come entirely from T02 and T07.** Every other class contributes
zero. The plan for this phase asserted the baseline would be "driven mainly by
T04 undisclosed synthetic media" on the strength of T04 being the one
video-level class whose name suggests it; measurement showed **T04 videos
receive no view events at all**. Only the two threat modules that plant VIEW
engagement put views on their own videos, and the base generator's views attach
only to base-population videos. The assumption was corrected by measuring before
building, not after.

Three consequences follow, and all three are limits rather than defects:

- **The realistic operating regime is large sampling fractions.** With 18
  violative views in 18,780, any small sample returns `p_hat = 0` and a
  degenerate interval. That makes the finite population correction load-bearing
  rather than decorative, and it is the opposite of YouTube's regime, where the
  sampling fraction is minuscule and the FPC is negligible.
- **The normal approximation is invalid at every realistic sample size here**
  and becomes valid only at a full census. Reported as a failed condition on
  every estimate rather than suppressed. This is precisely why 3.1 asks for a
  bootstrap cross-check.
- **Only two of the five strata hold any views**, because viewed videos take
  only two distinct observable profiles. No choice of cut points can manufacture
  strata the data does not contain, so the equal-quarters cuts are left untuned
  and what they yield is reported.

This is **not** a STEP-01 defect, on the same argument the Phase 6 finding
makes. STEP-01 promised byte-stable rebuilds and a passing leakage test and met
both. The view distribution is a limit this phase discovered by asking the data
for something STEP-01 never promised.

### A finding about panels, from running the estimator

A three-rater majority suppresses **independent** rater error quadratically: a
per-rater false-positive rate of 1% becomes an effective panel rate near
`3 * 0.01^2 = 0.0003`. Measured consequence at this corpus's rate: the same 99%
specificity that puts the truth outside a nominally 95% interval with one
reviewer leaves it comfortably inside with three.

This was expected to be a straightforward demonstration of the published
limitation and turned out to need a single reviewer to show it, which is why it
is recorded rather than quietly worked around. It cuts both ways, and the second
half is the part that matters: panels buy real robustness against independent
error and **nothing at all** against correlated error, such as a policy
misreading that a whole panel shares. Nothing in this phase models correlated
rater error, and the D2 bias curve inherits that limit.

### Future work: traversal enrichment (its own task, NOT STEP-07)

Recorded here as a task rather than a deferral, because it is scoped work with
a known blocker rather than something STEP-07 chose not to finish.

**Reaching the ring members a metadata pivot cannot see needs non-registration
pivot signals.** The traversing strategy recovers the shared-registration-linked
core and plateaus there, which is a property of the signals it can ask about
rather than of the budget it is given. The five remaining members of
`ring_t02_000` are connected by looser evidence: behavioural co-occurrence,
temporal proximity, weaker shared-attribute overlap. None of those is expressible
in the pivots the strategy currently uses.

`TEMPORAL_CORRELATION` is the vocabulary member that would supply the temporal
half, and it **cannot be proposed today**: it requires an `anchor_epoch_ms` and
no timestamp appears anywhere in the prompt the agent reads, so proposing it
would mean inventing a parameter rather than deriving one. That is the concrete
blocker, and it is a prompt-surface change rather than a strategy change.

Scope for whoever takes it: put a timestamp anchor in the evidence prompt so
`TEMPORAL_CORRELATION` becomes proposable, then measure whether behavioural and
temporal pivots reach members that registration metadata does not. Explicitly
**not** a STEP-07 blocker, and explicitly not a defect in the current strategy.

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
| `run-session`'s usage-error exit code | argparse's 2, contradicting its own documented table | 5, matching `verify-ledger` | Re-reading the exit-code contract after the phase closed; the collision STEP-02 removed had been reintroduced on a new subcommand |
| Session id reproducibility claim | "two runs of the same inputs comparable" | true within one build directory; **not** across rebuilds | Saif's re-run producing a different id, then measuring that `build.duckdb` is not byte-stable while the Parquet exports are (STEP-03 Honest Limits) |
| Session id reproducibility claim, again | true within one build directory only | true across rebuilds of the same seed and scale | STEP-04 deriving `dataset_digest` from the manifest's Parquet table hashes (4.11). The claim STEP-03 narrowed is now earned rather than restated |
| Session id uniqueness | analyst + dataset identifies a session | analyst + dataset + agent (+ case and subject for evidence) | The first evidence session run through the CLI came back carrying the triage session's id. One kind of session made the shorter derivation sufficient; two did not |
| `max_steps` as a usable budget | a turn may use the step `begin_turn` booked | the last step was refused by the model call's own re-check | The first multi-turn agent. Fixed with `require_step`, and pinned in both directions |
| Positional `ORDER BY` in the pivot templates | ordinals, reviewed and passed as safe | aliased projections, ordering by name | Saif's optional review note, taken up because the brittleness has a silent half that arity checking cannot see (4.5) |
| The recovery-saturation claim | "the stub exhausts its strategy in two hops" | the strategy never traverses at all | Saif reading `evidence_pack.json` at phase close (4.14) |
| The traversal claim, again | "the strategy never traverses and never chains" | it chains and varies pivot kind, but **plateaus at the ring core**: 3 of 8 members, all reachable through a shared registration value | The re-run on a real subject. The first diagnosis came from a session on a nonexistent subject, where the no-accounts branch fired every hop; a defect diagnosed from a degenerate input had been generalized into a claim about the algorithm |

---

## Carried into STEP-07: the central risk

Recorded here rather than only in the STEP-04 Outcome, because STEP-07 is the
phase that has to discharge it and this is the file its author will read first.

**The investigation does not traverse.** Found by Saif at phase close, by
reading `evidence_pack.json` rather than by any test. An evidence session on the
T-02 subject `t02_chan_003_000` ran 20 hops that were **all**
`pivot.account_link.v1` with **identical** parameters (`channel_id=t02_chan_003_000`,
`limit=25`, `min_comments=1`, the same `param_hash` every hop) and **all**
returned `row_count` 0, yielding a pack of 1 node and 0 edges.

Two defects, both STEP-07's:

- **(a)** The scripted strategy repeats one identical pivot instead of feeding
  entities found at hop N as seeds into hop N+1, so it never traverses.
- **(b)** It has no fallback to a different pivot kind when one returns empty,
  and here even hop 1 was empty.

**STEP-07's headline deliverable:** a strategy that varies pivot kind **and**
chains discovered entities as new seeds, validated on a subject whose first
pivot returns rows, recovering a measurable fraction of a planted network at 20
pivots versus 5. `test_recovery_saturates_before_the_smallest_reported_budget`
is written to fail when that lands, which is how the phase will know it
succeeded.

**A third defect, STEP-04's own, found while confirming the above.**
`t02_chan_003_000` does not exist in the seed-42 scale-1 build at all: only
rings `000` and `001` are planted. That is the mechanical cause of the twenty
empty hops, and it exposes something the design does not check. The
orchestrator accepts a seed subject that does not exist and produces a fully
valid audit trail for an investigation of nothing: exit 0, intact anchored
chain, 20 ledgered `HUMAN_DECISION` approvals, every pack through the ASSEMBLE
gate, provenance complete. Every governance claim held, and all of them were
about an entity that was never there. The pack's invariants are about internal
consistency and the gate validates the artifact rather than the world; nothing
checks that the analyst's chosen seed is real. Recorded and **not fixed** (4.15).
