# SPDX-License-Identifier: MIT
"""The two product findings from Saif's phase-close review of a real queue.

Both were found by reading `ranked_queue.json`, not by any test, which is the
point worth recording: the suite was green and every assertion was true, and
the output was still not useful. A ranking where every case scores the same
and every rationale cites the same component passes a correctness test and
fails an analyst.

1. **The queue did not discriminate.** Flagging on undisclosed synthetic media
   caught 64 of 66 channels at an identical severity, so the ranking collapsed
   to a velocity sort and the real rings never reached the queue at all.
2. **Rationales were uninformative.** Every one cited `severity_class`,
   because it was the largest number on every row - and therefore the one
   thing that explained nothing about the ordering.

These tests pin the fixes. They are built on purpose-made fixtures rather than
the seed-42 build, so they state the rules rather than a snapshot of one
dataset.
"""

from datetime import datetime, timedelta

import duckdb
import numpy as np
import pytest

from ts_sentry.agents.triage.rationale import discriminating_component, render_expected_form
from ts_sentry.agents.triage.scorer import PriorityScore, ScoreComponent, score
from ts_sentry.data.enums import EntityKind, InfraSignalKind, ProvenanceSignal
from ts_sentry.data.population import Dataset
from ts_sentry.data.schema import AccountMeta, Channel, Comment, InfraHint, Video
from ts_sentry.data.store import persist_dataset
from ts_sentry.data.tz import IST
from ts_sentry.orchestrator.detection_stub import build_flagged_queue

_BASE = datetime(2024, 6, 1, 12, 0, tzinfo=IST)


def _persist(dataset: Dataset) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    persist_dataset(con, dataset)
    return con


def _accounts(n: int, prefix: str = "acct") -> tuple[AccountMeta, ...]:
    return tuple(
        AccountMeta(
            account_id=f"{prefix}-{i}",
            created_ts=_BASE,
            display_name=f"user{i}",
            is_verified=False,
            signup_ip_bucket="10.0.0.0/24",
            device_fingerprint_hint=None,
        )
        for i in range(n)
    )


def _channel(index: int, account: str) -> Channel:
    return Channel(
        channel_id=f"chan-{index}",
        account_id=account,
        created_ts=_BASE,
        display_name=f"Channel {index}",
        subscriber_count=10,
        description="a description",
    )


def _video(index: int, *, disclosed: bool) -> Video:
    return Video(
        video_id=f"vid-{index}",
        channel_id=f"chan-{index}",
        title=f"Video {index}",
        description="desc",
        published_ts=_BASE,
        duration_s=60,
        synthetic_media_disclosed=disclosed,
        provenance_signal=ProvenanceSignal.ABSENT,
    )


def _comment(
    cid: str, video: str, account: str, minute: int, template: str | None = None
) -> Comment:
    return Comment(
        comment_id=cid,
        video_id=video,
        account_id=account,
        parent_comment_id=None,
        posted_ts=_BASE + timedelta(minutes=minute),
        text=f"comment {cid}",
        template_id=template,
    )


# --------------------------------------------------------------------------
# Finding 1a: a near-universal property must not flag
# --------------------------------------------------------------------------


def test_undisclosed_synthetic_media_alone_does_not_flag() -> None:
    """The defect exactly: on the seed-42 build this held for 64 of 66
    channels, so flagging on it produced 25 identical cases and buried every
    real ring beneath them. A property held by almost everything says nothing
    about which case to open first.
    """
    dataset = Dataset(
        accounts=_accounts(2),
        channels=(_channel(0, "acct-0"), _channel(1, "acct-1")),
        videos=(_video(0, disclosed=False), _video(1, disclosed=False)),
        comments=(_comment("c-0", "vid-0", "acct-0", 0), _comment("c-1", "vid-1", "acct-1", 0)),
        engagement_events=(),
        infra_hints=(),
        sealed_labels=(),
    )

    queue = build_flagged_queue(_persist(dataset), rng=np.random.default_rng(42))

    assert queue == ()


def test_undisclosed_media_still_aggravates_an_already_flagged_case() -> None:
    """Dropped as a trigger, kept as a contributor: on a channel that is
    already coordinating it is a genuine aggravator."""
    hint = InfraHint(
        hint_id="h-0",
        subject_kind=EntityKind.CHANNEL,
        subject_id="chan-0",
        signal_type=InfraSignalKind.SHARED_UPLOAD_PATTERN,
        signal_value="pattern-x",
        observed_ts=_BASE,
    )
    dataset = Dataset(
        accounts=_accounts(1),
        channels=(_channel(0, "acct-0"),),
        videos=(_video(0, disclosed=False),),
        comments=(_comment("c-0", "vid-0", "acct-0", 0),),
        engagement_events=(),
        infra_hints=(hint,),
        sealed_labels=(),
    )

    queue = build_flagged_queue(_persist(dataset), rng=np.random.default_rng(42))

    assert len(queue) == 1
    assert queue[0].signals.undisclosed_videos == 1


# --------------------------------------------------------------------------
# Finding 1b: comment-side rings, gated on coordination
# --------------------------------------------------------------------------


def _inbound_dataset(sharing_accounts: int) -> Dataset:
    """A channel whose comment section contains N accounts sharing one device.

    Models the shape measured on the seed-42 build: every holder of a
    link-domain or shared-device signal owned no channel and commented on
    eleven, so a channel-centric queue could not see them at all.
    """
    commenters = _accounts(sharing_accounts, prefix="ring")
    hints = tuple(
        InfraHint(
            hint_id=f"h-{i}",
            subject_kind=EntityKind.ACCOUNT,
            subject_id=f"ring-{i}",
            signal_type=InfraSignalKind.SHARED_DEVICE,
            signal_value="dev-shared",
            observed_ts=_BASE,
        )
        for i in range(sharing_accounts)
    )
    return Dataset(
        accounts=(*_accounts(1), *commenters),
        channels=(_channel(0, "acct-0"),),
        videos=(_video(0, disclosed=True),),
        comments=tuple(
            _comment(f"c-{i}", "vid-0", f"ring-{i}", i) for i in range(sharing_accounts)
        ),
        engagement_events=(),
        infra_hints=hints,
        sealed_labels=(),
    )


def test_one_signal_holder_in_a_comment_section_is_not_coordination() -> None:
    """One spammer is one spammer. Flagging on a single holder would make the
    queue fire on any channel a bad account happened to visit."""
    queue = build_flagged_queue(_persist(_inbound_dataset(1)), rng=np.random.default_rng(42))

    assert queue == ()


def test_two_holders_sharing_one_value_is_coordination_and_flags() -> None:
    queue = build_flagged_queue(_persist(_inbound_dataset(2)), rng=np.random.default_rng(42))

    assert len(queue) == 1
    assert queue[0].signals.inbound_signals == {InfraSignalKind.SHARED_DEVICE.value: 2}
    assert queue[0].severity_class == pytest.approx(0.8)


# --------------------------------------------------------------------------
# Finding 1c: recidivism has a basis again
# --------------------------------------------------------------------------


def test_recidivism_measures_pattern_persistence_across_days() -> None:
    """Measured before it was changed: on the seed-42 build every subject
    carries exactly one hint and every account owns exactly one channel, so
    counting a subject's own observation days is structurally zero forever.
    Persistence of the shared pattern is the signal the data does support.
    """
    hints = tuple(
        InfraHint(
            hint_id=f"h-{i}",
            subject_kind=EntityKind.ACCOUNT,
            subject_id=f"ring-{i}",
            signal_type=InfraSignalKind.SHARED_DEVICE,
            signal_value="dev-shared",
            observed_ts=_BASE + timedelta(days=i),  # one holder per day
        )
        for i in range(3)
    )
    dataset = Dataset(
        accounts=(*_accounts(1), *_accounts(3, prefix="ring")),
        channels=(_channel(0, "acct-0"),),
        videos=(_video(0, disclosed=True),),
        comments=tuple(_comment(f"c-{i}", "vid-0", f"ring-{i}", i) for i in range(3)),
        engagement_events=(),
        infra_hints=hints,
        sealed_labels=(),
    )

    queue = build_flagged_queue(_persist(dataset), rng=np.random.default_rng(42))

    assert queue[0].signals.pattern_days == 3
    assert queue[0].recidivism > 0.0


def test_a_pattern_seen_once_carries_no_recidivism() -> None:
    """Recidivism means repeat. One observation is not a repeat, so the first
    day does not count towards it."""
    queue = build_flagged_queue(_persist(_inbound_dataset(2)), rng=np.random.default_rng(42))

    assert queue[0].signals.pattern_days == 1
    assert queue[0].recidivism == 0.0


# --------------------------------------------------------------------------
# Finding 2: rationales cite what actually differentiates
# --------------------------------------------------------------------------


def _queue(*rows: tuple[float, float, float, float]) -> list[PriorityScore]:
    return [
        score(
            f"case-{i:04d}",
            severity_class=row[0],
            spread=row[1],
            velocity=row[2],
            recidivism=row[3],
        )
        for i, row in enumerate(rows)
    ]


def test_the_cited_component_is_the_differentiator_not_the_largest() -> None:
    """The defect exactly.

    Severity is the largest component on every row here and identical on every
    row, so citing it explains nothing about the ordering. Velocity is the
    only thing that moved, so velocity is what gets cited.
    """
    scores = _queue((0.9, 0.1, 0.9, 0.1), (0.9, 0.1, 0.2, 0.1), (0.9, 0.1, 0.1, 0.1))

    assert discriminating_component(scores, 0) is ScoreComponent.VELOCITY


def test_a_case_that_stands_out_on_spread_cites_spread() -> None:
    scores = _queue((0.5, 0.9, 0.3, 0.1), (0.5, 0.1, 0.3, 0.1), (0.5, 0.1, 0.3, 0.1))

    assert discriminating_component(scores, 0) is ScoreComponent.SPREAD


def test_a_uniform_neighbourhood_falls_back_to_the_widest_component() -> None:
    """Rows 1 and 2 are identical, so the local comparison says nothing. The
    queue-wide spread still does."""
    scores = _queue((0.9, 0.1, 0.5, 0.1), (0.5, 0.1, 0.5, 0.1), (0.5, 0.1, 0.5, 0.1))

    assert discriminating_component(scores, 1) is ScoreComponent.SEVERITY_CLASS


def test_a_wholly_uniform_queue_falls_back_to_the_largest_weighted_component() -> None:
    """Total, never raising on a degenerate queue: if nothing differentiates,
    cite the component carrying the most weight."""
    scores = _queue((0.5, 0.5, 0.5, 0.5), (0.5, 0.5, 0.5, 0.5))

    assert discriminating_component(scores, 0) is ScoreComponent.SEVERITY_CLASS


def test_a_single_row_queue_is_handled() -> None:
    assert (
        discriminating_component(_queue((0.4, 0.2, 0.1, 0.0)), 0) is ScoreComponent.SEVERITY_CLASS
    )


def test_an_empty_queue_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ValueError, match="empty queue"):
        discriminating_component([], 0)


def test_the_menu_names_the_distinguishing_component_per_case() -> None:
    """The guidance reaches the model as part of the citation menu, and the
    full legal set is still listed: this steers, it does not constrain."""
    scores = _queue((0.9, 0.1, 0.9, 0.1), (0.9, 0.1, 0.2, 0.1))

    menu = render_expected_form(scores).splitlines()

    assert "most distinguishing: [case-0000:velocity]" in menu[0]
    for component in ScoreComponent:
        assert f"[case-0000:{component.value}]" in menu[0]


def test_the_guidance_does_not_narrow_what_the_verifier_accepts() -> None:
    """An informativeness fix in the rationale builder, not a verifier change.
    Any resolvable citation still passes."""
    from ts_sentry.orchestrator.rationale_check import verify_rationales

    scores = _queue((0.9, 0.1, 0.9, 0.1), (0.9, 0.1, 0.2, 0.1))
    assert discriminating_component(scores, 0) is ScoreComponent.VELOCITY

    result = verify_rationales(
        scores,
        {
            "case-0000": "case-0000: cited on [case-0000:recidivism] instead",
            "case-0001": "case-0001: [case-0001:spread]",
        },
    )

    assert result.all_passed
