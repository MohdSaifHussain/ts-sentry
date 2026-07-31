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

The pattern itself moved to :mod:`ts_sentry.agents.citations` in STEP-04, when
the evidence agent needed the identical syntax for pivot proposals. It is
re-exported here, so every STEP-03 caller is unchanged. Two agents parsing one
syntax with two regexes would be two parsers that can disagree, in the one
place where disagreement means a claim verifies against the wrong set.
"""

from collections.abc import Sequence

from ts_sentry.agents.citations import CITATION_PATTERN, parse_citations
from ts_sentry.agents.triage.scorer import (
    WEIGHTS,
    PriorityScore,
    ScoreComponent,
    component_id,
)

__all__ = [
    "CITATION_PATTERN",
    "discriminating_component",
    "parse_citations",
    "parse_rationale_lines",
    "render_expected_form",
]


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


def discriminating_component(scores: Sequence[PriorityScore], index: int) -> ScoreComponent:
    """The component that most separates this case from its rank-neighbours.

    Not the largest component, which is what an obvious implementation cites
    and what the first version of this agent did. Saif found the failure by
    reading a real ranked queue: every rationale cited ``severity_class``,
    because severity was the biggest number on every row, and at equal
    severity it was the one thing that explained *nothing* about why this case
    outranked the next one. A citation identical across the whole queue
    carries no information even though it verifies perfectly.

    So the choice is comparative. Each component is scored by how far it
    deviates from the same component on the neighbouring rows, weighted by
    what that component is worth in the priority, and the largest deviation
    wins. That surfaces velocity where velocity moved the case and spread
    where spread did.

    Fallbacks, in order, so the function is total: if the neighbourhood is
    uniform, use the component that varies most across the whole queue; if the
    queue itself is uniform, use the largest weighted component. Ties break on
    ``ScoreComponent`` declaration order, so the result is deterministic.

    This changes what the agent is *asked* to cite, not what the verifier
    accepts. Any resolvable citation is still valid; this only decides which
    one is worth making.
    """
    if not scores:
        raise ValueError("cannot choose a component for an empty queue")
    item = scores[index]
    neighbours = [scores[i] for i in (index - 1, index + 1) if 0 <= i < len(scores)]

    def weighted_deviation(component: ScoreComponent) -> float:
        if not neighbours:
            return 0.0
        mean = sum(n.components[component] for n in neighbours) / len(neighbours)
        return abs(item.components[component] - mean) * WEIGHTS[component]

    best = max(ScoreComponent, key=weighted_deviation)
    if weighted_deviation(best) > 0.0:
        return best

    def queue_spread(component: ScoreComponent) -> float:
        values = [s.components[component] for s in scores]
        return (max(values) - min(values)) * WEIGHTS[component]

    widest = max(ScoreComponent, key=queue_spread)
    if queue_spread(widest) > 0.0:
        return widest

    return max(ScoreComponent, key=lambda c: item.components[c] * WEIGHTS[c])


def render_expected_form(scores: Sequence[PriorityScore]) -> str:
    """The citation menu handed to the model with the task.

    Listing the legal ids explicitly is the cheapest way to make the contract
    followable. It is not the control - the verifier is - but a model that is
    told exactly what it may cite fails the check less often, and every failure
    costs a turn.

    Each line also names the component that most differentiates that case from
    its neighbours. That is guidance, not a constraint: the verifier accepts
    any resolvable citation, and a model with a better reason to cite
    something else is free to use it.
    """
    lines = []
    for index, item in enumerate(scores):
        ids = ", ".join(
            f"[{component_id(item.case_id, component)}]" for component in ScoreComponent
        )
        focus = component_id(item.case_id, discriminating_component(scores, index))
        lines.append(f"{item.case_id}: cite only {ids}; most distinguishing: [{focus}]")
    return "\n".join(lines)
