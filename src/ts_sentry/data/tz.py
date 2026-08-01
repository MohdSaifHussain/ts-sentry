# SPDX-License-Identifier: MIT
"""Shared Asia/Kolkata (IST) constant and structural enforcement helper.

STEP-01 3.2: every timestamp in the synthetic schema must be timezone-aware
IST. A docstring saying so is not an invariant - ``require_ist`` is called
from every timestamp-bearing dataclass's ``__post_init__``
(``ts_sentry.data.schema``, ``ts_sentry.data.sealed``) so a naive or
wrong-offset datetime cannot be constructed at all.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# All row timestamps across the generator and threat modules are drawn from
# this fixed, deterministic window - never from wall-clock "now" - so
# rebuilds are byte-stable regardless of when they run.
WINDOW_START = datetime(2024, 1, 1, tzinfo=IST)
WINDOW_SECONDS = 2 * 365 * 24 * 3600  # ~2 years


def ist_from_epoch_ms(epoch_millis: int) -> datetime:
    """Rebuild an IST datetime from epoch milliseconds.

    Shared because two modules now need the identical conversion and a second
    spelling of it is a second chance to get it wrong. Timestamps are read out
    of DuckDB as ``epoch_ms(...)`` rather than as ``TIMESTAMPTZ`` or a text
    cast, for the reason STEP-02 D3 established and STEP-03 D5 hit again:
    DuckDB renders a ``TIMESTAMPTZ`` in the *reader's* session time zone, so a
    cast to text would produce different evidence on a Kolkata machine than in
    a UTC CI runner, and neither would look wrong. Epoch milliseconds carry no
    rendering at all.
    """
    return datetime.fromtimestamp(epoch_millis / 1000, tz=IST)


def require_ist(value: datetime, field_name: str) -> None:
    """Raise ``ValueError`` unless ``value`` is tz-aware and resolves to IST.

    Checked by UTC-offset equivalence rather than ``tzinfo`` identity:
    Asia/Kolkata has a fixed +05:30 offset (no DST), so any ``tzinfo``
    yielding that same offset at this instant is an equivalent
    representation of IST and is accepted.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            f"{field_name} must be timezone-aware (Asia/Kolkata); got a naive datetime"
        )
    if value.utcoffset() != value.astimezone(IST).utcoffset():
        raise ValueError(
            f"{field_name} must resolve to Asia/Kolkata (UTC+05:30); got offset {value.utcoffset()}"
        )


def require_ist_iso(value: str, field_name: str) -> None:
    """Parse an ISO 8601 string, then run ``require_ist`` on the result.

    Shared for the reason ``ist_from_epoch_ms`` is: two modules now store
    timestamps as text and both have to reject the same things, and a second
    spelling of the check is a second chance to accept a UTC-rendered or naive
    timestamp somewhere. A string that merely *looks* like a timestamp is not
    one, so this parses rather than pattern-matching.

    Lifted here in STEP-05 from ``agents.evidence.pack``, which had it private,
    when the policy corpus became the second text-timestamp store.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO 8601 timestamp; got {value!r}") from exc
    require_ist(parsed, field_name)
