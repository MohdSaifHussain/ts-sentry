# SPDX-License-Identifier: MIT
"""D5: the rationale *format* the triage agent writes to.

Parsing and rendering only. The checking lives in
``orchestrator.rationale_check``, and the split is not filing: an agent that
imports its own verifier is an agent that is not being verified. The
import-graph test caught the first draft of this module doing exactly that,
reaching ``governance.signature`` through ``verifier -> gates``, and the fix
was to move the judgment rather than to widen the rule.

What the citation syntax is doing
---------------------------------
Ids are cited in square brackets, and the parser accepts nothing else. That
is not a formatting preference: an unbracketed convention would make ordinary
prose about "velocity" indistinguishable from a citation of it, and the
verifier would then be checking sentences rather than claims.
"""

import re
from collections.abc import Sequence

from ts_sentry.agents.triage.scorer import PriorityScore, ScoreComponent, component_id

__all__ = [
    "CITATION_PATTERN",
    "parse_citations",
    "parse_rationale_lines",
    "render_expected_form",
]

CITATION_PATTERN = re.compile(r"\[([A-Za-z0-9._:-]+)\]")
"""One citation. Deliberately narrow: no whitespace, no nesting, so a bracket
in ordinary prose cannot be read as a citation and a citation cannot span a
sentence."""


def parse_citations(text: str) -> frozenset[str]:
    """Every bracketed id in ``text``. Nothing else counts as a citation."""
    return frozenset(CITATION_PATTERN.findall(text))


def parse_rationale_lines(text: str) -> dict[str, str]:
    """Split model output into one line per case id.

    A line belongs to the case whose id it starts with. Anything else is
    discarded rather than guessed at: an unattributable line would otherwise
    be assigned to whichever case happened to be next, and the verifier would
    then check the wrong row's citations.
    """
    lines: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        head, separator, _ = line.partition(":")
        case_id = head.strip()
        if separator and case_id.startswith("case-"):
            lines[case_id] = line
    return lines


def render_expected_form(scores: Sequence[PriorityScore]) -> str:
    """The citation menu handed to the model with the task.

    Listing the legal ids explicitly is the cheapest way to make the contract
    followable. It is not the control - the verifier is - but a model that is
    told exactly what it may cite fails the check less often, and every failure
    costs a turn.
    """
    lines = []
    for item in scores:
        ids = ", ".join(
            f"[{component_id(item.case_id, component)}]" for component in ScoreComponent
        )
        lines.append(f"{item.case_id}: cite only {ids}")
    return "\n".join(lines)
