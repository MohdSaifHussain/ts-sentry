# Model card

Documents this system's dependency on language models: what it uses them for,
what it does not, what has actually been run, and what has not.

Written to the practice of documenting model dependencies and disclosing the
offline stub. The disclosure that matters most is at the top rather than buried:
**no result in this repository was produced by a real language model.**

## What the model does here

| Agent | Model's contribution | Ceiling |
|---|---|---|
| Triage | One line of prose per case explaining why it ranks where it does, constrained to cite the score components that case carries | OBSERVE |
| Evidence | Chooses which reviewed query template runs next, and on which entity already in the pack | ASSEMBLE |
| Memo | Drafts sentences for an Article 17 style statement of reasons | RECOMMEND |
| Prompt-eval | Answers a threat-class classification prompt over the eval set | OBSERVE |

## What the model does not do

- **It never composes SQL.** Pivots are reviewed parameterized templates; the
  agent names one and supplies typed parameters that are bounds-checked, and any
  entity id it supplies must already be in the evidence pack.
- **It never produces a score.** Priority is computed deterministically from the
  dataset. The model explains a ranking it did not create, which is why the
  ranking is reproducible from the data alone.
- **It never reaches the enforcement path.** `Consequence.ENFORCE` is excluded
  at type level from every mandate. The documented claim is exactly "no agent
  action can reach the ENFORCE gate", not that the enum member is unmentionable.
- **It never sees case content as instructions.** All case content enters model
  context wrapped as inert data inside a fence whose token is a digest of the
  content it fences.
- **It never writes the automated-means disclosure.** A statement about how
  automated a decision was is worthless if the automated component composes it,
  so it is carried structurally.

## Model dependency

| | |
|---|---|
| Default adapter | **Deterministic offline stub.** No network, no credential, no vendor package. |
| Live adapter | Anthropic Messages API |
| Default live model id | `claude-opus-5`, overridable through `TS_SENTRY_LLM_MODEL` so a model change is a deployment decision rather than a code change |
| Vendor package | `anthropic`, unpinned, in the optional `live` extra, **not installed in CI** |
| Credential | `ANTHROPIC_API_KEY`. Its **value is never read by this repository**; only its presence is checked, and the vendor client reads it itself. |

The vendor client is imported **inside the call**, so an offline install never
loads it. A test parses every module with `ast` and fails on any module-scope
import of it.

### Live mode requires the intent expressed twice

`--llm-mode live` **and** `TS_SENTRY_LLM_MODE=live` in the environment. One
alone is refused. A shell alias or a stray script argument cannot start spending
money. `tests/conftest.py` strips all three variables suite-wide, so the
guarantee does not depend on anyone's shell.

Every session records which path produced it, inside the hash-chained
`SESSION_OPEN` entry and in the session manifest: `model_mode` (`stub` or
`live`) and, under the stub, `stub_mode`.

### API behaviour consulted rather than remembered

Three facts settled from the official API reference that memory would have got
wrong, and each changed the code:

- On `claude-opus-5` thinking is on by default, so `max_tokens` bounds thinking
  **and** text together.
- `temperature`, `top_p` and `top_k` are rejected with a 400 and are therefore
  not sent.
- A request can return HTTP 200 with `stop_reason == "refusal"`, so `content`
  must never be read before that is checked.

## The stub, and what it can and cannot demonstrate

The stub is deterministic and seeded: the same request produces the same
response and the same token accounting, forever. That is what makes a session
replay identically and what lets every number in this repository be regenerated.

It has four modes. Two are on the command line (`faithful`, `overclaim`) and two
are library-only (`transient`, `refuse`). They exist because a governance layer
whose failure paths have never fired is a governance layer nobody has tested.

**What the stub cannot do is be persuaded, and cannot reason.** Every claim in
this repository about agent behaviour is therefore a claim about the
*pipeline*, not about a model:

- That a proposal is validated before it executes.
- That an output is checked against resolvable evidence rather than trusted.
- That a refusal is recorded with a reason code that can be counted.
- That nothing becomes final without a human signature.

Those hold whichever model sits behind the adapter. Nothing here is evidence
about how any model performs at triage, investigation, drafting or
classification.

One consequence is stated in the STEP-05 Outcome rather than smoothed over: the
stub **does not revise**. Told exactly what is wrong with its draft, it re-sends
the same draft. The revise loop's success path is covered by a purpose-built
responder, not by the stub, and the difference is reported as a separate field.

## What has never been run

- **`LiveAdapter.complete` has never been executed.** It is marked `no-cover`.
  Covering it means either a network call in CI or a mock, and a mock would
  assert only that the code matches the shape its author imagined for the SDK.
  The live-mode smoke run is documented as a procedure and **has not been
  performed**. The `anthropic` version actually exercised is therefore unknown,
  which is why the extra carries no version floor: asserting a number nobody
  checked is the guess the official-sources rule exists to prevent.
- **The interactive reviewer has never been run.** Same treatment, same reason.

Both are real gaps, stated rather than implied.

## Prompts

Four, in a content-addressed registry where each file is named by the SHA-256 of
its own text: `prompts/`. Activation is an append-only pointer history, so
activating one version never rewrites another version's record, and a rollback
is another entry rather than an erasure.

One of the four, `classify.threat_class`, is **evaluated and gated but consumed
by no session**. The wind tunnel is real and the aircraft does not fly. That is
carried in Honest Limits rather than left to be noticed.

## Risks this design addresses, and what remains

| Risk | Control | Residual |
|---|---|---|
| Overclaiming: asserting facts not in evidence | Every claim sentence must resolve to an evidence-record id | The check is only as good as the pack's completeness |
| Prompt injection via case content | Input firewall: content-derived fence, instruction-stripping pass, no free-text-to-instruction path | **Detection is incomplete by construction.** Four adversarial fixtures are asserted as undetected. The load-bearing controls are structural, not the pattern set. |
| Excessive agency | Mandate-constrained dispatch; ENFORCE unreachable | Mandates are hand-written and could be written badly |
| Silent prompt drift | Regression gate on the interval's lower bound | Detects class collapse, not few-point drift, bounded by the eval set the generator can support |
| Unauditable behaviour | Hash-chained ledger with a stored anchor | Tail truncation is invisible to chain verification alone, and an anchor is only as independent as its custody |

## Cost

Building, testing and running a complete session costs **nothing** and requires
**no credential**. That is not a side effect; it is the default path and the CI
path.
