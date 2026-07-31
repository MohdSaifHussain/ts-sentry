# SPDX-License-Identifier: MIT
"""T-05: AI-persona authority channels.

Policy anchor: named 2026 enforcement priority (ARCHITECTURE 2.1, T-05) -
synthetic "experts" giving health, finance, or legal advice. Distinct from
T-04 (which is about disclosure of synthetic media in general): here the
content specifically claims domain authority ("as a doctor...", "as a
financial advisor...") while being undisclosed synthetic media, which is
what makes it a higher-severity nondisclosure case than an entertainment
recap channel.
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

_AUTHORITY_TOPICS = ("health", "finance", "legal")
_PERSONAS = ("Dr. Advisor", "Certified Planner", "Legal Expert")

CHANNEL_COUNT = 2
VIDEOS_PER_CHANNEL = 3
BURST_DURATION_S = 6 * 3600


@dataclass(frozen=True, slots=True)
class T05Params:
    channel_count: int = CHANNEL_COUNT
    videos_per_channel: int = VIDEOS_PER_CHANNEL
    burst_duration_s: int = BURST_DURATION_S

    @classmethod
    def for_budget(cls, budget: int) -> "T05Params":
        params = cls()
        while (
            params.channel_count * (2 + params.videos_per_channel) > budget
            and params.videos_per_channel > 1
        ):
            params = T05Params(
                channel_count=params.channel_count,
                videos_per_channel=params.videos_per_channel - 1,
                burst_duration_s=params.burst_duration_s,
            )
        while (
            params.channel_count * (2 + params.videos_per_channel) > budget
            and params.channel_count > 1
        ):
            params = T05Params(
                channel_count=params.channel_count - 1,
                videos_per_channel=params.videos_per_channel,
                burst_duration_s=params.burst_duration_s,
            )
        return params


def plant(rng: np.random.Generator, budget: int) -> PlantedResult:
    params = T05Params.for_budget(budget)
    phash = params_hash(params)
    results = []
    for chan_i in range(params.channel_count):
        ring_id = f"ring_t05_{chan_i:03d}"
        account_id = f"t05_acct_{chan_i:03d}"
        channel_id = f"t05_chan_{chan_i:03d}"
        burst_start_s = int(rng.integers(0, WINDOW_SECONDS - params.burst_duration_s))
        topic = _AUTHORITY_TOPICS[chan_i % len(_AUTHORITY_TOPICS)]
        persona = _PERSONAS[chan_i % len(_PERSONAS)]

        account = make_ring_account(rng, account_id, f"ipb_t05_{chan_i:03d}")
        channel = make_ring_channel(
            rng,
            channel_id,
            account_id,
            display_name=persona,
            description=f"Trusted {topic} advice from {persona}.",
            burst_start_s=burst_start_s,
            burst_duration_s=params.burst_duration_s,
        )
        videos = []
        labels = [
            make_label(
                rng,
                EntityKind.ACCOUNT,
                account_id,
                ThreatClass.T05_AI_PERSONA_AUTHORITY,
                ring_id,
                phash,
            ),
            make_label(
                rng,
                EntityKind.CHANNEL,
                channel_id,
                ThreatClass.T05_AI_PERSONA_AUTHORITY,
                ring_id,
                phash,
            ),
        ]
        for vid_i in range(params.videos_per_channel):
            video_id = f"t05_vid_{chan_i:03d}_{vid_i:03d}"
            video = make_ring_video(
                rng,
                video_id,
                channel_id,
                title=f"As a {topic} expert, here's what you need to know #{vid_i}",
                description=f"Professional {topic} guidance, episode {vid_i}.",
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
                    ThreatClass.T05_AI_PERSONA_AUTHORITY,
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
