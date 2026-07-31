# SPDX-License-Identifier: MIT
"""T-01: Coordinated comment spam rings.

Policy anchor: Spam, Deceptive Practices & Scams (ARCHITECTURE 2.1, T-01) -
"classic; still dominant by volume". A small ring of freshly created
accounts, sharing infrastructure (device fingerprint), posts near-identical
templated comments across a burst of *existing* base-population videos in a
short coordinated window. No new channels/videos are created - the ring
targets the benign base population, which is what makes this pattern
detectable as spam rather than as new content.
"""

from dataclasses import dataclass

import numpy as np

from ts_sentry.data.enums import EntityKind, InfraSignalKind, ThreatClass
from ts_sentry.data.schema import Video
from ts_sentry.data.threats.common import (
    PlantedResult,
    assert_budget_respected,
    make_infra_hint,
    make_label,
    make_ring_account,
    make_ring_comment,
    merge_planted,
    params_hash,
)
from ts_sentry.data.tz import WINDOW_SECONDS

_TEMPLATES = (
    "Check out my channel for more content like this!!",
    "Wow amazing video, you should see my channel too!",
    "This reminded me of a channel you should check out.",
)

RING_COUNT = 2
ACCOUNTS_PER_RING = 3
COMMENTS_PER_ACCOUNT = 2
BURST_DURATION_S = 3 * 3600  # 3-hour coordinated posting window


@dataclass(frozen=True, slots=True)
class T01Params:
    ring_count: int = RING_COUNT
    accounts_per_ring: int = ACCOUNTS_PER_RING
    comments_per_account: int = COMMENTS_PER_ACCOUNT
    burst_duration_s: int = BURST_DURATION_S

    def _labelable_count(self) -> int:
        # accounts + comments (accounts_per_ring comments each) per ring.
        return self.ring_count * self.accounts_per_ring * (1 + self.comments_per_account)

    @classmethod
    def for_budget(cls, budget: int) -> "T01Params":
        """Shrink ring/account/comment counts, never grow them, until the
        total labelable footprint (accounts + comments) fits the budget."""
        params = cls()
        while params._labelable_count() > budget and params.comments_per_account > 0:
            params = T01Params(
                ring_count=params.ring_count,
                accounts_per_ring=params.accounts_per_ring,
                comments_per_account=params.comments_per_account - 1,
                burst_duration_s=params.burst_duration_s,
            )
        while params._labelable_count() > budget and params.accounts_per_ring > 1:
            params = T01Params(
                ring_count=params.ring_count,
                accounts_per_ring=params.accounts_per_ring - 1,
                comments_per_account=params.comments_per_account,
                burst_duration_s=params.burst_duration_s,
            )
        while params._labelable_count() > budget and params.ring_count > 1:
            params = T01Params(
                ring_count=params.ring_count - 1,
                accounts_per_ring=params.accounts_per_ring,
                comments_per_account=params.comments_per_account,
                burst_duration_s=params.burst_duration_s,
            )
        return params


def plant(
    rng: np.random.Generator,
    target_videos: tuple[Video, ...],
    budget: int,
) -> PlantedResult:
    params = T01Params.for_budget(budget)
    phash = params_hash(params)
    results = []
    for ring_i in range(params.ring_count):
        ring_id = f"ring_t01_{ring_i:03d}"
        device_hint = f"devhint_t01_{ring_i:03d}"
        ip_bucket = f"ipb_t01_{ring_i:03d}"
        burst_start_s = int(rng.integers(0, WINDOW_SECONDS - params.burst_duration_s))

        accounts = []
        infra_hints = []
        comments = []
        labels = []
        for acct_i in range(params.accounts_per_ring):
            account_id = f"t01_acct_{ring_i:03d}_{acct_i:03d}"
            account = make_ring_account(rng, account_id, ip_bucket, device_hint)
            accounts.append(account)
            labels.append(
                make_label(
                    rng,
                    EntityKind.ACCOUNT,
                    account_id,
                    ThreatClass.T01_COMMENT_SPAM_RING,
                    ring_id,
                    phash,
                )
            )
            infra_hints.append(
                make_infra_hint(
                    rng,
                    f"t01_infra_{ring_i:03d}_{acct_i:03d}",
                    EntityKind.ACCOUNT,
                    account_id,
                    InfraSignalKind.SHARED_DEVICE,
                    device_hint,
                    burst_start_s,
                    params.burst_duration_s,
                )
            )
            for cmt_i in range(params.comments_per_account):
                target_video = target_videos[int(rng.integers(0, len(target_videos)))]
                comment_id = f"t01_cmt_{ring_i:03d}_{acct_i:03d}_{cmt_i:03d}"
                template = _TEMPLATES[int(rng.integers(0, len(_TEMPLATES)))]
                comment = make_ring_comment(
                    rng,
                    comment_id,
                    target_video.video_id,
                    account_id,
                    template,
                    template_id=f"t01_tmpl_{ring_i:03d}",
                    burst_start_s=burst_start_s,
                    burst_duration_s=params.burst_duration_s,
                )
                comments.append(comment)
                labels.append(
                    make_label(
                        rng,
                        EntityKind.COMMENT,
                        comment_id,
                        ThreatClass.T01_COMMENT_SPAM_RING,
                        ring_id,
                        phash,
                    )
                )

        results.append(
            PlantedResult(
                accounts=tuple(accounts),
                comments=tuple(comments),
                infra_hints=tuple(infra_hints),
                sealed_labels=tuple(labels),
            )
        )

    merged = merge_planted(tuple(results))
    assert_budget_respected(merged, budget)
    return merged
