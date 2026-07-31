# SPDX-License-Identifier: MIT
"""STEP-02 D5: the claim-to-evidence symbolic verifier.

STEP-02 3.4: per-sentence pass/fail with reason codes, zero tolerance (one
failing sentence fails the memo). Also covers the adapter that plugs the
verifier into the D4 RECOMMEND gate.
"""

from datetime import datetime

import duckdb
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ts_sentry.data.tz import IST
from ts_sentry.governance.gates import (
    FailureCode,
    GateChecks,
    GateFailure,
    GateOutcome,
    run_gate,
)
from ts_sentry.governance.ledger import EventType, Ledger, OrchestratorToken
from ts_sentry.governance.mandate import AgentId, Consequence
from ts_sentry.governance.verifier import (
    Claim,
    ClaimResult,
    ReasonCode,
    claim_check,
    verify_claims,
)

_RESOLVABLE = frozenset({"ev-1", "ev-2", "ev-3"})


def _claim(text: str, *ids: str) -> Claim:
    return Claim(text=text, claimed_evidence_ids=frozenset(ids))


# --------------------------------------------------------------------------
# Core verification
# --------------------------------------------------------------------------


def test_a_claim_citing_resolvable_evidence_passes() -> None:
    report = verify_claims([_claim("Ring shares an upload template.", "ev-1")], _RESOLVABLE)
    assert report.passed
    assert report.results[0].reason is None


def test_a_claim_citing_nothing_fails() -> None:
    """The A-01 overclaim control: an assertion with no evidence is exactly
    the failure mode this exists to catch."""
    report = verify_claims([_claim("This channel is clearly a bot farm.")], _RESOLVABLE)
    assert not report.passed
    assert report.results[0].reason is ReasonCode.NO_EVIDENCE_CITED


def test_a_claim_citing_a_phantom_id_fails() -> None:
    report = verify_claims([_claim("Cites something invented.", "ev-999")], _RESOLVABLE)
    result = report.results[0]
    assert not report.passed
    assert result.reason is ReasonCode.UNRESOLVABLE_EVIDENCE_ID
    assert result.unresolvable_ids == ("ev-999",)


def test_partially_resolvable_citations_fail() -> None:
    """One good citation does not launder a phantom one alongside it.

    Otherwise an agent could attach a real id to any claim and smuggle
    invented ones through in the same breath.
    """
    report = verify_claims([_claim("Half invented.", "ev-1", "ev-404")], _RESOLVABLE)
    assert not report.passed
    assert report.results[0].unresolvable_ids == ("ev-404",)


def test_unresolvable_ids_are_reported_sorted() -> None:
    """Deterministic output: the same defect must render identically every
    run, since these strings reach the ledger via the gate."""
    report = verify_claims([_claim("Many phantoms.", "ev-z", "ev-a", "ev-m")], _RESOLVABLE)
    assert report.results[0].unresolvable_ids == ("ev-a", "ev-m", "ev-z")


# --------------------------------------------------------------------------
# Zero tolerance (STEP-02 3.4)
# --------------------------------------------------------------------------


def test_one_failing_claim_fails_the_whole_report() -> None:
    report = verify_claims(
        [
            _claim("Supported.", "ev-1"),
            _claim("Supported too.", "ev-2"),
            _claim("Unsupported.", "ev-nope"),
            _claim("Supported again.", "ev-3"),
        ],
        _RESOLVABLE,
    )

    assert not report.passed
    assert len(report.failures) == 1
    assert report.failures[0].index == 2
    assert sum(1 for result in report.results if result.passed) == 3


def test_results_preserve_submission_order_and_index() -> None:
    """A revise loop (STEP-05 3.2) has to point at the offending sentence,
    so index must track position rather than filtered position."""
    report = verify_claims(
        [_claim("a", "ev-nope"), _claim("b", "ev-1"), _claim("c", "ev-nope")], _RESOLVABLE
    )
    assert [result.index for result in report.results] == [0, 1, 2]
    assert [result.index for result in report.failures] == [0, 2]


def test_an_empty_claim_sequence_passes_vacuously() -> None:
    """Correct reading of zero tolerance: there is no failing claim.

    This function answers "are these claims supported", not "does this
    document say enough". The latter is a memo structural rule and belongs to
    STEP-05 3.1.
    """
    report = verify_claims([], _RESOLVABLE)
    assert report.passed
    assert report.results == ()


def test_no_claim_passes_when_nothing_resolves() -> None:
    report = verify_claims([_claim("Cites a real-looking id.", "ev-1")], frozenset())
    assert not report.passed
    assert report.results[0].reason is ReasonCode.UNRESOLVABLE_EVIDENCE_ID


def test_verification_ignores_claim_text_entirely() -> None:
    """The check is symbolic, so it cannot itself hallucinate.

    Identical citations must produce identical verdicts no matter how
    plausible or absurd the prose around them is.
    """
    plausible = verify_claims([_claim("Account created 2024-03-01.", "ev-1")], _RESOLVABLE)
    absurd = verify_claims([_claim("The moon is a spam ring.", "ev-1")], _RESOLVABLE)
    assert plausible.passed == absurd.passed


def test_claim_result_rejects_an_inconsistent_shape() -> None:
    with pytest.raises(ValueError, match="exactly one reason"):
        ClaimResult(
            index=0, text="x", passed=True, reason=ReasonCode.NO_EVIDENCE_CITED, unresolvable_ids=()
        )


# --------------------------------------------------------------------------
# Soundness property
# --------------------------------------------------------------------------

_ID = st.sampled_from(["ev-1", "ev-2", "ev-3", "ev-404", "ev-999", "phantom"])
_CLAIM = st.builds(Claim, text=st.text(max_size=40), claimed_evidence_ids=st.frozensets(_ID))


@settings(max_examples=200, deadline=None)
@given(claims=st.lists(_CLAIM, max_size=10), resolvable=st.frozensets(_ID))
def test_a_passing_report_contains_no_unsupported_claim(
    claims: list[Claim], resolvable: frozenset[str]
) -> None:
    """Verifier soundness, the shape STEP-05 3.5 will reuse: if the report
    passes, then every claim cited at least one id and every id it cited
    resolves. The verifier cannot pass something by falling off the end of
    its checks.
    """
    report = verify_claims(claims, resolvable)

    if report.passed:
        for claim in claims:
            assert claim.claimed_evidence_ids
            assert claim.claimed_evidence_ids <= resolvable
    else:
        assert report.failures


@settings(max_examples=200, deadline=None)
@given(claims=st.lists(_CLAIM, max_size=10), resolvable=st.frozensets(_ID))
def test_verification_is_total(claims: list[Claim], resolvable: frozenset[str]) -> None:
    report = verify_claims(claims, resolvable)
    assert len(report.results) == len(claims)
    assert report.passed == (not report.failures)


# --------------------------------------------------------------------------
# The D4 adapter: RECOMMEND gate integration
# --------------------------------------------------------------------------


def _ledger() -> Ledger:
    return Ledger(duckdb.connect(":memory:"))


def _never_called(artifact: object, /) -> tuple[GateFailure, ...]:
    """The ASSEMBLE slot. GateChecks has no defaults by design, so a
    RECOMMEND-only test still has to say what ASSEMBLE would do."""
    raise AssertionError("the ASSEMBLE checker must not run for a RECOMMEND gate")


def _recommend(ledger: Ledger, artifact: object) -> GateOutcome:
    return run_gate(
        ledger,
        OrchestratorToken(session_id="verifier-session"),
        timestamp_ist=datetime(2026, 7, 31, 14, 30, tzinfo=IST),
        agent_id=AgentId.MEMO,
        mandate_hash="1" * 64,
        consequence=Consequence.RECOMMEND,
        artifact=artifact,
        checks=GateChecks(assemble=_never_called, recommend=claim_check(_RESOLVABLE)),
    )


def test_recommend_gate_accepts_a_fully_supported_memo() -> None:
    ledger = _ledger()
    outcome = _recommend(ledger, [_claim("Supported.", "ev-1")])

    assert outcome.accepted
    assert [entry.event_type for entry in outcome.ledgered] == [EventType.VERIFICATION_PASS]


def test_recommend_gate_rejects_a_memo_with_one_planted_overclaim() -> None:
    """The end-to-end path STEP-05 D7 will exercise with fixtures: the gate
    must be seen failing correctly, not merely capable of failing."""
    ledger = _ledger()
    outcome = _recommend(
        ledger,
        [_claim("Supported.", "ev-1"), _claim("Planted overclaim.", "ev-invented")],
    )

    assert not outcome.accepted
    failures = outcome.failures
    assert failures[0].code is FailureCode.UNVERIFIED_CLAIM
    assert "ev-invented" in failures[0].detail
    assert [entry.event_type for entry in outcome.ledgered] == [
        EventType.VERIFICATION_FAIL,
        EventType.GATE_REJECTION,
    ]
    assert ledger.verify().intact


def test_adapter_rejects_a_non_sequence_artifact() -> None:
    outcome = _recommend(_ledger(), object())
    assert not outcome.accepted
    assert outcome.failures[0].code is FailureCode.SCHEMA_INVALID


def test_adapter_rejects_a_sequence_of_the_wrong_type() -> None:
    outcome = _recommend(_ledger(), ["a bare string is not a Claim"])
    assert not outcome.accepted
    assert outcome.failures[0].code is FailureCode.SCHEMA_INVALID


def test_one_gate_failure_is_emitted_per_failing_claim() -> None:
    outcome = _recommend(
        _ledger(),
        [_claim("a"), _claim("b", "ev-1"), _claim("c", "ev-nope")],
    )
    assert len(outcome.failures) == 2
