# SPDX-License-Identifier: MIT
"""T-02: Fake engagement networks (sub-for-sub, view bursts, engagement pods).

Policy anchor: Fake Engagement policy (ARCHITECTURE 2.1, T-02). A ring of
freshly created accounts creates a small cluster of beneficiary channels and
videos, then reciprocally subscribes to / views / likes every other ring
member's channel and video in a tight coordinated burst - the
"engagement pod" pattern: dense, mutual, temporally clustered engagement
with no organic discovery path. Engagement events themselves aren't
labelable entities (no EntityKind member for them), so only the new
accounts/channels/videos count against budget.
"""

from dataclasses import dataclass

import numpy as np

from ts_sentry.data.enums import EngagementKind, EntityKind, ProvenanceSignal, ThreatClass
from ts_sentry.data.threats.common import (
    PlantedResult,
    assert_budget_respected,
    make_label,
    make_ring_account,
    make_ring_channel,
    make_ring_engagement,
    make_ring_video,
    merge_planted,
    params_hash,
)
from ts_sentry.data.tz import WINDOW_SECONDS

RING_COUNT = 2
MEMBERS_PER_RING = 3  # each member owns one channel with one video
BURST_DURATION_S = 2 * 3600


@dataclass(frozen=True, slots=True)
class T02Params:
    ring_count: int = RING_COUNT
    members_per_ring: int = MEMBERS_PER_RING
    burst_duration_s: int = BURST_DURATION_S

    @classmethod
    def for_budget(cls, budget: int) -> "T02Params":
        params = cls()
        # Each member costs 3 labelable rows: account + channel + video.
        while (
            params.ring_count * params.members_per_ring * 3 > budget and params.members_per_ring > 1
        ):
            params = T02Params(
                ring_count=params.ring_count,
                members_per_ring=params.members_per_ring - 1,
                burst_duration_s=params.burst_duration_s,
            )
        while params.ring_count * params.members_per_ring * 3 > budget and params.ring_count > 1:
            params = T02Params(
                ring_count=params.ring_count - 1,
                members_per_ring=params.members_per_ring,
                burst_duration_s=params.burst_duration_s,
            )
        return params


def plant(rng: np.random.Generator, budget: int) -> PlantedResult:
    params = T02Params.for_budget(budget)
    phash = params_hash(params)
    results = []
    for ring_i in range(params.ring_count):
        ring_id = f"ring_t02_{ring_i:03d}"
        ip_bucket = f"ipb_t02_{ring_i:03d}"
        device_hint = f"devhint_t02_{ring_i:03d}"
        burst_start_s = int(rng.integers(0, WINDOW_SECONDS - params.burst_duration_s))

        accounts = []
        channels = []
        videos = []
        labels = []
        for member_i in range(params.members_per_ring):
            account_id = f"t02_acct_{ring_i:03d}_{member_i:03d}"
            channel_id = f"t02_chan_{ring_i:03d}_{member_i:03d}"
            video_id = f"t02_vid_{ring_i:03d}_{member_i:03d}"

            account = make_ring_account(rng, account_id, ip_bucket, device_hint)
            channel = make_ring_channel(
                rng,
                channel_id,
                account_id,
                display_name=f"PodChannel{ring_i}{member_i}",
                description="Growth network member channel.",
                burst_start_s=burst_start_s,
                burst_duration_s=params.burst_duration_s,
            )
            video = make_ring_video(
                rng,
                video_id,
                channel_id,
                title=f"Sub4Sub video {ring_i}-{member_i}",
                description="Engagement pod content.",
                burst_start_s=burst_start_s,
                burst_duration_s=params.burst_duration_s,
                synthetic_media_disclosed=False,
                provenance_signal=ProvenanceSignal.UNKNOWN,
            )
            accounts.append(account)
            channels.append(channel)
            videos.append(video)
            labels.extend(
                [
                    make_label(
                        rng,
                        EntityKind.ACCOUNT,
                        account_id,
                        ThreatClass.T02_FAKE_ENGAGEMENT_NETWORK,
                        ring_id,
                        phash,
                    ),
                    make_label(
                        rng,
                        EntityKind.CHANNEL,
                        channel_id,
                        ThreatClass.T02_FAKE_ENGAGEMENT_NETWORK,
                        ring_id,
                        phash,
                    ),
                    make_label(
                        rng,
                        EntityKind.VIDEO,
                        video_id,
                        ThreatClass.T02_FAKE_ENGAGEMENT_NETWORK,
                        ring_id,
                        phash,
                    ),
                ]
            )

        # Reciprocal engagement: every member engages with every other
        # member's channel/video (not itself) - the dense mutual pod graph.
        engagement_events = []
        event_i = 0
        for actor in accounts:
            for other_channel, other_video in zip(channels, videos, strict=True):
                if other_channel.account_id == actor.account_id:
                    continue
                engagement_events.append(
                    make_ring_engagement(
                        rng,
                        f"t02_eng_{ring_i:03d}_{event_i:04d}",
                        EngagementKind.SUBSCRIBE,
                        actor.account_id,
                        burst_start_s,
                        params.burst_duration_s,
                        channel_id=other_channel.channel_id,
                    )
                )
                event_i += 1
                engagement_events.append(
                    make_ring_engagement(
                        rng,
                        f"t02_eng_{ring_i:03d}_{event_i:04d}",
                        EngagementKind.VIEW,
                        actor.account_id,
                        burst_start_s,
                        params.burst_duration_s,
                        video_id=other_video.video_id,
                    )
                )
                event_i += 1
                engagement_events.append(
                    make_ring_engagement(
                        rng,
                        f"t02_eng_{ring_i:03d}_{event_i:04d}",
                        EngagementKind.LIKE,
                        actor.account_id,
                        burst_start_s,
                        params.burst_duration_s,
                        video_id=other_video.video_id,
                    )
                )
                event_i += 1

        results.append(
            PlantedResult(
                accounts=tuple(accounts),
                channels=tuple(channels),
                videos=tuple(videos),
                engagement_events=tuple(engagement_events),
                sealed_labels=tuple(labels),
            )
        )

    merged = merge_planted(tuple(results))
    assert_budget_respected(merged, budget)
    return merged
