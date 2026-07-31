# SPDX-License-Identifier: MIT
"""STEP-02 D3: the Trajectory Ledger - chaining, tamper detection, storage.

The headline case here is ``test_chain_verifies_under_any_reader_timezone``:
a regression test for a defect avoided at design time rather than found
after the fact. See the ``governance.ledger`` module docstring and DuckDB's
TIMESTAMPTZ documentation for why it exists.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import duckdb
import pytest

from ts_sentry.data.tz import IST
from ts_sentry.governance.ledger import (
    GENESIS_PREV_HASH,
    BreakReason,
    EventType,
    Ledger,
    LedgerEntry,
    OrchestratorToken,
    canonical_timestamp,
    compute_entry_hash,
    digest_payload,
    read_jsonl,
    read_store,
    verify_chain,
)
from ts_sentry.governance.mandate import AgentId

_TOKEN = OrchestratorToken(session_id="session-001")
_MANDATE_HASH = "1" * 64
_TS = datetime(2026, 7, 31, 14, 30, tzinfo=IST)


def _append(ledger: Ledger, index: int, agent_id: AgentId | None = AgentId.TRIAGE) -> LedgerEntry:
    return ledger.append(
        _TOKEN,
        timestamp_ist=_TS + timedelta(seconds=index),
        agent_id=agent_id,
        mandate_hash=_MANDATE_HASH,
        event_type=EventType.TOOL_CALLED,
        payload_digest=digest_payload({"step": index}),
    )


def _chain(con: duckdb.DuckDBPyConnection, length: int = 5) -> Ledger:
    ledger = Ledger(con)
    ledger.append(
        _TOKEN,
        timestamp_ist=_TS,
        agent_id=None,
        mandate_hash=_MANDATE_HASH,
        event_type=EventType.SESSION_OPEN,
        payload_digest=digest_payload({"session": "session-001"}),
    )
    for index in range(1, length):
        _append(ledger, index)
    return ledger


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(":memory:")


# --------------------------------------------------------------------------
# Chain construction
# --------------------------------------------------------------------------


def test_first_entry_carries_the_genesis_prev_hash(con: duckdb.DuckDBPyConnection) -> None:
    entry = _chain(con, 1).read_all()[0]
    assert entry.seq == 0
    assert entry.prev_hash == GENESIS_PREV_HASH


def test_appended_chain_verifies_intact(con: duckdb.DuckDBPyConnection) -> None:
    result = _chain(con, 6).verify()
    assert result.intact
    assert result.entries_checked == 6
    assert result.first_broken_seq is None
    assert result.reason is None


def test_each_entry_links_to_its_predecessor(con: duckdb.DuckDBPyConnection) -> None:
    entries = _chain(con, 4).read_all()
    for previous, current in zip(entries[:-1], entries[1:], strict=True):
        assert current.prev_hash == previous.entry_hash
        assert current.seq == previous.seq + 1


def test_session_events_carry_no_agent(con: duckdb.DuckDBPyConnection) -> None:
    """ARCHITECTURE 3.2's field tuple has no nullable agent_id; SESSION_OPEN
    and SESSION_CLOSE have no agent behind them. Recorded deviation."""
    entry = _chain(con, 1).read_all()[0]
    assert entry.agent_id is None
    assert entry.event_type is EventType.SESSION_OPEN
    assert verify_chain([entry]).intact


def test_empty_chain_is_vacuously_intact(con: duckdb.DuckDBPyConnection) -> None:
    result = Ledger(con).verify()
    assert result.intact
    assert result.entries_checked == 0


def test_reopening_a_store_recovers_the_tail(tmp_path: Path) -> None:
    """The seq/hash cache is derived state, so a reopened store must continue
    the same chain rather than restart it."""
    store = tmp_path / "ledger.duckdb"

    first = duckdb.connect(str(store))
    ledger = _chain(first, 3)
    tail_seq, tail_hash = ledger.last_seq, ledger.last_hash
    first.close()

    second = duckdb.connect(str(store))
    reopened = Ledger(second)
    assert reopened.last_seq == tail_seq
    assert reopened.last_hash == tail_hash

    _append(reopened, 3)
    assert reopened.verify().intact
    assert reopened.last_seq == tail_seq + 1


# --------------------------------------------------------------------------
# The timezone regression
# --------------------------------------------------------------------------


@pytest.mark.parametrize("reader_tz", ["Asia/Kolkata", "UTC", "America/New_York"])
def test_chain_verifies_under_any_reader_timezone(
    con: duckdb.DuckDBPyConnection, reader_tz: str
) -> None:
    """An intact chain must verify identically for every reader.

    DuckDB renders a TIMESTAMPTZ in the session time zone, so had entry_hash
    covered a DuckDB-rendered timestamp, this ledger would verify on a
    machine in IST and report a false broken chain in CI, which runs UTC.
    The hash covers the stored canonical IST string instead. This test is the
    guard on that decision.
    """
    ledger = _chain(con, 4)
    hashes_before = [entry.entry_hash for entry in ledger.read_all()]

    con.execute("SET TimeZone=?;", [reader_tz])

    assert ledger.verify().intact
    assert [entry.entry_hash for entry in ledger.read_all()] == hashes_before


def test_stored_timestamp_columns_never_drift(con: duckdb.DuckDBPyConnection) -> None:
    """ts_ist and ts_ist_iso are one value written twice, so they must always
    denote the same instant. Guards the redundancy the design accepts.

    epoch_us on both sides, because comparing them as rendered strings would
    reintroduce exactly the session-time-zone dependence being avoided.
    """
    _chain(con, 3)
    mismatches = con.execute(
        "SELECT count(*) FROM governance.ledger "
        "WHERE epoch_us(ts_ist) <> epoch_us(CAST(ts_ist_iso AS TIMESTAMPTZ));"
    ).fetchone()
    assert mismatches is not None
    assert mismatches[0] == 0


def test_canonical_timestamp_normalizes_equivalent_offsets() -> None:
    """Two spellings of the same IST instant must hash identically."""
    fixed = datetime(2026, 7, 31, 14, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    zoned = datetime(2026, 7, 31, 14, 30, tzinfo=IST)
    assert canonical_timestamp(fixed) == canonical_timestamp(zoned)


def test_canonical_timestamp_rejects_non_ist() -> None:
    with pytest.raises(ValueError, match="Asia/Kolkata"):
        canonical_timestamp(datetime(2026, 7, 31, 9, 0, tzinfo=UTC))


# --------------------------------------------------------------------------
# Tamper detection
# --------------------------------------------------------------------------


def test_altering_a_hash_covered_field_is_detected(con: duckdb.DuckDBPyConnection) -> None:
    entries = list(_chain(con, 4).read_all())
    entries[2] = replace(entries[2], payload_digest="9" * 64)

    result = verify_chain(entries)
    assert not result.intact
    assert result.first_broken_seq == 2
    assert result.reason is BreakReason.ENTRY_HASH_MISMATCH


def test_altering_an_entry_hash_breaks_the_next_link(con: duckdb.DuckDBPyConnection) -> None:
    entries = list(_chain(con, 4).read_all())
    entries[1] = replace(entries[1], entry_hash="9" * 64)

    result = verify_chain(entries)
    assert not result.intact
    assert result.first_broken_seq == 1


def test_a_rewritten_link_is_reported_as_a_link_break(con: duckdb.DuckDBPyConnection) -> None:
    """The one ``BreakReason`` no test reached before STEP-03.

    Every other tampering shape fires an earlier check: altering a covered
    field breaks the entry's own recomputation, and altering an ``entry_hash``
    breaks that entry before the *next* one's link is ever examined. Reaching
    ``PREV_HASH_MISMATCH`` takes rewriting a ``prev_hash`` specifically, which
    is what an attacker splicing two chains together would do. Found as a
    coverage gap while wiring the session ledger, and recorded in the STEP-03
    Outcome as a finding outside the deliverables.
    """
    entries = list(_chain(con, 4).read_all())
    entries[2] = replace(entries[2], prev_hash="9" * 64)

    result = verify_chain(entries)
    assert not result.intact
    assert result.first_broken_seq == 2
    assert result.reason is BreakReason.PREV_HASH_MISMATCH


def test_deleting_an_entry_is_detected(con: duckdb.DuckDBPyConnection) -> None:
    """Append-only means no gaps: a deletion breaks contiguity."""
    entries = list(_chain(con, 5).read_all())
    del entries[2]

    result = verify_chain(entries)
    assert not result.intact
    assert result.first_broken_seq == 2
    assert result.reason is BreakReason.SEQ_NOT_CONTIGUOUS


def test_truncating_the_front_of_the_chain_is_detected(con: duckdb.DuckDBPyConnection) -> None:
    entries = list(_chain(con, 4).read_all())[1:]

    result = verify_chain(entries)
    assert not result.intact
    assert result.first_broken_seq == 0


def test_rewriting_the_genesis_link_is_detected(con: duckdb.DuckDBPyConnection) -> None:
    """The first entry must anchor to the genesis prev_hash.

    Distinct from front-truncation, which trips seq contiguity first: here
    seq stays 0 and only the anchor is rewritten, which is what an attacker
    splicing a forged prefix onto the chain would leave behind.
    """
    entries = list(_chain(con, 3).read_all())
    entries[0] = replace(entries[0], prev_hash="9" * 64)

    result = verify_chain(entries)
    assert not result.intact
    assert result.first_broken_seq == 0
    assert result.reason is BreakReason.GENESIS_PREV_HASH_WRONG


def test_reordering_entries_is_detected(con: duckdb.DuckDBPyConnection) -> None:
    entries = list(_chain(con, 4).read_all())
    entries[1], entries[2] = entries[2], entries[1]

    result = verify_chain(entries)
    assert not result.intact
    assert result.first_broken_seq == 1


def test_a_raw_out_of_band_insert_is_detected(con: duckdb.DuckDBPyConnection) -> None:
    """The substantive guarantee behind the orchestrator token.

    The token cannot be made unforgeable in Python. What holds regardless is
    that a row written around ``Ledger.append`` cannot produce a valid chain,
    because the writer would have to forge a SHA-256 preimage to do it.
    """
    ledger = _chain(con, 3)
    smuggled_seq = ledger.last_seq + 1
    con.execute(
        "INSERT INTO governance.ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
        [
            smuggled_seq,
            _TS,
            canonical_timestamp(_TS),
            AgentId.MEMO.value,
            _MANDATE_HASH,
            EventType.HUMAN_DECISION.value,
            digest_payload({"smuggled": True}),
            ledger.last_hash,
            "9" * 64,
        ],
    )

    result = ledger.verify()
    assert not result.intact
    assert result.first_broken_seq == smuggled_seq
    assert result.reason is BreakReason.ENTRY_HASH_MISMATCH


def test_the_ledger_table_is_outside_agent_scope() -> None:
    """It lives in the `governance` schema, and DataScope has no member
    resolving there, so it is denied by the same absence-is-denial rule as
    `sealed` (STEP-01 3.3)."""
    from ts_sentry.governance.scopes import DataScope, resolve_table

    assert all("governance." not in resolve_table(scope) for scope in DataScope)
    assert not any("ledger" in scope.value for scope in DataScope)


# --------------------------------------------------------------------------
# Append is O(1) in lookups (STEP-02 3.2c)
# --------------------------------------------------------------------------


class _CountingConnection:
    """Counts statements issued, to prove appends do not rescan the chain."""

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self._con = con
        self.statements: list[str] = []

    def execute(self, query: str, parameters: object = None) -> Any:
        self.statements.append(query)
        if parameters is None:
            return self._con.execute(query)
        return self._con.execute(query, parameters)


def test_append_issues_a_constant_number_of_statements() -> None:
    """STEP-02 3.2c: no full-chain rescan on write.

    Asserted as statement count rather than wall-clock time, which would be
    a flaky proxy on shared CI hardware.
    """
    spy = _CountingConnection(duckdb.connect(":memory:"))
    ledger = Ledger(cast(duckdb.DuckDBPyConnection, spy))
    spy.statements.clear()

    per_append: list[int] = []
    for index in range(25):
        before = len(spy.statements)
        _append(ledger, index)
        per_append.append(len(spy.statements) - before)

    assert per_append == [1] * 25


def test_append_never_selects_the_whole_chain() -> None:
    spy = _CountingConnection(duckdb.connect(":memory:"))
    ledger = Ledger(cast(duckdb.DuckDBPyConnection, spy))
    spy.statements.clear()

    for index in range(5):
        _append(ledger, index)

    assert all(statement.strip().upper().startswith("INSERT") for statement in spy.statements)


# --------------------------------------------------------------------------
# Export, readers, and equivalence
# --------------------------------------------------------------------------


def test_jsonl_export_round_trips(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    ledger = _chain(con, 5)
    path = tmp_path / "ledger.jsonl"
    ledger.export_jsonl(path)

    assert read_jsonl(path) == ledger.read_all()


def test_jsonl_export_is_one_object_per_line_in_sequence_order(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    ledger = _chain(con, 4)
    path = tmp_path / "ledger.jsonl"
    ledger.export_jsonl(path)

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 4
    assert [json.loads(line)["seq"] for line in lines] == [0, 1, 2, 3]


def test_jsonl_export_skips_blank_lines_on_read(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    ledger = _chain(con, 2)
    path = tmp_path / "ledger.jsonl"
    ledger.export_jsonl(path)
    path.write_text(path.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")

    assert len(read_jsonl(path)) == 2


def test_both_readers_agree_on_an_intact_chain(tmp_path: Path) -> None:
    store = tmp_path / "ledger.duckdb"
    jsonl = tmp_path / "ledger.jsonl"

    con = duckdb.connect(str(store))
    ledger = _chain(con, 5)
    ledger.export_jsonl(jsonl)
    con.close()

    from_store = verify_chain(read_store(store))
    from_jsonl = verify_chain(read_jsonl(jsonl))

    assert from_store == from_jsonl
    assert from_store.intact


def test_both_readers_agree_on_the_same_first_broken_seq(tmp_path: Path) -> None:
    """Confirmed D6 requirement: the two readers must not merely both fail,
    they must fail at the same place, with the same reason."""
    store = tmp_path / "ledger.duckdb"
    jsonl = tmp_path / "ledger.jsonl"

    con = duckdb.connect(str(store))
    ledger = _chain(con, 5)
    ledger.export_jsonl(jsonl)
    con.execute("UPDATE governance.ledger SET payload_digest = ? WHERE seq = ?;", ["9" * 64, 3])
    con.close()

    # Tamper with the export identically, so the two inputs really are the
    # same corrupted chain rather than two different failures.
    lines = jsonl.read_text(encoding="utf-8").strip().splitlines()
    obj = json.loads(lines[3])
    obj["payload_digest"] = "9" * 64
    lines[3] = json.dumps(obj, sort_keys=True)
    jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")

    from_store = verify_chain(read_store(store))
    from_jsonl = verify_chain(read_jsonl(jsonl))

    assert from_store == from_jsonl
    assert not from_store.intact
    assert from_store.first_broken_seq == 3
    assert from_store.reason is BreakReason.ENTRY_HASH_MISMATCH


# --------------------------------------------------------------------------
# Digests and the write token
# --------------------------------------------------------------------------


def test_payload_digest_ignores_key_order() -> None:
    assert digest_payload({"a": 1, "b": 2}) == digest_payload({"b": 2, "a": 1})


def test_payload_digest_distinguishes_content() -> None:
    assert digest_payload({"a": 1}) != digest_payload({"a": 2})


def test_entry_hash_is_domain_separated_from_the_signature_digest() -> None:
    """Same primitive, similarly shaped field list, different domain tag, so
    a signature digest can never be mistaken for an entry hash."""
    from ts_sentry.governance.canonical import digest_fields

    entry_hash = compute_entry_hash(
        seq=0,
        timestamp_ist=_TS,
        agent_id=AgentId.TRIAGE,
        mandate_hash=_MANDATE_HASH,
        event_type=EventType.PROMPT_SENT,
        payload_digest="2" * 64,
        prev_hash=GENESIS_PREV_HASH,
    )
    undomained = digest_fields(
        "0",
        canonical_timestamp(_TS),
        AgentId.TRIAGE.value,
        _MANDATE_HASH,
        EventType.PROMPT_SENT.value,
        "2" * 64,
        GENESIS_PREV_HASH,
    )
    assert entry_hash != undomained


def test_orchestrator_token_requires_a_session_id() -> None:
    with pytest.raises(ValueError, match="session_id"):
        OrchestratorToken(session_id="   ")


def test_append_cannot_be_called_without_a_token(con: duckdb.DuckDBPyConnection) -> None:
    """There is no untokened write path: the capability must be named at the
    call site. Honest limits on what that does and does not buy are in the
    OrchestratorToken docstring."""
    ledger = Ledger(con)
    with pytest.raises(TypeError):
        ledger.append(  # type: ignore[call-arg]
            timestamp_ist=_TS,
            agent_id=AgentId.TRIAGE,
            mandate_hash=_MANDATE_HASH,
            event_type=EventType.PROMPT_SENT,
            payload_digest="2" * 64,
        )


@pytest.mark.parametrize("field", ["mandate_hash", "payload_digest", "prev_hash", "entry_hash"])
def test_entry_rejects_malformed_digests(field: str) -> None:
    fields: dict[str, object] = {
        "seq": 0,
        "timestamp_ist": _TS,
        "agent_id": AgentId.TRIAGE,
        "mandate_hash": _MANDATE_HASH,
        "event_type": EventType.PROMPT_SENT,
        "payload_digest": "2" * 64,
        "prev_hash": GENESIS_PREV_HASH,
        "entry_hash": "3" * 64,
        field: "not-a-digest",
    }
    with pytest.raises(ValueError, match=field):
        LedgerEntry(**fields)  # type: ignore[arg-type]


def test_entry_rejects_a_negative_seq() -> None:
    with pytest.raises(ValueError, match="seq"):
        LedgerEntry(
            seq=-1,
            timestamp_ist=_TS,
            agent_id=None,
            mandate_hash=_MANDATE_HASH,
            event_type=EventType.PROMPT_SENT,
            payload_digest="2" * 64,
            prev_hash=GENESIS_PREV_HASH,
            entry_hash="3" * 64,
        )
