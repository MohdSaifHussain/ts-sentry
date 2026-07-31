# SPDX-License-Identifier: MIT
"""Queryable entity schemas: the six tables agents may pivot across.

Every table here is reachable through a ``DataScope`` member
(``ts_sentry.governance.scopes``). Ground-truth labels live separately in
``ts_sentry.data.sealed`` and are never reachable through this module or
through ``DataScope``.

All timestamps are timezone-aware ``Asia/Kolkata`` (IST), serialized ISO 8601
at the storage/export boundary, never naive.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import assert_never

from ts_sentry.data.enums import EngagementKind, EntityKind, InfraSignalKind, ProvenanceSignal
from ts_sentry.data.ids import AccountId, ChannelId, CommentId, EventId, InfraHintId, VideoId
from ts_sentry.data.tz import require_ist


@dataclass(frozen=True, slots=True)
class AccountMeta:
    """A platform account. May own zero or more channels."""

    account_id: AccountId
    created_ts: datetime
    display_name: str
    is_verified: bool
    signup_ip_bucket: str
    device_fingerprint_hint: str | None

    def __post_init__(self) -> None:
        require_ist(self.created_ts, "created_ts")


@dataclass(frozen=True, slots=True)
class Channel:
    """A channel, owned by exactly one account."""

    channel_id: ChannelId
    account_id: AccountId
    created_ts: datetime
    display_name: str
    subscriber_count: int
    description: str

    def __post_init__(self) -> None:
        require_ist(self.created_ts, "created_ts")


@dataclass(frozen=True, slots=True)
class Video:
    """A video published on a channel."""

    video_id: VideoId
    channel_id: ChannelId
    title: str
    description: str
    published_ts: datetime
    duration_s: int
    synthetic_media_disclosed: bool
    provenance_signal: ProvenanceSignal

    def __post_init__(self) -> None:
        require_ist(self.published_ts, "published_ts")


@dataclass(frozen=True, slots=True)
class Comment:
    """A comment on a video, optionally a reply to another comment."""

    comment_id: CommentId
    video_id: VideoId
    account_id: AccountId
    parent_comment_id: CommentId | None
    posted_ts: datetime
    text: str
    template_id: str | None

    def __post_init__(self) -> None:
        require_ist(self.posted_ts, "posted_ts")


@dataclass(frozen=True, slots=True)
class EngagementEvent:
    """A single engagement event.

    Exactly one of ``video_id`` / ``channel_id`` is populated, depending on
    ``kind`` (SUBSCRIBE targets a channel; VIEW/LIKE/DISLIKE/SHARE/REPORT
    target a video) - enforced in ``__post_init__``, not just documented.
    When ``kind`` is VIEW, this row doubles as the VVR-required view record:
    ``event_id`` is the view id, and ``video_id``/``ts_ist``/``account_id``
    are the video, timestamp, and viewer account fields STEP-01 names
    explicitly.
    """

    event_id: EventId
    kind: EngagementKind
    account_id: AccountId
    video_id: VideoId | None
    channel_id: ChannelId | None
    ts_ist: datetime
    session_id: str | None

    def __post_init__(self) -> None:
        require_ist(self.ts_ist, "ts_ist")
        match self.kind:
            case EngagementKind.SUBSCRIBE:
                wants_channel = True
            case (
                EngagementKind.VIEW
                | EngagementKind.LIKE
                | EngagementKind.DISLIKE
                | EngagementKind.SHARE
                | EngagementKind.REPORT
            ):
                wants_channel = False
            case _:  # pragma: no cover - exhaustiveness guard, unreachable per mypy
                assert_never(self.kind)
        if wants_channel:
            if self.channel_id is None or self.video_id is not None:
                raise ValueError(f"{self.kind} must target exactly channel_id (not video_id)")
        else:
            if self.video_id is None or self.channel_id is not None:
                raise ValueError(f"{self.kind} must target exactly video_id (not channel_id)")


@dataclass(frozen=True, slots=True)
class InfraHint:
    """An infrastructure-overlap signal attached to an account or channel."""

    hint_id: InfraHintId
    subject_kind: EntityKind
    subject_id: str
    signal_type: InfraSignalKind
    signal_value: str
    observed_ts: datetime

    def __post_init__(self) -> None:
        require_ist(self.observed_ts, "observed_ts")
