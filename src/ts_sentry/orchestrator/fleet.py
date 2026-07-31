# SPDX-License-Identifier: MIT
"""The mandates a session loads (ARCHITECTURE 3.1, 4).

Mandates are code, not policy prose, and this is where the code lives. They
are declared once, hashed, and recorded in the ledger at session open, so a
session is bound to the exact fleet configuration it ran under.

Only the triage mandate exists. The other three agents arrive in STEP-04 to
STEP-06, and declaring their mandates now would be granting authority to
agents that cannot yet be held to it.

Least privilege, per agent
--------------------------
``allowed_tools`` and ``data_scopes`` name what the agent needs and nothing
more. The triage mandate grants the four scopes its one tool actually reads,
not all six: the scopes it does not read are scopes it cannot leak, and a
mandate granting unused access is a mandate that would let a future change
widen silently.
"""

from collections.abc import Mapping

from ts_sentry.agents.triage.prompts import RankedQueue
from ts_sentry.governance.gates import ArtifactCheck, FailureCode, GateChecks, GateFailure
from ts_sentry.governance.mandate import AgentId, Consequence, Mandate, ToolId
from ts_sentry.orchestrator.tools import TOOL_TABLE

__all__ = ["PHASE_THREE_CHECKS", "TRIAGE_MANDATE", "default_mandates"]

TRIAGE_MANDATE = Mandate(
    agent_id=AgentId.TRIAGE,
    version="1.0.0",
    consequence_ceiling=Consequence.OBSERVE,
    allowed_tools=frozenset({ToolId.RANK_TRIAGE_QUEUE}),
    data_scopes=TOOL_TABLE[ToolId.RANK_TRIAGE_QUEUE].required_scopes,
    output_schema=RankedQueue,
    token_budget=120_000,
    max_steps=4,
)
"""The triage agent's mandate (ARCHITECTURE 4.1, ceiling OBSERVE).

``data_scopes`` is read from the tool table rather than restated, so the grant
and the requirement cannot drift apart: widening what the tool reads without
widening the mandate would refuse at dispatch, and widening the mandate alone
would grant access nothing uses.
"""


def default_mandates() -> Mapping[AgentId, Mandate]:
    """The fleet as it exists in this build."""
    return {AgentId.TRIAGE: TRIAGE_MANDATE}


def _unavailable(kind: str) -> ArtifactCheck:
    """A checker for a gate this phase cannot legitimately run.

    ASSEMBLE and RECOMMEND belong to STEP-04 and STEP-05, and no tool in this
    build declares either, so these are unreachable. They fail closed rather
    than auto-approving, because ``GateChecks`` has no defaults precisely so
    that an unconfigured gate cannot silently accept an artifact - supplying a
    permissive stand-in here would undo that.
    """

    def check(artifact: object, /) -> tuple[GateFailure, ...]:
        return (
            GateFailure(
                code=FailureCode.CHECKER_ERROR,
                detail=(
                    f"the {kind} gate has no checker in this build; it arrives with the "
                    f"agent that needs it. Nothing in STEP-03 declares a {kind} action"
                ),
            ),
        )

    return check


PHASE_THREE_CHECKS = GateChecks(
    assemble=_unavailable("ASSEMBLE"),
    recommend=_unavailable("RECOMMEND"),
)
"""Gate checkers for this build. OBSERVE needs none; the other two fail closed."""
