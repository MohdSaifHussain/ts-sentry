# SPDX-License-Identifier: MIT
"""STEP-07 D4 and D5: the measurement report and its CLI verb.

The report is the one document anybody will actually read, so most of what is
checked here is what it cannot say and what it cannot omit: no causal language,
no arm B presented as a VVR, no missing Honest Limits, and no silently absent
lens.
"""

import json
from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from ts_sentry.cli.main import EXIT_INPUT_ERROR, EXIT_OK, main
from ts_sentry.data.generator import build_dataset
from ts_sentry.data.population import BuildConfig
from ts_sentry.data.store import persist_dataset
from ts_sentry.data.tz import IST
from ts_sentry.measurement.frame import ARM_B_COMMENT_ATTRIBUTION, build_view_frame
from ts_sentry.measurement.raters import perfect_panel
from ts_sentry.measurement.report import (
    HONEST_LIMITS,
    MeasurementReport,
    MeasurementStamp,
    write_measurement_report,
)
from ts_sentry.measurement.vvr import measure_vvr
from ts_sentry.measurement.workflow import (
    BANNED_CAUSAL_PHRASES,
    AnalystMinutesModel,
    GovernanceActivity,
)

_CLOCK = datetime(2026, 8, 1, 12, 0, tzinfo=IST)


def _governance() -> GovernanceActivity:
    return GovernanceActivity(
        gate_rejections=0,
        mandate_violation_attempts=0,
        verification_passes=40,
        verification_failures=0,
        human_decisions=20,
        injection_signals=0,
        rejected_hops=0,
    )


def _stamp() -> MeasurementStamp:
    return MeasurementStamp.now(
        measurement_seed=42,
        dataset_digest="d" * 64,
        clock=_CLOCK,
        dataset_seed=42,
        dataset_scale=1,
        corpus_version="1.0.0",
        corpus_sha256="c" * 64,
        prompt_versions={"triage.rationale": "a" * 64},
    )


def _workflow_only() -> MeasurementReport:
    return MeasurementReport(
        stamp=_stamp(),
        governance=_governance(),
        minutes=AnalystMinutesModel().evaluate(cases=3),
        session_id="session-test",
    )


@pytest.fixture(scope="module")
def full_report() -> MeasurementReport:
    con = duckdb.connect()
    persist_dataset(con, build_dataset(BuildConfig(seed=42, scale=1)))
    frame = build_view_frame(con)
    arm = build_view_frame(con, scope=ARM_B_COMMENT_ATTRIBUTION)
    panel = perfect_panel(3)
    estimate, bootstrap = measure_vvr(frame, panel, seed=42, sample_size=9000, replicates=50)
    arm_estimate, _ = measure_vvr(arm, panel, seed=42, sample_size=9000, replicates=2)
    con.close()
    return MeasurementReport(
        stamp=_stamp(),
        governance=_governance(),
        minutes=AnalystMinutesModel().evaluate(cases=1),
        session_id="session-test",
        frame=frame,
        vvr=estimate,
        bootstrap=bootstrap,
        arms=(arm_estimate,),
    )


def test_no_rendering_uses_causal_language(full_report: MeasurementReport) -> None:
    """3.5, over the document a reader actually opens.

    Enforced against both renderings rather than against the workflow module
    alone, because the report is where the prose would drift.
    """
    for body in (full_report.render_markdown(), full_report.render_html()):
        lowered = body.lower()
        for phrase in BANNED_CAUSAL_PHRASES:
            assert phrase not in lowered, f"the report claims {phrase!r}"


def test_honest_limits_appear_in_every_rendering(full_report: MeasurementReport) -> None:
    """Mandatory and carried forward, per CLAUDE.md.

    Checked entry by entry: a limits section that lost one silently is exactly
    the failure this requirement exists to prevent.
    """
    markdown = full_report.render_markdown()
    rendered_html = full_report.render_html()
    payload = full_report.to_json_object()

    assert len(HONEST_LIMITS) >= 8
    for limit in HONEST_LIMITS:
        assert limit in markdown
        assert limit in payload["honest_limits"]  # type: ignore[operator]
    assert "Honest limits" in markdown
    assert "Honest limits" in rendered_html


def test_the_recovery_plateau_limit_is_stated(full_report: MeasurementReport) -> None:
    """The finding Saif accepted as-is, stated plainly rather than buried.

    The metadata-pivot strategy recovers the shared-registration-linked core and
    cannot reach members connected only by looser signals. The report says so,
    names the measured figure, and calls it a bounded limit rather than a
    defect.
    """
    markdown = full_report.render_markdown()

    assert "4 of 8" in markdown
    assert "t02_chan_000_000" in markdown
    assert "bounded limit" in markdown
    assert "rather than a defect" in markdown


def test_arm_b_is_labelled_not_a_vvr_wherever_it_appears(
    full_report: MeasurementReport,
) -> None:
    """The single most misquotable number in the report.

    Arm B is thirty times the baseline. If it can be lifted out without its
    label, it will be.
    """
    markdown = full_report.render_markdown()
    rendered_html = full_report.render_html()

    assert "arm_b_comment_attribution" in markdown
    assert "**NO, attribution differs**" in markdown
    assert "<strong>NO</strong>" in rendered_html

    payload = full_report.to_json_object()["platform_lens"]
    assert isinstance(payload, dict)
    arms = payload["arms"]
    assert isinstance(arms, list)
    assert any(arm["is_faithful_vvr"] is False for arm in arms)


def test_the_headline_states_that_the_interval_excludes_rater_quality(
    full_report: MeasurementReport,
) -> None:
    markdown = full_report.render_markdown()

    assert "sampling error only" in markdown
    assert "do not take into account rater quality" in markdown


def test_a_report_without_a_dataset_says_the_lens_was_not_computed() -> None:
    """Absent, not omitted.

    ``report --session`` alone cannot compute a VVR because it has no dataset.
    A report that simply left the section out would let a reader believe it
    covered more than it did.
    """
    markdown = _workflow_only().render_markdown()

    assert "Platform lens" in markdown
    assert "Not computed" in markdown
    assert _workflow_only().to_json_object()["platform_lens"] is None


def test_the_stamp_carries_every_field_d4_names(full_report: MeasurementReport) -> None:
    """Dataset seed, git SHA, corpus version and a prompt version pointer."""
    rows = dict(full_report.stamp.rows())

    assert rows["dataset seed / scale"] == "42 / 1"
    assert rows["dataset digest"] == "d" * 64
    assert rows["policy corpus"].startswith("1.0.0")
    assert "triage.rationale=" in rows["active prompt versions"]
    assert rows["code (git SHA)"]
    assert rows["measurement seed"] == "42"


def test_an_unrecorded_stamp_field_says_so_rather_than_guessing() -> None:
    stamp = MeasurementStamp.now(measurement_seed=1, dataset_digest="x" * 64, clock=_CLOCK)
    rows = dict(stamp.rows())

    assert rows["dataset seed / scale"] == "not recorded"
    assert rows["policy corpus"] == "not recorded"
    assert rows["active prompt versions"] == "not recorded"


def test_the_report_is_byte_stable_under_a_fixed_clock(tmp_path: Path) -> None:
    """The clock is injected precisely so this can be asserted.

    A generator that read the wall clock internally would produce a different
    artifact every run, and there would be no way to check that anything else
    about it was stable.
    """
    first = write_measurement_report(_workflow_only(), tmp_path / "a")
    second = write_measurement_report(_workflow_only(), tmp_path / "b")

    for left, right in zip(first, second, strict=True):
        assert left.read_bytes() == right.read_bytes(), left.name


def test_written_files_use_unix_newlines(tmp_path: Path) -> None:
    for path in write_measurement_report(_workflow_only(), tmp_path):
        assert b"\r\n" not in path.read_bytes(), path.name


def test_the_html_escapes_what_it_interpolates() -> None:
    """Nothing user-authored reaches the report today, and that is exactly the
    assumption a future change would break quietly."""
    report = MeasurementReport(
        stamp=_stamp(),
        governance=_governance(),
        minutes=AnalystMinutesModel().evaluate(cases=1),
        session_id="<script>alert(1)</script>",
    )

    rendered = report.render_html()

    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_the_governance_section_is_present_even_when_every_control_is_quiet(
    full_report: MeasurementReport,
) -> None:
    """3.3: mandatory, and the zero-note travels with it into the report."""
    markdown = full_report.render_markdown()

    assert "Governance activity" in markdown
    assert "no control above fired" in markdown
    assert "supports no claim" in markdown


def test_the_minutes_section_is_labelled_modelled_in_the_report(
    full_report: MeasurementReport,
) -> None:
    markdown = full_report.render_markdown()

    assert "MODELLED, not measured" in markdown
    assert "No published per-case review-time benchmark exists" in markdown


def test_the_cli_writes_a_report_from_a_session_alone(tmp_path: Path) -> None:
    """D5's literal contract: one command, session artifacts in."""
    session = tmp_path / "session"
    session.mkdir()
    (session / "session_events.json").write_text(
        json.dumps(
            {
                "session_id": "session-cli",
                "turn": {"attempted_hops": 2, "executed_hops": 2},
                "events": [{"seq": 0, "event_type": "session_open", "payload": {}}],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out"

    code = main(["report", "--session", str(session), "--out", str(out)])

    assert code == EXIT_OK
    assert (out / "report.md").is_file()
    assert (out / "report.html").is_file()
    assert (out / "report.json").is_file()
    assert "Not computed" in (out / "report.md").read_text(encoding="utf-8")


def test_the_cli_includes_the_platform_lens_when_given_a_build(tmp_path: Path) -> None:
    """The full artifact, end to end through the real verb."""
    build = tmp_path / "build"
    assert main(["build-dataset", "--seed", "42", "--scale", "1", "--out", str(build)]) == EXIT_OK

    session = tmp_path / "session"
    session.mkdir()
    (session / "session_events.json").write_text(
        json.dumps({"session_id": "session-cli", "turn": {}, "events": []}), encoding="utf-8"
    )
    out = tmp_path / "out"

    code = main(
        [
            "report",
            "--session",
            str(session),
            "--build",
            str(build),
            "--out",
            str(out),
            "--sample-size",
            "2000",
        ]
    )

    assert code == EXIT_OK
    markdown = (out / "report.md").read_text(encoding="utf-8")
    assert "Not computed" not in markdown
    assert "Violative View Rate" in markdown
    assert "arm_b_comment_attribution" in markdown

    for name in ("ci_width_vs_sample_size", "bias_vs_rater_quality", "policy_scope_expansion"):
        assert (out / f"{name}.png").is_file()
        assert (out / f"{name}.json").is_file()
    assert "![ci_width_vs_sample_size.png]" in markdown


def test_the_cli_uses_optimal_allocation_when_it_can(tmp_path: Path) -> None:
    """The published method's own design, not the proportional fallback.

    Worth asserting because the first version of this verb omitted the pilot
    and silently produced proportionally-allocated intervals, which are wider
    than the method being replicated would give.
    """
    build = tmp_path / "build"
    assert main(["build-dataset", "--seed", "42", "--scale", "1", "--out", str(build)]) == EXIT_OK
    session = tmp_path / "session"
    session.mkdir()
    (session / "session_events.json").write_text(
        json.dumps({"session_id": "s", "turn": {}, "events": []}), encoding="utf-8"
    )
    out = tmp_path / "out"

    main(
        [
            "report",
            "--session",
            str(session),
            "--build",
            str(build),
            "--out",
            str(out),
            "--sample-size",
            "9000",
        ]
    )

    assert "allocation=optimal" in (out / "report.md").read_text(encoding="utf-8")


def test_a_missing_session_is_an_input_error(tmp_path: Path) -> None:
    code = main(["report", "--session", str(tmp_path / "nope"), "--out", str(tmp_path / "out")])

    assert code == EXIT_INPUT_ERROR


def test_a_missing_build_store_is_an_input_error(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    (session / "session_events.json").write_text(
        json.dumps({"session_id": "s", "turn": {}, "events": []}), encoding="utf-8"
    )

    code = main(
        [
            "report",
            "--session",
            str(session),
            "--build",
            str(tmp_path / "absent"),
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert code == EXIT_INPUT_ERROR


def test_a_usage_error_exits_five_not_argparse_two(tmp_path: Path) -> None:
    """The collision DECISIONS 2.12 removed, kept removed for this verb.

    Argparse exits 2 on a usage error and 2 is EXIT_QUALITY_GATE_FAIL here, so
    a mistyped flag must not be indistinguishable from a failed quality gate.
    """
    code = main(["report", "--session", str(tmp_path), "--nonsense"])

    assert code == EXIT_INPUT_ERROR
