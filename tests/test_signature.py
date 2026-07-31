# SPDX-License-Identifier: MIT
"""STEP-02 D2: the human-only ENFORCE construction path.

Proves the factory is the only route to a valid signature, that a forged or
drifted signature is unconstructible, and that ENFORCE is unreachable without
an explicit APPROVE_ENFORCEMENT decision.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from ts_sentry.data.tz import IST
from ts_sentry.governance.canonical import FIELD_SEPARATOR
from ts_sentry.governance.mandate import Consequence, EnforceUnreachable
from ts_sentry.governance.signature import (
    Decision,
    HumanSignature,
    InvalidSignature,
    enforce_consequence,
    sign,
)

_ANALYST = "saif"
_SUBJECT = "b" * 64
_SIGNED_TS = datetime(2026, 7, 31, 14, 30, tzinfo=IST)


def _signature(decision: Decision = Decision.APPROVE_ENFORCEMENT) -> HumanSignature:
    return sign(_ANALYST, decision, _SUBJECT, _SIGNED_TS)


# --------------------------------------------------------------------------
# The factory
# --------------------------------------------------------------------------


def test_sign_produces_a_constructible_signature() -> None:
    signature = _signature()
    assert signature.analyst_id == _ANALYST
    assert signature.decision is Decision.APPROVE_ENFORCEMENT
    assert signature.subject_hash == _SUBJECT
    assert signature.signed_ts == _SIGNED_TS
    assert len(signature.signature_hash) == 64


def test_sign_is_deterministic() -> None:
    assert _signature().signature_hash == _signature().signature_hash


def test_signature_round_trips_through_direct_construction() -> None:
    """The five fields are sufficient to rebuild the object.

    Matters for D3: a signature read back out of the ledger must reconstruct
    and re-verify, not merely deserialize.
    """
    original = _signature()
    rebuilt = HumanSignature(
        analyst_id=original.analyst_id,
        decision=original.decision,
        subject_hash=original.subject_hash,
        signed_ts=original.signed_ts,
        signature_hash=original.signature_hash,
    )
    assert rebuilt == original


@pytest.mark.parametrize("decision", list(Decision))
def test_every_decision_is_signable(decision: Decision) -> None:
    """Declining is a signed, ledgerable governance event too, not an absence."""
    assert _signature(decision).decision is decision


def test_different_decisions_produce_different_digests() -> None:
    approve = _signature(Decision.APPROVE_ENFORCEMENT)
    reject = _signature(Decision.REJECT)
    assert approve.signature_hash != reject.signature_hash


def test_different_subjects_produce_different_digests() -> None:
    other = sign(_ANALYST, Decision.APPROVE_ENFORCEMENT, "c" * 64, _SIGNED_TS)
    assert other.signature_hash != _signature().signature_hash


# --------------------------------------------------------------------------
# Forgery and drift
# --------------------------------------------------------------------------


def test_a_forged_signature_hash_is_rejected() -> None:
    with pytest.raises(InvalidSignature, match="does not recompute"):
        HumanSignature(
            analyst_id=_ANALYST,
            decision=Decision.APPROVE_ENFORCEMENT,
            subject_hash=_SUBJECT,
            signed_ts=_SIGNED_TS,
            signature_hash="f" * 64,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("analyst_id", "someone-else"),
        ("decision", Decision.REJECT),
        ("subject_hash", "c" * 64),
        ("signed_ts", datetime(2026, 7, 31, 15, 0, tzinfo=IST)),
    ],
)
def test_any_field_drifting_from_the_digest_is_rejected(field: str, value: object) -> None:
    """Changing a field while keeping the old digest must not reconstruct.

    This is what binds the five fields together: a signature cannot be
    retargeted at a different analyst, decision, artifact, or moment.
    """
    original = _signature()
    with pytest.raises(InvalidSignature):
        replace(original, **{field: value})  # type: ignore[arg-type]


def test_a_signature_cannot_be_retargeted_at_an_edited_artifact() -> None:
    """The practical form of the above, for STEP-05: an approved memo that is
    then edited has a new hash, so the old signature no longer applies to it.
    """
    signature = _signature()
    edited_memo_hash = "d" * 64
    assert signature.subject_hash != edited_memo_hash
    with pytest.raises(InvalidSignature):
        replace(signature, subject_hash=edited_memo_hash)


def test_signature_is_frozen() -> None:
    with pytest.raises(AttributeError):
        _signature().analyst_id = "someone-else"  # type: ignore[misc]


# --------------------------------------------------------------------------
# Field validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("analyst_id", ["", "   ", "\t"])
def test_empty_analyst_identity_is_rejected(analyst_id: str) -> None:
    with pytest.raises(ValueError, match="analyst_id"):
        sign(analyst_id, Decision.APPROVE_ENFORCEMENT, _SUBJECT, _SIGNED_TS)


def test_separator_in_analyst_id_is_caught_by_the_factory() -> None:
    """Through ``sign``, the canonical encoder refuses first, before a digest
    is computed at all (see test_canonical for why the encoding cares).
    """
    with pytest.raises(ValueError, match="reserved field separator"):
        sign(f"sa{FIELD_SEPARATOR}if", Decision.APPROVE_ENFORCEMENT, _SUBJECT, _SIGNED_TS)


def test_separator_in_analyst_id_is_caught_by_direct_construction() -> None:
    """The guard in ``HumanSignature.__post_init__`` itself.

    Unreachable via ``sign`` (``join_fields`` refuses one layer earlier), so
    it is exercised directly rather than shipped as a branch nothing tests.
    Direct construction is exactly the path the guard exists for.
    """
    with pytest.raises(ValueError, match="analyst_id must not contain"):
        HumanSignature(
            analyst_id=f"sa{FIELD_SEPARATOR}if",
            decision=Decision.APPROVE_ENFORCEMENT,
            subject_hash=_SUBJECT,
            signed_ts=_SIGNED_TS,
            signature_hash="a" * 64,
        )


@pytest.mark.parametrize("subject", ["", "b" * 63, "B" * 64, "not-a-digest"])
def test_malformed_subject_hash_is_rejected(subject: str) -> None:
    with pytest.raises(ValueError, match="subject_hash"):
        sign(_ANALYST, Decision.APPROVE_ENFORCEMENT, subject, _SIGNED_TS)


def test_naive_signed_ts_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        sign(_ANALYST, Decision.APPROVE_ENFORCEMENT, _SUBJECT, datetime(2026, 7, 31, 14, 30))


def test_non_ist_signed_ts_is_rejected() -> None:
    with pytest.raises(ValueError, match="Asia/Kolkata"):
        sign(
            _ANALYST,
            Decision.APPROVE_ENFORCEMENT,
            _SUBJECT,
            datetime(2026, 7, 31, 9, 0, tzinfo=UTC),
        )


def test_an_equivalent_ist_offset_is_accepted() -> None:
    """IST has no DST, so any tzinfo at +05:30 is an equivalent spelling.

    Reuses ``ts_sentry.data.tz.require_ist`` rather than reimplementing the
    check, keeping one IST enforcement point across the repo.
    """
    fixed_offset = timezone(timedelta(hours=5, minutes=30))
    signature = sign(
        _ANALYST,
        Decision.APPROVE_ENFORCEMENT,
        _SUBJECT,
        datetime(2026, 7, 31, 14, 30, tzinfo=fixed_offset),
    )
    assert signature.signed_ts == _SIGNED_TS


# --------------------------------------------------------------------------
# The ENFORCE path itself
# --------------------------------------------------------------------------


def test_approved_signature_yields_enforce() -> None:
    assert enforce_consequence(_signature()) is Consequence.ENFORCE


@pytest.mark.parametrize("decision", [Decision.REJECT, Decision.DEFER])
def test_unapproved_decisions_cannot_yield_enforce(decision: Decision) -> None:
    with pytest.raises(EnforceUnreachable, match="requires approve_enforcement"):
        enforce_consequence(_signature(decision))


def test_enforce_requires_a_signature_object_at_all() -> None:
    """There is no zero-argument, no-identity route to ENFORCE.

    Stated as a test rather than a docstring claim: the only callable that
    produces ENFORCE for use takes a ``HumanSignature``, and a
    ``HumanSignature`` cannot exist without an analyst identity and an
    explicit decision.
    """
    with pytest.raises(TypeError):
        enforce_consequence()  # type: ignore[call-arg]
