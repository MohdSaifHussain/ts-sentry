# SPDX-License-Identifier: MIT
"""STEP-02 3.5 compile-check fixture: constructing a Mandate with ENFORCE
must not typecheck.

Deliberately **not** named ``test_*.py``. pytest never collects or executes
it (STEP-02 3.5: "a compile-check test file excluded from runtime"), but it
sits inside mypy's configured ``files = ["src", "tests"]``, so the existing
CI ``mypy --strict`` step checks it on every run.

Two complementary mechanisms guard the invariant, and neither subsumes the
other:

1. **In place, here.** The ``# type: ignore[arg-type]`` below suppresses a
   real error. ``--strict`` enables ``warn_unused_ignores``, so if passing
   ENFORCE ever *became* legal, the ignore would go unused and the CI mypy
   step turns red. This catches the invariant silently weakening.
2. **From outside**, in ``tests/test_enforce_unreachable.py``: that test
   copies this file with the ignore comment stripped, runs ``mypy --strict``
   on the copy, and asserts the ``arg-type`` error is reported. This catches
   this fixture being deleted, gutted, or made vacuous, which mechanism 1
   cannot see.

Mechanism 2 depends on the exact ignore-comment text below. Change it and
that test fails loudly rather than quietly stopping to mean anything.
"""

from typing import assert_type

from ts_sentry.governance.mandate import (
    AgentConsequence,
    AgentId,
    Consequence,
    Mandate,
    ToolId,
)


def _observe_mandate() -> Mandate:
    """A legal mandate. Its ceiling is statically the ENFORCE-free subset."""
    mandate = Mandate(
        agent_id=AgentId.TRIAGE,
        version="1.0.0",
        consequence_ceiling=Consequence.OBSERVE,
        allowed_tools=frozenset({ToolId.RANK_TRIAGE_QUEUE}),
        data_scopes=frozenset(),
        output_schema=dict,
        token_budget=1_000,
        max_steps=4,
    )
    assert_type(mandate.consequence_ceiling, AgentConsequence)
    return mandate


def _enforce_ceiling_must_not_typecheck() -> Mandate:
    """The forbidden construction. Never called; it exists to be rejected.

    Were it ever executed, ``Mandate.__post_init__`` would raise
    ``EnforceUnreachable`` - the runtime half of the same invariant.
    """
    return Mandate(
        agent_id=AgentId.TRIAGE,
        version="1.0.0",
        consequence_ceiling=Consequence.ENFORCE,  # type: ignore[arg-type]
        allowed_tools=frozenset({ToolId.RANK_TRIAGE_QUEUE}),
        data_scopes=frozenset(),
        output_schema=dict,
        token_budget=1_000,
        max_steps=4,
    )
