# SPDX-License-Identifier: MIT
"""Population containers shared by the generator and the threat planner.

Pulled out of ``ts_sentry.data.generator`` so ``ts_sentry.data.threats.plan``
can type its ``BasePopulation`` parameter without generator -> threats ->
generator becoming a circular import (``generator.build_dataset`` calls
``threats.plan.plant_all_threats``, so the dependency must run one way).
"""

from dataclasses import dataclass

from ts_sentry.data.schema import AccountMeta, Channel, Comment, EngagementEvent, InfraHint, Video
from ts_sentry.data.sealed import SealedLabel


@dataclass(frozen=True, slots=True)
class BuildConfig:
    """Parameters for one deterministic dataset build."""

    seed: int
    scale: int


@dataclass(frozen=True, slots=True)
class BasePopulation:
    """The benign base population, before threat planting (D3)."""

    accounts: tuple[AccountMeta, ...]
    channels: tuple[Channel, ...]
    infra_hints: tuple[InfraHint, ...]
    videos: tuple[Video, ...]
    comments: tuple[Comment, ...]
    engagement_events: tuple[EngagementEvent, ...]


@dataclass(frozen=True, slots=True)
class Dataset:
    """The full build output: base population + planted threats, merged,
    plus the complete sealed-label set (BENIGN and threat labels alike).
    """

    accounts: tuple[AccountMeta, ...]
    channels: tuple[Channel, ...]
    videos: tuple[Video, ...]
    comments: tuple[Comment, ...]
    engagement_events: tuple[EngagementEvent, ...]
    infra_hints: tuple[InfraHint, ...]
    sealed_labels: tuple[SealedLabel, ...]
