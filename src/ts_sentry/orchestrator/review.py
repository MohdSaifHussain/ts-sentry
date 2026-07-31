# SPDX-License-Identifier: MIT
"""D2: the analyst decision boundary (STEP-04 D2, ARCHITECTURE 3.3).

Every pivot is a ledgered ``HUMAN_DECISION``: the agent proposes, the analyst
approves or rejects, and only then does the orchestrator execute. This module
is where the decision comes from, and it is deliberately small: it holds who
decided, what they decided, and by which mechanism, and nothing about pivots or
packs.

Two reviewers, and the honesty problem they create
--------------------------------------------------
An offline test suite and a reproducible example session both need a decision
without a human present. A scripted stand-in solves that, exactly as
``detection_stub`` stands in for an upstream detector. But this stand-in is
different in kind, and the difference is the whole governance claim of this
system: a fake detector produces a queue nobody claims is authoritative, while
a fake analyst produces a ``HUMAN_DECISION`` ledger entry, and the human in
"human decision" is the thing ARCHITECTURE 3.3 says can never be automated.

So the mechanism is recorded, not just the decision:

* ``ReviewOutcome.reviewer_kind`` has **no default** and is required by the
  type. An approval record that does not say what decided it is
  unconstructible, rather than merely discouraged.
* It goes **inside the ledgered ``HUMAN_DECISION`` payload**, so it is covered
  by ``digest_payload`` and therefore by the hash chain. Editing it in a
  session artifact afterwards makes the body disagree with the digest already
  in the chain, which ``Session.attach_event`` refuses and the artifact tests
  detect. Recording it only in a side file would have left the claim
  unprotected by the one mechanism this system has for protecting claims.
* ``attribution`` is the single rendering used everywhere a decision is shown
  to a person, and a scripted decision always renders as scripted. There is no
  code path that prints "approved by analyst" without saying how.

``InteractiveReviewer`` is the real one, and it is unrun
--------------------------------------------------------
It reads a decision from a terminal, which cannot be exercised by an offline
suite without mocking the terminal, and a mock would assert only that the code
matches the shape its author imagined. So it is marked ``no cover`` and its
procedure is documented, which is the same honest treatment ``LiveAdapter``
gets: the path exists, it is written against a real interface, and nobody has
run it. That is stated rather than implied.

Manual procedure for the interactive path
-----------------------------------------
1. Build a dataset: ``ts-sentry build-dataset --seed 42 --scale 1 --out BUILD``.
2. Run a triage session to produce a queue and pick a case id from
   ``ranked_queue.json``.
3. Run ``ts-sentry run-session --agent evidence --seed-dataset BUILD --case
   CASE --review interactive``.
4. At each prompt, enter ``a`` to approve or ``r`` to reject, then a one-line
   reason. Anything else re-prompts.
5. Confirm every hop appears in ``ledger.jsonl`` as a ``HUMAN_DECISION`` whose
   payload carries ``"reviewer_kind": "interactive"``.
"""

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

__all__ = [
    "AnalystReviewer",
    "InteractiveReviewer",
    "ReviewDecision",
    "ReviewOutcome",
    "ReviewRequest",
    "ReviewerKind",
    "ScriptedReviewer",
]


class ReviewerKind(StrEnum):
    """What produced a decision.

    Not a detail. ``SCRIPTED`` means no human was present, and every artifact
    and ledger entry carrying a decision says which of these it was.
    """

    SCRIPTED = "scripted"
    INTERACTIVE = "interactive"


class ReviewDecision(StrEnum):
    """What the analyst decided about one proposal.

    There is no third member. A proposal is approved or it is not, and
    "deferred" would be a state the mandate's step budget has no way to bound.
    """

    APPROVE = "approve"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    """What the analyst is shown before deciding.

    Everything here is orchestrator-authored or already validated: the pivot
    resolved to a known kind, the parameters passed typing and bounds, and the
    reason's citations resolved against the pack. The analyst is asked to
    approve a specific, checked query, not to adjudicate a claim the machine
    already knows is unsupported.
    """

    case_id: str
    subject_id: str
    hop_index: int
    pivot_kind: str
    template_id: str
    template_sha256: str
    param_hash: str
    params: Mapping[str, object]
    summary: str
    reason: str

    def render(self) -> str:
        """One screen an analyst can decide from."""
        params = ", ".join(f"{name}={self.params[name]!r}" for name in sorted(self.params))
        return "\n".join(
            (
                f"case      : {self.case_id} (subject {self.subject_id})",
                f"hop       : {self.hop_index}",
                f"pivot     : {self.pivot_kind}  [{self.template_id}]",
                f"asks      : {self.summary}",
                f"params    : {params}",
                f"agent says: {self.reason}",
            )
        )


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    """One analyst decision, and what produced it.

    ``reviewer_kind`` carries no default on purpose. A record that does not say
    what decided it cannot be constructed, so there is no path by which a
    scripted decision reaches the ledger looking like a human one.
    """

    decision: ReviewDecision
    reviewer_kind: ReviewerKind
    reviewer_id: str
    reason: str

    def __post_init__(self) -> None:
        if not self.reviewer_id.strip():
            raise ValueError("a decision names the analyst identity it was made under")
        if not self.reason.strip():
            raise ValueError(
                "a decision states its reason; an unexplained approval is a rubber stamp"
            )

    @property
    def approved(self) -> bool:
        return self.decision is ReviewDecision.APPROVE

    @property
    def by_human(self) -> bool:
        """Whether a person actually made this decision.

        The one place the distinction is computed, so nothing has to re-derive
        it from the kind and get it wrong.
        """
        return self.reviewer_kind is ReviewerKind.INTERACTIVE

    def attribution(self) -> str:
        """How this decision is rendered wherever a person reads it.

        A scripted decision always renders as scripted. This is the single
        rendering used by the CLI and the session artifact, so there is no code
        path that shows an approval without showing what made it.
        """
        qualifier = "human analyst" if self.by_human else "scripted stand-in, no human present"
        return f"{self.decision.value} by {self.reviewer_id} ({qualifier})"

    def to_ledger_payload(self) -> dict[str, object]:
        """The fields that go into the ``HUMAN_DECISION`` entry.

        ``reviewer_kind`` is in here rather than only in a session artifact so
        that it is digested into the chain and cannot be edited afterwards
        without the body disagreeing with the digest.
        """
        return {
            "decision": self.decision.value,
            "reviewer_kind": self.reviewer_kind.value,
            "reviewer_id": self.reviewer_id,
            "by_human": self.by_human,
            "reason": self.reason,
        }


class AnalystReviewer(Protocol):
    """The boundary an analyst decision arrives through.

    Injected rather than constructed, the same way the model adapter and the
    clock are. A session that reached for a default reviewer would be a session
    that could approve its own pivots.
    """

    @property
    def reviewer_kind(self) -> ReviewerKind: ...

    @property
    def reviewer_id(self) -> str: ...

    def review(self, request: ReviewRequest, /) -> ReviewOutcome: ...


@dataclass(frozen=True, slots=True)
class ScriptedReviewer:
    """Deterministic decisions declared up front. The CI and example path.

    Decisions are consumed in order and ``default`` covers anything past the
    end of the script, so a session replays identically and a test can state
    exactly which hop is rejected. It is a stand-in and says so in every record
    it produces.
    """

    reviewer_id: str = "analyst"
    decisions: Sequence[ReviewDecision] = ()
    default: ReviewDecision = ReviewDecision.APPROVE
    note: str = "scripted decision, declared before the session ran"

    @property
    def reviewer_kind(self) -> ReviewerKind:
        return ReviewerKind.SCRIPTED

    def review(self, request: ReviewRequest, /) -> ReviewOutcome:
        """Decide by position in the script.

        Indexed by ``hop_index`` rather than by a mutable counter, so the
        reviewer is stateless and the same hop always gets the same answer
        however many times a test replays it.
        """
        position = request.hop_index - 1
        in_script = 0 <= position < len(self.decisions)
        decision = self.decisions[position] if in_script else self.default
        return ReviewOutcome(
            decision=decision,
            reviewer_kind=self.reviewer_kind,
            reviewer_id=self.reviewer_id,
            reason=f"{self.note} (hop {request.hop_index})",
        )


@dataclass(frozen=True, slots=True)
class InteractiveReviewer:
    """A real analyst at a terminal. Written, documented, and unrun.

    Marked ``no cover`` for the reason ``LiveAdapter.complete`` is: covering it
    means mocking a terminal, and a mock would assert only that this code
    matches the shape its author imagined for a person. The manual procedure is
    in the module docstring and has not been performed, which is stated rather
    than implied.
    """

    reviewer_id: str = "analyst"

    @property
    def reviewer_kind(self) -> ReviewerKind:
        return ReviewerKind.INTERACTIVE

    def review(self, request: ReviewRequest, /) -> ReviewOutcome:  # pragma: no cover
        print(request.render(), file=sys.stderr)
        while True:
            answer = input("approve or reject this pivot? [a/r]: ").strip().lower()
            if answer in {"a", "approve"}:
                decision = ReviewDecision.APPROVE
                break
            if answer in {"r", "reject"}:
                decision = ReviewDecision.REJECT
                break
            print("answer 'a' to approve or 'r' to reject.", file=sys.stderr)

        reason = input("reason (one line): ").strip()
        return ReviewOutcome(
            decision=decision,
            reviewer_kind=self.reviewer_kind,
            reviewer_id=self.reviewer_id,
            reason=reason or "no reason given",
        )
