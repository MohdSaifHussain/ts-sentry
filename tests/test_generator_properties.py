# SPDX-License-Identifier: MIT
"""STEP-01 3.5 hypothesis properties for the D1+D3 build pipeline:
(a) rebuild determinism, (b) referential integrity, (c) label completeness.
"""

from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from ts_sentry.data.enums import EntityKind, ThreatClass
from ts_sentry.data.generator import build_dataset
from ts_sentry.data.population import BuildConfig, Dataset

# STEP-01 3.4 / ARCHITECTURE 6.1: benign entities must be >= 97% of the
# labelable population. The per-class abuse budget targets 2% total
# (DEFAULT_TOTAL_ABUSE_FRACTION), so 97% is a safety margin, not the target.
_MIN_BENIGN_FRACTION = 0.97

# A scale=1 build is tens of thousands of rows; keep hypothesis example
# counts modest and disable the default deadline so slower CI machines
# don't get flagged as a hypothesis failure rather than a real one.
_SETTINGS = settings(max_examples=8, deadline=None)

_IST_OFFSET = timedelta(hours=5, minutes=30)


@_SETTINGS
@given(seed=st.integers(min_value=0, max_value=2**31 - 1))
def test_rebuild_determinism(seed: int) -> None:
    config = BuildConfig(seed=seed, scale=1)
    first = build_dataset(config)
    second = build_dataset(config)
    assert first == second


@_SETTINGS
@given(seed=st.integers(min_value=0, max_value=2**31 - 1))
def test_referential_integrity(seed: int) -> None:
    population = build_dataset(BuildConfig(seed=seed, scale=1))
    _assert_referential_integrity(population)


@_SETTINGS
@given(seed=st.integers(min_value=0, max_value=2**31 - 1))
def test_all_timestamps_are_ist_aware(seed: int) -> None:
    population = build_dataset(BuildConfig(seed=seed, scale=1))
    _assert_all_ist(population)


@_SETTINGS
@given(seed=st.integers(min_value=0, max_value=2**31 - 1))
def test_label_completeness(seed: int) -> None:
    dataset = build_dataset(BuildConfig(seed=seed, scale=1))
    _assert_label_completeness(dataset)


@_SETTINGS
@given(seed=st.integers(min_value=0, max_value=2**31 - 1))
def test_benign_majority(seed: int) -> None:
    dataset = build_dataset(BuildConfig(seed=seed, scale=1))
    benign = sum(1 for label in dataset.sealed_labels if label.threat_class is ThreatClass.BENIGN)
    assert benign / len(dataset.sealed_labels) >= _MIN_BENIGN_FRACTION


def _assert_label_completeness(dataset: Dataset) -> None:
    labeled_ids = {label.entity_id for label in dataset.sealed_labels}
    assert len(labeled_ids) == len(dataset.sealed_labels), "duplicate entity labeled more than once"

    entity_ids = (
        {a.account_id for a in dataset.accounts}
        | {c.channel_id for c in dataset.channels}
        | {v.video_id for v in dataset.videos}
        | {c.comment_id for c in dataset.comments}
    )
    assert entity_ids == labeled_ids


def _assert_all_ist(population: Dataset) -> None:
    for account in population.accounts:
        assert account.created_ts.utcoffset() == _IST_OFFSET
    for channel in population.channels:
        assert channel.created_ts.utcoffset() == _IST_OFFSET
    for video in population.videos:
        assert video.published_ts.utcoffset() == _IST_OFFSET
    for comment in population.comments:
        assert comment.posted_ts.utcoffset() == _IST_OFFSET
    for event in population.engagement_events:
        assert event.ts_ist.utcoffset() == _IST_OFFSET
    for hint in population.infra_hints:
        assert hint.observed_ts.utcoffset() == _IST_OFFSET


def _assert_referential_integrity(population: Dataset) -> None:
    account_ids = {a.account_id for a in population.accounts}
    channel_ids = {c.channel_id for c in population.channels}
    video_ids = {v.video_id for v in population.videos}
    comment_ids = {c.comment_id for c in population.comments}

    for channel in population.channels:
        assert channel.account_id in account_ids

    for video in population.videos:
        assert video.channel_id in channel_ids

    for comment in population.comments:
        assert comment.video_id in video_ids
        assert comment.account_id in account_ids
        if comment.parent_comment_id is not None:
            assert comment.parent_comment_id in comment_ids

    for event in population.engagement_events:
        assert event.account_id in account_ids
        if event.video_id is not None:
            assert event.video_id in video_ids
        if event.channel_id is not None:
            assert event.channel_id in channel_ids
        assert (event.video_id is None) != (event.channel_id is None)

    for hint in population.infra_hints:
        match hint.subject_kind:
            case EntityKind.ACCOUNT:
                assert hint.subject_id in account_ids
            case EntityKind.CHANNEL:
                assert hint.subject_id in channel_ids
            case EntityKind.VIDEO:
                assert hint.subject_id in video_ids
            case EntityKind.COMMENT:
                assert hint.subject_id in comment_ids
