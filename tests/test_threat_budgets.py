# SPDX-License-Identifier: MIT
"""D3 budget invariant: every threat generator must respect its labelable
entity budget (STEP-01 3.4 benign-majority floor), including at the tight
budgets that force it down to a single ring/channel. This is exactly the
class of bug caught in review: a shrink loop that ignored one cost factor
(comments) and busted its own budget under real seeds.
"""

from datetime import datetime

import numpy as np
import pytest

from ts_sentry.data.enums import EntityKind, ProvenanceSignal
from ts_sentry.data.schema import Video
from ts_sentry.data.threats import (
    t01_comment_spam_ring as t01,
)
from ts_sentry.data.threats import (
    t02_fake_engagement_network as t02,
)
from ts_sentry.data.threats import (
    t03_off_platform_diversion as t03,
)
from ts_sentry.data.threats import (
    t04_undisclosed_synthetic_media as t04,
)
from ts_sentry.data.threats import (
    t05_ai_persona_authority as t05,
)
from ts_sentry.data.threats import (
    t06_slop_farm as t06,
)
from ts_sentry.data.threats import (
    t07_coordinated_influence_op as t07,
)
from ts_sentry.data.tz import IST

_TARGET_VIDEOS = (
    Video(
        video_id="vid_target_0",
        channel_id="chan_target_0",
        title="Target video",
        description="A base-population video threat generators comment on.",
        published_ts=datetime(2024, 1, 1, tzinfo=IST),
        duration_s=300,
        synthetic_media_disclosed=True,
        provenance_signal=ProvenanceSignal.PRESENT,
    ),
)

# Each class's true minimum labelable footprint (one ring/channel, floor
# per-unit sizes). Budgets at and above this must never raise; the classes'
# own for_budget() must shrink down to fit.
_MIN_BUDGETS = {
    "t01": 1,  # 1 ring * 1 account * (1 + 0 comments)
    "t02": 3,  # 1 ring * 1 member * (account+channel+video)
    "t03": 1,
    "t04": 3,  # 1 channel * (account+channel+1 video)
    "t05": 3,
    "t06": 3,
    "t07": 8,  # 2 channels (minimum for cross-amplification) * 4 each
}


@pytest.mark.parametrize("budget", [1, 2, 3, 5, 10, 19, 50])
def test_all_classes_respect_budget(budget: int) -> None:
    rng = np.random.default_rng(0)
    results = [
        t01.plant(rng, _TARGET_VIDEOS, max(budget, _MIN_BUDGETS["t01"])),
        t02.plant(rng, max(budget, _MIN_BUDGETS["t02"])),
        t03.plant(rng, _TARGET_VIDEOS, max(budget, _MIN_BUDGETS["t03"])),
        t04.plant(rng, max(budget, _MIN_BUDGETS["t04"])),
        t05.plant(rng, max(budget, _MIN_BUDGETS["t05"])),
        t06.plant(rng, max(budget, _MIN_BUDGETS["t06"])),
        t07.plant(rng, max(budget, _MIN_BUDGETS["t07"])),
    ]
    for name, result in zip(_MIN_BUDGETS, results, strict=True):
        assert result.labelable_count <= max(budget, _MIN_BUDGETS[name]), name


def test_every_class_converges_to_its_exact_minimum_floor() -> None:
    """At exactly its minimum footprint, each class must produce precisely
    one ring/channel - not silently over-plant."""
    rng = np.random.default_rng(1)
    assert (
        t01.plant(rng, _TARGET_VIDEOS, _MIN_BUDGETS["t01"]).labelable_count == _MIN_BUDGETS["t01"]
    )
    assert t02.plant(rng, _MIN_BUDGETS["t02"]).labelable_count == _MIN_BUDGETS["t02"]
    assert (
        t03.plant(rng, _TARGET_VIDEOS, _MIN_BUDGETS["t03"]).labelable_count == _MIN_BUDGETS["t03"]
    )
    assert t04.plant(rng, _MIN_BUDGETS["t04"]).labelable_count == _MIN_BUDGETS["t04"]
    assert t05.plant(rng, _MIN_BUDGETS["t05"]).labelable_count == _MIN_BUDGETS["t05"]
    assert t06.plant(rng, _MIN_BUDGETS["t06"]).labelable_count == _MIN_BUDGETS["t06"]
    assert t07.plant(rng, _MIN_BUDGETS["t07"]).labelable_count == _MIN_BUDGETS["t07"]


def test_planted_entities_carry_their_own_threat_kind_label() -> None:
    rng = np.random.default_rng(2)
    result = t06.plant(rng, _MIN_BUDGETS["t06"])
    label_kinds = {label.entity_kind for label in result.sealed_labels}
    assert label_kinds <= {
        EntityKind.ACCOUNT,
        EntityKind.CHANNEL,
        EntityKind.VIDEO,
        EntityKind.COMMENT,
    }
    assert len(result.sealed_labels) == result.labelable_count
