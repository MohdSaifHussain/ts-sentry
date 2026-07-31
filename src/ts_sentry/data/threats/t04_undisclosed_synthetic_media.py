# SPDX-License-Identifier: MIT
"""T-04: Undisclosed synthetic media channels.

Policy anchor: 2026 AI-disclosure rules (ARCHITECTURE 2.1, T-04) -
nondisclosure risks demonetization. A small number of channels publish
videos that are synthetic (AI-generated) but never marked disclosed, and
carry an explicit ABSENT provenance signal (C2PA-direction field,
ARCHITECTURE 8.7) rather than the UNKNOWN default ordinary undecided
content gets - the nondisclosure is deliberate, not merely unset.
"""

from dataclasses import dataclass

import numpy as np

from ts_sentry.data.enums import EntityKind, ProvenanceSignal, ThreatClass
from ts_sentry.data.threats.common import (
    PlantedResult,
    assert_budget_respected,
    make_label,
    make_ring_account,
    make_ring_channel,
    make_ring_video,
    merge_planted,
    params_hash,
)
from ts_sentry.data.tz import WINDOW_SECONDS

_TOPICS = ("breaking news recap", "celebrity update", "true crime story")

CHANNEL_COUNT = 2
VIDEOS_PER_CHANNEL = 3
BURST_DURATION_S = 6 * 3600


@dataclass(frozen=True, slots=True)
class T04Params:
    channel_count: int = CHANNEL_COUNT
    videos_per_channel: int = VIDEOS_PER_CHANNEL
    burst_duration_s: int = BURST_DURATION_S

    @classmethod
    def for_budget(cls, budget: int) -> "T04Params":
        params = cls()
        # Each channel costs 1 (account) + 1 (channel) + videos_per_channel.
        while (
            params.channel_count * (2 + params.videos_per_channel) > budget
            and params.videos_per_channel > 1
        ):
            params = T04Params(
                channel_count=params.channel_count,
                videos_per_channel=params.videos_per_channel - 1,
                burst_duration_s=params.burst_duration_s,
            )
        while (
            params.channel_count * (2 + params.videos_per_channel) > budget
            and params.channel_count > 1
        ):
            params = T04Params(
                channel_count=params.channel_count - 1,
                videos_per_channel=params.videos_per_channel,
                burst_duration_s=params.burst_duration_s,
            )
        return params


def plant(rng: np.random.Generator, budget: int) -> PlantedResult:
    params = T04Params.for_budget(budget)
    phash = params_hash(params)
    results = []
    for chan_i in range(params.channel_count):
        ring_id = f"ring_t04_{chan_i:03d}"
        account_id = f"t04_acct_{chan_i:03d}"
        channel_id = f"t04_chan_{chan_i:03d}"
        burst_start_s = int(rng.integers(0, WINDOW_SECONDS - params.burst_duration_s))
        topic = _TOPICS[chan_i % len(_TOPICS)]

        account = make_ring_account(rng, account_id, f"ipb_t04_{chan_i:03d}")
        channel = make_ring_channel(
            rng,
            channel_id,
            account_id,
            display_name=f"{topic.title()} Daily",
            description=f"AI-narrated {topic} videos, posted daily.",
            burst_start_s=burst_start_s,
            burst_duration_s=params.burst_duration_s,
        )
        videos = []
        labels = [
            make_label(
                rng,
                EntityKind.ACCOUNT,
                account_id,
                ThreatClass.T04_UNDISCLOSED_SYNTHETIC_MEDIA,
                ring_id,
                phash,
            ),
            make_label(
                rng,
                EntityKind.CHANNEL,
                channel_id,
                ThreatClass.T04_UNDISCLOSED_SYNTHETIC_MEDIA,
                ring_id,
                phash,
            ),
        ]
        for vid_i in range(params.videos_per_channel):
            video_id = f"t04_vid_{chan_i:03d}_{vid_i:03d}"
            video = make_ring_video(
                rng,
                video_id,
                channel_id,
                title=f"{topic.title()} #{vid_i}",
                description=f"Today's {topic}.",
                burst_start_s=burst_start_s,
                burst_duration_s=params.burst_duration_s,
                synthetic_media_disclosed=False,
                provenance_signal=ProvenanceSignal.ABSENT,
            )
            videos.append(video)
            labels.append(
                make_label(
                    rng,
                    EntityKind.VIDEO,
                    video_id,
                    ThreatClass.T04_UNDISCLOSED_SYNTHETIC_MEDIA,
                    ring_id,
                    phash,
                )
            )

        results.append(
            PlantedResult(
                accounts=(account,),
                channels=(channel,),
                videos=tuple(videos),
                sealed_labels=tuple(labels),
            )
        )

    merged = merge_planted(tuple(results))
    assert_budget_respected(merged, budget)
    return merged
