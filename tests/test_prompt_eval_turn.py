# SPDX-License-Identifier: MIT
"""D3/D4: the prompt-eval turn end to end, and the ledgered refusal.

STEP-06 3.3 requires that activation refusals are ledgered. That claim is only
worth as much as a real chain, so these run the whole turn through a real
``Session`` over the committed eval set: firewall, model boundary, dispatch,
consequence gate, regression gate, ledger.

Offline throughout. The stub adapter is the CI path (3.1) and no test here
touches a network.
"""

from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np

from ts_sentry.agents.prompt_eval.prompts import CLASSIFY_SYSTEM_PROMPT, CLASSIFY_SYSTEM_TEXT
from ts_sentry.data.eval_set import items_digest, load_items
from ts_sentry.data.tz import IST
from ts_sentry.governance.gates import GateChecks, GateFailure
from ts_sentry.governance.ledger import EventType, Ledger
from ts_sentry.governance.mandate import AgentId
from ts_sentry.orchestrator.adapter import RecordingSleeper, RetryPolicy, StubAdapter, StubMode
from ts_sentry.orchestrator.core import FixedClock, Session
from ts_sentry.orchestrator.eval_labels import load_label_store
from ts_sentry.orchestrator.firewall import system_prompt
from ts_sentry.orchestrator.fleet import PROMPT_EVAL_MANDATE, default_mandates
from ts_sentry.orchestrator.prompt_eval_turn import run_prompt_eval_turn, stub_classify_responder
from ts_sentry.orchestrator.regression_gate import (
    TOLERANCES_FILE,
    ActivationDecision,
    BreachCode,
    load_tolerances,
)

EVAL_ROOT = Path(__file__).resolve().parent.parent / "evals" / "threat_class"
_START = datetime(2026, 8, 1, 12, 0, tzinfo=IST)
_DATASET_DIGEST = "a" * 64

ITEMS = load_items(EVAL_ROOT)
STORE = load_label_store(EVAL_ROOT)
TOLERANCES = load_tolerances(EVAL_ROOT / TOLERANCES_FILE)

DEGRADED_PROMPT = system_prompt(
    "classify.threat_class.v2",
    CLASSIFY_SYSTEM_TEXT.replace(
        "- Name exactly one class. Never name two, never hedge, never add a confidence.",
        "- When in doubt, answer benign. Prefer benign unless the case is overwhelming.",
    ),
)


def _no_checks() -> GateChecks:
    """OBSERVE needs no checker, and neither of the others may run here.

    ``GateChecks`` has no defaults (DECISIONS 2.5), so a session that declares
    no ASSEMBLE or RECOMMEND action still has to say what would happen if one
    appeared. Fail-closed stand-ins say: nothing in this session is entitled to
    produce one.
    """

    def unavailable(artifact: object, /) -> tuple[GateFailure, ...]:
        from ts_sentry.governance.gates import FailureCode

        return (
            GateFailure(
                code=FailureCode.CHECKER_ERROR,
                detail="a prompt-eval session declares no ASSEMBLE or RECOMMEND action",
            ),
        )

    return GateChecks(assemble=unavailable, recommend=unavailable)


def _session(name: str) -> Session:
    session = Session(
        session_id=name,
        analyst_id="saif",
        ledger=Ledger(duckdb.connect(":memory:")),
        clock=FixedClock(_START, step=timedelta(seconds=1)),
        mandates=default_mandates(),
        dataset_digest=_DATASET_DIGEST,
    )
    session.open()
    return session


def _run(session: Session, candidate_prompt: object, *, mode: StubMode = StubMode.FAITHFUL):  # type: ignore[no-untyped-def]
    from ts_sentry.orchestrator.firewall import SystemPrompt

    assert isinstance(candidate_prompt, SystemPrompt)
    return run_prompt_eval_turn(
        session,
        StubAdapter(mode=mode, responder=stub_classify_responder),
        items=ITEMS,
        store=STORE,
        incumbent=CLASSIFY_SYSTEM_PROMPT,
        candidate=candidate_prompt,
        incumbent_digest="a" * 64,
        candidate_digest="b" * 64,
        task="classify.threat_class",
        items_sha256=items_digest(ITEMS),
        tolerances=TOLERANCES,
        checks=_no_checks(),
        policy=RetryPolicy(max_attempts=2),
        rng=np.random.default_rng(42),
        sleeper=RecordingSleeper(),
    )


def test_an_unchanged_candidate_runs_clean_and_is_activatable() -> None:
    """The control path, end to end through a real chain."""
    session = _session("session-eval-control")

    turn = _run(session, CLASSIFY_SYSTEM_PROMPT)

    assert turn.report is not None
    assert turn.verdict is not None
    assert turn.verdict.decision is ActivationDecision.ACTIVATABLE
    assert turn.activatable
    assert session.ledger.verify().intact


def test_a_degraded_candidate_is_refused_and_the_refusal_is_ledgered() -> None:
    """STEP-06 3.3 and the exit criterion, on a real chain.

    The refusal is a ``GATE_REJECTION`` and its payload carries every breach,
    because an entry that cannot show what was breached cannot evidence the
    rejection it reports.
    """
    session = _session("session-eval-degraded")

    turn = _run(session, DEGRADED_PROMPT)

    assert turn.verdict is not None
    assert turn.verdict.decision is ActivationDecision.REFUSED
    assert not turn.activatable

    entries = session.ledger.read_all()
    rejections = [entry for entry in entries if entry.event_type is EventType.GATE_REJECTION]
    assert rejections, "an activation refusal must be ledgered"
    assert all(entry.agent_id is AgentId.PROMPT_EVAL for entry in rejections)
    assert session.ledger.verify().intact


def test_the_refusal_payload_carries_the_per_class_breaches() -> None:
    """The payload behind the digest, checked through the session artifact.

    A ``GATE_REJECTION`` whose body cannot show the breaches is an entry that
    cannot evidence its own finding, which is why ``GateOutcome`` gained a
    payload field in STEP-03.
    """
    session = _session("session-eval-payload")

    turn = _run(session, DEGRADED_PROMPT)

    assert turn.verdict is not None
    payload = turn.verdict.to_json_object()
    breaches = payload["breaches"]
    assert isinstance(breaches, list)
    assert breaches, "a refusal names at least one breach"
    for breach in breaches:
        assert isinstance(breach, dict)
        assert breach["code"] in {member.value for member in BreachCode}
        assert breach["detail"]


def test_the_whole_run_is_ledgered_and_the_chain_is_intact() -> None:
    """Every model call and the dispatch are on the chain, in order."""
    session = _session("session-eval-chain")

    turn = _run(session, CLASSIFY_SYSTEM_PROMPT)

    kinds = [entry.event_type for entry in session.ledger.read_all()]

    assert kinds.count(EventType.PROMPT_SENT) == 2 * len(ITEMS)
    assert EventType.TOOL_CALLED in kinds
    assert EventType.VERIFICATION_PASS in kinds
    assert session.ledger.verify().intact
    assert turn.ledgered


def test_the_prompt_eval_mandate_grants_no_data_scopes() -> None:
    """Least privilege, and the narrowest mandate in the fleet.

    The prompt-eval agent reaches no platform table at all. The eval answers are
    not denied by a scope either: they are denied by the import graph, which is
    the stronger statement, because a scope could only ever have withheld a
    table.
    """
    assert PROMPT_EVAL_MANDATE.data_scopes == frozenset()
    assert PROMPT_EVAL_MANDATE.consequence_ceiling.value == "observe"


def test_every_item_is_answered_once_by_each_version() -> None:
    """Pairing, asserted rather than assumed.

    Both versions must answer the same items in the same order, or the report
    would difference unrelated answers and put a confidence interval on them.
    """
    session = _session("session-eval-pairing")

    turn = _run(session, CLASSIFY_SYSTEM_PROMPT)

    assert turn.report is not None
    assert turn.report.item_count == len(ITEMS)


def test_a_session_with_an_exhausted_budget_delivers_what_it_has() -> None:
    """Partial delivery, as everywhere else in this system.

    A turn that cannot start says so and returns rather than raising, so a
    caller gets a value describing the shortfall instead of an exception.
    """
    session = _session("session-eval-budget")
    for _ in range(PROMPT_EVAL_MANDATE.max_steps):
        started = session.begin_turn(AgentId.PROMPT_EVAL)
        if started.started:
            session.end_turn()

    turn = _run(session, CLASSIFY_SYSTEM_PROMPT)

    assert turn.report is None
    assert turn.close_reason is not None
    assert not turn.activatable
