# SPDX-License-Identifier: MIT
"""D5: the deterministic priority scorer (STEP-03 3.1, ARCHITECTURE 4.1).

``priority = f(severity_class, spread, velocity, recidivism)`` with published
weights. Every row renders as its components, never as a bare number: an
analyst who cannot see why a case ranked first cannot disagree with the
ranking, and a ranking nobody can disagree with is not decision support.

Why a weighted sum
------------------
Monotonicity in each component is a stated requirement (STEP-03 3.1), and a
weighted sum with positive weights satisfies it by construction rather than by
tuning. Anything cleverer would have to earn the loss of that property, and on
synthetic data with no measured outcome to fit against, there is nothing to
earn it with. The honest description is that this is a *transparent* scorer,
not an accurate one.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "WEIGHTS",
    "WEIGHTS_VERSION",
    "PriorityScore",
    "ScoreComponent",
    "component_id",
    "score",
    "weights_hash",
]

WEIGHTS_VERSION = "1.0.0"
"""SemVer of the weight set. Recorded on every scored queue, so a ranking is
attributable to the weights that produced it rather than to whatever the code
says today."""


class ScoreComponent(StrEnum):
    """The four inputs, named once so nothing spells them differently.

    These names are also the citable evidence ids: a rationale cites
    ``case-0001:velocity`` and the verifier resolves it against this enum.
    """

    SEVERITY_CLASS = "severity_class"
    SPREAD = "spread"
    VELOCITY = "velocity"
    RECIDIVISM = "recidivism"


WEIGHTS: Mapping[ScoreComponent, float] = {
    ScoreComponent.SEVERITY_CLASS: 0.40,
    ScoreComponent.SPREAD: 0.25,
    ScoreComponent.VELOCITY: 0.20,
    ScoreComponent.RECIDIVISM: 0.15,
}
"""Published weights, summing to 1.0 so a priority reads as a 0..1 share.

Every weight is strictly positive, which is what makes the monotonicity
property structural. They are analyst-judgment values, not fitted parameters,
and nothing in this repository claims otherwise.
"""


def weights_hash() -> str:
    """Digest over the version and weights, for the session manifest."""
    from ts_sentry.governance.canonical import digest_fields

    return digest_fields(
        "ts-sentry/triage-weights/v1",
        WEIGHTS_VERSION,
        *(f"{component.value}={WEIGHTS[component]:.6f}" for component in ScoreComponent),
    )


def component_id(case_id: str, component: ScoreComponent) -> str:
    """The citable id for one component of one case.

    Namespaced by case on purpose: an unqualified ``velocity`` would resolve
    against every row, so a rationale could cite another case's evidence and
    still verify.
    """
    return f"{case_id}:{component.value}"


@dataclass(frozen=True, slots=True)
class PriorityScore:
    """One ranked row: the priority and the components it came from."""

    case_id: str
    components: Mapping[ScoreComponent, float]
    priority: float

    def __post_init__(self) -> None:
        missing = sorted(c.value for c in ScoreComponent if c not in self.components)
        if missing:
            raise ValueError(f"score is missing components: {', '.join(missing)}")
        for component, value in self.components.items():
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{component.value} must be 0..1; got {value}")

    @property
    def evidence_ids(self) -> frozenset[str]:
        """Exactly what a rationale for this row may cite."""
        return frozenset(component_id(self.case_id, component) for component in ScoreComponent)

    def to_json_object(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "priority": round(self.priority, 6),
            "weights_version": WEIGHTS_VERSION,
            "components": {
                component.value: round(self.components[component], 6)
                for component in ScoreComponent
            },
        }


def score(
    case_id: str,
    *,
    severity_class: float,
    spread: float,
    velocity: float,
    recidivism: float,
) -> PriorityScore:
    """Combine the four components into a priority.

    Pure and total over its declared domain: no I/O, no clock, no randomness.
    The same components give the same priority forever, which is what lets a
    published ranking be reproduced from a manifest.
    """
    components = {
        ScoreComponent.SEVERITY_CLASS: severity_class,
        ScoreComponent.SPREAD: spread,
        ScoreComponent.VELOCITY: velocity,
        ScoreComponent.RECIDIVISM: recidivism,
    }
    priority = sum(WEIGHTS[component] * value for component, value in components.items())
    return PriorityScore(case_id=case_id, components=components, priority=priority)
