# SPDX-License-Identifier: MIT
"""D3: the dispatch pipeline (STEP-03 D3, ARCHITECTURE 3.3 and 5.2).

    mandate check -> allowlisted tool table -> execute -> schema check
        -> consequence gate -> ledger

An agent proposes; this module disposes. Nothing here decides *what* to do,
only whether what was proposed may happen, and it refuses by not executing
rather than by executing and reporting.

The agent's side of the boundary is string-typed on purpose
-----------------------------------------------------------
``ToolProposal`` carries a tool *name* and scope *names*, not validated enum
members. That is the real shape of the boundary: an agent does not hand the
orchestrator a ``ToolId``, it hands it something like
``"rank_triage_queue"``, and on a bad day ``"sealed._labels"``. STEP-02 made
this argument for scopes when it took a ``str`` in ``guard_scope_request``;
tools get the same treatment, resolved through an allowlist where absence is
denial.

Consequence is taken from the table, never from the proposal, so an agent
cannot understate what an action costs in order to fit under its ceiling.

Where refusals are ledgered, and why validate() still does not
--------------------------------------------------------------
``mandate.validate`` stays exactly as STEP-02 shipped it: pure, total, no I/O,
no ledger write. This module is the caller that ledgers, which is what keeps
``validate`` callable from a test or a gate without a database behind it.
Every refusal below writes to the chain before returning, and scope refusals
go through the existing ``gates.guard_scope_request`` rather than a
reimplementation, with the payload bridged back into the session so the body
is not lost.

Two event-type readings, recorded rather than assumed
-----------------------------------------------------
ARCHITECTURE 3.2 fixes the eleven event types, and two dispatch outcomes are
not obviously any of them. Both choices are recorded here for the same reason
STEP-02 recorded its ENFORCE-event reading:

1. **A declared tool with no handler in this build** is ledgered as
   ``GATE_REJECTION`` alone, never ``MANDATE_VIOLATION_ATTEMPT``. Nothing was
   violated: the agent asked for a tool it is entitled to use, and this build
   cannot run it yet. Recording a build limitation as a governance violation
   would inflate the exact metric this system showcases, which would be
   self-serving in the worst direction.
2. **A handler that raised** is ledgered as ``TOOL_RESULT`` carrying the
   failure, and no gate runs. A crashed tool produced no artifact, and a gate
   that ran on nothing would be manufacturing a verdict.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from ts_sentry.governance.gates import GateChecks, GateOutcome, guard_scope_request, run_gate
from ts_sentry.governance.ledger import EventType, LedgerEntry
from ts_sentry.governance.mandate import (
    AgentId,
    ProposedAction,
    RefusalCode,
    ToolId,
    validate,
)
from ts_sentry.governance.scopes import DataScope
from ts_sentry.orchestrator.core import Session
from ts_sentry.orchestrator.toolspec import (
    ToolContext,
    ToolEntry,
    ToolResources,
    ToolViolation,
    resolve_tool_by_name,
)

NO_RESOURCES = ToolResources()
"""The empty lending set, and the default. A tool that needs a connection
and was given none refuses rather than reaching for one."""

__all__ = [
    "NO_RESOURCES",
    "DispatchDecision",
    "DispatchOutcome",
    "ToolProposal",
    "dispatch",
]


class DispatchDecision(StrEnum):
    """What became of a proposal.

    ``REFUSED`` and ``FAILED`` are separate because they answer different
    questions. Refused means the governance layer did not let it run, which is
    the layer working. Failed means it ran and broke, which is a defect.
    Collapsing them would make a bug look like a policy success.
    """

    EXECUTED = "executed"
    REFUSED = "refused"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ToolProposal:
    """One action an agent proposes, in the agent's own vocabulary."""

    agent_id: AgentId
    tool_name: str
    requested_scope_names: tuple[str, ...]
    params: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    """Structured result. Never an exception, never a bare bool."""

    decision: DispatchDecision
    tool_id: ToolId | None
    result: object | None
    refusal_code: RefusalCode | None
    gate: GateOutcome | None
    detail: str
    ledgered: tuple[LedgerEntry, ...]

    def __post_init__(self) -> None:
        refused = self.decision is DispatchDecision.REFUSED
        if not refused and self.refusal_code is not None:
            raise ValueError("only a refused dispatch carries a RefusalCode")
        if refused and self.refusal_code is None and (self.gate is None or self.gate.accepted):
            raise ValueError(
                "a refused dispatch names a RefusalCode or carries a rejected gate; a refusal "
                "with neither has no recorded cause"
            )
        if self.decision is not DispatchDecision.EXECUTED and self.result is not None:
            raise ValueError("only an executed dispatch carries a result")

    @property
    def executed(self) -> bool:
        return self.decision is DispatchDecision.EXECUTED


def _refuse(
    session: Session,
    proposal: ToolProposal,
    code: RefusalCode,
    detail: str,
    *,
    event_type: EventType = EventType.MANDATE_VIOLATION_ATTEMPT,
    tool_id: ToolId | None = None,
    already_ledgered: Sequence[LedgerEntry] = (),
) -> DispatchOutcome:
    """Ledger a refusal, then return it. In that order, always.

    Obligation from the STEP-02 Outcome: the orchestrator ledgers every
    refusal. Writing the entry inside this one helper is what makes that
    checkable by reading one function rather than by auditing every branch.
    """
    recorded = session.append_event(
        event_type,
        agent_id=proposal.agent_id,
        payload={
            "agent_id": proposal.agent_id.value,
            "tool_name": proposal.tool_name,
            "requested_scopes": sorted(proposal.requested_scope_names),
            "refusal_code": code.value,
            "detail": detail,
        },
    )
    return DispatchOutcome(
        decision=DispatchDecision.REFUSED,
        tool_id=tool_id,
        result=None,
        refusal_code=code,
        gate=None,
        detail=detail,
        ledgered=(*already_ledgered, recorded.entry),
    )


def dispatch(
    session: Session,
    proposal: ToolProposal,
    *,
    table: Mapping[ToolId, ToolEntry],
    checks: GateChecks,
    resources: ToolResources = NO_RESOURCES,
) -> DispatchOutcome:
    """Run one proposal through the full pipeline.

    ``table`` and ``checks`` are required rather than defaulted, following
    ``GateChecks``: there must be no way to dispatch without naming the
    allowlist it is dispatched against, so an unconfigured call cannot
    silently execute anything.

    ``resources`` is what the orchestrator lends the handler for this call and
    defaults to empty, which is the safe direction: a tool that needs a
    database connection and was given none refuses, rather than reaching for
    one. It is kept separate from ``proposal.params`` because params are the
    agent's and resources are not.

    Ordering is load-bearing. Mandate validation runs *before* the handler
    availability check, so a tool this agent may not use is refused as a
    governance matter whether or not this build could have run it. The
    opposite order would let a build limitation mask a real violation, and
    the violation is the thing worth recording.
    """
    binding = session.binding(proposal.agent_id)
    ledgered: list[LedgerEntry] = []

    try:
        tool_id = resolve_tool_by_name(proposal.tool_name)
    except ToolViolation:
        return _refuse(
            session,
            proposal,
            RefusalCode.TOOL_NOT_ALLOWED,
            f"no tool resolves {proposal.tool_name!r}; the allowlist has no such member, "
            "so absence is denial",
        )

    entry = table.get(tool_id)
    if entry is None:
        return _refuse(
            session,
            proposal,
            RefusalCode.TOOL_NOT_ALLOWED,
            f"tool {tool_id.value} has no entry in the allowlisted tool table",
            tool_id=tool_id,
        )

    granted: set[DataScope] = set()
    for name in proposal.requested_scope_names:
        guard = guard_scope_request(
            session.ledger,
            session.token,
            timestamp_ist=session.now(),
            agent_id=proposal.agent_id,
            mandate=binding.mandate,
            mandate_hash=binding.hash,
            requested_name=name,
        )
        if guard.ledgered is not None and guard.payload is not None:
            session.attach_event(guard.ledgered, guard.payload)
            ledgered.append(guard.ledgered)
        if not guard.granted:
            assert guard.code is not None  # a refused guard always carries one
            return _refuse(
                session,
                proposal,
                guard.code,
                guard.detail,
                tool_id=tool_id,
                already_ledgered=ledgered,
            )
        assert guard.scope is not None
        granted.add(guard.scope)

    missing = sorted(scope.value for scope in entry.required_scopes - granted)
    if missing:
        return _refuse(
            session,
            proposal,
            RefusalCode.SCOPE_NOT_ALLOWED,
            f"tool {tool_id.value} requires scopes that were not granted: {', '.join(missing)}",
            tool_id=tool_id,
            already_ledgered=ledgered,
        )

    action = ProposedAction(
        agent_id=proposal.agent_id,
        tool_id=tool_id,
        consequence=entry.consequence,
        requested_scopes=frozenset(granted),
    )
    verdict = validate(action, binding.mandate)
    if not verdict.allowed:
        assert verdict.code is not None  # a REFUSE verdict always carries one
        return _refuse(
            session,
            proposal,
            verdict.code,
            verdict.detail,
            tool_id=tool_id,
            already_ledgered=ledgered,
        )

    if entry.handler is None:
        return _refuse(
            session,
            proposal,
            RefusalCode.TOOL_HANDLER_NOT_IN_BUILD,
            f"tool {tool_id.value} is declared in the allowlist but its handler lands in "
            f"STEP-{entry.handler_due_step:02d}; nothing was violated and nothing ran",
            event_type=EventType.GATE_REJECTION,
            tool_id=tool_id,
            already_ledgered=ledgered,
        )

    ledgered.append(
        session.append_event(
            EventType.TOOL_CALLED,
            agent_id=proposal.agent_id,
            payload={
                "tool_id": tool_id.value,
                "consequence": entry.consequence.value,
                "granted_scopes": sorted(scope.value for scope in granted),
                "params": dict(proposal.params),
            },
        ).entry
    )

    context = ToolContext(
        agent_id=proposal.agent_id.value,
        granted_scopes=frozenset(granted),
        params=proposal.params,
        resources=resources,
    )
    try:
        result = entry.handler(context)
    except Exception as exc:  # noqa: BLE001 - deliberate fail-closed boundary
        detail = f"{type(exc).__name__} raised while executing {tool_id.value}: {exc}"
        ledgered.append(
            session.append_event(
                EventType.TOOL_RESULT,
                agent_id=proposal.agent_id,
                payload={"tool_id": tool_id.value, "ok": False, "error": detail},
            ).entry
        )
        return DispatchOutcome(
            decision=DispatchDecision.FAILED,
            tool_id=tool_id,
            result=None,
            refusal_code=None,
            gate=None,
            detail=detail,
            ledgered=tuple(ledgered),
        )

    ledgered.append(
        session.append_event(
            EventType.TOOL_RESULT,
            agent_id=proposal.agent_id,
            payload={
                "tool_id": tool_id.value,
                "ok": True,
                "result_type": type(result).__name__,
            },
        ).entry
    )

    if not isinstance(result, binding.mandate.output_schema):
        detail = (
            f"tool {tool_id.value} returned {type(result).__name__}; the "
            f"{proposal.agent_id.value} mandate declares "
            f"{binding.mandate.output_schema.__name__}"
        )
        ledgered.append(
            session.append_event(
                EventType.VERIFICATION_FAIL,
                agent_id=proposal.agent_id,
                payload={"tool_id": tool_id.value, "schema_error": detail},
            ).entry
        )
        ledgered.append(
            session.append_event(
                EventType.GATE_REJECTION,
                agent_id=proposal.agent_id,
                payload={"tool_id": tool_id.value, "schema_error": detail},
            ).entry
        )
        return DispatchOutcome(
            decision=DispatchDecision.FAILED,
            tool_id=tool_id,
            result=None,
            refusal_code=None,
            gate=None,
            detail=detail,
            ledgered=tuple(ledgered),
        )

    gate = run_gate(
        session.ledger,
        session.token,
        timestamp_ist=session.now(),
        agent_id=proposal.agent_id,
        mandate_hash=binding.hash,
        consequence=entry.consequence,
        artifact=result,
        checks=checks,
    )
    for gate_entry, gate_payload in zip(gate.ledgered, gate.ledgered_payloads, strict=True):
        session.attach_event(gate_entry, gate_payload)
        ledgered.append(gate_entry)

    if not gate.accepted:
        # No RefusalCode here, deliberately. RefusalCode answers "why was this
        # action outside its mandate", and a gate rejection is a different
        # finding: the action was within the mandate and its *output* did not
        # pass. The rejected gate is the cause, and it carries the failures.
        return DispatchOutcome(
            decision=DispatchDecision.REFUSED,
            tool_id=tool_id,
            result=None,
            refusal_code=None,
            gate=gate,
            detail="; ".join(failure.detail for failure in gate.failures),
            ledgered=tuple(ledgered),
        )

    return DispatchOutcome(
        decision=DispatchDecision.EXECUTED,
        tool_id=tool_id,
        result=result,
        refusal_code=None,
        gate=gate,
        detail=f"{tool_id.value} executed and passed the {entry.consequence.value} gate",
        ledgered=tuple(ledgered),
    )
