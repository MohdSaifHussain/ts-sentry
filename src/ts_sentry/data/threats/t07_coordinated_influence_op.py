# SPDX-License-Identifier: MIT
"""T-07: Coordinated influence operation clusters.

Policy anchor: TAG / Influence Operations Bulletin patterns (ARCHITECTURE
2.1, T-07) - many channels, one narrative, shared infrastructure. Several
channels, owned by accounts sharing one IP bucket, all publish videos
pushing the same narrative keyword and cross-amplify each other with
reinforcing comments and views - distinct from T-06 (volume/templating,
no shared narrative) and from T-02 (engagement-only, no shared content
narrative).
"""

from dataclasses import dataclass

import numpy as np

from ts_sentry.data.enums import (
    EngagementKind,
    EntityKind,
    InfraSignalKind,
    ProvenanceSignal,
    ThreatClass,
)
from ts_sentry.data.threats.common import (
    PlantedResult,
    assert_budget_respected,
    make_infra_hint,
    make_label,
    make_ring_account,
    make_ring_channel,
    make_ring_comment,
    make_ring_engagement,
    make_ring_video,
    params_hash,
)
from ts_sentry.data.tz import WINDOW_SECONDS

_NARRATIVE = "the mainstream story is hiding the real truth"

CHANNEL_COUNT = 3
BURST_DURATION_S = 5 * 3600


@dataclass(frozen=True, slots=True)
class T07Params:
    channel_count: int = CHANNEL_COUNT
    burst_duration_s: int = BURST_DURATION_S

    @classmethod
    def for_budget(cls, budget: int) -> "T07Params":
        params = cls()
        # Each channel costs 3 (account + channel + video) + 1 cross-comment.
        # Floor is 2, not 1: cross-amplification requires at least one other
        # channel's video to comment on and view (see plant() below) - a
        # single-channel "cluster" has no narrative to cross-amplify.
        while params.channel_count * 4 > budget and params.channel_count > 2:
            params = T07Params(
                channel_count=params.channel_count - 1,
                burst_duration_s=params.burst_duration_s,
            )
        return params


def plant(rng: np.random.Generator, budget: int) -> PlantedResult:
    params = T07Params.for_budget(budget)
    phash = params_hash(params)
    ring_id = "ring_t07_000"
    shared_ip_bucket = "ipb_t07_shared"
    burst_start_s = int(rng.integers(0, WINDOW_SECONDS - params.burst_duration_s))

    accounts = []
    channels = []
    videos = []
    labels = []
    infra_hints = []
    for chan_i in range(params.channel_count):
        account_id = f"t07_acct_{chan_i:03d}"
        channel_id = f"t07_chan_{chan_i:03d}"
        video_id = f"t07_vid_{chan_i:03d}"

        account = make_ring_account(rng, account_id, shared_ip_bucket)
        channel = make_ring_channel(
            rng,
            channel_id,
            account_id,
            display_name=f"Independent Voice {chan_i}",
            description=f"Reporting what others won't: {_NARRATIVE}.",
            burst_start_s=burst_start_s,
            burst_duration_s=params.burst_duration_s,
        )
        video = make_ring_video(
            rng,
            video_id,
            channel_id,
            title=f"Why {_NARRATIVE} (part {chan_i})",
            description=_NARRATIVE,
            burst_start_s=burst_start_s,
            burst_duration_s=params.burst_duration_s,
            synthetic_media_disclosed=False,
            provenance_signal=ProvenanceSignal.UNKNOWN,
        )
        infra_hint = make_infra_hint(
            rng,
            f"t07_infra_{chan_i:03d}",
            EntityKind.ACCOUNT,
            account_id,
            InfraSignalKind.SHARED_IP_BUCKET,
            shared_ip_bucket,
            burst_start_s,
            params.burst_duration_s,
        )

        accounts.append(account)
        channels.append(channel)
        videos.append(video)
        infra_hints.append(infra_hint)
        labels.extend(
            [
                make_label(
                    rng,
                    EntityKind.ACCOUNT,
                    account_id,
                    ThreatClass.T07_COORDINATED_INFLUENCE_OP,
                    ring_id,
                    phash,
                ),
                make_label(
                    rng,
                    EntityKind.CHANNEL,
                    channel_id,
                    ThreatClass.T07_COORDINATED_INFLUENCE_OP,
                    ring_id,
                    phash,
                ),
                make_label(
                    rng,
                    EntityKind.VIDEO,
                    video_id,
                    ThreatClass.T07_COORDINATED_INFLUENCE_OP,
                    ring_id,
                    phash,
                ),
            ]
        )

    # Cross-amplification: each member views every other member's video and
    # leaves one narrative-reinforcing comment on it - the "one narrative,
    # many channels" pattern, not budgeted beyond the one comment per member
    # (comments are labelable; views are not).
    comments = []
    engagement_events = []
    event_i = 0
    for actor, own_video in zip(accounts, videos, strict=True):
        for other_video in videos:
            if other_video.video_id == own_video.video_id:
                continue
            engagement_events.append(
                make_ring_engagement(
                    rng,
                    f"t07_eng_{event_i:04d}",
                    EngagementKind.VIEW,
                    actor.account_id,
                    burst_start_s,
                    params.burst_duration_s,
                    video_id=other_video.video_id,
                )
            )
            event_i += 1

        target_video = next(v for v in videos if v.video_id != own_video.video_id)
        comment_id = f"t07_cmt_{actor.account_id}"
        comment = make_ring_comment(
            rng,
            comment_id,
            target_video.video_id,
            actor.account_id,
            f"Exactly - {_NARRATIVE}, see my channel for more.",
            template_id="t07_tmpl_000",
            burst_start_s=burst_start_s,
            burst_duration_s=params.burst_duration_s,
        )
        comments.append(comment)
        labels.append(
            make_label(
                rng,
                EntityKind.COMMENT,
                comment_id,
                ThreatClass.T07_COORDINATED_INFLUENCE_OP,
                ring_id,
                phash,
            )
        )

    result = PlantedResult(
        accounts=tuple(accounts),
        channels=tuple(channels),
        videos=tuple(videos),
        comments=tuple(comments),
        engagement_events=tuple(engagement_events),
        infra_hints=tuple(infra_hints),
        sealed_labels=tuple(labels),
    )
    assert_budget_respected(result, budget)
    return result
