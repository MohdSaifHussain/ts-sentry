# SPDX-License-Identifier: MIT
"""D5: the claim-to-evidence symbolic verifier.

The A-01 control (ARCHITECTURE 2.2) and NIST AI 600-1's confabulation
control, made mechanical: a claim is acceptable only if it cites at least one
evidence id that actually resolves. Nothing here reads the claim text or
judges its plausibility. The check is symbolic, so it cannot itself
hallucinate.

Deliberately generic, not memo-shaped. STEP-05 will pass memo sentences with
evidence-record ids, and STEP-03 3.5 reuses the same function for triage
rationales with score-component ids ("rationale constrained to cite only the
score components"). Naming this after memos would have forced STEP-03 to
either reimplement it or pretend its score components were memo evidence.

Zero tolerance (STEP-02 3.4): one failing claim fails the whole report.
There is no partial pass, no score, no threshold. A memo with one
unsupported sentence is an unsupported memo.
"""

from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from enum import StrEnum

from ts_sentry.governance.gates import FailureCode, GateFailure

__all__ = [
    "Claim",
    "ClaimResult",
    "ReasonCode",
    "VerificationReport",
    "claim_check",
    "verify_claims",
]


class ReasonCode(StrEnum):
    """Why a claim failed verification."""

    NO_EVIDENCE_CITED = "no_evidence_cited"
    UNRESOLVABLE_EVIDENCE_ID = "unresolvable_evidence_id"


@dataclass(frozen=True, slots=True)
class Claim:
    """One verifiable assertion plus the evidence ids it cites."""

    text: str
    claimed_evidence_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """Per-claim verdict, carrying enough to flag the exact defect.

    ``index`` is the claim's position in the submitted sequence, so a
    draft-revise loop (STEP-05 3.2) can point at the offending sentence
    rather than hand back a whole failed document.
    """

    index: int
    text: str
    passed: bool
    reason: ReasonCode | None
    unresolvable_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.passed is (self.reason is not None):
            raise ValueError(
                "a failing claim carries exactly one reason; a passing one carries none"
            )


@dataclass(frozen=True, slots=True)
class VerificationReport:
    passed: bool
    results: tuple[ClaimResult, ...]

    @property
    def failures(self) -> tuple[ClaimResult, ...]:
        return tuple(result for result in self.results if not result.passed)


def verify_claims(claims: Sequence[Claim], resolvable_ids: AbstractSet[str]) -> VerificationReport:
    """Verify every claim against the set of ids that actually resolve.

    Pure and total: no I/O, no ledger write, no exception on any input. The
    gate ledgers the outcome (D4), which keeps this callable directly from a
    test or a revise loop.

    An empty claim sequence passes vacuously. That is the correct reading of
    zero-tolerance (there is no failing claim) and it is safe here, because
    this function answers "are these claims supported", not "does this
    document say enough". Requiring a document to be non-empty is a memo
    structural rule and belongs to STEP-05 3.1, which mandates FACT sentences
    carry evidence ids.
    """
    results: list[ClaimResult] = []

    for index, claim in enumerate(claims):
        if not claim.claimed_evidence_ids:
            results.append(
                ClaimResult(
                    index=index,
                    text=claim.text,
                    passed=False,
                    reason=ReasonCode.NO_EVIDENCE_CITED,
                    unresolvable_ids=(),
                )
            )
            continue

        unresolvable = tuple(sorted(claim.claimed_evidence_ids - resolvable_ids))
        if unresolvable:
            results.append(
                ClaimResult(
                    index=index,
                    text=claim.text,
                    passed=False,
                    reason=ReasonCode.UNRESOLVABLE_EVIDENCE_ID,
                    unresolvable_ids=unresolvable,
                )
            )
            continue

        results.append(
            ClaimResult(
                index=index,
                text=claim.text,
                passed=True,
                reason=None,
                unresolvable_ids=(),
            )
        )

    return VerificationReport(
        passed=all(result.passed for result in results),
        results=tuple(results),
    )


def claim_check(resolvable_ids: AbstractSet[str]) -> "ClaimCheck":
    """Adapt the verifier to the D4 ``ArtifactCheck`` protocol.

    Built as a closure over the resolvable id set because the gate's contract
    passes only the artifact: what counts as resolvable is a property of the
    evidence pack in scope, established before the gate runs, not something
    the claims themselves get to assert.
    """
    return ClaimCheck(resolvable_ids)


@dataclass(frozen=True, slots=True)
class ClaimCheck:
    """Callable adapter satisfying ``gates.ArtifactCheck``."""

    resolvable_ids: AbstractSet[str]

    def __call__(self, artifact: object, /) -> tuple[GateFailure, ...]:
        if not isinstance(artifact, Sequence):
            return (
                GateFailure(
                    code=FailureCode.SCHEMA_INVALID,
                    detail=f"expected a sequence of Claim; got {type(artifact).__name__}",
                ),
            )

        claims: list[Claim] = []
        for item in artifact:
            if not isinstance(item, Claim):
                return (
                    GateFailure(
                        code=FailureCode.SCHEMA_INVALID,
                        detail=f"expected Claim entries; got {type(item).__name__}",
                    ),
                )
            claims.append(item)

        report = verify_claims(claims, self.resolvable_ids)
        return tuple(
            GateFailure(
                code=FailureCode.UNVERIFIED_CLAIM,
                detail=(
                    f"claim {failure.index} ({failure.reason.value if failure.reason else '?'}): "
                    f"{failure.text!r}"
                    + (
                        f"; unresolvable ids: {', '.join(failure.unresolvable_ids)}"
                        if failure.unresolvable_ids
                        else ""
                    )
                ),
            )
            for failure in report.failures
        )
