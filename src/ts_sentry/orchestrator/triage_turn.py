# SPDX-License-Identifier: MIT
"""D5: one complete triage turn, end to end.

The ARCHITECTURE 3.3 pipeline, in order, for the one agent that exists:

    input firewall -> mandate check -> dispatch -> output schema check
        -> consequence gate -> ledger -> deliver to analyst

with the model call sitting *after* the deterministic tool rather than inside
it. That ordering is the design, not an accident of implementation:

1. The tool ranks the queue deterministically, and its output is what the
   consequence gate accepts. A ranking is the product, and it must be
   reproducible from the dataset alone.
2. Only then is the model asked for rationales, with the legal citations
   already known, because the citation menu *is* the scored queue.
3. Rationales are verified separately and ledgered separately, so a failed
   explanation is visible as a failed explanation rather than as a failed
   ranking.

Partial results are a first-class outcome. A turn whose model call is refused,
fails, or runs out of budget still delivers the ranked queue with no
rationales attached, and says so. STEP-03 3.3 requires exactly that for budget
exhaustion, and the same reasoning covers every other way the model can fail:
losing the explanation must not lose the work.
"""

from dataclasses import dataclass

import numpy as np

from ts_sentry.agents.triage.prompts import (
    TRIAGE_SYSTEM_PROMPT,
    RankedQueue,
    RankedRow,
    triage_instruction,
)
from ts_sentry.agents.triage.rationale import parse_rationale_lines, render_expected_form
from ts_sentry.agents.triage.scorer import PriorityScore
from ts_sentry.governance.gates import GateChecks
from ts_sentry.governance.ledger import EventType, LedgerEntry
from ts_sentry.governance.mandate import AgentId, ToolId
from ts_sentry.orchestrator.adapter import (
    ModelAdapter,
    ModelRequest,
    RetryPolicy,
    Sleeper,
    StubMode,
    call_model,
)
from ts_sentry.orchestrator.core import CloseReason, Session
from ts_sentry.orchestrator.detection_stub import case_records
from ts_sentry.orchestrator.dispatch import (
    DispatchDecision,
    DispatchOutcome,
    ToolProposal,
    dispatch,
)
from ts_sentry.orchestrator.firewall import apply_firewall, compose_user_content
from ts_sentry.orchestrator.rationale_check import RationaleResult, verify_rationales
from ts_sentry.orchestrator.tools import TOOL_TABLE, ToolResources, required_scope_names

__all__ = ["TriageTurn", "run_triage_turn", "stub_triage_responder"]

_MAX_OUTPUT_TOKENS = 2048


@dataclass(frozen=True, slots=True)
class TriageTurn:
    """Everything one turn produced, including the ways it fell short."""

    queue: RankedQueue | None
    dispatch_outcome: DispatchOutcome
    rationales: RationaleResult | None
    close_reason: CloseReason | None
    detail: str
    ledgered: tuple[LedgerEntry, ...]
    injection_signals: int

    @property
    def delivered(self) -> bool:
        return self.queue is not None


def stub_triage_responder(request: ModelRequest, mode: StubMode) -> str:
    """What the offline stub says when it is standing in for the triage model.

    Lives here rather than in the adapter because the adapter must not know
    what a rationale looks like - that contract is D5's, and encoding it in
    D4 would have been implementing ahead of the STEP. The adapter takes a
    responder; this is the one the triage path supplies.

    It reads the citation menu back out of the request rather than
    hard-coding component ids, so the stub tracks whatever the scorer
    produced instead of a snapshot of it. ``OVERCLAIM`` cites an id that
    cannot resolve, which is how the ``VERIFICATION_FAIL`` path gets
    demonstrated on a real session rather than only in a unit test.
    """
    lines: list[str] = []
    for line in request.user_content.splitlines():
        if ": cite only " not in line:
            continue
        case_id = line.split(":", 1)[0].strip()
        first_id = line.split("[", 1)[1].split("]", 1)[0]
        if mode is StubMode.OVERCLAIM:
            lines.append(f"{case_id}: confirmed abusive per [sealed:ground_truth]")
        else:
            lines.append(f"{case_id}: ranked here on [{first_id}]")
    return "\n".join(lines)


def run_triage_turn(
    session: Session,
    adapter: ModelAdapter,
    *,
    resources: ToolResources,
    checks: GateChecks,
    policy: RetryPolicy,
    rng: np.random.Generator,
    sleeper: Sleeper,
    limit: int = 25,
) -> TriageTurn:
    """Run the triage agent for one turn and return what it produced."""
    agent_id = AgentId.TRIAGE
    entry = TOOL_TABLE[ToolId.RANK_TRIAGE_QUEUE]
    ledgered: list[LedgerEntry] = []

    start = session.begin_turn(agent_id)
    if not start.started:
        # The step ceiling, refused before anything ran. Not an error.
        return TriageTurn(
            queue=None,
            dispatch_outcome=DispatchOutcome(
                decision=DispatchDecision.FAILED,
                tool_id=None,
                result=None,
                refusal_code=None,
                gate=None,
                detail=start.detail,
                ledgered=(),
            ),
            rationales=None,
            close_reason=start.close_reason,
            detail=start.detail,
            ledgered=(),
            injection_signals=0,
        )

    outcome = dispatch(
        session,
        ToolProposal(
            agent_id=agent_id,
            tool_name=ToolId.RANK_TRIAGE_QUEUE.value,
            requested_scope_names=required_scope_names(entry),
            params={"limit": limit},
        ),
        table=TOOL_TABLE,
        checks=checks,
        resources=resources,
    )
    ledgered.extend(outcome.ledgered)

    if not outcome.executed or not isinstance(outcome.result, RankedQueue):
        session.end_turn()
        return TriageTurn(
            queue=None,
            dispatch_outcome=outcome,
            rationales=None,
            close_reason=CloseReason.DISPATCH_ERROR,
            detail=outcome.detail,
            ledgered=tuple(ledgered),
            injection_signals=0,
        )

    queue = outcome.result
    scores: list[PriorityScore] = [row.score for row in queue.rows]

    # Case content enters here and nowhere else, wrapped before it is composed
    # with anything. The verbatim block is what the artifact keeps; the
    # redacted copy is what the model sees.
    connection = resources.connection
    records = case_records(connection, _cases_for(queue)) if connection is not None else ()
    firewalled = apply_firewall(records)
    user_content = compose_user_content(
        triage_instruction(render_expected_form(scores)), firewalled
    )

    call = call_model(
        session,
        agent_id,
        adapter,
        ModelRequest(
            system=TRIAGE_SYSTEM_PROMPT,
            user_content=user_content,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
        ),
        policy=policy,
        rng=rng,
        sleeper=sleeper,
        firewall_payload=firewalled.to_ledger_payload(),
    )
    ledgered.extend(call.ledgered)

    if call.response is None:
        # Partial delivery: the ranking stands, the explanations do not.
        session.end_turn()
        return TriageTurn(
            queue=queue,
            dispatch_outcome=outcome,
            rationales=None,
            close_reason=call.close_reason,
            detail=f"ranked queue delivered without rationales: {call.detail}",
            ledgered=tuple(ledgered),
            injection_signals=len(firewalled.signals),
        )

    verified = verify_rationales(scores, parse_rationale_lines(call.response.text))
    ledgered.append(
        session.append_event(
            EventType.VERIFICATION_PASS if verified.all_passed else EventType.VERIFICATION_FAIL,
            agent_id=agent_id,
            payload=verified.to_ledger_payload(),
        ).entry
    )

    accepted = {item.case_id: item for item in verified.accepted}
    delivered = RankedQueue(
        rows=tuple(
            RankedRow(
                score=row.score,
                subject_id=row.subject_id,
                rationale=None
                if row.score.case_id not in accepted
                else accepted[row.score.case_id].text,
            )
            for row in queue.rows
        ),
        weights_version=queue.weights_version,
        detector_version=queue.detector_version,
    )
    ledgered.append(
        session.append_event(
            EventType.OUTPUT_PROPOSED,
            agent_id=agent_id,
            payload={
                "rows": len(delivered.rows),
                "rationales": delivered.rationale_count,
                "weights_version": delivered.weights_version,
                "detector_version": delivered.detector_version,
            },
        ).entry
    )
    session.end_turn()

    return TriageTurn(
        queue=delivered,
        dispatch_outcome=outcome,
        rationales=verified,
        close_reason=None,
        detail=(
            f"{len(delivered.rows)} cases ranked, "
            f"{delivered.rationale_count} rationales accepted, "
            f"{len(verified.rejected)} rejected"
        ),
        ledgered=tuple(ledgered),
        injection_signals=len(firewalled.signals),
    )


def _cases_for(queue: RankedQueue) -> tuple[tuple[str, str], ...]:
    """The (case id, subject id) pairs whose platform text this turn will read.

    Reads the subject off the row rather than re-deriving it. An earlier draft
    reconstructed the channel id from the case id, which is not a mapping that
    exists: case ids are positional and say nothing about the entity. That
    would have fetched content for the wrong channel, or for none, and the
    rationales would have described whatever came back.
    """
    return tuple((row.score.case_id, row.subject_id) for row in queue.rows)
