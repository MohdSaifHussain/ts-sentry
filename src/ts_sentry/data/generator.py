# SPDX-License-Identifier: MIT
"""Seeded synthetic platform builder (D1) plus threat-planting orchestration
(D3 wiring).

Determinism contract: a single ``numpy.random.Generator``, seeded once in
``build_dataset``, is threaded explicitly through every generation function
below - including threat planting - as the ``rng`` parameter. No function
reads a module-level or global random state, and nothing here uses
``random`` or wall-clock entropy - the same seed and scale always produce
byte-identical rows (STEP-01 3.1).

Base-population generation (account_meta -> channel -> infra_hint -> video
-> comment -> engagement_event, in FK-safe order) is frozen benign content;
:func:`build_dataset` then runs the T-01..T-07 threat planners (D3) against
it with the *same* generator instance, and merges both into the full
:class:`~ts_sentry.data.population.Dataset`. DuckDB persistence and Parquet
export (D4), the AnalystKit quality gate (D6), and the build manifest (D7)
extend this separately.
"""

from datetime import datetime, timedelta

import numpy as np

from ts_sentry.data.enums import EngagementKind, EntityKind, InfraSignalKind, ProvenanceSignal
from ts_sentry.data.population import BasePopulation, BuildConfig, Dataset
from ts_sentry.data.schema import AccountMeta, Channel, Comment, EngagementEvent, InfraHint, Video
from ts_sentry.data.threats.plan import plant_all_threats
from ts_sentry.data.tz import WINDOW_SECONDS, WINDOW_START

# Base dataset-size constants; `scale` is an integer multiplier applied to
# each. Documented in docs/data-dictionary.md (D5).
BASE_ACCOUNTS = 400
BASE_CHANNELS = 50
MEAN_VIDEOS_PER_CHANNEL = 8
MEAN_COMMENTS_PER_VIDEO = 15
MEAN_VIEWS_PER_VIDEO = 50
MEAN_LIKES_PER_VIDEO = 12
MEAN_DISLIKES_PER_VIDEO = 2
MEAN_SHARES_PER_VIDEO = 4
MEAN_REPORTS_PER_VIDEO = 1
MEAN_SUBSCRIBES_PER_CHANNEL = 30
INFRA_HINT_ACCOUNT_RATE = 0.05  # fraction of accounts with a (mostly benign) infra hint
IP_BUCKET_POOL_SIZE = 40  # small pool => natural, non-adversarial IP overlap

_FIRST_NAMES = (
    "Asha",
    "Rohan",
    "Maya",
    "Kabir",
    "Zara",
    "Dev",
    "Priya",
    "Aarav",
    "Isha",
    "Vikram",
    "Neha",
    "Arjun",
    "Meera",
    "Sanjay",
    "Tara",
    "Karan",
    "Divya",
    "Rahul",
    "Anya",
    "Nikhil",
)
_LAST_WORDS = (
    "Studio",
    "Vlogs",
    "Media",
    "Talks",
    "Reviews",
    "Daily",
    "Live",
    "Creations",
    "Channel",
    "TV",
    "Official",
    "World",
    "Hub",
    "Now",
    "Cast",
    "Lab",
    "Works",
    "Zone",
    "Central",
    "Network",
)
_TOPIC_WORDS = (
    "travel",
    "cooking",
    "finance",
    "gaming",
    "tech",
    "music",
    "fitness",
    "news",
    "comedy",
    "education",
    "gardening",
    "fashion",
    "photography",
    "cars",
    "sports",
)


def _ist_timestamp(rng: np.random.Generator) -> datetime:
    offset_s = int(rng.integers(0, WINDOW_SECONDS))
    return WINDOW_START + timedelta(seconds=offset_s)


def generate_accounts(rng: np.random.Generator, config: BuildConfig) -> tuple[AccountMeta, ...]:
    n = BASE_ACCOUNTS * config.scale
    accounts = []
    for i in range(n):
        first = _FIRST_NAMES[rng.integers(0, len(_FIRST_NAMES))]
        suffix = int(rng.integers(0, 10_000))
        accounts.append(
            AccountMeta(
                account_id=f"acct_{i:07d}",
                created_ts=_ist_timestamp(rng),
                display_name=f"{first}{suffix}",
                is_verified=bool(rng.random() < 0.02),
                signup_ip_bucket=f"ipb_{int(rng.integers(0, IP_BUCKET_POOL_SIZE)):03d}",
                device_fingerprint_hint=None,
            )
        )
    return tuple(accounts)


def generate_channels(
    rng: np.random.Generator, config: BuildConfig, accounts: tuple[AccountMeta, ...]
) -> tuple[Channel, ...]:
    n = BASE_CHANNELS * config.scale
    owner_idx = rng.choice(len(accounts), size=n, replace=False)
    channels = []
    for i, idx in enumerate(owner_idx):
        owner = accounts[int(idx)]
        last = _LAST_WORDS[rng.integers(0, len(_LAST_WORDS))]
        channels.append(
            Channel(
                channel_id=f"chan_{i:06d}",
                account_id=owner.account_id,
                created_ts=_ist_timestamp(rng),
                display_name=f"{owner.display_name} {last}",
                subscriber_count=int(rng.integers(0, 50_000)),
                description=f"Channel about {_TOPIC_WORDS[rng.integers(0, len(_TOPIC_WORDS))]}.",
            )
        )
    return tuple(channels)


def generate_infra_hints(
    rng: np.random.Generator,
    accounts: tuple[AccountMeta, ...],
) -> tuple[InfraHint, ...]:
    hints = []
    hint_i = 0
    for account in accounts:
        if rng.random() < INFRA_HINT_ACCOUNT_RATE:
            hints.append(
                InfraHint(
                    hint_id=f"infra_{hint_i:06d}",
                    subject_kind=EntityKind.ACCOUNT,
                    subject_id=account.account_id,
                    signal_type=InfraSignalKind.SHARED_IP_BUCKET,
                    signal_value=account.signup_ip_bucket,
                    observed_ts=_ist_timestamp(rng),
                )
            )
            hint_i += 1
    return tuple(hints)


def generate_videos(rng: np.random.Generator, channels: tuple[Channel, ...]) -> tuple[Video, ...]:
    videos = []
    vid_i = 0
    for channel in channels:
        n_videos = int(rng.poisson(MEAN_VIDEOS_PER_CHANNEL))
        for _ in range(n_videos):
            topic = _TOPIC_WORDS[rng.integers(0, len(_TOPIC_WORDS))]
            disclosed = bool(rng.random() < 0.5)
            videos.append(
                Video(
                    video_id=f"vid_{vid_i:07d}",
                    channel_id=channel.channel_id,
                    title=f"My {topic} video #{vid_i}",
                    description=f"A video about {topic}.",
                    published_ts=_ist_timestamp(rng),
                    duration_s=int(rng.integers(30, 1800)),
                    synthetic_media_disclosed=disclosed,
                    provenance_signal=(
                        ProvenanceSignal.PRESENT if disclosed else ProvenanceSignal.UNKNOWN
                    ),
                )
            )
            vid_i += 1
    return tuple(videos)


def generate_comments(
    rng: np.random.Generator,
    videos: tuple[Video, ...],
    accounts: tuple[AccountMeta, ...],
) -> tuple[Comment, ...]:
    comments = []
    cmt_i = 0
    for video in videos:
        n_comments = int(rng.poisson(MEAN_COMMENTS_PER_VIDEO))
        video_comment_ids: list[str] = []
        for _ in range(n_comments):
            author = accounts[int(rng.integers(0, len(accounts)))]
            parent_id = None
            if video_comment_ids and rng.random() < 0.2:
                parent_id = video_comment_ids[int(rng.integers(0, len(video_comment_ids)))]
            comment_id = f"cmt_{cmt_i:08d}"
            comments.append(
                Comment(
                    comment_id=comment_id,
                    video_id=video.video_id,
                    account_id=author.account_id,
                    parent_comment_id=parent_id,
                    posted_ts=_ist_timestamp(rng),
                    text=f"Great {_TOPIC_WORDS[rng.integers(0, len(_TOPIC_WORDS))]} content!",
                    template_id=None,
                )
            )
            video_comment_ids.append(comment_id)
            cmt_i += 1
    return tuple(comments)


def _sample_events_for_video(
    rng: np.random.Generator,
    video: Video,
    accounts: tuple[AccountMeta, ...],
    kind: EngagementKind,
    mean_count: float,
    event_i: int,
) -> tuple[list[EngagementEvent], int]:
    events = []
    n = int(rng.poisson(mean_count))
    for _ in range(n):
        actor = accounts[int(rng.integers(0, len(accounts)))]
        events.append(
            EngagementEvent(
                event_id=f"eng_{event_i:09d}",
                kind=kind,
                account_id=actor.account_id,
                video_id=video.video_id,
                channel_id=None,
                ts_ist=_ist_timestamp(rng),
                session_id=None,
            )
        )
        event_i += 1
    return events, event_i


def generate_engagement_events(
    rng: np.random.Generator,
    videos: tuple[Video, ...],
    channels: tuple[Channel, ...],
    accounts: tuple[AccountMeta, ...],
) -> tuple[EngagementEvent, ...]:
    events: list[EngagementEvent] = []
    event_i = 0
    for video in videos:
        per_kind = (
            (EngagementKind.VIEW, MEAN_VIEWS_PER_VIDEO),
            (EngagementKind.LIKE, MEAN_LIKES_PER_VIDEO),
            (EngagementKind.DISLIKE, MEAN_DISLIKES_PER_VIDEO),
            (EngagementKind.SHARE, MEAN_SHARES_PER_VIDEO),
            (EngagementKind.REPORT, MEAN_REPORTS_PER_VIDEO),
        )
        for kind, mean_count in per_kind:
            new_events, event_i = _sample_events_for_video(
                rng, video, accounts, kind, mean_count, event_i
            )
            events.extend(new_events)

    for channel in channels:
        n_subs = int(rng.poisson(MEAN_SUBSCRIBES_PER_CHANNEL))
        for _ in range(n_subs):
            actor = accounts[int(rng.integers(0, len(accounts)))]
            events.append(
                EngagementEvent(
                    event_id=f"eng_{event_i:09d}",
                    kind=EngagementKind.SUBSCRIBE,
                    account_id=actor.account_id,
                    video_id=None,
                    channel_id=channel.channel_id,
                    ts_ist=_ist_timestamp(rng),
                    session_id=None,
                )
            )
            event_i += 1
    return tuple(events)


def build_base_population(rng: np.random.Generator, config: BuildConfig) -> BasePopulation:
    """Generate the deterministic benign base population for one build."""
    accounts = generate_accounts(rng, config)
    channels = generate_channels(rng, config, accounts)
    infra_hints = generate_infra_hints(rng, accounts)
    videos = generate_videos(rng, channels)
    comments = generate_comments(rng, videos, accounts)
    engagement_events = generate_engagement_events(rng, videos, channels, accounts)
    return BasePopulation(
        accounts=accounts,
        channels=channels,
        infra_hints=infra_hints,
        videos=videos,
        comments=comments,
        engagement_events=engagement_events,
    )


def build_dataset(config: BuildConfig) -> Dataset:
    """Entry point for the D1+D3 in-memory build.

    A single ``rng`` is seeded once here and threaded through both base
    population generation and threat planting, so the full dataset -
    benign content and planted abuse alike - is byte-stable per seed.
    DuckDB persistence, Parquet export (D4), the AnalystKit quality gate
    (D6), and the build manifest (D7) all consume this in-memory
    :class:`Dataset` rather than extending this function.
    """
    rng = np.random.default_rng(config.seed)
    base = build_base_population(rng, config)
    planted = plant_all_threats(rng, base)
    return Dataset(
        accounts=base.accounts + planted.accounts,
        channels=base.channels + planted.channels,
        videos=base.videos + planted.videos,
        comments=base.comments + planted.comments,
        engagement_events=base.engagement_events + planted.engagement_events,
        infra_hints=base.infra_hints + planted.infra_hints,
        sealed_labels=planted.sealed_labels,
    )
