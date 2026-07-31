# SPDX-License-Identifier: MIT
"""D4: the consequence-gate pipeline (ARCHITECTURE 3.3).

Every action is classified by consequence, never by content, and each level
gets its own gate:

======== ==================================================================
OBSERVE  Auto-approved, ledgered.
ASSEMBLE Deterministic validation: schema, referential integrity, provenance
         completeness.
RECOMMEND Symbolic verification: every claim must resolve to evidence
         (D5 supplies the checker).
ENFORCE  Human only. Refused for every agent action; reachable solely with a
         valid HumanSignature carrying APPROVE_ENFORCEMENT.
======== ==================================================================

Failures are returned, never raised (STEP-02 3.3). A caller gets a
``GateOutcome`` describing what failed and what was ledgered, because a
governance layer that signals rejection by throwing is a governance layer
whose rejections can be swallowed by an ``except``.

Fail-closed on checker error
----------------------------
If an injected checker raises, that is converted into a gate failure rather
than propagated. A crashing validator must never produce an *accepted*
artifact, and it must not skip the ledger write either. The exception text is
preserved in the failure detail, so this degrades to a loud rejection rather
than a silent one.

What is deliberately not here
-----------------------------
The ASSEMBLE and RECOMMEND checkers are injected, not implemented. The
Evidence Pack they validate is STEP-04 and the memo AST is STEP-05, so this
phase ships the pipeline and the contract. Implementing the checks now would
mean inventing the artifacts they check, which is exactly the
implement-ahead-of-STEP failure the project contract forbids. ``GateChecks``
has no defaults for the same reason: there is no way to run a gate without
naming the checks it runs, so an unconfigured gate cannot silently
auto-approve.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, assert_never

from ts_sentry.governance.ledger import (
    EventType,
    Ledger,
    LedgerEntry,
    OrchestratorToken,
    digest_payload,
)
from ts_sentry.governance.mandate import (
    AgentId,
    Consequence,
    Mandate,
    RefusalCode,
)
from ts_sentry.governance.scopes import DataScope, ScopeViolation, resolve_scope_by_name
from ts_sentry.governance.signature import Decision, HumanSignature

__all__ = [
    "ArtifactCheck",
    "FailureCode",
    "GateChecks",
    "GateDecision",
    "GateFailure",
    "GateOutcome",
    "ScopeGuardResult",
    "guard_scope_request",
    "run_gate",
]


class GateDecision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class FailureCode(StrEnum):
    """Why a gate rejected. Countable by cause, like ``RefusalCode``."""

    SCHEMA_INVALID = "schema_invalid"
    REFERENTIAL_INTEGRITY = "referential_integrity"
    PROVENANCE_INCOMPLETE = "provenance_incomplete"
    UNVERIFIED_CLAIM = "unverified_claim"
    ENFORCE_REQUIRES_HUMAN_SIGNATURE = "enforce_requires_human_signature"
    CHECKER_ERROR = "checker_error"


@dataclass(frozen=True, slots=True)
class GateFailure:
    code: FailureCode
    detail: str


class ArtifactCheck(Protocol):
    """A deterministic validation pass over a gated artifact.

    Returns an empty tuple when the artifact is acceptable. Implementations
    arrive in STEP-04 (Evidence Pack) and STEP-05 (memo AST); D5's verifier
    adapts to this shape.
    """

    def __call__(self, artifact: object, /) -> tuple[GateFailure, ...]: ...


@dataclass(frozen=True, slots=True)
class GateChecks:
    """The checkers a gate run may invoke. No defaults, by design."""

    assemble: ArtifactCheck
    recommend: ArtifactCheck


@dataclass(frozen=True, slots=True)
class GateOutcome:
    """Structured result. Never an exception, never a bare bool."""

    decision: GateDecision
    consequence: Consequence
    failures: tuple[GateFailure, ...]
    ledgered: tuple[LedgerEntry, ...]

    def __post_init__(self) -> None:
        accepted = self.decision is GateDecision.ACCEPTED
        if accepted is bool(self.failures):
            raise ValueError("an ACCEPTED outcome carries no failures; a REJECTED one carries some")

    @property
    def accepted(self) -> bool:
        return self.decision is GateDecision.ACCEPTED


def _run_check(check: ArtifactCheck, artifact: object) -> tuple[GateFailure, ...]:
    """Invoke a checker, converting any exception into a rejection.

    Fail-closed: a validator that crashes must not yield an accepted
    artifact. See the module docstring.
    """
    try:
        return check(artifact)
    except Exception as exc:  # noqa: BLE001 - deliberate fail-closed boundary
        return (
            GateFailure(
                code=FailureCode.CHECKER_ERROR,
                detail=f"{type(exc).__name__} raised during validation: {exc}",
            ),
        )


def _check_signature(signature: HumanSignature | None) -> tuple[GateFailure, ...]:
    """The ENFORCE gate.

    A ``HumanSignature`` that exists has already proven its own integrity in
    ``__post_init__``, so all that remains here is that one was supplied and
    that it approves rather than declines.
    """
    if signature is None:
        return (
            GateFailure(
                code=FailureCode.ENFORCE_REQUIRES_HUMAN_SIGNATURE,
                detail=(
                    "ENFORCE is human-only and carries no signature. No agent action can "
                    "reach this gate; see governance.signature."
                ),
            ),
        )
    if signature.decision is not Decision.APPROVE_ENFORCEMENT:
        return (
            GateFailure(
                code=FailureCode.ENFORCE_REQUIRES_HUMAN_SIGNATURE,
                detail=(
                    f"signature carries decision {signature.decision.value}; ENFORCE requires "
                    f"{Decision.APPROVE_ENFORCEMENT.value}"
                ),
            ),
        )
    return ()


def _evaluate(
    consequence: Consequence,
    artifact: object,
    checks: GateChecks,
    signature: HumanSignature | None,
) -> tuple[GateFailure, ...]:
    """Exhaustive over ``Consequence``, closed by ``assert_never``: a new
    level cannot be added without deciding how it is gated."""
    match consequence:
        case Consequence.OBSERVE:
            return ()
        case Consequence.ASSEMBLE:
            return _run_check(checks.assemble, artifact)
        case Consequence.RECOMMEND:
            return _run_check(checks.recommend, artifact)
        case Consequence.ENFORCE:
            return _check_signature(signature)
        case _:  # pragma: no cover - exhaustiveness guard, unreachable per mypy
            assert_never(consequence)


def _rejection_events(
    consequence: Consequence, signature: HumanSignature | None
) -> tuple[EventType, ...]:
    """Which events a rejection writes.

    STEP-02 3.3 specifies VERIFICATION_FAIL + GATE_REJECTION for gate
    failures. An unsigned ENFORCE is a different animal: nothing was verified
    and failed, something tried to reach a level it may never reach, so it is
    recorded as MANDATE_VIOLATION_ATTEMPT + GATE_REJECTION. Recorded as an
    interpretation of 3.3, which does not enumerate the ENFORCE case.
    """
    if consequence is Consequence.ENFORCE and signature is None:
        return (EventType.MANDATE_VIOLATION_ATTEMPT, EventType.GATE_REJECTION)
    return (EventType.VERIFICATION_FAIL, EventType.GATE_REJECTION)


def run_gate(
    ledger: Ledger,
    token: OrchestratorToken,
    *,
    timestamp_ist: datetime,
    agent_id: AgentId | None,
    mandate_hash: str,
    consequence: Consequence,
    artifact: object,
    checks: GateChecks,
    signature: HumanSignature | None = None,
) -> GateOutcome:
    """Run the consequence gate for one proposed output.

    Ledgers the result either way. An accepted OBSERVE/ASSEMBLE/RECOMMEND
    writes VERIFICATION_PASS; an accepted ENFORCE writes HUMAN_DECISION,
    because what is being recorded is a person's decision rather than a
    machine's check.
    """
    failures = _evaluate(consequence, artifact, checks, signature)

    if failures:
        events = _rejection_events(consequence, signature)
        payload = {
            "consequence": consequence.value,
            "failures": [
                {"code": failure.code.value, "detail": failure.detail} for failure in failures
            ],
        }
        decision = GateDecision.REJECTED
    else:
        events = (
            (EventType.HUMAN_DECISION,)
            if consequence is Consequence.ENFORCE
            else (EventType.VERIFICATION_PASS,)
        )
        payload = {"consequence": consequence.value, "failures": []}
        decision = GateDecision.ACCEPTED

    ledgered = tuple(
        ledger.append(
            token,
            timestamp_ist=timestamp_ist,
            agent_id=agent_id,
            mandate_hash=mandate_hash,
            event_type=event_type,
            payload_digest=digest_payload({**payload, "event": event_type.value}),
        )
        for event_type in events
    )

    return GateOutcome(
        decision=decision,
        consequence=consequence,
        failures=failures,
        ledgered=ledgered,
    )


# --------------------------------------------------------------------------
# STEP-02 3.5: sealed-scope resolution is refused and ledgered
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScopeGuardResult:
    """Outcome of a scope request. ``scope`` is populated only when granted."""

    granted: bool
    scope: DataScope | None
    code: RefusalCode | None
    detail: str
    ledgered: LedgerEntry | None

    def __post_init__(self) -> None:
        if self.granted is (self.scope is None):
            raise ValueError("a granted result carries a scope; a refused one carries none")


def guard_scope_request(
    ledger: Ledger,
    token: OrchestratorToken,
    *,
    timestamp_ist: datetime,
    agent_id: AgentId,
    mandate: Mandate,
    mandate_hash: str,
    requested_name: str,
) -> ScopeGuardResult:
    """Resolve an agent-supplied scope *name* against its mandate.

    Completes the half of STEP-02 3.5 that D1 could not: a sealed-scope
    request is refused and the attempt is ledgered as
    MANDATE_VIOLATION_ATTEMPT.

    Two independent refusals, in order. First the allowlist: ``DataScope``
    has no member resolving to anything under ``sealed``, so
    ``resolve_scope_by_name`` denies every sealed name by construction
    (STEP-01 3.3, absence is denial). Then the mandate: a real table this
    particular agent was not granted is refused too.

    The name is deliberately taken as a string, because that is the actual
    attack surface. An agent does not hand the orchestrator a validated
    ``DataScope``; it hands it something like ``"sealed._labels"``.
    """

    def _refuse(code: RefusalCode, detail: str) -> ScopeGuardResult:
        entry = ledger.append(
            token,
            timestamp_ist=timestamp_ist,
            agent_id=agent_id,
            mandate_hash=mandate_hash,
            event_type=EventType.MANDATE_VIOLATION_ATTEMPT,
            payload_digest=digest_payload(
                {
                    "requested_scope": requested_name,
                    "refusal_code": code.value,
                    "agent_id": agent_id.value,
                }
            ),
        )
        return ScopeGuardResult(granted=False, scope=None, code=code, detail=detail, ledgered=entry)

    try:
        scope = resolve_scope_by_name(requested_name)
    except ScopeViolation:
        return _refuse(
            RefusalCode.SCOPE_NOT_ALLOWED,
            f"no DataScope member resolves {requested_name!r}; the allowlist has no member "
            "for the sealed schema, so absence is denial",
        )

    if scope not in mandate.data_scopes:
        return _refuse(
            RefusalCode.SCOPE_NOT_ALLOWED,
            f"scope {scope.value} is not in the {agent_id.value} mandate allowlist",
        )

    return ScopeGuardResult(
        granted=True,
        scope=scope,
        code=None,
        detail=f"scope {scope.value} granted under the {agent_id.value} mandate",
        ledgered=None,
    )
