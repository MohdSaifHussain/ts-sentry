# SPDX-License-Identifier: MIT
"""D2: the human-only ENFORCE construction path (ARCHITECTURE 3.3, STEP-02 D2).

ENFORCE is the one consequence level no agent may reach under any mandate.
This module is the only place in the codebase that produces it for use, and
it will not do so without an analyst identity, an explicit decision, and a
signature hash that recomputes.

What is actually guaranteed
---------------------------
Stated narrowly on purpose, because the honest claim is narrower than the
convenient one and the tests only support the narrow version:

1. No ``Mandate`` can carry ENFORCE as its ceiling. Type-level via
   ``AgentConsequence`` (``mypy --strict`` rejects it, pinned by
   ``tests/typing/enforce_negative.py``), and again at runtime in
   ``Mandate.__post_init__``.
2. ``mandate.validate`` refuses every ENFORCE-consequence action under every
   mandate, unconditionally and before any other refusal check.
3. The gate pipeline (D4) refuses ENFORCE that does not carry a valid
   ``HumanSignature``.
4. A valid ``HumanSignature`` is unconstructible without an analyst identity
   and an explicit decision, both supplied at the human CLI boundary.

What is *not* claimed: that ``Consequence.ENFORCE`` is unmentionable. Any
module importing the enum can name the member, and Python offers no mechanism
to prevent that. The invariant is "no agent action can reach the ENFORCE
gate", which is provable and proven, not "ENFORCE cannot be typed", which
would be false. An import-graph test (``ts_sentry.agents.*`` must not import
this module) is the natural companion guard and is a recorded STEP-03
obligation; it is not shipped now because ``agents/`` does not exist yet and a
vacuously green test is worse than an absent one.

Forgery resistance
------------------
``HumanSignature.__post_init__`` recomputes ``signature_hash`` from the other
fields and refuses to construct on mismatch. So the object cannot be
hand-assembled with a plausible-looking digest, and cannot be mutated after
the fact (frozen). This is integrity binding, not authentication: it proves
the five fields belong together and have not drifted apart, and it does not
prove the analyst is who they say they are. Real analyst authentication is
out of scope for a synthetic-data portfolio system and is named in Honest
Limits rather than implied by the word "signature".
"""

import hmac
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

from ts_sentry.data.tz import require_ist
from ts_sentry.governance.canonical import FIELD_SEPARATOR, digest_fields, require_sha256_hex
from ts_sentry.governance.mandate import Consequence, EnforceUnreachable

__all__ = [
    "Decision",
    "HumanSignature",
    "InvalidSignature",
    "enforce_consequence",
    "sign",
]

_SIGNATURE_DOMAIN = "ts-sentry/human-signature/v1"
"""Domain-separation tag. Keeps a signature digest from ever colliding with a
ledger entry digest (D3), which is computed with the same primitive over
similarly shaped fields."""


class Decision(StrEnum):
    """The analyst's decision on a proposed enforcement.

    ``REJECT`` and ``DEFER`` are first-class members, not absences: an
    analyst declining to enforce is a governance event worth signing and
    ledgering, exactly like approving one.
    """

    APPROVE_ENFORCEMENT = "approve_enforcement"
    REJECT = "reject"
    DEFER = "defer"


class InvalidSignature(Exception):
    """Raised when a ``HumanSignature``'s fields and digest disagree."""


def _signature_digest(
    analyst_id: str, decision: Decision, subject_hash: str, signed_ts: datetime
) -> str:
    return digest_fields(
        _SIGNATURE_DOMAIN,
        analyst_id,
        decision.value,
        subject_hash,
        signed_ts.isoformat(),
    )


@dataclass(frozen=True, slots=True)
class HumanSignature:
    """An analyst's signed decision on a specific artifact.

    ``subject_hash`` is the SHA-256 of whatever is being signed (from STEP-05,
    a memo). Binding to the artifact's hash rather than to the artifact means
    a signature cannot silently carry over to an edited memo.

    Prefer the ``sign`` factory. Direct construction works only if you already
    hold a digest that recomputes, which in practice means you got it from
    ``sign``; that is the point.
    """

    analyst_id: str
    decision: Decision
    subject_hash: str
    signed_ts: datetime
    signature_hash: str

    def __post_init__(self) -> None:
        if not self.analyst_id.strip():
            raise ValueError("analyst_id must be a non-empty analyst identity")
        if FIELD_SEPARATOR in self.analyst_id:
            raise ValueError("analyst_id must not contain the reserved field separator (U+001F)")
        require_sha256_hex(self.subject_hash, "subject_hash")
        require_sha256_hex(self.signature_hash, "signature_hash")
        require_ist(self.signed_ts, "signed_ts")

        expected = _signature_digest(
            self.analyst_id, self.decision, self.subject_hash, self.signed_ts
        )
        # Constant-time comparison: cheap here, and the right habit at any
        # boundary where a caller controls one side of a digest check.
        if not hmac.compare_digest(self.signature_hash, expected):
            raise InvalidSignature(
                "signature_hash does not recompute from (analyst_id, decision, "
                "subject_hash, signed_ts); this signature is forged or its fields drifted"
            )


def sign(
    analyst_id: str, decision: Decision, subject_hash: str, signed_ts: datetime
) -> HumanSignature:
    """The only factory that computes a valid ``signature_hash``.

    Takes ``signed_ts`` explicitly rather than reading the clock: every
    timestamp in this system is supplied by its caller so that sessions stay
    reproducible and testable, matching the STEP-01 rule against time-based
    entropy.
    """
    require_sha256_hex(subject_hash, "subject_hash")
    require_ist(signed_ts, "signed_ts")
    return HumanSignature(
        analyst_id=analyst_id,
        decision=decision,
        subject_hash=subject_hash,
        signed_ts=signed_ts,
        signature_hash=_signature_digest(analyst_id, decision, subject_hash, signed_ts),
    )


def enforce_consequence(signature: HumanSignature) -> Literal[Consequence.ENFORCE]:
    """Yield ``Consequence.ENFORCE`` for an approved, signed human decision.

    The single function in this codebase that produces ENFORCE for use. It is
    unreachable without a ``HumanSignature`` that already survived
    construction, which means its digest recomputed.

    Raises ``EnforceUnreachable`` on a ``REJECT`` or ``DEFER`` signature.
    That is a caller bug rather than a routine outcome: an analyst who
    declined has no enforcement to carry out, so asking this function for one
    means the calling code lost track of the decision it was handling.
    """
    if signature.decision is not Decision.APPROVE_ENFORCEMENT:
        raise EnforceUnreachable(
            f"signature carries decision {signature.decision.value}; ENFORCE requires "
            f"{Decision.APPROVE_ENFORCEMENT.value}"
        )
    return Consequence.ENFORCE
