# SPDX-License-Identifier: MIT
"""STEP-02 D4: the consequence-gate pipeline.

Covers STEP-02 3.3: structured failures rather than exceptions, and
rejections ledgered as VERIFICATION_FAIL + GATE_REJECTION.

``guard_scope_request`` is exercised here for its gate mechanics (refusal
codes, ledger entry, the granted path writing nothing). The leakage
guarantee it serves, across every agent mandate and every sealed name
variant, lives in ``tests/test_scope_leakage.py`` alongside the STEP-01
allowlist layer it completes.
"""

from datetime import datetime

import duckdb
import pytest

from ts_sentry.data.tz import IST
from ts_sentry.governance.gates import (
    FailureCode,
    GateChecks,
    GateDecision,
    GateFailure,
    GateOutcome,
    guard_scope_request,
    run_gate,
)
from ts_sentry.governance.ledger import EventType, Ledger, OrchestratorToken
from ts_sentry.governance.mandate import (
    AgentId,
    Consequence,
    Mandate,
    RefusalCode,
    ToolId,
)
from ts_sentry.governance.scopes import DataScope
from ts_sentry.governance.signature import Decision, sign

_TOKEN = OrchestratorToken(session_id="gate-session")
_MANDATE_HASH = "1" * 64
_TS = datetime(2026, 7, 31, 14, 30, tzinfo=IST)
_SUBJECT = "b" * 64


class _OutputSchema:
    pass


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


def _passing(artifact: object, /) -> tuple[GateFailure, ...]:
    return ()


def _failing(artifact: object, /) -> tuple[GateFailure, ...]:
    return (GateFailure(code=FailureCode.REFERENTIAL_INTEGRITY, detail="edge resolves to nothing"),)


def _exploding(artifact: object, /) -> tuple[GateFailure, ...]:
    raise RuntimeError("checker is broken")


_ALL_PASS = GateChecks(assemble=_passing, recommend=_passing)
_ALL_FAIL = GateChecks(assemble=_failing, recommend=_failing)


@pytest.fixture
def ledger() -> Ledger:
    return Ledger(duckdb.connect(":memory:"))


def _run(
    ledger: Ledger,
    consequence: Consequence,
    checks: GateChecks = _ALL_PASS,
    **overrides: object,
) -> GateOutcome:
    kwargs: dict[str, object] = {
        "timestamp_ist": _TS,
        "agent_id": AgentId.TRIAGE,
        "mandate_hash": _MANDATE_HASH,
        "consequence": consequence,
        "artifact": object(),
        "checks": checks,
    }
    kwargs.update(overrides)
    return run_gate(ledger, _TOKEN, **kwargs)  # type: ignore[arg-type]


def _events(outcome: GateOutcome) -> list[EventType]:
    return [entry.event_type for entry in outcome.ledgered]


# --------------------------------------------------------------------------
# OBSERVE
# --------------------------------------------------------------------------


def test_observe_is_auto_approved_and_ledgered(ledger: Ledger) -> None:
    outcome = _run(ledger, Consequence.OBSERVE)
    assert outcome.accepted
    assert outcome.failures == ()
    assert _events(outcome) == [EventType.VERIFICATION_PASS]


def test_observe_does_not_consult_any_checker(ledger: Ledger) -> None:
    """OBSERVE is read-only analysis; there is nothing to validate."""
    assert _run(ledger, Consequence.OBSERVE, _ALL_FAIL).accepted


# --------------------------------------------------------------------------
# ASSEMBLE and RECOMMEND
# --------------------------------------------------------------------------


@pytest.mark.parametrize("consequence", [Consequence.ASSEMBLE, Consequence.RECOMMEND])
def test_passing_checks_accept_and_ledger_a_pass(ledger: Ledger, consequence: Consequence) -> None:
    outcome = _run(ledger, consequence)
    assert outcome.accepted
    assert _events(outcome) == [EventType.VERIFICATION_PASS]


@pytest.mark.parametrize("consequence", [Consequence.ASSEMBLE, Consequence.RECOMMEND])
def test_failing_checks_reject_with_both_events(ledger: Ledger, consequence: Consequence) -> None:
    """STEP-02 3.3: failures produce VERIFICATION_FAIL + GATE_REJECTION."""
    outcome = _run(ledger, consequence, _ALL_FAIL)
    assert not outcome.accepted
    assert outcome.decision is GateDecision.REJECTED
    assert outcome.failures[0].code is FailureCode.REFERENTIAL_INTEGRITY
    assert _events(outcome) == [EventType.VERIFICATION_FAIL, EventType.GATE_REJECTION]


def test_assemble_and_recommend_use_their_own_checker(ledger: Ledger) -> None:
    """A gate must not consult the wrong validator."""
    split = GateChecks(assemble=_failing, recommend=_passing)
    assert not _run(ledger, Consequence.ASSEMBLE, split).accepted
    assert _run(ledger, Consequence.RECOMMEND, split).accepted


# --------------------------------------------------------------------------
# Fail-closed
# --------------------------------------------------------------------------


def test_a_crashing_checker_rejects_rather_than_propagating(ledger: Ledger) -> None:
    """A broken validator must never yield an accepted artifact, and must
    not skip the ledger write on its way out."""
    outcome = _run(ledger, Consequence.ASSEMBLE, GateChecks(_exploding, _passing))

    assert not outcome.accepted
    assert outcome.failures[0].code is FailureCode.CHECKER_ERROR
    assert "RuntimeError" in outcome.failures[0].detail
    assert _events(outcome) == [EventType.VERIFICATION_FAIL, EventType.GATE_REJECTION]


@pytest.mark.parametrize("consequence", list(Consequence))
def test_the_gate_never_raises(ledger: Ledger, consequence: Consequence) -> None:
    """STEP-02 3.3: structured failure objects, never exceptions to the caller."""
    outcome = _run(ledger, consequence, GateChecks(_exploding, _exploding))
    assert isinstance(outcome, GateOutcome)


# --------------------------------------------------------------------------
# ENFORCE
# --------------------------------------------------------------------------


def test_unsigned_enforce_is_refused_as_a_mandate_violation(ledger: Ledger) -> None:
    """Nothing was verified and failed here; something reached for a level it
    may never reach, so it is recorded as an attempt rather than a failure."""
    outcome = _run(ledger, Consequence.ENFORCE)

    assert not outcome.accepted
    assert outcome.failures[0].code is FailureCode.ENFORCE_REQUIRES_HUMAN_SIGNATURE
    assert _events(outcome) == [
        EventType.MANDATE_VIOLATION_ATTEMPT,
        EventType.GATE_REJECTION,
    ]


@pytest.mark.parametrize("decision", [Decision.REJECT, Decision.DEFER])
def test_a_declining_signature_does_not_open_the_enforce_gate(
    ledger: Ledger, decision: Decision
) -> None:
    signature = sign("saif", decision, _SUBJECT, _TS)
    outcome = _run(ledger, Consequence.ENFORCE, signature=signature)

    assert not outcome.accepted
    assert outcome.failures[0].code is FailureCode.ENFORCE_REQUIRES_HUMAN_SIGNATURE
    assert _events(outcome) == [EventType.VERIFICATION_FAIL, EventType.GATE_REJECTION]


def test_an_approving_signature_opens_the_enforce_gate(ledger: Ledger) -> None:
    """The only accepting path, and it records a person's decision rather
    than a machine's check."""
    signature = sign("saif", Decision.APPROVE_ENFORCEMENT, _SUBJECT, _TS)
    outcome = _run(ledger, Consequence.ENFORCE, signature=signature)

    assert outcome.accepted
    assert _events(outcome) == [EventType.HUMAN_DECISION]


def test_enforce_ignores_the_checkers_entirely(ledger: Ledger) -> None:
    """No checker can substitute for a human signature."""
    assert not _run(ledger, Consequence.ENFORCE, _ALL_PASS).accepted


# --------------------------------------------------------------------------
# Ledger integrity across gate activity
# --------------------------------------------------------------------------


def test_the_chain_stays_intact_across_mixed_gate_outcomes(ledger: Ledger) -> None:
    _run(ledger, Consequence.OBSERVE)
    _run(ledger, Consequence.ASSEMBLE, _ALL_FAIL)
    _run(ledger, Consequence.ENFORCE)
    _run(ledger, Consequence.RECOMMEND)

    result = ledger.verify()
    assert result.intact
    assert result.entries_checked == 6


def test_outcome_rejects_an_inconsistent_shape() -> None:
    with pytest.raises(ValueError, match="ACCEPTED outcome carries no failures"):
        GateOutcome(
            decision=GateDecision.ACCEPTED,
            consequence=Consequence.OBSERVE,
            failures=(GateFailure(FailureCode.SCHEMA_INVALID, "x"),),
            ledgered=(),
        )


# --------------------------------------------------------------------------
# STEP-02 3.5: sealed-scope resolution is refused and ledgered
# --------------------------------------------------------------------------


def test_sealed_scope_request_is_refused_and_ledgered(ledger: Ledger) -> None:
    """The STEP-02 half of the leakage guarantee.

    STEP-01 proved the allowlist denies the name; this proves the attempt is
    recorded as MANDATE_VIOLATION_ATTEMPT rather than refused silently.
    """
    result = guard_scope_request(
        ledger,
        _TOKEN,
        timestamp_ist=_TS,
        agent_id=AgentId.TRIAGE,
        mandate=_mandate(),
        mandate_hash=_MANDATE_HASH,
        requested_name="sealed._labels",
    )

    assert not result.granted
    assert result.scope is None
    assert result.code is RefusalCode.SCOPE_NOT_ALLOWED
    assert result.ledgered is not None
    assert result.ledgered.event_type is EventType.MANDATE_VIOLATION_ATTEMPT
    assert ledger.verify().intact


def test_a_real_scope_outside_the_mandate_is_refused_and_ledgered(ledger: Ledger) -> None:
    """Two independent refusals: the allowlist, then the mandate. This is the
    second one, on a table that genuinely exists."""
    result = guard_scope_request(
        ledger,
        _TOKEN,
        timestamp_ist=_TS,
        agent_id=AgentId.TRIAGE,
        mandate=_mandate(data_scopes=frozenset({DataScope.CHANNEL})),
        mandate_hash=_MANDATE_HASH,
        requested_name="infra_hint",
    )

    assert not result.granted
    assert result.ledgered is not None
    assert result.ledgered.event_type is EventType.MANDATE_VIOLATION_ATTEMPT


def test_an_allowed_scope_is_granted_without_a_ledger_entry(ledger: Ledger) -> None:
    """A permitted read is not a governance event; ledgering every one would
    drown the violations that matter."""
    result = guard_scope_request(
        ledger,
        _TOKEN,
        timestamp_ist=_TS,
        agent_id=AgentId.TRIAGE,
        mandate=_mandate(),
        mandate_hash=_MANDATE_HASH,
        requested_name="channel",
    )

    assert result.granted
    assert result.scope is DataScope.CHANNEL
    assert result.ledgered is None
    assert ledger.last_seq == -1


def test_an_unknown_scope_name_is_refused(ledger: Ledger) -> None:
    result = guard_scope_request(
        ledger,
        _TOKEN,
        timestamp_ist=_TS,
        agent_id=AgentId.TRIAGE,
        mandate=_mandate(),
        mandate_hash=_MANDATE_HASH,
        requested_name="not_a_real_scope",
    )
    assert not result.granted
