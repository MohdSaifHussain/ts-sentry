# SPDX-License-Identifier: MIT
"""STEP-07 D2: the three sensitivity curves and their byte-stable emission.

The byte-stability tests here are the ones the phase's reproducibility claim
rests on, and they are deliberately about *bytes* rather than about values.
Equal numbers written through different newline handling are still different
files, and a claim of reproducibility across machines that only ever held on one
platform would be the kind of thing STEP-06 caught with `core.autocrlf`.
"""

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from ts_sentry.data.generator import build_dataset
from ts_sentry.data.population import BuildConfig
from ts_sentry.data.store import persist_dataset
from ts_sentry.measurement.frame import (
    ARM_A_CLASS_EXPANSION,
    ARM_B_COMMENT_ATTRIBUTION,
    BASELINE_SCOPE,
    ViewFrame,
    build_view_frame,
)
from ts_sentry.measurement.sensitivity import (
    Curve,
    CurvePoint,
    policy_scope_curve,
    rater_quality_curve,
    sample_size_curve,
    write_curve_data,
)

SPECIFICITIES = (1.0, 0.999, 0.99, 0.95, 0.9)


@pytest.fixture(scope="module")
def dataset() -> Iterator[duckdb.DuckDBPyConnection]:
    con = duckdb.connect()
    persist_dataset(con, build_dataset(BuildConfig(seed=42, scale=1)))
    yield con
    con.close()


@pytest.fixture(scope="module")
def frame(dataset: duckdb.DuckDBPyConnection) -> ViewFrame:
    return build_view_frame(dataset)


@pytest.fixture(scope="module")
def arms(dataset: duckdb.DuckDBPyConnection) -> tuple[ViewFrame, ...]:
    return tuple(
        build_view_frame(dataset, scope=scope)
        for scope in (BASELINE_SCOPE, ARM_A_CLASS_EXPANSION, ARM_B_COMMENT_ATTRIBUTION)
    )


def test_the_analytic_width_falls_monotonically_with_sample_size(
    frame: ViewFrame,
) -> None:
    """The reference series is the one that must be smooth.

    The realised series is one seed's luck at each point and is not asserted to
    be monotone, because asserting that would be asserting something untrue
    about sampling.
    """
    curve = sample_size_curve(frame, seed=42, sample_sizes=[500, 2000, 9000, 18780])

    widths = [point.values["analytic_half_width"] for point in curve.points]
    assert widths == sorted(widths, reverse=True)
    assert widths[-1] == 0.0


def test_the_sample_size_curve_reports_where_validity_ends(frame: ViewFrame) -> None:
    """The series exists so a reader does not have to infer it from the width.

    On this corpus the approximation is invalid at every realistic sample size
    and only becomes valid at a full census, which is the honest headline of
    this curve rather than a footnote to it.
    """
    curve = sample_size_curve(frame, seed=42, sample_sizes=[500, 2000, 9000, 18780])

    validity = [point.values["validity_holds"] for point in curve.points]
    assert validity[0] == 0.0
    assert validity[-1] == 1.0


def test_bias_grows_as_rater_quality_falls(frame: ViewFrame) -> None:
    curve = rater_quality_curve(frame, seed=42, specificities=SPECIFICITIES, sample_size=9000)

    biases = [point.values["bias_panel_1"] for point in curve.points]
    assert biases == sorted(biases), "falling specificity must not reduce the bias"
    assert biases[0] == pytest.approx(0.0, abs=1e-3)
    assert biases[-1] > 0.05


def test_the_interval_does_not_grow_to_cover_the_bias(frame: ViewFrame) -> None:
    """D2's whole point, as an assertion.

    Between perfect specificity and 90%, the single-reviewer bias grows by two
    orders of magnitude more than the half-width does. The interval is not
    tracking the error that dominates, exactly as the published method says it
    does not.
    """
    curve = rater_quality_curve(frame, seed=42, specificities=SPECIFICITIES, sample_size=9000)
    first, last = curve.points[0], curve.points[-1]

    bias_growth = last.values["bias_panel_1"] - first.values["bias_panel_1"]
    width_growth = last.values["half_width_panel_1"] - first.values["half_width_panel_1"]

    assert bias_growth > 10 * width_growth


def test_the_interval_stops_covering_the_truth_as_raters_degrade(
    frame: ViewFrame,
) -> None:
    curve = rater_quality_curve(frame, seed=42, specificities=SPECIFICITIES, sample_size=9000)

    coverage = [point.values["covers_truth_panel_1"] for point in curve.points]
    assert coverage[0] == 1.0
    assert coverage[-1] == 0.0


def test_a_panel_holds_coverage_longer_than_a_single_reviewer(
    frame: ViewFrame,
) -> None:
    """The quadratic suppression, visible on the curve.

    At 99% specificity a single reviewer's interval has already lost the truth
    while a three-rater majority still covers it.
    """
    curve = rater_quality_curve(frame, seed=42, specificities=SPECIFICITIES, sample_size=9000)
    at_99 = next(point for point in curve.points if point.x == pytest.approx(0.99))

    assert at_99.values["covers_truth_panel_1"] == 0.0
    assert at_99.values["covers_truth_panel_3"] == 1.0
    assert at_99.values["estimate_panel_3"] < at_99.values["estimate_panel_1"]


def test_arm_a_sits_exactly_on_the_baseline_and_arm_b_moves(
    arms: tuple[ViewFrame, ...],
) -> None:
    """Both arms on one curve, which is what makes the null legible.

    Arm A landing precisely on the baseline is the measurement, not a missing
    measurement, and it is only obvious as such when the two are plotted
    together.
    """
    curve = policy_scope_curve(arms, seed=42, sample_size=9000)
    baseline, arm_a, arm_b = curve.points

    assert arm_a.values["true_rate"] == baseline.values["true_rate"]
    assert arm_a.values["estimate"] == baseline.values["estimate"]
    assert arm_b.values["true_rate"] > 10 * baseline.values["true_rate"]


def test_the_scope_curve_marks_which_arm_is_not_a_vvr(
    arms: tuple[ViewFrame, ...],
) -> None:
    """A renderer must be able to tell them apart from the data alone."""
    curve = policy_scope_curve(arms, seed=42, sample_size=9000)

    faithful = {point.label: point.values["is_faithful_vvr"] for point in curve.points}
    assert faithful["baseline"] == 1.0
    assert faithful["arm_a_class_expansion"] == 1.0
    assert faithful["arm_b_comment_attribution"] == 0.0
    assert "NOT a VVR" in curve.note


def test_every_curve_carries_its_caveat(frame: ViewFrame, arms: tuple[ViewFrame, ...]) -> None:
    """A curve without its note is a chart that will be read wrong.

    The notes are where "this series uses ground truth a platform could not
    compute", "the interval does not model rater error" and "arm B is not a VVR"
    live, and all three are the sort of thing that gets lost when a figure is
    lifted out of a report.
    """
    curves = [
        sample_size_curve(frame, seed=1, sample_sizes=[500, 2000]),
        rater_quality_curve(frame, seed=1, specificities=(1.0, 0.99), sample_size=2000),
        policy_scope_curve(arms, seed=1, sample_size=2000),
    ]

    for curve in curves:
        assert len(curve.note) > 100, f"{curve.name} has no meaningful caveat"
        assert curve.x_label and curve.y_label


def test_curve_data_is_byte_identical_across_runs(frame: ViewFrame, tmp_path: Path) -> None:
    """The reproducibility claim, tested as bytes rather than as values."""
    curve = sample_size_curve(frame, seed=42, sample_sizes=[500, 2000, 9000])

    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    write_curve_data([curve], first_dir)
    rerun = sample_size_curve(frame, seed=42, sample_sizes=[500, 2000, 9000])
    write_curve_data([rerun], second_dir)

    for name in ("ci_width_vs_sample_size.json", "ci_width_vs_sample_size.csv"):
        assert (first_dir / name).read_bytes() == (second_dir / name).read_bytes()


def test_emitted_files_use_unix_newlines_on_every_platform(
    frame: ViewFrame, tmp_path: Path
) -> None:
    """Without this the same numbers produce different bytes on Windows.

    The default text mode would rewrite every separator to CRLF, and the
    across-machines half of the byte-stability claim would be false while every
    value-level test kept passing. STEP-06 found the same class of problem in
    the prompt registry through `core.autocrlf`.
    """
    curve = sample_size_curve(frame, seed=42, sample_sizes=[500, 2000])

    written = write_curve_data([curve], tmp_path)

    for path in written:
        assert b"\r\n" not in path.read_bytes(), f"{path.name} carries platform line endings"
        assert path.read_bytes().endswith(b"\n")


def test_the_csv_and_json_agree_on_every_value(frame: ViewFrame, tmp_path: Path) -> None:
    """Two serialisations of one curve must not drift apart.

    The CSV is the form a reader is most likely to open in a spreadsheet and the
    JSON is the form a program reads, so a divergence between them would be
    found by whichever audience was unlucky.
    """
    import json

    curve = sample_size_curve(frame, seed=42, sample_sizes=[500, 2000, 9000])
    write_curve_data([curve], tmp_path)

    payload = json.loads((tmp_path / f"{curve.name}.json").read_text(encoding="utf-8"))
    rows = (tmp_path / f"{curve.name}.csv").read_text(encoding="utf-8").splitlines()
    header = rows[0].split(",")

    assert header[2:] == payload["series"]
    for row, point in zip(rows[1:], payload["points"], strict=True):
        cells = row.split(",")
        assert float(cells[0]) == point["x"]
        for name, cell in zip(payload["series"], cells[2:], strict=True):
            assert float(cell) == point["values"][name]


def test_a_curve_refuses_to_be_empty_or_ragged() -> None:
    with pytest.raises(ValueError, match="no points"):
        Curve(name="c", x_label="x", y_label="y", note="n", points=())
    with pytest.raises(ValueError, match="differing series"):
        Curve(
            name="c",
            x_label="x",
            y_label="y",
            note="n",
            points=(
                CurvePoint(x=0.0, values={"a": 1.0}),
                CurvePoint(x=1.0, values={"b": 2.0}),
            ),
        )
