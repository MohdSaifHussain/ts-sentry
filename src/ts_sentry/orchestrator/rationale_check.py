# SPDX-License-Identifier: MIT
"""D5: verification of triage rationales (STEP-03 3.5).

Orchestrator-side, because the governance layer judges the agent and not the
other way round. This module reuses STEP-02's symbolic verifier exactly as it
was written - ``verify_claims`` with the resolvable ids set to one row's four
component ids - which is what that verifier was made generic for.

Why it is not in ``agents/``
---------------------------
The first draft put it there, and the import-graph test failed: reaching
``governance.verifier`` also reaches ``governance.gates`` and through it
``governance.signature``. The rule could have been widened. It was not,
because the failure was pointing at something true - an agent holding its own
verifier is an agent nobody is verifying - and moving the code fixed the
design rather than the symptom.

Failure is partial, not total
-----------------------------
A rationale that cites something unresolvable is dropped and the failure is
ledgered, but the row keeps its deterministic score and stays in the queue.
The score is the product; the rationale is the explanation. Losing an
explanation must not lose the work, which is the same reasoning STEP-03 3.3
applies to budget exhaustion.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ts_sentry.agents.triage.rationale import parse_citations
from ts_sentry.agents.triage.scorer import PriorityScore
from ts_sentry.governance.verifier import Claim, ClaimResult, verify_claims

__all__ = ["RationaleResult", "VerifiedRationale", "verify_rationales"]


@dataclass(frozen=True, slots=True)
class VerifiedRationale:
    """One case's rationale after checking.

    Carries the text either way. A rejected rationale is evidence about the
    model, and discarding it would remove the only record of what was
    proposed.
    """

    case_id: str
    text: str
    accepted: bool
    cited_ids: frozenset[str]
    result: ClaimResult

    def to_json_object(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "accepted": self.accepted,
            "text": self.text,
            "cited_ids": sorted(self.cited_ids),
            "reason": None if self.result.reason is None else self.result.reason.value,
            "unresolvable_ids": list(self.result.unresolvable_ids),
        }


@dataclass(frozen=True, slots=True)
class RationaleResult:
    """The whole batch, plus the counts a session reports."""

    rationales: tuple[VerifiedRationale, ...]

    @property
    def accepted(self) -> tuple[VerifiedRationale, ...]:
        return tuple(item for item in self.rationales if item.accepted)

    @property
    def rejected(self) -> tuple[VerifiedRationale, ...]:
        return tuple(item for item in self.rationales if not item.accepted)

    @property
    def all_passed(self) -> bool:
        return not self.rejected

    def to_ledger_payload(self) -> dict[str, object]:
        return {
            "rationales": len(self.rationales),
            "accepted": len(self.accepted),
            "rejected": len(self.rejected),
            "rejected_cases": [item.case_id for item in self.rejected],
        }

    def to_json_object(self) -> dict[str, object]:
        return {
            "accepted": len(self.accepted),
            "rejected": len(self.rejected),
            "rationales": [item.to_json_object() for item in self.rationales],
        }


def verify_rationales(
    scores: Sequence[PriorityScore],
    rationales: Mapping[str, str],
) -> RationaleResult:
    """Check each rationale against its own case's component ids.

    Per case, not per batch: the resolvable set is one row's four ids, so a
    rationale that cites a *different* case's velocity fails. A batch-wide set
    would have accepted it, and "this case is urgent because another case is
    fast" is exactly the confabulation the A-01 control exists to catch.

    Pure: no I/O and no ledger write. The caller ledgers, which is what keeps
    this callable from a test.
    """
    verified: list[VerifiedRationale] = []
    for item in scores:
        text = rationales.get(item.case_id, "")
        cited = parse_citations(text)
        report = verify_claims([Claim(text=text, claimed_evidence_ids=cited)], item.evidence_ids)
        result = report.results[0]
        verified.append(
            VerifiedRationale(
                case_id=item.case_id,
                text=text,
                accepted=result.passed,
                cited_ids=cited,
                result=result,
            )
        )
    return RationaleResult(rationales=tuple(verified))
