# SPDX-License-Identifier: MIT
"""D4: the memo *format* the agent writes to (STEP-05 D4).

Parsing and rendering only. Every judgment about a memo lives orchestrator-side,
the same split ``agents.evidence.proposal`` makes and for the same reason the
STEP-03 import-graph test found: an agent that imports its own verifier is an
agent nobody is verifying.

The contract
------------
Flat prefixed lines, one sentence per line, in the shape a statement of reasons
takes::

    FACT: Eight accounts share the device fingerprint devhint_t02_000 [node-4].
    FACT: The same accounts share a signup IP bucket [edge-11].
    GROUND: anchor=comment-spam | excerpt=Comment spam: Using high-volume, | \
This conduct is incompatible with the platform's spam policy.
    MEASURE: content_demoted
    REDRESS: The channel owner may appeal through the internal complaint-handling system.

Not JSON, for the reason STEP-04 recorded: a model emitting JSON has to be
correct about quoting, escaping and nesting before it can be correct about the
memo, and a parse failure then costs a step for a reason that has nothing to do
with the case. Prefixed lines fail one field at a time.

``MEASURE`` carries a bare enum value rather than prose, because STEP-05 3.1
limits it to a fixed vocabulary and a sentence the agent phrased freely would be
a sanction it invented. The rendered sentence is built from the enum by this
module, so the memo's measure and the sentence describing it cannot disagree.

The document digest is not the agent's to supply
------------------------------------------------
``GROUND`` names an anchor and an excerpt, and **never a document digest**. The
orchestrator resolves which document an anchor belongs to, because a digest the
agent supplied would let it point a citation at a document nobody checked it
against, and because sixty-four hex characters is not something a model should be
asked to reproduce for a check to pass.

Nothing here trusts what it reads. Values arrive as strings, the measure is
resolved through the enum by the caller, and an unparseable line is dropped
rather than guessed at.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from ts_sentry.agents.citations import parse_citations

__all__ = [
    "DraftLine",
    "DraftMemo",
    "parse_draft",
    "render_draft",
]

_LINE_PATTERN = re.compile(r"^(FACT|GROUND|MEASURE|REDRESS)\s*:\s*(.*)$", re.IGNORECASE)
_FIELD_PATTERN = re.compile(r"^(anchor|excerpt)\s*=\s*(.*)$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class DraftLine:
    """One parsed line, exactly as the agent expressed it.

    Deliberately untyped beyond this shape: ``kind`` is the literal keyword and
    ``anchor`` and ``excerpt`` are whatever strings followed. Resolving the
    anchor to a document, the measure to an enum member and the citation to a
    clause all happen at the orchestrator boundary, which is where the
    decisions belong.
    """

    kind: str
    text: str
    anchor: str | None = None
    excerpt: str | None = None

    @property
    def cited_ids(self) -> frozenset[str]:
        return parse_citations(self.text)

    def to_json_object(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "text": self.text,
            "anchor": self.anchor,
            "excerpt": self.excerpt,
            "cited_ids": sorted(self.cited_ids),
        }


@dataclass(frozen=True, slots=True)
class DraftMemo:
    """A parsed draft, before anything has judged it."""

    lines: tuple[DraftLine, ...]

    @property
    def measure_value(self) -> str | None:
        """The raw measure keyword, or ``None`` if the draft named none.

        The *last* MEASURE line wins, matching ``parse_proposal``'s rule that a
        model correcting itself is read as having corrected itself.
        """
        values = [line.text for line in self.lines if line.kind == "MEASURE"]
        return values[-1] if values else None

    def of_kind(self, kind: str) -> tuple[DraftLine, ...]:
        return tuple(line for line in self.lines if line.kind == kind)

    def to_json_object(self) -> dict[str, object]:
        return {"lines": [line.to_json_object() for line in self.lines]}


def _parse_ground(body: str) -> DraftLine:
    """Read ``anchor=... | excerpt=... | sentence`` in any field order.

    Pipe-separated because an excerpt is verbatim policy text and will contain
    commas, colons and semicolons. Choosing a separator that appears in the data
    is how a parser starts silently truncating the thing it is quoting, and the
    excerpt is the one field where truncation would change what the memo claims
    the policy says.
    """
    anchor: str | None = None
    excerpt: str | None = None
    remainder: list[str] = []

    for part in body.split("|"):
        match = _FIELD_PATTERN.match(part.strip())
        if match is None:
            remainder.append(part.strip())
            continue
        name = match.group(1).lower()
        if name == "anchor":
            anchor = match.group(2).strip()
        else:
            excerpt = match.group(2).strip()

    return DraftLine(
        kind="GROUND",
        text=" ".join(piece for piece in remainder if piece),
        anchor=anchor,
        excerpt=excerpt,
    )


def parse_draft(text: str) -> DraftMemo:
    """Read a draft. Unrecognised lines are dropped, never guessed at.

    Total: returns a ``DraftMemo`` for any input, including input that is not a
    draft at all, which then carries no lines and is refused by the checker with
    a reason code. Returning an empty draft rather than ``None`` keeps the
    refusal in one place instead of splitting it between a parse result and a
    verdict.
    """
    lines: list[DraftLine] = []

    for raw in text.splitlines():
        match = _LINE_PATTERN.match(raw.strip())
        if match is None:
            continue
        kind = match.group(1).upper()
        body = match.group(2).strip()
        if not body:
            continue
        if kind == "GROUND":
            lines.append(_parse_ground(body))
        else:
            lines.append(DraftLine(kind=kind, text=body))

    return DraftMemo(lines=tuple(lines))


def render_draft(lines: Sequence[DraftLine]) -> str:
    """The wire form, for prompts that show an example and for tests.

    Round-trips through ``parse_draft`` for any draft this system builds, which
    is asserted rather than assumed.
    """
    rendered: list[str] = []
    for line in lines:
        if line.kind == "GROUND":
            rendered.append(f"GROUND: anchor={line.anchor} | excerpt={line.excerpt} | {line.text}")
        else:
            rendered.append(f"{line.kind}: {line.text}")
    return "\n".join(rendered)
