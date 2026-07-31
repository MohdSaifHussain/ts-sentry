# SPDX-License-Identifier: MIT
"""The citation syntax every agent writes, in one place.

STEP-03 introduced bracketed citations for triage rationales and STEP-04 needs
the identical syntax for pivot proposals: an agent points at evidence, and the
orchestrator checks the pointer resolves. Two agents parsing the same syntax
with two regexes would be two parsers that can disagree, in the one place where
disagreement means a claim verifies against the wrong set.

So the pattern lives here and both agents import it.
``agents.triage.rationale`` re-exports it, so STEP-03's callers are unchanged.

Why brackets
------------
Ids are cited in square brackets and the parser accepts nothing else. That is
not a formatting preference: an unbracketed convention would make ordinary
prose about "velocity" indistinguishable from a citation of it, and the
verifier would then be checking sentences rather than claims.
"""

import re

__all__ = ["CITATION_PATTERN", "parse_citations"]

CITATION_PATTERN = re.compile(r"\[([A-Za-z0-9._:-]+)\]")
"""One citation. Deliberately narrow: no whitespace, no nesting, so a bracket
in ordinary prose cannot be read as a citation and a citation cannot span a
sentence."""


def parse_citations(text: str) -> frozenset[str]:
    """Every bracketed id in ``text``. Nothing else counts as a citation."""
    return frozenset(CITATION_PATTERN.findall(text))
