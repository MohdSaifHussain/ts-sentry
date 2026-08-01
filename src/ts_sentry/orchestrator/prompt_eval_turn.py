# SPDX-License-Identifier: MIT
"""D3/D4: the prompt-eval turn, and the ledgered activation refusal.

The ARCHITECTURE 3.3 pipeline for an OBSERVE agent, with a second gate after
the first::

    begin turn (books one step)
      -> firewall each eval item -> model classifies, under each version
      -> dispatch RUN_PROMPT_EVAL -> OBSERVE gate -> EvalReport
      -> regression gate over (report, tolerances)
      -> activatable: the caller may move the pointer
      -> refused: GATE_REJECTION ledgered, carrying the per-class breaches

Two gates, and they answer different questions
-----------------------------------------------
The consequence gate asks whether the artifact the tool produced is acceptable
at its consequence level. The regression gate asks whether the *version* the
report describes may become the incumbent. Running the second inside the first
would have meant inventing a consequence level for "activation", and activation
is not an agent action at all: no agent proposes it, and the pointer move is
made by the CLI on an analyst's instruction.

Refusals are ledgered here, not in the gate
--------------------------------------------
``regression_gate.decide`` is pure (3.5). This module is the caller that writes
the refusal down, through a single helper so the property is checkable by
reading one function, exactly as STEP-03 obligation 4 established for
``dispatch``.

Every item is classified twice
-------------------------------
Once under the incumbent and once under the candidate, over the same items in
the same order. That is what makes the comparison paired, and the ordering is
asserted by the label store rather than assumed here.
"""

from dataclasses import dataclass

import numpy as np

from ts_sentry.agents.prompt_eval.prompts import (
    ClassificationParseError,
    ClassificationProposal,
    classify_instruction,
    parse_classification,
)
from ts_sentry.data.eval_set import EvalItem
from ts_sentry.governance.gates import GateChecks
from ts_sentry.governance.ledger import EventType, LedgerEntry
from ts_sentry.governance.mandate import AgentId, ToolId
from ts_sentry.orchestrator.adapter import (
    ModelAdapter,
    ModelRequest,
    RetryPolicy,
    Sleeper,
    StubMode,
    call_model,
)
from ts_sentry.orchestrator.core import CloseReason, Session
from ts_sentry.orchestrator.dispatch import ToolProposal, dispatch
from ts_sentry.orchestrator.eval_labels import EvalLabelStore
from ts_sentry.orchestrator.eval_tool import (
    CANDIDATE_DIGEST_PARAM,
    INCUMBENT_DIGEST_PARAM,
    TASK_PARAM,
)
from ts_sentry.orchestrator.firewall import (
    CaseRecord,
    SystemPrompt,
    apply_firewall,
    compose_user_content,
)
from ts_sentry.orchestrator.prompt_eval import EvalReport
from ts_sentry.orchestrator.regression_gate import GateVerdict, Tolerances, decide
from ts_sentry.orchestrator.tools import TOOL_TABLE, ToolResources, required_scope_names

__all__ = ["PromptEvalTurn", "classify_items", "run_prompt_eval_turn", "stub_classify_responder"]

_MAX_OUTPUT_TOKENS = 64
"""One line of output. A classification is ``CLASS: <name>`` and nothing else,
so a generous ceiling would only buy room for the reasoning the prompt forbids."""


@dataclass(frozen=True, slots=True)
class PromptEvalTurn:
    """Everything one evaluation produced, including how it fell short."""

    report: EvalReport | None
    verdict: GateVerdict | None
    close_reason: CloseReason | None
    detail: str
    ledgered: tuple[LedgerEntry, ...] = ()
    injection_signals: int = 0

    @property
    def activatable(self) -> bool:
        return self.verdict is not None and self.verdict.activatable

    def to_json_object(self) -> dict[str, object]:
        return {
            "close_reason": None if self.close_reason is None else self.close_reason.value,
            "detail": self.detail,
            "injection_signals": self.injection_signals,
            "report": None if self.report is None else self.report.to_json_object(),
            "verdict": None if self.verdict is None else self.verdict.to_json_object(),
        }


_COLLAPSE_MARKERS = ("when in doubt", "prefer benign")
_CONTRACT_MARKERS = ("explain your reasoning",)


def stub_classify_responder(request: ModelRequest, mode: StubMode) -> str:
    """What the offline stub says when standing in for the classifier.

    Lives here rather than in the adapter for the reason the other three stub
    responders do: the adapter must not know what a classification looks like.

    It reads the case content back out of the prompt and applies a few keyword
    rules. That is deliberately a weak classifier and it is not trying to be a
    good one: what this phase measures is whether a *change between two
    versions* is detected, and a stub that scored perfectly would leave nothing
    for a degraded version to be worse than.

    It responds to the system prompt, not to ``mode``, and that is the
    load-bearing part
    ------------------------------------------------------------------
    The first version of this responder collapsed to ``benign`` under
    ``StubMode.OVERCLAIM``, mirroring the memo stub. That was wrong here, and
    the end-to-end turn test is what found it: a mode is a property of the
    *adapter*, so it applies to every call in the run, including the incumbent's.
    Both versions collapsed identically, every delta came out at zero, and the
    degraded candidate was reported **activatable**.

    The deeper problem is the one worth recording. A stub whose behaviour is
    keyed on a flag rather than on the prompt is a stub that answers the same
    way whatever prompt it is given, and an eval harness driven by such a stub
    measures nothing about prompts at all. It would have produced a full report,
    a confidence interval and a verdict, all of them describing the flag.

    So the degradation lives in the prompt text, where a real model's would.
    ``mode`` is accepted to satisfy the ``Responder`` protocol and deliberately
    ignored.
    """
    del mode  # see the docstring: behaviour comes from the prompt, never a flag

    instructions = request.system.text.lower()
    if any(marker in instructions for marker in _CONTRACT_MARKERS):
        return "Let me think about this case carefully before answering."
    if any(marker in instructions for marker in _COLLAPSE_MARKERS):
        return "CLASS: benign"

    content = request.user_content.lower()
    rules = (
        ("check out my channel", "t01_comment_spam_ring"),
        ("growth network", "t02_fake_engagement_network"),
        ("link in", "t03_off_platform_diversion"),
        ("synthetic media disclosed: no", "t04_undisclosed_synthetic_media"),
        ("expert", "t05_ai_persona_authority"),
        ("life hacks", "t06_slop_farm"),
        ("shared with", "t07_coordinated_influence_op"),
    )
    for needle, answer in rules:
        if needle in content:
            return f"CLASS: {answer}"
    return "CLASS: benign"


def _item_records(item: EvalItem) -> tuple[CaseRecord, ...]:
    """One eval item, as a record for the input firewall.

    Applied for the reason every other turn applies it: this content originates
    on the platform, and there is exactly one route by which platform-derived
    data reaches a model. An eval item is the most tempting place to make an
    exception, because it is "just test data", and it is the worst place to make
    one: an item carrying an injection would be measured as a classification
    failure rather than seen as an attack.
    """
    return (CaseRecord(record_id=item.item_id, source="eval.item", text=item.content),)


def classify_items(
    session: Session,
    adapter: ModelAdapter,
    prompt: SystemPrompt,
    items: tuple[EvalItem, ...],
    *,
    policy: RetryPolicy,
    rng: np.random.Generator,
    sleeper: Sleeper,
) -> tuple[tuple[ClassificationProposal, ...], int, int, tuple[LedgerEntry, ...]]:
    """Classify every item under one prompt version.

    Returns the parsed proposals, the count that could not be parsed, the number
    of firewall signals seen, and what was ledgered. An unparseable answer is
    counted rather than retried: the point is to measure what this prompt
    version produces, and quietly re-asking until it produces the right shape
    would measure the retry loop.
    """
    proposals: list[ClassificationProposal] = []
    unparseable = 0
    signals = 0
    ledgered: list[LedgerEntry] = []

    for item in items:
        firewalled = apply_firewall(_item_records(item))
        signals += len(firewalled.signals)
        call = call_model(
            session,
            AgentId.PROMPT_EVAL,
            adapter,
            ModelRequest(
                system=prompt,
                user_content=compose_user_content(classify_instruction(), firewalled),
                max_output_tokens=_MAX_OUTPUT_TOKENS,
            ),
            policy=policy,
            rng=rng,
            sleeper=sleeper,
            firewall_payload=firewalled.to_ledger_payload(),
        )
        ledgered.extend(call.ledgered)

        # Every item yields exactly one proposal, answered or not. A missing
        # entry would shorten this version's list and misalign the paired
        # comparison; `predicted=None` keeps the position and scores as a miss.
        if call.response is None:
            proposals.append(ClassificationProposal(item_id=item.item_id, predicted=None))
            unparseable += 1
            continue
        try:
            proposals.append(parse_classification(item.item_id, call.response.text))
        except ClassificationParseError:
            proposals.append(ClassificationProposal(item_id=item.item_id, predicted=None))
            unparseable += 1

    return tuple(proposals), unparseable, signals, tuple(ledgered)


def run_prompt_eval_turn(
    session: Session,
    adapter: ModelAdapter,
    *,
    items: tuple[EvalItem, ...],
    store: EvalLabelStore,
    incumbent: SystemPrompt,
    candidate: SystemPrompt,
    incumbent_digest: str,
    candidate_digest: str,
    task: str,
    items_sha256: str,
    tolerances: Tolerances,
    checks: GateChecks,
    policy: RetryPolicy,
    rng: np.random.Generator,
    sleeper: Sleeper,
    bootstrap_seed: int = 42,
) -> PromptEvalTurn:
    """Evaluate a candidate against the incumbent and decide activation."""
    agent_id = AgentId.PROMPT_EVAL
    entry = TOOL_TABLE[ToolId.RUN_PROMPT_EVAL]

    start = session.begin_turn(agent_id)
    if not start.started:
        return PromptEvalTurn(
            report=None, verdict=None, close_reason=start.close_reason, detail=start.detail
        )

    ledgered: list[LedgerEntry] = []
    signals = 0

    incumbent_answers, incumbent_bad, incumbent_signals, incumbent_log = classify_items(
        session, adapter, incumbent, items, policy=policy, rng=rng, sleeper=sleeper
    )
    candidate_answers, candidate_bad, candidate_signals, candidate_log = classify_items(
        session, adapter, candidate, items, policy=policy, rng=rng, sleeper=sleeper
    )
    ledgered.extend((*incumbent_log, *candidate_log))
    signals += incumbent_signals + candidate_signals

    dispatched = dispatch(
        session,
        ToolProposal(
            agent_id=agent_id,
            tool_name=ToolId.RUN_PROMPT_EVAL.value,
            requested_scope_names=required_scope_names(entry),
            params={
                TASK_PARAM: task,
                INCUMBENT_DIGEST_PARAM: incumbent_digest,
                CANDIDATE_DIGEST_PARAM: candidate_digest,
            },
        ),
        table=TOOL_TABLE,
        checks=checks,
        resources=ToolResources(
            seed=bootstrap_seed,
            eval_labels=store,
            eval_predictions=(incumbent_answers, candidate_answers),
            eval_unparseable=(incumbent_bad, candidate_bad),
            eval_items_sha256=items_sha256,
            eval_adapter_id=adapter.adapter_id,
            eval_model_id=adapter.model_id,
        ),
    )
    ledgered.extend(dispatched.ledgered)
    session.end_turn()

    report = dispatched.result if isinstance(dispatched.result, EvalReport) else None
    if report is None:
        return PromptEvalTurn(
            report=None,
            verdict=None,
            close_reason=None,
            detail=f"the eval did not produce a report: {dispatched.detail}",
            ledgered=tuple(ledgered),
            injection_signals=signals,
        )

    verdict = decide(report, tolerances)
    ledgered.extend(_ledger_verdict(session, verdict, report))

    return PromptEvalTurn(
        report=report,
        verdict=verdict,
        close_reason=None,
        detail=(
            f"{task}: candidate {candidate_digest[:12]} is "
            + ("activatable" if verdict.activatable else "refused")
            + f" against incumbent {incumbent_digest[:12]}"
        ),
        ledgered=tuple(ledgered),
        injection_signals=signals,
    )


def _ledger_verdict(
    session: Session, verdict: GateVerdict, report: EvalReport
) -> tuple[LedgerEntry, ...]:
    """Write the activation decision down. The single helper STEP-03 asked for.

    A refusal is a ``GATE_REJECTION`` carrying every breach, because STEP-06 3.3
    requires the refusal to be ledgered and ARCHITECTURE 3.2 makes those counts
    a showcased metric rather than an embarrassment. An acceptance is a
    ``VERIFICATION_PASS``: something was checked and passed.

    The payload carries the breaches in full rather than a count. A
    ``GATE_REJECTION`` whose body cannot show what was breached is an entry that
    cannot evidence the rejection it reports, which is the argument STEP-03
    recorded when ``GateOutcome`` gained its payload field.
    """
    payload: dict[str, object] = {
        "task": verdict.task,
        "decision": verdict.decision.value,
        "incumbent_digest": report.incumbent.content_digest,
        "candidate_digest": report.candidate.content_digest,
        "items_sha256": report.items_sha256,
        "labels_sha256": report.labels_sha256,
        "tolerances_sha256": verdict.tolerances_sha256,
        "bootstrap_seed": report.bootstrap_seed,
        "breaches": [breach.to_json_object() for breach in verdict.breaches],
    }
    event = EventType.VERIFICATION_PASS if verdict.activatable else EventType.GATE_REJECTION
    return (session.append_event(event, agent_id=AgentId.PROMPT_EVAL, payload=payload).entry,)
