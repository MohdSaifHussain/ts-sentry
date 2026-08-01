# SPDX-License-Identifier: MIT
"""D5: resolving a policy citation against the hashed corpus (STEP-05 D5).

The confabulation control (NIST AI 600-1, ARCHITECTURE 8.2) extended from
claims to citations. STEP-02's verifier answers "does this claim point at
evidence that exists"; this answers the same question about policy, where the
failure mode is worse: a memo can name a real document, a plausible anchor and a
fluent quotation, and be wrong about all three in a way no reader would catch
without the corpus in front of them.

Three independent things have to be true, and each gets its own reason code so
refusals stay countable by cause:

1. the document exists in the pinned corpus;
2. the anchor exists in that document;
3. the excerpt is actually there, and is within the fair-use ceiling.

Check 3 is the one that matters most and is easiest to omit. A citation that
resolves to a real clause while quoting words the clause does not contain is a
*fabricated quotation with a valid address*, which is more dangerous than a
phantom anchor because everything about it checks out except the part a reader
would rely on.

Orchestrator-side, per DECISIONS 3.11 and the ``proposal_check`` and
``pack_gate`` precedent: an agent holding its own verifier is an agent nobody
is verifying. ``agents.memo`` cannot import this module and does not need to.

Failures are returned, never raised (DECISIONS 2.4). Pure and total: no I/O, no
ledger write, and no exception on any input, including input that is not a
citation at all. The gate ledgers the outcome, which keeps this callable from a
test.
"""

from dataclasses import dataclass
from enum import StrEnum

from ts_sentry.agents.memo.memo import (
    MAX_EXCERPT_WORDS,
    MIN_EXCERPT_WORDS,
    PolicyCitation,
    excerpt_word_count,
)
from ts_sentry.data.policy_corpus import PolicyCorpus

__all__ = [
    "CitationFailure",
    "CitationResolution",
    "resolve_citation",
]


class CitationFailure(StrEnum):
    """Why a citation did not resolve.

    Four members rather than one, because they are four different findings
    about an agent and a count that could not tell them apart could not inform
    anything. A phantom anchor is a model inventing structure; an excerpt that
    is not in the clause is a model inventing *words*, which is the confabulation
    the A-01 control is named for.
    """

    UNKNOWN_DOCUMENT = "unknown_document"
    PHANTOM_ANCHOR = "phantom_anchor"
    EXCERPT_NOT_IN_CLAUSE = "excerpt_not_in_clause"
    EXCERPT_TOO_LONG = "excerpt_too_long"
    EXCERPT_TOO_SHORT = "excerpt_too_short"
    """Added at the HALT-2 review, finding 2. Distinct from
    ``EXCERPT_NOT_IN_CLAUSE`` because a too-short excerpt is a *true* quotation
    that identifies nothing, which is a different failure from a false one."""


@dataclass(frozen=True, slots=True)
class CitationResolution:
    """What became of one citation.

    ``doc_id`` and ``heading`` are populated as soon as they are known, even on
    a failure, so a flagged sentence can tell the analyst *which* document and
    clause the agent was reaching for rather than only that it missed.
    """

    resolved: bool
    citation: PolicyCitation
    code: CitationFailure | None
    detail: str
    doc_id: str | None = None
    heading: str | None = None

    def __post_init__(self) -> None:
        if self.resolved is (self.code is not None):
            raise ValueError(
                "a resolved citation carries no failure code; an unresolved one carries one"
            )

    def to_json_object(self) -> dict[str, object]:
        return {
            "resolved": self.resolved,
            "citation": self.citation.to_json_object(),
            "failure_code": None if self.code is None else self.code.value,
            "detail": self.detail,
            "doc_id": self.doc_id,
            "heading": self.heading,
        }


def _words(text: str) -> list[str]:
    """Split on whitespace, and normalise nothing else.

    Case, punctuation and curly quotes are left alone deliberately: "we do not
    allow" and "We Do Not Allow" are different quotations of a policy document,
    and a resolver that smoothed that over would be accepting a paraphrase as a
    quotation. Splitting is the only transformation applied to either side.

    A model that wraps a line differently has not misquoted anything, which is
    the one thing this does forgive.
    """
    return text.split()


def _is_contiguous_quotation(excerpt: str, clause_text: str) -> bool:
    """Whether ``excerpt`` appears in ``clause_text`` as whole words, in order.

    A word-sequence search rather than a substring search, from the HALT-2
    review, finding 3. Plain substring containment matched mid-word: ``"omment
    spam"`` is a contiguous substring of ``"Comment spam: Using..."`` and would
    have resolved, which is a quotation of something the clause does not say.

    Comparing word lists makes alignment structural rather than something a
    regex has to be trusted to get right, and it keeps the check exactly as
    strict as it was in every other respect: the words must match, in order,
    with nothing between them.
    """
    needle = _words(excerpt)
    haystack = _words(clause_text)
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[start : start + len(needle)] == needle
        for start in range(len(haystack) - len(needle) + 1)
    )


def resolve_citation(citation: PolicyCitation, corpus: PolicyCorpus) -> CitationResolution:
    """Resolve one citation against ``corpus``.

    Order is deliberate and cheapest-first only by accident: each check needs
    the previous one to have passed, because there is no anchor to look for
    until a document is found and no clause text to quote from until an anchor
    resolves.
    """
    document = corpus.document_by_content_digest(citation.content_digest)
    if document is None:
        return CitationResolution(
            resolved=False,
            citation=citation,
            code=CitationFailure.UNKNOWN_DOCUMENT,
            detail=(
                f"no document in corpus {corpus.corpus_version} has content digest "
                f"{citation.content_digest[:16]}...; this citation names a document the "
                "memo was not checked against"
            ),
        )

    clause = document.clause(citation.anchor_id)
    if clause is None:
        return CitationResolution(
            resolved=False,
            citation=citation,
            code=CitationFailure.PHANTOM_ANCHOR,
            detail=(
                f"{document.doc_id} has no anchor {citation.anchor_id!r}; the document is "
                "real and the section is not"
            ),
            doc_id=document.doc_id,
        )

    # Checked here as well as in PolicyCitation.__post_init__, and the
    # duplication is deliberate. The type's check is what makes an over-long
    # citation unconstructible; this one is what lets the gate *report* one,
    # including for a citation built by a route that bypassed the constructor.
    words = excerpt_word_count(citation.excerpt)
    if words > MAX_EXCERPT_WORDS:
        return CitationResolution(
            resolved=False,
            citation=citation,
            code=CitationFailure.EXCERPT_TOO_LONG,
            detail=(
                f"excerpt is {words} words and the fair-use ceiling is "
                f"{MAX_EXCERPT_WORDS}; a memo quotes a clause, it does not reproduce it"
            ),
            doc_id=document.doc_id,
            heading=clause.heading,
        )

    if words < MIN_EXCERPT_WORDS:
        return CitationResolution(
            resolved=False,
            citation=citation,
            code=CitationFailure.EXCERPT_TOO_SHORT,
            detail=(
                f"excerpt is {words} words and at least {MIN_EXCERPT_WORDS} are needed to "
                "identify a rule; a one-word quotation of a policy clause is true and "
                "says nothing about which ground is relied on"
            ),
            doc_id=document.doc_id,
            heading=clause.heading,
        )

    if not _is_contiguous_quotation(citation.excerpt, clause.text):
        return CitationResolution(
            resolved=False,
            citation=citation,
            code=CitationFailure.EXCERPT_NOT_IN_CLAUSE,
            detail=(
                f"the excerpt is not in {document.doc_id}#{citation.anchor_id}. The "
                "document and the anchor are real, so this is a fabricated quotation "
                "with a valid address"
            ),
            doc_id=document.doc_id,
            heading=clause.heading,
        )

    return CitationResolution(
        resolved=True,
        citation=citation,
        code=None,
        detail=f"resolves to {document.doc_id}#{citation.anchor_id} ({clause.heading})",
        doc_id=document.doc_id,
        heading=clause.heading,
    )
