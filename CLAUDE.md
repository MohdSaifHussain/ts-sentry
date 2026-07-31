# CLAUDE.md - Trust & Safety Sentry

## Contract
- ARCHITECTURE.md is the design authority. docs/decisions/STEP-NN files are
  binding per-phase contracts. Never implement ahead of the current STEP.
- Standing rule: every implementation follows the top applicable standard;
  the STEP files name the governing standard per requirement. If a standard
  is ambiguous, stop and ask, do not guess.

## Engineering standard (non-negotiable)
- Python 3.12+ compatible. StrEnum for all categoricals. Frozen slots
  dataclasses. PEP 695 type aliases. Timezone-aware IST (Asia/Kolkata)
  timestamps, serialized ISO 8601.
- mypy --strict clean. ruff (lint + format) clean. pytest + hypothesis;
  coverage floor per pyproject.
- DuckDB for storage. No bare random: single seeded numpy Generator.
- No dynamic SQL anywhere. Parameterized query templates only.
- Offline-first: all tests pass with the deterministic stub adapter, no
  network. Live LLM mode is env-gated.
- Official sources only: when a decision depends on a framework's, library's,
  or standard's current behavior (API signatures, config keys, spec wording,
  version differences), consult the official documentation or the official
  repository via fetch before implementing. Never rely on memory or blog-tier
  sources for such decisions. Cite the consulted URL in the commit message or
  the decision note. If official docs are unreachable, say so and ask rather
  than guessing.

## Governance invariants (test-enforced, never weaken)
- Consequence.ENFORCE is human-only: no Mandate may carry it; construction
  only via HumanSignature factory.
- Agents never communicate directly; all handoffs via orchestrator.
- Ledger is append-only and hash-chained; writes only via orchestrator token.
- sealed._labels is reachable only by measurement code; agents and
  orchestrator must not import it (import-graph test).

## Process
- Conventional Commits 1.0.0: feat(scope), test(scope), docs(scope), etc.
- Keep a Changelog 1.1.0: update Unreleased each phase.
- Small diffs; one deliverable ID (D1, D2, ...) per commit where feasible.
- At each STEP's declared review stop, halt and wait for Saif's review.
- On phase completion: append an Outcome section to the STEP file (shipped,
  deviations + rationale, exit-checklist state). Do not tag releases before
  STEP-08.

## Documentation style
- No em-dashes anywhere in docs or docstrings. Verified metrics only. No
  inflated claims. Honest Limits sections are mandatory and carried forward.

## Windows notes
- Saif runs Windows CMD/PowerShell. LF/CRLF warnings are benign. UTF-8 in
  CMD can misrender; prefer PowerShell for file inspection.
