# SPDX-License-Identifier: MIT
"""D7: the overclaim fixture suite (STEP-05 D7, 3.5).

The negative-path proof. STEP-05 D7 asks for "memos with planted unsupported
claims, phantom citations, excerpt overruns", and the standard it names is that
"the gate must be seen failing correctly".

**Correctly** is the load-bearing word, and it is why every fixture below
asserts a *reason code* rather than merely that something failed. A suite that
only checked "this memo was rejected" would pass just as well if the gate
rejected everything for the wrong reason, or for no reason it could name, and a
governance layer whose refusals cannot be counted by cause is one whose metrics
mean nothing. The reason codes are what make ``GATE_REJECTION`` counts
informative.

Each fixture plants exactly one defect against an otherwise valid memo, so a
failure names the defect rather than the fixture being generally malformed.
"""

from datetime import datetime
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ts_sentry.agents.evidence.pack import EvidencePack
from ts_sentry.agents.memo.memo import (
    MAX_EXCERPT_WORDS,
    MIN_EXCERPT_WORDS,
    AutomatedDecision,
    AutomatedMeans,
    Measure,
    Memo,
    MemoSentence,
    PolicyCitation,
    SentenceRole,
)
from ts_sentry.data.enums import EntityKind
from ts_sentry.data.policy_corpus import PolicyCorpus, load_corpus
from ts_sentry.data.tz import IST
from ts_sentry.governance.gates import FailureCode
from ts_sentry.orchestrator.citation_resolver import CitationFailure
from ts_sentry.orchestrator.memo_gate import memo_check

POLICIES_DIR = Path(__file__).resolve().parent.parent / "policies"
_TS = datetime(2026, 8, 1, 12, 0, tzinfo=IST).isoformat()


@pytest.fixture(scope="module")
def corpus() -> PolicyCorpus:
    return load_corpus(POLICIES_DIR)


@pytest.fixture(scope="module")
def pack() -> EvidencePack:
    return EvidencePack.seed("case-0001", "t02_chan_000_000", EntityKind.CHANNEL, _TS)


@pytest.fixture(scope="module")
def spam_digest(corpus: PolicyCorpus) -> str:
    document = corpus.document("youtube-spam")
    assert document is not None
    return document.content_digest


@pytest.fixture(scope="module")
def clause_words(corpus: PolicyCorpus) -> list[str]:
    document = corpus.document("youtube-spam")
    assert document is not None
    clause = document.clause("comment-spam")
    assert clause is not None
    return clause.text.split()


def _citation(digest: str, words: list[str], count: int = 6) -> PolicyCitation:
    return PolicyCitation(
        content_digest=digest, anchor_id="comment-spam", excerpt=" ".join(words[:count])
    )


def _memo(
    pack: EvidencePack,
    corpus: PolicyCorpus,
    *,
    sentences: tuple[MemoSentence, ...],
    pack_digest: str | None = None,
    corpus_sha256: str | None = None,
) -> Memo:
    return Memo(
        memo_id="memo-fixture",
        case_id=pack.case_id,
        subject_id=pack.subject_id,
        pack_digest=pack_digest or pack.content_digest,
        corpus_version=corpus.corpus_version,
        corpus_sha256=corpus_sha256 or corpus.corpus_sha256,
        sentences=sentences,
        measure=Measure.CONTENT_DEMOTED,
        automated_means=AutomatedMeans(
            detection_automated=True,
            decision=AutomatedDecision.PARTIALLY_AUTOMATED,
            drafted_by="fixture",
        ),
    )


def _sentences(
    pack: EvidencePack,
    citation: PolicyCitation,
    *,
    fact_id: str | None = None,
) -> tuple[MemoSentence, ...]:
    evidence = fact_id or pack.provenance[0].provenance_id
    return (
        MemoSentence.from_text(0, SentenceRole.FACT, f"The seed entered here [{evidence}]."),
        MemoSentence(
            index=1,
            role=SentenceRole.POLICY_GROUND,
            text="Incompatible with the spam policy.",
            citation=citation,
        ),
        MemoSentence(index=2, role=SentenceRole.MEASURE, text="Demotion is proposed."),
        MemoSentence(index=3, role=SentenceRole.REDRESS, text="Appeal is available."),
    )


def _bypass_citation(digest: str, anchor: str, excerpt: str) -> PolicyCitation:
    """Build a citation the constructor would refuse.

    Needed for the excerpt-bound fixtures: the type makes an over-long or
    too-short excerpt unconstructible, so the only way to see the *gate* report
    one is to hand it a citation built by a route that skipped ``__post_init__``.
    That is exactly the case those duplicated checks exist for.
    """
    forged = object.__new__(PolicyCitation)
    for field, value in (
        ("content_digest", digest),
        ("anchor_id", anchor),
        ("excerpt", excerpt),
    ):
        object.__setattr__(forged, field, value)
    return forged


# ---------------------------------------------------------------------------
# The control: an undefective memo passes
# ---------------------------------------------------------------------------


def test_the_undefected_fixture_passes(
    pack: EvidencePack, corpus: PolicyCorpus, spam_digest: str, clause_words: list[str]
) -> None:
    """The control every other fixture is a one-defect mutation of.

    Without it, a fixture that failed for an unrelated reason would look like a
    caught defect, and the suite would report the gate working when it was only
    rejecting a malformed baseline.
    """
    memo = _memo(pack, corpus, sentences=_sentences(pack, _citation(spam_digest, clause_words)))

    assert memo_check(memo, pack, corpus) == ()


# ---------------------------------------------------------------------------
# Planted defects, each asserted to its own reason code
# ---------------------------------------------------------------------------


def test_an_unsupported_claim_is_caught_as_an_unverified_claim(
    pack: EvidencePack, corpus: PolicyCorpus, spam_digest: str, clause_words: list[str]
) -> None:
    memo = _memo(
        pack,
        corpus,
        sentences=_sentences(pack, _citation(spam_digest, clause_words), fact_id="prov-9999"),
    )

    failures = memo_check(memo, pack, corpus)

    assert len(failures) == 1
    assert failures[0].code is FailureCode.UNVERIFIED_CLAIM
    assert "unresolvable_evidence_id" in failures[0].detail
    assert "sentence 0" in failures[0].detail


def test_a_phantom_anchor_is_caught_with_its_own_code(
    pack: EvidencePack, corpus: PolicyCorpus, spam_digest: str, clause_words: list[str]
) -> None:
    citation = PolicyCitation(
        content_digest=spam_digest,
        anchor_id="coordinated-inauthentic-behaviour",
        excerpt=" ".join(clause_words[:6]),
    )

    failures = memo_check(_memo(pack, corpus, sentences=_sentences(pack, citation)), pack, corpus)

    assert len(failures) == 1
    assert CitationFailure.PHANTOM_ANCHOR.value in failures[0].detail


def test_an_unknown_document_is_caught_with_its_own_code(
    pack: EvidencePack, corpus: PolicyCorpus, clause_words: list[str]
) -> None:
    citation = PolicyCitation(
        content_digest="f" * 64, anchor_id="comment-spam", excerpt=" ".join(clause_words[:6])
    )

    failures = memo_check(_memo(pack, corpus, sentences=_sentences(pack, citation)), pack, corpus)

    assert len(failures) == 1
    assert CitationFailure.UNKNOWN_DOCUMENT.value in failures[0].detail


def test_a_fabricated_quotation_is_caught_with_its_own_code(
    pack: EvidencePack, corpus: PolicyCorpus, spam_digest: str
) -> None:
    """Real document, real anchor, words the clause does not contain.

    The failure this phase most has to catch, because everything about it checks
    out except the part a reader would rely on.
    """
    citation = PolicyCitation(
        content_digest=spam_digest,
        anchor_id="comment-spam",
        excerpt="comment spam warrants immediate channel termination",
    )

    failures = memo_check(_memo(pack, corpus, sentences=_sentences(pack, citation)), pack, corpus)

    assert len(failures) == 1
    assert CitationFailure.EXCERPT_NOT_IN_CLAUSE.value in failures[0].detail


def test_an_excerpt_overrun_is_caught_with_its_own_code(
    pack: EvidencePack, corpus: PolicyCorpus, spam_digest: str, clause_words: list[str]
) -> None:
    """D7 names this defect explicitly. The type refuses it, so the gate is seen
    reporting it against a citation built by bypassing the constructor."""
    citation = _bypass_citation(
        spam_digest, "comment-spam", " ".join(clause_words[: MAX_EXCERPT_WORDS + 4])
    )

    failures = memo_check(_memo(pack, corpus, sentences=_sentences(pack, citation)), pack, corpus)

    assert len(failures) == 1
    assert CitationFailure.EXCERPT_TOO_LONG.value in failures[0].detail


def test_a_degenerate_excerpt_is_caught_with_its_own_code(
    pack: EvidencePack, corpus: PolicyCorpus, spam_digest: str
) -> None:
    """The HALT-2 finding, as a fixture. A one-word quotation is true and
    identifies no rule."""
    citation = _bypass_citation(spam_digest, "comment-spam", "spam")

    failures = memo_check(_memo(pack, corpus, sentences=_sentences(pack, citation)), pack, corpus)

    assert len(failures) == 1
    assert CitationFailure.EXCERPT_TOO_SHORT.value in failures[0].detail


def test_a_mid_word_quotation_is_caught(
    pack: EvidencePack, corpus: PolicyCorpus, spam_digest: str, clause_words: list[str]
) -> None:
    citation = PolicyCitation(
        content_digest=spam_digest,
        anchor_id="comment-spam",
        excerpt=" ".join(clause_words[:6])[1:],  # drops the leading character
    )

    failures = memo_check(_memo(pack, corpus, sentences=_sentences(pack, citation)), pack, corpus)

    assert len(failures) == 1
    assert CitationFailure.EXCERPT_NOT_IN_CLAUSE.value in failures[0].detail


def test_a_memo_bound_to_the_wrong_pack_is_caught(
    pack: EvidencePack, corpus: PolicyCorpus, spam_digest: str, clause_words: list[str]
) -> None:
    memo = _memo(
        pack,
        corpus,
        sentences=_sentences(pack, _citation(spam_digest, clause_words)),
        pack_digest="b" * 64,
    )

    failures = memo_check(memo, pack, corpus)

    assert len(failures) == 1
    assert failures[0].code is FailureCode.SCHEMA_INVALID
    assert "different evidence" in failures[0].detail


def test_a_memo_bound_to_the_wrong_corpus_is_caught(
    pack: EvidencePack, corpus: PolicyCorpus, spam_digest: str, clause_words: list[str]
) -> None:
    memo = _memo(
        pack,
        corpus,
        sentences=_sentences(pack, _citation(spam_digest, clause_words)),
        corpus_sha256="c" * 64,
    )

    failures = memo_check(memo, pack, corpus)

    assert len(failures) == 1
    assert "different corpus" in failures[0].detail


def test_every_planted_defect_is_reported_once_not_swallowed(
    pack: EvidencePack, corpus: PolicyCorpus, spam_digest: str
) -> None:
    """Two defects in one memo produce two findings.

    A gate that stopped at the first would spend a revise step per defect, and
    would report a memo with three problems as a memo with one.
    """
    citation = PolicyCitation(
        content_digest=spam_digest,
        anchor_id="comment-spam",
        excerpt="words that this clause does not contain",
    )
    memo = _memo(pack, corpus, sentences=_sentences(pack, citation, fact_id="prov-9999"))

    failures = memo_check(memo, pack, corpus)

    assert len(failures) == 2
    assert {"sentence 0" in f.detail for f in failures} == {True, False}


# ---------------------------------------------------------------------------
# STEP-05 3.5: the verifier-soundness property
# ---------------------------------------------------------------------------

_ID_ALPHABET = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=12)


@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    cited=st.lists(_ID_ALPHABET, min_size=1, max_size=6, unique=True),
    extra_real=st.lists(_ID_ALPHABET, min_size=0, max_size=6, unique=True),
)
def test_a_memo_that_passes_cites_nothing_absent_from_the_pack(
    cited: list[str],
    extra_real: list[str],
    pack: EvidencePack,
    corpus: PolicyCorpus,
    spam_digest: str,
    clause_words: list[str],
) -> None:
    """STEP-05 3.5, stated as the specification states it.

    "A memo that passes verification contains no sentence whose evidence ids are
    absent from the pack." Generated over arbitrary id sets rather than over a
    handful of chosen ones, because the interesting failures are the ones nobody
    thought to write a fixture for.

    The property is checked in the direction that matters. It does not assert
    that every unsupported memo fails, which would be a claim about coverage; it
    asserts that a memo the gate *accepted* has no unresolvable id, which is the
    soundness claim the RECOMMEND gate makes about itself.
    """
    resolvable = pack.record_ids
    sentence = MemoSentence(
        index=0,
        role=SentenceRole.FACT,
        text="A generated claim.",
        evidence_ids=frozenset(cited),
    )
    rest = _sentences(pack, _citation(spam_digest, clause_words))[1:]
    memo = _memo(
        pack,
        corpus,
        sentences=(sentence, *rest),
    )

    failures = memo_check(memo, pack, corpus)

    if not failures:
        for check_sentence in memo.sentences:
            assert check_sentence.evidence_ids <= resolvable, (
                f"sentence {check_sentence.index} passed while citing "
                f"{sorted(check_sentence.evidence_ids - resolvable)}"
            )


@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(word_count=st.integers(min_value=0, max_value=MAX_EXCERPT_WORDS + 6))
def test_only_excerpts_within_bounds_and_actually_present_resolve(
    word_count: int,
    pack: EvidencePack,
    corpus: PolicyCorpus,
    spam_digest: str,
    clause_words: list[str],
) -> None:
    """The excerpt contract, over every length the bounds permit and a few they
    do not."""
    citation = _bypass_citation(spam_digest, "comment-spam", " ".join(clause_words[:word_count]))
    memo = _memo(pack, corpus, sentences=_sentences(pack, citation))

    failures = memo_check(memo, pack, corpus)

    if not failures:
        assert MIN_EXCERPT_WORDS <= word_count <= MAX_EXCERPT_WORDS
