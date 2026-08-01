# SPDX-License-Identifier: MIT
"""STEP-05 D6: the signature path and the AI-DRAFT watermark.

Two claims are load-bearing here and both are asserted rather than described:

* **No parameter removes the watermark.** Rendering is called with every value
  its signature parameter can take, and the label is absent only when a real
  signature covers the memo.
* **The digest survives finalizing.** ``content_digest`` excludes ``status``, so
  the digest signed and the digest the signed memo carries are the same value.
  Without that the artifact would fail its own verification the instant it was
  produced, which is the kind of defect that looks like tampering.
"""

import inspect
from datetime import datetime
from pathlib import Path

import pytest

from ts_sentry.agents.evidence.pack import EvidencePack
from ts_sentry.agents.memo.memo import (
    AutomatedDecision,
    AutomatedMeans,
    Measure,
    Memo,
    MemoSentence,
    MemoStatus,
    PolicyCitation,
    SentenceRole,
)
from ts_sentry.data.enums import EntityKind
from ts_sentry.data.policy_corpus import PolicyCorpus, load_corpus
from ts_sentry.data.tz import IST
from ts_sentry.governance.gates import FailureCode, GateFailure
from ts_sentry.governance.signature import Decision
from ts_sentry.orchestrator.memo_export import (
    WATERMARK,
    render_html,
    render_markdown,
    write_memo_html,
    write_memo_markdown,
)
from ts_sentry.orchestrator.memo_gate import memo_check
from ts_sentry.orchestrator.signing import (
    SignedMemo,
    SigningRefused,
    sign_memo,
    verify_signed_memo,
)

POLICIES_DIR = Path(__file__).resolve().parent.parent / "policies"
_START = datetime(2026, 8, 1, 12, 0, tzinfo=IST)
_TS = _START.isoformat()


@pytest.fixture(scope="module")
def corpus() -> PolicyCorpus:
    return load_corpus(POLICIES_DIR)


@pytest.fixture
def pack() -> EvidencePack:
    return EvidencePack.seed("case-0001", "t02_chan_000_000", EntityKind.CHANNEL, _TS)


@pytest.fixture
def memo(pack: EvidencePack, corpus: PolicyCorpus) -> Memo:
    document = corpus.document("youtube-spam")
    assert document is not None
    clause = document.clause("comment-spam")
    assert clause is not None
    citation = PolicyCitation(
        content_digest=document.content_digest,
        anchor_id="comment-spam",
        excerpt=" ".join(clause.text.split()[:6]),
    )
    seed = pack.provenance[0].provenance_id
    return Memo(
        memo_id="memo-0001",
        case_id=pack.case_id,
        subject_id=pack.subject_id,
        pack_digest=pack.content_digest,
        corpus_version=corpus.corpus_version,
        corpus_sha256=corpus.corpus_sha256,
        sentences=(
            MemoSentence.from_text(0, SentenceRole.FACT, f"The seed entered here [{seed}]."),
            MemoSentence(
                index=1,
                role=SentenceRole.POLICY_GROUND,
                text="Incompatible with the spam policy.",
                citation=citation,
            ),
            MemoSentence(index=2, role=SentenceRole.MEASURE, text="Demotion is proposed."),
            MemoSentence(index=3, role=SentenceRole.REDRESS, text="Appeal is available."),
        ),
        measure=Measure.CONTENT_DEMOTED,
        automated_means=AutomatedMeans(
            detection_automated=True,
            decision=AutomatedDecision.PARTIALLY_AUTOMATED,
            drafted_by="stub/faithful",
        ),
    )


def _signed(memo: Memo) -> SignedMemo:
    return sign_memo(
        memo,
        analyst_id="saif",
        decision=Decision.APPROVE_ENFORCEMENT,
        signed_ts=_START,
        gate_failures=(),
    )


# ---------------------------------------------------------------------------
# The digest property everything else rests on
# ---------------------------------------------------------------------------


def test_finalizing_does_not_change_the_digest(memo: Memo) -> None:
    """``content_digest`` excludes ``status`` for exactly this reason.

    If the DRAFT to SIGNED flip changed the digest, the signature would be over
    a value the signed memo no longer has, and the artifact would fail its own
    verification the moment it was created.
    """
    assert memo.finalized().content_digest == memo.content_digest


def test_a_signed_memo_verifies_against_its_own_signature(memo: Memo) -> None:
    signed = _signed(memo)

    assert verify_signed_memo(signed)
    assert signed.memo.status is MemoStatus.SIGNED
    assert signed.signature.subject_hash == signed.memo.content_digest


def test_an_edited_memo_no_longer_matches_its_signature(memo: Memo) -> None:
    """The property ``HumanSignature.subject_hash`` documents, made true for
    memos: a signature cannot silently carry over to edited text."""
    signed = _signed(memo)
    edited = signed.memo.with_sentences(
        (
            *signed.memo.sentences[:2],
            MemoSentence(index=2, role=SentenceRole.MEASURE, text="Termination is proposed."),
            signed.memo.sentences[3],
        )
    )

    assert edited.content_digest != signed.signature.subject_hash
    with pytest.raises(SigningRefused, match="edited after it was signed"):
        SignedMemo(memo=edited.finalized(), signature=signed.signature)


def test_revising_a_signed_memo_returns_it_to_draft(memo: Memo) -> None:
    signed = _signed(memo)

    revised = signed.memo.with_sentences(signed.memo.sentences)

    assert revised.status is MemoStatus.DRAFT


# ---------------------------------------------------------------------------
# What may be signed
# ---------------------------------------------------------------------------


def test_an_unverified_memo_cannot_be_signed(memo: Memo) -> None:
    """``gate_failures`` is the gate's verdict, passed in rather than
    recomputed. Signing a memo the gate rejected requires a caller to have run
    the gate and thrown the answer away."""
    failures = (GateFailure(code=FailureCode.UNVERIFIED_CLAIM, detail="sentence 0: nope"),)

    with pytest.raises(SigningRefused, match="did not pass the RECOMMEND gate"):
        sign_memo(
            memo,
            analyst_id="saif",
            decision=Decision.APPROVE_ENFORCEMENT,
            signed_ts=_START,
            gate_failures=failures,
        )


def test_a_memo_cannot_be_signed_twice(memo: Memo) -> None:
    signed = _signed(memo)

    with pytest.raises(SigningRefused, match="already signed"):
        sign_memo(
            signed.memo,
            analyst_id="saif",
            decision=Decision.APPROVE_ENFORCEMENT,
            signed_ts=_START,
            gate_failures=(),
        )


@pytest.mark.parametrize("decision", [Decision.REJECT, Decision.DEFER])
def test_only_an_approval_finalizes_a_memo(memo: Memo, decision: Decision) -> None:
    """A rejection or deferral is a real governance event and is signable as
    one; it just does not produce a finalized memo."""
    with pytest.raises(SigningRefused, match="finalized only by an approval"):
        sign_memo(
            memo,
            analyst_id="saif",
            decision=decision,
            signed_ts=_START,
            gate_failures=(),
        )


def test_a_signed_memo_is_refused_by_the_recommend_gate(
    memo: Memo, pack: EvidencePack, corpus: PolicyCorpus
) -> None:
    """HALT-2 review, finding 4, implemented here as recorded.

    Re-gating a signed memo answers the wrong question, and accepting one would
    let it be re-laundered through the agent path and emerge with a fresh
    VERIFICATION_PASS that says nothing about the signature.
    """
    assert memo_check(memo, pack, corpus) == ()  # the draft passes

    failures = memo_check(_signed(memo).memo, pack, corpus)

    assert len(failures) == 1
    assert failures[0].code is FailureCode.SCHEMA_INVALID
    assert "already SIGNED" in failures[0].detail


# ---------------------------------------------------------------------------
# The watermark
# ---------------------------------------------------------------------------


def test_an_unsigned_memo_carries_the_watermark_in_both_formats(memo: Memo) -> None:
    assert WATERMARK in render_markdown(memo)
    assert WATERMARK in render_html(memo)


def test_the_watermark_appears_at_the_top_and_the_bottom(memo: Memo) -> None:
    """Persistent, per STEP-05 3.3. A label only at the top is a label that
    disappears the moment somebody scrolls or prints a second page."""
    assert render_markdown(memo).count(WATERMARK) == 2
    assert render_html(memo).count(WATERMARK) >= 2


def test_no_parameter_can_suppress_the_watermark(memo: Memo) -> None:
    """The claim the module makes about itself, checked against its signature.

    Rendering takes a memo and an optional signature and nothing else, so the
    only way to remove the label is to supply a signature that covers the memo.
    Asserted structurally as well as behaviourally: a future ``watermark=False``
    would fail this rather than quietly working.
    """
    for renderer in (render_markdown, render_html):
        parameters = list(inspect.signature(renderer).parameters)
        assert parameters == ["memo", "signed"], (
            f"{renderer.__name__} grew a parameter; the watermark must not become optional"
        )
        assert WATERMARK in renderer(memo)
        assert WATERMARK in renderer(memo, None)


def test_a_signature_over_a_different_memo_does_not_remove_the_watermark(
    memo: Memo,
) -> None:
    """A signature is checked, not trusted. Rendering a memo as finalized on the
    strength of someone else's signature is how one signed document
    authenticates another."""
    other = memo.with_sentences(
        (
            *memo.sentences[:2],
            MemoSentence(index=2, role=SentenceRole.MEASURE, text="Termination is proposed."),
            memo.sentences[3],
        )
    )
    foreign = _signed(other)

    rendered = render_markdown(memo, foreign)

    assert WATERMARK in rendered
    assert "does **not** cover this memo" in rendered
    assert "does not cover this memo" in render_html(memo, foreign)


def test_a_covering_signature_removes_the_watermark_and_says_who_signed(
    memo: Memo,
) -> None:
    signed = _signed(memo)

    markdown = render_markdown(signed.memo, signed)
    html_text = render_html(signed.memo, signed)

    assert WATERMARK not in markdown
    assert WATERMARK not in html_text
    assert "saif" in markdown
    assert signed.signature.signature_hash in markdown
    assert "SIGNED STATEMENT OF REASONS" in html_text


def test_the_export_states_the_automated_means_disclosure(memo: Memo) -> None:
    """Article 17(3)(c) reaches the reader, not just the JSON."""
    markdown = render_markdown(memo)

    assert "Art. 17(3)(c)" in markdown
    assert "partially_automated" in markdown
    assert "stub/faithful" in markdown


def test_html_escapes_memo_text(pack: EvidencePack, corpus: PolicyCorpus, memo: Memo) -> None:
    """The memo carries agent-authored prose and policy excerpts. An export that
    interpolated either unescaped would be a second route by which case-derived
    text reaches a renderer."""
    injected = memo.with_sentences(
        (
            memo.sentences[0],
            memo.sentences[1],
            MemoSentence(
                index=2,
                role=SentenceRole.MEASURE,
                text="<script>alert('x')</script> is proposed.",
            ),
            memo.sentences[3],
        )
    )

    rendered = render_html(injected)

    assert "<script>alert" not in rendered
    assert "&lt;script&gt;" in rendered


def test_writers_round_trip_to_disk(memo: Memo, tmp_path: Path) -> None:
    write_memo_markdown(memo, tmp_path / "memo.md")
    write_memo_html(memo, tmp_path / "memo.html")

    assert WATERMARK in (tmp_path / "memo.md").read_text(encoding="utf-8")
    assert WATERMARK in (tmp_path / "memo.html").read_text(encoding="utf-8")


def test_a_memo_survives_a_json_round_trip(memo: Memo) -> None:
    """The signing CLI reads a memo off disk, so the reader has to preserve the
    digest or a signature taken before the write would not verify after it."""
    reloaded = Memo.from_json_object(memo.to_json_object())

    assert reloaded.content_digest == memo.content_digest
    assert reloaded.to_json_object() == memo.to_json_object()
