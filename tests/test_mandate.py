# SPDX-License-Identifier: MIT
"""STEP-02 D1: the Mandate model, its canonical hash, and ``validate``.

Covers STEP-02 3.1 (validation is pure and total, exhaustive over
Consequence) and the runtime half of the D2 ENFORCE invariant. The
type-level half lives in ``tests/typing/enforce_negative.py`` and
``tests/test_enforce_unreachable.py``.
"""

from dataclasses import replace
from typing import cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ts_sentry.governance.mandate import (
    AgentConsequence,
    AgentId,
    Consequence,
    EnforceUnreachable,
    Mandate,
    ProposedAction,
    RefusalCode,
    ToolId,
    Verdict,
    VerdictKind,
    _consequence_rank,  # private: the ENFORCE branch has no public caller by design
    mandate_hash,
    validate,
)
from ts_sentry.governance.scopes import DataScope

_AGENT_CONSEQUENCES: tuple[AgentConsequence, ...] = (
    Consequence.OBSERVE,
    Consequence.ASSEMBLE,
    Consequence.RECOMMEND,
)


class _OutputSchema:
    """Stand-in for a real agent output contract.

    Agent output schemas arrive with the agents themselves (STEP-03 D5
    onward). Phase 2 owns the Mandate field, not what goes in it, so the
    tests supply their own rather than the source tree shipping placeholder
    schemas nothing dispatches.
    """


def _mandate(**overrides: object) -> Mandate:
    fields: dict[str, object] = {
        "agent_id": AgentId.TRIAGE,
        "version": "1.0.0",
        "consequence_ceiling": Consequence.OBSERVE,
        "allowed_tools": frozenset({ToolId.RANK_TRIAGE_QUEUE}),
        "data_scopes": frozenset({DataScope.CHANNEL, DataScope.VIDEO}),
        "output_schema": _OutputSchema,
        "token_budget": 10_000,
        "max_steps": 8,
    }
    fields.update(overrides)
    return Mandate(**fields)  # type: ignore[arg-type]


def _action(**overrides: object) -> ProposedAction:
    fields: dict[str, object] = {
        "agent_id": AgentId.TRIAGE,
        "tool_id": ToolId.RANK_TRIAGE_QUEUE,
        "consequence": Consequence.OBSERVE,
        "requested_scopes": frozenset({DataScope.CHANNEL}),
    }
    fields.update(overrides)
    return ProposedAction(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Construction invariants
# --------------------------------------------------------------------------


@pytest.mark.parametrize("ceiling", _AGENT_CONSEQUENCES)
def test_every_agent_consequence_is_a_constructible_ceiling(ceiling: AgentConsequence) -> None:
    assert _mandate(consequence_ceiling=ceiling).consequence_ceiling is ceiling


def test_enforce_ceiling_is_refused_at_runtime() -> None:
    """The runtime half of the invariant.

    ``cast`` is a deliberate lie to the type checker: the whole point is to
    reach the constructor the way a caller without mypy in front of them
    would (a JSON round-trip, a config loader, ``dataclasses.replace``), and
    prove the guard still fires there.
    """
    forbidden = cast(AgentConsequence, Consequence.ENFORCE)
    with pytest.raises(EnforceUnreachable, match="human-only"):
        _mandate(consequence_ceiling=forbidden)


def test_enforce_ceiling_is_refused_through_dataclasses_replace() -> None:
    """``replace`` rebuilds through ``__init__``, so the guard must hold there too."""
    mandate = _mandate()
    with pytest.raises(EnforceUnreachable):
        replace(mandate, consequence_ceiling=cast(AgentConsequence, Consequence.ENFORCE))


def test_mandate_is_frozen() -> None:
    with pytest.raises(AttributeError):
        _mandate().token_budget = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    "version", ["1.0.0", "0.1.0", "10.20.30", "1.0.0-alpha.1", "1.0.0+build.5"]
)
def test_semver_versions_are_accepted(version: str) -> None:
    assert _mandate(version=version).version == version


@pytest.mark.parametrize("version", ["", "1", "1.0", "v1.0.0", "1.0.0.0", "01.0.0", "1.0.0-"])
def test_non_semver_versions_are_rejected(version: str) -> None:
    with pytest.raises(ValueError, match="SemVer"):
        _mandate(version=version)


@pytest.mark.parametrize("budget", [0, -1])
def test_token_budget_must_be_positive(budget: int) -> None:
    with pytest.raises(ValueError, match="token_budget"):
        _mandate(token_budget=budget)


@pytest.mark.parametrize("steps", [0, -1])
def test_max_steps_must_be_positive(steps: int) -> None:
    with pytest.raises(ValueError, match="max_steps"):
        _mandate(max_steps=steps)


def test_datascope_is_imported_not_redefined() -> None:
    """STEP-01 Outcome: ``DataScope`` stays in ``governance.scopes``.

    Guards against a future refactor quietly re-declaring it here, which
    would fork the allowlist and reopen the sealed-leakage hole the STEP-01
    red-team closed.
    """
    import ts_sentry.governance.mandate as mandate_module

    # vars() rather than attribute access: DataScope is deliberately absent
    # from mandate's __all__, because this module imports it, it does not
    # re-export it as its own.
    assert vars(mandate_module)["DataScope"] is DataScope
    assert DataScope.__module__ == "ts_sentry.governance.scopes"


# --------------------------------------------------------------------------
# Canonical hashing
# --------------------------------------------------------------------------


def test_hash_is_deterministic_across_separate_constructions() -> None:
    assert mandate_hash(_mandate()) == mandate_hash(_mandate())


def test_hash_is_a_sha256_hex_digest() -> None:
    digest = mandate_hash(_mandate())
    assert len(digest) == 64
    assert digest == digest.lower()
    int(digest, 16)


def test_version_is_under_the_hash_not_beside_it() -> None:
    """Confirmed requirement: two mandates identical except for ``version``
    must hash differently, so a version bump is a hash change rather than a
    label sitting next to an unchanged digest.
    """
    assert mandate_hash(_mandate(version="1.0.0")) != mandate_hash(_mandate(version="1.0.1"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("agent_id", AgentId.MEMO),
        ("consequence_ceiling", Consequence.RECOMMEND),
        ("allowed_tools", frozenset({ToolId.RUN_PROMPT_EVAL})),
        ("data_scopes", frozenset({DataScope.COMMENT})),
        ("token_budget", 9_999),
        ("max_steps", 7),
    ],
)
def test_every_field_participates_in_the_hash(field: str, value: object) -> None:
    assert mandate_hash(_mandate(**{field: value})) != mandate_hash(_mandate())


def test_output_schema_participates_in_the_hash() -> None:
    class _Other:
        pass

    assert mandate_hash(_mandate(output_schema=_Other)) != mandate_hash(_mandate())


def test_hash_ignores_frozenset_iteration_order() -> None:
    """Set iteration order varies between processes; the digest must not."""
    forward = _mandate(data_scopes=frozenset({DataScope.CHANNEL, DataScope.VIDEO}))
    reverse = _mandate(data_scopes=frozenset({DataScope.VIDEO, DataScope.CHANNEL}))
    assert mandate_hash(forward) == mandate_hash(reverse)


# --------------------------------------------------------------------------
# validate: the refusal matrix
# --------------------------------------------------------------------------


def test_action_within_mandate_is_allowed() -> None:
    verdict = validate(_action(), _mandate())
    assert verdict.kind is VerdictKind.ALLOW
    assert verdict.code is None
    assert verdict.allowed


def test_enforce_action_is_refused_as_human_only() -> None:
    verdict = validate(_action(consequence=Consequence.ENFORCE), _mandate())
    assert verdict.code is RefusalCode.ENFORCE_IS_HUMAN_ONLY


@pytest.mark.parametrize("ceiling", _AGENT_CONSEQUENCES)
def test_enforce_is_refused_under_every_possible_ceiling(ceiling: AgentConsequence) -> None:
    """Unconditional: there is no mandate under which ENFORCE is allowed."""
    verdict = validate(
        _action(consequence=Consequence.ENFORCE), _mandate(consequence_ceiling=ceiling)
    )
    assert verdict.code is RefusalCode.ENFORCE_IS_HUMAN_ONLY


def test_enforce_refusal_is_not_shadowed_by_other_violations() -> None:
    """An ENFORCE action that *also* mismatches agent, tool, and scope must
    still be recorded as ENFORCE_IS_HUMAN_ONLY.

    Refusal ordering is load-bearing, not cosmetic: if some incidental cause
    could shadow it, the ledger would under-count attempts on the invariant
    that matters most.
    """
    verdict = validate(
        _action(
            agent_id=AgentId.MEMO,
            tool_id=ToolId.RESOLVE_POLICY_CITATION,
            consequence=Consequence.ENFORCE,
            requested_scopes=frozenset({DataScope.INFRA_HINT}),
        ),
        _mandate(),
    )
    assert verdict.code is RefusalCode.ENFORCE_IS_HUMAN_ONLY


def test_agent_mismatch_is_refused() -> None:
    verdict = validate(_action(agent_id=AgentId.EVIDENCE), _mandate(agent_id=AgentId.TRIAGE))
    assert verdict.code is RefusalCode.AGENT_MISMATCH


def test_tool_outside_the_allowlist_is_refused() -> None:
    verdict = validate(_action(tool_id=ToolId.RUN_PARAMETERIZED_PIVOT), _mandate())
    assert verdict.code is RefusalCode.TOOL_NOT_ALLOWED


def test_scope_outside_the_allowlist_is_refused() -> None:
    verdict = validate(_action(requested_scopes=frozenset({DataScope.INFRA_HINT})), _mandate())
    assert verdict.code is RefusalCode.SCOPE_NOT_ALLOWED
    assert "infra_hint" in verdict.detail


def test_partially_allowed_scopes_are_refused_as_a_whole() -> None:
    """Allowlist semantics: one out-of-scope table refuses the action. There
    is no partial grant that silently drops the disallowed half.
    """
    verdict = validate(
        _action(requested_scopes=frozenset({DataScope.CHANNEL, DataScope.ACCOUNT_META})),
        _mandate(),
    )
    assert verdict.code is RefusalCode.SCOPE_NOT_ALLOWED


def test_consequence_above_the_ceiling_is_refused() -> None:
    verdict = validate(
        _action(consequence=Consequence.RECOMMEND),
        _mandate(consequence_ceiling=Consequence.OBSERVE),
    )
    assert verdict.code is RefusalCode.CONSEQUENCE_EXCEEDS_CEILING


def test_consequence_below_the_ceiling_is_allowed() -> None:
    verdict = validate(
        _action(consequence=Consequence.OBSERVE),
        _mandate(consequence_ceiling=Consequence.RECOMMEND),
    )
    assert verdict.allowed


def test_empty_scope_request_is_allowed() -> None:
    assert validate(_action(requested_scopes=frozenset()), _mandate()).allowed


# --------------------------------------------------------------------------
# Verdict shape
# --------------------------------------------------------------------------


def test_consequence_rank_is_a_strict_total_order_with_enforce_highest() -> None:
    """Pinned directly, because ``validate`` can never reach it.

    ``validate`` refuses ENFORCE before anything gets ranked, so the ENFORCE
    branch of ``_consequence_rank`` is unreachable through the public path.
    That is the invariant working, not a coverage gap. The order still has to
    be correct for D4, where a human-signed ENFORCE does get ranked, so it is
    asserted here rather than left to inference.
    """
    ordered = (Consequence.OBSERVE, Consequence.ASSEMBLE, Consequence.RECOMMEND)
    ranks = [_consequence_rank(level) for level in ordered]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ranks)
    assert _consequence_rank(Consequence.ENFORCE) > max(ranks)


def test_refuse_verdict_requires_a_code() -> None:
    with pytest.raises(ValueError, match="exactly one RefusalCode"):
        Verdict(kind=VerdictKind.REFUSE, code=None, detail="")


def test_allow_verdict_must_not_carry_a_code() -> None:
    with pytest.raises(ValueError, match="exactly one RefusalCode"):
        Verdict(kind=VerdictKind.ALLOW, code=RefusalCode.TOOL_NOT_ALLOWED, detail="")


# --------------------------------------------------------------------------
# STEP-02 3.1: validate is pure and total
# --------------------------------------------------------------------------

_SETTINGS = settings(max_examples=200, deadline=None)


@_SETTINGS
@given(
    action_agent=st.sampled_from(AgentId),
    mandate_agent=st.sampled_from(AgentId),
    tool=st.sampled_from(ToolId),
    allowed_tools=st.frozensets(st.sampled_from(ToolId)),
    action_consequence=st.sampled_from(Consequence),
    ceiling=st.sampled_from(_AGENT_CONSEQUENCES),
    requested=st.frozensets(st.sampled_from(DataScope)),
    allowed_scopes=st.frozensets(st.sampled_from(DataScope)),
)
def test_validate_is_total_and_sound(
    action_agent: AgentId,
    mandate_agent: AgentId,
    tool: ToolId,
    allowed_tools: frozenset[ToolId],
    action_consequence: Consequence,
    ceiling: AgentConsequence,
    requested: frozenset[DataScope],
    allowed_scopes: frozenset[DataScope],
) -> None:
    """Total: never raises, on any combination of the full input space.

    Sound: an ALLOW verdict implies every condition genuinely held, so the
    function cannot pass something through by falling off the end of its
    checks.
    """
    verdict = validate(
        ProposedAction(
            agent_id=action_agent,
            tool_id=tool,
            consequence=action_consequence,
            requested_scopes=requested,
        ),
        _mandate(
            agent_id=mandate_agent,
            consequence_ceiling=ceiling,
            allowed_tools=allowed_tools,
            data_scopes=allowed_scopes,
        ),
    )

    assert isinstance(verdict, Verdict)
    if verdict.allowed:
        assert action_consequence is not Consequence.ENFORCE
        assert action_agent is mandate_agent
        assert tool in allowed_tools
        assert requested <= allowed_scopes
    else:
        assert verdict.code is not None
        assert verdict.detail


@_SETTINGS
@given(consequence=st.sampled_from(Consequence), ceiling=st.sampled_from(_AGENT_CONSEQUENCES))
def test_enforce_is_never_allowed_for_any_agent(
    consequence: Consequence, ceiling: AgentConsequence
) -> None:
    """The headline invariant, stated as a property over the whole fleet."""
    for agent in AgentId:
        verdict = validate(
            ProposedAction(
                agent_id=agent,
                tool_id=ToolId.RANK_TRIAGE_QUEUE,
                consequence=consequence,
                requested_scopes=frozenset(),
            ),
            _mandate(
                agent_id=agent,
                consequence_ceiling=ceiling,
                allowed_tools=frozenset(ToolId),
                data_scopes=frozenset(DataScope),
            ),
        )
        if consequence is Consequence.ENFORCE:
            assert not verdict.allowed
            assert verdict.code is RefusalCode.ENFORCE_IS_HUMAN_ONLY
