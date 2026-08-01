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

from collections import Counter

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

    It has shrunk four times, and this is the last one. At STEP-03 D3 the table
    declared four tools and executed none, because dispatch is a mechanism and
    the first real tool was still ahead. STEP-03 D5 landed the triage ranker,
    taking it to three. STEP-04 D2 landed the pivot handler, taking it to two.
    STEP-05 D5 landed the citation handler, taking it to one. STEP-06 D3 landed
    the prompt-eval handler, taking it to **none**.

    So this now asserts the empty tuple, and that is the discharge of the claim
    ``orchestrator.tools`` has carried since STEP-03: "by STEP-06 the table is
    fully executable or the build is broken". Every declared tool now runs.

    The assertion keeps working in the other direction, which is the one that
    matters from here: a tool added without a handler, or a handler quietly
    dropped, makes this non-empty again.
    """
    assert pending_handlers(TOOL_TABLE) == ()


def test_nothing_due_this_phase_or_earlier_is_still_pending() -> None:
    """The countdown, at full strength now that this phase has met its own
    deadline.

    Saif's condition on the no-orphan reading: the handler-less set must shrink
    phase by phase rather than being a permanent exemption. This is the test
    that enforces it. Bumping ``IMPLEMENTATION_PHASE`` to 4 without landing the
    pivot handler reddens the suite, 5 without the citation handler does the
    same, and by STEP-06 the table is fully executable or the build is broken.
    """
    overdue = [
        tool_id
        for tool_id in pending_handlers(TOOL_TABLE)
        if TOOL_TABLE[tool_id].handler_due_step <= IMPLEMENTATION_PHASE
    ]

    assert overdue == [], (
        f"phase {IMPLEMENTATION_PHASE} owes handlers for "
        f"{[t.value for t in overdue]}; the countdown has been missed"
    )


LAST_HANDLER_PHASE = 6
"""The final phase that owed a handler. The countdown ends here."""


def test_every_phase_that_owed_a_handler_landed_exactly_one() -> None:
    """The other direction: the countdown above passes vacuously if the entry
    that was due simply vanished from the table.

    Rewritten in STEP-07, and the reason matters more than the change. The
    previous form asserted that *this* phase owed exactly one handler, written
    against ``IMPLEMENTATION_PHASE`` so it would not need editing each phase.
    That held from STEP-03 to STEP-06 and became false by construction the
    moment the clock reached 7: STEP-07 adds no tool, because measurement is not
    something an agent may invoke, so ``due_now`` is empty and the assertion
    fails on a table that is perfectly correct.

    A test that goes red because the project did the right thing is a broken
    test, but deleting it would drop the guarantee it carried. So the guarantee
    is restated over the finished countdown rather than over the current phase:
    each of steps 3 through 6 owes exactly one handler, every declared tool
    executes, and nothing is due after the countdown ended.

    Both failure directions survive, which is the point. A handler quietly
    dropped makes an entry non-executable. An entry deleted breaks the
    one-per-phase histogram. A new tool parked at a due step past the end of the
    countdown, which is the obvious way to smuggle in a declared-but-unhandled
    tool now that the deadline has passed, trips the last assertion.
    """
    owed_by_phase = Counter(entry.handler_due_step for entry in TOOL_TABLE.values())

    assert owed_by_phase == Counter(dict.fromkeys(range(3, LAST_HANDLER_PHASE + 1), 1)), (
        f"each of steps 3..{LAST_HANDLER_PHASE} owes exactly one handler; "
        f"the table says {dict(sorted(owed_by_phase.items()))}"
    )
    assert all(entry.executable for entry in TOOL_TABLE.values()), (
        "every declared tool must execute now that the countdown has been discharged; "
        f"{[t.value for t in pending_handlers(TOOL_TABLE)]} do not"
    )
    assert max(owed_by_phase) <= LAST_HANDLER_PHASE, (
        f"no tool may be due after step {LAST_HANDLER_PHASE}; a later due step would park a "
        "declared-but-unhandled tool beyond the reach of the countdown"
    )


def test_the_countdown_clock_has_passed_the_last_deadline() -> None:
    """Guards the rewrite above.

    ``LAST_HANDLER_PHASE`` is only the right place to end the countdown while
    the build is actually past it. If this file were carried into a branch where
    ``IMPLEMENTATION_PHASE`` had been rolled back, the assertions above would be
    describing a future rather than a finished history.
    """
    assert IMPLEMENTATION_PHASE > LAST_HANDLER_PHASE
    assert pending_handlers(TOOL_TABLE) == ()


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
