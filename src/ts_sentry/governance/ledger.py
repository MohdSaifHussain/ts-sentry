# SPDX-License-Identifier: MIT
"""D3: the Trajectory Ledger - an append-only, hash-chained record of every
step every agent takes (ARCHITECTURE 3.2).

Entry fields and the chaining rule follow ARCHITECTURE 3.2. Two deviations
from its literal text, both deliberate and both recorded in the STEP-02
Outcome:

1. ``agent_id`` is nullable. 3.2's field tuple does not contemplate
   ``SESSION_OPEN`` / ``SESSION_CLOSE``, which are orchestrator events with
   no agent behind them.
2. The hash is computed over a separator-joined encoding
   (``governance.canonical``), not the literal ``a || b`` concatenation.
   Bare concatenation is ambiguous, which is a real weakness in the one
   structure whose entire job is telling entries apart.

Why the timestamp is stored twice
---------------------------------
This is the load-bearing storage decision, and it is evidence-based rather
than stylistic. Per DuckDB's official documentation on TIMESTAMP WITH TIME
ZONE (https://duckdb.org/docs/current/sql/data_types/timestamp.html), a
``TIMESTAMPTZ`` "only stores the INT64 number of non-leap microseconds since
the Unix epoch", and "string formatting for this type [is] performed in a
configured time zone, which defaults to the system time zone".

So a ``TIMESTAMPTZ`` does not preserve the offset it was written with, and
its rendered string depends on who is reading it. Verified against DuckDB
1.5.5: one instant written as ``2026-07-31T14:30:00+05:30`` renders as
``2026-07-31 14:30:00+05:30`` under a Kolkata session, ``2026-07-31
09:00:00+00`` under UTC, and ``2026-07-31 05:00:00-04`` under New York.

Had ``entry_hash`` covered a DuckDB-rendered timestamp, an intact ledger
would verify on a machine in IST and report a **false broken chain** in CI,
which runs UTC. So the hashed representation is the canonical IST ISO 8601
string in its own ``VARCHAR`` column, and the hash covers exactly the bytes
stored. The ``TIMESTAMPTZ`` column is retained alongside for SQL-side
querying and temporal binning, where the instant is what matters and the
rendering does not. A test asserts the two columns never drift.

This also avoids a dependency: materializing a ``TIMESTAMPTZ`` into Python
through the DuckDB client requires ``pytz`` (verified: it raises
``InvalidInputException`` without it), which this project does not depend on
and now does not need to.

Honest limit: this module cannot detect tail truncation
-------------------------------------------------------
Chain verification detects modification, reordering, and interior deletion.
It cannot detect entries dropped from the *end*: what remains is a shorter
chain whose every link still recomputes, indistinguishable from a session
that ended earlier. Surfaced by a hypothesis property rather than assumed,
and pinned by
``test_tail_truncation_is_invisible_to_chain_verification_alone``.

Narrowed in STEP-03, and worth stating precisely. Nothing in this module
changed: ``verify_chain`` could not see a truncated tail then and cannot now.
What changed is that the independent anchor it needs, the expected head
(chain length plus final ``entry_hash``) recorded outside the chain, now
exists in ``orchestrator.manifest``. So the limitation is a property of chain
verification rather than of the system, and the system's own limit moved to
where the anchor is kept: an anchor stored beside the ledger it describes is
rewritable by anyone who can truncate that ledger, so independence of custody
is what makes it a control. Both halves are asserted, and the second is
carried into Honest Limits.

Entries validate their field *shapes* on construction but do not verify
their own hash. That is deliberate and differs from ``HumanSignature``,
which does. A signature is a capability object, so holding an invalid one
must be impossible; a ledger entry is a record read back from possibly
tampered storage, so it must remain *inspectable* in order to be reported
on. Tamper detection belongs in ``verify_chain``, which returns a structured
verdict naming the first broken sequence number, rather than raising during
a read and losing that information.
"""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import duckdb

from ts_sentry.data.tz import IST, require_ist
from ts_sentry.governance.canonical import digest_fields, require_sha256_hex
from ts_sentry.governance.mandate import AgentId

__all__ = [
    "GENESIS_PREV_HASH",
    "BreakReason",
    "ChainHead",
    "ChainVerification",
    "EventType",
    "Ledger",
    "LedgerEntry",
    "OrchestratorToken",
    "canonical_timestamp",
    "chain_head",
    "compute_entry_hash",
    "digest_payload",
    "read_jsonl",
    "read_store",
    "verify_chain",
]

_LEDGER_DOMAIN = "ts-sentry/ledger-entry/v1"
"""Domain separation from the D2 signature digest, which uses the same
primitive over a similarly shaped field list."""

GENESIS_PREV_HASH = "0" * 64
"""``prev_hash`` of the first entry. Not a real digest, and not reachable as
one: SHA-256 has no known preimage for all-zeros, so no entry can legitimately
claim it as its own ``entry_hash``."""

_NO_AGENT = ""
"""Encoding of a null ``agent_id``. Safe as a distinct value because no
``AgentId`` member is the empty string, and ``join_fields`` keeps field
boundaries unambiguous regardless."""


class EventType(StrEnum):
    """The eleven ledger event types named in ARCHITECTURE 3.2, verbatim.

    ``GATE_REJECTION``, ``VERIFICATION_FAIL``, and
    ``MANDATE_VIOLATION_ATTEMPT`` are showcased metrics rather than
    embarrassments: a governance layer that never fires is one that was
    never tested.
    """

    PROMPT_SENT = "prompt_sent"
    TOOL_CALLED = "tool_called"
    TOOL_RESULT = "tool_result"
    OUTPUT_PROPOSED = "output_proposed"
    VERIFICATION_PASS = "verification_pass"
    VERIFICATION_FAIL = "verification_fail"
    GATE_REJECTION = "gate_rejection"
    HUMAN_DECISION = "human_decision"
    MANDATE_VIOLATION_ATTEMPT = "mandate_violation_attempt"
    SESSION_OPEN = "session_open"
    SESSION_CLOSE = "session_close"


@dataclass(frozen=True, slots=True)
class OrchestratorToken:
    """Capability required to append to the ledger.

    Honest limits: Python cannot make this unforgeable, and this module does
    not pretend otherwise. What it buys is that there is no untokened write
    path in this codebase, so a ledger write cannot happen by accident or by
    a caller who simply did not know better. The substantive guarantee
    against a determined out-of-band write is tamper-evidence:
    ``verify_chain`` catches rows inserted around ``Ledger.append``, and
    there is a test proving it on a raw DuckDB insert.
    """

    session_id: str

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("session_id must be a non-empty identifier")


def canonical_timestamp(value: datetime) -> str:
    """Render ``value`` as the canonical IST ISO 8601 string used in hashing.

    Normalizes the *representation* as well as validating it: a
    ``timezone(timedelta(hours=5, minutes=30))`` spelling and a
    ``ZoneInfo("Asia/Kolkata")`` spelling of the same instant produce one
    identical string, so two equivalent timestamps cannot hash differently.
    """
    require_ist(value, "timestamp_ist")
    return value.astimezone(IST).isoformat()


def digest_payload(payload: Mapping[str, object]) -> str:
    """SHA-256 over a canonical JSON form of an event payload.

    Structured objects use the canonical-JSON convention (sorted keys, no
    reliance on field order), the same one ``mandate.mandate_hash`` uses; see
    ``governance.canonical`` for why this codebase runs two hashing
    conventions rather than forcing one.

    Only the digest ever enters the ledger. Payload bodies are held by the
    session artifacts, so the chain stays fixed-width and a ledger row can
    never itself become a data-leak surface.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return digest_fields(encoded)


def compute_entry_hash(
    *,
    seq: int,
    timestamp_ist: datetime,
    agent_id: AgentId | None,
    mandate_hash: str,
    event_type: EventType,
    payload_digest: str,
    prev_hash: str,
) -> str:
    """``entry_hash`` per ARCHITECTURE 3.2, over the canonical encoding."""
    return digest_fields(
        _LEDGER_DOMAIN,
        str(seq),
        canonical_timestamp(timestamp_ist),
        _NO_AGENT if agent_id is None else agent_id.value,
        mandate_hash,
        event_type.value,
        payload_digest,
        prev_hash,
    )


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One immutable step in the chain.

    Validates field shapes, not its own hash. See the module docstring for
    why that differs from ``HumanSignature``.
    """

    seq: int
    timestamp_ist: datetime
    agent_id: AgentId | None
    mandate_hash: str
    event_type: EventType
    payload_digest: str
    prev_hash: str
    entry_hash: str

    def __post_init__(self) -> None:
        if self.seq < 0:
            raise ValueError(f"seq must be non-negative; got {self.seq}")
        require_ist(self.timestamp_ist, "timestamp_ist")
        require_sha256_hex(self.mandate_hash, "mandate_hash")
        require_sha256_hex(self.payload_digest, "payload_digest")
        require_sha256_hex(self.prev_hash, "prev_hash")
        require_sha256_hex(self.entry_hash, "entry_hash")

    @property
    def timestamp_iso(self) -> str:
        """The exact string this entry's hash was computed over."""
        return canonical_timestamp(self.timestamp_ist)

    def recomputed_hash(self) -> str:
        return compute_entry_hash(
            seq=self.seq,
            timestamp_ist=self.timestamp_ist,
            agent_id=self.agent_id,
            mandate_hash=self.mandate_hash,
            event_type=self.event_type,
            payload_digest=self.payload_digest,
            prev_hash=self.prev_hash,
        )

    def to_json_object(self) -> dict[str, object]:
        return {
            "seq": self.seq,
            "timestamp_ist": self.timestamp_iso,
            "agent_id": None if self.agent_id is None else self.agent_id.value,
            "mandate_hash": self.mandate_hash,
            "event_type": self.event_type.value,
            "payload_digest": self.payload_digest,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
        }


def entry_from_json_object(obj: Mapping[str, object]) -> LedgerEntry:
    """Rebuild an entry from its exported form.

    Deliberately tolerant of *content* (a tampered field must survive the
    read so ``verify_chain`` can report on it) and strict about *shape* (a
    structurally unreadable line is a format error, not an integrity
    finding).
    """
    raw_agent = obj["agent_id"]
    return LedgerEntry(
        seq=int(str(obj["seq"])),
        timestamp_ist=datetime.fromisoformat(str(obj["timestamp_ist"])),
        agent_id=None if raw_agent is None else AgentId(str(raw_agent)),
        mandate_hash=str(obj["mandate_hash"]),
        event_type=EventType(str(obj["event_type"])),
        payload_digest=str(obj["payload_digest"]),
        prev_hash=str(obj["prev_hash"]),
        entry_hash=str(obj["entry_hash"]),
    )


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


class BreakReason(StrEnum):
    """Why a chain failed verification."""

    SEQ_NOT_CONTIGUOUS = "seq_not_contiguous"
    GENESIS_PREV_HASH_WRONG = "genesis_prev_hash_wrong"
    PREV_HASH_MISMATCH = "prev_hash_mismatch"
    ENTRY_HASH_MISMATCH = "entry_hash_mismatch"


@dataclass(frozen=True, slots=True)
class ChainVerification:
    """Structured verdict. ``first_broken_seq`` is the *expected* sequence
    number at the first failing position, which is well defined even when the
    tampering is to a ``seq`` value itself.
    """

    intact: bool
    entries_checked: int
    first_broken_seq: int | None
    reason: BreakReason | None
    detail: str


def _intact(count: int) -> ChainVerification:
    return ChainVerification(
        intact=True,
        entries_checked=count,
        first_broken_seq=None,
        reason=None,
        detail=f"chain intact across {count} entries",
    )


def _broken(count: int, seq: int, reason: BreakReason, detail: str) -> ChainVerification:
    return ChainVerification(
        intact=False,
        entries_checked=count,
        first_broken_seq=seq,
        reason=reason,
        detail=detail,
    )


@dataclass(frozen=True, slots=True)
class ChainHead:
    """Where a chain currently ends: its length and its final ``entry_hash``.

    ``entry_hash`` of an empty chain is the genesis value, so a head is always
    well defined and "nothing has been appended" has a spelling rather than
    being a null.

    Lives here rather than in ``cli.main``, where it was first written, because
    two consumers now need the identical spelling and neither may import the
    other: ``verify-ledger`` compares a head, and the STEP-03 session manifest
    stores one. A head that renders differently in the store and the comparison
    is a head that cannot be compared.
    """

    count: int
    entry_hash: str

    def render(self) -> str:
        return f"{self.count}:{self.entry_hash}"


def chain_head(entries: tuple[LedgerEntry, ...]) -> ChainHead:
    if not entries:
        return ChainHead(count=0, entry_hash=GENESIS_PREV_HASH)
    return ChainHead(count=len(entries), entry_hash=entries[-1].entry_hash)


def verify_chain(entries: Iterable[LedgerEntry]) -> ChainVerification:
    """Recompute the chain and report the first broken link, if any.

    The single shared core both ``verify-ledger`` readers feed, so a JSONL
    export and the DuckDB store it came from cannot disagree.

    Walks positions in order, expecting ``seq == position``. Checks
    contiguity, then linkage to the previous entry, then the entry's own
    recomputed hash. Any single-field mutation therefore fails at or before
    that entry: altering a covered field breaks its own recomputation, and
    altering ``entry_hash`` breaks the next entry's linkage.
    """
    prev_hash = GENESIS_PREV_HASH
    checked = 0

    for position, entry in enumerate(entries):
        if entry.seq != position:
            return _broken(
                checked,
                position,
                BreakReason.SEQ_NOT_CONTIGUOUS,
                f"expected seq {position} at position {position}; found {entry.seq}. "
                "An append-only chain has no gaps, so this is a deletion, a "
                "reordering, or an out-of-band insert.",
            )

        if position == 0 and entry.prev_hash != GENESIS_PREV_HASH:
            return _broken(
                checked,
                position,
                BreakReason.GENESIS_PREV_HASH_WRONG,
                f"first entry must carry the genesis prev_hash; found {entry.prev_hash}. "
                "The chain has been truncated at the front.",
            )

        if entry.prev_hash != prev_hash:
            return _broken(
                checked,
                entry.seq,
                BreakReason.PREV_HASH_MISMATCH,
                f"prev_hash {entry.prev_hash} does not match the preceding entry_hash {prev_hash}",
            )

        recomputed = entry.recomputed_hash()
        if entry.entry_hash != recomputed:
            return _broken(
                checked,
                entry.seq,
                BreakReason.ENTRY_HASH_MISMATCH,
                f"entry_hash {entry.entry_hash} does not recompute; expected {recomputed}. "
                "A hash-covered field was altered after the entry was written.",
            )

        prev_hash = entry.entry_hash
        checked += 1

    return _intact(checked)


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

_CREATE_SCHEMA_GOVERNANCE = "CREATE SCHEMA IF NOT EXISTS governance;"

# Deliberately not in `main`: DataScope has no member resolving to the
# governance schema, so the ledger sits outside agent scope by the same
# allowlist rule as `sealed` (absence is denial).
_CREATE_LEDGER = """
CREATE TABLE IF NOT EXISTS governance.ledger (
    seq BIGINT PRIMARY KEY,
    ts_ist TIMESTAMPTZ NOT NULL,
    ts_ist_iso VARCHAR NOT NULL,
    agent_id VARCHAR,
    mandate_hash VARCHAR NOT NULL,
    event_type VARCHAR NOT NULL,
    payload_digest VARCHAR NOT NULL,
    prev_hash VARCHAR NOT NULL,
    entry_hash VARCHAR NOT NULL
);
"""

_INSERT_ENTRY = """
INSERT INTO governance.ledger
    (seq, ts_ist, ts_ist_iso, agent_id, mandate_hash, event_type,
     payload_digest, prev_hash, entry_hash)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_SELECT_TAIL = "SELECT seq, entry_hash FROM governance.ledger ORDER BY seq DESC LIMIT 1;"

# ts_ist_iso, never ts_ist: the hash covers the stored string, and reading a
# TIMESTAMPTZ into Python would additionally require pytz.
_SELECT_ALL = """
SELECT seq, ts_ist_iso, agent_id, mandate_hash, event_type,
       payload_digest, prev_hash, entry_hash
FROM governance.ledger
ORDER BY seq;
"""


def _row_to_entry(row: tuple[object, ...]) -> LedgerEntry:
    raw_agent = row[2]
    return LedgerEntry(
        seq=int(str(row[0])),
        timestamp_ist=datetime.fromisoformat(str(row[1])),
        agent_id=None if raw_agent is None else AgentId(str(raw_agent)),
        mandate_hash=str(row[3]),
        event_type=EventType(str(row[4])),
        payload_digest=str(row[5]),
        prev_hash=str(row[6]),
        entry_hash=str(row[7]),
    )


class Ledger:
    """Append-only, hash-chained store over a DuckDB connection.

    ``append`` is O(1) in lookups (STEP-02 3.2c): the tail is read once at
    construction and cached, so a write never rescans the chain. The cache is
    the only state, and it is derived, so reopening a store recovers it.
    """

    __slots__ = ("_con", "_last_hash", "_last_seq")

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self._con = con
        con.execute(_CREATE_SCHEMA_GOVERNANCE)
        con.execute(_CREATE_LEDGER)

        tail = con.execute(_SELECT_TAIL).fetchone()
        if tail is None:
            self._last_seq = -1
            self._last_hash = GENESIS_PREV_HASH
        else:
            self._last_seq = int(str(tail[0]))
            self._last_hash = str(tail[1])

    @property
    def last_seq(self) -> int:
        """Sequence number of the most recent entry, or -1 when empty."""
        return self._last_seq

    @property
    def last_hash(self) -> str:
        return self._last_hash

    @property
    def head(self) -> ChainHead:
        """The current head, from the cached tail rather than a rescan.

        Equal to ``chain_head(self.read_all())`` by construction, since seq is
        contiguous from zero; a test asserts the two agree so the O(1) path
        cannot drift from the reading path.
        """
        return ChainHead(count=self._last_seq + 1, entry_hash=self._last_hash)

    def append(
        self,
        token: OrchestratorToken,
        *,
        timestamp_ist: datetime,
        agent_id: AgentId | None,
        mandate_hash: str,
        event_type: EventType,
        payload_digest: str,
    ) -> LedgerEntry:
        """Append one entry and return it.

        ``token`` is positional and required, so there is no way to write to
        the ledger without naming the capability at the call site.
        """
        if not isinstance(token, OrchestratorToken):  # pragma: no cover - typed guard
            raise TypeError("ledger writes require an OrchestratorToken")

        seq = self._last_seq + 1
        prev_hash = self._last_hash
        entry = LedgerEntry(
            seq=seq,
            timestamp_ist=timestamp_ist,
            agent_id=agent_id,
            mandate_hash=mandate_hash,
            event_type=event_type,
            payload_digest=payload_digest,
            prev_hash=prev_hash,
            entry_hash=compute_entry_hash(
                seq=seq,
                timestamp_ist=timestamp_ist,
                agent_id=agent_id,
                mandate_hash=mandate_hash,
                event_type=event_type,
                payload_digest=payload_digest,
                prev_hash=prev_hash,
            ),
        )

        self._con.execute(
            _INSERT_ENTRY,
            [
                entry.seq,
                entry.timestamp_ist,
                entry.timestamp_iso,
                None if entry.agent_id is None else entry.agent_id.value,
                entry.mandate_hash,
                entry.event_type.value,
                entry.payload_digest,
                entry.prev_hash,
                entry.entry_hash,
            ],
        )

        self._last_seq = entry.seq
        self._last_hash = entry.entry_hash
        return entry

    def read_all(self) -> tuple[LedgerEntry, ...]:
        rows = self._con.execute(_SELECT_ALL).fetchall()
        return tuple(_row_to_entry(row) for row in rows)

    def verify(self) -> ChainVerification:
        return verify_chain(self.read_all())

    def export_jsonl(self, path: Path) -> None:
        """Write the chain as one JSON object per line, in sequence order."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for entry in self.read_all():
                handle.write(json.dumps(entry.to_json_object(), sort_keys=True))
                handle.write("\n")


def read_jsonl(path: Path) -> tuple[LedgerEntry, ...]:
    """Read an exported chain. Blank lines are skipped; anything else that
    fails to parse is a format error, raised rather than reported as
    tampering."""
    entries: list[LedgerEntry] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            entries.append(entry_from_json_object(json.loads(line)))
    return tuple(entries)


def read_store(path: Path) -> tuple[LedgerEntry, ...]:
    """Read a chain out of a DuckDB store file, read-only."""
    con = duckdb.connect(str(path), read_only=True)
    try:
        rows = con.execute(_SELECT_ALL).fetchall()
        return tuple(_row_to_entry(row) for row in rows)
    finally:
        con.close()
