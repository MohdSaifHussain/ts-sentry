# SPDX-License-Identifier: MIT
"""D2 sealed-schema dataclass: construction and frozen-immutability contract."""

from dataclasses import FrozenInstanceError
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from ts_sentry.data.enums import EntityKind, ThreatClass
from ts_sentry.data.sealed import SealedLabel

IST = ZoneInfo("Asia/Kolkata")


def test_sealed_label_construction_and_benign_default() -> None:
    label = SealedLabel(
        entity_kind=EntityKind.CHANNEL,
        entity_id="chan_000001",
        threat_class=ThreatClass.BENIGN,
        ring_id=None,
        planted_ts=datetime(2024, 6, 1, tzinfo=IST),
        generator_params_hash="deadbeef",
    )
    assert label.threat_class is ThreatClass.BENIGN
    assert label.ring_id is None


def test_sealed_label_is_frozen() -> None:
    label = SealedLabel(
        entity_kind=EntityKind.ACCOUNT,
        entity_id="acct_000001",
        threat_class=ThreatClass.T01_COMMENT_SPAM_RING,
        ring_id="ring_0001",
        planted_ts=datetime(2024, 6, 1, tzinfo=IST),
        generator_params_hash="deadbeef",
    )
    with pytest.raises(FrozenInstanceError):
        label.ring_id = "different"  # type: ignore[misc]
