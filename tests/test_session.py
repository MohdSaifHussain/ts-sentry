# SPDX-License-Identifier: MIT
"""STEP-03 D1: the session state machine.

Three things are asserted here that a docstring could otherwise have claimed
without earning:

* the published ``TRANSITIONS`` table and the enforced rule are the same
  object, checked pair by pair across every state combination rather than on
  the handful of edges the happy path happens to use;
* every ledger entry's ``payload_digest`` recomputes from the payload body the
  session kept, so a session artifact and its chain cannot disagree;
* budget exhaustion *returns* and closes cleanly, rather than raising, which
  is what STEP-03 3.3 means by "ends the agent turn cleanly and ledgers it".
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import duckdb
import pytest

from ts_sentry.data.tz import IST
from ts_sentry.governance.ledger import (
    EventType,
    Ledger,
    chain_head,
    digest_payload,
    verify_chain,
)
from ts_sentry.governance.mandate import AgentId, Consequence, Mandate, ToolId, mandate_hash
from ts_sentry.governance.scopes import DataScope
from ts_sentry.orchestrator.core import (
    TRANSITIONS,
    BudgetTracker,
    CloseReason,
    FixedClock,
    IllegalTransition,
    MandateBinding,
    Session,
    SessionState,
    SystemClock,
    TurnStart,
    UnknownAgent,
    can_transition,
    mandate_set_hash,
)

_START = datetime(2026, 7, 31, 14, 30, tzinfo=IST)
_DATASET_DIGEST = "a" * 64


@dataclass(frozen=True, slots=True)
class _RankedQueue:
    """Stand-in output schema. The real one arrives with D5."""

    rows: tuple[str, ...]


def _mandate(
    *,
    agent_id: AgentId = AgentId.TRIAGE,
    version: str = "1.0.0",
    token_budget: int = 1_000,
    max_steps: int = 4,
) -> Mandate:
    return Mandate(
        agent_id=agent_id,
        version=version,
        consequence_ceiling=Consequence.OBSERVE,
        allowed_tools=frozenset({ToolId.RANK_TRIAGE_QUEUE}),
        data_scopes=frozenset({DataScope.COMMENT, DataScope.CHANNEL}),
        output_schema=_RankedQueue,
        token_budget=token_budget,
        max_steps=max_steps,
    )


def _session(
    *,
    mandate: Mandate | None = None,
    step: timedelta = timedelta(seconds=1),
) -> Session:
    resolved = _mandate() if mandate is None else mandate
    return Session(
        session_id="session-001",
        analyst_id="saif",
        ledger=Ledger(duckdb.connect(":memory:")),
        clock=FixedClock(_START, step=step),
        mandates={resolved.agent_id: resolved},
        dataset_digest=_DATASET_DIGEST,
    )


# --------------------------------------------------------------------------
# The transition table
# --------------------------------------------------------------------------


def test_transitions_cover_every_state() -> None:
    """The table is derived from the exhaustive ``match``, so every member is
    present by construction. Asserted anyway: this is the check that fails if
    the derivation is ever replaced by a hand-written literal."""
    assert set(TRANSITIONS) == set(SessionState)


@pytest.mark.parametrize("source", list(SessionState))
@pytest.mark.parametrize("target", list(SessionState))
def test_can_transition_agrees_with_the_table(source: SessionState, target: SessionState) -> None:
    assert can_transition(source, target) is (target in TRANSITIONS[source])


def test_closed_is_terminal() -> None:
    """No edge leaves CLOSED. A session that could reopen would let entries be
    appended after the manifest anchored the head, which would make the anchor
    a claim about a moment rather than about a session."""
    assert TRANSITIONS[SessionState.CLOSED] == frozenset()


def test_every_state_is_reachable_from_created() -> None:
    """A state nobody can reach is a state nobody has to reason about, and it
    would quietly rot. Breadth-first over the table itself."""
    reached = {SessionState.CREATED}
    frontier = [SessionState.CREATED]
    while frontier:
        for successor in TRANSITIONS[frontier.pop()]:
            if successor not in reached:
                reached.add(successor)
                frontier.append(successor)
    assert reached == set(SessionState)


def test_illegal_transition_raises_and_names_the_legal_ones() -> None:
    session = _session()
    session.open()

    with pytest.raises(IllegalTransition) as excinfo:
        session.end_turn()  # OPEN -> OPEN is not an edge

    message = str(excinfo.value)
    assert "cannot move from open to open" in message
    assert "agent_turn" in message and "closing" in message


def test_a_closed_session_refuses_every_further_move() -> None:
    session = _session()
    session.open()
    session.close(CloseReason.COMPLETED)

    for attempt in (
        lambda: session.open(),
        lambda: session.begin_turn(AgentId.TRIAGE),
        lambda: session.close(CloseReason.COMPLETED),
    ):
        with pytest.raises(IllegalTransition):
            attempt()


# --------------------------------------------------------------------------
# Lifecycle and ledgering
# --------------------------------------------------------------------------


def test_open_ledgers_session_open_bound_to_the_fleet_configuration() -> None:
    session = _session()
    recorded = session.open()

    assert session.state is SessionState.OPEN
    assert recorded.entry.event_type is EventType.SESSION_OPEN
    assert recorded.entry.agent_id is None
    assert recorded.entry.mandate_hash == session.mandate_set_hash
    assert recorded.payload["analyst_id"] == "saif"
    assert recorded.payload["dataset_digest"] == _DATASET_DIGEST
    assert session.opened_ts == _START


def test_the_write_capability_is_scoped_to_this_session() -> None:
    """The token D3 dispatch will use carries this session's id, so a ledger
    write cannot be attributed to a session that did not make it."""
    session = _session()
    assert session.token.session_id == session.session_id


def test_every_recorded_payload_digests_to_its_entry() -> None:
    """The artifact and the chain agree, entry by entry. Without this the
    payload bodies a session writes out would be unfalsifiable: the chain only
    carries digests, so a body that does not hash to its entry would look
    exactly like one that does."""
    session = _session()
    session.open()
    session.begin_turn(AgentId.TRIAGE)
    session.append_event(
        EventType.TOOL_CALLED,
        agent_id=AgentId.TRIAGE,
        payload={"tool_id": ToolId.RANK_TRIAGE_QUEUE.value},
    )
    session.end_turn()
    session.close(CloseReason.COMPLETED)

    for recorded in session.recorded_events:
        assert recorded.entry.payload_digest == digest_payload(recorded.payload)


def test_agent_events_carry_that_agents_mandate_hash() -> None:
    mandate = _mandate()
    session = _session(mandate=mandate)
    session.open()
    session.begin_turn(AgentId.TRIAGE)

    recorded = session.append_event(
        EventType.PROMPT_SENT, agent_id=AgentId.TRIAGE, payload={"n": 1}
    )

    assert recorded.entry.mandate_hash == mandate_hash(mandate)
    assert recorded.entry.mandate_hash != session.mandate_set_hash


def test_close_anchors_the_head_of_the_finished_chain() -> None:
    """The anchor is read after SESSION_CLOSE is appended, so it describes the
    chain as it will be exported, not the chain as it was one entry earlier."""
    session = _session()
    session.open()
    closed = session.close(CloseReason.COMPLETED)

    entries = session.ledger.read_all()
    assert closed.entry.event_type is EventType.SESSION_CLOSE
    assert closed.head == chain_head(entries)
    assert closed.head.count == len(entries)
    assert verify_chain(entries).intact


def test_ledger_head_property_matches_the_reading_path() -> None:
    """``Ledger.head`` answers from the cached tail; ``chain_head`` reads the
    whole chain. They must never disagree."""
    session = _session()
    session.open()
    session.close(CloseReason.COMPLETED)

    assert session.ledger.head == chain_head(session.ledger.read_all())


def test_close_payload_counts_exclude_the_close_entry() -> None:
    """A close event that counted itself would make the number depend on when
    it was read."""
    session = _session()
    session.open()
    closed = session.close(CloseReason.COMPLETED)

    payload = session.recorded_events[-1].payload
    counts = payload["event_counts"]
    assert counts == {EventType.SESSION_OPEN.value: 1}
    assert closed.reason is CloseReason.COMPLETED
    assert session.event_counts()[EventType.SESSION_CLOSE.value] == 1


def test_session_close_records_the_reason_it_was_given() -> None:
    session = _session()
    session.open()
    closed = session.close(CloseReason.ANALYST_ABORT)

    assert closed.reason is CloseReason.ANALYST_ABORT
    assert session.recorded_events[-1].payload["close_reason"] == "analyst_abort"
    assert session.closed_ts is not None


# --------------------------------------------------------------------------
# Turns and budgets (STEP-03 3.3)
# --------------------------------------------------------------------------


def test_begin_turn_books_a_step_and_returns_the_binding() -> None:
    session = _session()
    session.open()

    start = session.begin_turn(AgentId.TRIAGE)

    assert start.started is True
    assert start.binding is not None
    assert start.binding.hash == mandate_hash(_mandate())
    assert session.state is SessionState.AGENT_TURN
    assert session.budget(AgentId.TRIAGE).snapshot().steps_taken == 1


def test_step_exhaustion_refuses_the_turn_rather_than_raising() -> None:
    session = _session(mandate=_mandate(max_steps=1))
    session.open()
    session.begin_turn(AgentId.TRIAGE)
    session.end_turn()

    start = session.begin_turn(AgentId.TRIAGE)

    assert start.started is False
    assert start.close_reason is CloseReason.STEP_BUDGET_EXHAUSTED
    assert start.binding is None
    assert session.state is SessionState.OPEN  # refused turns do not move the machine


def test_token_exhaustion_refuses_the_turn() -> None:
    session = _session(mandate=_mandate(token_budget=100))
    session.open()
    session.budget(AgentId.TRIAGE).record_tokens(100)

    start = session.begin_turn(AgentId.TRIAGE)

    assert start.close_reason is CloseReason.TOKEN_BUDGET_EXHAUSTED


def test_a_reservation_larger_than_the_remainder_is_refused_before_spending() -> None:
    """Preventive, not detective: the ceiling is enforced by not spending."""
    session = _session(mandate=_mandate(token_budget=100))
    session.open()
    session.budget(AgentId.TRIAGE).record_tokens(60)

    assert session.begin_turn(AgentId.TRIAGE, estimated_tokens=50).started is False
    assert session.begin_turn(AgentId.TRIAGE, estimated_tokens=40).started is True


def test_an_exhausted_session_still_closes_cleanly_with_that_reason() -> None:
    """STEP-03 3.3, end to end: exhaustion ends the turn cleanly, the reason
    code reaches SESSION_CLOSE, and the chain is intact."""
    session = _session(mandate=_mandate(max_steps=1))
    session.open()
    session.begin_turn(AgentId.TRIAGE)
    session.end_turn()

    start = session.begin_turn(AgentId.TRIAGE)
    assert start.close_reason is not None
    closed = session.close(start.close_reason)

    assert closed.reason is CloseReason.STEP_BUDGET_EXHAUSTED
    assert verify_chain(session.ledger.read_all()).intact
    assert session.state is SessionState.CLOSED


def test_steps_are_checked_before_tokens() -> None:
    """A tracker out of both reports the ceiling a turn consumes first."""
    tracker = BudgetTracker(_mandate(token_budget=10, max_steps=1))
    tracker.record_step()
    tracker.record_tokens(10)

    assert tracker.check() is CloseReason.STEP_BUDGET_EXHAUSTED


def test_budget_tracker_rejects_negative_amounts() -> None:
    tracker = BudgetTracker(_mandate())
    with pytest.raises(ValueError, match="must not be negative"):
        tracker.record_tokens(-1)
    with pytest.raises(ValueError, match="must not be negative"):
        tracker.check(-1)


def test_budget_snapshot_reports_its_own_position() -> None:
    tracker = BudgetTracker(_mandate(token_budget=10, max_steps=2))
    tracker.record_tokens(4)
    tracker.record_step()
    snapshot = tracker.snapshot()

    assert snapshot.tokens_remaining == 6
    assert snapshot.steps_remaining == 1
    assert snapshot.exhausted is False
    assert snapshot.to_json_object()["tokens_spent"] == 4


def test_an_unbound_agent_is_a_configuration_error() -> None:
    session = _session()
    session.open()

    with pytest.raises(UnknownAgent):
        session.begin_turn(AgentId.MEMO)
    with pytest.raises(UnknownAgent):
        session.budget(AgentId.MEMO)
    with pytest.raises(UnknownAgent):
        session.append_event(EventType.PROMPT_SENT, agent_id=AgentId.MEMO, payload={})


def test_await_analyst_and_resume_are_legal_edges() -> None:
    """AWAITING_ANALYST has no Phase 3 driver, so it is exercised directly
    rather than left untested until STEP-05 gives it one."""
    session = _session()
    session.open()
    session.begin_turn(AgentId.TRIAGE)

    # Read into locals: asserting on the property twice lets mypy narrow it
    # after the first check and then reject the second as non-overlapping.
    session.await_analyst()
    awaiting = session.state
    assert awaiting is SessionState.AWAITING_ANALYST

    session.resume()
    resumed = session.state
    assert resumed is SessionState.OPEN


# --------------------------------------------------------------------------
# Construction invariants
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("session_id", "  ", "session_id must be a non-empty"),
        ("analyst_id", "", "analyst_id must be a non-empty"),
        ("dataset_digest", "not-a-digest", "dataset_digest"),
    ],
)
def test_session_construction_validates_its_identity_fields(
    field: str, value: str, message: str
) -> None:
    kwargs: dict[str, object] = {
        "session_id": "session-001",
        "analyst_id": "saif",
        "dataset_digest": _DATASET_DIGEST,
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=message):
        Session(
            ledger=Ledger(duckdb.connect(":memory:")),
            clock=FixedClock(_START),
            mandates={AgentId.TRIAGE: _mandate()},
            **kwargs,  # type: ignore[arg-type]
        )


def test_a_session_must_load_at_least_one_mandate() -> None:
    with pytest.raises(ValueError, match="at least one mandate"):
        Session(
            session_id="session-001",
            analyst_id="saif",
            ledger=Ledger(duckdb.connect(":memory:")),
            clock=FixedClock(_START),
            mandates={},
            dataset_digest=_DATASET_DIGEST,
        )


def test_a_mandate_registered_under_the_wrong_agent_is_refused() -> None:
    """The registry key is what dispatch looks an agent up by; a key that
    disagrees with the mandate's own ``agent_id`` would let an action be
    validated against another agent's ceiling."""
    with pytest.raises(ValueError, match="declares agent_id"):
        Session(
            session_id="session-001",
            analyst_id="saif",
            ledger=Ledger(duckdb.connect(":memory:")),
            clock=FixedClock(_START),
            mandates={AgentId.MEMO: _mandate(agent_id=AgentId.TRIAGE)},
            dataset_digest=_DATASET_DIGEST,
        )


def test_the_mandate_set_hash_changes_when_any_mandate_does() -> None:
    """ARCHITECTURE 3.1: a mandate change is itself an audited event. The
    SESSION_OPEN entry is bound to the exact fleet configuration."""
    base = {AgentId.TRIAGE: MandateBinding.of(_mandate())}
    bumped = {AgentId.TRIAGE: MandateBinding.of(_mandate(version="1.0.1"))}

    assert mandate_set_hash(base) != mandate_set_hash(bumped)
    assert mandate_set_hash(base) == mandate_set_hash(dict(base))


def test_turn_start_invariants_are_enforced() -> None:
    with pytest.raises(ValueError, match="close reason"):
        TurnStart(started=True, binding=None, close_reason=CloseReason.COMPLETED, detail="")
    with pytest.raises(ValueError, match="mandate binding"):
        TurnStart(started=True, binding=None, close_reason=None, detail="")


# --------------------------------------------------------------------------
# Clocks
# --------------------------------------------------------------------------


def test_fixed_clock_advances_by_its_step() -> None:
    clock = FixedClock(_START, step=timedelta(seconds=5))
    assert clock.now() == _START
    assert clock.now() == _START + timedelta(seconds=5)


def test_fixed_clock_rejects_a_naive_start_and_a_negative_step() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FixedClock(datetime(2026, 7, 31, 14, 30))
    with pytest.raises(ValueError, match="must not be negative"):
        FixedClock(_START, step=timedelta(seconds=-1))


def test_system_clock_returns_ist_aware_time() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(hours=5, minutes=30)
