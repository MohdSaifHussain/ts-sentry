# SPDX-License-Identifier: MIT
"""STEP-05 D3/D5: the memo AST, the citation resolver, and the RECOMMEND gate.

Offline throughout. Citations resolve against the committed corpus in
``policies/``, so these tests check the real clause text rather than a fixture
that could drift from it.

The negative paths are the point of the phase. A memo that resolves is easy;
what STEP-05 has to show is the gate catching a fabricated quotation with a
valid address, and saying which sentence it was.
"""

from datetime import datetime
from pathlib import Path

import pytest

from ts_sentry.agents.evidence.pack import EvidencePack
from ts_sentry.agents.memo.memo import (
    MAX_EXCERPT_WORDS,
    AutomatedDecision,
    AutomatedMeans,
    Measure,
    Memo,
    MemoError,
    MemoSentence,
    MemoStatus,
    PolicyCitation,
    SentenceRole,
)
from ts_sentry.data.enums import EntityKind
from ts_sentry.data.policy_corpus import PolicyCorpus, load_corpus
from ts_sentry.data.tz import IST
from ts_sentry.governance.gates import FailureCode
from ts_sentry.orchestrator.citation_resolver import CitationFailure, resolve_citation
from ts_sentry.orchestrator.citation_tool import resolve_policy_citation
from ts_sentry.orchestrator.memo_gate import MemoCheck, memo_check, memo_checker
from ts_sentry.orchestrator.toolspec import ToolContext, ToolResources, ToolViolation

POLICIES_DIR = Path(__file__).resolve().parent.parent / "policies"
_TS = datetime(2026, 8, 1, 12, 0, tzinfo=IST).isoformat()


@pytest.fixture(scope="module")
def corpus() -> PolicyCorpus:
    return load_corpus(POLICIES_DIR)


@pytest.fixture(scope="module")
def pack() -> EvidencePack:
    return EvidencePack.seed("case-0001", "t02_chan_000_000", EntityKind.CHANNEL, _TS)


@pytest.fixture(scope="module")
def spam_citation(corpus: PolicyCorpus) -> PolicyCitation:
    """A real citation of the comment-spam clause, quoted from the corpus.

    Built by reading the committed clause rather than by typing a quotation out,
    so the fixture cannot drift from the text it claims to quote.
    """
    document = corpus.document("youtube-spam")
    assert document is not None
    clause = document.clause("comment-spam")
    assert clause is not None
    return PolicyCitation(
        content_digest=document.content_digest,
        anchor_id="comment-spam",
        excerpt=" ".join(clause.text.split()[:10]),
    )


def _means() -> AutomatedMeans:
    return AutomatedMeans(
        detection_automated=True,
        decision=AutomatedDecision.PARTIALLY_AUTOMATED,
        drafted_by="agents.memo/stub",
    )


def _memo(
    pack: EvidencePack,
    corpus: PolicyCorpus,
    citation: PolicyCitation,
    *,
    sentences: tuple[MemoSentence, ...] | None = None,
    status: MemoStatus = MemoStatus.DRAFT,
    pack_digest: str | None = None,
    corpus_sha256: str | None = None,
    means: AutomatedMeans | None = None,
) -> Memo:
    seed_id = pack.provenance[0].provenance_id
    default = (
        MemoSentence.from_text(
            0,
            SentenceRole.FACT,
            f"The subject channel entered this investigation as its seed [{seed_id}].",
        ),
        MemoSentence(
            index=1,
            role=SentenceRole.POLICY_GROUND,
            text="This conduct is incompatible with the platform's spam policy.",
            citation=citation,
        ),
        MemoSentence(
            index=2, role=SentenceRole.MEASURE, text="Demotion of the content is proposed."
        ),
        MemoSentence(
            index=3,
            role=SentenceRole.REDRESS,
            text="The channel owner may appeal through the internal complaint-handling system.",
        ),
    )
    return Memo(
        memo_id="memo-0001",
        case_id=pack.case_id,
        subject_id=pack.subject_id,
        pack_digest=pack_digest or pack.content_digest,
        corpus_version=corpus.corpus_version,
        corpus_sha256=corpus_sha256 or corpus.corpus_sha256,
        sentences=sentences or default,
        measure=Measure.CONTENT_DEMOTED,
        automated_means=means or _means(),
        status=status,
    )


# ---------------------------------------------------------------------------
# The AST
# ---------------------------------------------------------------------------


def test_a_well_formed_memo_builds(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    memo = _memo(pack, corpus, spam_citation)

    assert memo.status is MemoStatus.DRAFT
    assert not memo.signed
    assert len(memo.facts) == 1
    assert len(memo.policy_grounds) == 1


def test_a_fact_with_no_evidence_is_unconstructible() -> None:
    """Article 17(3)(b) requires the facts relied on. A fact nobody can trace to
    the pack is the A-01 confabulation the whole gate exists to refuse, and it
    is refused by the type rather than merely by the gate."""
    with pytest.raises(MemoError, match="FACT citing no evidence"):
        MemoSentence(index=0, role=SentenceRole.FACT, text="The channel is part of a ring.")


def test_a_policy_ground_with_no_citation_is_unconstructible() -> None:
    with pytest.raises(MemoError, match="POLICY_GROUND with no citation"):
        MemoSentence(index=0, role=SentenceRole.POLICY_GROUND, text="This breaches the rules.")


def test_only_a_policy_ground_may_carry_a_citation(spam_citation: PolicyCitation) -> None:
    """So a reader knows which sentence is the rule being relied on."""
    with pytest.raises(MemoError, match="carrying a policy citation"):
        MemoSentence(
            index=0,
            role=SentenceRole.MEASURE,
            text="Demotion is proposed.",
            citation=spam_citation,
        )


def test_from_text_derives_evidence_ids_from_the_bracketed_citations() -> None:
    sentence = MemoSentence.from_text(
        0, SentenceRole.FACT, "Two accounts share a device [prov-0001] and an IP [node-7]."
    )

    assert sentence.evidence_ids == frozenset({"prov-0001", "node-7"})


def test_a_sentence_cannot_display_a_citation_it_does_not_record() -> None:
    """Found by a test, not by inspection.

    A gate test expected three failures and got two: a sentence built directly
    rather than through ``from_text`` carried ``[prov-8002]`` in its prose while
    its ``evidence_ids`` stayed empty, so the claim checker never submitted it
    and the sentence read to a human as supported while resolving nothing. That
    is the confabulation this system exists to catch, arriving through the
    constructor rather than through a model.
    """
    with pytest.raises(MemoError, match="displays citations it does not record"):
        MemoSentence(
            index=0,
            role=SentenceRole.MEASURE,
            text="Demotion is proposed on the evidence [prov-0001].",
        )

    # Recording more than is displayed stays legal: the verifier checks
    # everything recorded, so the extra id is checked rather than hidden.
    MemoSentence(
        index=0,
        role=SentenceRole.FACT,
        text="Two accounts share a device [prov-0001].",
        evidence_ids=frozenset({"prov-0001", "prov-0002"}),
    )


def test_a_memo_missing_a_role_is_not_a_statement_of_reasons(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    """DSA Article 17(3) requires (a), (b), (e) and (f). A memo with three of
    the four roles is prose, not a statement of reasons."""
    seed_id = pack.provenance[0].provenance_id
    with pytest.raises(MemoError, match="carries every role"):
        _memo(
            pack,
            corpus,
            spam_citation,
            sentences=(
                MemoSentence.from_text(0, SentenceRole.FACT, f"A fact [{seed_id}]."),
                MemoSentence(
                    index=1,
                    role=SentenceRole.POLICY_GROUND,
                    text="A ground.",
                    citation=spam_citation,
                ),
                MemoSentence(index=2, role=SentenceRole.MEASURE, text="A measure."),
            ),
        )


def test_sentence_indices_must_be_contiguous(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    """The verifier reports failures by index, so an index that does not locate
    a sentence cannot be acted on by a revise loop."""
    seed_id = pack.provenance[0].provenance_id
    with pytest.raises(MemoError, match="contiguous from zero"):
        _memo(
            pack,
            corpus,
            spam_citation,
            sentences=(
                MemoSentence.from_text(0, SentenceRole.FACT, f"A fact [{seed_id}]."),
                MemoSentence(
                    index=5,
                    role=SentenceRole.POLICY_GROUND,
                    text="A ground.",
                    citation=spam_citation,
                ),
                MemoSentence(index=2, role=SentenceRole.MEASURE, text="A measure."),
                MemoSentence(index=3, role=SentenceRole.REDRESS, text="Redress."),
            ),
        )


def test_a_signed_memo_cannot_claim_a_fully_automated_decision(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    """The ENFORCE invariant reaching the memo.

    A signature is exactly the human step that makes the decision partially
    automated, so a signed memo disclosing full automation would describe a path
    this system does not have. Enforced, not documented.
    """
    fully = AutomatedMeans(
        detection_automated=True,
        decision=AutomatedDecision.FULLY_AUTOMATED,
        drafted_by="agents.memo/stub",
    )

    # Legal while the memo is a draft: nothing has been decided yet.
    _memo(pack, corpus, spam_citation, means=fully)

    with pytest.raises(MemoError, match="fully automated decision"):
        _memo(pack, corpus, spam_citation, status=MemoStatus.SIGNED, means=fully)


def test_automated_means_must_name_its_drafter() -> None:
    with pytest.raises(ValueError, match="names what drafted it"):
        AutomatedMeans(
            detection_automated=True,
            decision=AutomatedDecision.PARTIALLY_AUTOMATED,
            drafted_by="  ",
        )


def test_revising_a_signed_memo_returns_a_draft(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    """The signature was over text that no longer exists."""
    signed = _memo(pack, corpus, spam_citation, status=MemoStatus.SIGNED)

    revised = signed.with_sentences(signed.sentences)

    assert signed.status is MemoStatus.SIGNED
    assert revised.status is MemoStatus.DRAFT


def test_the_memo_digest_changes_when_any_sentence_does(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    """What makes ``HumanSignature.subject_hash`` mean something for memos: a
    signature cannot silently carry over to an edited memo."""
    memo = _memo(pack, corpus, spam_citation)
    edited = memo.with_sentences(
        (
            *memo.sentences[:2],
            MemoSentence(index=2, role=SentenceRole.MEASURE, text="Termination is proposed."),
            memo.sentences[3],
        )
    )

    assert edited.content_digest != memo.content_digest


def test_an_over_long_excerpt_is_unconstructible(corpus: PolicyCorpus) -> None:
    """A fair-use posture a model can exceed is not a posture."""
    document = corpus.document("youtube-spam")
    assert document is not None
    clause = document.clause("comment-spam")
    assert clause is not None

    with pytest.raises(ValueError, match="fair-use ceiling"):
        PolicyCitation(
            content_digest=document.content_digest,
            anchor_id="comment-spam",
            excerpt=" ".join(clause.text.split()[: MAX_EXCERPT_WORDS + 1]),
        )


# ---------------------------------------------------------------------------
# The citation resolver
# ---------------------------------------------------------------------------


def test_a_real_citation_resolves(corpus: PolicyCorpus, spam_citation: PolicyCitation) -> None:
    resolution = resolve_citation(spam_citation, corpus)

    assert resolution.resolved
    assert resolution.code is None
    assert resolution.doc_id == "youtube-spam"
    assert resolution.heading == "Comment spam"


def test_an_unknown_document_is_refused(corpus: PolicyCorpus) -> None:
    citation = PolicyCitation(content_digest="d" * 64, anchor_id="comment-spam", excerpt="spam")

    resolution = resolve_citation(citation, corpus)

    assert resolution.code is CitationFailure.UNKNOWN_DOCUMENT


def test_a_phantom_anchor_is_refused(corpus: PolicyCorpus) -> None:
    """The document is real and the section is not."""
    document = corpus.document("youtube-spam")
    assert document is not None
    citation = PolicyCitation(
        content_digest=document.content_digest,
        anchor_id="coordinated-inauthentic-behaviour",  # plausible, and not there
        excerpt="high-volume, repetitive, or deceptive comments",
    )

    resolution = resolve_citation(citation, corpus)

    assert resolution.code is CitationFailure.PHANTOM_ANCHOR
    assert resolution.doc_id == "youtube-spam"


def test_a_fabricated_quotation_with_a_valid_address_is_refused(corpus: PolicyCorpus) -> None:
    """The failure this phase most has to catch.

    Everything about this citation checks out except the part a reader would
    rely on: real document, real anchor, words the clause does not contain.
    """
    document = corpus.document("youtube-spam")
    assert document is not None
    citation = PolicyCitation(
        content_digest=document.content_digest,
        anchor_id="comment-spam",
        excerpt="comment spam is punishable by immediate termination",
    )

    resolution = resolve_citation(citation, corpus)

    assert resolution.code is CitationFailure.EXCERPT_NOT_IN_CLAUSE
    assert resolution.doc_id == "youtube-spam"
    assert resolution.heading == "Comment spam"


def test_the_resolver_accepts_rewrapped_whitespace_and_not_a_paraphrase(
    corpus: PolicyCorpus,
) -> None:
    """Whitespace is normalised; nothing else is.

    A model that wraps a line differently has not misquoted anything. A model
    that changes a word has, and smoothing that over would be accepting a
    paraphrase as a quotation.
    """
    document = corpus.document("youtube-spam")
    assert document is not None
    clause = document.clause("comment-spam")
    assert clause is not None
    words = clause.text.split()[:8]

    rewrapped = PolicyCitation(
        content_digest=document.content_digest,
        anchor_id="comment-spam",
        excerpt="  ".join(words) + "\n",
    )
    paraphrased = PolicyCitation(
        content_digest=document.content_digest,
        anchor_id="comment-spam",
        excerpt=" ".join(words).replace("repetitive", "recurring"),
    )

    assert resolve_citation(rewrapped, corpus).resolved
    assert resolve_citation(paraphrased, corpus).code is CitationFailure.EXCERPT_NOT_IN_CLAUSE


def test_the_resolver_is_total(corpus: PolicyCorpus) -> None:
    """Pure and total: no exception on any input, so the gate never degrades a
    precise finding into a generic CHECKER_ERROR."""
    for anchor, excerpt in (("", "x"), ("comment-spam", "x"), ("a" * 500, "y")):
        citation = PolicyCitation(content_digest="e" * 64, anchor_id=anchor or "x", excerpt=excerpt)
        assert resolve_citation(citation, corpus).code is not None


# ---------------------------------------------------------------------------
# The RECOMMEND gate
# ---------------------------------------------------------------------------


def test_a_supported_memo_passes_the_gate(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    assert memo_check(_memo(pack, corpus, spam_citation), pack, corpus) == ()


def test_an_unsupported_fact_fails_the_memo_and_names_the_sentence(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    """Zero tolerance, and the failure locates the sentence so the D4 revise
    loop can point at it rather than hand back a whole failed document."""
    memo = _memo(
        pack,
        corpus,
        spam_citation,
        sentences=(
            MemoSentence.from_text(
                0, SentenceRole.FACT, "Eight accounts share one device [prov-9999]."
            ),
            MemoSentence(
                index=1,
                role=SentenceRole.POLICY_GROUND,
                text="Incompatible with the spam policy.",
                citation=spam_citation,
            ),
            MemoSentence(index=2, role=SentenceRole.MEASURE, text="Demotion is proposed."),
            MemoSentence(index=3, role=SentenceRole.REDRESS, text="Appeal is available."),
        ),
    )

    failures = memo_check(memo, pack, corpus)

    assert len(failures) == 1
    assert failures[0].code is FailureCode.UNVERIFIED_CLAIM
    assert "sentence 0" in failures[0].detail
    assert "prov-9999" in failures[0].detail


def test_a_phantom_citation_fails_the_memo(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    document = corpus.document("youtube-spam")
    assert document is not None
    phantom = PolicyCitation(
        content_digest=document.content_digest,
        anchor_id="no-such-clause",
        excerpt="whatever the model wished to quote",
    )
    memo = _memo(pack, corpus, spam_citation).with_sentences(
        (
            MemoSentence.from_text(
                0,
                SentenceRole.FACT,
                f"The seed entered here [{pack.provenance[0].provenance_id}].",
            ),
            MemoSentence(
                index=1, role=SentenceRole.POLICY_GROUND, text="A ground.", citation=phantom
            ),
            MemoSentence(index=2, role=SentenceRole.MEASURE, text="Demotion is proposed."),
            MemoSentence(index=3, role=SentenceRole.REDRESS, text="Appeal is available."),
        )
    )

    failures = memo_check(memo, pack, corpus)

    assert len(failures) == 1
    assert "sentence 1" in failures[0].detail
    assert CitationFailure.PHANTOM_ANCHOR.value in failures[0].detail


def test_every_failure_is_reported_not_only_the_first(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    """A memo with three problems is reported as a memo with three problems.
    Reporting one at a time would spend a revise step per defect."""
    document = corpus.document("youtube-spam")
    assert document is not None
    memo = _memo(pack, corpus, spam_citation).with_sentences(
        (
            MemoSentence.from_text(0, SentenceRole.FACT, "One [prov-8001]."),
            MemoSentence.from_text(
                1,
                SentenceRole.POLICY_GROUND,
                "A ground [prov-8002].",
                citation=PolicyCitation(
                    content_digest=document.content_digest,
                    anchor_id="comment-spam",
                    excerpt="words that are not in the clause at all",
                ),
            ),
            MemoSentence(index=2, role=SentenceRole.MEASURE, text="Demotion is proposed."),
            MemoSentence(index=3, role=SentenceRole.REDRESS, text="Appeal is available."),
        )
    )

    failures = memo_check(memo, pack, corpus)

    assert len(failures) == 3  # two unresolvable claims, one fabricated quotation
    assert sum(1 for f in failures if "sentence 0" in f.detail) == 1
    assert sum(1 for f in failures if "sentence 1" in f.detail) == 2


def test_a_memo_drafted_from_another_pack_is_refused(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    """The binding check. Without it the gate would verify a memo's claims
    against whichever pack it happened to be handed."""
    memo = _memo(pack, corpus, spam_citation, pack_digest="b" * 64)

    failures = memo_check(memo, pack, corpus)

    assert len(failures) == 1
    assert failures[0].code is FailureCode.SCHEMA_INVALID
    assert "drafted from different evidence" in failures[0].detail


def test_a_memo_pinned_to_another_corpus_is_refused(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    memo = _memo(pack, corpus, spam_citation, corpus_sha256="c" * 64)

    failures = memo_check(memo, pack, corpus)

    assert len(failures) == 1
    assert "checked against a different corpus" in failures[0].detail


def test_a_binding_failure_suppresses_the_rest(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    """Claims verified against the wrong pack would produce failures describing
    nothing real. The binding failure is the finding worth acting on."""
    memo = _memo(
        pack,
        corpus,
        spam_citation,
        pack_digest="b" * 64,
        sentences=(
            MemoSentence.from_text(0, SentenceRole.FACT, "Unsupported [prov-9999]."),
            MemoSentence(
                index=1, role=SentenceRole.POLICY_GROUND, text="Ground.", citation=spam_citation
            ),
            MemoSentence(index=2, role=SentenceRole.MEASURE, text="Demotion."),
            MemoSentence(index=3, role=SentenceRole.REDRESS, text="Appeal."),
        ),
    )

    failures = memo_check(memo, pack, corpus)

    assert len(failures) == 1
    assert failures[0].code is FailureCode.SCHEMA_INVALID


def test_the_gate_refuses_anything_that_is_not_a_memo(
    pack: EvidencePack, corpus: PolicyCorpus
) -> None:
    """Fail-closed at the artifact boundary, as ``pack_gate`` is."""
    check = memo_checker(pack, corpus)

    failures = check({"looks": "memo-ish"})

    assert failures[0].code is FailureCode.SCHEMA_INVALID
    assert "expected a Memo" in failures[0].detail


def test_the_checker_is_a_closure_over_what_a_claim_may_resolve_against(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    """What counts as resolvable is established before the gate runs, never
    something the memo asserts about itself."""
    check = memo_checker(pack, corpus)

    assert isinstance(check, MemoCheck)
    assert check.pack is pack
    assert check.corpus is corpus
    assert check(_memo(pack, corpus, spam_citation)) == ()


def test_measure_and_redress_sentences_need_no_evidence(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    """Not leniency. A measure states what is proposed and a redress states a
    procedural right; neither is an assertion about the subject, so requiring a
    citation would require citing something that does not exist."""
    memo = _memo(pack, corpus, spam_citation)

    assert memo.sentences[2].evidence_ids == frozenset()
    assert memo.sentences[3].evidence_ids == frozenset()
    assert memo_check(memo, pack, corpus) == ()


# ---------------------------------------------------------------------------
# The RESOLVE_POLICY_CITATION handler
# ---------------------------------------------------------------------------


def _context(memo_obj: object, corpus_obj: PolicyCorpus | None, **params: object) -> ToolContext:
    return ToolContext(
        agent_id="memo",
        granted_scopes=frozenset(),
        params=params,
        resources=ToolResources(memo=memo_obj, corpus=corpus_obj),
    )


def test_the_handler_attaches_a_citation_and_returns_the_memo(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    """Returns a ``Memo``, not a resolution record.

    DECISIONS 4.6: dispatch gates whatever a tool returns and checks it against
    the mandate's output schema, so handing back a fragment would mean the gate
    validated the fragment while nothing validated the memo.
    """
    memo = _memo(pack, corpus, spam_citation)

    result = resolve_policy_citation(
        _context(
            memo,
            corpus,
            sentence_index=1,
            content_digest=spam_citation.content_digest,
            anchor_id="scams",
            excerpt="Scams: Promoting",
        )
    )

    assert isinstance(result, Memo)
    assert result is not memo  # frozen; a new object, never a mutation
    assert result.sentences[1].citation is not None
    assert result.sentences[1].citation.anchor_id == "scams"
    assert memo.sentences[1].citation is not None
    assert memo.sentences[1].citation.anchor_id == "comment-spam"  # original untouched


def test_the_handler_attaches_a_bad_citation_and_leaves_the_gate_to_refuse_it(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    """The division of labour this handler exists to respect.

    A handler that refused a fabricated quotation itself would produce a
    ``FAILED`` dispatch, which reads as a defect. The truthful outcome is a
    ``GATE_REJECTION`` carrying the resolver's reason code, so the handler
    attaches and the gate judges. Nothing is smuggled through: the gate runs on
    the returned memo either way.
    """
    memo = _memo(pack, corpus, spam_citation)

    result = resolve_policy_citation(
        _context(
            memo,
            corpus,
            sentence_index=1,
            content_digest=spam_citation.content_digest,
            anchor_id="comment-spam",
            excerpt="words the clause does not contain",
        )
    )

    assert isinstance(result, Memo)
    failures = memo_check(result, pack, corpus)
    assert len(failures) == 1
    assert CitationFailure.EXCERPT_NOT_IN_CLAUSE.value in failures[0].detail


def test_the_handler_refuses_a_memo_or_corpus_it_was_not_lent(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    """Both arrive through ``ToolResources``, never through ``params``.

    An agent that could supply the memo could supply one whose sentences it had
    rewritten; an agent that could supply the corpus could supply one whose
    clauses contained whatever it wished to quote, which would make every
    citation resolve by construction.
    """
    memo = _memo(pack, corpus, spam_citation)
    args = {
        "sentence_index": 1,
        "content_digest": spam_citation.content_digest,
        "anchor_id": "comment-spam",
        "excerpt": "Comment spam: Using",
    }

    with pytest.raises(ToolViolation, match="needs the draft memo"):
        resolve_policy_citation(_context(None, corpus, **args))
    with pytest.raises(ToolViolation, match="needs the policy corpus"):
        resolve_policy_citation(_context(memo, None, **args))


@pytest.mark.parametrize(
    ("index", "message"),
    [
        (99, "names no sentence"),
        (-1, "names no sentence"),
        ("1", "must be an integer"),
        (True, "must be an integer"),
    ],
)
def test_the_handler_refuses_a_sentence_index_that_locates_nothing(
    pack: EvidencePack,
    corpus: PolicyCorpus,
    spam_citation: PolicyCitation,
    index: object,
    message: str,
) -> None:
    with pytest.raises(ToolViolation, match=message):
        resolve_policy_citation(
            _context(
                _memo(pack, corpus, spam_citation),
                corpus,
                sentence_index=index,
                content_digest=spam_citation.content_digest,
                anchor_id="comment-spam",
                excerpt="Comment spam: Using",
            )
        )


def test_the_handler_refuses_to_cite_anything_but_a_policy_ground(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    """Article 17(3)(e) puts the contractual ground in one place."""
    with pytest.raises(ToolViolation, match="only a POLICY_GROUND"):
        resolve_policy_citation(
            _context(
                _memo(pack, corpus, spam_citation),
                corpus,
                sentence_index=2,  # the MEASURE sentence
                content_digest=spam_citation.content_digest,
                anchor_id="comment-spam",
                excerpt="Comment spam: Using",
            )
        )


def test_the_handler_refuses_a_malformed_citation(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation
) -> None:
    """An over-long excerpt is a refusal, not a crash: the type forbids it and
    the handler reports that as a governed refusal of the proposal."""
    document = corpus.document("youtube-spam")
    assert document is not None
    clause = document.clause("comment-spam")
    assert clause is not None

    with pytest.raises(ToolViolation, match="not well formed"):
        resolve_policy_citation(
            _context(
                _memo(pack, corpus, spam_citation),
                corpus,
                sentence_index=1,
                content_digest=spam_citation.content_digest,
                anchor_id="comment-spam",
                excerpt=" ".join(clause.text.split()[: MAX_EXCERPT_WORDS + 3]),
            )
        )


@pytest.mark.parametrize("missing", ["content_digest", "anchor_id", "excerpt"])
def test_the_handler_refuses_a_missing_or_blank_parameter(
    pack: EvidencePack, corpus: PolicyCorpus, spam_citation: PolicyCitation, missing: str
) -> None:
    args: dict[str, object] = {
        "sentence_index": 1,
        "content_digest": spam_citation.content_digest,
        "anchor_id": "comment-spam",
        "excerpt": "Comment spam: Using",
    }
    args[missing] = "   "

    with pytest.raises(ToolViolation, match=missing):
        resolve_policy_citation(_context(_memo(pack, corpus, spam_citation), corpus, **args))
