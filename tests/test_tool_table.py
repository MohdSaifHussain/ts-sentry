# SPDX-License-Identifier: MIT
"""STEP-03 D3: the allowlisted tool table, and the no-orphan ToolId rule.

This file carries the second obligation the STEP-02 Outcome recorded, under
the reading Saif chose: entry per ID now, handler per ID by its own phase.

Three tests do the work, and they fail for different reasons on purpose:

* every ``ToolId`` has an entry (the rule as recorded at the enum's
  definition site);
* the pending set is pinned exactly, so an entry silently added or a handler
  silently dropped fails rather than passes quietly;
* the countdown, which turns "later" into a deadline. It lands with D5, when
  this phase's own handler exists; until then it would be asserting a rule
  this phase has not yet satisfied.
"""

import pytest

from ts_sentry.governance.mandate import Consequence, ToolId
from ts_sentry.orchestrator.tools import (
    IMPLEMENTATION_PHASE,
    TOOL_TABLE,
    ToolEntry,
    ToolViolation,
    pending_handlers,
    required_scope_names,
    resolve_tool_by_name,
)


def test_every_tool_id_has_an_allowlist_entry() -> None:
    """The no-orphan rule, asserted from STEP-03 onward exactly as the
    STEP-02 Outcome required. A ``ToolId`` with no entry is a name that
    resolves to nothing, and a mandate could allowlist it."""
    assert set(TOOL_TABLE) == set(ToolId)


def test_each_entry_is_keyed_by_the_tool_it_declares() -> None:
    for tool_id, entry in TOOL_TABLE.items():
        assert entry.tool_id is tool_id


def test_the_pending_handler_set_is_pinned() -> None:
    """Pinned exactly, in due order.

    This is the snapshot that makes the shrinking visible commit by commit. It
    fails when a handler lands (update the list, one line) and equally when
    one silently disappears, which is the direction nobody would notice.

    At the D3 commit the table declares four tools and executes none: dispatch
    is a mechanism, and the first real tool is the triage ranker in D5. Saying
    so here is more honest than back-dating a handler to make the list shorter.
    """
    assert pending_handlers(TOOL_TABLE) == (
        ToolId.RANK_TRIAGE_QUEUE,
        ToolId.RUN_PARAMETERIZED_PIVOT,
        ToolId.RESOLVE_POLICY_CITATION,
        ToolId.RUN_PROMPT_EVAL,
    )


def test_every_pending_handler_has_a_deadline_in_a_later_phase() -> None:
    """The half of the countdown that binds today.

    Each pending tool names the phase that owes its handler, and none of those
    deadlines may be in the past. The complementary half, that nothing due at
    or before the current phase is still pending, lands in D5 with this
    phase's own handler.
    """
    for tool_id in pending_handlers(TOOL_TABLE):
        entry = TOOL_TABLE[tool_id]
        assert entry.handler_due_step >= IMPLEMENTATION_PHASE
        assert entry.handler is None


def test_the_due_steps_match_the_phase_that_owns_each_agent() -> None:
    """ARCHITECTURE 11: evidence in phase 4, memo in 5, prompt-eval in 6.

    Written out so that a due step cannot quietly slip to a later phase to
    avoid a failing countdown, which is the obvious way to defeat it.
    """
    assert TOOL_TABLE[ToolId.RANK_TRIAGE_QUEUE].handler_due_step == 3
    assert TOOL_TABLE[ToolId.RUN_PARAMETERIZED_PIVOT].handler_due_step == 4
    assert TOOL_TABLE[ToolId.RESOLVE_POLICY_CITATION].handler_due_step == 5
    assert TOOL_TABLE[ToolId.RUN_PROMPT_EVAL].handler_due_step == 6


def test_no_entry_declares_enforce() -> None:
    """The table is a second place the ENFORCE invariant has to hold.

    ``validate`` refuses ENFORCE under every mandate, but an entry declaring
    it would mean the allowlist itself proposed an action no agent may take.
    """
    for entry in TOOL_TABLE.values():
        assert entry.consequence is not Consequence.ENFORCE


def test_declared_consequences_match_the_architecture_ceilings() -> None:
    assert TOOL_TABLE[ToolId.RANK_TRIAGE_QUEUE].consequence is Consequence.OBSERVE
    assert TOOL_TABLE[ToolId.RUN_PARAMETERIZED_PIVOT].consequence is Consequence.ASSEMBLE
    assert TOOL_TABLE[ToolId.RESOLVE_POLICY_CITATION].consequence is Consequence.RECOMMEND
    assert TOOL_TABLE[ToolId.RUN_PROMPT_EVAL].consequence is Consequence.OBSERVE


def test_tool_names_resolve_by_allowlist_and_absence_is_denial() -> None:
    assert resolve_tool_by_name("rank_triage_queue") is ToolId.RANK_TRIAGE_QUEUE

    for name in ("", "sealed._labels", "RANK_TRIAGE_QUEUE", "rank_triage_queue "):
        with pytest.raises(ToolViolation):
            resolve_tool_by_name(name)


def test_required_scope_names_are_the_strings_an_agent_must_ask_for() -> None:
    """Names, not members. A helper handing back validated ``DataScope``
    values would let a proposal skip the boundary it exists to cross."""
    names = required_scope_names(TOOL_TABLE[ToolId.RANK_TRIAGE_QUEUE])

    assert names == tuple(sorted(names))
    assert all(isinstance(name, str) for name in names)
    assert "comment" in names
    assert required_scope_names(TOOL_TABLE[ToolId.RUN_PROMPT_EVAL]) == ()


def test_entries_validate_their_own_fields() -> None:
    with pytest.raises(ValueError, match="handler_due_step"):
        ToolEntry(
            tool_id=ToolId.RANK_TRIAGE_QUEUE,
            consequence=Consequence.OBSERVE,
            required_scopes=frozenset(),
            handler_due_step=0,
            handler=None,
            summary="x",
        )
    with pytest.raises(ValueError, match="states what the tool does"):
        ToolEntry(
            tool_id=ToolId.RANK_TRIAGE_QUEUE,
            consequence=Consequence.OBSERVE,
            required_scopes=frozenset(),
            handler_due_step=3,
            handler=None,
            summary="  ",
        )
