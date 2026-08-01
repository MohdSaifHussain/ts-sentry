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
from ts_sentry.agents.memo.memo import Memo
from ts_sentry.agents.triage.prompts import RankedQueue
from ts_sentry.data.policy_corpus import PolicyCorpus
from ts_sentry.governance.gates import ArtifactCheck, FailureCode, GateChecks, GateFailure
from ts_sentry.governance.mandate import AgentId, Consequence, Mandate, ToolId
from ts_sentry.orchestrator.memo_gate import memo_checker
from ts_sentry.orchestrator.pack_gate import pack_checker
from ts_sentry.orchestrator.prompt_eval import EvalReport
from ts_sentry.orchestrator.tools import TOOL_TABLE

__all__ = [
    "EVIDENCE_MANDATE",
    "MEMO_MANDATE",
    "PROMPT_EVAL_MANDATE",
    "PHASE_FOUR_CHECKS",
    "TRIAGE_MANDATE",
    "default_mandates",
    "phase_five_checks",
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
    token_budget=400_000,
    max_steps=20,
)
"""The evidence agent's mandate (ARCHITECTURE 4.2, ceiling ASSEMBLE).

``max_steps`` is the pivot budget, and that is the whole of STEP-04 3.3's
"max proposals per turn bounded by mandate". One hop is one turn is one step,
so a rejected proposal costs a step exactly as an approved one does. That is
the honest accounting: the analyst's attention was spent either way, and a
bound that only counted successes would let an agent propose indefinitely as
long as everything it proposed was refused.

It is 20 because STEP-04 3.5 reports recovery at 5, 10 and 20 pivots. A ceiling
of 12 would have made the 20-pivot column unreachable by construction, so the
largest number in the published table could never have differed from the
12-pivot result and nobody reading it would have known. A reported budget the
mandate forbids is not a measurement.

``data_scopes`` is read from the tool table rather than restated, so the grant
and the requirement cannot drift apart. The pivot tool declares all six entity
scopes because the agent may run any of the five pivots and between them they
read every entity table; least privilege is expressed per template instead, and
the handler refuses a pivot whose scopes this dispatch did not grant.
"""


MEMO_MANDATE = Mandate(
    agent_id=AgentId.MEMO,
    version="1.0.0",
    consequence_ceiling=Consequence.RECOMMEND,
    allowed_tools=frozenset({ToolId.RESOLVE_POLICY_CITATION}),
    data_scopes=TOOL_TABLE[ToolId.RESOLVE_POLICY_CITATION].required_scopes,
    output_schema=Memo,
    token_budget=200_000,
    max_steps=8,
)
"""The memo agent's mandate (ARCHITECTURE 4.3, ceiling RECOMMEND).

``data_scopes`` is empty, read from the tool table like the other two rather
than restated. That is not an oversight: the memo agent reaches **no platform
table at all**. It works from an accepted Evidence Pack and the hashed corpus,
both lent to it by the orchestrator, so there is nothing for it to query and
nothing it could scope-creep into. It is the narrowest mandate in the fleet, and
the one where least privilege costs nothing.

RECOMMEND is the ceiling, and it is the highest any mandate may declare.
``AgentConsequence`` makes ENFORCE unspellable here at type level; this mandate
is the one that sits closest to it, which is exactly why the memo it produces
stays a draft until a human signature it cannot reach finalizes it.

``max_steps`` is 8 because STEP-05 3.2's revise loop spends a step per attempt,
approved or refused, on the accounting STEP-04 established for pivots: the
analyst's attention was spent either way. Eight is enough for a memo of a few
sentences plus revisions and small enough that a model looping on one
unresolvable citation stops.
"""


PROMPT_EVAL_MANDATE = Mandate(
    agent_id=AgentId.PROMPT_EVAL,
    version="1.0.0",
    consequence_ceiling=Consequence.OBSERVE,
    allowed_tools=frozenset({ToolId.RUN_PROMPT_EVAL}),
    data_scopes=TOOL_TABLE[ToolId.RUN_PROMPT_EVAL].required_scopes,
    output_schema=EvalReport,
    token_budget=600_000,
    max_steps=2,
)
"""The prompt-eval agent's mandate (ARCHITECTURE 4.4, ceiling OBSERVE).

``data_scopes`` is empty, read from the tool table like the other three. The
prompt-eval agent reaches no platform table: it works from a committed eval set
and a prompt registry, both lent to it by the orchestrator. The eval *labels*
are not in its reach either, and that is not expressed here at all, because a
mandate scope could only ever have denied it a table. It is denied by the
import graph, which is the stronger statement: there is no code path from any
agent module to the answers.

OBSERVE is the ceiling, and it is the right one: evaluating a prompt reads and
reports, it changes nothing. The pointer move that *does* change something is
not an agent action under any mandate, and no tool in this table performs it.

``token_budget`` is the largest in the fleet because one turn classifies every
eval item twice, once under each version. ``max_steps`` is 2 because that turn
is one step and the accounting STEP-04 established leaves a second for a
retry-shaped failure rather than none.
"""


def default_mandates() -> Mapping[AgentId, Mandate]:
    """The fleet as it exists in this build.

    All four agents, for the first time. Adding one changes
    ``mandate_set_hash`` for **every** session type, so chain heads recorded
    before STEP-06 no longer reproduce, including Saif's STEP-05 phase-close
    head. Same class as the effect STEP-05 recorded when the memo mandate
    landed, and it looks like tampering while being the opposite: the session
    is bound to the exact fleet configuration it ran under, so a fleet that
    gained an agent must hash differently.
    """
    return {
        AgentId.TRIAGE: TRIAGE_MANDATE,
        AgentId.EVIDENCE: EVIDENCE_MANDATE,
        AgentId.MEMO: MEMO_MANDATE,
        AgentId.PROMPT_EVAL: PROMPT_EVAL_MANDATE,
    }


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
"""Gate checkers for a session that drafts no memo.

OBSERVE needs none. ASSEMBLE has had a real checker since STEP-04 D4. RECOMMEND
still fails closed here, and deliberately so: a triage or evidence session
declares no RECOMMEND action, so a permissive stand-in would be a gate that
could accept something nothing in the session was entitled to produce.
"""


def phase_five_checks(pack: EvidencePack, corpus: PolicyCorpus) -> GateChecks:
    """Gate checkers for a session that drafts a memo (STEP-05).

    A function rather than a constant, which is the whole difference between
    this and ``PHASE_FOUR_CHECKS``. The RECOMMEND checker has to be told what a
    claim may resolve against, and that is a property of the evidence pack and
    corpus in scope, established before the gate runs. A module-level constant
    would have to find them for itself, and the only way to do that is to let
    the memo say what it should be checked against.
    """
    return GateChecks(assemble=pack_checker(), recommend=memo_checker(pack, corpus))
