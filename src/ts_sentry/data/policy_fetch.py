# SPDX-License-Identifier: MIT
"""D1: the fetch-once script behind the policy corpus (STEP-05 D1).

Separate from :mod:`ts_sentry.data.policy_corpus` because they run at different
times and under different assumptions. The corpus module is loaded by every
test and never touches a network; this module reaches the public internet and
is run by hand, exactly once per corpus version, in the same shape as
``--llm-mode live``. CI does not run it.

Why stdlib, and not a fetch library
-----------------------------------
``urllib.request`` and ``html.parser``. Adding ``requests`` or ``beautifulsoup4``
to fetch three pages would widen the supply-chain surface ``docs/DECISIONS.md``
already lists gaps against, for a job that is one GET and one tag walk. Same
argument STEP-04 made when it hand-wrote GraphML rather than adding ``networkx``.

Why the raw bytes, and not a summarised retrieval
-------------------------------------------------
Recorded because it changed the design. During STEP-05 planning the policy pages
were reached through a summarising retrieval layer, which returned restructured
markdown rather than the page. That is fine for deciding whether a URL is the
right one and useless for hashing: a digest of a paraphrase identifies the
paraphrase. So this reads the response body itself, hashes exactly those bytes,
and extracts clauses from the same bytes it hashed.

What this does not do
---------------------
It does not decide whether a page is the right page. That is a human judgment
and STEP-05 halts for it: the operator is shown the fetched title and every
extracted heading, and confirms the document before anything is hashed into a
corpus. An extractor that silently accepted whatever came back would be the
component quietly deciding what the policy is.
"""

import html
import re
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from html.parser import HTMLParser

from ts_sentry.data.policy_corpus import (
    PolicyClause,
    PolicyDocument,
    Retrieval,
    anchor_ids_for,
)

__all__ = [
    "USER_AGENT",
    "CalloutTitle",
    "ExtractedSection",
    "FetchError",
    "FetchedPage",
    "SectionFilter",
    "SectionKind",
    "build_document",
    "extract_sections",
    "extract_title",
    "fetch_document",
    "fetch_page",
    "name_callouts",
]

USER_AGENT = "ts-sentry-policy-fetch/0.1 (+https://github.com/MohdSaifHussain/ts-sentry)"
"""Identifies the fetcher rather than impersonating a browser.

``support.google.com/robots.txt`` disallows only ``/*/search``, ``/*/api(s)``,
``/*/bin/search.*`` and ``/*/forum-attachment``; ``/youtube/answer/`` is not
disallowed for any user agent. Verified 1 August 2026.
"""

_TIMEOUT_SECONDS = 30
_HEADING_TAGS = frozenset({"h1", "h2", "h3"})
_LABEL_TAGS = frozenset({"strong", "b"})
_IGNORED_TAGS = frozenset({"script", "style", "noscript", "template", "svg"})
_WHITESPACE = re.compile(r"\s+")

_CALLOUT_CLASSES = frozenset({"tip", "note", "warning", "important"})
"""Classes marking a boxed aside that carries its own subject.

Measured against all three corpus pages rather than guessed: exactly two exist,
a ``tip`` on the fake-engagement page and a ``note`` on the GenAI page, and the
spam page has none. The wider set is declared because these are the classes this
help centre uses, and a callout appearing under one of the others should surface
as an unnamed callout that ``build_document`` refuses, rather than being folded
silently into a neighbouring clause.
"""

_MIN_PLAUSIBLE_BYTES = 2_000
"""Below this, a "page" is an error shell, a consent interstitial, or a
redirect stub. Fetching one of those and hashing it would pin a corpus document
to a page that never contained the policy, and the operator reviewing headings
might not notice because there would be no headings to look wrong."""


class FetchError(Exception):
    """The page could not be retrieved, or what came back is not usable.

    Raised rather than returned, unlike most failures in this codebase. This is
    not a governed outcome an agent produced; it is an operator-run script that
    could not do its one job, and there is nothing downstream that should carry
    on with a partial result.
    """


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """One retrieved page, and the digest of exactly the bytes retrieved."""

    url: str
    raw: bytes
    encoding: str

    @property
    def sha256(self) -> str:
        return sha256(self.raw).hexdigest()

    @property
    def text(self) -> str:
        return self.raw.decode(self.encoding, errors="replace")


class SectionKind(StrEnum):
    """What structure produced a section.

    Carried so the caller can treat callouts differently without re-parsing.
    A callout has no heading of its own, and the difference has to survive
    extraction for ``build_document`` to be able to insist on one.
    """

    HEADING = "heading"
    LABELLED_ITEM = "labelled_item"
    CALLOUT = "callout"


@dataclass(frozen=True, slots=True)
class ExtractedSection:
    """One heading and the text under it, before it becomes a clause.

    ``heading`` is empty for a ``CALLOUT``, because the page gives one no
    heading. Naming it is an editorial act and ``build_document`` refuses to
    proceed until the operator has performed it.
    """

    heading: str
    text: str
    kind: SectionKind = SectionKind.HEADING


@dataclass(frozen=True, slots=True)
class CalloutTitle:
    """An operator-supplied heading for one callout, matched by its content.

    Callouts are boxed asides with no heading in the markup, so a human has to
    name them. Matching on a distinctive substring rather than on position is
    deliberate: a positional key silently retitles the wrong box the day a page
    gains another one, and this is the corpus where a wrong label means a memo
    cites the wrong rule under a confident-looking anchor.
    """

    match: str
    heading: str

    def __post_init__(self) -> None:
        if not self.match.strip():
            raise ValueError("a callout title matches on non-empty text")
        if not self.heading.strip():
            raise ValueError("a callout title supplies a non-empty heading")


def fetch_page(url: str) -> FetchedPage:
    """GET ``url`` and keep the response body verbatim.

    Refuses anything that is not HTML or is implausibly short, because the
    failure this guards against is silent: a consent page or an error shell
    hashes perfectly well and would pin a corpus document to a page containing
    no policy at all.
    """
    if not url.startswith("https://"):
        raise FetchError(f"policy sources are fetched over https only; got {url!r}")

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
            raw = response.read()
            content_type = response.headers.get_content_type()
            encoding = response.headers.get_content_charset() or "utf-8"
            final_url = response.geturl()
    except Exception as exc:  # noqa: BLE001 - any transport failure is a fetch failure
        raise FetchError(f"could not fetch {url}: {type(exc).__name__}: {exc}") from exc

    if content_type != "text/html":
        raise FetchError(f"{url} returned {content_type}, not text/html")
    if len(raw) < _MIN_PLAUSIBLE_BYTES:
        raise FetchError(
            f"{url} returned {len(raw)} bytes, which is too short to be a policy page. "
            "This is usually a consent interstitial or an error shell, and hashing it "
            "would pin a corpus document to a page with no policy in it"
        )
    return FetchedPage(url=final_url, raw=raw, encoding=encoding)


class _SectionParser(HTMLParser):
    """Split a page into anchorable sections.

    Two kinds of unit, and the second one is the reason this is not a five-line
    heading splitter:

    1. **Heading sections**, bounded by ``h1``/``h2``/``h3``.
    2. **Labelled list items**: a top-level ``<li>`` opening with a bold label,
       as in ``<li><strong>Comment spam:</strong> Using high-volume ...</li>``.

    The second was a finding, not a design preference. On the YouTube spam page
    every individual violation type - comment spam, off-platform diversion,
    scams, automated mass-production - is one of these items inside a single
    486-word "What this policy means for you" section. Anchoring only at
    headings would mean a memo about a comment-spam ring could cite nothing more
    specific than that whole section, and the citation would not be saying which
    rule was broken. These items are what a statement of reasons actually needs
    to point at, and they map directly onto the threat classes in ARCHITECTURE
    2.1.

    Nested items fold into their parent rather than becoming anchors of their
    own. The nested ones here are all ``<strong>Example:</strong>``, and a corpus
    carrying anchors named ``example-7`` would be carrying names no memo could
    sensibly cite.

    Text before the first heading is discarded, which drops the navigation
    chrome preceding the article on these pages.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._sections: list[ExtractedSection] = []
        self._heading: str | None = None
        self._buffer: list[str] = []
        self._items: list[ExtractedSection] = []
        self._in_heading = False
        self._heading_buffer: list[str] = []
        self._ignore_depth = 0
        self._li_depth = 0
        self._item_buffer: list[str] = []
        self._item_label: str | None = None
        self._in_label = False
        self._label_buffer: list[str] = []
        self._callout_depth = 0
        self._callout_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _IGNORED_TAGS:
            self._ignore_depth += 1
            return
        if self._ignore_depth:
            return

        # Inside a callout, only div nesting matters: everything else is text.
        if self._callout_depth:
            if tag == "div":
                self._callout_depth += 1
            return

        if tag in _HEADING_TAGS:
            self._flush()
            self._in_heading = True
            self._heading_buffer = []
            return
        if tag == "div" and self._heading is not None and _is_callout(attrs):
            self._callout_depth = 1
            self._callout_buffer = []
            return
        if tag == "li" and self._heading is not None:
            self._li_depth += 1
            if self._li_depth == 1:
                self._item_buffer = []
                self._item_label = None
            return
        if tag in _LABEL_TAGS and self._li_depth == 1 and self._item_label is None:
            self._in_label = True
            self._label_buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag in _IGNORED_TAGS:
            self._ignore_depth = max(0, self._ignore_depth - 1)
            return
        if self._ignore_depth:
            return

        if self._callout_depth:
            if tag == "div":
                self._callout_depth -= 1
                if self._callout_depth == 0:
                    self._close_callout()
            return

        if tag in _HEADING_TAGS and self._in_heading:
            self._in_heading = False
            self._heading = _collapse("".join(self._heading_buffer))
            return
        if tag in _LABEL_TAGS and self._in_label:
            self._in_label = False
            self._item_label = _collapse("".join(self._label_buffer)).rstrip(":").strip()
            return
        if tag == "li" and self._li_depth:
            if self._li_depth == 1:
                self._close_item()
            self._li_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignore_depth:
            return
        if self._callout_depth:
            self._callout_buffer.append(data)
            return
        if self._in_heading:
            self._heading_buffer.append(data)
            return
        if self._in_label:
            self._label_buffer.append(data)
            return
        if self._heading is None:
            return
        if self._li_depth:
            self._item_buffer.append(data)
        else:
            self._buffer.append(data)

    def _close_callout(self) -> None:
        """Emit a callout as its own unnamed section.

        Never folded into the surrounding prose, which is the whole point. On
        the fake-engagement page the callout is 76 words of *impersonation*
        policy sitting immediately under the fake-engagement heading, so folding
        it in produced a clause whose anchor said fake engagement and whose
        opening third said something else. Clause boundaries follow policy
        subject, not page layout.
        """
        text = _collapse("".join(self._callout_buffer))
        if text:
            self._items.append(ExtractedSection(heading="", text=text, kind=SectionKind.CALLOUT))
        self._callout_buffer = []

    def _close_item(self) -> None:
        """Emit a labelled item as its own section, or fold it back.

        An unlabelled bullet is prose that happens to be in a list, so it goes
        back into the section's own text rather than becoming an anchor whose
        name would have to be invented.
        """
        text = _collapse("".join(self._item_buffer))
        if self._item_label and text:
            self._items.append(
                ExtractedSection(
                    heading=self._item_label,
                    text=f"{self._item_label}: {text}",
                    kind=SectionKind.LABELLED_ITEM,
                )
            )
        elif text:
            self._buffer.append(" " + text)
        self._item_buffer = []
        self._item_label = None

    def _flush(self) -> None:
        """Emit the section's own prose, then its labelled items, in order."""
        if self._heading:
            text = _collapse("".join(self._buffer))
            if text:
                self._sections.append(ExtractedSection(heading=self._heading, text=text))
            self._sections.extend(self._items)
        self._buffer = []
        self._items = []
        self._heading = None

    def close(self) -> None:
        super().close()
        self._flush()

    @property
    def sections(self) -> tuple[ExtractedSection, ...]:
        return tuple(self._sections)


def _is_callout(attrs: list[tuple[str, str | None]]) -> bool:
    """Whether a ``div``'s classes mark it as a boxed aside."""
    return any(
        name == "class" and value and _CALLOUT_CLASSES & set(value.split()) for name, value in attrs
    )


def _collapse(text: str) -> str:
    """One space between words, no leading or trailing whitespace.

    Applied so a clause digests identically however the source page happened to
    wrap it, which is what keeps ``content_digest`` a statement about the text
    rather than about its indentation.
    """
    return _WHITESPACE.sub(" ", html.unescape(text)).strip()


def extract_sections(page_text: str) -> tuple[ExtractedSection, ...]:
    """Every heading and its text, in document order."""
    parser = _SectionParser()
    parser.feed(page_text)
    parser.close()
    return parser.sections


def extract_title(page_text: str) -> str:
    """The page's ``<title>``, as fetched.

    Reported to the operator rather than compared against an expectation. A
    fetcher that checked the title against what it hoped to find would be
    deciding the thing the review stop exists to decide, and STEP-05 already
    turned up one page whose real title differs from the one its contract names.
    """
    match = re.search(r"<title[^>]*>(.*?)</title>", page_text, re.IGNORECASE | re.DOTALL)
    return _collapse(match.group(1)) if match else ""


@dataclass(frozen=True, slots=True)
class SectionFilter:
    """Which extracted sections become clauses.

    Present because these pages carry boilerplate ("Was this helpful?", "Need
    more help?") that is structurally a section and is not policy. The drop list
    is declared by the operator at fetch time and recorded, so a reader can see
    what was excluded rather than inferring it from what is missing.
    """

    drop_headings: frozenset[str] = field(default_factory=frozenset)
    min_words: int = 3

    def keep(self, section: ExtractedSection) -> bool:
        if section.heading.strip().lower() in {h.lower() for h in self.drop_headings}:
            return False
        return len(section.text.split()) >= self.min_words


def name_callouts(
    sections: tuple[ExtractedSection, ...], titles: Sequence[CalloutTitle]
) -> tuple[ExtractedSection, ...]:
    """Give every callout the heading its operator chose, or refuse.

    Fail-closed in both directions, which is the point rather than
    thoroughness for its own sake:

    * a callout no title matches is an **unnamed clause**, and the alternative
      to refusing is inventing a heading or dropping policy text;
    * a title matching no callout, or matching more than one, means the
      operator's intent no longer fits the page, and silently applying it would
      put a confident heading on whichever box happened to match.
    """
    named: list[ExtractedSection] = []
    used: dict[int, int] = {}

    for section in sections:
        if section.kind is not SectionKind.CALLOUT:
            named.append(section)
            continue
        matches = [index for index, title in enumerate(titles) if title.match in section.text]
        if not matches:
            raise FetchError(
                "an unnamed callout would become a clause with no heading: "
                f"{section.text[:120]!r}. Supply a CalloutTitle matching it, or the corpus "
                "would carry policy text under a heading nobody chose"
            )
        if len(matches) > 1:
            raise FetchError(
                f"{len(matches)} callout titles match one callout: "
                f"{[titles[index].heading for index in matches]}. Narrow the match text"
            )
        index = matches[0]
        used[index] = used.get(index, 0) + 1
        named.append(
            ExtractedSection(
                heading=titles[index].heading,
                text=section.text,
                kind=SectionKind.CALLOUT,
            )
        )

    for index, title in enumerate(titles):
        if used.get(index, 0) != 1:
            raise FetchError(
                f"callout title {title.heading!r} matched {used.get(index, 0)} callouts, "
                "expected exactly one; the page has changed under this configuration"
            )
    return tuple(named)


def fetch_document(
    doc_id: str,
    url: str,
    *,
    section_filter: SectionFilter,
    callout_titles: Sequence[CalloutTitle],
    fetched_ts_ist: datetime,
) -> PolicyDocument:
    """Fetch one page and build its corpus document, end to end.

    The whole pipeline in one call so the operator script and any future caller
    run the identical sequence: fetch, extract, filter boilerplate, name
    callouts, anchor, build. A second spelling of this order is a second chance
    for a corpus to be assembled slightly differently from the one that was
    reviewed.

    ``retrieval`` is fixed to ``FETCHED_VERIFIED`` here because that is the only
    thing this function can honestly claim: it fetched the page and extracted
    from those bytes. A document whose text a human supplied is built through
    ``build_document`` directly, with ``OPERATOR_SUPPLIED`` stated at the call
    site rather than defaulted anywhere.
    """
    page = fetch_page(url)
    every = extract_sections(page.text)
    kept = tuple(section for section in every if section_filter.keep(section))
    named = name_callouts(kept, callout_titles)
    return build_document(
        doc_id=doc_id,
        title=extract_title(page.text),
        page=page,
        sections=named,
        fetched_ts_ist=fetched_ts_ist,
        retrieval=Retrieval.FETCHED_VERIFIED,
    )


def build_document(
    *,
    doc_id: str,
    title: str,
    page: FetchedPage,
    sections: tuple[ExtractedSection, ...],
    fetched_ts_ist: datetime,
    retrieval: Retrieval,
) -> PolicyDocument:
    """Assemble a reviewed set of sections into a corpus document.

    Takes ``sections`` rather than re-extracting them, so the operator can drop
    boilerplate and name callouts between review and commit, and the document is
    built from exactly what was shown to them. Anchors come from
    ``anchor_ids_for``, never from this module, so there is one derivation in
    the codebase.
    """
    if not sections:
        raise FetchError(f"{doc_id}: no sections survived filtering, so there is nothing to anchor")
    unnamed = [section for section in sections if not section.heading.strip()]
    if unnamed:
        raise FetchError(
            f"{doc_id}: {len(unnamed)} section(s) reached build_document with no heading; "
            "run them through name_callouts first"
        )
    anchors = anchor_ids_for([section.heading for section in sections])
    return PolicyDocument(
        doc_id=doc_id,
        title=title,
        source_url=page.url,
        fetched_ts_ist=fetched_ts_ist.isoformat(),
        retrieval_sha256=page.sha256,
        retrieval=retrieval,
        clauses=tuple(
            PolicyClause(
                anchor_id=anchor,
                heading=section.heading,
                text=section.text,
                ordinal=ordinal,
            )
            for ordinal, (anchor, section) in enumerate(zip(anchors, sections, strict=True))
        ),
    )
