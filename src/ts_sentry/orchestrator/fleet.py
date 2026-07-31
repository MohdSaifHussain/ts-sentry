# SPDX-License-Identifier: MIT
"""The mandates a session loads (ARCHITECTURE 3.1, 4).

Mandates are code, not policy prose, and this is where the code lives. They
are declared once, hashed, and recorded in the ledger at session open, so a
session is bound to the exact fleet configuration it ran under.

Triage and evidence exist. The memo and prompt-eval mandates arrive in STEP-05
and STEP-06, and declaring them now would be granting authority to agents that
cannot yet be held to it.

Least privilege, per agent
--------------------------
``allowed_tools`` and ``data_scopes`` name what the agent needs and nothing
more. The triage mandate grants the four scopes its one tool actually reads,
not all six: the scopes it does not read are scopes it cannot leak, and a
mandate granting unused access is a mandate that would let a future change
widen silently.
"""

from collections.abc import Mapping

from ts_sentry.agents.evidence.pack import EvidencePack
from ts_sentry.agents.triage.prompts import RankedQueue
from ts_sentry.governance.gates import ArtifactCheck, FailureCode, GateChecks, GateFailure
from ts_sentry.governance.mandate import AgentId, Consequence, Mandate, ToolId
from ts_sentry.orchestrator.pack_gate import pack_checker
from ts_sentry.orchestrator.tools import TOOL_TABLE

__all__ = [
    "EVIDENCE_MANDATE",
    "PHASE_FOUR_CHECKS",
    "TRIAGE_MANDATE",
    "default_mandates",
]

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


EVIDENCE_MANDATE = Mandate(
    agent_id=AgentId.EVIDENCE,
    version="1.0.0",
    consequence_ceiling=Consequence.ASSEMBLE,
    allowed_tools=frozenset({ToolId.RUN_PARAMETERIZED_PIVOT}),
    data_scopes=TOOL_TABLE[ToolId.RUN_PARAMETERIZED_PIVOT].required_scopes,
    output_schema=EvidencePack,
    token_budget=200_000,
    max_steps=12,
)
"""The evidence agent's mandate (ARCHITECTURE 4.2, ceiling ASSEMBLE).

``max_steps`` is the pivot budget, and that is the whole of STEP-04 3.3's
"max proposals per turn bounded by mandate". One hop is one turn is one step,
so a rejected proposal costs a step exactly as an approved one does. That is
the honest accounting: the analyst's attention was spent either way, and a
bound that only counted successes would let an agent propose indefinitely as
long as everything it proposed was refused.

``data_scopes`` is read from the tool table rather than restated, so the grant
and the requirement cannot drift apart. The pivot tool declares all six entity
scopes because the agent may run any of the five pivots and between them they
read every entity table; least privilege is expressed per template instead, and
the handler refuses a pivot whose scopes this dispatch did not grant.
"""


def default_mandates() -> Mapping[AgentId, Mandate]:
    """The fleet as it exists in this build."""
    return {AgentId.TRIAGE: TRIAGE_MANDATE, AgentId.EVIDENCE: EVIDENCE_MANDATE}


def _unavailable(kind: str) -> ArtifactCheck:
    """A checker for a gate this build cannot legitimately run.

    RECOMMEND belongs to STEP-05 and no tool in this build declares it, so it
    is unreachable. It fails closed rather than auto-approving, because
    ``GateChecks`` has no defaults precisely so that an unconfigured gate cannot
    silently accept an artifact - supplying a permissive stand-in here would
    undo that.
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


PHASE_FOUR_CHECKS = GateChecks(
    assemble=pack_checker(),
    recommend=_unavailable("RECOMMEND"),
)
"""Gate checkers for this build.

OBSERVE needs none. ASSEMBLE now has a real checker (STEP-04 D4), which is the
change that makes an evidence hop able to pass a gate at all. RECOMMEND still
fails closed until the memo agent that needs it ships.
"""
