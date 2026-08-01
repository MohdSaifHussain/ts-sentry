# SPDX-License-Identifier: MIT
"""D4: turning an agent's draft into a ``Memo``, or refusing it (STEP-05 3.2).

Orchestrator-side, like ``proposal_check`` and ``rationale_check``, for the
reason the STEP-03 import-graph test found. This is the boundary where an
agent's text stops being text: it resolves the anchor the agent named to a real
document, the measure it proposed to an enum member, and hands back a typed
``Memo`` or a refusal carrying exactly one reason code.

What is checked here, and what deliberately is not
--------------------------------------------------
Here: that the draft *is* a memo. Every role present, a measure from the closed
vocabulary, an anchor that belongs to some document in the pinned corpus, an
excerpt within bounds, and every structural invariant ``Memo`` enforces.

Not here: whether the claims resolve and whether the citation quotes what it
says it quotes. That is the RECOMMEND gate, reached through dispatch, and
keeping it there is what makes the gate the thing that judges rather than one of
two things that judge. A draft that passes this check can still be refused by
the gate, and it should be: this answers "is this a memo", the gate answers "is
it supported".

The digest is resolved, never accepted
--------------------------------------
The agent names an anchor and never a document digest. This module finds which
document carries that anchor and supplies the digest itself. An agent that could
supply the digest could point a citation at a document nobody checked it
against, and asking a model to reproduce sixty-four hex characters for a check
to pass would make the check about transcription rather than about policy.

An anchor appearing in two documents is refused rather than resolved to the
first. Corpus v1 has one such collision by construction
(``what-this-policy-means-for-you`` is a heading on both the spam and
fake-engagement pages), so this is a real case and not a hypothetical: the memo
has to say which policy it is relying on, and picking one silently would decide
that for it.

Failures are returned, never raised (DECISIONS 2.4). Pure and total.
"""

from dataclasses import dataclass
from enum import StrEnum

from ts_sentry.agents.evidence.pack import EvidencePack
from ts_sentry.agents.memo.draft import DraftMemo
from ts_sentry.agents.memo.memo import (
    AutomatedMeans,
    Measure,
    Memo,
    MemoError,
    MemoSentence,
    MemoStatus,
    PolicyCitation,
    SentenceRole,
)
from ts_sentry.data.policy_corpus import PolicyCorpus, PolicyDocument

__all__ = ["DraftRefusal", "DraftVerdict", "check_draft"]

_ROLE_BY_KIND = {
    "FACT": SentenceRole.FACT,
    "GROUND": SentenceRole.POLICY_GROUND,
    "MEASURE": SentenceRole.MEASURE,
    "REDRESS": SentenceRole.REDRESS,
}


class DraftRefusal(StrEnum):
    """Why a draft was refused before it reached the gate."""

    MALFORMED = "malformed"
    MISSING_ROLE = "missing_role"
    UNKNOWN_MEASURE = "unknown_measure"
    UNKNOWN_ANCHOR = "unknown_anchor"
    AMBIGUOUS_ANCHOR = "ambiguous_anchor"
    MALFORMED_CITATION = "malformed_citation"
    STRUCTURAL = "structural"


@dataclass(frozen=True, slots=True)
class DraftVerdict:
    """Structured result. Never an exception, never a bare bool.

    ``memo`` is populated only on acceptance, so there is no partially validated
    memo for a caller to reach for by mistake, in the shape ``ProposalVerdict``
    and ``ParamResult`` both use.
    """

    accepted: bool
    memo: Memo | None
    code: DraftRefusal | None
    detail: str

    def __post_init__(self) -> None:
        if self.accepted is (self.code is not None):
            raise ValueError("an accepted draft carries no refusal code; a refused one carries one")
        if self.accepted is (self.memo is None):
            raise ValueError("an accepted draft carries the memo it produced")

    def to_ledger_payload(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "refusal_code": None if self.code is None else self.code.value,
            "detail": self.detail,
            "memo_id": None if self.memo is None else self.memo.memo_id,
        }


def _refuse(code: DraftRefusal, detail: str) -> DraftVerdict:
    return DraftVerdict(accepted=False, memo=None, code=code, detail=detail)


def _documents_with(corpus: PolicyCorpus, anchor_id: str) -> list[PolicyDocument]:
    return [document for document in corpus.documents if document.clause(anchor_id) is not None]


def check_draft(
    draft: DraftMemo,
    pack: EvidencePack,
    corpus: PolicyCorpus,
    *,
    memo_id: str,
    automated_means: AutomatedMeans,
) -> DraftVerdict:
    """Check one parsed draft and build the memo it describes."""
    if not draft.lines:
        return _refuse(
            DraftRefusal.MALFORMED,
            "the response contains no FACT, GROUND, MEASURE or REDRESS line",
        )

    present = {line.kind for line in draft.lines}
    missing = sorted({"FACT", "GROUND", "MEASURE", "REDRESS"} - present)
    if missing:
        return _refuse(
            DraftRefusal.MISSING_ROLE,
            f"the draft has no {', '.join(missing)} line; DSA Article 17(3) requires the "
            "facts relied on, the contractual ground, the measure and the redress available",
        )

    raw_measure = draft.measure_value or ""
    try:
        measure = Measure(raw_measure.strip().lower())
    except ValueError:
        return _refuse(
            DraftRefusal.UNKNOWN_MEASURE,
            f"{raw_measure!r} is not a measure this system recognises; the vocabulary is "
            "closed so a memo cannot propose a sanction nobody has to honour",
        )

    sentences: list[MemoSentence] = []
    for index, line in enumerate(draft.lines):
        role = _ROLE_BY_KIND[line.kind]
        citation: PolicyCitation | None = None

        if role is SentenceRole.POLICY_GROUND:
            anchor = (line.anchor or "").strip()
            matches = _documents_with(corpus, anchor)
            if not matches:
                return _refuse(
                    DraftRefusal.UNKNOWN_ANCHOR,
                    f"no document in corpus {corpus.corpus_version} has anchor {anchor!r}",
                )
            if len(matches) > 1:
                return _refuse(
                    DraftRefusal.AMBIGUOUS_ANCHOR,
                    f"anchor {anchor!r} appears in {[d.doc_id for d in matches]}; name a "
                    "clause that identifies one policy, because a ground that could be "
                    "either does not say which rule is relied on",
                )
            try:
                citation = PolicyCitation(
                    content_digest=matches[0].content_digest,
                    anchor_id=anchor,
                    excerpt=line.excerpt or "",
                )
            except ValueError as exc:
                return _refuse(DraftRefusal.MALFORMED_CITATION, str(exc))

        # A MEASURE sentence is rendered from the enum, never from the model's
        # prose, so the memo's measure and the sentence describing it cannot
        # disagree about what is being proposed.
        text = (
            f"The proposed measure is {measure.value}."
            if role is SentenceRole.MEASURE
            else line.text
        )

        try:
            sentences.append(
                MemoSentence.from_text(index, role, text, citation=citation)
                if role is not SentenceRole.MEASURE
                else MemoSentence(index=index, role=role, text=text)
            )
        except (MemoError, ValueError) as exc:
            return _refuse(DraftRefusal.STRUCTURAL, f"sentence {index}: {exc}")

    try:
        memo = Memo(
            memo_id=memo_id,
            case_id=pack.case_id,
            subject_id=pack.subject_id,
            pack_digest=pack.content_digest,
            corpus_version=corpus.corpus_version,
            corpus_sha256=corpus.corpus_sha256,
            sentences=tuple(sentences),
            measure=measure,
            automated_means=automated_means,
            status=MemoStatus.DRAFT,
        )
    except (MemoError, ValueError) as exc:
        return _refuse(DraftRefusal.STRUCTURAL, str(exc))

    return DraftVerdict(
        accepted=True,
        memo=memo,
        code=None,
        detail=f"{len(sentences)} sentences, measure {measure.value}",
    )
