# SPDX-License-Identifier: MIT
"""D2 hardening: timezone-awareness and EngagementEvent target invariants
are enforced structurally in __post_init__, not just documented.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from ts_sentry.data.enums import EngagementKind
from ts_sentry.data.schema import AccountMeta, EngagementEvent
from ts_sentry.data.tz import IST

_VALID_IST_TS = datetime(2024, 6, 1, tzinfo=IST)


def _account(created_ts: datetime) -> AccountMeta:
    return AccountMeta(
        account_id="acct_0000001",
        created_ts=created_ts,
        display_name="Test",
        is_verified=False,
        signup_ip_bucket="ipb_000",
        device_fingerprint_hint=None,
    )


def test_naive_timestamp_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _account(datetime(2024, 6, 1))  # no tzinfo


def test_wrong_offset_timestamp_rejected() -> None:
    with pytest.raises(ValueError, match="Asia/Kolkata"):
        _account(datetime(2024, 6, 1, tzinfo=UTC))


def test_equivalent_fixed_offset_accepted() -> None:
    # Same instant as IST expressed via a fixed-offset tzinfo rather than
    # ZoneInfo("Asia/Kolkata") - accepted, since Kolkata has no DST and the
    # UTC offset is what actually matters.
    fixed_ist = timezone(timedelta(hours=5, minutes=30))
    _account(datetime(2024, 6, 1, tzinfo=fixed_ist))  # must not raise


def _engagement_event(
    kind: EngagementKind, video_id: str | None, channel_id: str | None
) -> EngagementEvent:
    return EngagementEvent(
        event_id="eng_000000001",
        kind=kind,
        account_id="acct_0000001",
        video_id=video_id,
        channel_id=channel_id,
        ts_ist=_VALID_IST_TS,
        session_id=None,
    )


def test_engagement_event_valid_video_target_accepted() -> None:
    _engagement_event(EngagementKind.VIEW, video_id="vid_0000001", channel_id=None)


def test_engagement_event_valid_channel_target_accepted() -> None:
    _engagement_event(EngagementKind.SUBSCRIBE, video_id=None, channel_id="chan_000001")


def test_engagement_event_neither_target_rejected() -> None:
    with pytest.raises(ValueError, match="must target exactly"):
        _engagement_event(EngagementKind.VIEW, video_id=None, channel_id=None)


def test_engagement_event_both_targets_rejected() -> None:
    with pytest.raises(ValueError, match="must target exactly"):
        _engagement_event(EngagementKind.VIEW, video_id="vid_0000001", channel_id="chan_000001")


def test_engagement_event_wrong_target_for_video_kind_rejected() -> None:
    # VIEW must target a video, not a channel.
    with pytest.raises(ValueError, match="must target exactly video_id"):
        _engagement_event(EngagementKind.VIEW, video_id=None, channel_id="chan_000001")


def test_engagement_event_wrong_target_for_channel_kind_rejected() -> None:
    # SUBSCRIBE must target a channel, not a video.
    with pytest.raises(ValueError, match="must target exactly channel_id"):
        _engagement_event(EngagementKind.SUBSCRIBE, video_id="vid_0000001", channel_id=None)
