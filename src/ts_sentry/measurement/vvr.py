# SPDX-License-Identifier: MIT
"""STEP-07 D1: stratified sampling, the VVR estimate, and its 95% interval.

The arithmetic of the published method, in the order the sources describe it:
sample views within strata, send the sampled videos for review, aggregate the
reviewers' decisions into a rate, and report it with a 95% confidence interval.

    "We then use the aggregate results to estimate the proportion of views on
    YouTube that violate our community guidelines... The VVR metric is reported
    with a 95% confidence interval. This means that if we performed the
    measurement many times for the same time period, we would expect the true
    metric to lie within the interval 95% of the time."
    - https://support.google.com/transparencyreport/answer/9209072

Allocation is the part that matters most, and the part easiest to get wrong
-----------------------------------------------------------------------------
Sampling effort is **not** spread proportionally across strata. Barnett's Table
2B has the lowest-risk stratum holding 80% of the population and receiving 52.5%
of the sample, while the highest-risk stratum holds 1% and receives 6.4%:

    "strata in which the expected VVR is especially low would receive a lesser
    share of the sampling than their share of the population... sampling
    resources gravitate towards strata with higher VVRs"
    - Barnett, section III

On his own published population this is worth a third of the interval width:
optimal allocation gives a standard error of 0.054 percentage points where
proportional allocation gives 0.070, and proportional stratification in turn
beats no stratification at all by under one percent. On a rare-event estimand
nearly all of the benefit lives in the allocation rather than in the act of
stratifying. ``test_vvr_estimator`` reproduces Barnett's Table 2B exactly from
his inputs, which checks this arithmetic against a published external reference
rather than against our own expectations.

Where the allocation prior comes from, and why it is not ground truth
----------------------------------------------------------------------
Optimal allocation needs a prior ``p_h`` per stratum, and taking that from
``sealed._labels`` would optimise the sample against answers the method does not
have. YouTube does not do that either; it uses its own prior measurements:

    "the sample sizes in the five ranges are revised each day based on actual
    VVR rates in those ranges over the 90 preceding days"
    - Barnett, section III

The single-snapshot analogue is a **pilot sample**, drawn and rated through the
same panel, contributing its rater decisions and nothing else. ``allocate``
takes two mappings of numbers and has no route to a label. The pilot is then
discarded from the final estimate: it is an independent draw, so the estimate
stays unbiased conditional on the allocation it produced, at the cost of the
views spent on it.

A floor on the prior is **required for correctness**, not convenience. At this
corpus's true rate a thousand-view pilot expects one violative view in total, so
``p_h = 0`` everywhere is the normal outcome, and Neyman's key collapses to 0/0.
The floor is the "educated guess" Barnett describes starting from, and its
consequence is worth stating: when the pilot learns nothing, optimal allocation
degenerates to proportional, which is the correct behaviour rather than a
fallback.

What the interval does and does not cover
------------------------------------------
Sampling error only. Rater error is modelled in ``raters`` and reported by the
D2 bias curve, never folded in here, because the method being replicated says:

    "The confidence intervals do not take into account rater quality, which may
    impact our measurements."

A wider interval would be a better estimate and a worse replication.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from ts_sentry.measurement.frame import RiskBand, ViewFrame
from ts_sentry.measurement.raters import RaterPanel

__all__ = [
    "MINIMUM_PER_STRATUM",
    "PRIOR_RATE_FLOOR",
    "Z_95",
    "Allocation",
    "BootstrapCheck",
    "NormalApproximationCheck",
    "StratumResult",
    "VvrEstimate",
    "allocate_optimal",
    "allocate_proportional",
    "bootstrap_check",
    "draw_stratified_sample",
    "estimate_from_calls",
    "measure_vvr",
    "stratified_standard_error",
]

Z_95 = 1.959963984540054
"""Two-sided standard normal quantile for 95% coverage.

Spelled out rather than pulled from scipy: this project does not depend on
scipy, and the constant is the only thing it would have been imported for.
"""

PRIOR_RATE_FLOOR = 1e-4
"""Floor applied to each stratum's prior rate before optimal allocation.

Not a smoothing nicety. A stratum whose pilot found nothing gets ``p_h = 0``,
whose Neyman key is zero, whose allocation is zero, and a stratum that is never
sampled cannot contribute to the estimate at all. Since a zero pilot result is
the *expected* outcome at this corpus's rate, without a floor the estimator
would routinely stop sampling the strata most likely to matter.

One in ten thousand is chosen as the order of magnitude of the rates the
published method actually reports (Barnett's Table 1 quarterly VVRs run 0.17% to
0.20%, and his conclusion describes "about one ten-thousandth"). It is a stated
prior belief, not a measurement, and it never enters the estimate.
"""

MINIMUM_PER_STRATUM = 2
"""Smallest sample a non-empty stratum may receive.

Two, because the SRSWOR variance estimator divides by ``n_h - 1``. A stratum
allocated one view has an undefined variance contribution, so this is the point
where the interval stops existing rather than a tuning knob.
"""


@dataclass(frozen=True, slots=True)
class Allocation:
    """How many views to draw from each stratum, and how that was decided."""

    per_stratum: Mapping[RiskBand, int]
    method: str
    prior_rates: Mapping[RiskBand, float]

    @property
    def total(self) -> int:
        return sum(self.per_stratum.values())

    def shares(self) -> Mapping[RiskBand, float]:
        total = self.total
        return {band: count / total for band, count in self.per_stratum.items()} if total else {}


def _largest_remainder(
    weights: Mapping[RiskBand, float], total: int, caps: Mapping[RiskBand, int]
) -> dict[RiskBand, int]:
    """Round real-valued targets to integers summing exactly to ``total``.

    Largest remainder rather than plain rounding, because plain rounding does not
    sum to the requested total and the shortfall would land wherever the
    floating-point dust fell. This method is also what reproduces Barnett's
    published Table 2B allocation exactly, which is the check that it matches
    the arithmetic the source used.
    """
    floors = {band: min(int(math.floor(value)), caps[band]) for band, value in weights.items()}
    shortfall = total - sum(floors.values())
    if shortfall <= 0:
        return floors

    # Ties broken by band order so two runs of one input agree.
    ranked = sorted(
        weights,
        key=lambda band: (-(weights[band] - math.floor(weights[band])), list(RiskBand).index(band)),
    )
    for band in ranked:
        if shortfall == 0:
            break
        if floors[band] < caps[band]:
            floors[band] += 1
            shortfall -= 1
    return floors


def _allocate(
    keys: Mapping[RiskBand, float],
    stratum_sizes: Mapping[RiskBand, int],
    total: int,
    minimum_per_stratum: int,
    method: str,
    prior_rates: Mapping[RiskBand, float],
) -> Allocation:
    """Water-fill ``total`` across strata in proportion to ``keys``.

    Two constraints bind and both matter: a stratum cannot yield more views than
    it holds, and a non-empty stratum must receive at least two or its variance
    contribution is undefined. Surplus freed by a stratum hitting its cap is
    redistributed rather than dropped, so the requested total is always the
    total delivered.
    """
    active = [band for band in RiskBand if stratum_sizes[band] > 0]
    if not active:
        raise ValueError("every stratum is empty; there is nothing to sample")

    capacity = sum(stratum_sizes[band] for band in active)
    if total > capacity:
        raise ValueError(f"cannot draw {total} views from a population of {capacity}")

    floors = {band: min(minimum_per_stratum, stratum_sizes[band]) for band in active}
    if sum(floors.values()) > total:
        raise ValueError(
            f"a sample of {total} cannot give {minimum_per_stratum} views to each of "
            f"{len(active)} non-empty strata; raise the sample size or merge strata"
        )

    # The bounds are *constraints*, not a pre-allocation. Seeding every stratum
    # with its minimum first and sharing out the rest would perturb the answer
    # away from the proportional-to-key one even when no bound binds, which is
    # how the first version of this failed to reproduce Barnett's Table 2B: it
    # came back 2095/828/584/257/236 against his 2098/828/584/256/234. So the
    # unconstrained targets are computed first and only strata that violate a
    # bound get pinned, with the rest re-solved over what is left.
    pinned: dict[RiskBand, float] = {}
    free = set(active)
    remaining = float(total)
    targets: dict[RiskBand, float] = {}

    while free:
        key_total = sum(keys[band] for band in free)
        if key_total <= 0.0:
            targets = {band: remaining / len(free) for band in free}
        else:
            targets = {band: remaining * keys[band] / key_total for band in free}

        violated: list[RiskBand] = []
        for band in free:
            low = float(min(minimum_per_stratum, stratum_sizes[band]))
            high = float(stratum_sizes[band])
            if targets[band] > high:
                pinned[band] = high
                violated.append(band)
            elif targets[band] < low:
                pinned[band] = low
                violated.append(band)

        if not violated:
            break
        free -= set(violated)
        remaining = total - sum(pinned.values())

    allocated: dict[RiskBand, float] = {**pinned, **{band: targets[band] for band in free}}

    rounded = _largest_remainder(
        {band: float(allocated[band]) for band in active},
        total,
        {band: stratum_sizes[band] for band in active},
    )
    return Allocation(
        per_stratum={band: rounded.get(band, 0) for band in RiskBand},
        method=method,
        prior_rates=dict(prior_rates),
    )


def allocate_proportional(
    *,
    stratum_sizes: Mapping[RiskBand, int],
    total: int,
    minimum_per_stratum: int = MINIMUM_PER_STRATUM,
) -> Allocation:
    """Sample each stratum in proportion to its share of the population.

    The baseline the optimal allocation is measured against, and what optimal
    allocation degenerates to when the prior carries no information.
    """
    return _allocate(
        keys={band: float(stratum_sizes[band]) for band in RiskBand},
        stratum_sizes=stratum_sizes,
        total=total,
        minimum_per_stratum=minimum_per_stratum,
        method="proportional",
        prior_rates={},
    )


def allocate_optimal(
    *,
    stratum_sizes: Mapping[RiskBand, int],
    prior_rates: Mapping[RiskBand, float],
    total: int,
    minimum_per_stratum: int = MINIMUM_PER_STRATUM,
    rate_floor: float = PRIOR_RATE_FLOOR,
) -> Allocation:
    """Neyman allocation: ``n_h`` proportional to ``N_h * sqrt(p_h(1 - p_h))``.

    Takes two mappings of numbers. It receives no connection, no frame and no
    label, so the design cannot be optimised against ground truth even by
    mistake, which is the property that keeps the resulting estimate honest.
    Cochran, *Sampling Techniques* (Wiley, 1997), chapter 5 is the theory
    Barnett names for this.
    """
    if not 0.0 <= rate_floor < 0.5:
        raise ValueError(f"rate_floor must lie in [0, 0.5); got {rate_floor}")

    keys: dict[RiskBand, float] = {}
    for band in RiskBand:
        rate = min(max(prior_rates.get(band, 0.0), rate_floor), 1.0)
        keys[band] = stratum_sizes.get(band, 0) * math.sqrt(rate * (1.0 - rate))

    return _allocate(
        keys=keys,
        stratum_sizes=stratum_sizes,
        total=total,
        minimum_per_stratum=minimum_per_stratum,
        method="optimal",
        prior_rates=dict(prior_rates),
    )


def draw_stratified_sample(
    frame: ViewFrame,
    allocation: Allocation,
    *,
    rng: np.random.Generator,
) -> Mapping[RiskBand, NDArray[np.int_]]:
    """Draw ``n_h`` views from each stratum without replacement.

    Without replacement because the population is finite and known, which is the
    same reason the variance below carries a finite population correction. Drawn
    in band order from the frame's stable index order, so one seed reproduces one
    sample exactly.
    """
    grouped = frame.indices_by_stratum()
    drawn: dict[RiskBand, NDArray[np.int_]] = {}
    for band in RiskBand:
        wanted = allocation.per_stratum.get(band, 0)
        if wanted <= 0:
            drawn[band] = np.empty(0, dtype=np.int_)
            continue
        available = np.asarray(grouped[band], dtype=np.int_)
        if wanted > available.size:
            raise ValueError(
                f"allocation asks for {wanted} views from {band.value}, "
                f"which holds {available.size}"
            )
        drawn[band] = rng.choice(available, size=wanted, replace=False)
    return drawn


def stratified_standard_error(
    *,
    stratum_sizes: Mapping[RiskBand, int],
    sample_sizes: Mapping[RiskBand, int],
    rates: Mapping[RiskBand, float],
) -> float:
    """``sqrt(sum_h W_h^2 (1 - n_h/N_h) p_h(1 - p_h) / (n_h - 1))``.

    The SRSWOR variance of a stratified proportion, with the finite population
    correction that STEP-07 3.1 names. Exposed rather than buried inside the
    estimate because D2's sample-size curve needs the interval width at sample
    sizes nobody drew: sweeping ``n`` analytically is exact, whereas sweeping it
    by simulation would add Monte Carlo noise to a curve whose whole purpose is
    to show how width falls with ``n``.

    Strata with fewer than two sampled views contribute nothing, because the
    ``n_h - 1`` denominator is undefined there. That is a silent zero only in the
    sense that ``NormalApproximationCheck`` reports it loudly.
    """
    population = sum(stratum_sizes.values())
    if population == 0:
        raise ValueError("cannot compute a standard error over an empty population")

    variance = 0.0
    for band, size in stratum_sizes.items():
        drawn = sample_sizes.get(band, 0)
        if size == 0 or drawn < 2:
            continue
        rate = rates.get(band, 0.0)
        weight = size / population
        fpc = 1.0 - drawn / size
        variance += weight**2 * fpc * rate * (1.0 - rate) / (drawn - 1)
    return math.sqrt(variance)


@dataclass(frozen=True, slots=True)
class StratumResult:
    """One stratum's contribution, kept so the estimate can be recomputed by
    hand from the report."""

    band: RiskBand
    population: int
    sampled: int
    violative_calls: int

    @property
    def rate(self) -> float:
        """``p_hat_h``, from the reviewers' calls rather than from truth."""
        return self.violative_calls / self.sampled if self.sampled else 0.0

    @property
    def sampling_fraction(self) -> float:
        return self.sampled / self.population if self.population else 0.0


@dataclass(frozen=True, slots=True)
class NormalApproximationCheck:
    """Whether the interval's assumptions actually hold on this sample.

    STEP-07 3.1 asks for "documented validity conditions", which is only worth
    anything if they are checked and the failures are reported. They fail
    routinely at this corpus's rate, and that is the finding rather than an
    embarrassment: it is precisely why a bootstrap cross-check is required.

    ``degenerate_strata`` was added after the D2 sample-size curve produced a
    zero-width interval at 14,000 views that the other four conditions all
    passed. The cause is the Wald interval's known collapse at ``p_hat = 0``: an
    under-sampled stratum that happens to contain no violative calls contributes
    ``p(1-p) = 0`` to the variance, so the interval reports certainty it has not
    earned. Observing nothing in a sample is not evidence that a stratum
    contains nothing, and the aggregate conditions cannot see the difference
    because in aggregate the sample did find violative views elsewhere. A
    stratum sampled to a census is excluded, because there the zero variance is
    real rather than an artifact.
    """

    every_stratum_has_two: bool
    successes: float
    failures: float
    success_threshold: float
    interval_within_unit: bool
    degenerate_strata: tuple[RiskBand, ...]

    @property
    def no_degenerate_strata(self) -> bool:
        return not self.degenerate_strata

    @property
    def holds(self) -> bool:
        return (
            self.every_stratum_has_two
            and self.successes >= self.success_threshold
            and self.failures >= self.success_threshold
            and self.interval_within_unit
            and self.no_degenerate_strata
        )

    def render(self) -> str:
        def mark(passed: bool) -> str:
            return "ok  " if passed else "FAIL"

        degenerate = (
            "none"
            if self.no_degenerate_strata
            else ", ".join(band.value for band in self.degenerate_strata)
        )
        return "\n".join(
            [
                f"  {mark(self.every_stratum_has_two)} every sampled stratum has n_h >= 2",
                f"  {mark(self.successes >= self.success_threshold)} expected violative calls "
                f"{self.successes:.2f} >= {self.success_threshold:g}",
                f"  {mark(self.failures >= self.success_threshold)} expected non-violative calls "
                f"{self.failures:.2f} >= {self.success_threshold:g}",
                f"  {mark(self.interval_within_unit)} interval lies inside [0, 1] without clipping",
                f"  {mark(self.no_degenerate_strata)} no under-sampled stratum returned an "
                f"all-or-nothing rate: {degenerate}",
            ]
        )


@dataclass(frozen=True, slots=True)
class BootstrapCheck:
    """The stratified bootstrap interval, beside the analytic one.

    Resampling with replacement inside each stratum ignores the finite
    population correction, so the bootstrap interval is **expected to be wider**
    than the analytic one, by roughly ``1 / sqrt(1 - f)`` at overall sampling
    fraction ``f``. That direction is asserted rather than glossed: a bootstrap
    interval that came out narrower than an FPC-corrected analytic interval would
    mean one of the two is wrong.
    """

    replicates: int
    lower: float
    upper: float
    analytic_half_width: float
    expected_ratio: float
    applicable: bool

    @property
    def half_width(self) -> float:
        return (self.upper - self.lower) / 2.0

    @property
    def width_ratio(self) -> float:
        if self.analytic_half_width <= 0.0:
            return math.nan
        return self.half_width / self.analytic_half_width

    def agrees(self, *, tolerance: float) -> bool:
        """Whether the two intervals agree within a documented tolerance.

        Returns ``True`` when the check is not applicable, and ``applicable``
        says which case a reader is looking at. At a point estimate of zero both
        intervals collapse to a point and the comparison is vacuous rather than
        passing, which is the common case on this corpus and must not be
        reported as agreement.
        """
        if not self.applicable:
            return True
        return abs(self.width_ratio - self.expected_ratio) <= tolerance


@dataclass(frozen=True, slots=True)
class VvrEstimate:
    """The estimate, its interval, and everything needed to audit both."""

    scope_name: str
    is_faithful_vvr: bool
    point: float
    standard_error: float
    strata: tuple[StratumResult, ...]
    population: int
    disagreement_rate: float
    validity: NormalApproximationCheck
    allocation_method: str

    @property
    def sampled(self) -> int:
        return sum(stratum.sampled for stratum in self.strata)

    @property
    def half_width(self) -> float:
        return Z_95 * self.standard_error

    @property
    def lower(self) -> float:
        """Clipped at zero, because a negative share of views is not a thing.

        The clipping is recorded by ``validity.interval_within_unit`` rather than
        hidden: an interval that had to be clipped is one whose normal
        approximation was not valid, and the reader needs to know that.
        """
        return max(0.0, self.point - self.half_width)

    @property
    def upper(self) -> float:
        return min(1.0, self.point + self.half_width)

    def render(self) -> str:
        headline = "VVR" if self.is_faithful_vvr else "rate (NOT a VVR: attribution differs)"
        lines = [
            f"{headline}: {100 * self.point:.4f}%  "
            f"95% CI [{100 * self.lower:.4f}%, {100 * self.upper:.4f}%]",
            f"scope={self.scope_name}  allocation={self.allocation_method}  "
            f"n={self.sampled} of N={self.population}",
            "",
            f"{'stratum':<20}{'N_h':>8}{'n_h':>7}{'calls':>7}{'p_h':>11}",
            "-" * 53,
        ]
        for stratum in self.strata:
            lines.append(
                f"{stratum.band.value:<20}{stratum.population:>8}{stratum.sampled:>7}"
                f"{stratum.violative_calls:>7}{100 * stratum.rate:>10.4f}%"
            )
        lines.extend(
            [
                "",
                "normal approximation validity:",
                self.validity.render(),
                "",
                f"panel disagreement rate: {100 * self.disagreement_rate:.2f}%",
                "interval covers sampling error only; rater quality is not in it",
            ]
        )
        return "\n".join(lines)


def estimate_from_calls(
    frame: ViewFrame,
    sample: Mapping[RiskBand, NDArray[np.int_]],
    calls: Mapping[RiskBand, NDArray[np.bool_]],
    *,
    allocation_method: str,
    disagreement_rate: float,
    success_threshold: float = 10.0,
) -> VvrEstimate:
    """Aggregate reviewers' calls into a stratified estimate and its interval.

    Computed from ``calls``, never from ``frame.violative``. That is the whole
    point: the estimator sees what the reviewers said, exactly as YouTube's does,
    and ground truth is available in this codebase only to check the answer
    afterwards.
    """
    sizes = frame.stratum_sizes()
    strata = tuple(
        StratumResult(
            band=band,
            population=sizes[band],
            sampled=int(sample[band].size),
            violative_calls=int(np.count_nonzero(calls[band])),
        )
        for band in RiskBand
        if sizes[band] > 0
    )

    point = sum(
        (stratum.population / frame.size) * stratum.rate for stratum in strata if stratum.sampled
    )
    standard_error = stratified_standard_error(
        stratum_sizes={stratum.band: stratum.population for stratum in strata},
        sample_sizes={stratum.band: stratum.sampled for stratum in strata},
        rates={stratum.band: stratum.rate for stratum in strata},
    )

    sampled_total = sum(stratum.sampled for stratum in strata)
    half_width = Z_95 * standard_error
    validity = NormalApproximationCheck(
        every_stratum_has_two=all(
            stratum.sampled >= 2 for stratum in strata if stratum.sampled > 0
        ),
        successes=point * sampled_total,
        failures=(1.0 - point) * sampled_total,
        success_threshold=success_threshold,
        interval_within_unit=(point - half_width) >= 0.0 and (point + half_width) <= 1.0,
        degenerate_strata=tuple(
            stratum.band
            for stratum in strata
            if stratum.sampled >= 2
            and stratum.sampled < stratum.population
            and stratum.rate in (0.0, 1.0)
        ),
    )

    return VvrEstimate(
        scope_name=frame.scope.name,
        is_faithful_vvr=frame.scope.is_faithful_vvr,
        point=point,
        standard_error=standard_error,
        strata=strata,
        population=frame.size,
        disagreement_rate=disagreement_rate,
        validity=validity,
        allocation_method=allocation_method,
    )


def bootstrap_check(
    estimate: VvrEstimate,
    calls: Mapping[RiskBand, NDArray[np.bool_]],
    *,
    rng: np.random.Generator,
    replicates: int = 2000,
) -> BootstrapCheck:
    """Percentile interval from a stratified nonparametric bootstrap.

    Resamples within each stratum with replacement, which is what makes it an
    independent check on the analytic formula rather than a restatement of it.
    """
    if replicates < 2:
        raise ValueError(f"a bootstrap needs at least two replicates; got {replicates}")

    weights = {
        stratum.band: stratum.population / estimate.population for stratum in estimate.strata
    }
    drawn = [stratum for stratum in estimate.strata if stratum.sampled > 0]

    replicate_points = np.empty(replicates, dtype=np.float64)
    for index in range(replicates):
        total = 0.0
        for stratum in drawn:
            observations = calls[stratum.band]
            picks = rng.integers(0, observations.size, size=observations.size)
            total += (
                weights[stratum.band]
                * float(np.count_nonzero(observations[picks]))
                / (observations.size)
            )
        replicate_points[index] = total

    lower, upper = (float(value) for value in np.percentile(replicate_points, [2.5, 97.5]))
    fraction = estimate.sampled / estimate.population
    return BootstrapCheck(
        replicates=replicates,
        lower=lower,
        upper=upper,
        analytic_half_width=estimate.half_width,
        expected_ratio=1.0 / math.sqrt(1.0 - fraction) if fraction < 1.0 else math.inf,
        applicable=estimate.half_width > 0.0 and estimate.point > 0.0,
    )


def _rates_from_calls(
    sample: Mapping[RiskBand, NDArray[np.int_]],
    calls: Mapping[RiskBand, NDArray[np.bool_]],
) -> dict[RiskBand, float]:
    return {
        band: (float(np.count_nonzero(calls[band])) / sample[band].size)
        if sample[band].size
        else 0.0
        for band in RiskBand
    }


def _review_sample(
    frame: ViewFrame,
    sample: Mapping[RiskBand, NDArray[np.int_]],
    panel: RaterPanel,
    *,
    rng: np.random.Generator,
) -> tuple[dict[RiskBand, NDArray[np.bool_]], float]:
    """Send each stratum's sampled views for review, in a fixed band order."""
    truth = np.asarray(frame.violative, dtype=np.bool_)
    calls: dict[RiskBand, NDArray[np.bool_]] = {}
    split = 0
    total = 0
    for band in RiskBand:
        indices = sample[band]
        verdicts = panel.review(truth[indices], rng=rng)
        calls[band] = verdicts.violative
        split += int(round(verdicts.disagreement_rate * verdicts.size))
        total += verdicts.size
    return calls, (split / total if total else 0.0)


def measure_vvr(
    frame: ViewFrame,
    panel: RaterPanel,
    *,
    seed: int,
    sample_size: int,
    pilot_size: int = 0,
    replicates: int = 2000,
    minimum_per_stratum: int = MINIMUM_PER_STRATUM,
) -> tuple[VvrEstimate, BootstrapCheck]:
    """Run one full measurement: pilot, allocate, sample, review, estimate.

    ``pilot_size`` of zero skips the pilot and allocates proportionally, which is
    the honest default when there is no prior at all. Any positive pilot is drawn
    and rated first, its rater-derived rates seed the optimal allocation, and it
    is then discarded so it cannot contribute to the estimate it shaped.

    The random streams are named children of one root, so changing the bootstrap
    replicate count cannot perturb the drawn sample. That property has its own
    test, because losing it would make two runs at one seed disagree for a reason
    invisible in the output.
    """
    if sample_size <= 0:
        raise ValueError(f"sample_size must be positive; got {sample_size}")
    if pilot_size < 0:
        raise ValueError(f"pilot_size cannot be negative; got {pilot_size}")

    streams = np.random.default_rng(seed).spawn(5)
    pilot_sample_rng, pilot_review_rng, sample_rng, review_rng, bootstrap_rng = streams

    sizes = frame.stratum_sizes()
    if pilot_size:
        pilot_allocation = allocate_proportional(
            stratum_sizes=sizes, total=pilot_size, minimum_per_stratum=minimum_per_stratum
        )
        pilot_draw = draw_stratified_sample(frame, pilot_allocation, rng=pilot_sample_rng)
        pilot_calls, _ = _review_sample(frame, pilot_draw, panel, rng=pilot_review_rng)
        allocation = allocate_optimal(
            stratum_sizes=sizes,
            prior_rates=_rates_from_calls(pilot_draw, pilot_calls),
            total=sample_size,
            minimum_per_stratum=minimum_per_stratum,
        )
    else:
        allocation = allocate_proportional(
            stratum_sizes=sizes, total=sample_size, minimum_per_stratum=minimum_per_stratum
        )

    sample = draw_stratified_sample(frame, allocation, rng=sample_rng)
    calls, disagreement = _review_sample(frame, sample, panel, rng=review_rng)
    estimate = estimate_from_calls(
        frame,
        sample,
        calls,
        allocation_method=allocation.method,
        disagreement_rate=disagreement,
    )
    return estimate, bootstrap_check(estimate, calls, rng=bootstrap_rng, replicates=replicates)
