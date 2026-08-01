# SPDX-License-Identifier: MIT
"""D3: the eval harness. Metrics, and a paired bootstrap over the deltas.

Orchestrator-side, and that placement is the phase's central governance claim:
``agents.prompt_eval`` proposes a candidate, and everything that *judges* one
lives here. An agent that graded its own successor would be the
self-verification failure this phase exists to prevent.

Why the interval is paired
--------------------------
The question a regression gate asks is not "how good is the candidate" but "is
the candidate worse than the incumbent". Those need different arithmetic. Two
independent confidence intervals on two recalls can overlap while the *paired*
difference is dead certain, because both prompts are being scored on the **same
items**: if they agree on every item but four, only those four carry any
information about the difference, and an unpaired interval throws that away.

So the resampling unit is the item, and each resample carries both prompts'
outcomes on that item together. What the interval then reflects is how much of
the observed difference could be an accident of which items happen to be in the
eval set.

Percentile bootstrap rather than a normal approximation
-------------------------------------------------------
Deliberate, and it matters at the sizes this project actually has. A normal
approximation would put a symmetric interval around the point estimate, which
is wrong in both directions here:

* When two prompts agree on every item, the true interval is exactly
  ``[0, 0]``. A normal approximation with a plug-in variance also gives zero
  width, so that case is fine either way.
* When a prompt **collapses a class**, every item moves the same way and every
  resample gives the same delta, so the percentile interval is ``[-1, -1]`` and
  the refusal is decisive. A normal approximation would report a symmetric
  interval around -1 with nonzero width, which would claim uncertainty about
  something the data settles completely, and part of that interval would sit
  below -1, which is not a value a recall difference can take.

The bootstrap gets both right by construction, because it only ever reports
differences it actually observed.

Determinism
-----------
One seeded ``numpy.random.Generator``, supplied by the caller, never created
here. The seed is stamped into the report (D6), and the report is what the gate
reads, so the gate stays a pure function of ``(report, tolerances)`` per 3.5
while the randomness stays upstream of it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from ts_sentry.data.enums import ThreatClass
from ts_sentry.orchestrator.eval_labels import ClassCounts, EvalLabelStore

__all__ = [
    "DEFAULT_CONFIDENCE",
    "DEFAULT_RESAMPLES",
    "ClassDelta",
    "EvalReport",
    "VersionMetrics",
    "bootstrap_delta",
    "build_report",
]

DEFAULT_RESAMPLES = 2000
"""Bootstrap resamples.

Enough that the 2.5th and 97.5th percentiles are stable to about a thousandth,
which is finer than any tolerance this project can justify at its eval-set size.
More would be arithmetic nobody reads; fewer would make the interval itself
noisy, and an interval whose width moves between runs of the same inputs would
break the reproducibility every other number here has.
"""

DEFAULT_CONFIDENCE = 0.95


@dataclass(frozen=True, slots=True)
class ClassDelta:
    """The candidate-minus-incumbent recall change for one class, with its interval.

    ``lower`` is the number the gate reads. Everything else is for the reader.
    """

    threat_class: ThreatClass
    support: int
    incumbent_recall: float
    candidate_recall: float
    delta: float
    lower: float
    upper: float
    resamples: int
    confidence: float
    discordant: int

    def __post_init__(self) -> None:
        if self.support < 0:
            raise ValueError("support must not be negative")
        if not 0.0 < self.confidence < 1.0:
            raise ValueError(f"confidence must lie in (0, 1); got {self.confidence}")
        if self.lower > self.upper:
            raise ValueError(
                f"{self.threat_class.value}: interval lower bound {self.lower} exceeds "
                f"upper bound {self.upper}"
            )

    @property
    def half_width(self) -> float:
        """How precisely this class can measure anything at all.

        Reported per class because it is the honest answer to "why did the gate
        refuse a candidate that looks fine": at support 4, a single item moving
        is a quarter of the recall, and no amount of resampling invents
        precision the items do not carry.
        """
        return (self.upper - self.lower) / 2.0

    def to_json_object(self) -> dict[str, object]:
        return {
            "threat_class": self.threat_class.value,
            "support": self.support,
            "incumbent_recall": round(self.incumbent_recall, 6),
            "candidate_recall": round(self.candidate_recall, 6),
            "delta": round(self.delta, 6),
            "ci_lower": round(self.lower, 6),
            "ci_upper": round(self.upper, 6),
            "ci_half_width": round(self.half_width, 6),
            "discordant_items": self.discordant,
            "resamples": self.resamples,
            "confidence": self.confidence,
        }


def bootstrap_delta(
    threat_class: ThreatClass,
    paired: Sequence[tuple[bool, bool]],
    *,
    rng: np.random.Generator,
    resamples: int = DEFAULT_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
) -> ClassDelta:
    """Percentile interval on the recall delta for one class.

    ``paired`` is one ``(incumbent_correct, candidate_correct)`` per item that
    truly carries ``threat_class``. For such an item, "correct" and "recalled"
    are the same event, which is what lets recall be a mean over this vector.

    A class with no items returns a zero delta and a zero-width interval. That
    is not a claim that the two prompts are equal on it; it is the absence of
    any claim, and the ``support`` of 0 travelling alongside is what tells a
    reader which of the two they are looking at.
    """
    support = len(paired)
    if support == 0:
        return ClassDelta(
            threat_class=threat_class,
            support=0,
            incumbent_recall=0.0,
            candidate_recall=0.0,
            delta=0.0,
            lower=0.0,
            upper=0.0,
            resamples=resamples,
            confidence=confidence,
            discordant=0,
        )

    incumbent = np.array([pair[0] for pair in paired], dtype=float)
    candidate = np.array([pair[1] for pair in paired], dtype=float)
    difference = candidate - incumbent

    draws = rng.integers(0, support, size=(resamples, support))
    resampled = difference[draws].mean(axis=1)

    alpha = 1.0 - confidence
    lower = float(np.quantile(resampled, alpha / 2.0))
    upper = float(np.quantile(resampled, 1.0 - alpha / 2.0))

    return ClassDelta(
        threat_class=threat_class,
        support=support,
        incumbent_recall=float(incumbent.mean()),
        candidate_recall=float(candidate.mean()),
        delta=float(difference.mean()),
        lower=lower,
        upper=upper,
        resamples=resamples,
        confidence=confidence,
        discordant=int(np.count_nonzero(difference)),
    )


@dataclass(frozen=True, slots=True)
class VersionMetrics:
    """One prompt version's per-class counts, plus what could not be parsed.

    ``unparseable`` is first-class rather than folded into the error count, on
    the reasoning recorded at ``ClassificationParseError``: a wrong class is a
    classifier performing badly and an unparseable answer is a prompt that
    stopped producing the shape its consumers were built for. Reporting the
    second as the first would let a prompt that broke its own output contract
    look merely less accurate.
    """

    content_digest: str
    counts: Mapping[ThreatClass, ClassCounts]
    unparseable: int

    def macro_f1(self) -> float:
        """Unweighted mean F1 over classes that have items.

        Macro rather than micro, and the choice is not cosmetic at this class
        balance: micro averaging is dominated by whichever class has the most
        items, and with benign at 25% and four items in some threat classes,
        a micro number would mostly report how well benign was handled.
        """
        scored = [entry.f1 for entry in self.counts.values() if entry.support]
        return sum(scored) / len(scored) if scored else 0.0

    def to_json_object(self) -> dict[str, object]:
        return {
            "content_digest": self.content_digest,
            "macro_f1": round(self.macro_f1(), 6),
            "unparseable_answers": self.unparseable,
            "per_class": [
                self.counts[member].to_json_object()
                for member in ThreatClass
                if self.counts[member].support
            ],
        }


@dataclass(frozen=True, slots=True)
class EvalReport:
    """Everything the gate reads, and everything a reader needs to check it.

    This is the boundary artifact of STEP-06 3.2: it carries per-class counts
    and intervals and **no per-item rows**. An eval report is what leaves the
    eval boundary, so anything on it is something a prompt author may see.
    """

    task: str
    incumbent: VersionMetrics
    candidate: VersionMetrics
    deltas: tuple[ClassDelta, ...]
    item_count: int
    items_sha256: str
    labels_sha256: str
    adapter_id: str
    model_id: str
    bootstrap_seed: int

    def __post_init__(self) -> None:
        if not self.deltas:
            raise ValueError("a report with no per-class deltas cannot be gated")
        if self.item_count <= 0:
            raise ValueError("a report over no items is not a report")

    def delta_for(self, threat_class: ThreatClass) -> ClassDelta:
        for entry in self.deltas:
            if entry.threat_class is threat_class:
                return entry
        raise KeyError(f"no delta recorded for {threat_class.value}")

    def to_json_object(self) -> dict[str, object]:
        return {
            "task": self.task,
            "item_count": self.item_count,
            "items_sha256": self.items_sha256,
            "labels_sha256": self.labels_sha256,
            "adapter_id": self.adapter_id,
            "model_id": self.model_id,
            "bootstrap_seed": self.bootstrap_seed,
            "incumbent": self.incumbent.to_json_object(),
            "candidate": self.candidate.to_json_object(),
            "deltas": [entry.to_json_object() for entry in self.deltas],
        }


def build_report(
    store: EvalLabelStore,
    *,
    task: str,
    incumbent_digest: str,
    candidate_digest: str,
    incumbent_predictions: Sequence[object],
    candidate_predictions: Sequence[object],
    incumbent_unparseable: int,
    candidate_unparseable: int,
    item_count: int,
    items_sha256: str,
    adapter_id: str,
    model_id: str,
    bootstrap_seed: int,
    resamples: int = DEFAULT_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
) -> EvalReport:
    """Grade both versions and interval every per-class delta.

    The predictions are typed ``object`` here and narrowed by the label store,
    for the reason ``ToolResources.pack`` is: this function is about reports,
    and pinning the agent's proposal type into its signature would couple the
    reporting layer to one agent's schema.
    """
    from ts_sentry.agents.prompt_eval.prompts import ClassificationProposal

    incumbent = [p for p in incumbent_predictions if isinstance(p, ClassificationProposal)]
    candidate = [p for p in candidate_predictions if isinstance(p, ClassificationProposal)]

    rng = np.random.default_rng(bootstrap_seed)
    paired = store.paired_class_correctness(incumbent, candidate)
    deltas = tuple(
        bootstrap_delta(member, paired[member], rng=rng, resamples=resamples, confidence=confidence)
        for member in ThreatClass
        if paired[member]
    )

    return EvalReport(
        task=task,
        incumbent=VersionMetrics(
            content_digest=incumbent_digest,
            counts=store.score(incumbent),
            unparseable=incumbent_unparseable,
        ),
        candidate=VersionMetrics(
            content_digest=candidate_digest,
            counts=store.score(candidate),
            unparseable=candidate_unparseable,
        ),
        deltas=deltas,
        item_count=item_count,
        items_sha256=items_sha256,
        labels_sha256=store.digest,
        adapter_id=adapter_id,
        model_id=model_id,
        bootstrap_seed=bootstrap_seed,
    )
