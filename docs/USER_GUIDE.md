# User guide

Per-verb reference. For the guided path from clone to report, read
[QUICKSTART.md](../QUICKSTART.md) first; for the design, read
[ARCHITECTURE.md](../ARCHITECTURE.md).

## Exit codes, once, for the whole CLI

Allocated across every subcommand so no number carries two meanings.

| Code | Meaning |
|---|---|
| `0` | Success |
| `2` | Data-quality gate failed. **`build-dataset` only.** |
| `3` | Sealed-label leakage self-check failed |
| `4` | Broken ledger chain |
| `5` | Input error: missing file, unreadable content, malformed value, unknown flag, or a malformed invocation |
| `6` | Chain links intact, head does not match the expected anchor |
| `7` | A prompt candidate was refused activation |

Two of these are worth understanding rather than just reading.

**Nothing except `build-dataset` ever exits `2`.** Argparse exits 2 on a usage
error and 2 means "quality gate failed" here, so a mistyped flag would have been
indistinguishable from failed data. Usage errors are translated to `5`
everywhere else. This was found by CI on the pinned Python 3.12: argparse
classifies a dash-prefixed option value differently across versions, so one
input exited 5 on 3.14 and 2 on 3.12. It was fixed in the contract rather than
in the test.

**`7` is a governance outcome, not an error.** A refused prompt means the gate
did its job. It prints to stdout like any other result, and the nonzero code is
what a script reads.

---

## Running the published container

```bash
docker pull ghcr.io/mohdsaifhussain/ts-sentry:1.0.0
```

The image is the CLI. It runs `ts-sentry` as its entrypoint, so a verb and its
flags go straight on the end:

```bash
docker run --rm ghcr.io/mohdsaifhussain/ts-sentry:1.0.0 --help
```

The working directory inside the image is `/work`, so mount the directory you
want artifacts written into there.

### It runs as uid 10001, and that has one consequence you will hit

The image runs as a non-root user (`analyst`, uid **10001**), because nothing
it does needs root. A bind mount carries the **host's** ownership through
unchanged, so if the directory you mount is owned by someone else, the
container cannot write into it and you get:

```
PermissionError: [Errno 13] Permission denied: 'build'
```

That is the design working, not a fault. On Linux, give the mount to the
container's uid first:

```bash
mkdir -p work && sudo chown 10001:10001 work

docker run --rm --network none -v "$PWD/work:/work" \
    ghcr.io/mohdsaifhussain/ts-sentry:1.0.0 build-dataset --seed 42 --scale 1 --out build
```

Or run as yourself instead, if you would rather own the output:

```bash
docker run --rm --network none --user "$(id -u):$(id -g)" -v "$PWD/work:/work" \
    ghcr.io/mohdsaifhussain/ts-sentry:1.0.0 build-dataset --seed 42 --scale 1 --out build
```

On Docker Desktop for Windows and macOS this rarely comes up, because those
mounts are permissive. The requirement is real on Linux, and it is where CI
runs.

### `--network none` is the point

```bash
docker run --rm --network none -v "$PWD/work:/work" \
    ghcr.io/mohdsaifhussain/ts-sentry:1.0.0 run-session --agent triage \
    --seed-dataset build --out session
```

Every verb except `fetch-policies` runs with networking switched off entirely.
The hashed policy corpus, the prompt registry and the eval set ship inside the
image, so citations resolve and prompts load without reaching anywhere.

### What is not in the image

The examples, the tests and the docs. A reader gets those from the repository;
5 MB of ledgers and CSVs has no business in a runtime image. The image also
carries no git, no compiler and no `uv`: the build stage has them and the
runtime stage does not.

One consequence is visible in the artifacts. `git_sha` is a provenance stamp
taken by shelling out to git, and inside the image there is no git, so manifests
record `"git_sha": "unknown"` rather than failing. A manifest that silently
dropped the field would be worse than one that says it could not take it.

### Verifying what you pulled

```bash
gh attestation verify oci://ghcr.io/mohdsaifhussain/ts-sentry:1.0.0 \
    --repo MohdSaifHussain/ts-sentry --format json
```

The digest it reports must match what `docker pull` printed. That
correspondence is the whole point: it is what proves the attestation describes
the image you actually have.

---

## `ts-sentry build-dataset`

Builds the seeded synthetic platform plus the sealed ground truth.

```bash
ts-sentry build-dataset --seed 42 --scale 1 [--out DIR] [--quality-thresholds PATH]
```

| Flag | Required | Meaning |
|---|---|---|
| `--seed N` | yes | Seeds the single `numpy.random.Generator` driving the whole build. Same seed and scale gives byte-identical Parquet exports. |
| `--scale S` | yes | Integer multiplier on the base population size. |
| `--out DIR` | no | Output directory. Defaults to `./build/`. |
| `--quality-thresholds PATH` | no | JSON overriding one or more of `completeness`/`uniqueness`/`validity`/`consistency` (percent). Omitted keys keep their defaults. |

Writes `build.duckdb`, Parquet exports per table, `build_manifest.json`, and the
sealed labels under their own segregated export path.

**The abuse budget does not scale.** Threat entities per class are 4 to 12 and
are invariant to `--scale`; only the benign population grows. That is a property
of the generator, it bounds what the eval set can resolve, and it is recorded in
DECISIONS under Phase 6 with the measurements.

**Two data-quality dimensions are deliberately not gated:**

- *Timeliness* is reported but never gated. It is a wall-clock-relative recency
  score, and this dataset's build window is fixed historical for
  reproducibility, so it scores 0% by construction forever on a dimension no
  improvement could fix.
- *Accuracy* has no percentage in the profiler by design ("no tool can measure
  accuracy without an authoritative source"). It is gated instead by reconciling
  against `sealed._labels` for zero orphans in either direction.

---

## `ts-sentry run-session`

Opens a session, runs one agent turn under a mandate, closes with an anchored
manifest, and writes the artifacts.

```bash
ts-sentry run-session --agent triage|evidence|memo --seed-dataset PATH [--out DIR]
    [--analyst-id ID] [--llm-mode stub|live] [--stub-mode faithful|overclaim]
    [--seed N] [--session-id ID] [--limit N]
    [--case ID] [--subject ID] [--review scripted|interactive] [--max-hops N]
    [--pack PATH] [--policies DIR] [--memo-id ID] [--max-attempts N]
```

### Common flags

| Flag | Meaning |
|---|---|
| `--agent` | `triage`, `evidence` or `memo`. Prompt evaluation has its own verb. |
| `--seed-dataset PATH` | A `build-dataset` output directory, or the `build.duckdb` inside it. Opened **read-only** for every agent: an ASSEMBLE ceiling is authority to assemble evidence, not to change it. Requires `build_manifest.json` beside the store, with no fallback. |
| `--out DIR` | Where artifacts land. Defaults to `session`. |
| `--llm-mode` | `stub` (default) or `live`. Live also requires `TS_SENTRY_LLM_MODE=live`. |
| `--stub-mode` | `faithful` (default) or `overclaim`. See below. |
| `--session-id ID` | Override the derived id. Rarely wanted: the derived id is a function of the inputs and reads no clock. |

### `--stub-mode`, and why it is in the artifacts

`overclaim` makes the agent cite an evidence id no pack carries, so the
consequence gate refuses the output and ledgers the refusal. It is how the
governance layer's failure path gets demonstrated on a real artifact rather than
only in a test.

The chosen mode is **provenance, not a hidden switch**. It is written into the
hash-chained `SESSION_OPEN` entry and stamped in `session_manifest.json`, both
rendered by one function so they cannot disagree, and it is read off the adapter
that actually served the calls rather than declared alongside it. An overclaim
session is self-identifying and cannot be presented as a faithful run. Combining
it with `--llm-mode live` is refused rather than ignored, because there is no
stub to put in a mode.

### `--agent triage`

Artifacts: `ledger.jsonl`, `ledger.duckdb`, `ranked_queue.json`,
`session_events.json`, `session_manifest.json`.

`ranked_queue.json` carries one row per case with its full component vector, the
weighted priority, the subject, and the model's rationale if it passed
verification. A rationale may cite only that row's own component ids; anything
else is rejected, the rejection is ledgered as `VERIFICATION_FAIL`, and the row
keeps its deterministic score. **Losing the explanation does not lose the
work.**

Severity is a heuristic stand-in with no measured precision or recall. It must
never be read as detection performance.

### `--agent evidence`

Requires `--subject`. Optional: `--case` (default `case-0000`), `--review`,
`--max-hops` (defaults to the mandate's `max_steps`, which is 20).

Artifacts add `evidence_pack.json` and `evidence_graph.graphml`.

The agent proposes a pivot from a vocabulary of five; the orchestrator validates
it; the analyst approves or rejects; only then does a reviewed parameterized
query run. Every hop is a ledgered `HUMAN_DECISION` carrying `reviewer_kind`
**inside the hashed payload**, so a scripted stand-in can never be rendered as a
human decision.

`--subject` must exist in the dataset. That guard runs before the output
directory is created, because a refusal after opening would already have written
the chain, manifest and anchor it exists to prevent. Without it the system
produces a fully valid audit trail for an investigation of nothing: exit 0,
intact anchored chain, every pack through the gate, every claim true, all of
them about an entity that was never there.

`--review interactive` prompts a real person. **It is written and has never been
run.**

### `--agent memo`

Requires `--pack` naming an `evidence_pack.json`. Optional: `--policies`
(default `policies`), `--memo-id`, `--max-attempts`.

Artifacts: `memo.json`, `memo.md`, `memo.html`, plus the usual ledger set.

The memo is a DSA Article 17 style statement of reasons. Every factual sentence
must resolve to an evidence-record id the pack carries; every policy citation
must resolve to a real anchor in the hashed corpus, quote at least four words,
and quote them as a contiguous word sequence rather than a substring. The memo
stays DRAFT and carries an AI-DRAFT watermark until signed. The watermark has no
suppression parameter, because a label that can be switched off is a label that
will be switched off at the one moment it matters.

The memo agent is granted **no data scopes at all**. It reaches no platform
table; it works from an accepted pack and the hashed corpus.

---

## `ts-sentry sign-memo`

The human signature path. The only route to a final memo.

```bash
ts-sentry sign-memo SESSION_DIR --analyst-id ID --pack PATH
    [--policies DIR] [--decision approve_enforcement|...]
```

Only an approval finalizes a memo. A rejection or deferral is a real governance
event and does not produce a signed memo. On approval the exports re-render
without the watermark and `memo_signature.json` is written.

**A signature proves integrity, not identity.** It binds five fields together
and shows they have not drifted apart. It does not authenticate the analyst.

The RECOMMEND gate refuses a memo that is already SIGNED. Re-gating answers the
wrong question: a signed memo is trustworthy because its digest recomputes and
its signature verifies, and accepting one back into the agent path would let it
emerge with a fresh `VERIFICATION_PASS` that says nothing about the signature.

---

## `ts-sentry eval-prompts`

Evaluates a candidate prompt against the incumbent and gates its activation.

```bash
ts-sentry eval-prompts --candidate DIGEST --out DIR
    [--registry DIR] [--evals DIR] [--analyst-id ID] [--llm-mode stub|live]
    [--seed N] [--session-id ID]
```

`--candidate` takes the **full 64-character content digest** of a prompt version
present in `--registry` (default `prompts`). Artifacts: `eval_report.md`,
`eval_report.json`, and the ledger set.

Exit `0` if the candidate may be activated, **`7`** if the gate refused it. A
refusal names the classes and the numbers behind them.

**The gate reads the confidence interval's lower bound, not the point
estimate.** Activation requires evidence of non-regression, not absence of
evidence of regression, so a candidate whose interval is wide is refused even
when its point estimate looks fine. That cost is accepted rather than tuned
away.

Two breach codes, deliberately separate: `RECALL_REGRESSION` says the candidate
is measurably worse; `REGRESSION_NOT_EXCLUDED` says the eval set cannot tell. On
this project's data the second is the common case and is a fact about the
generator, so reporting it as the first would blame a prompt for the eval set's
resolution.

`--out` must not already exist as a non-empty directory. A session writes its
own directory, and overwriting one would destroy the audit trail it holds.

---

## `ts-sentry report`

The measurement report. Two lenses.

```bash
ts-sentry report --session DIR --out DIR
    [--build DIR] [--policies DIR] [--seed N] [--sample-size N] [--cases N]
```

Twelve artifacts: `report.md`, `report.html`, `report.json`, and three
sensitivity curves each as CSV, JSON and PNG.

`--build` is optional. Without it the platform lens says it was **not
computed**, rather than being omitted silently: a report missing a whole lens
without saying so lets a reader believe it covered more than it did.

Ten honest limits are carried in the report source and asserted entry by entry
into every rendering, so a rendering cannot drop one.

**The curve data (CSV and JSON) is byte-stable across runs and machines**, and
it is what a reader regenerates numbers from. Two PNG renders in one environment
are byte-identical; cross-version PNG stability is explicitly not claimed.

---

## `ts-sentry verify-ledger`

Recomputes a hash chain and reports the first broken link, plus the chain head.

```bash
ts-sentry verify-ledger PATH [--expect-head COUNT:HASH | --expect-head-from MANIFEST]
```

`.jsonl` verifies an exported chain; `.duckdb` verifies the stored table. Both
readers feed one shared verification function, so an export and the store it
came from cannot disagree.

Output always reports `entries` and `head`, whether the chain verifies or not.
Chain integrity is checked **before** the head comparison, because a broken
chain makes any head claim meaningless.

### What `--expect-head` is, and what it is not

Chain verification detects modification, reordering and interior deletion. It
**cannot** detect entries removed from the end: what remains is a shorter chain
whose every link still recomputes, indistinguishable from a session that ended
earlier. This is asserted by a passing test rather than only documented.

`--expect-head` is a comparison verb, not an anchor system. It compares against
an expectation the caller already holds. `--expect-head-from` reads that
expectation out of a session manifest, which records it at `SESSION_CLOSE`.

**An anchor is only as independent as its custody.** A manifest sitting next to
the ledger it describes can be rewritten by anyone who can truncate that ledger,
so co-located files catch accidents and partial tampering, not a determined
editor with write access to the whole directory. The anchor becomes a real
control when a copy is held where the ledger's writer cannot reach it. Both
halves are asserted, including the test showing a rewritten manifest agreeing
with a truncated ledger.

---

## `ts-sentry fetch-policies`

Rebuilds the hashed policy corpus from its public sources.

```bash
ts-sentry fetch-policies [--out DIR]
```

**The only verb that uses the network, and CI never runs it.** The corpus is
committed, so citations resolve offline.

Clause-level text is committed rather than whole pages: memos quote at most 15
words, so redistributing three whole policy pages would ship far more than
citation resolution needs.

`content_digest` over the committed clauses is the citation identity;
`retrieval_sha256` is provenance only. That split was forced by a measurement:
two fetches of one page in a single process returned different digests, because
of a CSP nonce regenerated per request. A raw-byte digest changes whether or not
any policy changed, so it can never answer "has this policy changed". The clause
text is stable; all 14 substantive sections came back byte-identical.

A re-fetch is build-time provenance, not a session action, so it is not a ledger
event type. `SESSION_OPEN` binds `corpus_version` and `corpus_sha256` instead,
which ties every memo permanently to the corpus state its citations were checked
against.
