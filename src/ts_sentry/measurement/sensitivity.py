# SPDX-License-Identifier: MIT
"""STEP-07 D2: the three sensitivity curves, as byte-stable data.

D2 asks for CI width against sample size, estimate bias against rater quality,
and a policy-scope expansion simulation, "every curve reproducible from seed".

Data first, plots second
------------------------
Each curve is produced here as numbers and written as JSON and CSV.
``measurement.plots`` renders those numbers and nothing else. The split is the
reproducibility claim: **the curve data is the byte-stable artifact**, identical
across runs and machines, and it is what a reader regenerates the numbers from.
A PNG is a rendering of it. Nothing in this phase claims a PNG is byte-stable
across matplotlib versions, because that stability would belong to a version
pin rather than to this code.

Floats are rounded to ``ROUND_DECIMALS`` before emission. That is what makes
"identical across machines" true rather than nearly true: the underlying
arithmetic can differ in the last bit or two between builds of numpy, and a
byte-stability claim that a different CPU could falsify is not a claim worth
making.

What the curves are allowed to use
-----------------------------------
These are measurement-side analyses, so they may read ground truth: a bias curve
needs the true value to measure bias against, and there is no other source for
it. What they must not do is let truth reach the estimator, and they do not.
Every estimate below comes from ``measure_vvr``, which sees rater calls only.
Truth appears in these curves as the reference line the estimates are compared
*to*, never as an input to producing them.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ts_sentry.measurement.frame import ViewFrame
from ts_sentry.measurement.raters import RaterPanel, perfect_panel, uniform_panel
from ts_sentry.measurement.vvr import (
    MINIMUM_PER_STRATUM,
    Z_95,
    allocate_proportional,
    measure_vvr,
    stratified_standard_error,
)

__all__ = [
    "ROUND_DECIMALS",
    "Curve",
    "CurvePoint",
    "policy_scope_curve",
    "rater_quality_curve",
    "sample_size_curve",
    "write_curve_data",
]

ROUND_DECIMALS = 10
"""Decimal places retained when a curve is emitted.

Ten is far beyond anything a reader will act on and far short of where
floating-point noise lives, which is the point: it absorbs last-bit differences
between platforms without touching any digit the report displays.
"""


def _round(value: float) -> float:
    return round(value, ROUND_DECIMALS)


@dataclass(frozen=True, slots=True)
class CurvePoint:
    """One point on a curve: an x value and the named series measured there."""

    x: float
    values: Mapping[str, float]
    label: str = ""


@dataclass(frozen=True, slots=True)
class Curve:
    """A named curve, its axes, and the caveat that has to travel with it."""

    name: str
    x_label: str
    y_label: str
    note: str
    points: tuple[CurvePoint, ...]

    def __post_init__(self) -> None:
        if not self.points:
            raise ValueError(f"curve {self.name!r} has no points")
        series = {frozenset(point.values) for point in self.points}
        if len(series) != 1:
            raise ValueError(f"curve {self.name!r} has points with differing series names")

        # Labels reach the CSV unquoted. Today they are scope names and contain
        # neither, but a label with a comma would silently shift every column
        # after it and the file would still parse, which is the failure mode
        # nobody notices. Refused rather than quoted, because a separator inside
        # a curve label is a naming mistake rather than something to accommodate.
        for point in self.points:
            if any(character in point.label for character in ",\r\n"):
                raise ValueError(
                    f"curve {self.name!r} has a label containing a separator: {point.label!r}"
                )

    @property
    def series_names(self) -> tuple[str, ...]:
        return tuple(sorted(self.points[0].values))

    def to_json_object(self) -> dict[str, object]:
        return {
            "name": self.name,
            "x_label": self.x_label,
            "y_label": self.y_label,
            "note": self.note,
            "series": list(self.series_names),
            "points": [
                {
                    "x": _round(point.x),
                    "label": point.label,
                    "values": {name: _round(point.values[name]) for name in self.series_names},
                }
                for point in self.points
            ],
        }

    def to_csv(self) -> str:
        """CSV with an explicit trailing newline and no platform line endings.

        Written by the caller with ``newline="\\n"``. On Windows the default
        would rewrite every separator to CRLF and the file would differ from the
        same run on Linux, which would falsify the byte-stability claim in the
        least interesting way possible.
        """
        header = ",".join(["x", "label", *self.series_names])
        rows = [
            ",".join(
                [
                    repr(_round(point.x)),
                    point.label,
                    *(repr(_round(point.values[name])) for name in self.series_names),
                ]
            )
            for point in self.points
        ]
        return "\n".join([header, *rows]) + "\n"


def sample_size_curve(
    frame: ViewFrame,
    *,
    seed: int,
    sample_sizes: Sequence[int],
    panel: RaterPanel | None = None,
    pilot_fraction: float = 0.2,
) -> Curve:
    """Interval half-width against sample size, drawn and analytic side by side.

    Two series, because either alone misleads. ``realised_half_width`` is what a
    run at that sample size actually produced, rater error and allocation luck
    included. ``analytic_half_width`` is the width the variance formula gives at
    the population's true stratum rates, which is the smooth curve the realised
    one scatters around and the only one that is meaningful where the sample
    found nothing at all.

    ``validity_holds`` rides along as a third series, as 1 or 0, so a reader can
    see exactly where the normal approximation stops being usable rather than
    having to infer it from a width that looks fine.
    """
    reviewers = panel if panel is not None else perfect_panel(3)
    sizes = frame.stratum_sizes()
    true_rates = frame.true_stratum_rates()

    # A pilot has to be big enough to give every *non-empty* stratum the
    # per-stratum minimum, or the allocation it feeds refuses outright. The
    # first version compared against ``len(RiskBand)``, which counts strata that
    # hold no views: with three non-empty strata on this frame it let a pilot of
    # five through and the curve raised at sample sizes around 26.
    smallest_pilot = MINIMUM_PER_STRATUM * sum(1 for size in sizes.values() if size > 0)

    points: list[CurvePoint] = []
    for total in sorted(sample_sizes):
        pilot = int(total * pilot_fraction)
        estimate, _ = measure_vvr(
            frame,
            reviewers,
            seed=seed,
            sample_size=total,
            pilot_size=pilot if pilot >= smallest_pilot else 0,
            replicates=2,
        )
        analytic = stratified_standard_error(
            stratum_sizes=sizes,
            sample_sizes=allocate_proportional(stratum_sizes=sizes, total=total).per_stratum,
            rates=true_rates,
        )
        points.append(
            CurvePoint(
                x=float(total),
                values={
                    "realised_half_width": estimate.half_width,
                    "analytic_half_width": Z_95 * analytic,
                    "point_estimate": estimate.point,
                    "validity_holds": float(estimate.validity.holds),
                },
            )
        )

    return Curve(
        name="ci_width_vs_sample_size",
        x_label="views sampled",
        y_label="95% interval half-width (share of views)",
        note=(
            "Realised widths come from one seed per sample size and scatter around the "
            "analytic curve. The analytic series uses the population's true stratum rates, "
            "which a real platform could not compute; it is the reference line, not an "
            "estimate. Width reaches zero at a census because the finite population "
            "correction does."
        ),
        points=tuple(points),
    )


def rater_quality_curve(
    frame: ViewFrame,
    *,
    seed: int,
    specificities: Sequence[float],
    sample_size: int,
    panel_sizes: Sequence[int] = (1, 3),
) -> Curve:
    """Estimate bias against rater specificity, with the interval beside it.

    The curve D2 exists for, and the one that demonstrates the published
    method's stated limitation. As specificity falls the estimate climbs away
    from the truth, and the 95% half-width does **not** grow to cover the gap,
    because it never modelled rater error in the first place.

    Swept over specificity rather than sensitivity because that is where the
    damage is. At a true rate near 0.1%, a false-positive rate of 1% contributes
    ten times the signal, while even a catastrophic loss of sensitivity can only
    remove the 0.1% that is there.

    Both a single reviewer and a three-rater majority are reported, because
    majority voting suppresses independent error quadratically and the
    difference between the two series is large enough to change what a reader
    concludes.
    """
    truth = frame.true_vvr()
    points: list[CurvePoint] = []

    for specificity in sorted(specificities, reverse=True):
        values: dict[str, float] = {"true_vvr": truth}
        for size in panel_sizes:
            estimate, _ = measure_vvr(
                frame,
                uniform_panel(size, sensitivity=1.0, specificity=specificity),
                seed=seed,
                sample_size=sample_size,
                replicates=2,
            )
            values[f"estimate_panel_{size}"] = estimate.point
            values[f"bias_panel_{size}"] = estimate.point - truth
            values[f"half_width_panel_{size}"] = estimate.half_width
            values[f"covers_truth_panel_{size}"] = float(estimate.lower <= truth <= estimate.upper)
        points.append(CurvePoint(x=specificity, values=values))

    return Curve(
        name="bias_vs_rater_quality",
        x_label="rater specificity (P(calls it fine | it is fine))",
        y_label="share of views",
        note=(
            "The headline interval covers sampling error only, replicating the published "
            "method: 'the confidence intervals do not take into account rater quality'. "
            "Compare each bias series against its half-width series: the bias grows and the "
            "half-width does not follow it. Where covers_truth is 0, a nominally 95% "
            "interval has missed the true value, and the cause is rater error rather than "
            "anything wrong with the interval. Rater error is modelled here as independent "
            "per rater; correlated error, such as a policy misreading a whole panel shares, "
            "is not modelled and would not be suppressed by majority voting."
        ),
        points=tuple(points),
    )


def policy_scope_curve(
    frames: Sequence[ViewFrame],
    *,
    seed: int,
    sample_size: int,
    panel: RaterPanel | None = None,
) -> Curve:
    """Measured rate under each policy scope, baseline first.

    Reports both expansion arms beside the baseline, and keeps them
    distinguishable: ``is_faithful_vvr`` rides along as a series so a renderer
    cannot present the comment-attribution arm as a VVR. Arm A is expected to
    sit exactly on the baseline on this corpus, and that null is the result
    rather than a missing measurement.
    """
    reviewers = panel if panel is not None else perfect_panel(3)
    points: list[CurvePoint] = []

    for index, frame in enumerate(frames):
        estimate, _ = measure_vvr(
            frame,
            reviewers,
            seed=seed,
            sample_size=sample_size,
            replicates=2,
        )
        points.append(
            CurvePoint(
                x=float(index),
                label=frame.scope.name,
                values={
                    "true_rate": frame.true_vvr(),
                    "estimate": estimate.point,
                    "lower": estimate.lower,
                    "upper": estimate.upper,
                    "is_faithful_vvr": float(frame.scope.is_faithful_vvr),
                },
            )
        )

    return Curve(
        name="policy_scope_expansion",
        x_label="policy scope",
        y_label="share of views judged violative",
        note=(
            "Arm A widens the class set and keeps the video-judges-itself attribution. It is "
            "exactly null on this corpus because the classes it adds carry no views, and the "
            "null is reported rather than suppressed. Arm B changes the attribution rule so a "
            "video counts when it hosts a comment-spam-ring comment; it moves the rate and is "
            "where the required scope-effect direction comes from. Arm B is NOT a VVR: the "
            "published method judges the video itself, not comments hosted on it, so arm B "
            "illustrates a policy-scope question rather than extending the replication."
        ),
        points=tuple(points),
    )


def write_curve_data(curves: Sequence[Curve], out_dir: Path) -> tuple[Path, ...]:
    """Write each curve as JSON and CSV, byte-stably.

    ``newline="\\n"`` on every write and ``sort_keys`` on the JSON. Without the
    first, a Windows run and a Linux run of identical numbers produce different
    bytes; without the second, dictionary ordering would do the same thing more
    subtly.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for curve in curves:
        json_path = out_dir / f"{curve.name}.json"
        json_path.write_text(
            json.dumps(curve.to_json_object(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        csv_path = out_dir / f"{curve.name}.csv"
        csv_path.write_text(curve.to_csv(), encoding="utf-8", newline="\n")
        written.extend((json_path, csv_path))
    return tuple(written)
