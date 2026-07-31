# SPDX-License-Identifier: MIT
"""D2: verification of pivot proposals (STEP-04 3.2).

Orchestrator-side, because the governance layer judges the agent and not the
other way round. This is the same placement decision STEP-03 made for
``rationale_check``, and for the same reason the import-graph test found then:
an agent holding its own verifier is an agent nobody is verifying.

What is checked, in order, and why that order
---------------------------------------------
1. **The text is a proposal at all.** A response that is not three fields
   cannot be acted on.
2. **The pivot name resolves** through the allowlist, where absence is denial.
3. **The reason cites a record that exists in the pack**, checked by STEP-02's
   symbolic verifier with the resolvable set being the pack's own record ids.
   This is the same reuse triage makes with score component ids, which is what
   that verifier was made generic for.
4. **The parameters are typed, in bounds, and name only entities already in the
   pack.**

The order matters at step 3 and 4 versus everything after: all four run
*before* the analyst is asked. An unsupported or malformed proposal never
reaches a human. That is not a convenience, it is the division of labour the
whole design rests on: the machine checks what is mechanically checkable, and
the analyst spends attention on the one question that is actually theirs, which
is whether this pivot is worth running.

Failures are returned, never raised (STEP-02 2.4), and each carries exactly one
reason code so refusals are countable by cause.
"""

from dataclasses import dataclass
from enum import StrEnum

from ts_sentry.agents.evidence.pack import EvidencePack
from ts_sentry.agents.evidence.proposal import PivotProposal, parse_proposal
from ts_sentry.governance.verifier import Claim, verify_claims
from ts_sentry.orchestrator.pivots import (
    PIVOT_TEMPLATES,
    ParamResult,
    PivotKind,
    PivotTemplate,
    PivotViolation,
    resolve_pivot_by_name,
    validate_params,
)

__all__ = ["ProposalRefusal", "ProposalVerdict", "check_proposal"]


class ProposalRefusal(StrEnum):
    """Why a proposal was refused before the analyst saw it."""

    MALFORMED = "malformed"
    UNKNOWN_PIVOT = "unknown_pivot"
    UNRESOLVED_CITATION = "unresolved_citation"
    INVALID_PARAMS = "invalid_params"


@dataclass(frozen=True, slots=True)
class ProposalVerdict:
    """Structured result. Never an exception, never a bare bool.

    ``template`` and ``values`` are populated only on acceptance, so there is no
    partially validated proposal for a caller to reach for by mistake, in the
    same shape ``ParamResult`` uses.
    """

    accepted: bool
    proposal: PivotProposal | None
    pivot_kind: PivotKind | None
    template: PivotTemplate | None
    values: dict[str, object]
    code: ProposalRefusal | None
    detail: str

    def __post_init__(self) -> None:
        if self.accepted is (self.code is not None):
            raise ValueError(
                "an accepted proposal carries no refusal code; a refused one carries one"
            )
        if self.accepted and (self.template is None or self.pivot_kind is None):
            raise ValueError("an accepted proposal names the template it resolved to")
        if not self.accepted and self.values:
            raise ValueError("a refused proposal carries no validated values")

    def to_ledger_payload(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "pivot_kind": None if self.pivot_kind is None else self.pivot_kind.value,
            "refusal_code": None if self.code is None else self.code.value,
            "detail": self.detail,
            "proposal": None if self.proposal is None else self.proposal.to_json_object(),
        }


def _refuse(
    code: ProposalRefusal,
    detail: str,
    *,
    proposal: PivotProposal | None = None,
    pivot_kind: PivotKind | None = None,
) -> ProposalVerdict:
    return ProposalVerdict(
        accepted=False,
        proposal=proposal,
        pivot_kind=pivot_kind,
        template=None,
        values={},
        code=code,
        detail=detail,
    )


def check_proposal(text: str, pack: EvidencePack) -> ProposalVerdict:
    """Check one model response against the pack it was asked about.

    Pure and total: no I/O, no ledger write, and no exception on any input,
    including input that is not a proposal at all. The caller ledgers, which is
    what keeps this callable from a test.
    """
    proposal = parse_proposal(text)
    if proposal is None:
        return _refuse(
            ProposalRefusal.MALFORMED,
            "the response is not a proposal: PIVOT and REASON are both required",
        )

    try:
        pivot_kind = resolve_pivot_by_name(proposal.pivot_name)
    except PivotViolation as exc:
        return _refuse(ProposalRefusal.UNKNOWN_PIVOT, str(exc), proposal=proposal)

    # STEP-02's verifier, with the resolvable set being this pack's own records.
    # Per proposal, not per session: a reason that cites a record from some
    # other investigation is exactly the confabulation the A-01 control exists
    # to catch.
    report = verify_claims(
        [Claim(text=proposal.reason, claimed_evidence_ids=proposal.cited_ids)],
        pack.record_ids,
    )
    if not report.passed:
        failure = report.results[0]
        unresolvable = ", ".join(failure.unresolvable_ids)
        return _refuse(
            ProposalRefusal.UNRESOLVED_CITATION,
            (
                "the reason cites nothing this pack carries"
                if not unresolvable
                else f"the reason cites {unresolvable}, which this pack does not carry"
            ),
            proposal=proposal,
            pivot_kind=pivot_kind,
        )

    template = PIVOT_TEMPLATES[pivot_kind]
    params: ParamResult = validate_params(template, proposal.params, known_ids=pack.node_ids)
    if not params.ok:
        return _refuse(
            ProposalRefusal.INVALID_PARAMS,
            params.detail,
            proposal=proposal,
            pivot_kind=pivot_kind,
        )

    return ProposalVerdict(
        accepted=True,
        proposal=proposal,
        pivot_kind=pivot_kind,
        template=template,
        values=dict(params.values),
        code=None,
        detail=f"{pivot_kind.value} proposed with resolvable citations and valid parameters",
    )
