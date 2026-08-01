# SPDX-License-Identifier: MIT
"""D3: the allowlisted tool table (STEP-03 D3, ARCHITECTURE 5.2).

The orchestrator executes tool calls "via an allowlisted tool table; refuse
and ledger anything else". This module is that table. It is a flat, readable
mapping rather than a registry with dynamic registration, for the same reason
the firewall's pattern set is a flat tuple: an allowlist nobody can read off
in one screen is an allowlist nobody audits.

The contract for what a tool *is* lives in ``orchestrator.toolspec`` and is
re-exported here, so callers may import either. The split exists because the
table has to name real handlers and handlers have to be typed against
``ToolContext``, which would otherwise be a cycle.

The no-orphan rule, and the reading taken
-----------------------------------------
STEP-02 recorded the rule at ``ToolId``'s definition site: a member may only
be added in the same commit that lands its table entry, and from STEP-03
onward a test must assert every member has one. Three of the four members
predate the rule and belong to agents that do not exist yet.

Saif's decision, recorded here as the chosen reading: **entry per ID now,
handler per ID by its own phase.** Every ``ToolId`` has an entry declaring
what it is, what it costs in consequence, and which scopes it needs.
``handler`` is populated when the agent that owns the tool ships.

That is a weaker guarantee than "every declared tool executes", and pretending
otherwise would be the dishonest version. Two things keep it from decaying:

* A declared-but-unhandled tool is refused with its own ``RefusalCode``
  (``TOOL_HANDLER_NOT_IN_BUILD``), never with ``TOOL_NOT_ALLOWED``. A build
  limitation must not be countable as a mandate violation.
* ``handler_due_step`` gives every pending handler a deadline, and
  ``tests/test_tool_table.py`` fails when a phase passes its deadline without
  landing one. Bumping ``IMPLEMENTATION_PHASE`` to 4 without a pivot handler
  reddens the suite. The set shrinks phase by phase, and by STEP-06 the table
  is fully executable.

Consequence comes from the table, never from the agent
------------------------------------------------------
``ToolEntry.consequence`` is what dispatch validates against the mandate
ceiling. An agent proposes *which tool to run*, and the table says what
running it costs. If the agent supplied the consequence, an agent could
understate one and walk a RECOMMEND-weight action through an OBSERVE ceiling.
"""

from collections.abc import Mapping

from ts_sentry.governance.mandate import Consequence, ToolId
from ts_sentry.governance.scopes import DataScope
from ts_sentry.orchestrator.citation_tool import resolve_policy_citation
from ts_sentry.orchestrator.pivot_tool import run_parameterized_pivot
from ts_sentry.orchestrator.toolspec import (
    IMPLEMENTATION_PHASE,
    ToolContext,
    ToolEntry,
    ToolHandler,
    ToolResources,
    ToolViolation,
    pending_handlers,
    required_scope_names,
    resolve_tool_by_name,
)
from ts_sentry.orchestrator.triage_tool import rank_triage_queue

__all__ = [
    "IMPLEMENTATION_PHASE",
    "TOOL_TABLE",
    "ToolContext",
    "ToolEntry",
    "ToolHandler",
    "ToolResources",
    "ToolViolation",
    "pending_handlers",
    "required_scope_names",
    "resolve_tool_by_name",
]

_ALL_ENTITY_SCOPES = frozenset(DataScope)

TOOL_TABLE: Mapping[ToolId, ToolEntry] = {
    ToolId.RANK_TRIAGE_QUEUE: ToolEntry(
        tool_id=ToolId.RANK_TRIAGE_QUEUE,
        consequence=Consequence.OBSERVE,
        required_scopes=frozenset(
            {
                DataScope.CHANNEL,
                DataScope.VIDEO,
                DataScope.COMMENT,
                DataScope.INFRA_HINT,
            }
        ),
        handler_due_step=3,
        handler=rank_triage_queue,
        summary="Rank the flagged-entity queue by decomposed priority score.",
    ),
    ToolId.RUN_PARAMETERIZED_PIVOT: ToolEntry(
        tool_id=ToolId.RUN_PARAMETERIZED_PIVOT,
        consequence=Consequence.ASSEMBLE,
        required_scopes=_ALL_ENTITY_SCOPES,
        handler_due_step=4,
        handler=run_parameterized_pivot,
        summary="Run one analyst-approved parameterized pivot query (ARCHITECTURE 4.2).",
    ),
    ToolId.RESOLVE_POLICY_CITATION: ToolEntry(
        tool_id=ToolId.RESOLVE_POLICY_CITATION,
        consequence=Consequence.RECOMMEND,
        required_scopes=frozenset(),
        handler_due_step=5,
        handler=resolve_policy_citation,
        summary="Resolve a policy citation against the hashed corpus (ARCHITECTURE 4.3).",
    ),
    ToolId.RUN_PROMPT_EVAL: ToolEntry(
        tool_id=ToolId.RUN_PROMPT_EVAL,
        consequence=Consequence.OBSERVE,
        required_scopes=frozenset(),
        handler_due_step=6,
        handler=None,
        summary="Evaluate a prompt version against the labeled set (ARCHITECTURE 4.4).",
    ),
}
"""Every ``ToolId``, one entry each. See the module docstring for the reading
of the no-orphan rule this satisfies and for what it does not claim.

``RANK_TRIAGE_QUEUE`` requires only the four scopes its queries actually read.
It was declared with all six until D5 made the queries real, and narrowing it
to what is used is the least-privilege position: a mandate granting the extra
two would be granting access nothing asks for.
"""
