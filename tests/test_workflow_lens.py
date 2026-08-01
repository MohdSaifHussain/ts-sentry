# SPDX-License-Identifier: MIT
"""STEP-07 D3: the workflow lens.

Most of this file is about what the lens is not allowed to say. The arithmetic
of a model whose every input is assumed is not the interesting part; the
interesting part is that it cannot be rendered as a measurement, cannot use
causal language about itself, and cannot report a governance table of zeros
without saying what zero means.
"""

import json
from pathlib import Path

import pytest

from ts_sentry.measurement.workflow import (
    BANNED_CAUSAL_PHRASES,
    DEFAULT_ASSUMPTIONS,
    NO_BENCHMARK_NOTE,
    AnalystMinutesModel,
    GovernanceActivity,
    TimeAssumption,
    read_session_counts,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _quiet() -> GovernanceActivity:
    return GovernanceActivity(
        gate_rejections=0,
        mandate_violation_attempts=0,
        verification_passes=12,
        verification_failures=0,
        human_decisions=6,
        injection_signals=0,
        rejected_hops=0,
    )


def _busy() -> GovernanceActivity:
    return GovernanceActivity(
        gate_rejections=2,
        mandate_violation_attempts=1,
        verification_passes=9,
        verification_failures=3,
        human_decisions=4,
        injection_signals=5,
        rejected_hops=2,
    )


def test_the_minutes_model_never_uses_causal_language() -> None:
    """3.5, enforced against the rendered text rather than intended in prose.

    A model over assumed inputs cannot establish that anything caused anything,
    so the words that would imply it are banned outright. Prose drifts, and it
    drifts in the flattering direction.
    """
    rendered = AnalystMinutesModel().evaluate(cases=25).render().lower()

    for phrase in BANNED_CAUSAL_PHRASES:
        assert phrase not in rendered, f"the minutes model claims {phrase!r}"


def test_the_governance_rendering_never_uses_causal_language() -> None:
    for activity in (_quiet(), _busy()):
        rendered = activity.render().lower()
        for phrase in BANNED_CAUSAL_PHRASES:
            assert phrase not in rendered, f"the governance section claims {phrase!r}"


def test_every_rendering_carries_the_no_benchmark_note() -> None:
    """The note is structural, not editorial.

    "There is no benchmark" is itself a claim, so it is sourced to TSPA and
    travels with every rendering rather than appearing once in a document
    somebody might not read.
    """
    result = AnalystMinutesModel().evaluate(cases=3)

    assert NO_BENCHMARK_NOTE in result.render()
    assert result.to_json_object()["no_benchmark_note"] == NO_BENCHMARK_NOTE
    assert "TSPA" in NO_BENCHMARK_NOTE


def test_the_model_labels_itself_modelled_and_the_counts_measured() -> None:
    """The two kinds of number must be distinguishable from the data alone.

    A downstream renderer that had to infer which was which would eventually
    get it wrong, and the direction it would get it wrong in is obvious.
    """
    assert AnalystMinutesModel().evaluate(cases=1).to_json_object()["kind"] == "modelled"
    assert _busy().to_json_object()["kind"] == "measured"


def test_every_assumption_is_labelled_an_assumption_in_the_output() -> None:
    payload = AnalystMinutesModel().evaluate(cases=1).to_json_object()
    assumptions = payload["assumptions"]

    assert isinstance(assumptions, list)
    for entry in assumptions:
        assert entry["source"] == "assumption"
        assert entry["rationale"].strip()


def test_the_assumption_table_is_rendered_before_the_number() -> None:
    """A delta without its table is a headline figure, which is the thing D3
    must not produce."""
    rendered = AnalystMinutesModel().evaluate(cases=10).render()

    table_at = rendered.index("Assumptions (every figure below is assumed")
    delta_at = rendered.index("the assumption table implies a difference")

    assert table_at < delta_at
    for assumption in DEFAULT_ASSUMPTIONS:
        assert assumption.step in rendered


def test_the_result_has_no_minutes_saved_attribute() -> None:
    """Guards the naming, because the name is what gets quoted.

    ``delta_minutes`` invites the reader to ask "delta between what"; a
    ``minutes_saved`` would answer a question nobody is entitled to answer here.
    """
    result = AnalystMinutesModel().evaluate(cases=1)

    for forbidden in ("minutes_saved", "savings", "time_saved", "speedup"):
        assert not hasattr(result, forbidden)
    assert not any(forbidden in result.to_json_object() for forbidden in ("minutes_saved",))


def test_the_delta_is_the_difference_of_the_two_assumed_totals() -> None:
    result = AnalystMinutesModel().evaluate(cases=4)

    expected_baseline = sum(a.baseline_minutes for a in DEFAULT_ASSUMPTIONS) * 4
    expected_assisted = sum(a.assisted_minutes for a in DEFAULT_ASSUMPTIONS) * 4

    assert result.baseline_total == pytest.approx(expected_baseline)
    assert result.assisted_total == pytest.approx(expected_assisted)
    assert result.delta_minutes == pytest.approx(expected_baseline - expected_assisted)


def test_sensitivity_brackets_the_delta_in_the_right_direction() -> None:
    """A larger assumed assisted time must give a smaller delta.

    The sign is easy to invert and the inversion would be invisible: the table
    would still look plausible, and every span would point the wrong way.
    """
    result = AnalystMinutesModel(variation=0.5).evaluate(cases=1)

    for span in result.spans:
        assert span.low_delta <= result.delta_minutes <= span.high_delta
        assert span.swing >= 0.0


def test_a_step_assumed_identical_in_both_arms_has_no_swing() -> None:
    """Human sign-off is assumed identical because ENFORCE is human-only.

    It must therefore contribute nothing to the comparison, and a non-zero
    swing there would mean the model was crediting the workbench for a step it
    cannot touch.
    """
    result = AnalystMinutesModel(variation=0.5).evaluate(cases=1)
    sign_off = next(span for span in result.spans if "sign-off" in span.step)
    assumption = next(a for a in DEFAULT_ASSUMPTIONS if "sign-off" in a.step)

    assert assumption.baseline_minutes == assumption.assisted_minutes
    assert assumption.delta_minutes == 0.0
    assert sign_off.break_even_assisted == assumption.baseline_minutes


def test_the_break_even_is_the_baseline_time_for_that_step() -> None:
    """The honest summary statistic.

    A step contributes nothing to the comparison when its assisted time equals
    its baseline time, so that is the threshold an assumption would have to
    cross before the sign of the comparison changed.
    """
    result = AnalystMinutesModel().evaluate(cases=1)

    for span, assumption in zip(result.spans, DEFAULT_ASSUMPTIONS, strict=True):
        assert span.break_even_assisted == assumption.baseline_minutes


def test_the_widest_span_names_the_assumption_the_answer_rests_on() -> None:
    """A delta driven by one assumed number is a delta about that assumption.

    On the default table that is evidence gathering, which is also the line the
    table itself flags as least defensible given the Phase 4 traversal finding.
    """
    result = AnalystMinutesModel().evaluate(cases=1)
    widest = result.widest_span

    assert widest is not None
    assert widest.step == "evidence gathering"
    assert "least defensible" in next(
        a.rationale for a in DEFAULT_ASSUMPTIONS if a.step == "evidence gathering"
    )


def test_the_model_refuses_unusable_configurations() -> None:
    with pytest.raises(ValueError, match="at least one assumption"):
        AnalystMinutesModel(assumptions=())
    with pytest.raises(ValueError, match="distinct"):
        AnalystMinutesModel(
            assumptions=(DEFAULT_ASSUMPTIONS[0], DEFAULT_ASSUMPTIONS[0]),
        )
    with pytest.raises(ValueError, match="variation"):
        AnalystMinutesModel(variation=0.0)
    with pytest.raises(ValueError, match="cases must be positive"):
        AnalystMinutesModel().evaluate(cases=0)


def test_an_assumption_without_a_rationale_is_refused() -> None:
    """An unexplained number in an assumptions table is a number a reader
    cannot argue with, which defeats the point of showing the table."""
    with pytest.raises(ValueError, match="rationale"):
        TimeAssumption(step="x", baseline_minutes=1.0, assisted_minutes=1.0, rationale="  ")
    with pytest.raises(ValueError, match="step name"):
        TimeAssumption(step=" ", baseline_minutes=1.0, assisted_minutes=1.0, rationale="r")
    with pytest.raises(ValueError, match="cannot be negative"):
        TimeAssumption(step="x", baseline_minutes=-1.0, assisted_minutes=1.0, rationale="r")


def test_a_governance_table_of_zeros_says_what_zero_means() -> None:
    """3.3, which is the requirement most likely to be quietly dropped.

    An empty table reads as competence unless it says otherwise.
    """
    rendered = _quiet().render()

    assert _quiet().untested
    assert "NOTE" in rendered
    assert "not evidence the governance layer works" in rendered
    assert "supports no claim" in rendered


def test_the_zero_note_does_not_misdescribe_the_non_zero_counts() -> None:
    """The note fires on control activity, and passing verifications are not
    control activity.

    An earlier wording said "every control count above is zero" while twelve
    passing verifications were printed directly above it. A governance note
    that contradicts the table it sits under is worse than no note.
    """
    rendered = _quiet().render()

    assert "every control count above is zero" not in rendered
    assert "no control above fired" in rendered
    assert "are not controls firing" in rendered


def test_an_exercised_governance_layer_drops_the_note() -> None:
    activity = _busy()

    assert not activity.untested
    assert activity.total_exercised == 13
    assert "NOTE" not in activity.render()


def test_a_session_that_verified_nothing_has_no_pass_rate() -> None:
    """``None``, not 1.0.

    Reporting a perfect pass rate for having verified nothing is the most
    flattering possible reading of having done no work.
    """
    empty = GovernanceActivity(
        gate_rejections=0,
        mandate_violation_attempts=0,
        verification_passes=0,
        verification_failures=0,
        human_decisions=0,
        injection_signals=0,
        rejected_hops=0,
    )

    assert empty.verification_pass_rate is None
    assert "n/a (nothing verified)" in empty.render()
    assert _busy().verification_pass_rate == pytest.approx(9 / 12)


def test_counts_are_read_from_a_real_session_directory(tmp_path: Path) -> None:
    """Counted from the events, not from the manifest's own summary.

    Two independent readings of one artifact can disagree, which is the point
    of not reusing the summary the same run wrote.
    """
    events = {
        "session_id": "session-test",
        "case_id": "case-0000",
        "turn": {
            "attempted_hops": 5,
            "executed_hops": 3,
            "rejected_hops": 2,
            "injection_signals": 1,
        },
        "events": [
            {"seq": 0, "event_type": "session_open", "payload": {}},
            {"seq": 1, "event_type": "gate_rejection", "payload": {}},
            {"seq": 2, "event_type": "gate_rejection", "payload": {}},
            {"seq": 3, "event_type": "verification_pass", "payload": {}},
            {"seq": 4, "event_type": "verification_fail", "payload": {}},
            {"seq": 5, "event_type": "mandate_violation_attempt", "payload": {}},
            {"seq": 6, "event_type": "human_decision", "payload": {}},
        ],
    }
    (tmp_path / "session_events.json").write_text(json.dumps(events), encoding="utf-8")

    counts = read_session_counts(tmp_path)

    assert counts.session_id == "session-test"
    assert counts.case_id == "case-0000"
    assert counts.attempted_hops == 5
    assert counts.executed_hops == 3
    assert counts.governance.gate_rejections == 2
    assert counts.governance.mandate_violation_attempts == 1
    assert counts.governance.verification_passes == 1
    assert counts.governance.verification_failures == 1
    assert counts.governance.injection_signals == 1
    assert counts.governance.rejected_hops == 2
    assert not counts.governance.untested


def test_a_malformed_turn_count_is_refused_rather_than_coerced(tmp_path: Path) -> None:
    """Session artifacts are JSON, so every value arrives untyped.

    A string that happens to parse, or a negative count, is a malformed
    artifact. Coercing it quietly would put a fabricated number into a section
    labelled MEASURED.
    """
    for bad in ("3", -1, 2.5, True):
        (tmp_path / "session_events.json").write_text(
            json.dumps({"session_id": "s", "turn": {"rejected_hops": bad}, "events": []}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="rejected_hops"):
            read_session_counts(tmp_path)


def test_a_directory_without_session_events_is_refused(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="not a session directory"):
        read_session_counts(tmp_path)


@pytest.mark.skipif(
    not (REPO_ROOT / "session_p4").is_dir(),
    reason="Saif's verification artifacts are not present in every checkout",
)
def test_the_lens_reads_a_real_recorded_session() -> None:
    """Run against a session Saif actually produced, when one is present.

    Read-only, and skipped rather than fabricated when the directory is absent:
    these are his artifacts and the suite must not depend on them existing.
    """
    counts = read_session_counts(REPO_ROOT / "session_p4")

    assert counts.session_id
    assert counts.attempted_hops == counts.executed_hops == 20
    assert counts.governance.untested, (
        "this recorded session exercised no control, which is the finding the "
        "zero-note exists to state"
    )
