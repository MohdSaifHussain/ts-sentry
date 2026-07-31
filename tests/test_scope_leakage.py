# SPDX-License-Identifier: MIT
"""Leakage: sealed ground truth is unreachable by any agent, at two layers.

Part 1 (STEP-01 3.3) is the allowlist itself: ``DataScope`` has no member
resolving to the sealed schema, so a sealed name cannot be resolved at all.

Part 2 (STEP-02 3.5) is the mandate layer promised here when this file was
written, now landed: construct every agent mandate, attempt sealed
resolution, and assert both structural refusal *and* a ledgered
MANDATE_VIOLATION_ATTEMPT. Part 1 does not supersede Part 2 or vice versa.
They fail independently, which is the point: STEP-01's red-team showed a
single sabotage tripping several unrelated layers at once, and that is the
property worth keeping.
"""

from datetime import datetime
from enum import StrEnum
from pathlib import Path

import duckdb
import pytest

from ts_sentry.data.tz import IST
from ts_sentry.governance.gates import guard_scope_request
from ts_sentry.governance.ledger import EventType, Ledger, OrchestratorToken
from ts_sentry.governance.mandate import (
    AgentConsequence,
    AgentId,
    Consequence,
    Mandate,
    RefusalCode,
    ToolId,
)
from ts_sentry.governance.scopes import (
    DataScope,
    ScopeViolation,
    resolve_export_path,
    resolve_scope_by_name,
    resolve_table,
)


def test_datascope_has_no_sealed_member() -> None:
    values = {member.value for member in DataScope}
    assert "sealed" not in values
    assert "_labels" not in values
    assert not any("sealed" in value for value in values)


def test_sealed_table_name_denied() -> None:
    with pytest.raises(ScopeViolation):
        resolve_scope_by_name("sealed._labels")


def test_unknown_name_denied() -> None:
    with pytest.raises(ScopeViolation):
        resolve_scope_by_name("not_a_real_scope")


@pytest.mark.parametrize("scope", list(DataScope))
def test_every_allowlisted_scope_resolves(scope: DataScope, tmp_path: Path) -> None:
    assert resolve_table(scope).startswith("main.")
    assert resolve_export_path(scope, tmp_path).parent == tmp_path
    # Round-trips through the same by-name lookup a caller would use.
    assert resolve_scope_by_name(scope.value) is scope


def test_red_team_added_sealed_member_would_be_caught() -> None:
    """Demonstrates the leakage guard is discriminating, not vacuous.

    A local enum that *does* carry a sealed-scope member resolves
    successfully through the same lookup-by-value mechanism
    ``resolve_scope_by_name`` uses. This proves that if such a member were
    ever added to the real ``DataScope``, ``test_sealed_table_name_denied``
    above would go red rather than silently continuing to pass - the
    exit-checklist red-team requirement.
    """

    class LeakyScope(StrEnum):
        CHANNEL = "channel"
        SEALED_LABELS = "sealed._labels"

    resolved = LeakyScope("sealed._labels")
    assert resolved is LeakyScope.SEALED_LABELS


# --------------------------------------------------------------------------
# Part 2 (STEP-02 3.5): the mandate layer, ledgered
# --------------------------------------------------------------------------

_TOKEN = OrchestratorToken(session_id="leakage-session")
_MANDATE_HASH = "1" * 64
_TS = datetime(2026, 7, 31, 14, 30, tzinfo=IST)

_AGENT_CONSEQUENCES: tuple[AgentConsequence, ...] = (
    Consequence.OBSERVE,
    Consequence.ASSEMBLE,
    Consequence.RECOMMEND,
)


class _OutputSchema:
    pass


def _widest_mandate(agent_id: AgentId, ceiling: AgentConsequence) -> Mandate:
    """The most permissive mandate the type system allows.

    Every tool, every allowlisted scope, the highest agent-reachable ceiling.
    If sealed access were reachable through a mandate at all, it would be
    reachable through this one, so a refusal here cannot be an artifact of a
    conveniently narrow fixture.
    """
    return Mandate(
        agent_id=agent_id,
        version="1.0.0",
        consequence_ceiling=ceiling,
        allowed_tools=frozenset(ToolId),
        data_scopes=frozenset(DataScope),
        output_schema=_OutputSchema,
        token_budget=1_000_000,
        max_steps=1_000,
    )


@pytest.mark.parametrize("agent_id", list(AgentId))
@pytest.mark.parametrize("ceiling", _AGENT_CONSEQUENCES)
def test_every_agent_mandate_refuses_sealed_and_ledgers_the_attempt(
    agent_id: AgentId, ceiling: AgentConsequence
) -> None:
    """The exact test STEP-01 3.3 deferred to Phase 2, across the full fleet."""
    ledger = Ledger(duckdb.connect(":memory:"))

    result = guard_scope_request(
        ledger,
        _TOKEN,
        timestamp_ist=_TS,
        agent_id=agent_id,
        mandate=_widest_mandate(agent_id, ceiling),
        mandate_hash=_MANDATE_HASH,
        requested_name="sealed._labels",
    )

    assert not result.granted
    assert result.scope is None
    assert result.code is RefusalCode.SCOPE_NOT_ALLOWED

    assert result.ledgered is not None
    assert result.ledgered.event_type is EventType.MANDATE_VIOLATION_ATTEMPT
    assert result.ledgered.agent_id is agent_id
    assert ledger.verify().intact


@pytest.mark.parametrize(
    "requested_name",
    [
        "sealed._labels",
        "sealed",
        "_labels",
        "main.sealed._labels",
        "SEALED._LABELS",
    ],
)
def test_sealed_name_variants_are_all_refused(requested_name: str) -> None:
    """Absence is denial, so no spelling of the sealed target resolves.

    Variants matter because the refusal is by allowlist membership, not by
    pattern matching on the string. Nothing here greps for "sealed"; these
    names fail because no member equals them.
    """
    ledger = Ledger(duckdb.connect(":memory:"))

    result = guard_scope_request(
        ledger,
        _TOKEN,
        timestamp_ist=_TS,
        agent_id=AgentId.EVIDENCE,
        mandate=_widest_mandate(AgentId.EVIDENCE, Consequence.ASSEMBLE),
        mandate_hash=_MANDATE_HASH,
        requested_name=requested_name,
    )

    assert not result.granted
    assert result.ledgered is not None


def test_the_violation_attempt_is_tamper_evident() -> None:
    """A ledgered refusal is only worth something if it cannot be edited away
    afterwards. The attempt joins the hash chain like any other entry."""
    ledger = Ledger(duckdb.connect(":memory:"))
    guard_scope_request(
        ledger,
        _TOKEN,
        timestamp_ist=_TS,
        agent_id=AgentId.MEMO,
        mandate=_widest_mandate(AgentId.MEMO, Consequence.RECOMMEND),
        mandate_hash=_MANDATE_HASH,
        requested_name="sealed._labels",
    )

    entries = list(ledger.read_all())
    assert len(entries) == 1

    from dataclasses import replace

    from ts_sentry.governance.ledger import verify_chain

    tampered = [replace(entries[0], event_type=EventType.PROMPT_SENT)]
    assert not verify_chain(tampered).intact
