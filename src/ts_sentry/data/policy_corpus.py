# SPDX-License-Identifier: MIT
"""D1: the hashed, anchored policy corpus (ARCHITECTURE 6.2, STEP-05 D1).

Citations in a memo have to resolve against something that cannot move under
them. ARCHITECTURE 6.2 states the requirement: policy documents are "fetched
once, hashed (SHA-256), versioned, and anchored (stable section IDs). Citations
resolve against anchors, so a policy update is an explicit, ledgered corpus
event, never a silent drift."

This module is the corpus. The fetcher that populates it is
:mod:`ts_sentry.data.policy_fetch`; the resolver that checks a citation against
it is orchestrator-side, for the reason STEP-03 recorded when the import-graph
test caught an agent holding its own verifier.

What is committed, and what is not
----------------------------------
**Clause-level text, not whole pages.** ``policies/`` carries one JSON file per
document holding the anchored clauses, plus a manifest. The raw fetched page is
hashed and its digest recorded, but the page itself is not committed.

That is a deliberate scoping decision, taken by Saif and recorded in
``docs/DECISIONS.md`` rather than left to be inferred from the file listing.
The clauses are the citation-resolution target, memos quote at most fifteen
words of one (STEP-05 D2), and this repository is public and MIT-licensed while
the clause text is third-party content quoted for citation resolution. Storing
three whole policy pages would redistribute far more than resolution needs.

Content identity and retrieval provenance, kept apart
-----------------------------------------------------
``PolicyDocument`` carries two digests and they answer different questions. The
line between them was drawn by a measurement, not by taste:

* ``content_digest`` is the **citation identity**. It covers what this
  repository committed, which is the document's identity fields and every
  clause, anchor and ordinal. A memo citation pins this.
* ``retrieval_sha256`` is **provenance for one retrieval**: the SHA-256 of the
  raw response body that particular fetch received. It is explicitly *not* an
  identity for the policy.

The obvious design was the other way round, hashing the fetched page and calling
that the document. It does not work, and STEP-05 found out by trying it. Two
fetches of the YouTube spam policy in the same process returned different
digests (``ada4c178...`` and ``159c932a...``), differing only in the CSP
``nonce`` attribute that Google regenerates per request and repeats throughout
the page. A raw-byte digest of a live page therefore changes on every fetch
whether or not one word of policy changed, so it can never answer "has this
policy changed" - the one question D1's standard wants it for.

What *is* stable is the policy text. In the same experiment all fourteen
substantive sections came back byte-identical; the only unstable extracted
section was the site's navigation carousel, which carries a per-request session
id and is dropped as boilerplate. So drift detection operates on extracted
clause content, and ``retrieval_sha256`` is kept beside it as an honest record
of what arrived rather than promoted into a role it cannot fill.

``retrieval_sha256`` and ``retrieval`` are both outside ``content_digest`` for
the same reason: they describe how the text got here, not what it says. Two
corpora with identical clauses have identical content digests even if one was
fetched and the other pasted, which is the correct answer for a *citation*
identity.

Anchor stability
----------------
An anchor is derived from its clause's **heading**, never from its position.
``ordinal`` is stored because document order is real information for rendering,
and it is deliberately *not* part of the anchor: if it were, inserting one
clause near the top would renumber every anchor below it and silently break
every memo that had already cited them, which is exactly the drift ARCHITECTURE
6.2 forbids.

Two clauses in one document can legitimately share a heading ("Examples"
appears more than once on some pages). Those are disambiguated by their
occurrence among *same-slug* clauses only, so a new clause with a new heading
never disturbs them. The residual limit is stated rather than papered over:
inserting a second clause titled "Examples" *above* an existing one does move
that one's anchor, and there is no derivation from heading text alone that
avoids it. A test pins both halves.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ts_sentry.data.tz import require_ist_iso
from ts_sentry.governance.canonical import digest_fields, require_sha256_hex

__all__ = [
    "CORPUS_MANIFEST",
    "FAIR_USE_NOTICE",
    "CorpusError",
    "PolicyClause",
    "PolicyCorpus",
    "PolicyDocument",
    "Retrieval",
    "anchor_ids_for",
    "load_corpus",
    "slugify_heading",
    "write_corpus",
]

CORPUS_MANIFEST = "manifest.json"

_DOCUMENT_DOMAIN = "ts-sentry/policy-document/v1"
_CORPUS_DOMAIN = "ts-sentry/policy-corpus/v1"
"""Domain separation, for the reason the ledger, signature and mandate-set
digests carry one: the same primitive over similarly shaped field lists must
not be able to collide across two meanings."""

FAIR_USE_NOTICE = (
    "Clause-level excerpts of third-party public policy documents, stored as the "
    "citation-resolution target for generated memos. Full pages are not "
    "redistributed; only the SHA-256 of each fetched response is recorded, as "
    "retrieval provenance. Memos quote at most 15 words of any clause. Rights in "
    "this text remain with its publisher."
)
"""Recorded in the manifest itself, not only in documentation.

A reader who opens ``policies/`` sees what the text is and why it is here,
without having to find ``docs/DECISIONS.md`` first.
"""

_SEMVER_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_ANCHOR_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
"""What an anchor may look like.

Constrained to a subset of ``agents.citations.CITATION_PATTERN``'s character
class so an anchor is always citable in the bracketed syntax the rest of the
system uses, and so it can never be confused with a digest.
"""


class CorpusError(Exception):
    """Raised when a corpus cannot be loaded, or does not match its manifest.

    Distinct from a gate rejection or a resolver failure. A resolver failure is
    a governed finding about a well-formed citation; this is the corpus itself
    being unusable, which is a build problem rather than an agent outcome.
    """


class Retrieval(StrEnum):
    """How a document's text actually arrived.

    Required by ``PolicyDocument`` with **no default**, following
    ``ReviewOutcome.reviewer_kind``: a record that does not say how it was
    obtained is unconstructible rather than merely discouraged.

    The reason is concrete. The first attempt to retrieve DSA Article 17 during
    STEP-05 planning returned only the regulation's preamble, and a manifest
    that recorded that as a clean fetch would have been asserting something
    false about its own provenance. A corpus whose whole purpose is to make
    citation trustworthy cannot be vague about where its text came from.
    """

    FETCHED_VERIFIED = "fetched_verified"
    """The fetcher retrieved the complete page and these clauses were extracted
    from exactly those bytes."""

    OPERATOR_SUPPLIED = "operator_supplied"
    """The fetch was incomplete or unavailable and a human supplied the verbatim
    text. ``retrieval_sha256`` then digests what the operator supplied, and this
    value is what says so."""


def slugify_heading(heading: str) -> str:
    """The anchor stem for a heading.

    Lowercase ASCII alphanumerics, everything else collapsed to a single
    hyphen. Deterministic and lossy on purpose: two headings differing only in
    punctuation collide, and the occurrence disambiguator in ``anchor_ids_for``
    is what keeps the result unique within a document.
    """
    slug = _SLUG_STRIP.sub("-", heading.strip().lower()).strip("-")
    if not slug:
        raise ValueError(
            f"heading {heading!r} has no anchorable characters; an anchor must be citable"
        )
    return slug


def anchor_ids_for(headings: Sequence[str]) -> tuple[str, ...]:
    """Anchors for one document's headings, in document order.

    The single derivation, so the fetcher that writes anchors and any check
    that recomputes them cannot disagree. Repeat slugs get ``-2``, ``-3`` and so
    on by their occurrence *among same-slug headings*, which is what keeps an
    unrelated insertion from shifting them.
    """
    seen: dict[str, int] = {}
    anchors: list[str] = []
    for heading in headings:
        slug = slugify_heading(heading)
        seen[slug] = seen.get(slug, 0) + 1
        anchors.append(slug if seen[slug] == 1 else f"{slug}-{seen[slug]}")
    return tuple(anchors)


@dataclass(frozen=True, slots=True)
class PolicyClause:
    """One anchored section of a policy document.

    ``text`` is the verbatim clause. It is the only thing a memo excerpt is ever
    checked against, so an excerpt that is not a substring of some clause is not
    a quotation of this corpus.
    """

    anchor_id: str
    heading: str
    text: str
    ordinal: int

    def __post_init__(self) -> None:
        if _ANCHOR_PATTERN.match(self.anchor_id) is None:
            raise ValueError(
                f"anchor_id must be a lowercase hyphenated slug so it stays citable; "
                f"got {self.anchor_id!r}"
            )
        if not self.heading.strip():
            raise ValueError("every clause carries the heading its anchor derives from")
        if not self.text.strip():
            raise ValueError(
                f"clause {self.anchor_id} carries no text; an anchor resolving to nothing "
                "would let a citation pass while quoting nothing"
            )
        if self.ordinal < 0:
            raise ValueError(f"ordinal must be non-negative; got {self.ordinal}")

    def to_json_object(self) -> dict[str, object]:
        return {
            "anchor_id": self.anchor_id,
            "heading": self.heading,
            "text": self.text,
            "ordinal": self.ordinal,
        }

    @classmethod
    def from_json_object(cls, data: Mapping[str, object]) -> "PolicyClause":
        return cls(
            anchor_id=str(data["anchor_id"]),
            heading=str(data["heading"]),
            text=str(data["text"]),
            ordinal=int(str(data["ordinal"])),
        )


@dataclass(frozen=True, slots=True)
class PolicyDocument:
    """One policy document, as fetched and as committed.

    ``source_url`` is the **exact** URL retrieved, not a bare answer id. That is
    a finding rather than a formality: ``support.google.com`` serves different
    content for the same answer id depending on locale and platform, and a
    planning fetch of the synthetic-media page returned its Android variant. A
    digest of "the page at answer/14328491" would therefore identify nothing, so
    the parameters are part of the identity.
    """

    doc_id: str
    title: str
    source_url: str
    fetched_ts_ist: str
    retrieval_sha256: str
    retrieval: Retrieval
    clauses: tuple[PolicyClause, ...]

    def __post_init__(self) -> None:
        if _ANCHOR_PATTERN.match(self.doc_id) is None:
            raise ValueError(
                f"doc_id must be a lowercase hyphenated slug so it stays citable; "
                f"got {self.doc_id!r}"
            )
        if not self.title.strip():
            raise ValueError(
                f"{self.doc_id} carries no title; the title is what a reader of a memo "
                "citation sees, and it is recorded as fetched rather than as expected"
            )
        if not self.source_url.startswith("https://"):
            raise ValueError(
                f"{self.doc_id} source_url must be an https URL naming the exact page "
                f"fetched; got {self.source_url!r}"
            )
        require_sha256_hex(self.retrieval_sha256, "retrieval_sha256")
        require_ist_iso(self.fetched_ts_ist, "fetched_ts_ist")

        if not self.clauses:
            raise ValueError(
                f"{self.doc_id} has no clauses; a document with no anchors cannot resolve "
                "a citation and would sit in the corpus looking usable"
            )

        seen: set[str] = set()
        for clause in self.clauses:
            if clause.anchor_id in seen:
                raise ValueError(
                    f"duplicate anchor {clause.anchor_id!r} in {self.doc_id}: a citation "
                    "resolving to two clauses is not a citation"
                )
            seen.add(clause.anchor_id)

        expected = anchor_ids_for([clause.heading for clause in self.clauses])
        actual = tuple(clause.anchor_id for clause in self.clauses)
        if expected != actual:
            raise ValueError(
                f"{self.doc_id} anchors do not match the derivation from their headings: "
                f"expected {expected}, got {actual}. Anchors are derived, never hand-written, "
                "so a corpus cannot carry one nobody can recompute"
            )

    @property
    def content_digest(self) -> str:
        """The citation identity: a digest over what this repository committed.

        Deliberately excludes ``retrieval_sha256`` and ``retrieval``. Including
        the first would make this change on every fetch, because the fetched
        page carries a per-request CSP nonce, and a citation identity that moves
        without the policy moving is not an identity. See the module docstring
        for the measurement behind that.

        Reproducible: re-extracting the same page bytes yields the same digest,
        and changing one word of one clause changes it. Both directions are
        pinned by tests, because a digest nobody checks in both directions is a
        digest that could be stable for the wrong reason.
        """
        fields = [
            _DOCUMENT_DOMAIN,
            self.doc_id,
            self.title,
            self.source_url,
        ]
        for clause in self.clauses:
            fields.extend((clause.anchor_id, clause.heading, clause.text, str(clause.ordinal)))
        return digest_fields(*fields)

    def clause(self, anchor_id: str) -> PolicyClause | None:
        """The clause at ``anchor_id``, or ``None``.

        Returns rather than raises: a phantom anchor is a governed finding the
        resolver reports with a reason code, not an exception for it to catch.
        """
        for clause in self.clauses:
            if clause.anchor_id == anchor_id:
                return clause
        return None

    def to_json_object(self) -> dict[str, object]:
        """The clause file's body. The manifest carries the identity fields."""
        return {
            "doc_id": self.doc_id,
            "clauses": [clause.to_json_object() for clause in self.clauses],
        }

    def manifest_entry(self) -> dict[str, object]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "source_url": self.source_url,
            "fetched_ts_ist": self.fetched_ts_ist,
            "retrieval": self.retrieval.value,
            "retrieval_sha256": self.retrieval_sha256,
            "content_digest": self.content_digest,
            "clause_file": f"{self.doc_id}.json",
            "clause_count": len(self.clauses),
        }


@dataclass(frozen=True, slots=True)
class PolicyCorpus:
    """Every policy document a memo may cite, at one version.

    ``corpus_version`` is a human-assigned SemVer label; ``corpus_sha256`` is
    derived from the documents. The version is deliberately *outside* the
    digest: if it were inside, "the version bumps whenever the hash changes"
    would be circular, and two builds of identical text under different labels
    could not be recognised as identical.
    """

    corpus_version: str
    documents: tuple[PolicyDocument, ...]

    def __post_init__(self) -> None:
        if _SEMVER_PATTERN.match(self.corpus_version) is None:
            raise ValueError(
                f"corpus_version must be a SemVer 2.0.0 release string (e.g. '1.0.0'); "
                f"got {self.corpus_version!r}"
            )
        if not self.documents:
            raise ValueError("a corpus carries at least one document")

        seen: set[str] = set()
        for document in self.documents:
            if document.doc_id in seen:
                raise ValueError(f"duplicate doc_id {document.doc_id!r} in the corpus")
            seen.add(document.doc_id)

    @property
    def corpus_sha256(self) -> str:
        """One digest over the whole corpus, in document order.

        This is what binds into ``SESSION_OPEN`` and what a memo pins, so a
        session is tied to the exact corpus state its citations were checked
        against.
        """
        return digest_fields(
            _CORPUS_DOMAIN, *(document.content_digest for document in self.documents)
        )

    def document_by_content_digest(self, content_digest: str) -> PolicyDocument | None:
        """The document a citation's digest names, or ``None``.

        Lookup is by ``content_digest`` because that is what a memo citation
        carries (STEP-05 D3, "policy ground: doc hash + anchor"). Citing by
        digest rather than by id is what stops a citation surviving a document
        being replaced under the same name; citing by *content* digest rather
        than by the retrieval digest is what stops it breaking every time
        somebody re-fetches the page.
        """
        for document in self.documents:
            if document.content_digest == content_digest:
                return document
        return None

    def document(self, doc_id: str) -> PolicyDocument | None:
        for document in self.documents:
            if document.doc_id == doc_id:
                return document
        return None

    def manifest_object(self) -> dict[str, object]:
        return {
            "corpus_version": self.corpus_version,
            "corpus_sha256": self.corpus_sha256,
            "fair_use": FAIR_USE_NOTICE,
            "documents": [document.manifest_entry() for document in self.documents],
        }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    """UTF-8 and ``\\n`` newlines explicitly, as every artifact here is written,
    so a file digest does not depend on the platform that produced it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_corpus(corpus: PolicyCorpus, policies_dir: Path) -> None:
    """Write the manifest and one clause file per document."""
    for document in corpus.documents:
        _write_json(policies_dir / f"{document.doc_id}.json", document.to_json_object())
    _write_json(policies_dir / CORPUS_MANIFEST, corpus.manifest_object())


def load_corpus(policies_dir: Path) -> PolicyCorpus:
    """Read the corpus from disk, and verify it against its own manifest.

    The verification is the point, not a courtesy. The manifest records a
    ``content_digest`` per document and a ``corpus_sha256`` over all of them; if
    a clause file were edited after the manifest was written, the recomputed
    digests would disagree and this refuses to load rather than handing back a
    corpus whose citations resolve against text the manifest never described.

    Raises ``CorpusError`` on anything unusable, so a caller catches one class.
    """
    manifest_path = policies_dir / CORPUS_MANIFEST
    if not manifest_path.is_file():
        raise CorpusError(f"no policy corpus manifest at {manifest_path}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusError(f"could not read {manifest_path}: {exc}") from exc

    documents: list[PolicyDocument] = []
    try:
        for record in manifest["documents"]:
            clause_path = policies_dir / str(record["clause_file"])
            if not clause_path.is_file():
                raise CorpusError(
                    f"manifest names clause file {clause_path.name}, which is not present"
                )
            body = json.loads(clause_path.read_text(encoding="utf-8"))
            document = PolicyDocument(
                doc_id=str(record["doc_id"]),
                title=str(record["title"]),
                source_url=str(record["source_url"]),
                fetched_ts_ist=str(record["fetched_ts_ist"]),
                retrieval_sha256=str(record["retrieval_sha256"]),
                retrieval=Retrieval(str(record["retrieval"])),
                clauses=tuple(PolicyClause.from_json_object(clause) for clause in body["clauses"]),
            )
            if document.content_digest != str(record["content_digest"]):
                raise CorpusError(
                    f"{document.doc_id} does not match its manifest entry: the clause file "
                    f"digests to {document.content_digest} and the manifest records "
                    f"{record['content_digest']}. The corpus has been edited since it was "
                    "written, so nothing here can be cited"
                )
            documents.append(document)

        corpus = PolicyCorpus(
            corpus_version=str(manifest["corpus_version"]),
            documents=tuple(documents),
        )
    except CorpusError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise CorpusError(f"malformed policy corpus at {policies_dir}: {exc}") from exc

    if corpus.corpus_sha256 != str(manifest["corpus_sha256"]):
        raise CorpusError(
            f"corpus digest mismatch: the documents digest to {corpus.corpus_sha256} and the "
            f"manifest records {manifest['corpus_sha256']}"
        )
    return corpus
