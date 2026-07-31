# SPDX-License-Identifier: MIT
"""D2: the pivot-proposal *format* the evidence agent writes to.

Parsing and rendering only. The checking lives in
``orchestrator.proposal_check``, and the split is the same one STEP-03 made for
triage rationales after the import-graph test caught the first draft doing its
own verification: an agent that imports its own verifier is an agent nobody is
verifying.

The contract (STEP-04 3.2)
--------------------------
An agent's output is ``(pivot_kind, params, one-line reason citing existing
pack record ids)``. That is three lines, and the format is deliberately flat:

    PIVOT: infra_overlap
    PARAMS: subject_id=chan_000016; signal_type=any; limit=25
    REASON: the subject carries a device fingerprint seen elsewhere [prov-0001]

Not JSON, and the reason is worth recording. A model emitting JSON has to be
correct about quoting, escaping and nesting before it can be correct about the
pivot, and a parse failure then costs a hop for a reason that has nothing to do
with the investigation. Three prefixed lines fail one field at a time.

Nothing here trusts what it reads. Values arrive as strings, integers are
coerced only when the text is unambiguously an integer, and everything else is
left as a string so that ``pivots.validate_params`` reports a type failure
rather than this module guessing. Parsing is not validation, and keeping them
apart is what lets the validation be the checked thing.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from ts_sentry.agents.citations import parse_citations

__all__ = ["PivotProposal", "parse_proposal", "render_proposal"]

_FIELD_PATTERN = re.compile(r"^(PIVOT|PARAMS|REASON)\s*:\s*(.*)$", re.IGNORECASE)
_INTEGER_PATTERN = re.compile(r"^-?\d+$")


@dataclass(frozen=True, slots=True)
class PivotProposal:
    """One proposal, exactly as the agent expressed it.

    Deliberately untyped beyond this shape: ``pivot_name`` is whatever string
    the model wrote, and ``params`` maps names to whatever it supplied. The
    orchestrator resolves the name through the allowlist and types the values.
    Storing a resolved ``PivotKind`` here would mean this module had already
    made the decision the boundary exists to make.
    """

    pivot_name: str
    params: Mapping[str, object]
    reason: str

    @property
    def cited_ids(self) -> frozenset[str]:
        """Pack record ids the reason points at."""
        return parse_citations(self.reason)

    def to_json_object(self) -> dict[str, object]:
        return {
            "pivot_name": self.pivot_name,
            "params": {name: self.params[name] for name in sorted(self.params)},
            "reason": self.reason,
            "cited_ids": sorted(self.cited_ids),
        }


def _coerce(raw: str) -> object:
    """An integer when the text is unambiguously one, a string otherwise.

    ``"25"`` becomes ``25`` and ``"25x"`` stays ``"25x"``, so the bounds check
    sees an integer where one was meant and a type failure where one was not.
    Guessing more than this would move typing decisions out of the validator
    that is tested for them.
    """
    text = raw.strip()
    if _INTEGER_PATTERN.match(text):
        return int(text)
    return text


def parse_proposal(text: str) -> PivotProposal | None:
    """Read a proposal, or ``None`` if the text is not one.

    Returns ``None`` rather than raising, and rather than returning a partial
    proposal. A half-read proposal is the dangerous shape: it would let a
    missing REASON reach the analyst as an approval request with no stated
    reason, which is exactly the rubber stamp the citation requirement exists
    to prevent.

    Later lines win over earlier ones for the same field, so a model that
    corrects itself mid-response is read as having corrected itself. A model
    that emits two different pivots without correcting is a model whose second
    answer is the one it settled on.
    """
    fields: dict[str, str] = {}
    for line in text.splitlines():
        match = _FIELD_PATTERN.match(line.strip())
        if match is not None:
            fields[match.group(1).upper()] = match.group(2).strip()

    pivot = fields.get("PIVOT", "")
    reason = fields.get("REASON", "")
    if not pivot or not reason:
        return None

    params: dict[str, object] = {}
    for clause in fields.get("PARAMS", "").split(";"):
        name, separator, value = clause.partition("=")
        if not separator:
            continue
        if name.strip():
            params[name.strip()] = _coerce(value)

    return PivotProposal(pivot_name=pivot, params=params, reason=reason)


def render_proposal(proposal: PivotProposal) -> str:
    """The wire form, for prompts that show an example and for tests.

    Round-trips through ``parse_proposal`` for any proposal this system builds,
    which is asserted rather than assumed.
    """
    params = "; ".join(f"{name}={proposal.params[name]}" for name in sorted(proposal.params))
    return "\n".join(
        (
            f"PIVOT: {proposal.pivot_name}",
            f"PARAMS: {params}",
            f"REASON: {proposal.reason}",
        )
    )
