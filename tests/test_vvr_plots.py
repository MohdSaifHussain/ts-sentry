# SPDX-License-Identifier: MIT
"""STEP-07 D2: deterministic rendering of the sensitivity curves.

Every claim tested here is scoped to what the phase actually asserts. Two
renders in one environment must produce identical bytes. Nothing tests
cross-version stability, because nothing claims it, and a fixture of expected
PNG bytes would turn every matplotlib upgrade into a red suite while proving
only that the pin had not moved.
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
    build_view_frame,
)
from ts_sentry.measurement.plots import (
    PLOTTED_SERIES,
    render_curve,
    render_curves,
)
from ts_sentry.measurement.sensitivity import (
    Curve,
    CurvePoint,
    policy_scope_curve,
    rater_quality_curve,
    sample_size_curve,
)


@pytest.fixture(scope="module")
def dataset() -> Iterator[duckdb.DuckDBPyConnection]:
    con = duckdb.connect()
    persist_dataset(con, build_dataset(BuildConfig(seed=42, scale=1)))
    yield con
    con.close()


@pytest.fixture(scope="module")
def curves(dataset: duckdb.DuckDBPyConnection) -> tuple[Curve, ...]:
    frame = build_view_frame(dataset)
    arms = tuple(
        build_view_frame(dataset, scope=scope)
        for scope in (BASELINE_SCOPE, ARM_A_CLASS_EXPANSION, ARM_B_COMMENT_ATTRIBUTION)
    )
    return (
        sample_size_curve(frame, seed=42, sample_sizes=[500, 2000, 9000]),
        rater_quality_curve(frame, seed=42, specificities=(1.0, 0.99, 0.9), sample_size=2000),
        policy_scope_curve(arms, seed=42, sample_size=2000),
    )


def test_two_renders_in_one_environment_are_byte_identical(
    curves: tuple[Curve, ...], tmp_path: Path
) -> None:
    """The claim, at exactly its stated width.

    Suppressing the Agg PNG writer's auto-generated ``Software`` key is what
    makes this hold; without it the matplotlib version is embedded in every
    file. This says nothing about a different matplotlib producing the same
    bytes, and nothing here should be read as saying so.
    """
    first = render_curves(curves, tmp_path / "first")
    second = render_curves(curves, tmp_path / "second")

    for left, right in zip(first, second, strict=True):
        assert left.read_bytes() == right.read_bytes(), left.name


def test_the_matplotlib_version_is_not_embedded_in_the_output(
    curves: tuple[Curve, ...], tmp_path: Path
) -> None:
    """Guards the mechanism rather than the symptom.

    The byte-identity test above would keep passing if the metadata
    suppression were removed, because both renders would embed the *same*
    version. This is the assertion that actually notices.
    """
    import matplotlib

    path = render_curve(curves[0], tmp_path / "one.png")
    body = path.read_bytes()

    assert b"Software" not in body
    assert matplotlib.__version__.encode() not in body


def test_every_curve_the_phase_produces_has_a_plot_specification(
    curves: tuple[Curve, ...],
) -> None:
    """A renderer must never guess which series belong on an axis.

    ``validity_holds`` is 0 or 1 beside widths of around 0.001; drawing them
    together would flatten every real series onto the x-axis. So the split is
    declared per curve, and a curve without a declaration is refused rather
    than rendered wrongly.
    """
    for curve in curves:
        assert curve.name in PLOTTED_SERIES
        values, indicators = PLOTTED_SERIES[curve.name]
        known = set(curve.series_names)
        assert set(values) <= known, f"{curve.name} plots a series it does not have"
        assert set(indicators) <= known
        assert not set(values) & set(indicators)


def test_an_unspecified_curve_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    unknown = Curve(
        name="something_new",
        x_label="x",
        y_label="y",
        note="n" * 120,
        points=(CurvePoint(x=0.0, values={"a": 1.0}),),
    )

    with pytest.raises(ValueError, match="no plot specification"):
        render_curve(unknown, tmp_path / "nope.png")


def test_categorical_and_continuous_curves_render_differently(
    curves: tuple[Curve, ...], tmp_path: Path
) -> None:
    """Decided from the data, not from a caller's argument.

    The policy-scope arms are three unrelated scopes, and a caller must not be
    able to ask for a continuous line drawn through them as though moving from
    one to the next meant something.
    """
    _, _, scope_curve = curves
    line_curve = curves[0]

    assert any(point.label for point in scope_curve.points)
    assert not any(point.label for point in line_curve.points)

    rendered = render_curves((line_curve, scope_curve), tmp_path)
    assert all(path.stat().st_size > 0 for path in rendered)
    assert rendered[0].read_bytes() != rendered[1].read_bytes()


def test_rendering_creates_its_output_directory(curves: tuple[Curve, ...], tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested"

    written = render_curves(curves[:1], target)

    assert written[0].is_file()
    assert written[0].parent == target


def test_rendering_touches_no_global_matplotlib_state(
    curves: tuple[Curve, ...], tmp_path: Path
) -> None:
    """No pyplot, no backend switch, no figure left in a registry.

    A module that flipped the global backend on import would change how every
    other importer in the process renders, and a figure left in pyplot's
    registry is a leak that grows with each curve. Constructing the Agg canvas
    directly avoids both, and this checks the avoidance rather than trusting it.
    """
    import sys

    render_curves(curves, tmp_path)

    assert "matplotlib.pyplot" not in sys.modules, (
        "the plot module must not import pyplot; it constructs its canvas directly"
    )
