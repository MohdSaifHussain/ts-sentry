# Trust & Safety Sentry v1.0.0

**A governed agentic workbench for Trust & Safety scaled-abuse analysis.**

Four narrow AI agents do bounded cognitive work under a deterministic control
layer, supervised by one human analyst who holds sole enforcement authority.
Out-of-mandate actions are not blocked at runtime; they are structurally
unexpressible.

## How this was built

**Build directed by Mohd Saif Hussain with Claude as AI collaborator, per the
documented STEP files.**

That framing is exact and is worth reading at exactly its width. The
architecture, the eight-phase contract, every decision recorded in
`docs/DECISIONS.md`, and every review stop were Saif's. The implementation was
Claude's, against binding per-phase specifications, with review stops that
halted the work until he had read the artifacts himself. The record of it is
[`docs/decisions/`](decisions/): eight STEP files, each with an Outcome section
naming what shipped, what deviated from the specification, and what was left
unmet.

The pattern that mattered most is recorded there too. **Three phase-close
defects were found by Saif executing something and reading the output, never by
a test**: STEP-04's non-traversing evidence pack, STEP-06's uninformative ranked
queue, and STEP-07's bootstrap width ratio computed from the wrong quantity. A
fourth was found the same way in this phase. The tests check that the arithmetic
is self-consistent; a human reading the output checks that it means anything.

## What is in it

| | |
|---|---|
| Agents | Triage (OBSERVE), Evidence (ASSEMBLE), Memo (RECOMMEND), Prompt-eval (OBSERVE) |
| CLI verbs | 8, offline by default, zero credentials, zero cost |
| Tests | 1,230 passing, 93.16% coverage against a 90% floor |
| Examples | 8 complete runs with full artifacts, 3 of them showing a refusal |
| Quickstart | 2.1 to 2.3 minutes, measured |

### The governance core

- **`Consequence.ENFORCE` is unreachable by any agent, at type level.** Proven
  by two independent mechanisms, neither subsuming the other.
- **Append-only hash-chained trajectory ledger**, with the chain head anchored
  in the session manifest at close.
- **Mandate-constrained dispatch**: an action outside the mandate is never
  executed, and the attempt is ledgered.
- **Agents never communicate directly.** Enforced by an import-graph test over
  the transitive closure, not by convention.
- **The sealed ground truth is reachable only by measurement code.**

### What the negative paths look like

Three of the eight examples exist to show a control firing, because a control
that has never fired is a control nobody has tested:

- `05-overclaim-refused`: 8 drafting attempts, 8 `gate_rejection` + 8
  `verification_fail`, memo held at DRAFT, and the artifacts say the stub was
  deliberately made to overclaim.
- `06-prompt-eval-refused`: exit 7, four per-class recall breaches plus a
  macro-F1 breach, decided on the confidence interval's lower bound.
- `08-firewall-real-comments`: a published research corpus refused outright on
  duplicate record ids.

## Verifying this release

Every published artifact is signed keylessly through Sigstore using the release
workflow's own OIDC identity. No PAT, no stored signing key.

```bash
# The published image. The digest this reports must match `docker pull`.
gh attestation verify oci://ghcr.io/mohdsaifhussain/ts-sentry:1.0.0 \
    --repo MohdSaifHussain/ts-sentry

# The distributions: SLSA build provenance.
gh attestation verify ts_sentry-1.0.0-py3-none-any.whl \
    --repo MohdSaifHussain/ts-sentry

# The same files carry a second, separate attestation over the SBOM.
gh attestation verify ts_sentry-1.0.0-py3-none-any.whl \
    --repo MohdSaifHussain/ts-sentry \
    --predicate-type https://cyclonedx.org/bom
```

**Two attestations per artifact, not one, and they are different predicate
types.** `gh attestation verify` defaults to SLSA provenance, so the SBOM
attestation needs `--predicate-type` to be found. A release candidate shipped
with only the SBOM attestation and no provenance, which made the first command
above return HTTP 404 against artifacts that looked signed; that was found by
running these commands against a real published candidate rather than by
reading the workflow.

A CycloneDX 1.6 SBOM ships as a release asset and is itself attested. The
container image separately carries its own SBOM and provenance attestations
produced by BuildKit and pushed to the registry.

**The tag is annotated, not GPG-signed.** Signing a tag in CI needs a private
key in a secret, which is the credential class this release deliberately avoids.
The verifiable signing on offer is the Sigstore attestations above.

## Honest limits

Mandatory and carried forward. The full set is in
[README.md](../README.md#honest-limits) and
[ARCHITECTURE.md section 12](../ARCHITECTURE.md). The ones that most change how
this release should be read:

- **Every agent's competence is untested.** Every model output in this
  repository came from a deterministic offline stub that cannot be persuaded and
  cannot reason. What is demonstrated is the pipeline, not the agent. That
  property holds whichever model sits behind the adapter, and nothing here is
  evidence about any of them.
- **Synthetic data**, except 1,956 public YouTube comments used for one firewall
  demonstration that states exactly what it does and does not show.
- **This system does not detect abuse.** The flagged queue is a stub standing in
  for the enterprise detector upstream, with no measured precision or recall.
- **Evidence recovery plateaus at 4 of 8** on the target STEP-04 named, flat
  from 5 to 20 pivots. **This is a recorded-unmet obligation**, not a solved
  problem. See the roadmap.
- **The VVR estimand is narrow**, carried by 18 violative views from two threat
  classes, and its interval covers sampling error only.
- **Analyst-minutes are a modelled break-even, not a measurement.** There is no
  published per-case review-time benchmark to cite.
- **Two code paths have never been run**: the live model adapter and the
  interactive reviewer. Both are marked.
- **Tail truncation is invisible to chain verification alone**, and an anchor is
  only as independent as its custody.
- **Framework alignment is evidenced by artifact, never certified.**

## Roadmap to v1.1

Deliberately out of scope for v1.0 and recorded rather than forgotten.

From the STEP-08 specification's section 4:

- Free-form pivot exploration behind a gate.
- A dashboard. Static HTML is v1.
- Concurrency. One analyst, one session at a time in v1.
- Automated prompt optimization with contamination review.

And the one carried from a phase that did not discharge it:

- **Traversal enrichment**, which is where the recorded-unmet t02 obligation
  lives. The evidence strategy recovers the shared-registration-linked core of
  `ring_t02_000` and plateaus at 4 of 8; the four it does not reach
  (`t02_chan_000_001`, `t02_chan_000_002`, `t02_vid_000_001`,
  `t02_vid_000_002`) are connected by behavioural co-occurrence and temporal
  proximity rather than by a shared registration value.

  **The blocker is named and concrete.** `TEMPORAL_CORRELATION` is the pivot
  that would supply the temporal half, and it cannot be proposed today because
  it requires an `anchor_epoch_ms` and no timestamp appears anywhere in the
  prompt the agent reads, so proposing it would mean inventing a parameter
  rather than deriving one.

  So the task is a **prompt-surface change, not a strategy change**: put a
  timestamp anchor in the evidence prompt, take it through the prompt wind
  tunnel as the first governed prompt change this project has made, then measure
  whether behavioural and temporal pivots reach members registration metadata
  does not. Doing it that way is the point: the wind tunnel exists and has never
  gated a change to a prompt a session actually consumes.

Also open, from the supply-chain posture: no upper bounds on dependencies, and
the `live` extra remains unpinned and unexercised.

## Licence and citation

MIT. Cite via [CITATION.cff](../CITATION.cff).

Third-party data: the UCI YouTube Spam Collection (Alberto & Lochter, 2015),
CC BY 4.0, DOI 10.24432/C58885, redistributed unmodified and attributed in
`examples/data/youtube-spam-collection/ATTRIBUTION.md`.
