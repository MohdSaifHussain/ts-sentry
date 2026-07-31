# SPDX-License-Identifier: MIT
"""Shared plumbing for the T-01..T-07 threat generators (D3).

Every threat module creates *new* entities layered on top of the benign
base population (never mutates it - the base dataclasses are frozen) and
returns a :class:`PlantedResult`: the new rows plus exactly one
``SealedLabel`` per new account/channel/video/comment it created.

Benign-majority budget (STEP-01 3.4, ARCHITECTURE 6.1: >=97% of entities
benign): only ``EntityKind`` members get ground-truth labels - CHANNEL,
VIDEO, COMMENT, ACCOUNT. Engagement events and infra hints are signals, not
labeled entities, so they don't count against the budget. Each threat class
is handed a ``budget`` (an upper bound on new accounts+channels+videos+
comments combined) computed from the base population size so the invariant
holds structurally, for any seed or scale, rather than by tuning constants
by hand.

Burst shaping (STEP-01 3.4 realism envelope): a documented Poisson-burst
mixture, not a Hawkes process (choice + rationale recorded in
docs/decisions/STEP-01-data-foundation.md Outcome section). Each planted
timestamp is drawn either from a short, coordinated burst window (the
"burst state") or uniformly across the full deterministic window (the
background rate), which is exactly what makes ring activity look
temporally clustered without needing a self-exciting kernel.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from ts_sentry.data.enums import (
    EngagementKind,
    EntityKind,
    InfraSignalKind,
    ProvenanceSignal,
    ThreatClass,
)
from ts_sentry.data.schema import AccountMeta, Channel, Comment, EngagementEvent, InfraHint, Video
from ts_sentry.data.sealed import SealedLabel
from ts_sentry.data.tz import WINDOW_SECONDS, WINDOW_START

DEFAULT_TOTAL_ABUSE_FRACTION = 0.02
"""Target share of labelable entities (accounts+channels+videos+comments)
that are abusive, summed across all seven threat classes. Kept well under
the STEP-01 3.4 floor (benign >= 97%, i.e. abusive <= 3%) so per-class
rounding never risks crossing it."""

NUM_THREAT_CLASSES = 7


@dataclass(frozen=True, slots=True)
class PlantedResult:
    """New rows contributed by one threat generator."""

    accounts: tuple[AccountMeta, ...] = ()
    channels: tuple[Channel, ...] = ()
    videos: tuple[Video, ...] = ()
    comments: tuple[Comment, ...] = ()
    engagement_events: tuple[EngagementEvent, ...] = ()
    infra_hints: tuple[InfraHint, ...] = ()
    sealed_labels: tuple[SealedLabel, ...] = ()

    @property
    def labelable_count(self) -> int:
        return len(self.accounts) + len(self.channels) + len(self.videos) + len(self.comments)


def compute_class_budget(
    labelable_base_count: int,
    total_abuse_fraction: float = DEFAULT_TOTAL_ABUSE_FRACTION,
    num_classes: int = NUM_THREAT_CLASSES,
) -> int:
    """Upper bound on new labelable rows (accounts+channels+videos+comments)
    one threat class may create, so the 7-class total stays within
    ``total_abuse_fraction`` of the base population regardless of scale.
    """
    return max(1, int(labelable_base_count * total_abuse_fraction / num_classes))


def assert_budget_respected(result: PlantedResult, budget: int) -> None:
    if result.labelable_count > budget:
        raise ValueError(
            f"threat generator exceeded its labelable-entity budget: "
            f"{result.labelable_count} > {budget}"
        )


def params_hash(params: object) -> str:
    """Deterministic short hash of a parameter dataclass, for
    ``SealedLabel.generator_params_hash`` traceability."""
    return hashlib.sha256(repr(params).encode("utf-8")).hexdigest()[:16]


def burst_timestamp(
    rng: np.random.Generator,
    burst_start_s: int,
    burst_duration_s: int,
    burst_weight: float,
) -> datetime:
    """One timestamp from the Poisson-burst mixture: with probability
    ``burst_weight`` drawn from the short coordinated burst window, otherwise
    drawn from the background rate (uniform across the full deterministic
    build window).
    """
    if rng.random() < burst_weight:
        offset_s = burst_start_s + int(rng.integers(0, max(burst_duration_s, 1)))
    else:
        offset_s = int(rng.integers(0, WINDOW_SECONDS))
    return WINDOW_START + timedelta(seconds=offset_s)


def make_ring_account(
    rng: np.random.Generator, account_id: str, ip_bucket: str, device_hint: str | None = None
) -> AccountMeta:
    return AccountMeta(
        account_id=account_id,
        created_ts=burst_timestamp(rng, 0, WINDOW_SECONDS, 1.0),
        display_name=f"user{int(rng.integers(0, 10**6))}",
        is_verified=False,
        signup_ip_bucket=ip_bucket,
        device_fingerprint_hint=device_hint,
    )


def make_ring_channel(
    rng: np.random.Generator,
    channel_id: str,
    account_id: str,
    display_name: str,
    description: str,
    burst_start_s: int,
    burst_duration_s: int,
) -> Channel:
    return Channel(
        channel_id=channel_id,
        account_id=account_id,
        created_ts=burst_timestamp(rng, burst_start_s, burst_duration_s, 0.9),
        display_name=display_name,
        subscriber_count=int(rng.integers(0, 500)),
        description=description,
    )


def make_ring_video(
    rng: np.random.Generator,
    video_id: str,
    channel_id: str,
    title: str,
    description: str,
    burst_start_s: int,
    burst_duration_s: int,
    synthetic_media_disclosed: bool,
    provenance_signal: ProvenanceSignal,
) -> Video:
    return Video(
        video_id=video_id,
        channel_id=channel_id,
        title=title,
        description=description,
        published_ts=burst_timestamp(rng, burst_start_s, burst_duration_s, 0.9),
        duration_s=int(rng.integers(30, 1800)),
        synthetic_media_disclosed=synthetic_media_disclosed,
        provenance_signal=provenance_signal,
    )


def make_ring_comment(
    rng: np.random.Generator,
    comment_id: str,
    video_id: str,
    account_id: str,
    text: str,
    template_id: str | None,
    burst_start_s: int,
    burst_duration_s: int,
) -> Comment:
    return Comment(
        comment_id=comment_id,
        video_id=video_id,
        account_id=account_id,
        parent_comment_id=None,
        posted_ts=burst_timestamp(rng, burst_start_s, burst_duration_s, 0.95),
        text=text,
        template_id=template_id,
    )


def make_ring_engagement(
    rng: np.random.Generator,
    event_id: str,
    kind: EngagementKind,
    account_id: str,
    burst_start_s: int,
    burst_duration_s: int,
    video_id: str | None = None,
    channel_id: str | None = None,
) -> EngagementEvent:
    return EngagementEvent(
        event_id=event_id,
        kind=kind,
        account_id=account_id,
        video_id=video_id,
        channel_id=channel_id,
        ts_ist=burst_timestamp(rng, burst_start_s, burst_duration_s, 0.95),
        session_id=None,
    )


def make_infra_hint(
    rng: np.random.Generator,
    hint_id: str,
    subject_kind: EntityKind,
    subject_id: str,
    signal_type: InfraSignalKind,
    signal_value: str,
    burst_start_s: int,
    burst_duration_s: int,
) -> InfraHint:
    return InfraHint(
        hint_id=hint_id,
        subject_kind=subject_kind,
        subject_id=subject_id,
        signal_type=signal_type,
        signal_value=signal_value,
        observed_ts=burst_timestamp(rng, burst_start_s, burst_duration_s, 0.9),
    )


def make_label(
    rng: np.random.Generator,
    entity_kind: EntityKind,
    entity_id: str,
    threat_class: ThreatClass,
    ring_id: str | None,
    generator_params_hash: str,
) -> SealedLabel:
    return SealedLabel(
        entity_kind=entity_kind,
        entity_id=entity_id,
        threat_class=threat_class,
        ring_id=ring_id,
        planted_ts=burst_timestamp(rng, 0, WINDOW_SECONDS, 1.0),
        generator_params_hash=generator_params_hash,
    )


def merge_planted(results: tuple[PlantedResult, ...]) -> PlantedResult:
    return PlantedResult(
        accounts=tuple(a for r in results for a in r.accounts),
        channels=tuple(c for r in results for c in r.channels),
        videos=tuple(v for r in results for v in r.videos),
        comments=tuple(c for r in results for c in r.comments),
        engagement_events=tuple(e for r in results for e in r.engagement_events),
        infra_hints=tuple(h for r in results for h in r.infra_hints),
        sealed_labels=tuple(label for r in results for label in r.sealed_labels),
    )
