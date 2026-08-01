# SPDX-License-Identifier: MIT
"""D3: the memo, as a DSA Article 17 statement of reasons (STEP-05 D3).

ARCHITECTURE 4.3 and 8.5 fix the shape: "facts, legal or policy ground with
precise citation, proposed measure, redress information", so that analyst output
is regulation-shaped by default rather than ad hoc.

Everything below is built against the Regulation's actual text, retrieved from
EUR-Lex rather than recalled (CLAUDE.md, official sources):
https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32022R2065

Article 17(3) requires a statement of reasons to contain at least:

    (a) what the decision entails ... and, where relevant, the territorial
        scope of the decision and its duration;
    (b) the facts and circumstances relied on in taking the decision ...;
    (c) where applicable, information on the use made of automated means in
        taking the decision, including information on whether the decision was
        taken in respect of content detected or identified using automated
        means;
    (d) where the decision concerns allegedly illegal content, a reference to
        the legal ground ...;
    (e) where the decision is based on the alleged incompatibility of the
        information with the terms and conditions ..., a reference to the
        contractual ground relied on and explanations as to why ...;
    (f) clear and user-friendly information on the possibilities for redress ...

STEP-05 3.1's four sentence roles map onto (b), (e), (a) and (f). Two elements
need saying out loud rather than leaving to a reader to notice.

**(d) is deliberately unreachable.** Every memo this system drafts is a
terms-and-conditions case, because the corpus is platform policy and the system
makes no assessment of legality. There is no ``LEGAL_GROUND`` role, and adding
one would invite a memo to assert that something is illegal, which is a judgment
nothing here is equipped to make.

**(c) is not a sentence.** It is structural, on ``AutomatedMeans``, and the
agent cannot write it. That is the same argument ``ReviewOutcome.reviewer_kind``
makes: a disclosure about how automated a decision was is worthless if the
automated component gets to phrase it. The orchestrator knows whether detection
was automated and whether a human signed; the agent's account of its own
autonomy is not evidence of anything.

Recorded deviations from STEP-05 3.1
------------------------------------
3.1 names four sentence roles, and they are implemented exactly as named. Three
things fall outside them, recorded here rather than left for a reader to notice:

1. **17(3)(c) has no role** and is carried structurally, as above.
2. **No ``LEGAL_GROUND`` role**, so 17(3)(d) is unreachable by construction.
   Deliberate: every case here is a terms-and-conditions matter, and a legal
   ground role would invite a memo to assert illegality that nothing in this
   system is equipped to assess.
3. **17(3)(a)'s "territorial scope ... and its duration" is not modelled.** The
   measure is recorded; its scope and duration are not. The synthetic platform
   has no jurisdictional dimension to carry one.

Honest limit, carried into the README
-------------------------------------
Article 17(2) says: "Paragraph 1 shall not apply where the information is
deceptive high-volume commercial content." That plausibly covers a large part of
this system's caseload, T-01 comment spam rings and T-06 slop farms in
particular, so for much of what this workbench drafts memos about the DSA would
not require a statement of reasons at all. The format is regulation-shaped
best-practice documentation; "these are DSA Article 17 statements of reasons" is
wider than the truth, and this is where that is said.

Where the judgment lives
------------------------
Not here. This module defines the structure and enforces what is *structurally*
true of a memo; whether its claims resolve is
``orchestrator.memo_gate``. Same split as ``agents.evidence.pack`` and
``orchestrator.pack_gate``, for the reason the STEP-03 import-graph test found:
an agent holding its own verifier is an agent nobody is verifying.

Unconstructible, not merely rejected
------------------------------------
A ``FACT`` with no evidence ids and a ``POLICY_GROUND`` with no citation cannot
be built at all. The gate then judges well-formed memos rather than malformed
ones, which is the division STEP-04 D4 recorded.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from ts_sentry.agents.citations import parse_citations
from ts_sentry.governance.canonical import require_sha256_hex

__all__ = [
    "MAX_EXCERPT_WORDS",
    "MIN_EXCERPT_WORDS",
    "AutomatedDecision",
    "AutomatedMeans",
    "Measure",
    "Memo",
    "MemoError",
    "MemoSentence",
    "MemoStatus",
    "PolicyCitation",
    "SentenceRole",
    "excerpt_word_count",
]

MAX_EXCERPT_WORDS = 15
"""The excerpt ceiling from STEP-05 D2's fair-use scoping.

A memo cites a clause by anchor and quotes at most this many words of it. The
limit is enforced by the type rather than requested in a prompt, because a
fair-use posture a model can exceed is not a posture.
"""

MIN_EXCERPT_WORDS = 4
"""The excerpt floor. Added at the HALT-2 review, finding 2.

The ceiling had no counterpart, and that was a hole rather than an omission: an
excerpt of ``"spam"`` is a perfectly true substring of the comment-spam clause
and identifies no rule at all, so a memo could satisfy Article 17(3)(e)'s
"reference to the contractual ground relied on" with one common word. A citation
has to quote enough of the clause to say *which* rule is being relied on.

Four is a judgment, and a small one: it is enough that a quotation is
recognisably of a particular clause, and short enough that a genuine short
clause can still be cited. It is not derived from anything, and saying so is
better than implying a provenance it does not have.
"""


class MemoError(Exception):
    """Raised when a memo cannot be assembled from what it was given.

    Distinct from a gate rejection, exactly as ``PackError`` is. A gate
    rejection is a governed finding about a well-formed memo; this is a caller
    handing the constructor something that is not a statement of reasons.
    """


class SentenceRole(StrEnum):
    """What one sentence of a memo is doing (STEP-05 3.1).

    Four members, mapping to DSA Article 17(3) points (b), (e), (a) and (f).
    Article 17(3)(d), the illegal-content legal ground, is deliberately absent;
    see the module docstring.
    """

    FACT = "fact"
    """Art 17(3)(b): a fact or circumstance relied on. Must cite evidence."""

    POLICY_GROUND = "policy_ground"
    """Art 17(3)(e): the contractual ground, with a resolvable citation."""

    MEASURE = "measure"
    """Art 17(3)(a): what the proposed decision entails."""

    REDRESS = "redress"
    """Art 17(3)(f): the recipient's possibilities for redress."""


class Measure(StrEnum):
    """The proposed measure, from a fixed vocabulary (STEP-05 3.1).

    No free-text sanctions: a memo that could invent a penalty would be a memo
    proposing something no system has to honour. Every member is one of the
    restrictions Article 17(1) enumerates, quoted in the comment beside it:

    * (a) "any restrictions of the visibility of specific items of information
      ... including removal of content, disabling access to content, or
      demoting content";
    * (b) "suspension, termination or other restriction of monetary payments";
    * (c) "suspension or termination of the provision of the service in whole
      or in part";
    * (d) "suspension or termination of the recipient of the service's
      account".

    There is no member for taking no action. A memo is a *proposed* enforcement
    rationale, and an analyst who declines records that through
    ``signature.Decision.REJECT``, which already exists for exactly this. Adding
    a "no action" measure would create a second, unsigned way to say no.
    """

    CONTENT_REMOVED = "content_removed"  # 17(1)(a)
    ACCESS_DISABLED = "access_disabled"  # 17(1)(a)
    CONTENT_DEMOTED = "content_demoted"  # 17(1)(a)
    VISIBILITY_RESTRICTED = "visibility_restricted"  # 17(1)(a)
    MONETIZATION_SUSPENDED = "monetization_suspended"  # 17(1)(b)
    MONETIZATION_TERMINATED = "monetization_terminated"  # 17(1)(b)
    SERVICE_SUSPENDED_PARTIAL = "service_suspended_partial"  # 17(1)(c)
    SERVICE_SUSPENDED_FULL = "service_suspended_full"  # 17(1)(c)
    SERVICE_TERMINATED = "service_terminated"  # 17(1)(c)
    ACCOUNT_SUSPENDED = "account_suspended"  # 17(1)(d)
    ACCOUNT_TERMINATED = "account_terminated"  # 17(1)(d)


class AutomatedDecision(StrEnum):
    """How automated the decision was (Art 17(3)(c)).

    Values follow the European Commission's own DSA Transparency Database
    schema, which is the operational rendering of this article's requirement
    (``automated_decision``: fully, partially, or not automated).
    https://transparency.dsa.ec.europa.eu/page/api-documentation
    """

    FULLY_AUTOMATED = "fully_automated"
    PARTIALLY_AUTOMATED = "partially_automated"
    NOT_AUTOMATED = "not_automated"


class MemoStatus(StrEnum):
    """Whether a human has signed this memo.

    ``DRAFT`` is the only status an agent can produce. The transition to
    ``SIGNED`` happens on the human signature path (STEP-05 D6), which
    ``agents.*`` cannot import.
    """

    DRAFT = "draft"
    SIGNED = "signed"


def excerpt_word_count(excerpt: str) -> int:
    """Words in an excerpt, by the one definition the whole system uses.

    Whitespace-separated, so it cannot disagree with itself between the type's
    check and the resolver's. A second spelling of "how long is this" is a
    second chance for a memo to be accepted by one and refused by the other.
    """
    return len(excerpt.split())


@dataclass(frozen=True, slots=True)
class PolicyCitation:
    """A pointer into the hashed corpus: which document, which clause, which words.

    ``content_digest`` names the document rather than ``doc_id`` naming it,
    because an id can be reused for replaced text while a digest cannot. It is
    the *content* digest rather than the retrieval digest for the reason
    STEP-05 D1 recorded: the fetched page carries a per-request CSP nonce, so a
    citation pinned to the retrieval digest would break every time somebody
    re-fetched a page that had not changed.
    """

    content_digest: str
    anchor_id: str
    excerpt: str

    def __post_init__(self) -> None:
        require_sha256_hex(self.content_digest, "content_digest")
        if not self.anchor_id.strip():
            raise ValueError("a citation names the anchor it resolves to")
        if not self.excerpt.strip():
            raise ValueError(
                "a citation quotes the clause it relies on; an empty excerpt cites a "
                "location without saying what is there"
            )
        words = excerpt_word_count(self.excerpt)
        if words > MAX_EXCERPT_WORDS:
            raise ValueError(
                f"excerpt is {words} words; the fair-use ceiling is {MAX_EXCERPT_WORDS}"
            )
        if words < MIN_EXCERPT_WORDS:
            raise ValueError(
                f"excerpt is {words} words; a citation quotes at least "
                f"{MIN_EXCERPT_WORDS} so it identifies which rule is relied on"
            )

    def to_json_object(self) -> dict[str, object]:
        return {
            "content_digest": self.content_digest,
            "anchor_id": self.anchor_id,
            "excerpt": self.excerpt,
        }


@dataclass(frozen=True, slots=True)
class MemoSentence:
    """One typed sentence of a statement of reasons.

    ``evidence_ids`` is the set of pack record ids this sentence relies on. It
    is stored rather than re-parsed from ``text`` on every read, but the two are
    kept honest against each other: ``from_text`` derives the set from the
    bracketed citations the agent actually wrote, so a sentence cannot claim
    support it did not point at.
    """

    index: int
    role: SentenceRole
    text: str
    evidence_ids: frozenset[str] = frozenset()
    citation: PolicyCitation | None = None

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError(f"index must be non-negative; got {self.index}")
        if not self.text.strip():
            raise ValueError(f"sentence {self.index} carries no text")

        if self.role is SentenceRole.FACT and not self.evidence_ids:
            raise MemoError(
                f"sentence {self.index} is a FACT citing no evidence. Article 17(3)(b) "
                "requires the facts relied on, and a fact nobody can trace to the pack "
                "is the A-01 confabulation this system exists to refuse"
            )
        if self.role is SentenceRole.POLICY_GROUND and self.citation is None:
            raise MemoError(
                f"sentence {self.index} is a POLICY_GROUND with no citation. Article "
                "17(3)(e) requires a reference to the contractual ground relied on"
            )
        if self.role is not SentenceRole.POLICY_GROUND and self.citation is not None:
            raise MemoError(
                f"sentence {self.index} is a {self.role.value} carrying a policy "
                "citation; the ground is stated once, in a POLICY_GROUND sentence, so a "
                "reader knows which sentence is the rule being relied on"
            )

        # The text and the recorded ids must agree. Found by a test that expected
        # three gate failures and got two: a sentence built directly rather than
        # through `from_text` carried "[prov-8002]" in its prose while its
        # evidence_ids stayed empty, so the claim checker never saw it and the
        # sentence read to a human as supported while resolving nothing.
        #
        # That is the confabulation this system exists to catch, arriving
        # through the constructor instead of through a model. A sentence may
        # record *more* ids than it displays; it may never display one it has
        # not recorded.
        undeclared = sorted(parse_citations(self.text) - self.evidence_ids)
        if undeclared:
            raise MemoError(
                f"sentence {self.index} displays citations it does not record: "
                f"{undeclared}. A sentence that shows a reader an evidence id the "
                "verifier will not check is unsupported prose wearing a citation"
            )

    @classmethod
    def from_text(
        cls,
        index: int,
        role: SentenceRole,
        text: str,
        *,
        citation: PolicyCitation | None = None,
    ) -> "MemoSentence":
        """Build a sentence, deriving its evidence ids from its own text.

        The route an agent's output takes. Citations are the bracketed ids
        ``agents.citations`` defines and both other agents already write, so a
        memo, a pivot proposal and a triage rationale all point at evidence the
        same way and one parser decides what a citation is.
        """
        return cls(
            index=index,
            role=role,
            text=text,
            evidence_ids=parse_citations(text),
            citation=citation,
        )

    def to_json_object(self) -> dict[str, object]:
        return {
            "index": self.index,
            "role": self.role.value,
            "text": self.text,
            "evidence_ids": sorted(self.evidence_ids),
            "citation": None if self.citation is None else self.citation.to_json_object(),
        }


@dataclass(frozen=True, slots=True)
class AutomatedMeans:
    """Article 17(3)(c), recorded by the machinery rather than written by it.

    Not a sentence, and not the agent's to phrase. A disclosure about how
    automated a decision was is worthless if the automated component composes
    it, which is the argument ``ReviewOutcome.reviewer_kind`` already makes
    about who decided.

    ``detection_automated`` is true throughout this system: the flagged queue
    comes from ``orchestrator.detection_stub``, so every case reaching a memo
    was identified by automated means, and Article 17(3)(c) asks for exactly
    that fact.

    ``decision`` is ``PARTIALLY_AUTOMATED`` for a signed memo, because an agent
    drafted it and a human signed it. ``FULLY_AUTOMATED`` on a signed memo is
    **refused by ``Memo.__post_init__``**, not merely documented as impossible:
    ENFORCE is human-only, so a signed memo claiming a fully automated decision
    would be describing a path this system does not have. Enforced rather than
    described, because a docstring-only invariant is not an invariant.
    """

    detection_automated: bool
    decision: AutomatedDecision
    drafted_by: str

    def __post_init__(self) -> None:
        if not self.drafted_by.strip():
            raise ValueError(
                "a memo names what drafted it; an undisclosed drafter is the "
                "transparency failure Article 17(3)(c) exists to prevent"
            )

    def to_json_object(self) -> dict[str, object]:
        return {
            "detection_automated": self.detection_automated,
            "decision": self.decision.value,
            "drafted_by": self.drafted_by,
        }


@dataclass(frozen=True, slots=True)
class Memo:
    """A draft enforcement memo: the memo agent's output contract.

    This is the type ``MEMO_MANDATE`` declares as its ``output_schema``, so
    dispatch's schema check is a check against this class and the RECOMMEND gate
    validates this object.

    Three bindings, all load-bearing, all covered by ``content_digest``:

    * ``pack_digest`` names the evidence pack this memo was drafted from, so the
      gate cannot verify a memo's claims against a pack it never saw;
    * ``corpus_sha256`` and ``corpus_version`` pin the corpus its citations were
      checked against (STEP-05 3.4), matching what ``SESSION_OPEN`` recorded;
    * ``status`` is ``DRAFT`` until a human signature finalizes it, and no code
      in ``agents.*`` can reach the module that produces one.
    """

    memo_id: str
    case_id: str
    subject_id: str
    pack_digest: str
    corpus_version: str
    corpus_sha256: str
    sentences: tuple[MemoSentence, ...]
    measure: Measure
    automated_means: AutomatedMeans
    status: MemoStatus = MemoStatus.DRAFT

    def __post_init__(self) -> None:
        for name, value in (
            ("memo_id", self.memo_id),
            ("case_id", self.case_id),
            ("subject_id", self.subject_id),
            ("corpus_version", self.corpus_version),
        ):
            if not value.strip():
                raise ValueError(f"{name} must be a non-empty identifier")
        require_sha256_hex(self.pack_digest, "pack_digest")
        require_sha256_hex(self.corpus_sha256, "corpus_sha256")

        if not self.sentences:
            raise MemoError("a memo carries at least one sentence")

        actual = tuple(sentence.index for sentence in self.sentences)
        if actual != tuple(range(len(self.sentences))):
            raise MemoError(
                f"sentence indices must be contiguous from zero; got {actual}. The "
                "verifier reports failures by index, and an index that does not locate "
                "a sentence cannot be acted on by a revise loop"
            )

        # Structural completeness. This is what makes the artifact a *statement
        # of reasons* rather than prose in four flavours: Article 17(3) requires
        # points (a), (b), (e) and (f), so a memo missing any of the four roles
        # is not the document this type claims to be.
        present = {sentence.role for sentence in self.sentences}
        missing = sorted(role.value for role in SentenceRole if role not in present)
        if missing:
            raise MemoError(
                f"a statement of reasons carries every role; this memo has no {missing}. "
                "DSA Article 17(3) requires the facts relied on (b), the contractual "
                "ground (e), what the decision entails (a), and the redress available (f)"
            )

        # The ENFORCE invariant, reaching the memo. A signed memo is one a human
        # decided on, so it cannot also claim the decision was fully automated.
        if (
            self.status is MemoStatus.SIGNED
            and self.automated_means.decision is AutomatedDecision.FULLY_AUTOMATED
        ):
            raise MemoError(
                "a signed memo cannot disclose a fully automated decision. ENFORCE is "
                "human-only, so a signature is exactly the human step that makes the "
                "decision partially automated; claiming otherwise would describe a path "
                "this system does not have"
            )

    # -- reads -------------------------------------------------------------

    @property
    def signed(self) -> bool:
        return self.status is MemoStatus.SIGNED

    @property
    def facts(self) -> tuple[MemoSentence, ...]:
        return tuple(s for s in self.sentences if s.role is SentenceRole.FACT)

    @property
    def policy_grounds(self) -> tuple[MemoSentence, ...]:
        return tuple(s for s in self.sentences if s.role is SentenceRole.POLICY_GROUND)

    @property
    def cited_evidence_ids(self) -> frozenset[str]:
        """Every pack record id this memo relies on, across all sentences."""
        return frozenset().union(*(s.evidence_ids for s in self.sentences)) or frozenset()

    @property
    def citations(self) -> tuple[PolicyCitation, ...]:
        return tuple(s.citation for s in self.sentences if s.citation is not None)

    @property
    def content_digest(self) -> str:
        """The digest a human signature binds to (STEP-05 D6).

        Covers the whole memo including its bindings and its status, so a
        signature cannot silently carry over to an edited memo. That property is
        already documented on ``HumanSignature.subject_hash``; this is the value
        that makes it true for memos.
        """
        return hashlib.sha256(
            json.dumps(self.to_json_object(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def with_sentences(self, sentences: Sequence[MemoSentence]) -> "Memo":
        """A new memo with revised sentences, for the D4 revise loop.

        Returns a new object. A memo that had already been through the gate must
        not mutate, so "this memo was verified" stays a statement about a
        specific set of bytes. ``status`` is deliberately *not* carried into the
        new memo: revising a signed memo produces a draft, because the signature
        was over text that no longer exists.
        """
        return Memo(
            memo_id=self.memo_id,
            case_id=self.case_id,
            subject_id=self.subject_id,
            pack_digest=self.pack_digest,
            corpus_version=self.corpus_version,
            corpus_sha256=self.corpus_sha256,
            sentences=tuple(sentences),
            measure=self.measure,
            automated_means=self.automated_means,
            status=MemoStatus.DRAFT,
        )

    def to_json_object(self) -> dict[str, object]:
        return {
            "memo_id": self.memo_id,
            "case_id": self.case_id,
            "subject_id": self.subject_id,
            "pack_digest": self.pack_digest,
            "corpus_version": self.corpus_version,
            "corpus_sha256": self.corpus_sha256,
            "measure": self.measure.value,
            "automated_means": self.automated_means.to_json_object(),
            "status": self.status.value,
            "sentences": [sentence.to_json_object() for sentence in self.sentences],
        }
