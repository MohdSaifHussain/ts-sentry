# SPDX-License-Identifier: MIT
"""STEP-02 3.2 hypothesis properties for the trajectory ledger.

(a) the chain is valid after N random appends;
(b) any single-field mutation breaks verification at or before that entry.

Property (c), "append is O(1) lookups (no full-chain rescan on write)", is
asserted in ``tests/test_ledger.py`` by counting statements issued per
append. It is an example-based test on purpose: the claim is about the
number of queries, which is exact, not about a distribution over inputs.
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta

import duckdb
from hypothesis import given, settings
from hypothesis import strategies as st

from ts_sentry.data.tz import IST
from ts_sentry.governance.ledger import (
    EventType,
    Ledger,
    LedgerEntry,
    OrchestratorToken,
    digest_payload,
    verify_chain,
)
from ts_sentry.governance.mandate import AgentId

_TOKEN = OrchestratorToken(session_id="property-session")
_MANDATE_HASH = "1" * 64
_BASE_TS = datetime(2026, 7, 31, 14, 30, tzinfo=IST)

# A digest no generated entry can already hold, so every mutation below is
# guaranteed to be a real change rather than an accidental no-op.
_FOREIGN_DIGEST = "9" * 64

_SETTINGS = settings(max_examples=100, deadline=None)

_STEP = st.tuples(
    st.sampled_from(EventType),
    st.one_of(st.none(), st.sampled_from(AgentId)),
    st.integers(min_value=0, max_value=86_400),
    st.integers(min_value=0, max_value=1_000),
)
type _Step = tuple[EventType, AgentId | None, int, int]


def _build(steps: list[_Step]) -> tuple[LedgerEntry, ...]:
    ledger = Ledger(duckdb.connect(":memory:"))
    for event_type, agent_id, offset_s, payload_value in steps:
        ledger.append(
            _TOKEN,
            timestamp_ist=_BASE_TS + timedelta(seconds=offset_s),
            agent_id=agent_id,
            mandate_hash=_MANDATE_HASH,
            event_type=event_type,
            payload_digest=digest_payload({"value": payload_value}),
        )
    return ledger.read_all()


@_SETTINGS
@given(steps=st.lists(_STEP, min_size=0, max_size=25))
def test_chain_is_valid_after_n_random_appends(steps: list[_Step]) -> None:
    """STEP-02 3.2(a). Includes the empty chain, which is vacuously intact."""
    entries = _build(steps)
    result = verify_chain(entries)

    assert result.intact, result.detail
    assert result.entries_checked == len(steps)
    assert [entry.seq for entry in entries] == list(range(len(steps)))


@_SETTINGS
@given(steps=st.lists(_STEP, min_size=1, max_size=20))
def test_round_trip_through_storage_preserves_the_chain(steps: list[_Step]) -> None:
    """Reading entries back must not perturb any hash-covered field.

    The timestamp is the field at risk: DuckDB stores a TIMESTAMPTZ as a bare
    instant and renders it in the reader's time zone, so a chain that
    survived a write but not a read would be the failure mode here.
    """
    entries = _build(steps)
    for entry in entries:
        assert entry.entry_hash == entry.recomputed_hash()


_MUTATIONS: dict[str, Callable[[LedgerEntry], LedgerEntry]] = {
    "seq": lambda e: replace(e, seq=e.seq + 100),
    "timestamp_ist": lambda e: replace(e, timestamp_ist=e.timestamp_ist + timedelta(seconds=1)),
    "agent_id": lambda e: replace(
        e, agent_id=AgentId.MEMO if e.agent_id is not AgentId.MEMO else AgentId.TRIAGE
    ),
    "mandate_hash": lambda e: replace(e, mandate_hash=_FOREIGN_DIGEST),
    "event_type": lambda e: replace(
        e,
        event_type=(
            EventType.GATE_REJECTION
            if e.event_type is not EventType.GATE_REJECTION
            else EventType.TOOL_CALLED
        ),
    ),
    "payload_digest": lambda e: replace(e, payload_digest=_FOREIGN_DIGEST),
    "prev_hash": lambda e: replace(e, prev_hash=_FOREIGN_DIGEST),
    "entry_hash": lambda e: replace(e, entry_hash=_FOREIGN_DIGEST),
}


@_SETTINGS
@given(
    steps=st.lists(_STEP, min_size=1, max_size=15),
    target=st.integers(min_value=0),
    field=st.sampled_from(sorted(_MUTATIONS)),
)
def test_any_single_field_mutation_breaks_verification(
    steps: list[_Step], target: int, field: str
) -> None:
    """STEP-02 3.2(b).

    Every field of an entry is covered by its own ``entry_hash``, and that
    hash is covered by the next entry's ``prev_hash``. So mutating anything
    at position i is caught at i (its own hash stops recomputing) or before i
    (a contiguity or linkage break earlier in the walk). Nothing can be
    altered silently.
    """
    entries = list(_build(steps))
    index = target % len(entries)
    original = entries[index]
    mutated = _MUTATIONS[field](original)

    assert mutated != original, f"mutation of {field} was a no-op"
    entries[index] = mutated

    result = verify_chain(entries)

    assert not result.intact, f"mutating {field} at index {index} went undetected"
    assert result.first_broken_seq is not None
    assert result.first_broken_seq <= index, (
        f"mutation of {field} at index {index} reported at "
        f"{result.first_broken_seq}, which is after the tampered entry"
    )
    assert result.reason is not None
    assert result.detail


@_SETTINGS
@given(steps=st.lists(_STEP, min_size=2, max_size=15), target=st.integers(min_value=0))
def test_deleting_any_interior_entry_breaks_verification(steps: list[_Step], target: int) -> None:
    """Append-only, tested as a property: no *interior* entry can be dropped
    quietly, because removing one breaks seq contiguity at that position.

    Scoped to interior entries deliberately. Truncating the tail is not
    detectable by chain verification alone and must not be claimed here; see
    ``test_truncating_the_tail_is_undetectable`` for the limit, stated
    explicitly rather than hidden by a strategy that never generates it.
    """
    entries = list(_build(steps))
    index = target % (len(entries) - 1)  # never the last entry
    del entries[index]

    result = verify_chain(entries)

    assert not result.intact
    assert result.first_broken_seq is not None
    assert result.first_broken_seq <= index


@_SETTINGS
@given(steps=st.lists(_STEP, min_size=2, max_size=15))
def test_truncating_the_tail_is_undetectable(steps: list[_Step]) -> None:
    """A named, test-enforced limitation of hash chains, not a defect.

    Dropping entries from the end leaves a shorter chain whose every link
    still recomputes, so verification alone cannot distinguish it from a
    session that simply ended earlier. Detecting it requires an independent
    anchor recording the expected head (length plus final entry_hash), which
    this phase does not have: the session manifest that would carry one is
    STEP-03.

    Asserted rather than merely documented, so the day an anchor lands this
    test fails and forces the limitation to be rewritten instead of quietly
    outliving its own truth.
    """
    entries = list(_build(steps))
    del entries[-1]

    assert verify_chain(entries).intact
