# SPDX-License-Identifier: MIT
"""D6: the human signature path for a memo (STEP-05 D6).

The only route by which a memo stops being a draft, and the one path in this
system no agent can reach. ``tests/test_import_graph.py`` enforces that:
``governance.signature`` has a named allowlist of consumers, and
``ts_sentry.agents.*`` is not on it and cannot reach it transitively.

This module is a **deliberate addition to that allowlist**, which is what the
allowlist is for. It is orchestrator-side rather than in ``cli.main`` because
signing a memo is a governed operation with an order that has to be right, and
burying it in argument parsing would make it untestable except through the CLI.

What signing actually guarantees, stated narrowly
-------------------------------------------------
Exactly what ``governance.signature`` already documents, and no more. The
signature binds five fields together and proves they have not drifted apart. It
does **not** prove the analyst is who they say they are: there is no
authentication in this system, and STEP-02 recorded that as an honest limit
rather than implying otherwise with the word "signature". What is new here is
only that the subject being bound is a memo.

Order, and why it is the deliverable
------------------------------------
Verify, then sign, then finalize. A memo that has not passed the RECOMMEND gate
is not signable through this module: ``sign_memo`` takes the gate's verdict as
an argument it cannot fabricate, so "the analyst signed an unverified memo"
requires a caller to have run the gate and thrown the answer away rather than
merely to have forgotten a step.

``Memo.content_digest`` excludes ``status``, so the digest signed and the digest
carried by the finalized memo are the same value. Without that the artifact
would fail its own verification the instant it was produced.
"""

from dataclasses import dataclass
from datetime import datetime

from ts_sentry.agents.memo.memo import Memo, MemoStatus
from ts_sentry.governance.gates import GateFailure
from ts_sentry.governance.signature import Decision, HumanSignature, sign

__all__ = ["SignedMemo", "SigningRefused", "sign_memo", "verify_signed_memo"]


class SigningRefused(Exception):
    """Raised when a memo may not be signed.

    Raised rather than returned, unlike gate failures. A gate failure is a
    governed finding about an agent's output; this is a caller trying to sign
    something that is not eligible, which is a bug in the calling code rather
    than an outcome of the system working.
    """


@dataclass(frozen=True, slots=True)
class SignedMemo:
    """A finalized memo and the signature that finalized it.

    The two travel together because neither is meaningful alone: a signature
    without its memo names a digest nobody can check, and a memo whose status
    says SIGNED without a signature beside it is a claim with nothing behind it.
    """

    memo: Memo
    signature: HumanSignature

    def __post_init__(self) -> None:
        if self.memo.status is not MemoStatus.SIGNED:
            raise SigningRefused(
                f"memo {self.memo.memo_id} is {self.memo.status.value}; a SignedMemo carries "
                "a finalized memo"
            )
        if self.signature.subject_hash != self.memo.content_digest:
            raise SigningRefused(
                f"the signature is over {self.signature.subject_hash[:16]}... and this memo "
                f"digests to {self.memo.content_digest[:16]}...; the memo was edited after it "
                "was signed"
            )

    @property
    def analyst_id(self) -> str:
        return self.signature.analyst_id

    def to_json_object(self) -> dict[str, object]:
        return {
            "memo": self.memo.to_json_object(),
            "signature": {
                "analyst_id": self.signature.analyst_id,
                "decision": self.signature.decision.value,
                "subject_hash": self.signature.subject_hash,
                "signed_ts_ist": self.signature.signed_ts.isoformat(),
                "signature_hash": self.signature.signature_hash,
            },
        }


def sign_memo(
    memo: Memo,
    *,
    analyst_id: str,
    decision: Decision,
    signed_ts: datetime,
    gate_failures: tuple[GateFailure, ...],
) -> SignedMemo:
    """Finalize a verified memo under an analyst's signature.

    ``gate_failures`` is the RECOMMEND gate's verdict, passed in rather than
    recomputed. That is the control: this function will not sign a memo the
    gate rejected, and it cannot check for itself without holding a pack and a
    corpus, so the caller has to have run the gate and hand over what it said.
    Passing an empty tuple without running the gate is possible and is a lie the
    caller told, not a hole this function left open.

    ``signed_ts`` is supplied rather than read from the clock, as
    ``governance.signature.sign`` requires and as every timestamp in this system
    is, so a signing is reproducible and testable.
    """
    if memo.status is MemoStatus.SIGNED:
        raise SigningRefused(f"memo {memo.memo_id} is already signed")
    if gate_failures:
        raise SigningRefused(
            f"memo {memo.memo_id} did not pass the RECOMMEND gate and cannot be signed; "
            f"{len(gate_failures)} failure(s) outstanding, the first being: "
            f"{gate_failures[0].detail}"
        )

    # The digest is taken before finalizing and is unchanged by it, because
    # content_digest excludes status. Taking it here rather than after makes the
    # dependency visible instead of implicit.
    subject_hash = memo.content_digest
    signature = sign(analyst_id, decision, subject_hash, signed_ts)

    if decision is not Decision.APPROVE_ENFORCEMENT:
        raise SigningRefused(
            f"the analyst decision is {decision.value}; a memo is finalized only by an "
            "approval. A rejection or deferral is a real governance event and is ledgered "
            "as one, but it does not produce a signed memo"
        )

    return SignedMemo(memo=memo.finalized(), signature=signature)


def verify_signed_memo(signed: SignedMemo) -> bool:
    """Recompute the memo's digest and check the signature still covers it.

    ``SignedMemo.__post_init__`` already refuses to construct a mismatched pair,
    and ``HumanSignature.__post_init__`` already refuses a forged digest, so
    this is true by construction for any object that exists. It is here because
    a reader of an exported memo wants to *perform* the check rather than be
    told it was performed at construction time in another process.
    """
    return signed.signature.subject_hash == signed.memo.content_digest
