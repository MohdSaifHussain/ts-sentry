# SPDX-License-Identifier: MIT
"""T-06: Mass-produced inauthentic content networks ("slop farms").

Policy anchor: July 2025 monetization update, tightened through 2026
(ARCHITECTURE 2.1, T-06). Several channels, all owned by accounts sharing a
device fingerprint (the "farm" infrastructure), each publish a high volume
of near-templated videos in a tight burst - the SHARED_UPLOAD_PATTERN
infra hint on every channel is the distinguishing signal: same
production pipeline, different nominal owners.
"""

from dataclasses import dataclass

import numpy as np

from ts_sentry.data.enums import EntityKind, InfraSignalKind, ProvenanceSignal, ThreatClass
from ts_sentry.data.threats.common import (
    PlantedResult,
    assert_budget_respected,
    make_infra_hint,
    make_label,
    make_ring_account,
    make_ring_channel,
    make_ring_video,
    merge_planted,
    params_hash,
)
from ts_sentry.data.tz import WINDOW_SECONDS

_TOPICS = ("top 10 facts", "life hacks", "quiz compilation")

CHANNEL_COUNT = 3
VIDEOS_PER_CHANNEL = 4
BURST_DURATION_S = 8 * 3600


@dataclass(frozen=True, slots=True)
class T06Params:
    channel_count: int = CHANNEL_COUNT
    videos_per_channel: int = VIDEOS_PER_CHANNEL
    burst_duration_s: int = BURST_DURATION_S

    @classmethod
    def for_budget(cls, budget: int) -> "T06Params":
        params = cls()
        while (
            params.channel_count * (2 + params.videos_per_channel) > budget
            and params.videos_per_channel > 1
        ):
            params = T06Params(
                channel_count=params.channel_count,
                videos_per_channel=params.videos_per_channel - 1,
                burst_duration_s=params.burst_duration_s,
            )
        while (
            params.channel_count * (2 + params.videos_per_channel) > budget
            and params.channel_count > 1
        ):
            params = T06Params(
                channel_count=params.channel_count - 1,
                videos_per_channel=params.videos_per_channel,
                burst_duration_s=params.burst_duration_s,
            )
        return params


def plant(rng: np.random.Generator, budget: int) -> PlantedResult:
    params = T06Params.for_budget(budget)
    phash = params_hash(params)
    ring_id = "ring_t06_000"
    shared_upload_pattern = "upload_pattern_t06_000"
    burst_start_s = int(rng.integers(0, WINDOW_SECONDS - params.burst_duration_s))

    results = []
    for chan_i in range(params.channel_count):
        account_id = f"t06_acct_{chan_i:03d}"
        channel_id = f"t06_chan_{chan_i:03d}"
        topic = _TOPICS[chan_i % len(_TOPICS)]

        account = make_ring_account(rng, account_id, f"ipb_t06_{chan_i:03d}")
        channel = make_ring_channel(
            rng,
            channel_id,
            account_id,
            display_name=f"{topic.title()} Farm {chan_i}",
            description=f"Daily {topic} content.",
            burst_start_s=burst_start_s,
            burst_duration_s=params.burst_duration_s,
        )
        infra_hint = make_infra_hint(
            rng,
            f"t06_infra_{chan_i:03d}",
            EntityKind.CHANNEL,
            channel_id,
            InfraSignalKind.SHARED_UPLOAD_PATTERN,
            shared_upload_pattern,
            burst_start_s,
            params.burst_duration_s,
        )
        videos = []
        labels = [
            make_label(
                rng, EntityKind.ACCOUNT, account_id, ThreatClass.T06_SLOP_FARM, ring_id, phash
            ),
            make_label(
                rng, EntityKind.CHANNEL, channel_id, ThreatClass.T06_SLOP_FARM, ring_id, phash
            ),
        ]
        for vid_i in range(params.videos_per_channel):
            video_id = f"t06_vid_{chan_i:03d}_{vid_i:03d}"
            video = make_ring_video(
                rng,
                video_id,
                channel_id,
                title=f"{topic.title()} you won't believe #{vid_i}",
                description=f"{topic.title()} compilation {vid_i}.",
                burst_start_s=burst_start_s,
                burst_duration_s=params.burst_duration_s,
                synthetic_media_disclosed=False,
                provenance_signal=ProvenanceSignal.UNKNOWN,
            )
            videos.append(video)
            labels.append(
                make_label(
                    rng, EntityKind.VIDEO, video_id, ThreatClass.T06_SLOP_FARM, ring_id, phash
                )
            )

        results.append(
            PlantedResult(
                accounts=(account,),
                channels=(channel,),
                videos=tuple(videos),
                infra_hints=(infra_hint,),
                sealed_labels=tuple(labels),
            )
        )

    merged = merge_planted(tuple(results))
    assert_budget_respected(merged, budget)
    return merged
