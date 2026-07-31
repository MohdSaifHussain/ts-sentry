# SPDX-License-Identifier: MIT
"""T-03: Off-platform diversion (malware, scam funnels).

Policy anchor: Spam & Deceptive Practices policy (ARCHITECTURE 2.1, T-03).
A ring of freshly created accounts posts comments on *existing*
base-population videos, each pointing to the same small pool of
off-platform scam-funnel domains - the LINK_DOMAIN_REUSE signal is what
distinguishes this from ordinary spam (T-01): the ring is defined by shared
destination infrastructure, not shared device/account signals.
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

_SCAM_DOMAINS = (
    "free-crypto-giveaway.example",
    "claim-your-prize.example",
    "verify-account-now.example",
)
_LURE_TEXTS = (
    "I made $500 in a day, check {domain}",
    "Free gift cards here: {domain}",
    "Verify your account to keep access: {domain}",
)

RING_COUNT = 2
ACCOUNTS_PER_RING = 3
COMMENTS_PER_ACCOUNT = 2
BURST_DURATION_S = 4 * 3600


@dataclass(frozen=True, slots=True)
class T03Params:
    ring_count: int = RING_COUNT
    accounts_per_ring: int = ACCOUNTS_PER_RING
    comments_per_account: int = COMMENTS_PER_ACCOUNT
    burst_duration_s: int = BURST_DURATION_S

    def _labelable_count(self) -> int:
        # accounts + comments (accounts_per_ring comments each) per ring.
        return self.ring_count * self.accounts_per_ring * (1 + self.comments_per_account)

    @classmethod
    def for_budget(cls, budget: int) -> "T03Params":
        """Shrink ring/account/comment counts, never grow them, until the
        total labelable footprint (accounts + comments) fits the budget."""
        params = cls()
        while params._labelable_count() > budget and params.comments_per_account > 0:
            params = T03Params(
                ring_count=params.ring_count,
                accounts_per_ring=params.accounts_per_ring,
                comments_per_account=params.comments_per_account - 1,
                burst_duration_s=params.burst_duration_s,
            )
        while params._labelable_count() > budget and params.accounts_per_ring > 1:
            params = T03Params(
                ring_count=params.ring_count,
                accounts_per_ring=params.accounts_per_ring - 1,
                comments_per_account=params.comments_per_account,
                burst_duration_s=params.burst_duration_s,
            )
        while params._labelable_count() > budget and params.ring_count > 1:
            params = T03Params(
                ring_count=params.ring_count - 1,
                accounts_per_ring=params.accounts_per_ring,
                comments_per_account=params.comments_per_account,
                burst_duration_s=params.burst_duration_s,
            )
        return params


def plant(rng: np.random.Generator, target_videos: tuple[Video, ...], budget: int) -> PlantedResult:
    params = T03Params.for_budget(budget)
    phash = params_hash(params)
    results = []
    for ring_i in range(params.ring_count):
        ring_id = f"ring_t03_{ring_i:03d}"
        domain = _SCAM_DOMAINS[ring_i % len(_SCAM_DOMAINS)]
        burst_start_s = int(rng.integers(0, WINDOW_SECONDS - params.burst_duration_s))

        accounts = []
        comments = []
        infra_hints = []
        labels = []
        for acct_i in range(params.accounts_per_ring):
            account_id = f"t03_acct_{ring_i:03d}_{acct_i:03d}"
            account = make_ring_account(rng, account_id, f"ipb_t03_{ring_i:03d}_{acct_i:03d}")
            accounts.append(account)
            labels.append(
                make_label(
                    rng,
                    EntityKind.ACCOUNT,
                    account_id,
                    ThreatClass.T03_OFF_PLATFORM_DIVERSION,
                    ring_id,
                    phash,
                )
            )
            infra_hints.append(
                make_infra_hint(
                    rng,
                    f"t03_infra_{ring_i:03d}_{acct_i:03d}",
                    EntityKind.ACCOUNT,
                    account_id,
                    InfraSignalKind.LINK_DOMAIN_REUSE,
                    domain,
                    burst_start_s,
                    params.burst_duration_s,
                )
            )
            for cmt_i in range(params.comments_per_account):
                target_video = target_videos[int(rng.integers(0, len(target_videos)))]
                comment_id = f"t03_cmt_{ring_i:03d}_{acct_i:03d}_{cmt_i:03d}"
                lure = _LURE_TEXTS[int(rng.integers(0, len(_LURE_TEXTS)))].format(domain=domain)
                comment = make_ring_comment(
                    rng,
                    comment_id,
                    target_video.video_id,
                    account_id,
                    lure,
                    template_id=None,
                    burst_start_s=burst_start_s,
                    burst_duration_s=params.burst_duration_s,
                )
                comments.append(comment)
                labels.append(
                    make_label(
                        rng,
                        EntityKind.COMMENT,
                        comment_id,
                        ThreatClass.T03_OFF_PLATFORM_DIVERSION,
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
