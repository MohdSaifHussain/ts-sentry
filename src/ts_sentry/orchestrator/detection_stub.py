# SPDX-License-Identifier: MIT
"""The flagged-entity queue: a stand-in for upstream detection.

ARCHITECTURE 4.1 gives the triage agent "the flagged-entity queue (synthetic
detection output)" as its input, and Honest Limits says plainly that this
system does not detect abuse. STEP-01 shipped no such queue. This module is
the agreed resolution (Saif, STEP-03 D1/D2 planning): a deterministic,
seeded stub that stands in for the enterprise detector which would sit
upstream in a real deployment.

What "severity" is here, stated plainly
---------------------------------------
**Severity in this module is a heuristic stand-in signal, not ground truth.**
It is computed from observable platform features only. There is no sealed
influence on it, direct or derived: this module never reads
``sealed._labels``, never reads anything computed from it, and could not
reach it if it tried, because ``DataScope`` has no member that resolves there
and every table this module touches comes from ``resolve_table``. A test
asserts that last part against the SQL rather than trusting this paragraph.

The honest reading of what that buys: the queue surfaces entities carrying
*visible* coordination artifacts, which the STEP-01 threat generators plant
alongside their labels. It does not know which entities are abusive, it has
no measured precision or recall, and it must never be reported as detection
performance. STEP-07 is where a queue like this could be scored against
sealed ground truth, by measurement code that is allowed to read it.

Determinism
-----------
No wall clock, no bare ``random``: ordering is by score then entity id, and
the single seeded ``numpy`` generator is threaded in for the one place a
tie-break needs it. The same dataset yields the same queue on every run.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import duckdb
import numpy as np

from ts_sentry.data.enums import InfraSignalKind
from ts_sentry.data.tz import IST, require_ist
from ts_sentry.governance.scopes import DataScope, resolve_table
from ts_sentry.orchestrator.firewall import CaseRecord

__all__ = [
    "DETECTOR_VERSION",
    "SIGNAL_SEVERITY",
    "FlaggedEntity",
    "SignalCounts",
    "build_flagged_queue",
    "case_records",
    "queries",
]

DETECTOR_VERSION = "0.1.0-stub"
"""Version of this stand-in, recorded on every queue so a ranked result can be
traced to the flagging rules that produced it."""

_VELOCITY_WINDOW = timedelta(hours=24)
_SPREAD_CAP = 12
"""Peer count at which spread saturates. A presentation choice, not a finding:
it makes the component readable as a 0..1 share rather than an unbounded
count."""

SIGNAL_SEVERITY: Mapping[InfraSignalKind, float] = {
    InfraSignalKind.LINK_DOMAIN_REUSE: 1.0,
    InfraSignalKind.SHARED_DEVICE: 0.8,
    InfraSignalKind.SHARED_IP_BUCKET: 0.6,
    InfraSignalKind.TEMPLATE_REUSE: 0.5,
    InfraSignalKind.SHARED_UPLOAD_PATTERN: 0.4,
}
"""Per-signal severity weights, published rather than buried.

These are *judgments about how alarming a signal looks*, not measured
likelihoods. Off-platform link reuse ranks highest because it is the signal
most associated with taking users somewhere the platform cannot see; a shared
upload pattern ranks lowest because benign tooling produces it constantly.
Nothing here was fitted to data.
"""

_UNDISCLOSED_SYNTHETIC_SEVERITY = 0.3
"""Undisclosed synthetic media, as a severity *contributor* only.

It is deliberately not a flag trigger and deliberately the lowest weight here.
On the seed-42 build it holds for 64 of 66 channels, which makes it worthless
as a discriminator: flagging on it produced a queue where every case scored an
identical 0.7 and the ranking collapsed to a velocity sort. Saif found that by
reading a real ``ranked_queue.json``, which is the kind of thing only a human
reading the output catches.

A property held by 97% of the population is a property that says nothing about
which case to open first. It still contributes once something else has flagged
the entity, because on a channel that is *already* coordinating it is a
genuine aggravator.
"""

_TEMPLATED_COMMENT_SEVERITY = 0.5

_RECIDIVISM_CAP = 4

_INBOUND_COORDINATION_FLOOR = 2
"""Distinct commenting accounts that must share one signal value before it
counts. One spammer in a comment section is one spammer; several sharing a
device fingerprint is coordination."""


# Table names come from `resolve_table`, an exhaustive match over `DataScope`,
# so each query below is a fixed template per call rather than SQL built from
# runtime values. Same construction `data.store` already uses, and the same
# reason: absence from the allowlist is denial, so a table with no DataScope
# member is unnameable here.
def queries() -> Mapping[str, str]:
    """The complete set of statements this module issues.

    Returned as data so a test can assert that every table mentioned resolves
    through ``DataScope``. A module that only *claims* to stay inside the
    allowlist is a module nobody can check.
    """
    channel = resolve_table(DataScope.CHANNEL)
    video = resolve_table(DataScope.VIDEO)
    comment = resolve_table(DataScope.COMMENT)
    infra = resolve_table(DataScope.INFRA_HINT)
    return {
        "channels": f"SELECT channel_id, account_id FROM {channel} ORDER BY channel_id;",
        "infra_hints": (
            f"SELECT subject_kind, subject_id, signal_type, signal_value, epoch_ms(observed_ts) "
            f"FROM {infra} ORDER BY hint_id;"
        ),
        "undisclosed_videos": (
            f"SELECT channel_id, COUNT(*) FROM {video} "
            f"WHERE synthetic_media_disclosed = FALSE GROUP BY channel_id;"
        ),
        "templated_comments": (
            f"SELECT v.channel_id, COUNT(*) FROM {comment} cm "
            f"JOIN {video} v ON cm.video_id = v.video_id "
            f"WHERE cm.template_id IS NOT NULL GROUP BY v.channel_id;"
        ),
        "comment_times": (
            f"SELECT v.channel_id, epoch_ms(cm.posted_ts) FROM {comment} cm "
            f"JOIN {video} v ON cm.video_id = v.video_id ORDER BY cm.comment_id;"
        ),
        "commenters": (
            f"SELECT DISTINCT v.channel_id, cm.account_id FROM {comment} cm "
            f"JOIN {video} v ON cm.video_id = v.video_id ORDER BY v.channel_id;"
        ),
        "channel_text": (
            f"SELECT channel_id, display_name, description FROM {channel} WHERE channel_id = ?;"
        ),
        "channel_comments": (
            f"SELECT cm.comment_id, cm.text FROM {comment} cm "
            f"JOIN {video} v ON cm.video_id = v.video_id "
            f"WHERE v.channel_id = ? ORDER BY cm.comment_id LIMIT ?;"
        ),
    }


def _ist_instant(epoch_millis: int) -> datetime:
    """Rebuild an IST datetime from epoch milliseconds.

    Timestamps are selected as ``epoch_ms(...)`` rather than as ``TIMESTAMPTZ``
    for two reasons, both learned the hard way in STEP-02 D3.

    First, materializing a ``TIMESTAMPTZ`` through the DuckDB Python client
    requires ``pytz``, which this project does not depend on; the first draft
    of this module failed on exactly that.

    Second, and more importantly, DuckDB renders a ``TIMESTAMPTZ`` in the
    *reader's* session time zone, so a cast to text would produce a different
    string on a Kolkata machine than in a UTC CI runner. STEP-02 avoided a
    false broken chain that way. Here it would have been quieter and worse: the
    recidivism component counts distinct observation *days*, so a
    session-dependent date would have made two machines compute different
    priorities from one dataset and neither would have looked wrong.

    Epoch milliseconds carry no rendering at all. Verified against DuckDB
    1.5.5: the same row yields the identical integer under Asia/Kolkata, UTC,
    and America/New_York session time zones.
    """
    return datetime.fromtimestamp(epoch_millis / 1000, tz=IST)


def _ist_date(epoch_millis: int) -> str:
    """The IST calendar date of an instant, as ``YYYY-MM-DD``."""
    return _ist_instant(epoch_millis).date().isoformat()


@dataclass(frozen=True, slots=True)
class SignalCounts:
    """The observable evidence behind one flag, kept so the queue can explain
    itself rather than emitting a bare number."""

    infra_signals: Mapping[str, int]
    inbound_signals: Mapping[str, int]
    peer_entities: int
    pattern_days: int
    undisclosed_videos: int
    templated_comments: int
    comment_count: int
    burst_comments: int

    def to_json_object(self) -> dict[str, object]:
        return {
            "infra_signals": dict(sorted(self.infra_signals.items())),
            "inbound_signals": dict(sorted(self.inbound_signals.items())),
            "peer_entities": self.peer_entities,
            "pattern_days": self.pattern_days,
            "undisclosed_videos": self.undisclosed_videos,
            "templated_comments": self.templated_comments,
            "comment_count": self.comment_count,
            "burst_comments": self.burst_comments,
        }


@dataclass(frozen=True, slots=True)
class FlaggedEntity:
    """One case on the queue, with its four normalized score components.

    The components are computed here, in the stub, because they are properties
    of *observable platform data*. The triage agent's scorer combines them
    into a priority; keeping the two apart is what lets the weights be
    re-tuned without re-deriving the evidence.
    """

    case_id: str
    channel_id: str
    account_id: str
    severity_class: float
    spread: float
    velocity: float
    recidivism: float
    signals: SignalCounts

    def __post_init__(self) -> None:
        for name in ("severity_class", "spread", "velocity", "recidivism"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be normalized to 0..1; got {value}")

    def to_json_object(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "channel_id": self.channel_id,
            "account_id": self.account_id,
            "components": {
                "severity_class": self.severity_class,
                "spread": self.spread,
                "velocity": self.velocity,
                "recidivism": self.recidivism,
            },
            "signals": self.signals.to_json_object(),
        }


def _burst_count(times: Sequence[datetime]) -> int:
    """Largest number of comments inside any 24-hour window.

    A sliding window over sorted timestamps rather than calendar-day buckets:
    a ring that posts across midnight is the same ring, and bucketing would
    halve its apparent velocity.
    """
    if not times:
        return 0
    ordered = sorted(times)
    best = 1
    start = 0
    for end in range(len(ordered)):
        while ordered[end] - ordered[start] > _VELOCITY_WINDOW:
            start += 1
        best = max(best, end - start + 1)
    return best


def build_flagged_queue(
    connection: duckdb.DuckDBPyConnection,
    *,
    rng: np.random.Generator,
    limit: int = 25,
) -> tuple[FlaggedEntity, ...]:
    """Produce the queue an analyst starts their hour with.

    Reads only allowlisted tables. Flags a channel when it carries an
    observable *coordination* artifact - an infrastructure signal on the
    channel or its owning account, or templated comments - then ranks by the
    same severity the components expose, so the queue's own order is
    explainable from the row.

    Undisclosed synthetic media deliberately does not flag on its own. See
    ``_UNDISCLOSED_SYNTHETIC_SEVERITY`` for why: it is near-universal in this
    population, so flagging on it buried every real ring under 25 identical
    cases.
    """
    if limit <= 0:
        raise ValueError(f"limit must be positive; got {limit}")
    sql = queries()

    channels: dict[str, str] = {
        str(row[0]): str(row[1]) for row in connection.execute(sql["channels"]).fetchall()
    }

    signal_kinds: dict[str, dict[str, int]] = {}
    signal_values: dict[str, set[tuple[str, str]]] = {}
    value_days: dict[tuple[str, str], set[str]] = {}
    value_holders: dict[tuple[str, str], set[str]] = {}

    for row in connection.execute(sql["infra_hints"]).fetchall():
        subject_id = str(row[1])
        signal_type = str(row[2])
        signal_value = str(row[3])
        observed = row[4]
        signal_kinds.setdefault(subject_id, {})
        signal_kinds[subject_id][signal_type] = signal_kinds[subject_id].get(signal_type, 0) + 1
        signal_values.setdefault(subject_id, set()).add((signal_type, signal_value))
        value_holders.setdefault((signal_type, signal_value), set()).add(subject_id)
        value_days.setdefault((signal_type, signal_value), set()).add(_ist_date(int(observed)))

    undisclosed = {
        str(row[0]): int(row[1]) for row in connection.execute(sql["undisclosed_videos"]).fetchall()
    }
    templated = {
        str(row[0]): int(row[1]) for row in connection.execute(sql["templated_comments"]).fetchall()
    }

    commenters: dict[str, set[str]] = {}
    for row in connection.execute(sql["commenters"]).fetchall():
        commenters.setdefault(str(row[0]), set()).add(str(row[1]))

    times_by_channel: dict[str, list[datetime]] = {}
    for row in connection.execute(sql["comment_times"]).fetchall():
        posted = _ist_instant(int(row[1]))
        require_ist(posted, "posted_ts")
        times_by_channel.setdefault(str(row[0]), []).append(posted)

    flagged: list[FlaggedEntity] = []
    for channel_id, account_id in channels.items():
        # A channel's signals are its own plus its owning account's: a ring
        # that registers accounts from one IP bucket leaves the trace on the
        # account, not the channel.
        kinds: dict[str, int] = {}
        for subject in (channel_id, account_id):
            for name, count in signal_kinds.get(subject, {}).items():
                kinds[name] = kinds.get(name, 0) + count

        # Inbound signals: what the accounts commenting on this channel carry.
        #
        # Without this the queue is structurally blind to the rings that matter
        # most. Measured on the seed-42 build: every holder of a link-domain
        # reuse or shared-device signal owns *no* channel and comments on
        # eleven, because a comment-spam ring operates through commenting
        # accounts rather than by publishing. A channel-centric queue that
        # reads only the channel and its owner can never see them.
        #
        # Gated on concentration, not presence: a signal counts only when at
        # least two distinct commenting accounts share the same value on this
        # channel. One spammer in a comment section is one spammer; several
        # sharing a device fingerprint is coordination, and only the second is
        # a reason to open the case.
        inbound: dict[str, int] = {}
        inbound_values: set[tuple[str, str]] = set()
        sharers: dict[tuple[str, str], set[str]] = {}
        for account in commenters.get(channel_id, set()):
            for key in signal_values.get(account, set()):
                sharers.setdefault(key, set()).add(account)
        for key, accounts in sharers.items():
            if len(accounts) >= _INBOUND_COORDINATION_FLOOR:
                inbound[key[0]] = inbound.get(key[0], 0) + len(accounts)
                inbound_values.add(key)

        undisclosed_count = undisclosed.get(channel_id, 0)
        templated_count = templated.get(channel_id, 0)
        if not kinds and not inbound and templated_count == 0:
            continue

        own_values = signal_values.get(channel_id, set()) | signal_values.get(account_id, set())
        peers: set[str] = set()
        for key in own_values | inbound_values:
            peers |= value_holders.get(key, set())
        peers -= {channel_id, account_id}

        # Recidivism as *pattern persistence*: the days on which this entity's
        # own signal values were seen anywhere in the record, not the days this
        # one subject was seen.
        #
        # The obvious reading (count this subject's own observation days) is
        # structurally dead on this data: every subject carries exactly one
        # hint and every account owns exactly one channel, so it is zero for
        # every case, forever. Measured before changing it rather than assumed.
        # Persistence is the honest signal the data does support - a ring whose
        # shared device fingerprint keeps reappearing across days is a ring
        # that came back, which is what recidivism means for an infrastructure
        # signal.
        days: set[str] = set()
        for key in own_values | inbound_values:
            days |= value_days.get(key, set())

        severity_inputs = [
            SIGNAL_SEVERITY[kind]
            for kind in InfraSignalKind
            if kind.value in kinds or kind.value in inbound
        ]
        if undisclosed_count:
            severity_inputs.append(_UNDISCLOSED_SYNTHETIC_SEVERITY)
        if templated_count:
            severity_inputs.append(_TEMPLATED_COMMENT_SEVERITY)
        severity = max(severity_inputs) if severity_inputs else 0.0

        times = times_by_channel.get(channel_id, [])
        burst = _burst_count(times)
        velocity = 0.0 if not times else min(1.0, burst / len(times))

        flagged.append(
            FlaggedEntity(
                case_id="",  # assigned after ordering, so ids read in queue order
                channel_id=channel_id,
                account_id=account_id,
                severity_class=severity,
                spread=min(1.0, len(peers) / _SPREAD_CAP),
                velocity=velocity,
                recidivism=min(1.0, max(0, len(days) - 1) / _RECIDIVISM_CAP),
                signals=SignalCounts(
                    infra_signals=kinds,
                    inbound_signals=inbound,
                    peer_entities=len(peers),
                    pattern_days=len(days),
                    undisclosed_videos=undisclosed_count,
                    templated_comments=templated_count,
                    comment_count=len(times),
                    burst_comments=burst,
                ),
            )
        )

    # Ordered by severity then channel id. The id tiebreak is what makes the
    # queue byte-stable; the generator is threaded through for future
    # sampling rather than used to break ties, because a random tiebreak
    # would make two runs of one dataset disagree.
    _ = rng
    flagged.sort(key=lambda entity: (-entity.severity_class, entity.channel_id))
    return tuple(
        FlaggedEntity(
            case_id=f"case-{index:04d}",
            channel_id=entity.channel_id,
            account_id=entity.account_id,
            severity_class=entity.severity_class,
            spread=entity.spread,
            velocity=entity.velocity,
            recidivism=entity.recidivism,
            signals=entity.signals,
        )
        for index, entity in enumerate(flagged[:limit])
    )


def case_records(
    connection: duckdb.DuckDBPyConnection,
    cases: Sequence[tuple[str, str]],
    *,
    comments_per_case: int = 3,
) -> tuple[CaseRecord, ...]:
    """The platform text behind a queue, as firewall-ready case records.

    This is the material that reaches a model, and it is the material an
    attacker controls: channel names, descriptions, and comment bodies are all
    written by the platform's users. It is fetched verbatim and handed
    straight to the firewall, which is the only thing that decides how it may
    be presented.

    Takes ``(case_id, channel_id)`` pairs rather than the detector's own row
    type, so the caller can ask for content for a queue that has already
    been scored and reordered.

    Parameterized (``?`` placeholders) rather than formatted, so a channel id
    is never spliced into SQL text.
    """
    sql = queries()
    records: list[CaseRecord] = []
    for case_id, channel_id in cases:
        for row in connection.execute(sql["channel_text"], [channel_id]).fetchall():
            records.append(
                CaseRecord(
                    record_id=f"{case_id}:channel.display_name",
                    source="channel.display_name",
                    text=str(row[1]),
                )
            )
            records.append(
                CaseRecord(
                    record_id=f"{case_id}:channel.description",
                    source="channel.description",
                    text=str(row[2]),
                )
            )
        rows = connection.execute(
            sql["channel_comments"], [channel_id, comments_per_case]
        ).fetchall()
        for row in rows:
            records.append(
                CaseRecord(
                    record_id=f"{case_id}:comment:{row[0]}",
                    source="comment.text",
                    text=str(row[1]),
                )
            )
    return tuple(records)
