# SPDX-License-Identifier: MIT
"""STEP-03 D5: one complete triage turn.

The exit criterion in miniature. Everything the phase built meets here: the
session machine, the firewall, dispatch, the tool table, the adapter, the
scorer, and the verifier, driven once and ledgered into an intact chain.

The turn's failure paths matter as much as its happy path, and each is a
separate test: an overclaiming model, a refusing provider, an exhausted step
budget, a missing connection. In every one of them the ranked queue still
reaches the analyst if it was produced, because losing the explanation must
not lose the work.
"""

from datetime import datetime

import duckdb
import numpy as np
import pytest
from test_triage import _dataset  # the hand-built platform, shared rather than duplicated

from ts_sentry.agents.triage.prompts import TRIAGE_SYSTEM_PROMPT, RankedQueue
from ts_sentry.data.store import persist_dataset
from ts_sentry.data.tz import IST
from ts_sentry.governance.gates import GateChecks, GateFailure
from ts_sentry.governance.ledger import Ledger, verify_chain
from ts_sentry.governance.mandate import AgentId, Consequence, Mandate, ToolId
from ts_sentry.governance.scopes import DataScope
from ts_sentry.orchestrator.adapter import (
    ModelRequest,
    RecordingSleeper,
    RetryPolicy,
    StubAdapter,
    StubMode,
)
from ts_sentry.orchestrator.core import CloseReason, FixedClock, Session
from ts_sentry.orchestrator.toolspec import ToolResources
from ts_sentry.orchestrator.triage_turn import TriageTurn, run_triage_turn

_BASE = datetime(2024, 6, 1, 12, 0, tzinfo=IST)


@pytest.fixture
def connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    persist_dataset(con, _dataset())
    return con


def _accept(artifact: object, /) -> tuple[GateFailure, ...]:
    return ()


CHECKS = GateChecks(assemble=_accept, recommend=_accept)


def _session(*, token_budget: int = 200_000, max_steps: int = 4) -> Session:
    mandate = Mandate(
        agent_id=AgentId.TRIAGE,
        version="1.0.0",
        consequence_ceiling=Consequence.OBSERVE,
        allowed_tools=frozenset({ToolId.RANK_TRIAGE_QUEUE}),
        data_scopes=frozenset(DataScope),
        output_schema=RankedQueue,
        token_budget=token_budget,
        max_steps=max_steps,
    )
    session = Session(
        session_id="session-001",
        analyst_id="saif",
        ledger=Ledger(duckdb.connect(":memory:")),
        clock=FixedClock(_BASE),
        mandates={AgentId.TRIAGE: mandate},
        dataset_digest="a" * 64,
    )
    session.open()
    return session


def _responder(request: ModelRequest, mode: StubMode) -> str:
    """A stub that writes rationales the contract accepts.

    Reads the citation menu out of the request rather than hard-coding ids, so
    the stub tracks whatever the scorer produced instead of a snapshot of it.
    """
    lines = []
    for line in request.user_content.splitlines():
        if ": cite only " in line:
            case_id = line.split(":", 1)[0]
            first = line.split("[", 1)[1].split("]", 1)[0]
            if mode is StubMode.OVERCLAIM:
                lines.append(f"{case_id}: confirmed abusive per [sealed:ground_truth]")
            else:
                lines.append(f"{case_id}: ranked here on [{first}]")
    return "\n".join(lines)


def _run(
    session: Session,
    connection: duckdb.DuckDBPyConnection | None,
    mode: StubMode = StubMode.FAITHFUL,
    adapter: StubAdapter | None = None,
) -> TriageTurn:
    return run_triage_turn(
        session,
        adapter or StubAdapter(mode=mode, responder=_responder),
        resources=ToolResources(connection=connection, seed=42),
        checks=CHECKS,
        policy=RetryPolicy(max_attempts=2),
        rng=np.random.default_rng(42),
        sleeper=RecordingSleeper(),
    )


def test_a_full_turn_delivers_a_ranked_queue_with_verified_rationales(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    session = _session()

    turn = _run(session, connection)

    assert turn.delivered
    assert turn.queue is not None
    assert turn.rationales is not None and turn.rationales.all_passed
    assert turn.queue.rationale_count == len(turn.queue.rows)
    assert verify_chain(session.ledger.read_all()).intact


def test_the_turn_ledgers_the_architecture_pipeline_in_order(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """ARCHITECTURE 3.3's sequence, asserted as the chain actually records it.

    The model call lands *after* the tool and its gate, which is the design:
    the ranking is the product and must be reproducible from the dataset
    alone, so the rationale step is separate and separately verified.
    """
    session = _session()

    turn = _run(session, connection)

    assert [entry.event_type.value for entry in turn.ledgered] == [
        "tool_called",
        "tool_result",
        "verification_pass",  # the OBSERVE gate accepting the ranking
        "prompt_sent",
        "verification_pass",  # the rationales clearing the verifier
        "output_proposed",
    ]


def test_an_overclaiming_model_loses_its_rationales_but_not_the_ranking(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """The showcased failure path.

    The queue survives; the explanations are rejected and ledgered as
    VERIFICATION_FAIL, which ARCHITECTURE 3.2 calls a showcased metric rather
    than an embarrassment. A governance layer that never fires is one nobody
    tested.
    """
    session = _session()

    turn = _run(session, connection, StubMode.OVERCLAIM)

    assert turn.delivered
    assert turn.queue is not None and turn.queue.rationale_count == 0
    assert turn.rationales is not None and not turn.rationales.all_passed
    assert "verification_fail" in [entry.event_type.value for entry in turn.ledgered]
    assert verify_chain(session.ledger.read_all()).intact


def test_a_refused_provider_still_delivers_the_ranking(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    session = _session()

    turn = _run(session, connection, adapter=StubAdapter(mode=StubMode.REFUSE))

    assert turn.delivered
    assert turn.queue is not None and turn.queue.rationale_count == 0
    assert turn.rationales is None
    assert "without rationales" in turn.detail


def test_an_exhausted_step_budget_refuses_the_turn_before_anything_runs(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    session = _session(max_steps=1)
    _run(session, connection)

    second = _run(session, connection)

    assert not second.delivered
    assert second.close_reason is CloseReason.STEP_BUDGET_EXHAUSTED
    assert second.ledgered == ()


def test_an_exhausted_token_budget_delivers_the_ranking_without_rationales(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """STEP-03 3.3 exactly: exhaustion ends the turn cleanly, partial results
    are delivered, and the reason code reaches the caller."""
    session = _session(token_budget=50)

    turn = _run(session, connection)

    assert turn.delivered
    assert turn.queue is not None
    assert turn.close_reason is CloseReason.TOKEN_BUDGET_EXHAUSTED
    assert turn.queue.rationale_count == 0


def test_case_content_reaches_the_model_only_through_the_firewall(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """The D2 boundary, checked where case text is actually used."""
    session = _session()
    seen: list[ModelRequest] = []

    def capture(request: ModelRequest, mode: StubMode) -> str:
        seen.append(request)
        return _responder(request, mode)

    _run(session, connection, adapter=StubAdapter(responder=capture))

    request = seen[0]
    assert request.system is TRIAGE_SYSTEM_PROMPT
    assert "BEGIN TS-SENTRY CASE DATA" in request.user_content
    assert "a description" in request.user_content  # platform text, inside the fence
    assert "a description" not in request.system.text


def test_a_turn_without_a_connection_fails_closed(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    session = _session()

    turn = _run(session, None)

    assert not turn.delivered
    assert turn.close_reason is CloseReason.DISPATCH_ERROR
    assert verify_chain(session.ledger.read_all()).intact


def test_the_turn_reports_how_many_injection_signals_it_carried(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """The firewall's signal count travels with the turn, so a session can
    report attempted injections without re-reading the chain."""
    session = _session()

    turn = _run(session, connection)

    assert turn.injection_signals >= 0
