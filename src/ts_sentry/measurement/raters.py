# SPDX-License-Identifier: MIT
"""STEP-07 D1: simulated review, as a confusion matrix per rater.

STEP-07 3.1 requires "disagreement modeled as a confusion matrix per simulated
rater". This module is that, and it stands where YouTube's human reviewers stand
in the published method:

    "The videos in that sample are then sent for review, and our teams determine
    whether each video does or does not violate our community guidelines."
    - https://support.google.com/transparencyreport/answer/9209072

Why this is a superset of the published method, and labelled as one
-------------------------------------------------------------------
YouTube's headline interval does not model rater error at all. The help centre
says so in one sentence:

    "The confidence intervals do not take into account rater quality, which may
    impact our measurements."

The independent assessment stops at the same line. Barnett's footnote 5 lists
what he did not evaluate, and "the quality of the human rater reviews" is on it;
his section IV discusses the two error kinds qualitatively and concludes only
that it is "unlikely that either... are common".

So the modelling here goes **beyond** what either source claims, deliberately,
because STEP-07 3.1 asks for it. The rule that keeps that honest is enforced in
``vvr``, not here: the headline estimate and its interval are computed from
rater *decisions* exactly as YouTube computes them from reviewer decisions, and
the effect of rater quality appears only as a separate D2 bias curve. Widening
the interval to absorb rater error would be an improvement on the method, and
this phase replicates the method rather than improving it.

Determinism
-----------
One uniform draw per (item, rater), taken as a fixed-shape matrix before any
truth value is consulted. That ordering matters: if the number of draws depended
on how many items were truly violative, changing a label would shift the random
stream and two runs of the same seed would disagree for a reason no reader could
see.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

__all__ = [
    "PERFECT_RATER",
    "PanelVerdicts",
    "RaterPanel",
    "RaterProfile",
    "perfect_panel",
    "uniform_panel",
]


@dataclass(frozen=True, slots=True)
class RaterProfile:
    """One simulated reviewer, as the two rates that define a 2x2 confusion
    matrix over a binary judgement.

    ``sensitivity`` is P(calls it violative | it is violative); ``specificity``
    is P(calls it fine | it is fine). Stated as the two conditional rates rather
    than as an accuracy, because a single accuracy number cannot distinguish the
    two error directions and they do very different things to a rare-event
    estimate: at a true rate near 0.1%, a small loss of specificity swamps the
    signal entirely while a large loss of sensitivity barely moves it.
    """

    rater_id: str
    sensitivity: float
    specificity: float

    def __post_init__(self) -> None:
        if not self.rater_id.strip():
            raise ValueError("a rater needs an id; it appears in disagreement reporting")
        for name in ("sensitivity", "specificity"):
            value: float = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} is a probability and must lie in [0, 1]; got {value}")

    @property
    def false_positive_rate(self) -> float:
        """P(calls it violative | it is fine). The error that dominates here."""
        return 1.0 - self.specificity

    @property
    def false_negative_rate(self) -> float:
        """P(calls it fine | it is violative)."""
        return 1.0 - self.sensitivity

    def confusion_matrix(self) -> tuple[tuple[float, float], tuple[float, float]]:
        """Rows are truth (fine, violative); columns are the call (fine, violative).

        Returned in full even though two numbers determine it, because 3.1 names
        a confusion matrix and a reader checking the model against that wording
        should not have to reconstruct it.
        """
        return (
            (self.specificity, self.false_positive_rate),
            (self.false_negative_rate, self.sensitivity),
        )


PERFECT_RATER = RaterProfile(rater_id="perfect", sensitivity=1.0, specificity=1.0)
"""A reviewer who is never wrong.

Not a realistic profile. It exists so the estimator can be checked against an
identity: with a census and perfect raters, the estimate must equal the true
rate exactly and the interval must have zero width. That is the test that proves
the arithmetic, and it needs an error-free rater to isolate the arithmetic from
the error model.
"""


@dataclass(frozen=True, slots=True)
class PanelVerdicts:
    """What a panel returned on one sample, plus how much it disagreed."""

    violative: NDArray[np.bool_]
    votes: NDArray[np.int_]
    panel_size: int

    @property
    def size(self) -> int:
        return int(self.violative.size)

    @property
    def disagreement_rate(self) -> float:
        """Share of items where the panel was not unanimous.

        Reported in its own right because it is the visible symptom of rater
        error. An estimate produced by a panel that never disagreed and one
        produced by a panel that split on a third of the sample deserve
        different amounts of trust, and the interval alone will not say so:
        the interval is computed from the aggregated calls and cannot see the
        split behind them.
        """
        if self.size == 0:
            return 0.0
        split = np.logical_and(self.votes > 0, self.votes < self.panel_size)
        return float(np.count_nonzero(split)) / self.size


@dataclass(frozen=True, slots=True)
class RaterPanel:
    """A panel of reviewers and the rule that turns their votes into a call."""

    profiles: tuple[RaterProfile, ...]

    def __post_init__(self) -> None:
        if not self.profiles:
            raise ValueError("a panel needs at least one rater")
        ids = [profile.rater_id for profile in self.profiles]
        if len(set(ids)) != len(ids):
            raise ValueError(f"rater ids must be distinct; got {sorted(ids)}")

    @property
    def size(self) -> int:
        return len(self.profiles)

    def review(
        self,
        truth: Sequence[bool] | NDArray[np.bool_],
        *,
        rng: np.random.Generator,
    ) -> PanelVerdicts:
        """Send a sample for review and aggregate the panel by majority.

        A tie on an even-sized panel resolves to **not violative**. That is a
        choice and it is the conservative one: it mirrors a review process that
        removes content only on an affirmative finding, so an evenly split panel
        leaves the content up. It also biases the estimate downward whenever the
        panel is even, which is a good enough reason to prefer odd panels and
        the reason the default below is three.
        """
        truth_array = np.asarray(truth, dtype=np.bool_)
        if truth_array.ndim != 1:
            raise ValueError(f"truth must be one-dimensional; got shape {truth_array.shape}")

        # Fixed shape, drawn before any truth value is read. See the module
        # docstring: making the draw count depend on the labels would couple the
        # random stream to ground truth.
        draws = rng.random((truth_array.size, self.size))

        thresholds = np.empty((truth_array.size, self.size), dtype=np.float64)
        for index, profile in enumerate(self.profiles):
            thresholds[:, index] = np.where(
                truth_array, profile.sensitivity, profile.false_positive_rate
            )

        calls = draws < thresholds
        votes = calls.sum(axis=1).astype(np.int_)
        return PanelVerdicts(
            violative=votes * 2 > self.size,
            votes=votes,
            panel_size=self.size,
        )


def perfect_panel(size: int = 1) -> RaterPanel:
    """A panel that always returns ground truth, for the exactness checks."""
    if size <= 0:
        raise ValueError(f"panel size must be positive; got {size}")
    return RaterPanel(
        profiles=tuple(
            RaterProfile(
                rater_id=f"{PERFECT_RATER.rater_id}-{index}",
                sensitivity=PERFECT_RATER.sensitivity,
                specificity=PERFECT_RATER.specificity,
            )
            for index in range(size)
        )
    )


def uniform_panel(
    size: int,
    *,
    sensitivity: float,
    specificity: float,
) -> RaterPanel:
    """A panel of identically-skilled raters.

    The shape the D2 bias curve sweeps: holding the panel uniform means the
    curve has one axis per error direction rather than one per rater, and the
    resulting bias is attributable to the rates rather than to a mixture.
    """
    if size <= 0:
        raise ValueError(f"panel size must be positive; got {size}")
    return RaterPanel(
        profiles=tuple(
            RaterProfile(
                rater_id=f"rater-{index}",
                sensitivity=sensitivity,
                specificity=specificity,
            )
            for index in range(size)
        )
    )
