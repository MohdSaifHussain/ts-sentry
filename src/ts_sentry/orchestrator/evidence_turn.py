# SPDX-License-Identifier: MIT
"""D2: the evidence investigation, one ledgered hop at a time.

The ARCHITECTURE 3.3 pipeline for an ASSEMBLE agent, with the human decision
sitting between the proposal and the execution:

    begin turn (books one step)
      -> firewall the pack summary -> model proposes
      -> verify: parses, resolves, cites the pack, parameters in bounds
      -> await analyst -> HUMAN_DECISION ledgered, with what decided it
      -> reject: terminal for this proposal, agent may propose another
      -> approve: dispatch -> execute -> schema check -> ASSEMBLE gate -> ledger
      -> resume

``SessionState.AWAITING_ANALYST`` gets its first driver here. STEP-03 shipped
the state with no path into it and its docstring said STEP-05 would supply one;
STEP-04 does, because a pivot is the first thing an agent produces that a human
must decide on before anything happens.

Rejection is terminal for a proposal, not for the turn (STEP-04 3.3). The agent
may propose an alternative, and the rejected pivot is named back to it so the
step budget is not spent re-proposing something already refused. It is never
told *why* it was refused: the analyst's reasoning is a human judgment recorded
in the ledger, not training signal fed back into the next prompt.

Partial delivery, as everywhere else in this system
---------------------------------------------------
A turn whose budget runs out, whose model fails, or whose every proposal is
rejected still delivers the pack it has, and says so. STEP-03 3.3 requires that
for budget exhaustion and the same reasoning covers the rest: losing the next
hop must not lose the hops already gathered and gated.
"""

from dataclasses import dataclass, field

import numpy as np

from ts_sentry.agents.evidence.pack import EvidencePack
from ts_sentry.agents.evidence.prompts import EVIDENCE_SYSTEM_PROMPT, evidence_instruction
from ts_sentry.data.enums import EntityKind
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
from ts_sentry.orchestrator.dispatch import ToolProposal, dispatch
from ts_sentry.orchestrator.firewall import CaseRecord, apply_firewall, compose_user_content
from ts_sentry.orchestrator.pivot_tool import PIVOT_KIND_PARAM
from ts_sentry.orchestrator.pivots import (
    PIVOT_TEMPLATES,
    PivotKind,
    param_hash,
    template_sha256,
)
from ts_sentry.orchestrator.proposal_check import ProposalRefusal, check_proposal
from ts_sentry.orchestrator.review import AnalystReviewer, ReviewRequest
from ts_sentry.orchestrator.tools import TOOL_TABLE, ToolResources, required_scope_names

__all__ = ["EvidenceTurn", "HopRecord", "run_evidence_turn", "stub_evidence_responder"]

_MAX_OUTPUT_TOKENS = 1024


@dataclass(frozen=True, slots=True)
class HopRecord:
    """One attempted hop, whatever became of it.

    Rejected and refused hops are kept alongside executed ones. A session
    artifact that showed only the pivots that ran would describe an
    investigation that went straight to its answer, which is not what happened
    and not what an auditor needs to see.
    """

    hop_index: int
    pivot_kind: str | None
    outcome: str
    detail: str
    attribution: str | None
    params: dict[str, object] = field(default_factory=dict)

    def to_json_object(self) -> dict[str, object]:
        return {
            "hop_index": self.hop_index,
            "pivot_kind": self.pivot_kind,
            "outcome": self.outcome,
            "detail": self.detail,
            # Always present when a human decision was taken, and always says
            # which mechanism took it. There is no rendering of an approval in
            # this system that omits it.
            "attribution": self.attribution,
            "params": dict(self.params),
        }


@dataclass(frozen=True, slots=True)
class EvidenceTurn:
    """Everything one investigation produced, including how it fell short."""

    pack: EvidencePack
    hops: tuple[HopRecord, ...]
    close_reason: CloseReason | None
    detail: str
    ledgered: tuple[LedgerEntry, ...]
    injection_signals: int

    @property
    def executed_hops(self) -> int:
        return sum(1 for hop in self.hops if hop.outcome == "executed")

    @property
    def rejected_hops(self) -> int:
        return sum(1 for hop in self.hops if hop.outcome == "rejected")

    def to_json_object(self) -> dict[str, object]:
        return {
            "case_id": self.pack.case_id,
            "subject_id": self.pack.subject_id,
            "close_reason": None if self.close_reason is None else self.close_reason.value,
            "detail": self.detail,
            "attempted_hops": len(self.hops),
            "executed_hops": self.executed_hops,
            "rejected_hops": self.rejected_hops,
            "injection_signals": self.injection_signals,
            "hops": [hop.to_json_object() for hop in self.hops],
            "pack": self.pack.to_json_object(),
        }


def stub_evidence_responder(request: ModelRequest, mode: StubMode) -> str:
    """What the offline stub says when standing in for the evidence model.

    Lives here rather than in the adapter for the reason
    ``stub_triage_responder`` does: the adapter must not know what a proposal
    looks like, and encoding that there would be implementing D4's contract
    inside D3's module.

    It reads the pack back out of the request rather than hard-coding ids, so
    the stub tracks whatever the pack actually contains. Under ``OVERCLAIM`` it
    cites an id no pack carries, which is how the ``UNRESOLVED_CITATION`` path
    gets demonstrated on a real session rather than only in a unit test.

    The choice of pivot is not arbitrary, and the reason is a finding rather
    than a preference. An investigation seeds on a *channel*, and infrastructure
    hints in this dataset attach to *accounts*: a channel has no signals of its
    own. A stub that opened with ``INFRA_OVERLAP`` on the subject therefore
    returned zero rows on every hop, forever, while every test about the loop
    passed. That is the STEP-03 finding again in a new place, where the
    machinery is right and the product finds nothing.

    So the strategy is the one an analyst actually uses: reach the accounts
    first with ``ACCOUNT_LINK``, then pivot on an account's infrastructure. The
    stub reads which of those it is in from the prompt, the way a model would.
    """
    subject = ""
    accounts: list[str] = []
    citable: list[str] = []
    for raw in request.user_content.splitlines():
        line = raw.strip()
        if raw.startswith("Case ") and " concerns " in raw:
            subject = raw.split(" concerns ", 1)[1].split(".", 1)[0].strip()
        if line.startswith("["):
            record_id = line[1:].split(" ", 1)[0].rstrip("]")
            citable.append(record_id)
            if line.endswith("(account)"):
                accounts.append(record_id)

    citation = (
        "prov-9999" if mode is StubMode.OVERCLAIM else (citable[0] if citable else "prov-0000")
    )

    if not accounts:
        return "\n".join(
            (
                f"PIVOT: {PivotKind.ACCOUNT_LINK.value}",
                f"PARAMS: channel_id={subject}; min_comments=1; limit=25",
                f"REASON: the accounts touching this channel are not yet in the pack [{citation}]",
            )
        )
    return "\n".join(
        (
            f"PIVOT: {PivotKind.INFRA_OVERLAP.value}",
            f"PARAMS: subject_id={accounts[0]}; signal_type=any; limit=25",
            f"REASON: this account may share infrastructure with others [{citation}]",
        )
    )


def _pack_records(pack: EvidencePack) -> tuple[CaseRecord, ...]:
    """The pack, as records for the input firewall.

    No pivot returns a free-text column, so nothing here is user-authored prose
    and the firewall has little to find. It is applied anyway, and deliberately:
    entity ids and signal values still originate on the platform, and this
    system has exactly one route by which platform-derived data reaches a model.
    Making an exception for data that "cannot" contain an injection is how the
    exception becomes the rule.
    """
    records = [
        CaseRecord(
            record_id=node.node_id,
            source=f"pack.node.{node.kind.value}",
            text="; ".join(f"{name}={node.attributes[name]}" for name in sorted(node.attributes)),
        )
        for node in pack.nodes
    ]
    records.extend(
        CaseRecord(
            record_id=edge.edge_id,
            source=f"pack.edge.{edge.relation.value}",
            text=f"{edge.source_id} -> {edge.target_id}",
        )
        for edge in pack.edges
    )
    return tuple(records)


def run_evidence_turn(
    session: Session,
    adapter: ModelAdapter,
    *,
    case_id: str,
    subject_id: str,
    reviewer: AnalystReviewer,
    resources: ToolResources,
    checks: GateChecks,
    policy: RetryPolicy,
    rng: np.random.Generator,
    sleeper: Sleeper,
    subject_kind: EntityKind = EntityKind.CHANNEL,
    max_hops: int | None = None,
) -> EvidenceTurn:
    """Investigate one case until the budget, the analyst, or the model stops it."""
    agent_id = AgentId.EVIDENCE
    entry = TOOL_TABLE[ToolId.RUN_PARAMETERIZED_PIVOT]
    binding = session.binding(agent_id)
    ceiling = binding.mandate.max_steps if max_hops is None else max_hops

    pack = EvidencePack.seed(case_id, subject_id, subject_kind, session.now().isoformat())
    hops: list[HopRecord] = []
    ledgered: list[LedgerEntry] = []
    rejected_names: list[str] = []
    close_reason: CloseReason | None = None
    signals = 0

    for attempt in range(1, ceiling + 1):
        start = session.begin_turn(agent_id)
        if not start.started:
            close_reason = start.close_reason
            hops.append(
                HopRecord(
                    hop_index=attempt,
                    pivot_kind=None,
                    outcome="budget_exhausted",
                    detail=start.detail,
                    attribution=None,
                )
            )
            break

        firewalled = apply_firewall(_pack_records(pack))
        signals += len(firewalled.signals)
        call = call_model(
            session,
            agent_id,
            adapter,
            ModelRequest(
                system=EVIDENCE_SYSTEM_PROMPT,
                user_content=compose_user_content(
                    evidence_instruction(pack, rejected_names), firewalled
                ),
                max_output_tokens=_MAX_OUTPUT_TOKENS,
            ),
            policy=policy,
            rng=rng,
            sleeper=sleeper,
            firewall_payload=firewalled.to_ledger_payload(),
        )
        ledgered.extend(call.ledgered)

        if call.response is None:
            session.end_turn()
            close_reason = call.close_reason
            hops.append(
                HopRecord(
                    hop_index=attempt,
                    pivot_kind=None,
                    outcome="model_unavailable",
                    detail=call.detail,
                    attribution=None,
                )
            )
            break

        verdict = check_proposal(call.response.text, pack)
        ledgered.append(
            session.append_event(
                EventType.VERIFICATION_PASS if verdict.accepted else EventType.VERIFICATION_FAIL,
                agent_id=agent_id,
                payload=verdict.to_ledger_payload(),
            ).entry
        )

        if not verdict.accepted:
            session.end_turn()
            hops.append(
                HopRecord(
                    hop_index=attempt,
                    pivot_kind=None if verdict.pivot_kind is None else verdict.pivot_kind.value,
                    outcome="refused",
                    detail=verdict.detail,
                    attribution=None,
                )
            )
            # An unverifiable proposal never reaches the analyst. Their
            # attention is for the question that is theirs, which is whether a
            # well-formed pivot is worth running.
            if verdict.code is ProposalRefusal.UNRESOLVED_CITATION:
                rejected_names.append("a proposal citing evidence the pack does not carry")
            continue

        assert verdict.pivot_kind is not None and verdict.template is not None
        template = PIVOT_TEMPLATES[verdict.pivot_kind]

        session.await_analyst()
        outcome = reviewer.review(
            ReviewRequest(
                case_id=case_id,
                subject_id=subject_id,
                hop_index=attempt,
                pivot_kind=verdict.pivot_kind.value,
                template_id=template.template_id,
                template_sha256=template_sha256(template),
                param_hash=param_hash(verdict.values),
                params=dict(verdict.values),
                summary=template.summary,
                reason="" if verdict.proposal is None else verdict.proposal.reason,
            )
        )
        decision_payload = {
            "hop_index": attempt,
            "case_id": case_id,
            "subject_id": subject_id,
            "pivot_kind": verdict.pivot_kind.value,
            "template_id": template.template_id,
            "param_hash": param_hash(verdict.values),
            "params": dict(verdict.values),
            **outcome.to_ledger_payload(),
        }
        ledgered.append(
            session.append_event(
                EventType.HUMAN_DECISION, agent_id=agent_id, payload=decision_payload
            ).entry
        )

        if not outcome.approved:
            session.resume()
            rejected_names.append(verdict.pivot_kind.value)
            hops.append(
                HopRecord(
                    hop_index=attempt,
                    pivot_kind=verdict.pivot_kind.value,
                    outcome="rejected",
                    detail=outcome.reason,
                    attribution=outcome.attribution(),
                    params=dict(verdict.values),
                )
            )
            continue

        dispatched = dispatch(
            session,
            ToolProposal(
                agent_id=agent_id,
                tool_name=ToolId.RUN_PARAMETERIZED_PIVOT.value,
                requested_scope_names=required_scope_names(entry),
                params={PIVOT_KIND_PARAM: verdict.pivot_kind.value, **verdict.values},
            ),
            table=TOOL_TABLE,
            checks=checks,
            resources=ToolResources(
                connection=resources.connection,
                seed=resources.seed,
                pack=pack,
                retrieval_ts=session.now(),
            ),
        )
        ledgered.extend(dispatched.ledgered)
        session.resume()

        if dispatched.executed and isinstance(dispatched.result, EvidencePack):
            pack = dispatched.result
            hops.append(
                HopRecord(
                    hop_index=attempt,
                    pivot_kind=verdict.pivot_kind.value,
                    outcome="executed",
                    detail=f"{pack.provenance[-1].row_count} rows",
                    attribution=outcome.attribution(),
                    params=dict(verdict.values),
                )
            )
            continue

        hops.append(
            HopRecord(
                hop_index=attempt,
                pivot_kind=verdict.pivot_kind.value,
                outcome="dispatch_failed",
                detail=dispatched.detail,
                attribution=outcome.attribution(),
                params=dict(verdict.values),
            )
        )
        close_reason = CloseReason.DISPATCH_ERROR
        break

    executed = sum(1 for hop in hops if hop.outcome == "executed")
    return EvidenceTurn(
        pack=pack,
        hops=tuple(hops),
        close_reason=close_reason,
        detail=(
            f"{executed} pivots executed, "
            f"{sum(1 for hop in hops if hop.outcome == 'rejected')} rejected by the analyst, "
            f"{sum(1 for hop in hops if hop.outcome == 'refused')} refused before review; "
            f"pack holds {len(pack.nodes)} entities and {len(pack.edges)} relations"
        ),
        ledgered=tuple(ledgered),
        injection_signals=signals,
    )
