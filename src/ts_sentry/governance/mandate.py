# SPDX-License-Identifier: MIT
"""D1: the Mandate model - a frozen, declarative specification of everything
an agent is permitted to do (ARCHITECTURE 3.1).

A Mandate is code, not policy prose. The orchestrator (STEP-03) validates
every proposed agent action against one *before* dispatch, so an action
outside the mandate is never executed rather than executed-and-blocked.

``DataScope`` is imported from :mod:`ts_sentry.governance.scopes`, where
STEP-01 already landed it together with its two exhaustive resolvers. It is
not redefined, relocated, or re-exported under a new name here.

Two structures in this module are load-bearing rather than stylistic:

* ``AgentConsequence`` (the PEP 695 alias below) is the type-level half of
  the ENFORCE human-only invariant. See :mod:`ts_sentry.governance.signature`
  for the runtime half and for the precise, deliberately narrow statement of
  what that invariant does and does not guarantee.
* ``_consequence_rank`` is an exhaustive ``match`` over ``Consequence``
  closed by ``assert_never``, mirroring ``scopes.resolve_table``. STEP-01's
  leakage red-team showed why: an unhandled new member does not silently
  fall through, it makes the function itself incomplete and breaks every
  call site loudly.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, assert_never

from ts_sentry.governance.scopes import DataScope

__all__ = [
    "AgentConsequence",
    "AgentId",
    "Consequence",
    "EnforceUnreachable",
    "Mandate",
    "ProposedAction",
    "RefusalCode",
    "ToolId",
    "Verdict",
    "VerdictKind",
    "mandate_hash",
    "validate",
]


class AgentId(StrEnum):
    """The four narrow agents of the fleet (ARCHITECTURE 4).

    The human analyst is deliberately absent: the analyst is the supervisor,
    not a fleet member, and holds the only ENFORCE authority.
    """

    TRIAGE = "triage"
    EVIDENCE = "evidence"
    MEMO = "memo"
    PROMPT_EVAL = "prompt_eval"


class ToolId(StrEnum):
    """Allowlisted tool identifiers referenced by ``Mandate.allowed_tools``.

    One member per agent, each traceable to its ARCHITECTURE section
    (4.1 triage, 4.2 evidence, 4.3 memo, 4.4 prompt-eval). Nothing dispatches
    these in Phase 2; the allowlisted tool table that binds a ``ToolId`` to an
    executable is STEP-03 D3.

    **No orphan tool IDs.** A member may only be added in the same commit that
    lands its corresponding entry in the STEP-03 allowlisted tool table, and
    from STEP-03 onward a test asserts every ``ToolId`` member has a table
    entry. The rule is recorded here, at the definition site, because that is
    where it binds whoever next reaches for this enum.
    """

    RANK_TRIAGE_QUEUE = "rank_triage_queue"
    RUN_PARAMETERIZED_PIVOT = "run_parameterized_pivot"
    RESOLVE_POLICY_CITATION = "resolve_policy_citation"
    RUN_PROMPT_EVAL = "run_prompt_eval"


class Consequence(StrEnum):
    """Consequence classification of an action (ARCHITECTURE 3.3).

    Actions are classified by *consequence*, never by content.

    All four members exist, ENFORCE included. It has to be nameable: a
    proposed action must be able to *carry* ENFORCE so that ``validate`` can
    refuse it explicitly, and so the gate pipeline (D4) can dispatch over the
    enum exhaustively. What is prevented is a Mandate carrying it, and any
    agent action reaching the ENFORCE gate. Absence from the enum would make
    the refusal path unwritable, not stronger.
    """

    OBSERVE = "observe"
    ASSEMBLE = "assemble"
    RECOMMEND = "recommend"
    ENFORCE = "enforce"


type AgentConsequence = Literal[
    Consequence.OBSERVE,
    Consequence.ASSEMBLE,
    Consequence.RECOMMEND,
]
"""The consequence levels an agent mandate may declare as its ceiling.

ENFORCE is excluded by construction. Annotating ``Mandate.consequence_ceiling``
with this alias makes ``Mandate(consequence_ceiling=Consequence.ENFORCE, ...)``
a mypy ``arg-type`` error, checked on every CI run and pinned by the negative
fixture in ``tests/typing/enforce_negative.py``.
"""


class EnforceUnreachable(Exception):
    """Raised when ENFORCE is reached through a path reserved for the human.

    Distinct from ``ValueError`` on purpose: this is a governance-invariant
    breach, not an ordinary bad argument, and callers that catch it are
    catching exactly one thing.
    """


# Official SemVer 2.0.0 grammar (semver.org, "Is there a suggested regular
# expression to check a SemVer string?"), split across lines for line length.
_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)


def _reject_enforce(ceiling: Consequence) -> None:
    """Runtime half of the ENFORCE ceiling exclusion.

    Takes the full ``Consequence`` rather than ``AgentConsequence`` for a
    reason: against the narrow alias mypy would (correctly) report the
    identity check as non-overlapping and refuse to compile it. Widening here
    is what lets the guard exist at runtime for callers that arrive without a
    type checker in front of them, which is the whole point of having it.
    """
    if ceiling is Consequence.ENFORCE:
        raise EnforceUnreachable(
            "Consequence.ENFORCE is human-only: no Mandate may declare it as a "
            "consequence ceiling. See ts_sentry.governance.signature."
        )


@dataclass(frozen=True, slots=True)
class Mandate:
    """Everything one agent is permitted to do, and nothing else.

    Field set per ARCHITECTURE 3.1, plus ``version``: 3.1's prose says
    mandates are "versioned, hashed, and recorded in the ledger" while its
    dataclass sketch carries no version field. The explicit field is the
    faithful reading, and it sits *inside* the canonical hash form, so a
    version bump is a hash change rather than a label beside one.

    ``output_schema`` is spelled ``type[object]`` rather than 3.1's bare
    ``type`` only because ``mypy --strict`` rejects unparameterized generics;
    it accepts the same set of values.

    Every constraint below is enforced in ``__post_init__``, not described in
    this docstring. A docstring-only invariant is not an invariant.
    """

    agent_id: AgentId
    version: str
    consequence_ceiling: AgentConsequence
    allowed_tools: frozenset[ToolId]
    data_scopes: frozenset[DataScope]
    output_schema: type[object]
    token_budget: int
    max_steps: int

    def __post_init__(self) -> None:
        if _SEMVER_PATTERN.match(self.version) is None:
            raise ValueError(
                f"version must be a SemVer 2.0.0 string (e.g. '1.0.0'); got {self.version!r}"
            )
        _reject_enforce(self.consequence_ceiling)
        if self.token_budget <= 0:
            raise ValueError(f"token_budget must be positive; got {self.token_budget}")
        if self.max_steps <= 0:
            raise ValueError(f"max_steps must be positive; got {self.max_steps}")


def _canonical_form(mandate: Mandate) -> dict[str, object]:
    """Serialization-stable view of a mandate, for hashing.

    Frozensets are emitted as value-sorted lists and ``output_schema`` as
    ``module:qualname``, so the digest depends only on declared content: no
    ``hash()``, no ``id()``, no set iteration order, nothing that varies
    between processes or runs.
    """
    return {
        "agent_id": mandate.agent_id.value,
        "version": mandate.version,
        "consequence_ceiling": mandate.consequence_ceiling.value,
        "allowed_tools": sorted(tool.value for tool in mandate.allowed_tools),
        "data_scopes": sorted(scope.value for scope in mandate.data_scopes),
        "output_schema": f"{mandate.output_schema.__module__}:{mandate.output_schema.__qualname__}",
        "token_budget": mandate.token_budget,
        "max_steps": mandate.max_steps,
    }


def mandate_hash(mandate: Mandate) -> str:
    """SHA-256 of the mandate's canonical form (ARCHITECTURE 3.1).

    Recorded in the ledger at session start, so a mandate change is itself an
    audited event. Stable across processes and Python invocations.
    """
    payload = json.dumps(_canonical_form(mandate), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ProposedAction:
    """One action an agent proposes to take, before any dispatch happens.

    ``consequence`` is the full ``Consequence``, not ``AgentConsequence``: an
    agent must be able to *propose* ENFORCE, because a governance layer that
    cannot represent the forbidden request cannot refuse it, ledger it, or
    show itself refusing it.
    """

    agent_id: AgentId
    tool_id: ToolId
    consequence: Consequence
    requested_scopes: frozenset[DataScope]


class VerdictKind(StrEnum):
    """Outcome of mandate validation."""

    ALLOW = "allow"
    REFUSE = "refuse"


class RefusalCode(StrEnum):
    """Why a proposed action was refused.

    Every refusal carries exactly one of these, so ledger entries and gate
    rejections are countable by cause rather than by free text.
    """

    AGENT_MISMATCH = "agent_mismatch"
    ENFORCE_IS_HUMAN_ONLY = "enforce_is_human_only"
    TOOL_NOT_ALLOWED = "tool_not_allowed"
    SCOPE_NOT_ALLOWED = "scope_not_allowed"
    CONSEQUENCE_EXCEEDS_CEILING = "consequence_exceeds_ceiling"


@dataclass(frozen=True, slots=True)
class Verdict:
    """Result of ``validate``: allow, or refuse with exactly one cause."""

    kind: VerdictKind
    code: RefusalCode | None
    detail: str

    def __post_init__(self) -> None:
        refuses = self.kind is VerdictKind.REFUSE
        if refuses != (self.code is not None):
            raise ValueError("a REFUSE verdict carries exactly one RefusalCode; ALLOW carries none")

    @property
    def allowed(self) -> bool:
        return self.kind is VerdictKind.ALLOW


def _refuse(code: RefusalCode, detail: str) -> Verdict:
    return Verdict(kind=VerdictKind.REFUSE, code=code, detail=detail)


def _consequence_rank(consequence: Consequence) -> int:
    """Total order over consequence levels, for ceiling comparison.

    Exhaustive ``match`` closed by ``assert_never``: mypy proves every member
    is handled, so adding a level to ``Consequence`` without ranking it is a
    type error at this function rather than a silent misordering at every
    call site (STEP-02 3.1).
    """
    match consequence:
        case Consequence.OBSERVE:
            return 0
        case Consequence.ASSEMBLE:
            return 1
        case Consequence.RECOMMEND:
            return 2
        case Consequence.ENFORCE:
            return 3
        case _:  # pragma: no cover - exhaustiveness guard, unreachable per mypy
            assert_never(consequence)


def validate(action: ProposedAction, mandate: Mandate) -> Verdict:
    """Decide whether ``action`` is within ``mandate`` (STEP-02 3.1).

    Pure and total: no I/O, no ledger write, no exception on any input. The
    orchestrator ledgers the refusal (STEP-03); keeping that out of here is
    what makes this function testable in isolation and safe to call from a
    gate.

    Refusal order is deliberate. ENFORCE is checked first, before agent
    identity, tools, scopes, or ceiling, so that the human-only invariant can
    never be shadowed by an incidental refusal for some other reason, and so
    the recorded ``RefusalCode`` names the invariant that actually fired.
    """
    if action.consequence is Consequence.ENFORCE:
        return _refuse(
            RefusalCode.ENFORCE_IS_HUMAN_ONLY,
            f"agent {action.agent_id.value} proposed an ENFORCE action; ENFORCE is human-only "
            "and is refused under every mandate",
        )

    if action.agent_id is not mandate.agent_id:
        return _refuse(
            RefusalCode.AGENT_MISMATCH,
            f"action proposed by {action.agent_id.value} validated against the "
            f"{mandate.agent_id.value} mandate",
        )

    if action.tool_id not in mandate.allowed_tools:
        return _refuse(
            RefusalCode.TOOL_NOT_ALLOWED,
            f"tool {action.tool_id.value} is not in the {mandate.agent_id.value} allowlist",
        )

    out_of_scope = sorted(scope.value for scope in action.requested_scopes - mandate.data_scopes)
    if out_of_scope:
        return _refuse(
            RefusalCode.SCOPE_NOT_ALLOWED,
            f"data scopes not in the {mandate.agent_id.value} allowlist: {', '.join(out_of_scope)}",
        )

    if _consequence_rank(action.consequence) > _consequence_rank(mandate.consequence_ceiling):
        return _refuse(
            RefusalCode.CONSEQUENCE_EXCEEDS_CEILING,
            f"action consequence {action.consequence.value} exceeds the "
            f"{mandate.agent_id.value} ceiling {mandate.consequence_ceiling.value}",
        )

    return Verdict(
        kind=VerdictKind.ALLOW,
        code=None,
        detail=f"within the {mandate.agent_id.value} mandate at version {mandate.version}",
    )
