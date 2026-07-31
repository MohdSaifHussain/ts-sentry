# SPDX-License-Identifier: MIT
"""What a tool *is*, separately from which tools exist.

Split out of ``orchestrator.tools`` in D5, for a structural reason rather than
a stylistic one. The allowlist table has to name real handlers, and a handler
has to be typed against ``ToolContext``; keeping both in one module makes
``tools`` import the handler module and the handler module import ``tools``.
Putting the vocabulary here breaks that cycle without loosening anything:
``tools`` still owns the table, and this module still owns the contract.

``orchestrator.tools`` re-exports every name here, so callers may continue to
import from either.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import duckdb

from ts_sentry.governance.mandate import Consequence, ToolId
from ts_sentry.governance.scopes import DataScope

__all__ = [
    "IMPLEMENTATION_PHASE",
    "ToolContext",
    "ToolEntry",
    "ToolHandler",
    "ToolResources",
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
class ToolResources:
    """What the *orchestrator* holds and lends to a handler for one call.

    Deliberately separate from ``ToolContext.params``, and the separation is
    the control. Params are what the *agent* asked for and are therefore
    untrusted; resources are what the orchestrator already had. A tool that
    needs a database gets the connection from here, so an agent cannot name
    the file it would like opened.
    """

    connection: duckdb.DuckDBPyConnection | None = None
    seed: int = 42


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Everything a handler is given, and nothing else.

    Handlers receive resolved scopes rather than free rein, and there is no
    path from here to the sealed schema because ``DataScope`` has no member
    for it.
    """

    agent_id: str
    granted_scopes: frozenset[DataScope]
    params: Mapping[str, object]
    resources: ToolResources = ToolResources()

    def require_connection(self) -> duckdb.DuckDBPyConnection:
        """The connection, or a clear failure naming who should have supplied it."""
        if self.resources.connection is None:
            raise ToolViolation(
                "this tool needs a dataset connection, which the orchestrator supplies "
                "through ToolResources; an agent cannot provide one"
            )
        return self.resources.connection


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
