# SPDX-License-Identifier: MIT
"""STEP-05 D1/D2: the hashed policy corpus, its anchors, and its digests.

Entirely offline. Every test here loads the committed corpus from ``policies/``
or builds one in memory; nothing reaches the network, which is the same posture
the suite takes towards the model adapter. The fetcher is exercised against
inline HTML fixtures rather than against Google.

Two properties carry the phase and are asserted in both directions, because a
digest checked in one direction only can be stable for the wrong reason:

* the same clauses always produce the same ``content_digest``;
* changing one word of one clause changes it.
"""

from datetime import datetime
from pathlib import Path

import pytest

from ts_sentry.data.policy_corpus import (
    CORPUS_MANIFEST,
    FAIR_USE_NOTICE,
    CorpusError,
    PolicyClause,
    PolicyCorpus,
    PolicyDocument,
    Retrieval,
    anchor_ids_for,
    load_corpus,
    slugify_heading,
    write_corpus,
)
from ts_sentry.data.policy_fetch import (
    CalloutTitle,
    ExtractedSection,
    FetchError,
    SectionFilter,
    SectionKind,
    extract_sections,
    extract_title,
    name_callouts,
)
from ts_sentry.data.policy_sources import CORPUS_VERSION, POLICY_SOURCES
from ts_sentry.data.tz import IST

POLICIES_DIR = Path(__file__).resolve().parent.parent / "policies"

_TS = datetime(2026, 8, 1, 12, 0, tzinfo=IST).isoformat()
_DIGEST = "a" * 64


def _clause(heading: str, text: str, ordinal: int, anchor: str | None = None) -> PolicyClause:
    return PolicyClause(
        anchor_id=anchor or slugify_heading(heading),
        heading=heading,
        text=text,
        ordinal=ordinal,
    )


def _document(
    doc_id: str = "doc-one", clauses: tuple[PolicyClause, ...] | None = None
) -> PolicyDocument:
    return PolicyDocument(
        doc_id=doc_id,
        title="A Policy",
        source_url="https://example.test/policy?hl=en",
        fetched_ts_ist=_TS,
        retrieval_sha256=_DIGEST,
        retrieval=Retrieval.FETCHED_VERIFIED,
        # `is None`, not `or`: an empty tuple is a case under test, and `or`
        # would quietly substitute the default for it.
        clauses=(_clause("First rule", "Do not do the thing.", 0),) if clauses is None else clauses,
    )


# ---------------------------------------------------------------------------
# The committed corpus
# ---------------------------------------------------------------------------


def test_the_committed_corpus_loads_and_verifies() -> None:
    """``load_corpus`` re-derives every digest, so this is not merely a read."""
    corpus = load_corpus(POLICIES_DIR)

    assert corpus.corpus_version == CORPUS_VERSION
    assert len(corpus.documents) == len(POLICY_SOURCES)
    assert {document.doc_id for document in corpus.documents} == {
        source.doc_id for source in POLICY_SOURCES
    }


def test_the_caseload_anchors_are_present() -> None:
    """Pins the clauses the threat model actually needs to cite.

    Not decoration. These anchors exist because the extractor was extended to
    anchor labelled list items; without that, a memo about a T-01 comment-spam
    ring could cite nothing narrower than a 486-word section. If a re-fetch ever
    loses them the corpus still loads and still verifies, and only this test
    says the product stopped working.
    """
    spam = load_corpus(POLICIES_DIR).document("youtube-spam")
    assert spam is not None

    anchors = {clause.anchor_id for clause in spam.clauses}
    for required in (
        "comment-spam",  # T-01
        "engagement-manipulation",  # T-02
        "off-platform-diversion",  # T-03
        "scams",  # T-03
        "automated-or-synthetic-mass-production",  # T-06
    ):
        assert required in anchors, f"{required} is missing from the spam corpus"


def test_the_impersonation_callout_is_its_own_clause() -> None:
    """The finding that changed extraction, pinned.

    The fake-engagement page opens with a tip callout carrying impersonation
    policy. Folded into the heading it visually sits under, the clause named
    ``fake-engagement-policy`` opened with a different rule, so a citation
    resolved perfectly to the wrong policy.
    """
    document = load_corpus(POLICIES_DIR).document("youtube-fake-engagement")
    assert document is not None

    lead = document.clause("fake-engagement-policy")
    callout = document.clause("unauthorized-impersonation")
    assert lead is not None and callout is not None

    assert "artificially increases the number of views" in lead.text
    assert "impersonation" not in lead.text.lower()
    assert "unauthorized impersonation" in callout.text.lower()


def test_no_whole_pages_are_committed() -> None:
    """The fair-use posture, asserted rather than described.

    ``policies/`` carries a manifest and one clause file per document. A raw
    page landing here would be redistribution the D2 standard does not cover,
    and it would arrive as an ordinary-looking new file.
    """
    expected = {CORPUS_MANIFEST} | {f"{source.doc_id}.json" for source in POLICY_SOURCES}

    assert {path.name for path in POLICIES_DIR.iterdir() if path.is_file()} == expected


def test_the_manifest_states_the_fair_use_posture_and_real_provenance() -> None:
    import json

    manifest = json.loads((POLICIES_DIR / CORPUS_MANIFEST).read_text(encoding="utf-8"))

    assert manifest["fair_use"] == FAIR_USE_NOTICE
    for record in manifest["documents"]:
        # Every document in corpus v1 was fetched and verified this session. A
        # future operator-supplied document is legal here; what is not legal is
        # a record that does not say which it was.
        assert record["retrieval"] == Retrieval.FETCHED_VERIFIED.value
        assert record["retrieval_sha256"] != record["content_digest"]


def test_recorded_titles_are_the_ones_the_pages_actually_carry() -> None:
    """STEP-05 D2 names 'Spam, Deceptive Practices & Scams'; the page is now
    titled 'Spam Policy'. The corpus records what was fetched, and the
    divergence is a recorded deviation rather than a silent correction."""
    corpus = load_corpus(POLICIES_DIR)
    spam = corpus.document("youtube-spam")
    assert spam is not None

    assert spam.title == "Spam Policy - YouTube Help"
    assert "Deceptive Practices" not in spam.title


# ---------------------------------------------------------------------------
# content_digest: both directions
# ---------------------------------------------------------------------------


def test_identical_clauses_produce_an_identical_content_digest() -> None:
    assert _document().content_digest == _document().content_digest


def test_changing_one_word_of_one_clause_changes_the_content_digest() -> None:
    original = _document()
    edited = _document(clauses=(_clause("First rule", "Do not do the thingg.", 0),))

    assert edited.content_digest != original.content_digest


def test_the_retrieval_digest_is_outside_the_content_digest() -> None:
    """The decision the CSP-nonce finding forced.

    Two fetches of one page return different bytes because Google regenerates a
    per-request nonce, so a content digest covering the raw response would move
    without the policy moving. Verified against the live pages during D2: the
    same clauses, fetched twice, produced identical content digests and
    different retrieval digests.
    """
    clauses = (_clause("First rule", "Do not do the thing.", 0),)
    first = _document(clauses=clauses)
    second = PolicyDocument(
        doc_id=first.doc_id,
        title=first.title,
        source_url=first.source_url,
        fetched_ts_ist=_TS,
        retrieval_sha256="b" * 64,  # a different fetch
        retrieval=Retrieval.OPERATOR_SUPPLIED,  # and a different provenance
        clauses=clauses,
    )

    assert second.content_digest == first.content_digest


def test_corpus_version_is_outside_the_corpus_digest() -> None:
    """Otherwise "bump the version when the digest changes" is circular."""
    documents = (_document(),)

    assert (
        PolicyCorpus(corpus_version="1.0.0", documents=documents).corpus_sha256
        == PolicyCorpus(corpus_version="9.4.2", documents=documents).corpus_sha256
    )


def test_the_corpus_digest_changes_when_any_document_does() -> None:
    before = PolicyCorpus(corpus_version="1.0.0", documents=(_document(),))
    after = PolicyCorpus(
        corpus_version="1.0.0",
        documents=(_document(clauses=(_clause("First rule", "Something else.", 0),)),),
    )

    assert after.corpus_sha256 != before.corpus_sha256


# ---------------------------------------------------------------------------
# Anchors
# ---------------------------------------------------------------------------


def test_anchors_derive_from_headings_not_from_position() -> None:
    """The property ARCHITECTURE 6.2 needs: inserting a clause must not
    renumber the citations below it."""
    before = anchor_ids_for(["Alpha rule", "Beta rule", "Gamma rule"])
    after = anchor_ids_for(["Alpha rule", "Inserted rule", "Beta rule", "Gamma rule"])

    assert before == ("alpha-rule", "beta-rule", "gamma-rule")
    assert set(before) <= set(after)


def test_repeated_headings_are_disambiguated_by_occurrence() -> None:
    assert anchor_ids_for(["Examples", "Other", "Examples"]) == (
        "examples",
        "other",
        "examples-2",
    )


def test_the_residual_anchor_limit_is_asserted_rather_than_described() -> None:
    """Stated at its true width, in the shape STEP-02 used for tail truncation.

    Inserting a *duplicate* heading above an existing one does move the existing
    one's anchor. No derivation from heading text alone avoids that, so it is
    pinned as a passing test: the day someone changes the scheme, this fails and
    forces the claim to be rewritten rather than quietly outliving its truth.
    """
    before = anchor_ids_for(["Other", "Examples"])
    after = anchor_ids_for(["Other", "Examples", "Examples"])

    assert before[1] == "examples"
    assert after[1] == "examples"
    assert after[2] == "examples-2"

    moved = anchor_ids_for(["Examples", "Other", "Examples"])
    assert moved[2] == "examples-2"  # the later one, not the original, shifts


def test_an_unanchorable_heading_is_refused() -> None:
    with pytest.raises(ValueError, match="no anchorable characters"):
        slugify_heading("   ---   ")


def test_a_document_cannot_carry_a_hand_written_anchor() -> None:
    """Anchors are derived. One typed in by hand is one nobody can recompute,
    and a citation would then resolve against a name with no rule behind it."""
    with pytest.raises(ValueError, match="do not match the derivation"):
        _document(clauses=(_clause("First rule", "Text.", 0, anchor="something-else"),))


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------


def test_an_edited_clause_file_fails_to_load(tmp_path: Path) -> None:
    """The mismatch Saif asked to be detectable.

    A clause file edited after the manifest was written no longer digests to
    what the manifest records, and the corpus refuses to load rather than
    resolving citations against text nobody described.
    """
    import json

    write_corpus(PolicyCorpus(corpus_version="1.0.0", documents=(_document(),)), tmp_path)

    clause_file = tmp_path / "doc-one.json"
    body = json.loads(clause_file.read_text(encoding="utf-8"))
    body["clauses"][0]["text"] = "Do whatever you like."
    clause_file.write_text(json.dumps(body, indent=2), encoding="utf-8", newline="\n")

    with pytest.raises(CorpusError, match="does not match its manifest entry"):
        load_corpus(tmp_path)


def test_an_edited_corpus_digest_fails_to_load(tmp_path: Path) -> None:
    import json

    write_corpus(PolicyCorpus(corpus_version="1.0.0", documents=(_document(),)), tmp_path)

    manifest_path = tmp_path / CORPUS_MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["corpus_sha256"] = "c" * 64
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")

    with pytest.raises(CorpusError, match="corpus digest mismatch"):
        load_corpus(tmp_path)


def test_a_missing_corpus_is_an_error_not_an_empty_one(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="no policy corpus manifest"):
        load_corpus(tmp_path)


def test_a_corpus_round_trips_through_disk(tmp_path: Path) -> None:
    original = PolicyCorpus(corpus_version="2.1.0", documents=(_document(),))
    write_corpus(original, tmp_path)

    reloaded = load_corpus(tmp_path)

    assert reloaded.corpus_sha256 == original.corpus_sha256
    assert reloaded.corpus_version == "2.1.0"


# ---------------------------------------------------------------------------
# Model invariants
# ---------------------------------------------------------------------------


def test_retrieval_has_no_default() -> None:
    """Following ``ReviewOutcome.reviewer_kind``: a record that does not say how
    it was obtained is unconstructible, not merely discouraged."""
    with pytest.raises(TypeError):
        PolicyDocument(  # type: ignore[call-arg]
            doc_id="doc-one",
            title="A Policy",
            source_url="https://example.test/policy",
            fetched_ts_ist=_TS,
            retrieval_sha256=_DIGEST,
            clauses=(_clause("First rule", "Text.", 0),),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_url", "http://example.test/x", "https URL"),
        ("retrieval_sha256", "not-a-digest", "retrieval_sha256"),
        ("fetched_ts_ist", "2026-08-01T12:00:00+00:00", "Asia/Kolkata"),
        ("title", "   ", "carries no title"),
        ("doc_id", "Doc_One", "lowercase hyphenated slug"),
    ],
)
def test_documents_validate_their_own_fields(field: str, value: str, message: str) -> None:
    kwargs: dict[str, object] = {
        "doc_id": "doc-one",
        "title": "A Policy",
        "source_url": "https://example.test/policy",
        "fetched_ts_ist": _TS,
        "retrieval_sha256": _DIGEST,
        "retrieval": Retrieval.FETCHED_VERIFIED,
        "clauses": (_clause("First rule", "Text.", 0),),
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=message):
        PolicyDocument(**kwargs)  # type: ignore[arg-type]


def test_a_document_with_no_clauses_is_refused() -> None:
    with pytest.raises(ValueError, match="no clauses"):
        _document(clauses=())


def test_a_clause_with_no_text_is_refused() -> None:
    """An anchor resolving to nothing would let a citation pass while quoting
    nothing at all."""
    with pytest.raises(ValueError, match="carries no text"):
        PolicyClause(anchor_id="rule", heading="Rule", text="   ", ordinal=0)


def test_duplicate_doc_ids_are_refused() -> None:
    with pytest.raises(ValueError, match="duplicate doc_id"):
        PolicyCorpus(corpus_version="1.0.0", documents=(_document(), _document()))


def test_lookup_by_content_digest_is_what_a_citation_uses() -> None:
    corpus = load_corpus(POLICIES_DIR)
    document = corpus.documents[0]

    assert corpus.document_by_content_digest(document.content_digest) is document
    assert corpus.document_by_content_digest("f" * 64) is None


def test_a_phantom_anchor_resolves_to_none_rather_than_raising() -> None:
    """Returned, not raised: a phantom anchor is a governed finding the D5
    resolver reports with a reason code, not an exception to catch."""
    document = load_corpus(POLICIES_DIR).documents[0]

    assert document.clause("no-such-anchor") is None


# ---------------------------------------------------------------------------
# Extraction, against inline fixtures rather than the network
# ---------------------------------------------------------------------------

_FIXTURE = """
<html><head><title>Fixture Policy - Help</title></head><body>
<nav>chrome that precedes the article</nav>
<h1>Fixture Policy</h1>
<p>We do not allow the thing.</p>
<div class="tip"><div class="no-margin">A different policy lives in this box.</div></div>
<h2>What this means</h2>
<p>Some framing prose.</p>
<ol>
  <li><strong>Comment spam:</strong> Posting the same comment everywhere.
    <ul><li><strong>Example:</strong> A hundred identical replies.</li></ul>
  </li>
  <li><strong>Scams:</strong> Promising money that does not exist.</li>
  <li>An unlabelled bullet that is really just prose.</li>
</ol>
<script>var nonce = "changes-every-request";</script>
</body></html>
"""


def test_extraction_splits_headings_labelled_items_and_callouts() -> None:
    sections = extract_sections(_FIXTURE)
    by_heading = {section.heading: section for section in sections}

    assert extract_title(_FIXTURE) == "Fixture Policy - Help"
    assert by_heading["Comment spam"].kind is SectionKind.LABELLED_ITEM
    assert "Posting the same comment everywhere" in by_heading["Comment spam"].text
    # The nested Example folds into its parent rather than becoming an anchor.
    assert "A hundred identical replies" in by_heading["Comment spam"].text
    assert "Example" not in by_heading
    # An unlabelled bullet stays with its section's prose.
    assert "unlabelled bullet" in by_heading["What this means"].text
    # Script contents never reach a clause.
    assert not any("changes-every-request" in section.text for section in sections)


def test_extraction_is_deterministic() -> None:
    assert extract_sections(_FIXTURE) == extract_sections(_FIXTURE)


def test_a_callout_becomes_its_own_unnamed_section() -> None:
    callouts = [
        section for section in extract_sections(_FIXTURE) if section.kind is SectionKind.CALLOUT
    ]

    assert len(callouts) == 1
    assert callouts[0].heading == ""
    assert callouts[0].text == "A different policy lives in this box."
    # And it is not folded into the heading it visually sits under.
    lead = next(s for s in extract_sections(_FIXTURE) if s.heading == "Fixture Policy")
    assert "different policy" not in lead.text


def test_an_unnamed_callout_is_refused() -> None:
    """Fail-closed. The alternative is inventing a heading or dropping policy
    text, and both are worse than stopping."""
    sections = extract_sections(_FIXTURE)

    with pytest.raises(FetchError, match="unnamed callout"):
        name_callouts(sections, ())


def test_a_callout_title_matching_nothing_is_refused() -> None:
    sections = extract_sections(_FIXTURE)
    titles = (
        CalloutTitle(match="A different policy", heading="Other policy"),
        CalloutTitle(match="text that is not present", heading="Ghost"),
    )

    with pytest.raises(FetchError, match="matched 0 callouts"):
        name_callouts(sections, titles)


def test_a_callout_title_matching_two_callouts_is_refused() -> None:
    sections = (
        ExtractedSection(heading="", text="shared phrase one", kind=SectionKind.CALLOUT),
        ExtractedSection(heading="", text="shared phrase two", kind=SectionKind.CALLOUT),
    )

    with pytest.raises(FetchError, match="matched 2 callouts"):
        name_callouts(sections, (CalloutTitle(match="shared phrase", heading="Shared"),))


def test_naming_a_callout_leaves_other_sections_untouched() -> None:
    sections = extract_sections(_FIXTURE)
    named = name_callouts(
        sections, (CalloutTitle(match="A different policy", heading="Other policy"),)
    )

    assert len(named) == len(sections)
    assert all(section.heading.strip() for section in named)
    assert any(
        section.heading == "Other policy" and section.kind is SectionKind.CALLOUT
        for section in named
    )


def test_the_section_filter_drops_boilerplate_and_keeps_policy() -> None:
    section_filter = SectionFilter(drop_headings=frozenset({"What this means"}))
    sections = extract_sections(_FIXTURE)

    kept = [section for section in sections if section_filter.keep(section)]

    assert "What this means" not in {section.heading for section in kept}
    assert "Comment spam" in {section.heading for section in kept}
