# SPDX-License-Identifier: MIT
"""STEP-03 D4: the model adapter boundary.

The obligation this file carries is offline-first (STEP-03 3.4): the stub is
the CI path, the live adapter is env-gated, and credentials come only from the
environment. Two tests are structural rather than behavioral and matter most:

* ``test_no_module_imports_a_vendor_client_at_module_scope`` reads the source
  tree and asserts the offline guarantee rather than trusting it;
* ``test_live_mode_is_refused_without_the_env_flag`` and its credential
  sibling assert the gate from the outside, which is where an accidental live
  call would come from.

Nothing here reaches the network, and nothing sleeps: the retry policy's
delays go to a recording sleeper, so the backoff sequence is inspected rather
than waited out.
"""

import ast
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pytest

from ts_sentry.data.tz import IST
from ts_sentry.governance.ledger import EventType, Ledger, verify_chain
from ts_sentry.governance.mandate import AgentId, Consequence, Mandate, ToolId
from ts_sentry.governance.scopes import DataScope
from ts_sentry.orchestrator.adapter import (
    DEFAULT_LIVE_MODEL,
    ENV_LLM_MODE,
    ENV_LLM_MODEL,
    AdapterError,
    LiveAdapter,
    ModelCall,
    ModelMode,
    ModelRefusal,
    ModelRequest,
    ModelResponse,
    PermanentAdapterError,
    RecordingSleeper,
    RetryPolicy,
    StubAdapter,
    StubMode,
    TransientAdapterError,
    call_model,
    call_with_retry,
    estimate_tokens,
    resolve_mode,
)
from ts_sentry.orchestrator.core import CloseReason, FixedClock, Session
from ts_sentry.orchestrator.firewall import system_prompt

_START = datetime(2026, 7, 31, 14, 30, tzinfo=IST)
_DATASET_DIGEST = "a" * 64
_SYSTEM = system_prompt("triage.rank.v1", "Rank the flagged entities. Cite only components.")


class _RankedQueue:
    """Stand-in output schema."""


def _request(user_content: str = "case data goes here", max_output: int = 512) -> ModelRequest:
    return ModelRequest(system=_SYSTEM, user_content=user_content, max_output_tokens=max_output)


def _rng() -> np.random.Generator:
    return np.random.default_rng(42)


def _session(*, token_budget: int = 100_000) -> Session:
    mandate = Mandate(
        agent_id=AgentId.TRIAGE,
        version="1.0.0",
        consequence_ceiling=Consequence.OBSERVE,
        allowed_tools=frozenset({ToolId.RANK_TRIAGE_QUEUE}),
        data_scopes=frozenset({DataScope.COMMENT}),
        output_schema=_RankedQueue,
        token_budget=token_budget,
        max_steps=4,
    )
    session = Session(
        session_id="session-001",
        analyst_id="saif",
        ledger=Ledger(duckdb.connect(":memory:")),
        clock=FixedClock(_START),
        mandates={AgentId.TRIAGE: mandate},
        dataset_digest=_DATASET_DIGEST,
    )
    session.open()
    session.begin_turn(AgentId.TRIAGE)
    return session


# --------------------------------------------------------------------------
# Offline-first, asserted structurally
# --------------------------------------------------------------------------


def test_no_module_imports_a_vendor_client_at_module_scope() -> None:
    """The offline guarantee, read off the source tree rather than trusted.

    ``import anthropic`` at module scope anywhere in ``ts_sentry`` would make
    an offline install fail at import time and would quietly move the model
    boundary out of this one file. The live adapter imports it inside the call
    instead, so this test passes while the live path still works.
    """
    package_root = Path(__file__).resolve().parent.parent / "src" / "ts_sentry"
    offenders: list[str] = []

    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            # Module scope means a direct child of the module body.
            if node not in tree.body:
                continue
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            if any(name.split(".")[0] == "anthropic" for name in names):
                offenders.append(str(path.relative_to(package_root)))

    assert offenders == []


def test_the_stub_is_the_default_mode() -> None:
    """Fail-safe in the direction that matters: only an exact opt-in reaches
    the network."""
    assert resolve_mode({}) is ModelMode.STUB
    assert resolve_mode({ENV_LLM_MODE: ""}) is ModelMode.STUB
    assert resolve_mode({ENV_LLM_MODE: "LIVE"}) is ModelMode.STUB
    assert resolve_mode({ENV_LLM_MODE: "liv"}) is ModelMode.STUB
    assert resolve_mode({ENV_LLM_MODE: "live"}) is ModelMode.LIVE


def test_live_mode_is_refused_without_the_env_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_LLM_MODE, raising=False)

    with pytest.raises(PermanentAdapterError, match="live mode requires"):
        LiveAdapter()


def test_live_mode_is_refused_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Credentials come from the environment only. The check is for *presence*
    of the variable, never its value, so the secret has no path into this
    process's own logic."""
    monkeypatch.setenv(ENV_LLM_MODE, "live")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(PermanentAdapterError, match="ANTHROPIC_API_KEY is not set"):
        LiveAdapter()


def test_the_live_model_id_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Constructing a LiveAdapter opens nothing.

    The placeholder key below is never used: construction only reads the model
    id and checks that the credential variable *exists*. The vendor client is
    built on the first ``complete`` call, which this test does not make, so
    nothing here can reach the network or spend anything.
    """
    monkeypatch.setenv(ENV_LLM_MODE, "live")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-never-sent")

    monkeypatch.delenv(ENV_LLM_MODEL, raising=False)
    assert LiveAdapter().model_id == DEFAULT_LIVE_MODEL

    monkeypatch.setenv(ENV_LLM_MODEL, "claude-sonnet-5")
    assert LiveAdapter().model_id == "claude-sonnet-5"
    assert LiveAdapter("claude-haiku-4-5").model_id == "claude-haiku-4-5"


def test_constructing_a_live_adapter_creates_no_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The no-cost guarantee at its narrowest point.

    Even in live mode with a key present, construction must not instantiate a
    vendor client, open a connection, or send anything. Only ``complete``
    does, and only when called.
    """
    monkeypatch.setenv(ENV_LLM_MODE, "live")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-never-sent")

    adapter = LiveAdapter()

    assert adapter._client is None  # noqa: SLF001 - the point of the test


def test_the_suite_runs_with_the_live_variables_absent() -> None:
    """The conftest scrub, asserted rather than assumed.

    If this fails, someone's shell environment is leaking into the suite and
    the zero-cost guarantee is no longer a property of the repository.
    """
    import os

    for name in ("TS_SENTRY_LLM_MODE", "ANTHROPIC_API_KEY"):
        assert name not in os.environ
    assert resolve_mode() is ModelMode.STUB


def test_no_repository_file_contains_an_api_key_shaped_string() -> None:
    """Credentials never in the repo, checked rather than promised.

    Scoped to the source and test trees, which is what this test can honestly
    cover: it is a guard against a key pasted into code, not a secret scanner
    for the whole history.
    """
    # Assembled at runtime so this file does not trip its own scan, which
    # would either fail the test or force it to skip the very file most
    # likely to contain a pasted key while someone was testing live mode.
    needle = "sk" + "-ant-"
    roots = [Path(__file__).resolve().parent.parent / part for part in ("src", "tests")]
    for root in roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert needle not in text, f"possible credential in {path}"


# --------------------------------------------------------------------------
# The stub
# --------------------------------------------------------------------------


def test_the_stub_is_deterministic_across_instances() -> None:
    first = StubAdapter().complete(_request())
    second = StubAdapter().complete(_request())

    assert first == second
    assert first.stop_reason == "end_turn"


def test_the_stub_distinguishes_requests() -> None:
    a = StubAdapter().complete(_request("case A"))
    b = StubAdapter().complete(_request("case B"))

    assert a.text != b.text


def test_the_stub_can_be_given_a_caller_supplied_responder() -> None:
    """The stub knows nothing about what a rationale looks like. That contract
    belongs to D5, and encoding it here would be implementing ahead."""

    def responder(request: ModelRequest, mode: StubMode) -> str:
        return f"cites {request.user_content} under {mode.value}"

    response = StubAdapter(responder=responder).complete(_request("case-1"))

    assert response.text == "cites case-1 under faithful"


def test_the_stub_can_be_made_to_fail_and_then_succeed() -> None:
    adapter = StubAdapter(mode=StubMode.TRANSIENT, transient_failures=2)
    sleeper = RecordingSleeper()

    response = call_with_retry(
        adapter,
        _request(),
        policy=RetryPolicy(max_attempts=3),
        rng=_rng(),
        sleeper=sleeper,
    )

    assert response.text.startswith("[stub:transient]")
    assert len(sleeper.delays) == 2


def test_the_stub_can_refuse() -> None:
    """A provider declining is an expected outcome for T&S case content, not
    an exotic one."""
    with pytest.raises(ModelRefusal):
        StubAdapter(mode=StubMode.REFUSE).complete(_request())


def test_stub_construction_is_validated() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        StubAdapter(transient_failures=-1)


# --------------------------------------------------------------------------
# Retry
# --------------------------------------------------------------------------


def test_retries_stop_at_the_attempt_ceiling() -> None:
    adapter = StubAdapter(mode=StubMode.TRANSIENT, transient_failures=99)
    sleeper = RecordingSleeper()

    with pytest.raises(TransientAdapterError, match="3 attempts exhausted"):
        call_with_retry(
            adapter,
            _request(),
            policy=RetryPolicy(max_attempts=3),
            rng=_rng(),
            sleeper=sleeper,
        )

    assert len(sleeper.delays) == 2  # no sleep after the final attempt


def test_a_refusal_is_never_retried() -> None:
    """Retrying a deliberate decline is futile and rude."""
    sleeper = RecordingSleeper()

    with pytest.raises(ModelRefusal):
        call_with_retry(
            StubAdapter(mode=StubMode.REFUSE),
            _request(),
            policy=RetryPolicy(max_attempts=5),
            rng=_rng(),
            sleeper=sleeper,
        )

    assert sleeper.delays == []


def test_backoff_is_bounded_and_jittered() -> None:
    """Full jitter: each delay is uniform over the whole interval up to an
    exponentially growing ceiling, and the ceiling is capped."""
    policy = RetryPolicy(max_attempts=8, base_delay_s=0.5, max_delay_s=4.0)
    rng = _rng()

    delays = [policy.delay_for(attempt, rng) for attempt in range(8)]

    for attempt, delay in enumerate(delays):
        ceiling = min(4.0, 0.5 * (2**attempt))
        assert 0.0 <= delay <= ceiling
    assert len(set(delays)) > 1  # jittered, not a fixed schedule


def test_backoff_is_reproducible_for_a_seed() -> None:
    policy = RetryPolicy()

    assert [policy.delay_for(i, _rng()) for i in range(3)] == [
        policy.delay_for(i, _rng()) for i in range(3)
    ]


def test_retry_policy_validates_its_own_shape() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError, match="max_delay_s"):
        RetryPolicy(base_delay_s=5.0, max_delay_s=1.0)


# --------------------------------------------------------------------------
# Requests, responses, estimates
# --------------------------------------------------------------------------


def test_a_request_is_validated() -> None:
    with pytest.raises(ValueError, match="user_content must be non-empty"):
        ModelRequest(system=_SYSTEM, user_content="  ", max_output_tokens=10)
    with pytest.raises(ValueError, match="max_output_tokens must be positive"):
        ModelRequest(system=_SYSTEM, user_content="x", max_output_tokens=0)


def test_a_request_digest_covers_the_system_prompt_by_hash() -> None:
    other = system_prompt("triage.rank.v2", "A different instruction entirely.")

    assert (
        _request().digest
        != ModelRequest(
            system=other, user_content="case data goes here", max_output_tokens=512
        ).digest
    )


def test_truncation_is_a_first_class_property() -> None:
    """A truncated rationale that still parses would otherwise be verified and
    accepted as though it were whole."""
    response = ModelResponse(
        text="half a rationale",
        input_tokens=10,
        output_tokens=512,
        adapter_id="stub/faithful",
        model_id="x",
        stop_reason="max_tokens",
    )

    assert response.truncated is True
    assert response.to_ledger_payload()["truncated"] is True


def test_response_token_counts_are_validated() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        ModelResponse(
            text="x",
            input_tokens=-1,
            output_tokens=0,
            adapter_id="a",
            model_id="m",
            stop_reason="end_turn",
        )


def test_the_estimate_is_never_zero() -> None:
    """A zero estimate would let an exhausted agent pass the pre-flight check
    on an empty-looking prompt."""
    assert estimate_tokens("") == 1
    assert estimate_tokens("a" * 400) == 100


# --------------------------------------------------------------------------
# Budgeted, ledgered calling
# --------------------------------------------------------------------------


def test_a_call_ledgers_prompt_sent_and_books_the_actuals() -> None:
    session = _session()
    before = session.budget(AgentId.TRIAGE).snapshot().tokens_spent

    call = call_model(
        session,
        AgentId.TRIAGE,
        StubAdapter(),
        _request(),
        policy=RetryPolicy(),
        rng=_rng(),
        sleeper=RecordingSleeper(),
    )

    assert call.completed
    assert call.response is not None
    assert [entry.event_type for entry in call.ledgered] == [EventType.PROMPT_SENT]
    spent = session.budget(AgentId.TRIAGE).snapshot().tokens_spent
    assert spent == before + call.response.total_tokens
    assert verify_chain(session.ledger.read_all()).intact


def test_the_prompt_sent_payload_names_the_prompt_without_copying_it() -> None:
    session = _session()

    call_model(
        session,
        AgentId.TRIAGE,
        StubAdapter(),
        _request(),
        policy=RetryPolicy(),
        rng=_rng(),
        sleeper=RecordingSleeper(),
        firewall_payload={"signal_count": 3, "pattern_set_version": "1.0.0"},
    )

    payload = session.recorded_events[-1].payload
    assert payload["system_prompt_sha256"] == _SYSTEM.sha256
    assert _SYSTEM.text not in str(payload)
    assert payload["firewall"] == {"signal_count": 3, "pattern_set_version": "1.0.0"}


def test_an_exhausted_budget_refuses_before_sending() -> None:
    """Preventive, not detective: the agent does not spend and then get told."""
    session = _session(token_budget=10)
    sent: list[ModelRequest] = []

    def responder(request: ModelRequest, mode: StubMode) -> str:
        sent.append(request)
        return "should not happen"

    call = call_model(
        session,
        AgentId.TRIAGE,
        StubAdapter(responder=responder),
        _request(),
        policy=RetryPolicy(),
        rng=_rng(),
        sleeper=RecordingSleeper(),
    )

    assert not call.completed
    assert call.close_reason is CloseReason.TOKEN_BUDGET_EXHAUSTED
    assert sent == []
    assert call.ledgered == ()


def test_an_adapter_failure_closes_the_turn_rather_than_raising() -> None:
    session = _session()

    call = call_model(
        session,
        AgentId.TRIAGE,
        StubAdapter(mode=StubMode.TRANSIENT, transient_failures=99),
        _request(),
        policy=RetryPolicy(max_attempts=2),
        rng=_rng(),
        sleeper=RecordingSleeper(),
    )

    assert not call.completed
    assert call.close_reason is CloseReason.DISPATCH_ERROR
    assert [entry.event_type for entry in call.ledgered] == [
        EventType.PROMPT_SENT,
        EventType.VERIFICATION_FAIL,
    ]
    assert verify_chain(session.ledger.read_all()).intact


def test_a_provider_refusal_is_recorded_and_not_retried() -> None:
    session = _session()
    sleeper = RecordingSleeper()

    call = call_model(
        session,
        AgentId.TRIAGE,
        StubAdapter(mode=StubMode.REFUSE),
        _request(),
        policy=RetryPolicy(max_attempts=5),
        rng=_rng(),
        sleeper=sleeper,
    )

    assert not call.completed
    assert "ModelRefusal" in call.detail
    assert sleeper.delays == []


def test_model_call_shapes_are_enforced() -> None:
    with pytest.raises(ValueError, match="names the reason it did not"):
        ModelCall(response=None, close_reason=None, detail="", ledgered=())


def test_every_adapter_error_shares_one_base() -> None:
    """So a caller that wants to catch everything from this boundary can catch
    exactly one thing."""
    for error in (TransientAdapterError, PermanentAdapterError, ModelRefusal):
        assert issubclass(error, AdapterError)
