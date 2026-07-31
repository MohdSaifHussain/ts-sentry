# SPDX-License-Identifier: MIT
"""D3 aggregator: runs all seven threat generators against one base
population and produces the full sealed-label set (BENIGN for every
untouched base entity, plus every planted threat label).
"""

from dataclasses import dataclass

import numpy as np

from ts_sentry.data.enums import EntityKind, ThreatClass
from ts_sentry.data.population import BasePopulation
from ts_sentry.data.sealed import SealedLabel
from ts_sentry.data.threats import (
    t01_comment_spam_ring as t01,
)
from ts_sentry.data.threats import (
    t02_fake_engagement_network as t02,
)
from ts_sentry.data.threats import (
    t03_off_platform_diversion as t03,
)
from ts_sentry.data.threats import (
    t04_undisclosed_synthetic_media as t04,
)
from ts_sentry.data.threats import (
    t05_ai_persona_authority as t05,
)
from ts_sentry.data.threats import (
    t06_slop_farm as t06,
)
from ts_sentry.data.threats import (
    t07_coordinated_influence_op as t07,
)
from ts_sentry.data.threats.common import (
    DEFAULT_TOTAL_ABUSE_FRACTION,
    NUM_THREAT_CLASSES,
    PlantedResult,
    compute_class_budget,
    make_label,
    merge_planted,
)


@dataclass(frozen=True, slots=True)
class ThreatPlan:
    """Tunable knob on the overall abuse budget; per-class defaults live in
    each ``t0N`` module."""

    total_abuse_fraction: float = DEFAULT_TOTAL_ABUSE_FRACTION


_DEFAULT_PLAN = ThreatPlan()


def _labelable_base_count(base_population: BasePopulation) -> int:
    return (
        len(base_population.accounts)
        + len(base_population.channels)
        + len(base_population.videos)
        + len(base_population.comments)
    )


def label_base_population_benign(
    rng: np.random.Generator, base_population: BasePopulation
) -> tuple[SealedLabel, ...]:
    """Every base-population entity gets exactly one BENIGN label - this is
    what makes the label-completeness hypothesis property (STEP-01 3.5)
    checkable: every entity is labeled, whether abusive or not.
    """
    labels = []
    for account in base_population.accounts:
        labels.append(
            make_label(
                rng, EntityKind.ACCOUNT, account.account_id, ThreatClass.BENIGN, None, "base"
            )
        )
    for channel in base_population.channels:
        labels.append(
            make_label(
                rng, EntityKind.CHANNEL, channel.channel_id, ThreatClass.BENIGN, None, "base"
            )
        )
    for video in base_population.videos:
        labels.append(
            make_label(rng, EntityKind.VIDEO, video.video_id, ThreatClass.BENIGN, None, "base")
        )
    for comment in base_population.comments:
        labels.append(
            make_label(
                rng, EntityKind.COMMENT, comment.comment_id, ThreatClass.BENIGN, None, "base"
            )
        )
    return tuple(labels)


def plant_all_threats(
    rng: np.random.Generator, base_population: BasePopulation, plan: ThreatPlan = _DEFAULT_PLAN
) -> PlantedResult:
    """Run every T-01..T-07 generator and merge their output, plus BENIGN
    labels for the untouched base population, into one :class:`PlantedResult`.
    """
    budget = compute_class_budget(
        _labelable_base_count(base_population), plan.total_abuse_fraction, NUM_THREAT_CLASSES
    )

    planted = merge_planted(
        (
            t01.plant(rng, base_population.videos, budget),
            t02.plant(rng, budget),
            t03.plant(rng, base_population.videos, budget),
            t04.plant(rng, budget),
            t05.plant(rng, budget),
            t06.plant(rng, budget),
            t07.plant(rng, budget),
        )
    )
    benign_labels = label_base_population_benign(rng, base_population)
    return PlantedResult(
        accounts=planted.accounts,
        channels=planted.channels,
        videos=planted.videos,
        comments=planted.comments,
        engagement_events=planted.engagement_events,
        infra_hints=planted.infra_hints,
        sealed_labels=planted.sealed_labels + benign_labels,
    )
