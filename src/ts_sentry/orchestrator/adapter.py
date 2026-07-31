# SPDX-License-Identifier: MIT
"""D4: the model adapter (STEP-03 D4, ARCHITECTURE 5).

The single boundary through which a model is ever called. Nothing else in this
codebase imports an LLM client, so "where does this system talk to a model"
has exactly one answer, and the offline guarantee is checkable by reading one
file's imports.

Offline-first (STEP-03 3.4)
--------------------------
``StubAdapter`` is the CI path: deterministic, seeded, no network, and the
default everywhere. ``LiveAdapter`` exists only when ``TS_SENTRY_LLM_MODE`` is
set to ``live``, and it imports the vendor client *inside the call* so an
offline run never even loads it.

Credentials come from the environment, and this repository never touches the
secret. ``LiveAdapter`` checks that ``ANTHROPIC_API_KEY`` is *present* and then
hands construction to the vendor client, which reads it itself. There is no
code path here that reads the value, logs it, or could put it in a ledger
payload.

One retry authority, not two
----------------------------
STEP-03 D4 asks for retries with jitter. The vendor SDK also retries on its
own by default, and two retry layers multiply into a surprising number of
attempts while making the step and token accounting wrong. So the SDK's
retries are switched off (``max_retries=0``) and ``RetryPolicy`` here is the
only one, with full jitter over a seeded generator so a session stays
reproducible.

Token accounting, stated at its true precision
----------------------------------------------
Budgets are enforced *before* spending (``call_model`` asks the tracker first),
which needs an estimate of a request's cost before it is sent. An exact count
would mean a ``count_tokens`` round trip, which is a network call, so the
pre-flight number is a documented character-based approximation
(``estimate_tokens``) and can be wrong in either direction. What is exact is
the *post-flight* figure: usage reported by the provider, or the stub's own
deterministic count. The consequence is honest and small: a request may be let
through on an optimistic estimate and push the agent past its ceiling, at which
point the next pre-flight check refuses and the session closes cleanly. The
ceiling is enforced, not the individual call.

Consulted while writing the live path, per CLAUDE.md's official-sources rule:
the Anthropic API reference bundled as the ``claude-api`` skill (models and
IDs, ``messages.create`` shape, ``usage`` fields, typed exception classes,
retry semantics, ``stop_reason`` values). Three things it settles that memory
would have got wrong: on ``claude-opus-5`` thinking is on by default so
``max_tokens`` bounds thinking *and* text together; ``temperature`` / ``top_p``
/ ``top_k`` are rejected with a 400 and are therefore not sent; and a request
can come back HTTP 200 with ``stop_reason == "refusal"``, so ``content`` must
never be read before that is checked.
"""

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

import numpy as np

from ts_sentry.governance.canonical import digest_fields
from ts_sentry.governance.ledger import EventType, LedgerEntry
from ts_sentry.governance.mandate import AgentId
from ts_sentry.orchestrator.core import CloseReason, Session
from ts_sentry.orchestrator.firewall import SystemPrompt

__all__ = [
    "DEFAULT_LIVE_MODEL",
    "ENV_LLM_MODE",
    "ENV_LLM_MODEL",
    "AdapterError",
    "LiveAdapter",
    "ModelAdapter",
    "ModelCall",
    "ModelMode",
    "ModelRefusal",
    "ModelRequest",
    "ModelResponse",
    "PermanentAdapterError",
    "RetryPolicy",
    "Sleeper",
    "StubAdapter",
    "StubMode",
    "TransientAdapterError",
    "call_model",
    "call_with_retry",
    "estimate_tokens",
    "resolve_mode",
]

ENV_LLM_MODE = "TS_SENTRY_LLM_MODE"
ENV_LLM_MODEL = "TS_SENTRY_LLM_MODEL"
ENV_API_KEY = "ANTHROPIC_API_KEY"

DEFAULT_LIVE_MODEL = "claude-opus-5"
"""Live model id. Current and most capable at the time of writing; overridable
through ``TS_SENTRY_LLM_MODEL`` so a model change is a deployment decision
rather than a code change."""

_CHARS_PER_TOKEN = 4
"""Divisor for the pre-flight estimate. A rule of thumb, not a measurement;
see the module docstring for why an exact count is not available offline."""


class ModelMode(StrEnum):
    STUB = "stub"
    LIVE = "live"


class AdapterError(Exception):
    """Base for every failure at the model boundary."""


class TransientAdapterError(AdapterError):
    """Worth retrying: rate limits, server errors, connection failures."""


class PermanentAdapterError(AdapterError):
    """Not worth retrying: authentication, malformed request, unknown model.

    Retrying these burns budget to arrive at the same answer, so the policy
    below re-raises them immediately rather than backing off.
    """


class ModelRefusal(AdapterError):
    """The provider returned a successful response that declines the request.

    Its own class because it is neither a transport failure nor a governance
    refusal by this system: the model declined. Case content in a Trust and
    Safety workload is exactly the material a provider's safety classifiers
    are built to notice, so this is an expected outcome here rather than an
    exotic one, and it must not be retried or mistaken for a bug.
    """


# --------------------------------------------------------------------------
# Requests and responses
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """One model call, with the roles kept apart by type.

    ``system`` is a ``SystemPrompt``, never a ``str``. That is the D2
    invariant made structural at this boundary: a caller cannot concatenate
    case content into the system role, because the system role does not accept
    a string it could concatenate into.
    """

    system: SystemPrompt
    user_content: str
    max_output_tokens: int

    def __post_init__(self) -> None:
        if not self.user_content.strip():
            raise ValueError("user_content must be non-empty")
        if self.max_output_tokens <= 0:
            raise ValueError(f"max_output_tokens must be positive; got {self.max_output_tokens}")

    @property
    def digest(self) -> str:
        """Identity of this request, for deterministic stubbing and for the
        ``PROMPT_SENT`` payload. Covers the system prompt by its hash rather
        than its text, so the payload names the prompt without copying it."""
        return digest_fields(
            "ts-sentry/model-request/v1",
            self.system.sha256,
            self.user_content,
            str(self.max_output_tokens),
        )

    def estimated_input_tokens(self) -> int:
        return estimate_tokens(self.system.text) + estimate_tokens(self.user_content)


def estimate_tokens(text: str) -> int:
    """Pre-flight approximation. Not a measurement.

    Deliberately crude and deliberately not hidden behind a precise-sounding
    name. The exact figure needs a provider round trip, which is a network
    call this system will not make to decide whether it may make a network
    call.
    """
    return max(1, len(text) // _CHARS_PER_TOKEN)


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """What came back, plus what it cost."""

    text: str
    input_tokens: int
    output_tokens: int
    adapter_id: str
    model_id: str
    stop_reason: str

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token counts must not be negative")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def truncated(self) -> bool:
        """The response hit its output ceiling and is incomplete.

        Worth a first-class property: a truncated rationale that still parses
        would otherwise be verified and accepted as if it were whole.
        """
        return self.stop_reason == "max_tokens"

    def to_ledger_payload(self) -> dict[str, object]:
        return {
            "adapter_id": self.adapter_id,
            "model_id": self.model_id,
            "stop_reason": self.stop_reason,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "truncated": self.truncated,
        }


class ModelAdapter(Protocol):
    """Provider-agnostic model boundary."""

    @property
    def adapter_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    def complete(self, request: ModelRequest, /) -> ModelResponse: ...


# --------------------------------------------------------------------------
# Retry
# --------------------------------------------------------------------------


class Sleeper(Protocol):
    """Injected so no test ever sleeps, and so a caller can cancel."""

    def sleep(self, seconds: float) -> None: ...


class RealSleeper:
    __slots__ = ()

    def sleep(self, seconds: float) -> None:  # pragma: no cover - wall-clock wait
        import time

        time.sleep(seconds)


class RecordingSleeper:
    """Records the delays it was asked for instead of waiting.

    Lives here rather than in the tests because the retry policy's whole
    observable behavior *is* the sequence of delays, and a helper that makes
    that inspectable belongs beside the thing it inspects.
    """

    __slots__ = ("delays",)

    def __init__(self) -> None:
        self.delays: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Exponential backoff with full jitter.

    Full jitter (uniform over the whole interval) rather than equal jitter,
    because the failure this guards against is a fleet of clients retrying in
    lockstep after a shared outage, and only full jitter spreads them evenly.

    The jitter is drawn from a seeded generator, so a session replays
    identically. That is not a contradiction: jitter needs to be
    *uncorrelated between clients*, not unpredictable.
    """

    max_attempts: int = 3
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be at least 1; got {self.max_attempts}")
        if self.base_delay_s < 0 or self.max_delay_s < self.base_delay_s:
            raise ValueError("delays must be non-negative with max_delay_s >= base_delay_s")

    def delay_for(self, attempt: int, rng: np.random.Generator) -> float:
        """Delay before retrying, where ``attempt`` is the one that just failed."""
        ceiling = min(self.max_delay_s, self.base_delay_s * (2**attempt))
        return float(rng.uniform(0.0, ceiling))


def call_with_retry(
    adapter: ModelAdapter,
    request: ModelRequest,
    *,
    policy: RetryPolicy,
    rng: np.random.Generator,
    sleeper: Sleeper,
) -> ModelResponse:
    """Call ``adapter``, retrying transient failures only.

    ``PermanentAdapterError`` and ``ModelRefusal`` propagate on the first
    occurrence. A refusal in particular must never be retried: the provider
    declined deliberately, and hammering it is both futile and rude.
    """
    last: TransientAdapterError | None = None
    for attempt in range(policy.max_attempts):
        try:
            return adapter.complete(request)
        except TransientAdapterError as exc:
            last = exc
            if attempt + 1 >= policy.max_attempts:
                break
            sleeper.sleep(policy.delay_for(attempt, rng))
    assert last is not None  # the loop only exits here after a transient failure
    raise TransientAdapterError(
        f"{policy.max_attempts} attempts exhausted; last failure: {last}"
    ) from last


# --------------------------------------------------------------------------
# The stub: the CI path
# --------------------------------------------------------------------------


class StubMode(StrEnum):
    """How the stub behaves.

    ``OVERCLAIM`` and ``TRANSIENT`` are not test scaffolding smuggled into
    production code; they are how the governance layer's failure paths get
    demonstrated. A verifier that has never rejected anything is a verifier
    nobody has tested, and STEP-02 recorded that reasoning about
    ``VERIFICATION_FAIL`` counts being a showcased metric.
    """

    FAITHFUL = "faithful"
    OVERCLAIM = "overclaim"
    TRANSIENT = "transient"
    REFUSE = "refuse"


type Responder = Callable[[ModelRequest, StubMode], str]
"""Produces the stub's text for a request. Injected so this module never has
to know what a triage rationale looks like: that contract belongs to D5, and
encoding it here would be implementing ahead of the STEP."""


def _default_responder(request: ModelRequest, mode: StubMode) -> str:
    """Deterministic filler for callers with no format of their own."""
    return f"[stub:{mode.value}] {request.digest[:16]}"


@dataclass(slots=True)
class StubAdapter:
    """Deterministic, offline, seeded. The default and the CI path.

    Determinism is by construction rather than by discipline: the text comes
    from the responder, and the token counts are computed from that text, so
    the same request produces the same response and the same cost forever.
    """

    seed: int = 42
    mode: StubMode = StubMode.FAITHFUL
    responder: Responder = _default_responder
    transient_failures: int = 2
    _failures_left: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        if self.transient_failures < 0:
            raise ValueError("transient_failures must not be negative")
        self._failures_left = self.transient_failures if self.mode is StubMode.TRANSIENT else 0

    @property
    def adapter_id(self) -> str:
        return f"stub/{self.mode.value}"

    @property
    def model_id(self) -> str:
        return "deterministic-stub-v1"

    def complete(self, request: ModelRequest, /) -> ModelResponse:
        if self._failures_left > 0:
            self._failures_left -= 1
            raise TransientAdapterError(
                f"stub transient failure ({self._failures_left} remaining before success)"
            )
        if self.mode is StubMode.REFUSE:
            raise ModelRefusal("stub adapter is configured to refuse")

        text = self.responder(request, self.mode)
        return ModelResponse(
            text=text,
            input_tokens=request.estimated_input_tokens(),
            output_tokens=estimate_tokens(text),
            adapter_id=self.adapter_id,
            model_id=self.model_id,
            stop_reason="end_turn",
        )


# --------------------------------------------------------------------------
# The live path: env-gated, lazily imported
# --------------------------------------------------------------------------

_RETRYABLE_STATUS_FLOOR = 500


class LiveAdapter:
    """Calls a real model. Constructed only in live mode.

    The vendor client is imported inside ``complete`` rather than at module
    scope, so an offline run never imports it and a missing optional
    dependency is an error only for the caller who actually asked for live
    mode.
    """

    __slots__ = ("_client", "_max_retries", "_model_id")

    def __init__(self, model_id: str | None = None) -> None:
        if os.environ.get(ENV_LLM_MODE) != ModelMode.LIVE.value:
            raise PermanentAdapterError(
                f"live mode requires {ENV_LLM_MODE}={ModelMode.LIVE.value}; "
                "the stub adapter is the default and the CI path"
            )
        if ENV_API_KEY not in os.environ:
            raise PermanentAdapterError(
                f"{ENV_API_KEY} is not set. Credentials come from the environment only "
                "and are never read, stored, or logged by this repository"
            )
        self._model_id = model_id or os.environ.get(ENV_LLM_MODEL) or DEFAULT_LIVE_MODEL
        self._client: object | None = None
        # The vendor SDK retries by default. Ours is the only retry authority
        # (see the module docstring), so its is switched off.
        self._max_retries = 0

    @property
    def adapter_id(self) -> str:
        return "anthropic/messages"

    @property
    def model_id(self) -> str:
        return self._model_id

    def complete(self, request: ModelRequest, /) -> ModelResponse:  # pragma: no cover
        """Send one request. Not exercised by the offline suite, by design.

        Marked no-cover because covering it would mean either a network call
        in CI or a mock of the vendor client, and a mock would assert only
        that this code matches the shape someone imagined for the SDK. The
        live path is verified by the documented live-mode smoke run instead,
        and that limitation is recorded rather than papered over.
        """
        import anthropic

        if self._client is None:
            self._client = anthropic.Anthropic(max_retries=self._max_retries)
        client = self._client
        assert isinstance(client, anthropic.Anthropic)

        try:
            message = client.messages.create(
                model=self._model_id,
                max_tokens=request.max_output_tokens,
                system=[{"type": "text", "text": request.system.text}],
                messages=[{"role": "user", "content": request.user_content}],
            )
        except anthropic.RateLimitError as exc:
            raise TransientAdapterError(f"rate limited: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise TransientAdapterError(f"connection failure: {exc}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= _RETRYABLE_STATUS_FLOOR:
                raise TransientAdapterError(f"server error {exc.status_code}: {exc}") from exc
            raise PermanentAdapterError(f"API error {exc.status_code}: {exc}") from exc

        # Checked before `content` is touched: a refusal returns HTTP 200 with
        # an empty or partial content list, so reading content first would
        # either raise IndexError or silently accept a truncated answer.
        if message.stop_reason == "refusal":
            raise ModelRefusal(f"provider declined the request: {message.stop_details}")

        text = "".join(block.text for block in message.content if block.type == "text")
        return ModelResponse(
            text=text,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            adapter_id=self.adapter_id,
            model_id=self._model_id,
            stop_reason=str(message.stop_reason),
        )


def resolve_mode(environ: Mapping[str, str] | None = None) -> ModelMode:
    """Read the mode from the environment. Anything but ``live`` is stub.

    Fail-safe in the direction that matters: a typo, an empty value, or an
    unset variable all mean offline. Reaching the network is the outcome that
    has to be asked for explicitly.
    """
    source = os.environ if environ is None else environ
    return ModelMode.LIVE if source.get(ENV_LLM_MODE) == ModelMode.LIVE.value else ModelMode.STUB


# --------------------------------------------------------------------------
# Budgeted, ledgered calling
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelCall:
    """Outcome of a budgeted model call. A value, never an exception."""

    response: ModelResponse | None
    close_reason: CloseReason | None
    detail: str
    ledgered: tuple[LedgerEntry, ...]

    def __post_init__(self) -> None:
        if (self.response is None) is (self.close_reason is None):
            raise ValueError(
                "a model call either produced a response or names the reason it did not"
            )

    @property
    def completed(self) -> bool:
        return self.response is not None


def call_model(
    session: Session,
    agent_id: AgentId,
    adapter: ModelAdapter,
    request: ModelRequest,
    *,
    policy: RetryPolicy,
    rng: np.random.Generator,
    sleeper: Sleeper,
    firewall_payload: Mapping[str, object] | None = None,
) -> ModelCall:
    """Check the budget, ledger the prompt, call, then book the actuals.

    In that order, and the order is the control. The budget is asked *before*
    the call, so an exhausted agent does not spend and then get told; the
    ``PROMPT_SENT`` entry is written *before* the call, so a request that
    vanishes into a timeout still left a record that it was sent.

    ``firewall_payload`` carries the D2 signals for the entry, so a prompt
    that contained an injection attempt is ledgered as such at the moment it
    was sent rather than reconstructed afterwards.
    """
    tracker = session.budget(agent_id)
    estimate = request.estimated_input_tokens() + request.max_output_tokens
    # Tokens only. The caller is inside a turn whose step ``begin_turn`` already
    # booked, and re-checking the step ceiling here would refuse work the
    # session had just authorized. See ``BudgetTracker.check``.
    reason = tracker.check(estimate, require_step=False)
    if reason is not None:
        snapshot = tracker.snapshot()
        return ModelCall(
            response=None,
            close_reason=reason,
            detail=(
                f"model call refused before sending: {agent_id.value} has "
                f"{snapshot.tokens_remaining} tokens and {snapshot.steps_remaining} steps "
                f"left, and this call was estimated at {estimate}"
            ),
            ledgered=(),
        )

    payload: dict[str, object] = {
        "adapter_id": adapter.adapter_id,
        "model_id": adapter.model_id,
        "system_prompt_id": request.system.prompt_id,
        "system_prompt_sha256": request.system.sha256,
        "request_digest": request.digest,
        "estimated_tokens": estimate,
        "max_output_tokens": request.max_output_tokens,
    }
    if firewall_payload is not None:
        payload["firewall"] = dict(firewall_payload)

    ledgered = [
        session.append_event(EventType.PROMPT_SENT, agent_id=agent_id, payload=payload).entry
    ]

    try:
        response = call_with_retry(adapter, request, policy=policy, rng=rng, sleeper=sleeper)
    except AdapterError as exc:
        detail = f"{type(exc).__name__}: {exc}"
        ledgered.append(
            session.append_event(
                EventType.VERIFICATION_FAIL,
                agent_id=agent_id,
                payload={"request_digest": request.digest, "adapter_error": detail},
            ).entry
        )
        return ModelCall(
            response=None,
            close_reason=CloseReason.DISPATCH_ERROR,
            detail=detail,
            ledgered=tuple(ledgered),
        )

    tracker.record_tokens(response.total_tokens)
    return ModelCall(
        response=response,
        close_reason=None,
        detail=f"{adapter.adapter_id} returned {response.output_tokens} output tokens",
        ledgered=tuple(ledgered),
    )
