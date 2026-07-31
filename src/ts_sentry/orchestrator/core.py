# SPDX-License-Identifier: MIT
"""D1: the session state machine (STEP-03 D1, ARCHITECTURE 5).

A synchronous, deterministic finite state machine. It owns session lifecycle,
the mandate registry, budget accounting, and every ledger write that is not
attributable to a single agent action. It makes no model calls and takes no
decisions about content.

Three structures here are load-bearing rather than stylistic.

**The transition table is total.** ``_successors`` is an exhaustive ``match``
over ``SessionState`` closed by ``assert_never``, mirroring
``scopes.resolve_table`` and ``mandate._consequence_rank``. STEP-01's leakage
red-team is the reason: a new member that nobody handled must break the
function itself, loudly, rather than fall through silently at one call site.
``TRANSITIONS`` is derived from that function, so the published table and the
enforced rule cannot drift apart.

**Illegal transitions raise; budget exhaustion returns.** The distinction is
deliberate and matches the D4 gates. A caller driving the machine from CLOSED
back to OPEN has a bug, and a bug should stop. An agent running out of tokens
is a *governed outcome*, expected and ledgered, so it comes back as a value
that the caller has to look at (STEP-03 3.3: exhaustion ends the turn cleanly
with a ``SESSION_CLOSE`` reason code, and partial results are still
delivered).

**Nothing reads the clock directly.** A ``Clock`` is injected, the same way
``signature.sign`` takes its ``signed_ts``. Tests and reproducible example
sessions use ``FixedClock``; only the CLI reaches for ``SystemClock``. This is
STEP-01 3.1's no-time-based-entropy rule applied to the one component that
genuinely has to record when things happened.

Honest limit, carried forward: a session records *its own* view of events.
``Session.recorded_events`` pairs each ledger entry with the payload body it
digested, because a digest whose body is lost is a digest nobody can check.
The bodies live in session artifacts, never in the chain, so a ledger row can
never itself become a data-leak surface.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol, assert_never

from ts_sentry.data.tz import IST, require_ist
from ts_sentry.governance.canonical import digest_fields, require_sha256_hex
from ts_sentry.governance.ledger import (
    ChainHead,
    EventType,
    Ledger,
    LedgerEntry,
    OrchestratorToken,
    digest_payload,
)
from ts_sentry.governance.mandate import AgentId, Mandate, mandate_hash

__all__ = [
    "TRANSITIONS",
    "BudgetSnapshot",
    "BudgetTracker",
    "Clock",
    "CloseReason",
    "FixedClock",
    "IllegalTransition",
    "MandateBinding",
    "RecordedEvent",
    "Session",
    "SessionClose",
    "SessionState",
    "SystemClock",
    "TurnStart",
    "UnknownAgent",
    "can_transition",
    "mandate_set_hash",
]

_MANDATE_SET_DOMAIN = "ts-sentry/session-mandate-set/v1"
"""Domain separation, for the same reason the ledger and signature digests
carry one: the same primitive over a similarly shaped field list must not be
able to collide across two meanings."""


class SessionState(StrEnum):
    """States of one analyst session.

    ``AWAITING_ANALYST`` has no CLI driver in STEP-03: a triage session
    produces a ranked queue under an OBSERVE ceiling and asks the analyst for
    nothing, so no Phase 3 path enters it. It exists because the state machine
    is the deliverable and a session that cannot wait for its supervisor is
    not a supervised session; it is exercised directly in the tests, and it
    gains a driver when an agent first produces something a human must decide
    on (STEP-05).
    """

    CREATED = "created"
    OPEN = "open"
    AGENT_TURN = "agent_turn"
    AWAITING_ANALYST = "awaiting_analyst"
    CLOSING = "closing"
    CLOSED = "closed"


class CloseReason(StrEnum):
    """Why a session ended. Every ``SESSION_CLOSE`` carries exactly one.

    Budget exhaustion is split into two members rather than collapsed into
    one: "ran out of tokens" and "ran out of steps" are different findings
    about an agent, and a metric that cannot tell them apart cannot inform a
    budget change.
    """

    COMPLETED = "completed"
    TOKEN_BUDGET_EXHAUSTED = "token_budget_exhausted"
    STEP_BUDGET_EXHAUSTED = "step_budget_exhausted"
    ANALYST_ABORT = "analyst_abort"
    DISPATCH_ERROR = "dispatch_error"


class IllegalTransition(Exception):
    """Raised when a caller drives the machine along an edge that does not
    exist. A programming error, not a governance outcome, so it stops."""


class UnknownAgent(Exception):
    """Raised when a turn is requested for an agent with no mandate bound to
    this session. The fleet is fixed and its mandates are loaded at session
    open, so this is a configuration error rather than a refusal."""


def _successors(state: SessionState) -> frozenset[SessionState]:
    """Legal successors of ``state``.

    Exhaustive and closed by ``assert_never``, so adding a state without
    deciding where it may go is a type error here rather than a silent gap
    everywhere.
    """
    match state:
        case SessionState.CREATED:
            return frozenset({SessionState.OPEN})
        case SessionState.OPEN:
            return frozenset({SessionState.AGENT_TURN, SessionState.CLOSING})
        case SessionState.AGENT_TURN:
            return frozenset(
                {SessionState.OPEN, SessionState.AWAITING_ANALYST, SessionState.CLOSING}
            )
        case SessionState.AWAITING_ANALYST:
            return frozenset({SessionState.OPEN, SessionState.CLOSING})
        case SessionState.CLOSING:
            return frozenset({SessionState.CLOSED})
        case SessionState.CLOSED:
            return frozenset()
        case _:  # pragma: no cover - exhaustiveness guard, unreachable per mypy
            assert_never(state)


TRANSITIONS: Mapping[SessionState, frozenset[SessionState]] = {
    state: _successors(state) for state in SessionState
}
"""The published transition table, derived from ``_successors`` rather than
written out beside it, so documentation and enforcement are the same object."""


def can_transition(source: SessionState, target: SessionState) -> bool:
    return target in TRANSITIONS[source]


# --------------------------------------------------------------------------
# Clock
# --------------------------------------------------------------------------


class Clock(Protocol):
    """Source of IST timestamps for ledger entries."""

    def now(self) -> datetime: ...


class SystemClock:
    """Wall-clock IST. The only component that reads real time, and only the
    CLI constructs it."""

    __slots__ = ()

    def now(self) -> datetime:
        return datetime.now(IST)


class FixedClock:
    """Deterministic clock for tests and reproducible example sessions.

    ``step`` defaults to one second rather than zero so a generated session
    has strictly increasing timestamps, which is what a real trajectory looks
    like. Zero is allowed: entries at one instant still chain correctly,
    because ``seq`` is part of the hashed field list.
    """

    __slots__ = ("_next", "_step")

    def __init__(self, start: datetime, step: timedelta = timedelta(seconds=1)) -> None:
        require_ist(start, "start")
        if step < timedelta(0):
            raise ValueError(f"step must not be negative; got {step}")
        self._next = start
        self._step = step

    def now(self) -> datetime:
        current = self._next
        self._next = current + self._step
        return current


# --------------------------------------------------------------------------
# Budgets (STEP-03 3.3)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    """An agent's budget position at one moment."""

    token_budget: int
    tokens_spent: int
    max_steps: int
    steps_taken: int

    @property
    def tokens_remaining(self) -> int:
        return self.token_budget - self.tokens_spent

    @property
    def steps_remaining(self) -> int:
        return self.max_steps - self.steps_taken

    @property
    def exhausted(self) -> bool:
        return self.tokens_remaining <= 0 or self.steps_remaining <= 0

    def to_json_object(self) -> dict[str, object]:
        return {
            "token_budget": self.token_budget,
            "tokens_spent": self.tokens_spent,
            "max_steps": self.max_steps,
            "steps_taken": self.steps_taken,
        }


class BudgetTracker:
    """Token and step accounting for one agent against one mandate.

    Preventive rather than detective, like every other control here: ``check``
    is asked *before* work happens and names the reason a turn may not start,
    so the ceiling is enforced by not spending rather than by noticing it was
    overspent. ``record`` afterwards books actuals, which can still push the
    position past zero when a model reports more usage than was reserved; the
    next ``check`` then refuses, and the session closes cleanly.
    """

    __slots__ = ("_max_steps", "_steps_taken", "_token_budget", "_tokens_spent")

    def __init__(self, mandate: Mandate) -> None:
        self._token_budget = mandate.token_budget
        self._max_steps = mandate.max_steps
        self._tokens_spent = 0
        self._steps_taken = 0

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            token_budget=self._token_budget,
            tokens_spent=self._tokens_spent,
            max_steps=self._max_steps,
            steps_taken=self._steps_taken,
        )

    def check(self, estimated_tokens: int = 0) -> CloseReason | None:
        """``None`` when the work may proceed, otherwise why it may not.

        Steps are checked before tokens so a session that has run out of both
        reports the ceiling it hit first in the sequence a turn actually
        consumes them.
        """
        if estimated_tokens < 0:
            raise ValueError(f"estimated_tokens must not be negative; got {estimated_tokens}")
        snapshot = self.snapshot()
        if snapshot.steps_remaining <= 0:
            return CloseReason.STEP_BUDGET_EXHAUSTED
        if snapshot.tokens_remaining <= 0 or estimated_tokens > snapshot.tokens_remaining:
            return CloseReason.TOKEN_BUDGET_EXHAUSTED
        return None

    def record_step(self) -> None:
        self._steps_taken += 1

    def record_tokens(self, tokens: int) -> None:
        if tokens < 0:
            raise ValueError(f"tokens must not be negative; got {tokens}")
        self._tokens_spent += tokens


# --------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MandateBinding:
    """A mandate as loaded into a session, with its hash computed once.

    The hash is recorded on every ledger entry the agent's actions produce, so
    a mandate change mid-session would be visible as a change in the chain
    rather than as an untraceable difference in behavior.
    """

    mandate: Mandate
    hash: str

    @classmethod
    def of(cls, mandate: Mandate) -> "MandateBinding":
        return cls(mandate=mandate, hash=mandate_hash(mandate))


def mandate_set_hash(mandates: Mapping[AgentId, MandateBinding]) -> str:
    """One digest over the whole loaded fleet configuration.

    ARCHITECTURE 3.1 requires mandates to be recorded in the ledger at session
    start. ``SESSION_OPEN`` has no single agent behind it, so it carries this
    set digest in the entry's ``mandate_hash`` field: the entry is bound to
    the exact fleet configuration the session opened with, and swapping any
    one mandate changes it.
    """
    return digest_fields(
        _MANDATE_SET_DOMAIN,
        *(f"{agent_id.value}={mandates[agent_id].hash}" for agent_id in sorted(mandates)),
    )


@dataclass(frozen=True, slots=True)
class RecordedEvent:
    """A ledger entry paired with the payload body it digested.

    Only the digest enters the chain. Keeping the body here is what lets a
    session artifact be checked against its own ledger: a test recomputes
    ``digest_payload(payload)`` and asserts it equals ``entry.payload_digest``.
    """

    entry: LedgerEntry
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class TurnStart:
    """Outcome of asking to begin an agent turn.

    Refusal is a value, not an exception: budget exhaustion is an expected,
    ledgered outcome that the caller must handle by closing the session
    cleanly (STEP-03 3.3).
    """

    started: bool
    binding: MandateBinding | None
    close_reason: CloseReason | None
    detail: str

    def __post_init__(self) -> None:
        if self.started is (self.close_reason is not None):
            raise ValueError(
                "a started turn carries no close reason; a refused one carries exactly one"
            )
        if self.started is (self.binding is None):
            raise ValueError(
                "a started turn carries its mandate binding; a refused one carries none"
            )


@dataclass(frozen=True, slots=True)
class SessionClose:
    """What closing produced: the entry, the reason, and the anchor.

    ``head`` is read *after* ``SESSION_CLOSE`` is appended, so it is the head
    of the finished chain. That is the value the session manifest stores and
    the value ``verify-ledger --expect-head`` compares against.
    """

    entry: LedgerEntry
    reason: CloseReason
    head: ChainHead


class Session:
    """One analyst session over one ledger.

    Responsibilities per ARCHITECTURE 5.1: open, bind analyst identity, load
    mandates, seed the ledger; then hold budgets and route lifecycle events.
    Dispatch (D3) and the firewall (D2) are separate modules that take a
    session rather than living inside it, so this stays a state machine.
    """

    __slots__ = (
        "_analyst_id",
        "_budgets",
        "_clock",
        "_closed_ts",
        "_dataset_digest",
        "_events",
        "_ledger",
        "_mandate_set_hash",
        "_mandates",
        "_opened_ts",
        "_session_id",
        "_state",
        "_token",
    )

    def __init__(
        self,
        *,
        session_id: str,
        analyst_id: str,
        ledger: Ledger,
        clock: Clock,
        mandates: Mapping[AgentId, Mandate],
        dataset_digest: str,
    ) -> None:
        if not session_id.strip():
            raise ValueError("session_id must be a non-empty identifier")
        if not analyst_id.strip():
            raise ValueError("analyst_id must be a non-empty analyst identity")
        if not mandates:
            raise ValueError("a session must load at least one mandate")
        for agent_id, mandate in mandates.items():
            if mandate.agent_id is not agent_id:
                raise ValueError(
                    f"mandate registered under {agent_id.value} declares "
                    f"agent_id {mandate.agent_id.value}"
                )
        require_sha256_hex(dataset_digest, "dataset_digest")

        self._session_id = session_id
        self._analyst_id = analyst_id
        self._ledger = ledger
        self._clock = clock
        self._dataset_digest = dataset_digest
        self._token = OrchestratorToken(session_id=session_id)
        self._mandates = {
            agent_id: MandateBinding.of(mandate) for agent_id, mandate in mandates.items()
        }
        self._mandate_set_hash = mandate_set_hash(self._mandates)
        self._budgets = {
            agent_id: BudgetTracker(binding.mandate) for agent_id, binding in self._mandates.items()
        }
        self._events: list[RecordedEvent] = []
        self._state = SessionState.CREATED
        self._opened_ts: datetime | None = None
        self._closed_ts: datetime | None = None

    # -- read-only surface -------------------------------------------------

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def analyst_id(self) -> str:
        return self._analyst_id

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def ledger(self) -> Ledger:
        return self._ledger

    @property
    def token(self) -> OrchestratorToken:
        """The write capability for this session's ledger.

        Exposed because D3 dispatch needs it and dispatch is orchestrator
        code: the orchestrator is the sole executor, so it is the only thing
        that should hold this. It never leaves ``ts_sentry.orchestrator``, and
        the D5 import-graph test asserts no agent module can reach the ledger
        at all.
        """
        return self._token

    @property
    def mandate_set_hash(self) -> str:
        return self._mandate_set_hash

    @property
    def dataset_digest(self) -> str:
        return self._dataset_digest

    @property
    def recorded_events(self) -> tuple[RecordedEvent, ...]:
        return tuple(self._events)

    @property
    def opened_ts(self) -> datetime | None:
        return self._opened_ts

    @property
    def closed_ts(self) -> datetime | None:
        return self._closed_ts

    def now(self) -> datetime:
        """The session's clock, for helpers that timestamp their own writes.

        ``guard_scope_request`` and ``run_gate`` take a timestamp because
        STEP-02 refused to let anything read the wall clock behind its
        caller's back. Dispatch gets it from here, so a session's entries all
        come from one clock rather than from whichever one each helper found.
        """
        return self._clock.now()

    def binding(self, agent_id: AgentId) -> MandateBinding:
        try:
            return self._mandates[agent_id]
        except KeyError as exc:
            raise UnknownAgent(
                f"no mandate for {agent_id.value} is bound to session {self._session_id}"
            ) from exc

    def budget(self, agent_id: AgentId) -> BudgetTracker:
        self.binding(agent_id)  # raises UnknownAgent for an unbound agent
        return self._budgets[agent_id]

    def event_counts(self) -> dict[str, int]:
        """Per-event-type totals for this session so far.

        ``GATE_REJECTION``, ``VERIFICATION_FAIL`` and
        ``MANDATE_VIOLATION_ATTEMPT`` counts are showcased metrics
        (ARCHITECTURE 3.2), which is why this is a first-class read rather
        than something a report recomputes by grepping the chain.
        """
        counts: dict[str, int] = {}
        for recorded in self._events:
            key = recorded.entry.event_type.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    # -- machine -----------------------------------------------------------

    def _transition(self, target: SessionState) -> None:
        if not can_transition(self._state, target):
            legal = ", ".join(sorted(state.value for state in TRANSITIONS[self._state]))
            raise IllegalTransition(
                f"session {self._session_id} cannot move from {self._state.value} to "
                f"{target.value}; legal successors: {legal or 'none'}"
            )
        self._state = target

    def append_event(
        self,
        event_type: EventType,
        *,
        agent_id: AgentId | None,
        payload: Mapping[str, object],
    ) -> RecordedEvent:
        """Append one entry to this session's ledger and keep its body.

        The single write path for D3 dispatch and D4 gate integration, so
        every session event is recorded with its payload rather than only its
        digest. ``mandate_hash`` is chosen here, not by the caller: an agent
        event carries that agent's mandate hash, and an orchestrator event
        carries the session's mandate-set hash.
        """
        binding_hash = self._mandate_set_hash if agent_id is None else self.binding(agent_id).hash
        entry = self._ledger.append(
            self._token,
            timestamp_ist=self._clock.now(),
            agent_id=agent_id,
            mandate_hash=binding_hash,
            event_type=event_type,
            payload_digest=digest_payload(payload),
        )
        recorded = RecordedEvent(entry=entry, payload=dict(payload))
        self._events.append(recorded)
        return recorded

    def attach_event(self, entry: LedgerEntry, payload: Mapping[str, object]) -> RecordedEvent:
        """Record an entry a governance helper appended on this session's behalf.

        ``gates.guard_scope_request`` writes its own
        ``MANDATE_VIOLATION_ATTEMPT`` rather than handing one back, which is
        correct: a refusal that depends on its caller remembering to ledger it
        is not a refusal. But it means the session did not see the payload, and
        an entry whose body is lost is an entry nobody can check.

        So the body comes back through here, and the session verifies it
        against the digest already in the chain before filing it. A body that
        has drifted from what was actually digested is refused rather than
        recorded, which makes this bridge self-checking instead of trusting.
        """
        if digest_payload(payload) != entry.payload_digest:
            raise ValueError(
                f"payload does not digest to entry {entry.seq}'s payload_digest; "
                "the body and the chain disagree"
            )
        recorded = RecordedEvent(entry=entry, payload=dict(payload))
        self._events.append(recorded)
        return recorded

    def open(self) -> RecordedEvent:
        """Bind the analyst, record the fleet configuration, seed the ledger."""
        self._transition(SessionState.OPEN)
        recorded = self.append_event(
            EventType.SESSION_OPEN,
            agent_id=None,
            payload={
                "session_id": self._session_id,
                "analyst_id": self._analyst_id,
                "dataset_digest": self._dataset_digest,
                "mandate_set_hash": self._mandate_set_hash,
                "mandates": {
                    agent_id.value: binding.hash
                    for agent_id, binding in sorted(self._mandates.items())
                },
            },
        )
        self._opened_ts = recorded.entry.timestamp_ist
        return recorded

    def begin_turn(self, agent_id: AgentId, *, estimated_tokens: int = 0) -> TurnStart:
        """Start an agent turn, or refuse it on an exhausted budget.

        The step is booked on start, not on completion: a turn that begins has
        consumed one of the agent's steps whether or not it produces anything,
        which is the reading that makes ``max_steps`` a ceiling on attempts
        rather than on successes.
        """
        binding = self.binding(agent_id)
        tracker = self._budgets[agent_id]
        reason = tracker.check(estimated_tokens)
        if reason is not None:
            snapshot = tracker.snapshot()
            return TurnStart(
                started=False,
                binding=None,
                close_reason=reason,
                detail=(
                    f"{agent_id.value} budget exhausted: {snapshot.tokens_remaining} tokens and "
                    f"{snapshot.steps_remaining} steps remaining under mandate "
                    f"version {binding.mandate.version}"
                ),
            )

        self._transition(SessionState.AGENT_TURN)
        tracker.record_step()
        return TurnStart(
            started=True,
            binding=binding,
            close_reason=None,
            detail=(
                f"turn started for {agent_id.value} under mandate version {binding.mandate.version}"
            ),
        )

    def end_turn(self) -> None:
        self._transition(SessionState.OPEN)

    def await_analyst(self) -> None:
        self._transition(SessionState.AWAITING_ANALYST)

    def resume(self) -> None:
        self._transition(SessionState.OPEN)

    def close(self, reason: CloseReason) -> SessionClose:
        """Close the session and return the anchor for its finished chain.

        ``event_counts`` in the payload deliberately excludes this entry: the
        counts describe what the session did, and a close event counting
        itself would make the number depend on when it was read.
        """
        self._transition(SessionState.CLOSING)
        recorded = self.append_event(
            EventType.SESSION_CLOSE,
            agent_id=None,
            payload={
                "session_id": self._session_id,
                "analyst_id": self._analyst_id,
                "close_reason": reason.value,
                "event_counts": self.event_counts(),
                "budgets": {
                    agent_id.value: tracker.snapshot().to_json_object()
                    for agent_id, tracker in sorted(self._budgets.items())
                },
            },
        )
        self._transition(SessionState.CLOSED)
        self._closed_ts = recorded.entry.timestamp_ist
        return SessionClose(entry=recorded.entry, reason=reason, head=self._ledger.head)
