# SPDX-License-Identifier: MIT
"""D2: which documents corpus v1 is, and how each one is read (STEP-05 D2).

A flat, declarative table, for the reason ``orchestrator.tools.TOOL_TABLE`` is
one and the firewall's pattern set is one: a specification nobody can read off
in a screen is a specification nobody audits. Everything that decided what the
corpus contains is here, including what was excluded and who named what.

Every entry below was confirmed by Saif against the full verbatim text of all
thirty clauses before anything was hashed, at the STEP-05 D2 review halt. That
is recorded because the corpus is immutable in practice: memos pin a corpus
digest, so re-hashing it later would invalidate every pinned memo and fixture,
and "somebody read this once" is the only thing standing between a citation and
a plausible-looking quotation of the wrong rule.

Why the URLs carry query parameters
-----------------------------------
``hl=en`` and ``co=GENIE.Platform%3DDesktop`` are part of the identity, not
decoration. ``support.google.com`` serves different content for one answer id
depending on locale and platform: fetched bare, the synthetic-media page
returned its **Android** variant, and pinning the parameter returns the
**Computer** one. A source that did not pin them would name a different document
on a different day.
"""

from dataclasses import dataclass

from ts_sentry.data.policy_fetch import CalloutTitle, SectionFilter

__all__ = ["CORPUS_VERSION", "POLICY_SOURCES", "PolicySource"]

CORPUS_VERSION = "1.0.0"
"""The version corpus v1 is written under.

Bumped by hand whenever the committed clauses change, which ``corpus_sha256``
makes detectable rather than a matter of remembering. It is outside that digest
on purpose: a version inside the hash would make "bump the version when the hash
changes" circular.
"""

_BOILERPLATE = frozenset(
    {
        "Was this helpful?",
        "Try these next steps:",
        "YouTube policies",
    }
)
"""Site chrome present on all three pages, and not policy.

``YouTube policies`` earns its place twice over. It is the navigation carousel
listing sibling articles, and it is also **the only extracted section that is
not reproducible**: it carries a per-request session id, so two fetches minutes
apart differ inside it while every substantive section is byte-identical. A
corpus that kept it could never re-derive its own digest.
"""


@dataclass(frozen=True, slots=True)
class PolicySource:
    """One document's fetch and extraction configuration."""

    doc_id: str
    url: str
    section_filter: SectionFilter
    callout_titles: tuple[CalloutTitle, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        if not self.doc_id.strip() or not self.url.startswith("https://"):
            raise ValueError(f"{self.doc_id!r} needs a doc_id and an https url")


POLICY_SOURCES: tuple[PolicySource, ...] = (
    PolicySource(
        doc_id="youtube-spam",
        url=("https://support.google.com/youtube/answer/2801973?hl=en&co=GENIE.Platform%3DDesktop"),
        section_filter=SectionFilter(drop_headings=_BOILERPLATE),
        note=(
            "STEP-05 D2 names this document 'Spam, Deceptive Practices & Scams'. YouTube "
            "has since retitled the page 'Spam Policy'; the body still covers comment "
            "spam, off-platform diversion, scams and AI mass-production, so the subject "
            "matter is unchanged. The corpus records the real current title and the "
            "divergence is recorded as a STEP-05 deviation. Carries no callouts. Its nine "
            "labelled items are the clauses the caseload actually cites: comment-spam "
            "(T-01), off-platform-diversion and scams (T-03), engagement-manipulation "
            "(T-02), automated-or-synthetic-mass-production (T-06)."
        ),
    ),
    PolicySource(
        doc_id="youtube-fake-engagement",
        url=("https://support.google.com/youtube/answer/3399767?hl=en&co=GENIE.Platform%3DDesktop"),
        section_filter=SectionFilter(drop_headings=_BOILERPLATE),
        callout_titles=(
            CalloutTitle(
                match="unauthorized impersonation",
                heading="Unauthorized impersonation",
            ),
        ),
        note=(
            "The page opens with a tip callout carrying 76 words of *impersonation* "
            "policy, before its own subject begins. Extracted as its own clause rather "
            "than folded into the heading it visually sits under, so a fake-engagement "
            "citation cannot resolve to the impersonation rule. Clause boundaries follow "
            "policy subject, not page layout. The heading is operator-supplied, because "
            "the page gives a callout none."
        ),
    ),
    PolicySource(
        doc_id="youtube-genai-disclosure",
        url=(
            "https://support.google.com/youtube/answer/14328491?hl=en&co=GENIE.Platform%3DDesktop"
        ),
        section_filter=SectionFilter(drop_headings=_BOILERPLATE | {"Yes"}),
        callout_titles=(
            CalloutTitle(
                match="limit a video’s audience",
                heading="Disclosure and monetization",
            ),
        ),
        note=(
            "'Yes' is dropped: it is a YouTube Studio UI instruction ('Yes: In the "
            "Attributes section, under “AI use”...'), not citable policy, and its "
            "anchor would have been the useless name 'yes'. Confirmed by Saif. The note "
            "callout is operator-titled 'Disclosure and monetization'; the page gives it "
            "no heading, and that naming is editorial and recorded as such."
        ),
    ),
)
"""Corpus v1. Three documents, thirty clauses, confirmed clause by clause.

ARCHITECTURE 4.3 names the three: "Spam, Deceptive Practices & Scams; Fake
Engagement; synthetic-media disclosure requirements". These are those documents
as they exist today, under the titles they actually carry.
"""
