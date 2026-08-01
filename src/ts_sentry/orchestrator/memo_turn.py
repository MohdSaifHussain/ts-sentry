# SPDX-License-Identifier: MIT
"""D4: the memo drafting turn, with the draft-revise loop (STEP-05 3.2).

The ARCHITECTURE 3.3 pipeline for a RECOMMEND agent::

    begin turn (books one step)
      -> firewall the pack summary -> model drafts
      -> check the draft is a memo at all (draft_check)
      -> dispatch RESOLVE_POLICY_CITATION -> RECOMMEND gate over the whole memo
      -> pass: deliver the draft memo to the analyst
      -> fail: flag the sentences, let the agent revise within the step budget
      -> budget exhausted with failures outstanding: the memo stays DRAFT

Why the dispatch is the submission
----------------------------------
The gate is not called directly here. The memo goes through
``dispatch(RESOLVE_POLICY_CITATION)``, which validates the mandate, ledgers
``TOOL_CALLED``, runs the handler, checks the result against the mandate's
``output_schema``, and runs the RECOMMEND gate over what came back. That is the
governed path, and calling ``memo_check`` directly from here would be the
orchestrator grading the memo outside the pipeline that exists to grade it.

It also gives the tool a real job. The handler attaches the citation the agent
named to the sentence that carries it, so the memo that reaches the gate is the
one the tool produced rather than one this module assembled and then asked the
gate to bless.

No analyst decision here, and that is deliberate
------------------------------------------------
The evidence turn asks the analyst before every pivot, because a pivot *acts*:
it runs a query and grows an artifact. Drafting a memo acts on nothing. The
analyst's decision on a memo is the signature (D6), which is the ENFORCE path
and the one thing no agent can reach. Asking for an approval here would spend
human attention on a document that is about to be checked mechanically anyway,
and would put a ``HUMAN_DECISION`` in the ledger for something no human decided.

Partial delivery, as everywhere else in this system
---------------------------------------------------
A turn whose budget runs out, whose model fails, or whose every draft is refused
still delivers what it has and says so. STEP-03 3.3 requires that for budget
exhaustion and the same reasoning covers the rest: an unverified draft is worth
delivering *as an unverified draft*, because an analyst can read the flagged
sentences and fix them, and losing it would lose the work.
"""

from dataclasses import dataclass

import numpy as np

from ts_sentry.agents.evidence.pack import EvidencePack
from ts_sentry.agents.memo.draft import parse_draft
from ts_sentry.agents.memo.memo import AutomatedDecision, AutomatedMeans, Memo
from ts_sentry.agents.memo.prompts import MEMO_SYSTEM_PROMPT, memo_instruction
from ts_sentry.data.policy_corpus import PolicyCorpus
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
from ts_sentry.orchestrator.citation_tool import (
    ANCHOR_PARAM,
    CONTENT_DIGEST_PARAM,
    EXCERPT_PARAM,
    SENTENCE_INDEX_PARAM,
)
from ts_sentry.orchestrator.core import CloseReason, Session
from ts_sentry.orchestrator.dispatch import ToolProposal, dispatch
from ts_sentry.orchestrator.draft_check import check_draft
from ts_sentry.orchestrator.firewall import CaseRecord, apply_firewall, compose_user_content
from ts_sentry.orchestrator.tools import TOOL_TABLE, ToolResources, required_scope_names

__all__ = ["AttemptRecord", "MemoTurn", "run_memo_turn", "stub_memo_responder"]

_MAX_OUTPUT_TOKENS = 2048


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """One drafting attempt, whatever became of it.

    Refused and failed attempts are kept alongside the accepted one. A session
    artifact showing only the draft that passed would describe an agent that got
    it right first time, which is not what happened and not what the memo
    integrity metric (ARCHITECTURE 7.2, "count of claims corrected by the
    symbolic verifier before human review") is counting.

    ``flagged`` carries the exact failure details the gate produced, which is
    what makes ``MemoTurn.distinct_defects`` able to tell one defect rejected
    three times apart from three defects.
    """

    attempt: int
    outcome: str
    detail: str
    flagged: tuple[str, ...] = ()

    def to_json_object(self) -> dict[str, object]:
        return {
            "attempt": self.attempt,
            "outcome": self.outcome,
            "detail": self.detail,
            "flagged": list(self.flagged),
        }


@dataclass(frozen=True, slots=True)
class MemoTurn:
    """Everything one drafting turn produced, including how it fell short."""

    memo: Memo | None
    verified: bool
    attempts: tuple[AttemptRecord, ...]
    close_reason: CloseReason | None
    detail: str
    ledgered: tuple[LedgerEntry, ...] = ()
    injection_signals: int = 0

    @property
    def rejected_attempts(self) -> int:
        """Attempts the verifier refused. Not the same as defects found."""
        return sum(1 for attempt in self.attempts if attempt.outcome != "verified")

    @property
    def distinct_defects(self) -> int:
        """How many *different* things the verifier objected to.

        Separate from ``rejected_attempts`` because they answer different
        questions and conflating them inflates the metric ARCHITECTURE 7.2
        showcases. A first run of this loop produced three rejections of one
        unchanged sentence: the agent had been told what was wrong and re-sent
        it verbatim. Counting that as three corrections would have reported the
        verifier catching three defects when it caught one, three times, and
        reporting a governance layer as busier than it was is the flattering
        direction this project is supposed to refuse.
        """
        return len({flag for attempt in self.attempts for flag in attempt.flagged})

    @property
    def revised(self) -> bool:
        """Whether the agent ever changed its output after being told.

        Reported because a revise loop that never revises is machinery, not a
        capability, and the difference is invisible from the attempt count.
        """
        flagged = [attempt.flagged for attempt in self.attempts if attempt.flagged]
        return len({flags for flags in flagged}) > 1 or (
            bool(flagged) and any(attempt.outcome == "verified" for attempt in self.attempts)
        )

    def to_json_object(self) -> dict[str, object]:
        return {
            "verified": self.verified,
            "close_reason": None if self.close_reason is None else self.close_reason.value,
            "detail": self.detail,
            "attempts": len(self.attempts),
            "rejected_attempts": self.rejected_attempts,
            "distinct_defects_caught": self.distinct_defects,
            "agent_revised_after_feedback": self.revised,
            "injection_signals": self.injection_signals,
            "attempt_log": [attempt.to_json_object() for attempt in self.attempts],
            "memo": None if self.memo is None else self.memo.to_json_object(),
        }


def stub_memo_responder(request: ModelRequest, mode: StubMode) -> str:
    """What the offline stub says when standing in for the memo model.

    Lives here rather than in the adapter for the reason the other two stub
    responders do: the adapter must not know what a memo looks like.

    It reads the pack's citable ids and the corpus anchors back out of the
    prompt rather than hard-coding them, so it tracks whatever it is actually
    given. Under ``OVERCLAIM`` it cites an evidence id no pack carries, which is
    how the unsupported-claim path gets demonstrated on a real session rather
    than only in a unit test.

    The excerpt is the one thing it cannot read out of the prompt, because the
    clause menu deliberately shows headings rather than clause text. So the stub
    quotes the anchor's own heading, which *is* the opening of every labelled
    clause in this corpus by construction (the extractor prefixes an item's text
    with its label). That is a fact about how the corpus was built, and if it
    ever stops holding this stub starts failing verification, which is the
    correct way for it to find out.
    """
    citable: list[str] = []
    anchors: list[tuple[str, str]] = []
    in_anchor_block = False

    for raw in request.user_content.splitlines():
        line = raw.strip()
        if line.startswith("Policy anchors you may cite"):
            in_anchor_block = True
            continue
        if line.startswith("Measures you may propose"):
            in_anchor_block = False
            continue
        if line.startswith("[") and line.endswith("]"):
            citable.append(line[1:-1].split(" ", 1)[0])
        elif in_anchor_block and ": " in line and not line.startswith("youtube-"):
            anchor, _, heading = line.partition(": ")
            if anchor and heading:
                anchors.append((anchor.strip(), heading.strip()))

    evidence = (
        "prov-9999" if mode is StubMode.OVERCLAIM else (citable[0] if citable else "prov-0000")
    )
    anchor, heading = next(
        ((a, h) for a, h in anchors if a == "comment-spam"),
        anchors[0] if anchors else ("comment-spam", "Comment spam"),
    )

    return "\n".join(
        (
            f"FACT: The subject entered this investigation as its seed [{evidence}].",
            f"GROUND: anchor={anchor} | excerpt={heading}: Using high-volume, | "
            "This conduct is incompatible with the platform's spam policy.",
            "MEASURE: content_demoted",
            "REDRESS: The channel owner may appeal through the internal "
            "complaint-handling system, and may seek out-of-court dispute settlement.",
        )
    )


def _pack_records(pack: EvidencePack) -> tuple[CaseRecord, ...]:
    """The pack, as records for the input firewall.

    Applied for the reason the evidence turn applies it: entity ids and signal
    values originate on the platform, and this system has exactly one route by
    which platform-derived data reaches a model. Making an exception for data
    that "cannot" contain an injection is how the exception becomes the rule.
    """
    return tuple(
        CaseRecord(
            record_id=node.node_id,
            source=f"pack.node.{node.kind.value}",
            text="; ".join(f"{name}={node.attributes[name]}" for name in sorted(node.attributes)),
        )
        for node in pack.nodes
    )


def run_memo_turn(
    session: Session,
    adapter: ModelAdapter,
    *,
    pack: EvidencePack,
    corpus: PolicyCorpus,
    checks: GateChecks,
    policy: RetryPolicy,
    rng: np.random.Generator,
    sleeper: Sleeper,
    memo_id: str = "memo-0001",
    max_attempts: int | None = None,
) -> MemoTurn:
    """Draft one memo, revising until it verifies or the budget stops it."""
    agent_id = AgentId.MEMO
    entry = TOOL_TABLE[ToolId.RESOLVE_POLICY_CITATION]
    binding = session.binding(agent_id)
    ceiling = binding.mandate.max_steps if max_attempts is None else max_attempts

    means = AutomatedMeans(
        detection_automated=True,
        decision=AutomatedDecision.PARTIALLY_AUTOMATED,
        drafted_by=f"{adapter.adapter_id}:{adapter.model_id}",
    )

    attempts: list[AttemptRecord] = []
    ledgered: list[LedgerEntry] = []
    flagged: list[str] = []
    memo: Memo | None = None
    close_reason: CloseReason | None = None
    signals = 0

    for attempt in range(1, ceiling + 1):
        start = session.begin_turn(agent_id)
        if not start.started:
            close_reason = start.close_reason
            attempts.append(
                AttemptRecord(attempt=attempt, outcome="budget_exhausted", detail=start.detail)
            )
            break

        firewalled = apply_firewall(_pack_records(pack))
        signals += len(firewalled.signals)
        call = call_model(
            session,
            agent_id,
            adapter,
            ModelRequest(
                system=MEMO_SYSTEM_PROMPT,
                user_content=compose_user_content(
                    memo_instruction(pack, corpus, flagged), firewalled
                ),
                max_output_tokens=_MAX_OUTPUT_TOKENS,
            ),
            policy=policy,
            rng=rng,
            sleeper=sleeper,
            firewall_payload=firewalled.to_ledger_payload(),
        )
        ledgered.extend(call.ledgered)

        if call.response is None:
            session.end_turn()
            close_reason = call.close_reason
            attempts.append(
                AttemptRecord(attempt=attempt, outcome="model_unavailable", detail=call.detail)
            )
            break

        verdict = check_draft(
            parse_draft(call.response.text),
            pack,
            corpus,
            memo_id=memo_id,
            automated_means=means,
        )
        ledgered.append(
            session.append_event(
                EventType.OUTPUT_PROPOSED,
                agent_id=agent_id,
                payload=verdict.to_ledger_payload(),
            ).entry
        )

        if not verdict.accepted:
            session.end_turn()
            flagged = [verdict.detail]
            attempts.append(
                AttemptRecord(
                    attempt=attempt,
                    outcome="refused",
                    detail=verdict.detail,
                    flagged=tuple(flagged),
                )
            )
            continue

        assert verdict.memo is not None  # an accepted verdict carries one
        draft = verdict.memo
        ground = draft.policy_grounds[0]
        assert ground.citation is not None  # a POLICY_GROUND always carries one

        dispatched = dispatch(
            session,
            ToolProposal(
                agent_id=agent_id,
                tool_name=ToolId.RESOLVE_POLICY_CITATION.value,
                requested_scope_names=required_scope_names(entry),
                params={
                    SENTENCE_INDEX_PARAM: ground.index,
                    CONTENT_DIGEST_PARAM: ground.citation.content_digest,
                    ANCHOR_PARAM: ground.citation.anchor_id,
                    EXCERPT_PARAM: ground.citation.excerpt,
                },
            ),
            table=TOOL_TABLE,
            checks=checks,
            resources=ToolResources(memo=draft, corpus=corpus),
        )
        ledgered.extend(dispatched.ledgered)
        session.end_turn()

        if dispatched.executed and isinstance(dispatched.result, Memo):
            memo = dispatched.result
            attempts.append(
                AttemptRecord(
                    attempt=attempt,
                    outcome="verified",
                    detail=f"{len(memo.sentences)} sentences passed the RECOMMEND gate",
                )
            )
            break

        # A gate rejection is the revise loop's input. Every failure detail names
        # a sentence index, which is what makes the next prompt actionable rather
        # than a request to try again.
        failures = tuple(
            failure.detail for failure in (dispatched.gate.failures if dispatched.gate else ())
        ) or (dispatched.detail,)
        flagged = list(failures)
        memo = draft
        attempts.append(
            AttemptRecord(
                attempt=attempt,
                outcome="gate_rejected",
                detail=dispatched.detail,
                flagged=failures,
            )
        )

    verified = any(record.outcome == "verified" for record in attempts)
    return MemoTurn(
        memo=memo,
        verified=verified,
        attempts=tuple(attempts),
        close_reason=close_reason,
        detail=(
            f"{len(attempts)} drafting attempt(s); "
            + ("memo verified and awaiting signature" if verified else "memo remains DRAFT")
        ),
        ledgered=tuple(ledgered),
        injection_signals=signals,
    )
