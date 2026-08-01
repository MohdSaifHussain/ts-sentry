# Contributing

Thanks for looking. Read this first, because this repository has a few
non-obvious rules and they are load-bearing rather than stylistic.

## What this project is

A portfolio and research project built to a documented eight-phase contract. The
build log is in [docs/decisions/](docs/decisions/) and the design authority is
[ARCHITECTURE.md](ARCHITECTURE.md). It is maintained by one person.

**The most useful contribution is a finding, not a feature.** Every phase-close
defect in this project's history was found by a human executing something and
reading the artifact, never by a test. If you run the quickstart and something
in an output does not mean what the documentation says it means, that is worth
more than a pull request adding a capability.

## The rule that governs everything else

> **The claim must be exactly as wide as the behaviour.**

`docs/DECISIONS.md` opens by warning against a decision log that manufactures
its own evidence, and several of its entries exist because a claim was found to
be wider than the behaviour and the **claim was narrowed rather than the
behaviour oversold**.

In practice this means a change is not finished when the tests pass. It is
finished when every sentence describing it is true at its stated width.

## Before you open a pull request

Everything below is enforced in CI, so running it locally only saves you a round
trip.

```bash
pip install -e ".[dev]"

ruff check src tests
ruff format --check src tests
mypy                      # --strict, configured in pyproject.toml
pytest --cov              # 90% floor, enforced
```

## Engineering standard, non-negotiable

These are not preferences. They are in [CLAUDE.md](CLAUDE.md) and
[ARCHITECTURE.md](ARCHITECTURE.md) section 9, and CI enforces most of them.

- Python 3.12+. `StrEnum` for all categoricals. Frozen slots dataclasses. PEP 695
  type aliases.
- Timezone-aware IST (`Asia/Kolkata`) timestamps, serialized ISO 8601.
- `mypy --strict` clean. `ruff` lint and format clean.
- **No dynamic SQL anywhere.** Parameterized query templates only. A test reads
  the SQL text and enforces this.
- **No bare `random`.** One seeded `numpy.random.Generator`, threaded explicitly.
- **No wall-clock reads outside an injected `Clock`.** A session must replay
  identically.
- **Offline-first.** The whole suite passes with no network, no credential and
  without the optional vendor package installed. Live mode is env-gated twice.

## Governance invariants, never weaken

These are test-enforced and a pull request that weakens one will be declined
regardless of how convenient it is:

- `Consequence.ENFORCE` is human-only. No `Mandate` may carry it; construction
  only through the `HumanSignature` path.
- Agents never communicate directly. All handoffs pass through the orchestrator.
- The ledger is append-only and hash-chained; writes only via the orchestrator
  token.
- `sealed._labels` is reachable only by measurement code. Agents and the
  orchestrator must not import it, enforced transitively by an import-graph test.

If a test blocks something you need, the answer is usually not to widen the test.
The one time an import-graph rule could have been widened, it was not, because
an agent holding its own verifier is an agent nobody is verifying.

## Official sources, not memory

When a change depends on a framework's, library's or standard's current
behaviour (API signatures, config keys, spec wording, version differences),
**consult the official documentation and cite the URL in the commit message.**

This is a standing rule because guessing has cost real defects here. DuckDB's
`TIMESTAMPTZ` stores only epoch microseconds and renders in the *reader's* time
zone, which would have made an intact ledger verify in IST and report a false
broken chain in a UTC CI runner. That was avoided by reading the docs before
writing the code.

If the official docs are unreachable, say so and ask rather than guessing.

## Tests

- `pytest` plus `hypothesis`. Properties have repeatedly earned their place here:
  the U+2028 fence escape, the floating-point monotonicity limit and three budget
  defects were all found by properties rather than by inspection.
- **Negative-path tests must assert a reason code, not just that something was
  rejected.** A suite checking only "this was rejected" passes if the gate
  rejects everything for the wrong reason, and refusals that cannot be counted by
  cause make the governance metrics meaningless. Assert a passing control too.
- **A limitation gets asserted as a passing test**, not described in prose, so
  the day it stops being true the suite fails and forces the claim to be
  rewritten rather than quietly outliving its own truth.

## Documentation style

- **No em-dashes** anywhere in docs or docstrings.
- Verified metrics only. If you state a number, it must come from a run you did.
- **Honest Limits sections are mandatory and carried forward.** Do not delete a
  limit because it is inconvenient. Narrow it if it has genuinely narrowed, and
  say what changed.

## Commits

[Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/):
`feat(scope):`, `fix(scope):`, `test(scope):`, `docs(scope):`.

Update the `Unreleased` section of [CHANGELOG.md](CHANGELOG.md) per
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/).

Keep diffs small. One deliverable per commit where feasible.

## Examples

The committed examples in [examples/](examples/) are regenerated by
`python examples/regenerate.py`. If you change anything that affects their
output, regenerate them and check that the numbers quoted in each `NOTES.md`
still come out of that directory. `tests/test_examples.py` checks the ones that
can be checked mechanically, but a NOTES file is prose and prose drifts.

Note that regeneration is **not** byte-reproducible and is not meant to be: only
`ranked_queue.json` is byte-identical across runs, because everything else
carries real timestamps.

## Security

See [SECURITY.md](SECURITY.md), which lists what is genuinely interesting to
attack and what is already documented as a limitation.

## Code of conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
