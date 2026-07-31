# SPDX-License-Identifier: MIT
"""D3: the allowlisted tool table (STEP-03 D3, ARCHITECTURE 5.2).

The orchestrator executes tool calls "via an allowlisted tool table; refuse
and ledger anything else". This module is that table. It is a flat, readable
mapping rather than a registry with dynamic registration, for the same reason
the firewall's pattern set is a flat tuple: an allowlist nobody can read off
in one screen is an allowlist nobody audits.

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
from dataclasses import dataclass
from typing import Protocol

from ts_sentry.governance.mandate import Consequence, ToolId
from ts_sentry.governance.scopes import DataScope

__all__ = [
    "IMPLEMENTATION_PHASE",
    "TOOL_TABLE",
    "ToolContext",
    "ToolEntry",
    "ToolHandler",
    "ToolViolation",
    "pending_handlers",
    "required_scope_names",
    "resolve_tool_by_name",
]

IMPLEMENTATION_PHASE = 3
"""The STEP number this build implements.

Bumped by each phase as its first act. It is the countdown test's clock: an
entry whose ``handler_due_step`` is at or below this number must have a
handler, so raising it is what forces the next phase to land the handler it
promised.
"""


class ToolViolation(Exception):
    """Raised when a requested tool name resolves to nothing.

    Mirrors ``scopes.ScopeViolation``, and for the same reason: an agent hands
    the orchestrator a string, not a validated enum member, so the resolution
    step is a real boundary rather than a formality.
    """


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Everything a handler is given, and nothing else.

    Handlers receive resolved scopes rather than a connection they may query
    freely, and parameters the orchestrator has already accepted. A handler
    cannot widen its own access: what is not in ``granted_scopes`` was not
    granted, and there is no path from here to the sealed schema because
    ``DataScope`` has no member for it.
    """

    agent_id: str
    granted_scopes: frozenset[DataScope]
    params: Mapping[str, object]


class ToolHandler(Protocol):
    """A deterministic executable behind one ``ToolId``.

    Handlers do not call models. The model boundary is the D4 adapter, and it
    is the agent, not the tool, that speaks to it: a tool that could prompt
    would be an agent wearing a tool's allowlist entry.
    """

    def __call__(self, context: ToolContext, /) -> object: ...


@dataclass(frozen=True, slots=True)
class ToolEntry:
    """One row of the allowlist."""

    tool_id: ToolId
    consequence: Consequence
    required_scopes: frozenset[DataScope]
    handler_due_step: int
    handler: ToolHandler | None
    summary: str

    def __post_init__(self) -> None:
        if self.handler_due_step < 1:
            raise ValueError(f"handler_due_step must be a STEP number; got {self.handler_due_step}")
        if not self.summary.strip():
            raise ValueError("every allowlist entry states what the tool does")

    @property
    def executable(self) -> bool:
        return self.handler is not None


TOOL_TABLE: Mapping[ToolId, ToolEntry] = {
    ToolId.RANK_TRIAGE_QUEUE: ToolEntry(
        tool_id=ToolId.RANK_TRIAGE_QUEUE,
        consequence=Consequence.OBSERVE,
        required_scopes=frozenset(
            {
                DataScope.CHANNEL,
                DataScope.VIDEO,
                DataScope.COMMENT,
                DataScope.ENGAGEMENT_EVENT,
                DataScope.ACCOUNT_META,
                DataScope.INFRA_HINT,
            }
        ),
        handler_due_step=3,
        handler=None,  # D5 lands it; see tests/test_tool_table.py
        summary="Rank the flagged-entity queue by decomposed priority score.",
    ),
    ToolId.RUN_PARAMETERIZED_PIVOT: ToolEntry(
        tool_id=ToolId.RUN_PARAMETERIZED_PIVOT,
        consequence=Consequence.ASSEMBLE,
        required_scopes=frozenset(
            {
                DataScope.CHANNEL,
                DataScope.VIDEO,
                DataScope.COMMENT,
                DataScope.ENGAGEMENT_EVENT,
                DataScope.ACCOUNT_META,
                DataScope.INFRA_HINT,
            }
        ),
        handler_due_step=4,
        handler=None,
        summary="Run one analyst-approved parameterized pivot query (ARCHITECTURE 4.2).",
    ),
    ToolId.RESOLVE_POLICY_CITATION: ToolEntry(
        tool_id=ToolId.RESOLVE_POLICY_CITATION,
        consequence=Consequence.RECOMMEND,
        required_scopes=frozenset(),
        handler_due_step=5,
        handler=None,
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
of the no-orphan rule this satisfies and for what it does not claim."""


def pending_handlers(table: Mapping[ToolId, ToolEntry]) -> tuple[ToolId, ...]:
    """Tools declared in ``table`` with no handler in this build, in due order.

    The countdown test's subject. Reading it in due order makes the shrinking
    sequence obvious: whichever tool is first is the one the next phase owes.
    """
    return tuple(
        entry.tool_id
        for entry in sorted(
            (entry for entry in table.values() if not entry.executable),
            key=lambda entry: (entry.handler_due_step, entry.tool_id.value),
        )
    )


def resolve_tool_by_name(name: str) -> ToolId:
    """Resolve an agent-supplied tool *name*, or deny it.

    Allowlist semantics, identical to ``scopes.resolve_scope_by_name``:
    lookup is by enum value, so a name with no member is denied by
    construction rather than by a list of things to reject.
    """
    try:
        return ToolId(name)
    except ValueError as exc:
        raise ToolViolation(f"no ToolId member resolves {name!r}") from exc


def required_scope_names(entry: ToolEntry) -> tuple[str, ...]:
    """The scope names a proposal for ``entry`` has to request, sorted.

    Exposed so an agent's proposal is built from the table rather than from a
    hand-maintained list that drifts out of step with it. The agent still
    hands dispatch *strings*, which is the point: this returns the names it
    should ask for, not a pre-validated set that would skip the boundary.
    """
    return tuple(sorted(scope.value for scope in entry.required_scopes))
