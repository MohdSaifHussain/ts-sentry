## What this changes

<!-- One paragraph. What is different after this than before? -->

## Why

<!-- The problem, not the solution. If this is a claim-accuracy fix, quote the
     claim and say what the true width is. -->

## The claim, at its true width

<!-- The governing rule of this repository: the claim must be exactly as wide as
     the behaviour. State what this change lets the project say, and what it
     deliberately does NOT let it say. -->

## Checks

- [ ] `ruff check src tests` and `ruff format --check src tests` clean
- [ ] `mypy` clean (strict)
- [ ] `pytest --cov` green, coverage at or above the 90% floor
- [ ] No em-dashes in any documentation or docstring touched here
- [ ] Any number stated in prose comes from a run I did, not from an earlier phase's notes
- [ ] `CHANGELOG.md` Unreleased updated
- [ ] Official docs consulted and the URL cited in the commit message, if this depends on any third-party behaviour

## Governance invariants

- [ ] No `Mandate` can carry `Consequence.ENFORCE`
- [ ] No agent-to-agent path introduced
- [ ] Ledger stays append-only, written only via the orchestrator token
- [ ] Nothing outside measurement code can reach `sealed._labels`
- [ ] No dynamic SQL

<!-- If you weakened a test to make this pass, say so here and explain why the
     test was wrong rather than the code. That is sometimes the right answer and
     it has happened before, but it needs to be visible. -->

## Examples

- [ ] Not affected, or regenerated with `python examples/regenerate.py` and the
      numbers quoted in each `NOTES.md` re-checked against its own directory
