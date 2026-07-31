# SPDX-License-Identifier: MIT
"""STEP-04 D2: the evidence agent, the approval loop, and what it ledgers.

The load-bearing tests here are the ones about ``reviewer_kind``. A scripted
reviewer produces a ``HUMAN_DECISION`` ledger entry, and the human in "human
decision" is the thing ARCHITECTURE 3.3 says can never be automated. So the
suite asserts three separate things, because each could hold while another
failed:

* the mechanism is **in the ledgered payload**, and therefore hash-covered;
* editing it afterwards makes the body disagree with the digest already in the
  chain, which is what "tamper-evident" has to mean to be worth saying;
* **no rendering path** anywhere shows an approval without showing what made
  it, checked over every string a person could read.

The rest cover the loop: rejection is terminal for a proposal, the agent may
propose an alternative, an unverifiable proposal never reaches the analyst, and
the step budget bounds proposals whether they are approved or refused.
"""

import json
from collections.abc import Iterator
from datetime import datetime, timedelta

import duckdb
import numpy as np
import pytest

from ts_sentry.agents.evidence.pack import EvidencePack
from ts_sentry.agents.evidence.proposal import parse_proposal
from ts_sentry.data.enums import EntityKind
from ts_sentry.data.generator import build_dataset
from ts_sentry.data.population import BuildConfig
from ts_sentry.data.store import persist_dataset
from ts_sentry.data.tz import IST
from ts_sentry.governance.ledger import EventType, Ledger, digest_payload
from ts_sentry.governance.mandate import AgentId
from ts_sentry.orchestrator.adapter import RetryPolicy, StubAdapter, StubMode
from ts_sentry.orchestrator.core import CloseReason, FixedClock, Session, SessionState
from ts_sentry.orchestrator.evidence_turn import (
    EvidenceTurn,
    run_evidence_turn,
    stub_evidence_responder,
)
from ts_sentry.orchestrator.fleet import PHASE_FOUR_CHECKS, default_mandates
from ts_sentry.orchestrator.review import (
    InteractiveReviewer,
    ReviewDecision,
    ReviewerKind,
    ReviewOutcome,
    ReviewRequest,
    ScriptedReviewer,
)
from ts_sentry.orchestrator.toolspec import ToolResources

_START = datetime(2026, 7, 31, 14, 30, tzinfo=IST)
_DIGEST = "a" * 64


class _RecordingSleeper:
    def sleep(self, seconds: float) -> None:  # pragma: no cover - never called offline
        raise AssertionError("the stub adapter does not retry")


@pytest.fixture(scope="module")
def dataset() -> Iterator[duckdb.DuckDBPyConnection]:
    con = duckdb.connect()
    persist_dataset(con, build_dataset(BuildConfig(seed=42, scale=1)))
    yield con
    con.close()


@pytest.fixture(scope="module")
def subject(dataset: duckdb.DuckDBPyConnection) -> str:
    """A channel whose owning account carries a shared infrastructure signal.

    Chosen from the build rather than hard-coded, so the fixture cannot drift
    from the generator, and chosen for a *ring* subject so the pivots have
    something to find.
    """
    row = dataset.execute(
        "SELECT ch.channel_id FROM main.channel ch "
        "JOIN main.infra_hint h ON h.subject_id = ch.account_id "
        "WHERE (h.signal_type, h.signal_value) IN "
        "  (SELECT signal_type, signal_value FROM main.infra_hint "
        "   GROUP BY 1, 2 HAVING COUNT(DISTINCT subject_id) > 1) "
        "ORDER BY ch.channel_id LIMIT 1"
    ).fetchone()
    assert row is not None
    return str(row[0])


def _session() -> Session:
    session = Session(
        session_id="session-evidence",
        analyst_id="saif",
        ledger=Ledger(duckdb.connect(":memory:")),
        clock=FixedClock(_START, timedelta(seconds=1)),
        mandates=default_mandates(),
        dataset_digest=_DIGEST,
    )
    session.open()
    return session


def _run(
    session: Session,
    connection: duckdb.DuckDBPyConnection,
    subject_id: str,
    *,
    reviewer: ScriptedReviewer | None = None,
    mode: StubMode = StubMode.FAITHFUL,
    max_hops: int | None = 3,
) -> EvidenceTurn:
    return run_evidence_turn(
        session,
        StubAdapter(mode=mode, responder=stub_evidence_responder),
        case_id="case-0000",
        subject_id=subject_id,
        reviewer=reviewer or ScriptedReviewer(reviewer_id="saif"),
        resources=ToolResources(connection=connection, seed=42),
        checks=PHASE_FOUR_CHECKS,
        policy=RetryPolicy(),
        rng=np.random.default_rng(42),
        sleeper=_RecordingSleeper(),
        subject_kind=EntityKind.CHANNEL,
        max_hops=max_hops,
    )


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------


def test_an_approved_hop_executes_and_grows_the_pack(
    dataset: duckdb.DuckDBPyConnection, subject: str
) -> None:
    session = _session()

    turn = _run(session, dataset, subject, max_hops=1)

    assert turn.executed_hops == 1
    assert turn.pack.hops == 1
    assert len(turn.pack.nodes) > 1
    assert turn.pack.provenance[-1].pivot_kind is not None


def test_every_hop_is_a_ledgered_human_decision(
    dataset: duckdb.DuckDBPyConnection, subject: str
) -> None:
    """STEP-04's exit checklist: all hops present as HUMAN_DECISION events."""
    session = _session()

    turn = _run(session, dataset, subject, max_hops=3)

    decisions = [
        recorded
        for recorded in session.recorded_events
        if recorded.entry.event_type is EventType.HUMAN_DECISION
    ]
    reviewed = [hop for hop in turn.hops if hop.outcome in {"executed", "rejected"}]
    assert len(decisions) == len(reviewed)
    assert decisions, "a turn that reviewed nothing proves nothing about reviewing"


def test_the_analyst_decision_precedes_the_tool_call_in_the_chain(
    dataset: duckdb.DuckDBPyConnection, subject: str
) -> None:
    """Approval is a precondition of execution, not a note taken beside it.

    Asserted on sequence numbers rather than on the code's shape, because the
    ordering is the governance claim: nothing ran that a human had not already
    approved.
    """
    session = _session()

    _run(session, dataset, subject, max_hops=1)

    order = [
        (recorded.entry.seq, recorded.entry.event_type)
        for recorded in session.recorded_events
        if recorded.entry.event_type in {EventType.HUMAN_DECISION, EventType.TOOL_CALLED}
    ]
    assert [event for _, event in order] == [EventType.HUMAN_DECISION, EventType.TOOL_CALLED]
    assert order[0][0] < order[1][0]


def test_a_rejected_proposal_is_terminal_and_nothing_runs(
    dataset: duckdb.DuckDBPyConnection, subject: str
) -> None:
    """STEP-04 3.3. A rejection ends that proposal and executes nothing."""
    session = _session()

    turn = _run(
        session,
        dataset,
        subject,
        reviewer=ScriptedReviewer(reviewer_id="saif", default=ReviewDecision.REJECT),
        max_hops=2,
    )

    assert turn.executed_hops == 0
    assert turn.rejected_hops == 2
    assert turn.pack.hops == 0
    assert not any(
        recorded.entry.event_type is EventType.TOOL_CALLED for recorded in session.recorded_events
    )


def test_the_agent_may_propose_again_after_a_rejection(
    dataset: duckdb.DuckDBPyConnection, subject: str
) -> None:
    """Rejection is terminal for the proposal, not for the turn."""
    session = _session()

    turn = _run(
        session,
        dataset,
        subject,
        reviewer=ScriptedReviewer(
            reviewer_id="saif",
            decisions=(ReviewDecision.REJECT,),
            default=ReviewDecision.APPROVE,
        ),
        max_hops=2,
    )

    assert [hop.outcome for hop in turn.hops] == ["rejected", "executed"]
    assert turn.pack.hops == 1


def test_proposals_are_bounded_by_the_mandate_step_budget(
    dataset: duckdb.DuckDBPyConnection, subject: str
) -> None:
    """STEP-04 3.3: max proposals per turn bounded by mandate.

    One hop is one turn is one step, so a rejected proposal costs a step
    exactly as an approved one does. A bound that counted only successes would
    let an agent propose indefinitely as long as everything it proposed was
    refused.
    """
    session = _session()
    budget = session.binding(AgentId.EVIDENCE).mandate.max_steps

    turn = _run(
        session,
        dataset,
        subject,
        reviewer=ScriptedReviewer(reviewer_id="saif", default=ReviewDecision.REJECT),
        max_hops=None,
    )

    assert turn.rejected_hops == budget
    assert session.budget(AgentId.EVIDENCE).snapshot().steps_remaining == 0


def test_an_unverifiable_proposal_never_reaches_the_analyst(
    dataset: duckdb.DuckDBPyConnection, subject: str
) -> None:
    """The division of labour the design rests on.

    Under OVERCLAIM the stub cites a record no pack carries. The machine
    catches it, ledgers VERIFICATION_FAIL, and the analyst is never asked: their
    attention is for the question that is theirs, which is whether a well-formed
    pivot is worth running.
    """
    session = _session()

    turn = _run(session, dataset, subject, mode=StubMode.OVERCLAIM, max_hops=2)

    assert turn.executed_hops == 0
    assert all(hop.outcome == "refused" for hop in turn.hops)
    assert all(hop.attribution is None for hop in turn.hops)
    assert not any(
        recorded.entry.event_type is EventType.HUMAN_DECISION
        for recorded in session.recorded_events
    )
    assert any(
        recorded.entry.event_type is EventType.VERIFICATION_FAIL
        for recorded in session.recorded_events
    )


def test_the_turn_drives_awaiting_analyst_and_returns_to_open(
    dataset: duckdb.DuckDBPyConnection, subject: str
) -> None:
    """STEP-03 shipped AWAITING_ANALYST with no driver and predicted STEP-05
    would supply one. STEP-04 does: a pivot is the first thing an agent produces
    that a human must decide on before anything happens."""
    session = _session()
    seen: list[SessionState] = []

    class _Watching(ScriptedReviewer):
        def review(self, request: ReviewRequest, /) -> ReviewOutcome:
            seen.append(session.state)
            return super().review(request)

    _run(session, dataset, subject, reviewer=_Watching(reviewer_id="saif"), max_hops=1)

    assert seen == [SessionState.AWAITING_ANALYST]
    assert session.state is SessionState.OPEN


def test_a_turn_delivers_the_pack_it_has_when_the_model_fails(
    dataset: duckdb.DuckDBPyConnection, subject: str
) -> None:
    """Partial delivery. Losing the next hop must not lose the gathered ones."""
    session = _session()
    _run(session, dataset, subject, max_hops=1)

    turn = run_evidence_turn(
        session,
        StubAdapter(mode=StubMode.REFUSE, responder=stub_evidence_responder),
        case_id="case-0000",
        subject_id=subject,
        reviewer=ScriptedReviewer(reviewer_id="saif"),
        resources=ToolResources(connection=dataset, seed=42),
        checks=PHASE_FOUR_CHECKS,
        policy=RetryPolicy(),
        rng=np.random.default_rng(42),
        sleeper=_RecordingSleeper(),
        max_hops=1,
    )

    assert turn.close_reason is CloseReason.DISPATCH_ERROR
    assert turn.pack.hops == 0
    assert turn.hops[0].outcome == "model_unavailable"


# --------------------------------------------------------------------------
# reviewer_kind: hash-covered, tamper-evident, and never renderable as human
# --------------------------------------------------------------------------


def _decision_payloads(session: Session) -> list[dict[str, object]]:
    return [
        dict(recorded.payload)
        for recorded in session.recorded_events
        if recorded.entry.event_type is EventType.HUMAN_DECISION
    ]


def test_reviewer_kind_is_inside_the_ledgered_payload(
    dataset: duckdb.DuckDBPyConnection, subject: str
) -> None:
    """Saif's requirement. Not a side artifact: the ledgered payload, so the
    chain covers it."""
    session = _session()

    _run(session, dataset, subject, max_hops=1)

    payloads = _decision_payloads(session)
    assert payloads
    for payload in payloads:
        assert payload["reviewer_kind"] == ReviewerKind.SCRIPTED.value
        assert payload["by_human"] is False
        assert payload["reviewer_id"] == "saif"


def test_editing_reviewer_kind_breaks_the_payload_digest(
    dataset: duckdb.DuckDBPyConnection, subject: str
) -> None:
    """What "hash-covered" has to mean to be worth saying.

    The chain stores only the digest, so the claim is that a body edited after
    the fact no longer digests to the entry already in the chain. Demonstrated
    rather than asserted about: flip the field and recompute.
    """
    session = _session()
    _run(session, dataset, subject, max_hops=1)

    recorded = next(
        item
        for item in session.recorded_events
        if item.entry.event_type is EventType.HUMAN_DECISION
    )
    assert digest_payload(recorded.payload) == recorded.entry.payload_digest

    forged = dict(recorded.payload)
    forged["reviewer_kind"] = ReviewerKind.INTERACTIVE.value
    forged["by_human"] = True

    assert digest_payload(forged) != recorded.entry.payload_digest
    with pytest.raises(ValueError, match="body and the chain disagree"):
        session.attach_event(recorded.entry, forged)


def test_no_rendering_of_a_scripted_hop_can_read_as_human_approved(
    dataset: duckdb.DuckDBPyConnection, subject: str
) -> None:
    """Over every string a person could read, not just the ones we remembered.

    The turn's JSON, the ledger payloads and the attribution lines are all
    swept: anything that mentions approval has to also say what approved it.
    """
    session = _session()

    turn = _run(session, dataset, subject, max_hops=2)

    rendered = json.dumps(turn.to_json_object()) + json.dumps(_decision_payloads(session))
    assert "interactive" not in rendered
    assert '"by_human": true' not in rendered.lower()

    for hop in turn.hops:
        if hop.attribution is None:
            assert hop.outcome in {"refused", "budget_exhausted", "model_unavailable"}
            continue
        assert "scripted stand-in, no human present" in hop.attribution
        assert "approve" in hop.attribution or "reject" in hop.attribution


def test_a_review_outcome_cannot_omit_what_decided_it() -> None:
    """``reviewer_kind`` has no default, so an unattributed decision is
    unconstructible rather than merely discouraged."""
    with pytest.raises(TypeError):
        ReviewOutcome(  # type: ignore[call-arg]
            decision=ReviewDecision.APPROVE,
            reviewer_id="saif",
            reason="looks fine",
        )


def test_a_decision_states_its_reason_and_its_analyst() -> None:
    for kwargs, message in (
        ({"reviewer_id": "  "}, "names the analyst identity"),
        ({"reason": ""}, "states its reason"),
    ):
        fields: dict[str, object] = {
            "decision": ReviewDecision.APPROVE,
            "reviewer_kind": ReviewerKind.SCRIPTED,
            "reviewer_id": "saif",
            "reason": "looks fine",
            **kwargs,
        }
        with pytest.raises(ValueError, match=message):
            ReviewOutcome(**fields)  # type: ignore[arg-type]


def test_the_two_reviewers_declare_different_kinds() -> None:
    assert ScriptedReviewer().reviewer_kind is ReviewerKind.SCRIPTED
    assert InteractiveReviewer().reviewer_kind is ReviewerKind.INTERACTIVE


def test_the_scripted_reviewer_is_stateless_and_replays_identically() -> None:
    """Indexed by hop, not by a mutable counter, so the same hop always gets
    the same answer however many times a test replays it."""
    reviewer = ScriptedReviewer(
        reviewer_id="saif",
        decisions=(ReviewDecision.REJECT, ReviewDecision.APPROVE),
    )

    def ask(hop: int) -> ReviewDecision:
        return reviewer.review(
            ReviewRequest(
                case_id="case-0000",
                subject_id="chan_000000",
                hop_index=hop,
                pivot_kind="infra_overlap",
                template_id="pivot.infra_overlap.v1",
                template_sha256="b" * 64,
                param_hash="c" * 64,
                params={},
                summary="x",
                reason="y",
            )
        ).decision

    assert [ask(1), ask(2), ask(3)] == [
        ReviewDecision.REJECT,
        ReviewDecision.APPROVE,
        ReviewDecision.APPROVE,
    ]
    assert ask(1) is ReviewDecision.REJECT


# --------------------------------------------------------------------------
# The proposal format
# --------------------------------------------------------------------------


def test_the_stub_response_parses_as_a_proposal(
    dataset: duckdb.DuckDBPyConnection, subject: str
) -> None:
    session = _session()
    _run(session, dataset, subject, max_hops=1)

    prompt_sent = [
        recorded
        for recorded in session.recorded_events
        if recorded.entry.event_type is EventType.PROMPT_SENT
    ]

    assert prompt_sent, "a turn that sent no prompt proves nothing about proposals"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "PIVOT: infra_overlap",
        "REASON: something [prov-0000]",
        "I think we should look at the infrastructure overlap.",
        "PARAMS: subject_id=chan_000000",
    ],
)
def test_an_incomplete_proposal_parses_as_nothing(text: str) -> None:
    """``None`` rather than a partial proposal. A half-read proposal is the
    dangerous shape: a missing REASON would otherwise reach the analyst as an
    approval request with no stated reason."""
    assert parse_proposal(text) is None


def test_integers_are_coerced_only_when_unambiguous() -> None:
    proposal = parse_proposal(
        "PIVOT: infra_overlap\n"
        "PARAMS: subject_id=chan_000000; limit=25; signal_type=any; odd=25x\n"
        "REASON: because [prov-0000]"
    )

    assert proposal is not None
    assert proposal.params == {
        "subject_id": "chan_000000",
        "limit": 25,
        "signal_type": "any",
        "odd": "25x",
    }
    assert proposal.cited_ids == {"prov-0000"}


def test_the_pack_summary_reaches_the_model_as_fenced_data(
    dataset: duckdb.DuckDBPyConnection, subject: str
) -> None:
    """No pivot returns free text, so the firewall has little to find here. It
    is applied anyway: making an exception for data that "cannot" contain an
    injection is how the exception becomes the rule."""
    session = _session()

    _run(session, dataset, subject, max_hops=1)

    prompt = next(
        recorded
        for recorded in session.recorded_events
        if recorded.entry.event_type is EventType.PROMPT_SENT
    )
    assert "firewall" in prompt.payload
    firewall = prompt.payload["firewall"]
    assert isinstance(firewall, dict)
    assert "fence_nonce" in firewall


def test_a_seeded_pack_alone_still_lets_the_first_proposal_cite_something(
    subject: str,
) -> None:
    """The first hop has only the seed and its origin record to cite.

    Worth its own test: if the seed carried no provenance the agent's opening
    proposal could cite nothing, and every investigation would fail on its
    first move.
    """
    pack = EvidencePack.seed("case-0000", subject, EntityKind.CHANNEL, _START.isoformat())

    assert pack.record_ids == {subject, "prov-0000"}
