# SPDX-License-Identifier: MIT
"""STEP-07 D1: the simulated review panel.

Two properties carry the weight. A perfect panel must return ground truth
unchanged, because every exactness check downstream is built on that. And the
random stream must not depend on the labels, because if it did, a fixed seed
would stop meaning a fixed sample the moment the scope rule changed.
"""

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ts_sentry.measurement.raters import (
    PERFECT_RATER,
    RaterPanel,
    RaterProfile,
    perfect_panel,
    uniform_panel,
)


def _rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


def test_a_perfect_panel_returns_ground_truth_unchanged() -> None:
    """The identity every exactness check downstream rests on."""
    truth = [True, False, True, False, False]

    verdicts = perfect_panel(3).review(truth, rng=_rng())

    assert list(verdicts.violative) == truth
    assert verdicts.disagreement_rate == 0.0


def test_a_useless_panel_never_calls_anything_violative() -> None:
    """Zero sensitivity and perfect specificity is the degenerate reviewer who
    approves everything. The estimate must come out at zero rather than at some
    accidental non-zero value from a mis-signed comparison."""
    panel = uniform_panel(3, sensitivity=0.0, specificity=1.0)

    verdicts = panel.review([True] * 20, rng=_rng())

    assert not verdicts.violative.any()


def test_an_inverted_panel_calls_everything_violative() -> None:
    """The opposite corner, which catches the same sign error from the other
    side: zero specificity means every fine item is called violative."""
    panel = uniform_panel(3, sensitivity=1.0, specificity=0.0)

    verdicts = panel.review([False] * 20, rng=_rng())

    assert verdicts.violative.all()


def test_the_confusion_matrix_is_the_two_rates_laid_out() -> None:
    profile = RaterProfile(rater_id="r", sensitivity=0.9, specificity=0.8)

    assert profile.false_negative_rate == pytest.approx(0.1)
    assert profile.false_positive_rate == pytest.approx(0.2)
    fine_row, violative_row = profile.confusion_matrix()
    assert fine_row == pytest.approx((0.8, 0.2))
    assert violative_row == pytest.approx((0.1, 0.9))
    for row in (fine_row, violative_row):
        assert sum(row) == pytest.approx(1.0)


def test_the_random_stream_does_not_depend_on_the_labels() -> None:
    """The determinism property that is easy to lose and invisible when lost.

    Two samples of the same size but different truth vectors must consume the
    same number of draws, so a generator handed to one is left in the same state
    as a generator handed to the other. If the draw count tracked the labels, a
    scope-rule change would silently reshuffle every later sample and no test
    that only checked one scope would notice.
    """
    panel = uniform_panel(3, sensitivity=0.9, specificity=0.99)

    first, second = _rng(), _rng()
    panel.review([True] * 50, rng=first)
    panel.review([False] * 50, rng=second)

    assert first.random() == second.random()


def test_review_is_reproducible_under_a_fixed_seed() -> None:
    panel = uniform_panel(5, sensitivity=0.85, specificity=0.97)
    truth = [True, False] * 40

    first = panel.review(truth, rng=_rng(7))
    second = panel.review(truth, rng=_rng(7))

    assert np.array_equal(first.violative, second.violative)
    assert np.array_equal(first.votes, second.votes)


def test_an_even_panel_resolves_a_tie_to_not_violative() -> None:
    """The documented tie rule, asserted rather than described.

    A two-rater panel where one rater always says violative and the other never
    does splits on every item, and every item must come back not violative.
    """
    panel = RaterPanel(
        profiles=(
            RaterProfile(rater_id="always", sensitivity=1.0, specificity=0.0),
            RaterProfile(rater_id="never", sensitivity=0.0, specificity=1.0),
        )
    )

    verdicts = panel.review([True, False, True], rng=_rng())

    assert not verdicts.violative.any()
    assert verdicts.disagreement_rate == 1.0


def test_disagreement_is_reported_separately_from_the_verdict() -> None:
    """A split panel and a unanimous one can return the same call.

    Which is the point: the interval is computed from the calls and cannot see
    the split behind them, so the split has to be reported on its own.
    """
    panel = uniform_panel(3, sensitivity=0.5, specificity=0.5)

    verdicts = panel.review([True] * 200, rng=_rng())

    assert 0.0 < verdicts.disagreement_rate <= 1.0
    assert verdicts.size == 200
    assert verdicts.panel_size == 3


@settings(max_examples=50, deadline=None)
@given(
    sensitivity=st.floats(min_value=0.0, max_value=1.0),
    specificity=st.floats(min_value=0.0, max_value=1.0),
    count=st.integers(min_value=1, max_value=200),
    seed=st.integers(min_value=0, max_value=2**32 - 1),
)
def test_votes_never_exceed_the_panel_size(
    sensitivity: float, specificity: float, count: int, seed: int
) -> None:
    """A structural bound over the whole parameter space.

    Cheap to state and it catches the class of bug where an aggregation reduces
    over the wrong axis, which would otherwise surface as a plausible but wrong
    rate rather than as a crash.
    """
    panel = uniform_panel(3, sensitivity=sensitivity, specificity=specificity)

    verdicts = panel.review([True] * count, rng=np.random.default_rng(seed))

    assert verdicts.votes.min() >= 0
    assert verdicts.votes.max() <= panel.size
    assert verdicts.violative.size == count


def test_a_panel_refuses_duplicate_or_missing_raters() -> None:
    with pytest.raises(ValueError, match="at least one rater"):
        RaterPanel(profiles=())
    with pytest.raises(ValueError, match="distinct"):
        RaterPanel(profiles=(PERFECT_RATER, PERFECT_RATER))


def test_a_rater_refuses_impossible_rates() -> None:
    for kwargs in (
        {"sensitivity": 1.5, "specificity": 1.0},
        {"sensitivity": 1.0, "specificity": -0.1},
    ):
        with pytest.raises(ValueError, match="probability"):
            RaterProfile(rater_id="r", **kwargs)
    with pytest.raises(ValueError, match="id"):
        RaterProfile(rater_id=" ", sensitivity=1.0, specificity=1.0)


def test_panel_builders_refuse_a_non_positive_size() -> None:
    for builder in (perfect_panel, lambda n: uniform_panel(n, sensitivity=1.0, specificity=1.0)):
        with pytest.raises(ValueError, match="positive"):
            builder(0)


def test_review_refuses_a_non_vector_truth() -> None:
    with pytest.raises(ValueError, match="one-dimensional"):
        perfect_panel().review(np.array([[True, False]]), rng=_rng())
