# SPDX-License-Identifier: MIT
"""D5: the ``RESOLVE_POLICY_CITATION`` handler (STEP-05 D5).

The tool the memo agent proposes, and the last of the four declared in
``TOOL_TABLE`` to become executable before STEP-06. Its job is narrow: attach
one policy citation to one POLICY_GROUND sentence of the draft memo, and hand
back the memo.

Attach, do not judge
--------------------
This handler does not decide whether the citation is good. It builds it,
attaches it, and returns the grown memo; the RECOMMEND gate then resolves every
citation and rejects the memo if any fails. The split is deliberate and follows
DECISIONS 4.6 exactly: dispatch runs the consequence gate over whatever a tool
returns, so a handler that refused a bad citation itself would be producing a
``FAILED`` dispatch, which reads as a defect, where the truthful outcome is a
``GATE_REJECTION`` carrying the resolver's reason code. A governance finding
must not be recorded as a crash.

It is also why an unresolvable citation cannot be smuggled through by attaching
it here: the gate runs on the returned memo either way.

What it does refuse
-------------------
Structural impossibilities, as ``ToolViolation``, because they are the caller
handing this function something that is not a citation attachment at all: a
sentence index that names no sentence, a sentence that is not a POLICY_GROUND,
and an absent memo or corpus. Those are not governed outcomes about a proposal;
they are a broken call.

The memo and the corpus arrive through ``ToolResources``, never through
``params``. An agent that could supply the memo could supply one whose sentences
it had rewritten, and an agent that could supply the corpus could supply one
whose clauses contained whatever it wished to quote, which would make every
citation resolve by construction.
"""

from ts_sentry.agents.memo.memo import Memo, MemoSentence, PolicyCitation, SentenceRole
from ts_sentry.orchestrator.toolspec import ToolContext, ToolViolation

__all__ = [
    "ANCHOR_PARAM",
    "CONTENT_DIGEST_PARAM",
    "EXCERPT_PARAM",
    "SENTENCE_INDEX_PARAM",
    "resolve_policy_citation",
]

SENTENCE_INDEX_PARAM = "sentence_index"
CONTENT_DIGEST_PARAM = "content_digest"
ANCHOR_PARAM = "anchor_id"
EXCERPT_PARAM = "excerpt"


def _require_str(context: ToolContext, name: str) -> str:
    value = context.params.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ToolViolation(f"{name} must be a non-empty string; got {value!r}")
    return value


def resolve_policy_citation(context: ToolContext, /) -> object:
    """Attach one policy citation to the draft memo and return the memo.

    Returns a ``Memo`` because that is what the memo mandate declares as its
    ``output_schema``, so dispatch's schema check and the RECOMMEND gate both
    see the whole artifact rather than a fragment of it.
    """
    memo = context.resources.memo
    if not isinstance(memo, Memo):
        raise ToolViolation(
            "this tool needs the draft memo, which the orchestrator supplies through "
            f"ToolResources; got {type(memo).__name__}. An agent cannot provide one"
        )
    if context.resources.corpus is None:
        raise ToolViolation(
            "this tool needs the policy corpus, which the orchestrator supplies through "
            "ToolResources; an agent cannot provide one"
        )

    raw_index = context.params.get(SENTENCE_INDEX_PARAM)
    if not isinstance(raw_index, int) or isinstance(raw_index, bool):
        raise ToolViolation(f"{SENTENCE_INDEX_PARAM} must be an integer; got {raw_index!r}")
    if not 0 <= raw_index < len(memo.sentences):
        raise ToolViolation(
            f"{SENTENCE_INDEX_PARAM} {raw_index} names no sentence; this memo has "
            f"{len(memo.sentences)}"
        )

    target = memo.sentences[raw_index]
    if target.role is not SentenceRole.POLICY_GROUND:
        raise ToolViolation(
            f"sentence {raw_index} is a {target.role.value}; only a POLICY_GROUND carries "
            "the contractual ground relied on (DSA Article 17(3)(e))"
        )

    # PolicyCitation validates its own shape, including the fair-use excerpt
    # ceiling. A ValueError here is an agent proposing something the type
    # forbids, which is a refusal rather than a crash.
    try:
        citation = PolicyCitation(
            content_digest=_require_str(context, CONTENT_DIGEST_PARAM),
            anchor_id=_require_str(context, ANCHOR_PARAM),
            excerpt=_require_str(context, EXCERPT_PARAM),
        )
    except ValueError as exc:
        raise ToolViolation(f"the proposed citation is not well formed: {exc}") from exc

    revised = [
        sentence if index != raw_index else _attach(sentence, citation)
        for index, sentence in enumerate(memo.sentences)
    ]
    return memo.with_sentences(revised)


def _attach(sentence: MemoSentence, citation: PolicyCitation) -> MemoSentence:
    """Rebuild one sentence carrying its citation.

    A new object rather than a mutation, because the memo is frozen, which is
    what keeps "this memo passed the gate" a statement about a specific set of
    bytes.
    """
    return MemoSentence(
        index=sentence.index,
        role=sentence.role,
        text=sentence.text,
        evidence_ids=sentence.evidence_ids,
        citation=citation,
    )
