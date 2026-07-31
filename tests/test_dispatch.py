# SPDX-License-Identifier: MIT
"""STEP-03 D3: the dispatch pipeline.

The obligation this file discharges is the fourth from the STEP-02 Outcome:
``validate`` stays pure, and the *orchestrator* ledgers every refusal. So
almost every test here asserts two things about a refusal, not one. That it
was refused, and that the chain says so.

Dispatch is exercised against purpose-built tool tables rather than the
production one. That is not a shortcut: at D3 the production table declares
four tools and executes none, and the mechanism has to be provable before the
first real tool exists. The production table gets its own assertions in
``tests/test_tool_table.py``.
"""

from dataclasses import dataclass
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
    ScopeGuardResult,
)
from ts_sentry.governance.ledger import EventType, Ledger, digest_payload, verify_chain
from ts_sentry.governance.mandate import (
    AgentId,
    Consequence,
    Mandate,
    RefusalCode,
    ToolId,
)
from ts_sentry.governance.scopes import DataScope
from ts_sentry.orchestrator.core import CloseReason, FixedClock, Session, UnknownAgent
from ts_sentry.orchestrator.dispatch import (
    DispatchDecision,
    DispatchOutcome,
    ToolProposal,
    dispatch,
)
from ts_sentry.orchestrator.tools import ToolContext, ToolEntry

_START = datetime(2026, 7, 31, 14, 30, tzinfo=IST)
_DATASET_DIGEST = "a" * 64


@dataclass(frozen=True, slots=True)
class RankedQueue:
    """Stand-in for the D5 output schema."""

    rows: tuple[str, ...]


def _refuse_everything(artifact: object, /) -> tuple[GateFailure, ...]:
    return (GateFailure(code=FailureCode.SCHEMA_INVALID, detail="test checker refuses"),)


def _accept_everything(artifact: object, /) -> tuple[GateFailure, ...]:
    return ()


CHECKS = GateChecks(assemble=_accept_everything, recommend=_accept_everything)
REFUSING_CHECKS = GateChecks(assemble=_refuse_everything, recommend=_refuse_everything)


def _rank(context: ToolContext) -> object:
    return RankedQueue(rows=(f"case-1:{len(context.granted_scopes)}",))


def _explode(context: ToolContext) -> object:
    raise RuntimeError("handler blew up")


def _wrong_type(context: ToolContext) -> object:
    return {"not": "a RankedQueue"}


def _entry(
    *,
    tool_id: ToolId = ToolId.RANK_TRIAGE_QUEUE,
    consequence: Consequence = Consequence.OBSERVE,
    scopes: frozenset[DataScope] = frozenset({DataScope.COMMENT}),
    handler: object = _rank,
    due: int = 3,
) -> ToolEntry:
    return ToolEntry(
        tool_id=tool_id,
        consequence=consequence,
        required_scopes=scopes,
        handler_due_step=due,
        handler=handler,  # type: ignore[arg-type]
        summary="test entry",
    )


def _table(*entries: ToolEntry) -> dict[ToolId, ToolEntry]:
    return {entry.tool_id: entry for entry in entries}


def _mandate(
    *,
    tools: frozenset[ToolId] = frozenset({ToolId.RANK_TRIAGE_QUEUE}),
    scopes: frozenset[DataScope] = frozenset({DataScope.COMMENT, DataScope.CHANNEL}),
    ceiling: Consequence = Consequence.OBSERVE,
) -> Mandate:
    return Mandate(
        agent_id=AgentId.TRIAGE,
        version="1.0.0",
        consequence_ceiling=ceiling,  # type: ignore[arg-type]
        allowed_tools=tools,
        data_scopes=scopes,
        output_schema=RankedQueue,
        token_budget=1_000,
        max_steps=4,
    )


def _open_session(mandate: Mandate | None = None) -> Session:
    session = Session(
        session_id="session-001",
        analyst_id="saif",
        ledger=Ledger(duckdb.connect(":memory:")),
        clock=FixedClock(_START),
        mandates={AgentId.TRIAGE: _mandate() if mandate is None else mandate},
        dataset_digest=_DATASET_DIGEST,
    )
    session.open()
    session.begin_turn(AgentId.TRIAGE)
    return session


def _proposal(
    *,
    tool_name: str = "rank_triage_queue",
    scopes: tuple[str, ...] = ("comment",),
) -> ToolProposal:
    return ToolProposal(
        agent_id=AgentId.TRIAGE,
        tool_name=tool_name,
        requested_scope_names=scopes,
        params={},
    )


def _events(session: Session) -> list[str]:
    return [recorded.entry.event_type.value for recorded in session.recorded_events]


# --------------------------------------------------------------------------
# The accepted path
# --------------------------------------------------------------------------


def test_an_allowed_proposal_executes_and_is_ledgered_end_to_end() -> None:
    session = _open_session()

    outcome = dispatch(session, _proposal(), table=_table(_entry()), checks=CHECKS)

    assert outcome.decision is DispatchDecision.EXECUTED
    assert isinstance(outcome.result, RankedQueue)
    assert outcome.gate is not None and outcome.gate.accepted
    assert _events(session) == [
        EventType.SESSION_OPEN.value,
        EventType.TOOL_CALLED.value,
        EventType.TOOL_RESULT.value,
        EventType.VERIFICATION_PASS.value,
    ]
    assert verify_chain(session.ledger.read_all()).intact


def test_the_handler_receives_only_the_scopes_that_were_granted() -> None:
    """A handler cannot widen its own access: what it is handed is what the
    mandate allowed, and there is no path from a ToolContext to anything
    else."""
    session = _open_session()
    seen: list[frozenset[DataScope]] = []

    def _capture(context: ToolContext) -> object:
        seen.append(context.granted_scopes)
        return RankedQueue(rows=())

    dispatch(
        session,
        _proposal(scopes=("comment", "channel")),
        table=_table(_entry(handler=_capture, scopes=frozenset({DataScope.COMMENT}))),
        checks=CHECKS,
    )

    assert seen == [frozenset({DataScope.COMMENT, DataScope.CHANNEL})]


def test_every_dispatched_payload_digests_to_its_entry() -> None:
    """Including the ones written by guard_scope_request and run_gate, which
    append directly. Bridged bodies have to be checkable too, or the artifact
    they end up in is not evidence."""
    session = _open_session()
    dispatch(
        session,
        _proposal(scopes=("comment", "sealed._labels")),
        table=_table(_entry()),
        checks=CHECKS,
    )
    dispatch(session, _proposal(), table=_table(_entry()), checks=CHECKS)

    for recorded in session.recorded_events:
        assert recorded.entry.payload_digest == digest_payload(recorded.payload)


# --------------------------------------------------------------------------
# Refusals, each ledgered
# --------------------------------------------------------------------------


def test_an_unresolvable_tool_name_is_refused_and_ledgered() -> None:
    session = _open_session()

    outcome = dispatch(
        session, _proposal(tool_name="do_whatever"), table=_table(_entry()), checks=CHECKS
    )

    assert outcome.decision is DispatchDecision.REFUSED
    assert outcome.refusal_code is RefusalCode.TOOL_NOT_ALLOWED
    assert _events(session)[-1] == EventType.MANDATE_VIOLATION_ATTEMPT.value


def test_a_tool_absent_from_this_table_is_refused_and_ledgered() -> None:
    session = _open_session(_mandate(tools=frozenset(ToolId)))

    outcome = dispatch(
        session,
        _proposal(tool_name="run_prompt_eval"),
        table=_table(_entry()),
        checks=CHECKS,
    )

    assert outcome.refusal_code is RefusalCode.TOOL_NOT_ALLOWED
    assert outcome.tool_id is ToolId.RUN_PROMPT_EVAL
    assert _events(session)[-1] == EventType.MANDATE_VIOLATION_ATTEMPT.value


def test_a_sealed_scope_request_is_refused_and_ledgered_by_the_existing_guard() -> None:
    """STEP-01 3.3 and STEP-02 3.5, now reached through dispatch. The refusal
    comes from ``gates.guard_scope_request``, reused rather than
    reimplemented, and its own MANDATE_VIOLATION_ATTEMPT is bridged into the
    session so the body survives with it."""
    session = _open_session()

    outcome = dispatch(
        session,
        _proposal(scopes=("sealed._labels",)),
        table=_table(_entry()),
        checks=CHECKS,
    )

    assert outcome.refusal_code is RefusalCode.SCOPE_NOT_ALLOWED
    assert _events(session).count(EventType.MANDATE_VIOLATION_ATTEMPT.value) == 2
    assert "sealed" in session.recorded_events[1].payload["requested_scope"]  # type: ignore[operator]
    assert EventType.TOOL_CALLED.value not in _events(session)


def test_a_real_scope_outside_the_mandate_is_refused_and_ledgered() -> None:
    session = _open_session(_mandate(scopes=frozenset({DataScope.COMMENT})))

    outcome = dispatch(session, _proposal(scopes=("video",)), table=_table(_entry()), checks=CHECKS)

    assert outcome.refusal_code is RefusalCode.SCOPE_NOT_ALLOWED
    assert EventType.TOOL_CALLED.value not in _events(session)


def test_a_tool_whose_required_scopes_were_not_requested_is_refused() -> None:
    """The allowlist runs in both directions: the mandate says what the agent
    may reach, and the table says what the tool actually needs."""
    session = _open_session()

    outcome = dispatch(
        session,
        _proposal(scopes=("comment",)),
        table=_table(_entry(scopes=frozenset({DataScope.COMMENT, DataScope.CHANNEL}))),
        checks=CHECKS,
    )

    assert outcome.refusal_code is RefusalCode.SCOPE_NOT_ALLOWED
    assert "channel" in outcome.detail


def test_a_tool_outside_the_mandate_allowlist_is_refused_and_ledgered() -> None:
    session = _open_session(_mandate(tools=frozenset()))

    outcome = dispatch(session, _proposal(), table=_table(_entry()), checks=CHECKS)

    assert outcome.refusal_code is RefusalCode.TOOL_NOT_ALLOWED
    assert _events(session)[-1] == EventType.MANDATE_VIOLATION_ATTEMPT.value


def test_a_consequence_above_the_ceiling_is_refused_and_ledgered() -> None:
    """The table declares the consequence, so an agent cannot understate it to
    fit under its own ceiling."""
    session = _open_session()

    outcome = dispatch(
        session,
        _proposal(),
        table=_table(_entry(consequence=Consequence.RECOMMEND)),
        checks=CHECKS,
    )

    assert outcome.refusal_code is RefusalCode.CONSEQUENCE_EXCEEDS_CEILING
    assert EventType.TOOL_CALLED.value not in _events(session)


def test_an_enforce_tool_is_refused_before_anything_else() -> None:
    """No production entry declares ENFORCE and none may. This drives the
    refusal through dispatch anyway, because the invariant that matters is
    that reaching the ENFORCE gate is impossible, not that nobody tried."""
    session = _open_session()

    outcome = dispatch(
        session,
        _proposal(),
        table=_table(_entry(consequence=Consequence.ENFORCE)),
        checks=CHECKS,
    )

    assert outcome.refusal_code is RefusalCode.ENFORCE_IS_HUMAN_ONLY
    assert EventType.TOOL_CALLED.value not in _events(session)
    assert _events(session)[-1] == EventType.MANDATE_VIOLATION_ATTEMPT.value


def test_agent_mismatch_cannot_arise_through_dispatch() -> None:
    """``RefusalCode.AGENT_MISMATCH`` is unreachable here, and that is the
    session invariant working rather than a coverage gap.

    Dispatch never picks the mandate: it looks one up by the proposing
    agent's id, and ``Session.__init__`` already refused any registry whose
    key disagrees with the mandate's own ``agent_id``. So the pair handed to
    ``validate`` always matches by construction. The refusal still exists and
    is tested at the ``validate`` level in STEP-02, where a caller can supply
    a foreign mandate directly.
    """
    session = _open_session()

    binding = session.binding(AgentId.TRIAGE)
    assert binding.mandate.agent_id is AgentId.TRIAGE

    outcome = dispatch(session, _proposal(), table=_table(_entry()), checks=CHECKS)
    assert outcome.refusal_code is not RefusalCode.AGENT_MISMATCH


def test_an_agent_with_no_mandate_in_this_session_is_a_configuration_error() -> None:
    session = _open_session()

    with pytest.raises(UnknownAgent):
        dispatch(
            session,
            ToolProposal(
                agent_id=AgentId.MEMO,
                tool_name="rank_triage_queue",
                requested_scope_names=(),
                params={},
            ),
            table=_table(_entry()),
            checks=CHECKS,
        )


# --------------------------------------------------------------------------
# The build-limitation path, kept apart from governance violations
# --------------------------------------------------------------------------


def test_a_declared_tool_with_no_handler_is_refused_as_a_build_limitation() -> None:
    """The distinction Saif required: a build limitation must never be
    countable as a mandate violation. Nothing was violated here, so nothing is
    recorded as a violation."""
    session = _open_session()

    outcome = dispatch(
        session, _proposal(), table=_table(_entry(handler=None, due=9)), checks=CHECKS
    )

    assert outcome.refusal_code is RefusalCode.TOOL_HANDLER_NOT_IN_BUILD
    assert _events(session)[-1] == EventType.GATE_REJECTION.value
    assert EventType.MANDATE_VIOLATION_ATTEMPT.value not in _events(session)
    assert "STEP-09" in outcome.detail


def test_a_mandate_violation_outranks_a_missing_handler() -> None:
    """Ordering, asserted rather than assumed. If the build check ran first, a
    real violation would be reported as a build limitation and would vanish
    from the metric that exists to show the governance layer working."""
    session = _open_session(_mandate(tools=frozenset()))

    outcome = dispatch(
        session, _proposal(), table=_table(_entry(handler=None, due=9)), checks=CHECKS
    )

    assert outcome.refusal_code is RefusalCode.TOOL_NOT_ALLOWED
    assert _events(session)[-1] == EventType.MANDATE_VIOLATION_ATTEMPT.value


# --------------------------------------------------------------------------
# Failures, which are defects rather than policy successes
# --------------------------------------------------------------------------


def test_a_handler_that_raises_fails_closed_and_is_ledgered() -> None:
    session = _open_session()

    outcome = dispatch(session, _proposal(), table=_table(_entry(handler=_explode)), checks=CHECKS)

    assert outcome.decision is DispatchDecision.FAILED
    assert outcome.refusal_code is None
    assert outcome.result is None
    assert "RuntimeError" in outcome.detail
    assert _events(session)[-1] == EventType.TOOL_RESULT.value
    assert session.recorded_events[-1].payload["ok"] is False
    assert outcome.gate is None  # nothing was produced, so nothing was gated


def test_a_result_off_the_declared_schema_is_rejected() -> None:
    session = _open_session()

    outcome = dispatch(
        session, _proposal(), table=_table(_entry(handler=_wrong_type)), checks=CHECKS
    )

    assert outcome.decision is DispatchDecision.FAILED
    assert _events(session)[-2:] == [
        EventType.VERIFICATION_FAIL.value,
        EventType.GATE_REJECTION.value,
    ]


def test_a_gate_rejection_is_a_refusal_without_a_refusal_code() -> None:
    """A rejected gate is a different finding from a mandate violation: the
    action was inside the mandate and its output did not pass."""
    session = _open_session(_mandate(ceiling=Consequence.ASSEMBLE))

    outcome = dispatch(
        session,
        _proposal(),
        table=_table(_entry(consequence=Consequence.ASSEMBLE)),
        checks=REFUSING_CHECKS,
    )

    assert outcome.decision is DispatchDecision.REFUSED
    assert outcome.refusal_code is None
    assert outcome.gate is not None and not outcome.gate.accepted
    assert _events(session)[-2:] == [
        EventType.VERIFICATION_FAIL.value,
        EventType.GATE_REJECTION.value,
    ]


# --------------------------------------------------------------------------
# Outcome invariants
# --------------------------------------------------------------------------


def test_dispatch_outcome_shapes_are_enforced() -> None:
    with pytest.raises(ValueError, match="only a refused dispatch carries a RefusalCode"):
        DispatchOutcome(
            decision=DispatchDecision.EXECUTED,
            tool_id=ToolId.RANK_TRIAGE_QUEUE,
            result=None,
            refusal_code=RefusalCode.TOOL_NOT_ALLOWED,
            gate=None,
            detail="",
            ledgered=(),
        )
    with pytest.raises(ValueError, match="has no recorded cause"):
        DispatchOutcome(
            decision=DispatchDecision.REFUSED,
            tool_id=None,
            result=None,
            refusal_code=None,
            gate=None,
            detail="",
            ledgered=(),
        )
    with pytest.raises(ValueError, match="only an executed dispatch carries a result"):
        DispatchOutcome(
            decision=DispatchDecision.FAILED,
            tool_id=None,
            result=RankedQueue(rows=()),
            refusal_code=None,
            gate=None,
            detail="",
            ledgered=(),
        )


def test_the_bridged_payload_invariants_hold_on_both_helpers() -> None:
    """The two STEP-02 structures that grew a payload field in STEP-03.

    A body that does not travel with its entry is a body nobody can check
    later, so each refuses the half-populated shape outright rather than
    letting a caller discover the gap when assembling an artifact.
    """
    with pytest.raises(ValueError, match="one body per ledgered entry"):
        GateOutcome(
            decision=GateDecision.ACCEPTED,
            consequence=Consequence.OBSERVE,
            failures=(),
            ledgered=(),
            ledgered_payloads=({"event": "verification_pass"},),
        )
    with pytest.raises(ValueError, match="carries the payload that was digested"):
        ScopeGuardResult(
            granted=True,
            scope=DataScope.COMMENT,
            code=None,
            detail="granted",
            ledgered=None,
            payload={"requested_scope": "comment"},
        )


def test_the_session_refuses_a_body_that_does_not_match_the_chain() -> None:
    """``attach_event`` is a bridge, not a trust exercise: a payload that has
    drifted from what was digested is refused rather than filed."""
    session = _open_session()
    recorded = session.recorded_events[0]

    with pytest.raises(ValueError, match="body and the chain disagree"):
        session.attach_event(recorded.entry, {"tampered": True})


def test_an_executed_outcome_reports_itself_as_executed() -> None:
    session = _open_session()

    outcome = dispatch(session, _proposal(), table=_table(_entry()), checks=CHECKS)

    assert outcome.executed is True


def test_a_refused_dispatch_leaves_the_session_able_to_close_cleanly() -> None:
    """Refusal is an expected outcome, not a broken session."""
    session = _open_session()
    dispatch(session, _proposal(tool_name="nope"), table=_table(_entry()), checks=CHECKS)
    session.end_turn()

    closed = session.close(CloseReason.COMPLETED)

    assert verify_chain(session.ledger.read_all()).intact
    assert closed.head.count == len(session.ledger.read_all())
