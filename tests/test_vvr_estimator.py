# SPDX-License-Identifier: MIT
"""STEP-07 D1: the stratified estimator, its validity check and the bootstrap.

The two tests worth reading first are the external ones. Barnett's published
Table 2B allocation and its published standard error are reproduced from his own
inputs, which checks this arithmetic against a number an outside statistician
published rather than against our own expectation of what it should be. Every
other test in this file could pass with a subtly wrong formula; those two could
not.

The other load-bearing test is the one showing the 95% interval failing to cover
the truth once raters make mistakes. That is not a defect. It is the published
method's own stated limitation, reproduced deliberately.
"""

from collections.abc import Iterator

import duckdb
import numpy as np
import pytest

from ts_sentry.data.generator import build_dataset
from ts_sentry.data.population import BuildConfig
from ts_sentry.data.store import persist_dataset
from ts_sentry.measurement.frame import (
    ARM_B_COMMENT_ATTRIBUTION,
    RiskBand,
    ViewFrame,
    build_view_frame,
)
from ts_sentry.measurement.raters import perfect_panel, uniform_panel
from ts_sentry.measurement.vvr import (
    MINIMUM_PER_STRATUM,
    allocate_optimal,
    allocate_proportional,
    draw_stratified_sample,
    estimate_from_calls,
    measure_vvr,
    stratified_standard_error,
)

BANDS = list(RiskBand)

# Barnett, Table 2A: a hypothetical population "similar to the one YouTube
# actually faces", five strata holding 80/10/5/1/4 percent of views at violative
# rates of 0.05/0.50/1.0/5.0/0.25 percent, overall 0.20%. Scaled to a million
# views so the shares are exact integers. His fifth stratum is "no score
# available", which is why the mapping onto RiskBand runs in that order.
BARNETT_SIZES = dict(zip(BANDS, [800_000, 100_000, 50_000, 10_000, 40_000], strict=True))
BARNETT_RATES = dict(zip(BANDS, [0.0005, 0.005, 0.010, 0.050, 0.0025], strict=True))
BARNETT_SAMPLE = 4000
BARNETT_TABLE_2B = [2098, 828, 584, 256, 234]


@pytest.fixture(scope="module")
def dataset() -> Iterator[duckdb.DuckDBPyConnection]:
    con = duckdb.connect()
    persist_dataset(con, build_dataset(BuildConfig(seed=42, scale=1)))
    yield con
    con.close()


@pytest.fixture(scope="module")
def frame(dataset: duckdb.DuckDBPyConnection) -> ViewFrame:
    return build_view_frame(dataset)


def test_optimal_allocation_reproduces_barnetts_published_table() -> None:
    """The external reference case, on the source's own inputs.

    Barnett's Table 2B gives "YouTube Optimal Sample Sizes" of 2098, 828, 584,
    256 and 234 for a 4,000-view daily sample. Reproducing all five exactly from
    his population shares and violative rates is evidence that the allocator
    implements the method the source describes, and not merely something that
    looks like it.

    This also pins the rounding rule. Largest remainder is what lands on his
    numbers; naive rounding does not sum to 4,000 at all.
    """
    allocation = allocate_optimal(
        stratum_sizes=BARNETT_SIZES, prior_rates=BARNETT_RATES, total=BARNETT_SAMPLE
    )

    assert [allocation.per_stratum[band] for band in BANDS] == BARNETT_TABLE_2B
    assert allocation.total == BARNETT_SAMPLE


def test_the_allocation_shares_match_barnetts_published_percentages() -> None:
    """The same table read the way his prose reads it: the lowest-risk stratum
    holds 80% of the population and receives "only about half the sampled
    views", while the 1% highest-risk stratum receives a disproportionate
    share."""
    shares = allocate_optimal(
        stratum_sizes=BARNETT_SIZES, prior_rates=BARNETT_RATES, total=BARNETT_SAMPLE
    ).shares()

    assert shares[RiskBand.LOWEST] == pytest.approx(0.525, abs=0.001)
    assert shares[RiskBand.LOW] == pytest.approx(0.207, abs=0.001)
    assert shares[RiskBand.MIDDLE] == pytest.approx(0.146, abs=0.001)
    assert shares[RiskBand.HIGHEST] == pytest.approx(0.064, abs=0.001)
    assert shares[RiskBand.NO_SCORE] == pytest.approx(0.059, abs=0.001)

    assert shares[RiskBand.LOWEST] < 0.80, "the lowest-risk stratum must be under-sampled"
    assert shares[RiskBand.HIGHEST] > 0.01, "the highest-risk stratum must be over-sampled"


def test_the_variance_formula_reproduces_barnetts_published_standard_error() -> None:
    """Barnett's Table 2B: "Expected Standard Deviation 0.054 percentage points".

    Checks the variance formula itself against a published number, independently
    of the allocator that produced the sample sizes. A wrong finite population
    correction, a wrong weight, or an ``n_h`` where ``n_h - 1`` belongs would all
    miss this.
    """
    allocation = allocate_optimal(
        stratum_sizes=BARNETT_SIZES, prior_rates=BARNETT_RATES, total=BARNETT_SAMPLE
    )

    standard_error = stratified_standard_error(
        stratum_sizes=BARNETT_SIZES,
        sample_sizes=allocation.per_stratum,
        rates=BARNETT_RATES,
    )

    assert 100 * standard_error == pytest.approx(0.054, abs=0.0005)


def test_optimal_allocation_is_worth_a_third_of_the_interval() -> None:
    """Why the allocation is the part that matters.

    On Barnett's own population, optimal allocation gives 0.054 percentage
    points and proportional gives 0.070. Stratifying proportionally instead of
    not stratifying at all is worth almost nothing by comparison, which is the
    finding that makes proportional-only allocation the wrong default for a
    rare-event estimand.
    """
    optimal = allocate_optimal(
        stratum_sizes=BARNETT_SIZES, prior_rates=BARNETT_RATES, total=BARNETT_SAMPLE
    )
    proportional = allocate_proportional(stratum_sizes=BARNETT_SIZES, total=BARNETT_SAMPLE)

    optimal_se = stratified_standard_error(
        stratum_sizes=BARNETT_SIZES, sample_sizes=optimal.per_stratum, rates=BARNETT_RATES
    )
    proportional_se = stratified_standard_error(
        stratum_sizes=BARNETT_SIZES, sample_sizes=proportional.per_stratum, rates=BARNETT_RATES
    )

    assert optimal_se < proportional_se
    assert proportional_se / optimal_se == pytest.approx(1.30, abs=0.02)


def test_proportional_allocation_matches_the_population_shares() -> None:
    allocation = allocate_proportional(stratum_sizes=BARNETT_SIZES, total=BARNETT_SAMPLE)

    assert [allocation.per_stratum[band] for band in BANDS] == [3200, 400, 200, 40, 160]


def test_an_uninformative_prior_degenerates_to_proportional() -> None:
    """The documented consequence of the prior floor.

    A pilot that finds nothing anywhere gives every stratum the same floored
    rate, the Neyman keys become proportional to ``N_h``, and the allocation is
    the proportional one. That is the correct behaviour rather than a fallback,
    and it is the common case on this corpus.
    """
    flat = allocate_optimal(
        stratum_sizes=BARNETT_SIZES,
        prior_rates=dict.fromkeys(BANDS, 0.0),
        total=BARNETT_SAMPLE,
    )
    proportional = allocate_proportional(stratum_sizes=BARNETT_SIZES, total=BARNETT_SAMPLE)

    assert flat.per_stratum == proportional.per_stratum


def test_allocation_respects_capacity_and_the_two_view_minimum() -> None:
    sizes = dict(zip(BANDS, [10, 3, 0, 0, 0], strict=True))

    allocation = allocate_optimal(
        stratum_sizes=sizes,
        prior_rates={RiskBand.LOWEST: 0.5, RiskBand.LOW: 0.0},
        total=12,
    )

    assert allocation.total == 12
    assert allocation.per_stratum[RiskBand.LOWEST] <= 10
    assert allocation.per_stratum[RiskBand.LOW] >= MINIMUM_PER_STRATUM
    for band in (RiskBand.MIDDLE, RiskBand.HIGHEST, RiskBand.NO_SCORE):
        assert allocation.per_stratum[band] == 0, "an empty stratum cannot be sampled"


def test_allocation_refuses_the_impossible() -> None:
    sizes = dict(zip(BANDS, [10, 10, 0, 0, 0], strict=True))

    with pytest.raises(ValueError, match="population"):
        allocate_proportional(stratum_sizes=sizes, total=21)
    with pytest.raises(ValueError, match="cannot give"):
        allocate_proportional(stratum_sizes=sizes, total=3, minimum_per_stratum=2)
    with pytest.raises(ValueError, match="every stratum is empty"):
        allocate_proportional(stratum_sizes=dict.fromkeys(BANDS, 0), total=1)
    with pytest.raises(ValueError, match="rate_floor"):
        allocate_optimal(stratum_sizes=sizes, prior_rates={}, total=4, rate_floor=0.9)


def test_the_allocator_takes_numbers_and_nothing_else() -> None:
    """The governance property behind the pilot design.

    Optimal allocation needs a prior ``p_h``. If that could come from
    ``sealed._labels`` the sample would be optimised against answers the method
    does not have, and the resulting interval would describe a measurement
    nobody could reproduce without the labels. Asserted over the signature: two
    mappings of numbers in, an allocation out.
    """
    import inspect

    parameters = inspect.signature(allocate_optimal).parameters

    assert set(parameters) == {
        "stratum_sizes",
        "prior_rates",
        "total",
        "minimum_per_stratum",
        "rate_floor",
    }
    for name, parameter in parameters.items():
        rendered = str(parameter.annotation)
        for forbidden in ("ViewFrame", "duckdb", "ThreatClass", "Connection"):
            assert forbidden not in rendered, f"{name} can reach {forbidden}"


def test_labels_cannot_change_the_allocation(frame: ViewFrame) -> None:
    """The same property from the other side, behaviourally.

    Two frames differing only in their scope rule have different ground truth
    and identical stratum sizes. An allocation seeded from a fixed prior must
    come out identical, because the allocator never sees the difference.
    """
    other = ViewFrame(
        view_ids=frame.view_ids,
        video_ids=frame.video_ids,
        bands=frame.bands,
        violative=tuple(not flag for flag in frame.violative),
        scope=frame.scope,
        sampling_instant_ms=frame.sampling_instant_ms,
    )

    prior = dict.fromkeys(BANDS, 0.01)
    first = allocate_optimal(stratum_sizes=frame.stratum_sizes(), prior_rates=prior, total=2000)
    second = allocate_optimal(stratum_sizes=other.stratum_sizes(), prior_rates=prior, total=2000)

    assert first.per_stratum == second.per_stratum


def test_a_census_with_perfect_raters_recovers_the_truth(frame: ViewFrame) -> None:
    """The exactness identity, asserted where it is actually exact.

    The *counts* match bit for bit: a census reviewed by an infallible panel
    calls exactly the views that are violative, no more and no fewer. The rate
    matches to floating-point precision rather than bitwise, because the
    stratified sum reassociates the same division differently from a single
    division over the whole frame. Asserting bitwise equality on the rate would
    be asserting a property of IEEE addition order, not of this estimator.

    The standard error is exactly zero, and that is bitwise: at a census every
    finite population correction is ``1 - N_h/N_h``.
    """
    estimate, _ = measure_vvr(
        frame, perfect_panel(1), seed=1, sample_size=frame.size, replicates=10
    )

    assert sum(stratum.violative_calls for stratum in estimate.strata) == sum(frame.violative)
    assert estimate.sampled == frame.size
    assert estimate.point == pytest.approx(frame.true_vvr(), rel=1e-12)
    assert estimate.standard_error == 0.0
    assert estimate.lower == estimate.upper


def test_the_interval_narrows_as_the_sample_grows(frame: ViewFrame) -> None:
    """Monotone in ``n``, and zero at a census.

    Uses the analytic width at fixed stratum rates rather than drawn samples, so
    the curve is the estimator's behaviour rather than one seed's luck.
    """
    sizes = frame.stratum_sizes()
    rates = frame.true_stratum_rates()

    widths = []
    for total in (500, 2000, 9000, frame.size):
        allocation = allocate_proportional(stratum_sizes=sizes, total=total)
        widths.append(
            stratified_standard_error(
                stratum_sizes=sizes, sample_sizes=allocation.per_stratum, rates=rates
            )
        )

    assert widths == sorted(widths, reverse=True)
    assert widths[-1] == 0.0, "the finite population correction must vanish at a census"


def test_the_validity_conditions_fail_at_a_realistic_sample_and_say_so(
    frame: ViewFrame,
) -> None:
    """The finding, pinned as a test rather than left in prose.

    At this corpus's rate a two-thousand-view sample expects around two
    violative calls, far below the ten the normal approximation wants, and the
    interval clips at zero. The estimator reports that instead of presenting a
    confident-looking interval, which is the whole reason 3.1 asks for
    documented validity conditions and a bootstrap cross-check.
    """
    estimate, _ = measure_vvr(
        frame, perfect_panel(3), seed=42, sample_size=2000, pilot_size=500, replicates=10
    )

    assert not estimate.validity.holds
    assert estimate.validity.successes < estimate.validity.success_threshold
    assert "FAIL" in estimate.validity.render()


def test_the_bootstrap_is_wider_than_the_analytic_interval_and_by_how_much(
    dataset: duckdb.DuckDBPyConnection,
) -> None:
    """Direction first, magnitude second.

    Resampling with replacement inside a stratum throws away the finite
    population correction, so the bootstrap interval must come out wider by
    roughly ``1 / sqrt(1 - f)``. Run on arm B, whose rate is high enough that
    both intervals are non-degenerate; on the baseline estimand the point
    estimate is usually zero and the comparison has nothing to compare.
    """
    arm_b = build_view_frame(dataset, scope=ARM_B_COMMENT_ATTRIBUTION)

    estimate, bootstrap = measure_vvr(
        arm_b, perfect_panel(3), seed=42, sample_size=3000, pilot_size=1000, replicates=2000
    )

    assert bootstrap.applicable
    assert bootstrap.half_width > estimate.half_width
    assert bootstrap.expected_ratio == pytest.approx(1.0909, abs=0.001)
    assert bootstrap.agrees(tolerance=0.15)


def test_the_bootstrap_reports_itself_inapplicable_rather_than_agreeing(
    frame: ViewFrame,
) -> None:
    """A degenerate comparison must not be counted as a passing one.

    When the sample finds nothing, both intervals collapse to a point and their
    ratio is undefined. The check says so rather than returning agreement,
    because "0 == 0" is not evidence that two methods concur.
    """
    estimate, bootstrap = measure_vvr(
        frame,
        uniform_panel(3, sensitivity=0.0, specificity=1.0),
        seed=3,
        sample_size=1000,
        replicates=200,
    )

    assert estimate.point == 0.0
    assert not bootstrap.applicable
    assert np.isnan(bootstrap.width_ratio)


def test_rater_error_moves_the_estimate_out_of_its_own_interval(
    frame: ViewFrame,
) -> None:
    """The published limitation, reproduced on purpose.

    "The confidence intervals do not take into account rater quality." This is
    what that sentence costs. A single reviewer with 99% specificity calls one
    benign view in a hundred violative, which at a true rate near 0.1% swamps
    the signal by an order of magnitude. The estimate moves; the interval does
    not widen to cover the move; and the truth ends up outside a nominally 95%
    interval.

    The interval is not broken. It is measuring sampling error, faithfully, and
    sampling error is not the dominant error here. Reporting it as though it
    covered rater error is the mistake this test exists to make visible.

    A single rater, because that is the configuration the published wording
    describes: a sampled video "sent for review" and a team member determining
    whether it violates. It is also the configuration where the effect is
    undiluted; the panel test below covers what majority voting does to it.
    """
    truth = frame.true_vvr()

    honest, _ = measure_vvr(
        frame, perfect_panel(1), seed=11, sample_size=9000, pilot_size=1000, replicates=10
    )
    biased, _ = measure_vvr(
        frame,
        uniform_panel(1, sensitivity=1.0, specificity=0.99),
        seed=11,
        sample_size=9000,
        pilot_size=1000,
        replicates=10,
    )

    assert honest.lower <= truth <= honest.upper
    assert biased.point > 5 * truth
    assert biased.lower > truth, "the biased interval must fail to cover the truth"


def test_a_majority_panel_suppresses_independent_rater_error(frame: ViewFrame) -> None:
    """Found by running the estimator, not by reasoning about it beforehand.

    A three-rater majority needs *two* independent errors to flip a call, so a
    per-rater false-positive rate of 1% becomes an effective panel rate near
    3 * 0.01^2 = 0.0003. The suppression is quadratic, and it is large enough
    that the same 99% specificity which puts the truth well outside the interval
    with one reviewer leaves it comfortably inside with three.

    Worth its own test because it cuts both ways. Panels buy real robustness
    against independent error, and they buy nothing at all against *correlated*
    error such as a mistaken policy interpretation everyone on the panel shares.
    Nothing here models correlated rater error, and the D2 bias curve inherits
    that limit.
    """
    single, _ = measure_vvr(
        frame,
        uniform_panel(1, sensitivity=1.0, specificity=0.99),
        seed=11,
        sample_size=9000,
        pilot_size=1000,
        replicates=10,
    )
    panel, _ = measure_vvr(
        frame,
        uniform_panel(3, sensitivity=1.0, specificity=0.99),
        seed=11,
        sample_size=9000,
        pilot_size=1000,
        replicates=10,
    )
    truth = frame.true_vvr()

    assert panel.point < single.point / 5
    assert panel.lower <= truth <= panel.upper
    assert not (single.lower <= truth <= single.upper)


def test_the_estimate_is_computed_from_calls_and_never_from_truth(
    frame: ViewFrame,
) -> None:
    """Two frames with opposite ground truth and identical reviewer calls must
    give the identical estimate.

    If the estimator peeked at ``frame.violative`` anywhere, this would fail.
    """
    allocation = allocate_proportional(stratum_sizes=frame.stratum_sizes(), total=600)
    sample = draw_stratified_sample(frame, allocation, rng=np.random.default_rng(5))
    calls = {band: np.zeros(indices.size, dtype=np.bool_) for band, indices in sample.items()}
    for band, indices in sample.items():
        if indices.size:
            calls[band][0] = True

    inverted = ViewFrame(
        view_ids=frame.view_ids,
        video_ids=frame.video_ids,
        bands=frame.bands,
        violative=tuple(not flag for flag in frame.violative),
        scope=frame.scope,
        sampling_instant_ms=frame.sampling_instant_ms,
    )

    first = estimate_from_calls(
        frame, sample, calls, allocation_method="proportional", disagreement_rate=0.0
    )
    second = estimate_from_calls(
        inverted, sample, calls, allocation_method="proportional", disagreement_rate=0.0
    )

    assert first.point == second.point
    assert first.standard_error == second.standard_error


def test_a_fixed_seed_reproduces_the_whole_measurement(frame: ViewFrame) -> None:
    first, first_boot = measure_vvr(
        frame,
        uniform_panel(3, sensitivity=0.9, specificity=0.999),
        seed=99,
        sample_size=4000,
        pilot_size=800,
        replicates=200,
    )
    second, second_boot = measure_vvr(
        frame,
        uniform_panel(3, sensitivity=0.9, specificity=0.999),
        seed=99,
        sample_size=4000,
        pilot_size=800,
        replicates=200,
    )

    assert first.point == second.point
    assert first.standard_error == second.standard_error
    assert (first_boot.lower, first_boot.upper) == (second_boot.lower, second_boot.upper)


def test_changing_the_replicate_count_leaves_the_sample_untouched(
    frame: ViewFrame,
) -> None:
    """What the named child streams buy.

    The bootstrap draws from its own stream, so asking for more replicates must
    not reshuffle the sample or the review. Without that, a reader comparing two
    runs at one seed would see the point estimate move and have no way to tell
    why.
    """
    panel = uniform_panel(3, sensitivity=0.9, specificity=0.999)

    few, _ = measure_vvr(frame, panel, seed=7, sample_size=3000, pilot_size=600, replicates=50)
    many, _ = measure_vvr(frame, panel, seed=7, sample_size=3000, pilot_size=600, replicates=5000)

    assert few.point == many.point
    assert few.standard_error == many.standard_error
    assert [s.violative_calls for s in few.strata] == [s.violative_calls for s in many.strata]


def test_the_pilot_does_not_leak_into_the_estimate(frame: ViewFrame) -> None:
    """The pilot changes the allocation and nothing else.

    Asserted by the sample size: the estimate is built from exactly the
    requested views, not from the requested views plus the pilot's.
    """
    estimate, _ = measure_vvr(
        frame, perfect_panel(3), seed=4, sample_size=2500, pilot_size=1000, replicates=10
    )

    assert estimate.sampled == 2500
    assert estimate.allocation_method == "optimal"


def test_measurement_refuses_nonsense_inputs(frame: ViewFrame) -> None:
    for kwargs in ({"sample_size": 0}, {"sample_size": 10, "pilot_size": -1}):
        with pytest.raises(ValueError):
            measure_vvr(frame, perfect_panel(), seed=1, replicates=10, **kwargs)
    with pytest.raises(ValueError, match="at least two replicates"):
        measure_vvr(frame, perfect_panel(), seed=1, sample_size=100, replicates=1)


def test_a_non_faithful_scope_is_labelled_in_its_own_rendering(
    dataset: duckdb.DuckDBPyConnection,
) -> None:
    """A renderer must not be able to print arm B as a VVR."""
    arm_b = build_view_frame(dataset, scope=ARM_B_COMMENT_ATTRIBUTION)

    estimate, _ = measure_vvr(arm_b, perfect_panel(3), seed=2, sample_size=2000, replicates=10)

    assert not estimate.is_faithful_vvr
    assert "NOT a VVR" in estimate.render()
    assert "rater quality is not in it" in estimate.render()


def test_the_standard_error_refuses_an_empty_population() -> None:
    with pytest.raises(ValueError, match="empty population"):
        stratified_standard_error(
            stratum_sizes=dict.fromkeys(BANDS, 0),
            sample_sizes=dict.fromkeys(BANDS, 0),
            rates=dict.fromkeys(BANDS, 0.0),
        )
