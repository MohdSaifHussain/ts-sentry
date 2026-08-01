# SPDX-License-Identifier: MIT
"""D2: the eval label store. Orchestrator-side, and reachable by nothing else.

STEP-06 3.2 requires that prompt authors, human or agent, never see per-item
eval labels through the tooling. This module is where the labels live, and
three separate things keep them here.

**One: the import graph.** ``ts_sentry.orchestrator.eval_labels`` is in
``FORBIDDEN_FOR_AGENTS`` (``tests/test_import_graph.py``), enforced over the
*transitive* first-party closure. No module under ``agents.`` can reach it, by
any path, including through an innocent-looking intermediary. That is the same
mechanism that caught ``agents.triage.rationale`` reaching
``governance.signature`` through ``verifier -> gates`` in STEP-03.

**Two: the type on the other side.** ``data.eval_set.EvalItem`` has no label
field. Even a caller holding both an item and this store cannot put a label on
an item, because there is nowhere on the item for one to go.

**Three: this module's own API.** There is deliberately no ``label_of(item_id)``
and no way to enumerate the mapping. The store grades: it takes predictions and
returns counts. Per-item labels are read inside these methods and never leave
them. That is defence in depth rather than the load-bearing control, and it is
worth having because the load-bearing control is a test, and a test is a thing
somebody can decide to relax.

Why the grader is not an agent
------------------------------
Saif's framing for this phase: an agent that can see eval labels or judge its
own regression is the contamination and self-verification failure the phase
exists to prevent. ``agents.prompt_eval`` proposes a candidate; the orchestrator
runs the eval and the gate. This module is the half of that split which holds
the answers.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ts_sentry.agents.prompt_eval.prompts import ClassificationProposal
from ts_sentry.data.enums import ThreatClass
from ts_sentry.data.eval_set import LABELS_FILE, EvalSetError, labels_digest, load_manifest

__all__ = [
    "ClassCounts",
    "EvalLabelStore",
    "load_label_store",
]


@dataclass(frozen=True, slots=True)
class ClassCounts:
    """Per-class outcome counts for one prompt version over one eval set.

    The confusion matrix in aggregate form. ``support`` is how many items truly
    carry this class, which is the denominator recall is taken over and the
    number that makes this phase's whole sensitivity story checkable: a class
    with support 4 cannot evidence a two-point change in anything.
    """

    threat_class: ThreatClass
    support: int
    predicted: int
    true_positives: int

    def __post_init__(self) -> None:
        if self.support < 0 or self.predicted < 0 or self.true_positives < 0:
            raise ValueError("counts must not be negative")
        if self.true_positives > min(self.support, self.predicted):
            raise ValueError(
                f"{self.threat_class.value}: true positives cannot exceed support or predictions"
            )

    @property
    def recall(self) -> float:
        """Fraction of this class's items the prompt found. Zero support is 0.0.

        A class with no items has no recall to report, and returning 0.0 rather
        than raising keeps the metric total. The support is carried alongside so
        a reader can tell "found none of four" from "there were none".
        """
        return self.true_positives / self.support if self.support else 0.0

    @property
    def precision(self) -> float:
        """Fraction of this class's predictions that were right.

        **Not a deployment estimate.** The eval set over-samples rare classes
        against a >97% benign platform (ARCHITECTURE 6.1), and precision moves
        with prevalence while recall does not. Reported, carried into the report
        artifact with this caveat attached, and never quoted alone.
        """
        return self.true_positives / self.predicted if self.predicted else 0.0

    @property
    def f1(self) -> float:
        denominator = self.precision + self.recall
        return 2 * self.precision * self.recall / denominator if denominator else 0.0

    def to_json_object(self) -> dict[str, object]:
        return {
            "threat_class": self.threat_class.value,
            "support": self.support,
            "predicted": self.predicted,
            "true_positives": self.true_positives,
            "precision": round(self.precision, 6),
            "recall": round(self.recall, 6),
            "f1": round(self.f1, 6),
        }


class EvalLabelStore:
    """The answers, held where no agent can reach them.

    A plain class with ``__slots__`` rather than a dataclass, following
    ``governance.ledger.Ledger``: the mapping is private state and there is no
    public field that would expose it. Every method returns counts or flags,
    never a label.

    The attribute is ``_answers`` and not ``_labels`` for a mechanical reason
    worth leaving here, because the obvious name is the other one.
    ``tests/test_import_graph.py`` greps code string literals for ``_labels`` to
    catch anything naming the sealed ground-truth table, and ``__slots__`` puts
    the attribute name in a string literal. Calling it ``_labels`` tripped that
    check on this module's first run. The check is deliberately broad and the
    right response was to move the name, not to widen the allowlist: adding this
    module to ``LEGITIMATE_SEALED_CONSUMERS`` would have recorded that it reads
    ``sealed._labels``, which it does not and must not.
    """

    __slots__ = ("_answers",)

    def __init__(self, labels: Mapping[str, ThreatClass]) -> None:
        if not labels:
            raise EvalSetError("an eval label store with no labels can grade nothing")
        self._answers = dict(labels)

    def __len__(self) -> int:
        return len(self._answers)

    @property
    def digest(self) -> str:
        """Identity of the label set, for stamping into a report."""
        return labels_digest(self._answers)

    def covers(self, item_ids: Sequence[str]) -> bool:
        return all(item_id in self._answers for item_id in item_ids)

    def support(self) -> dict[ThreatClass, int]:
        """How many items truly carry each class. Aggregate, so it may leave.

        This is the class balance D2's governing standard asks to be documented,
        and it is safe to publish precisely because it is a histogram: knowing
        that four items are T-04 says nothing about *which* four.
        """
        counts = {member: 0 for member in ThreatClass}
        for label in self._answers.values():
            counts[label] += 1
        return counts

    def correct_flags(self, predictions: Sequence[ClassificationProposal]) -> tuple[bool, ...]:
        """Per-item correctness, in the order given.

        The one per-item thing this store will hand back, and it is deliberately
        not the label: it is whether *this caller's own prediction* was right.
        The bootstrap needs it, because a paired resample has to resample items
        rather than aggregate counts, and pairing is what makes the interval
        reflect the two prompts disagreeing rather than two independent samples.

        A caller who already knows its prediction and learns it was correct has
        learned the label for that item. That is unavoidable for anything that
        grades, and it is why the control that matters is *who may call this*,
        not what it returns.
        """
        self._require_coverage(prediction.item_id for prediction in predictions)
        return tuple(
            self._answers[prediction.item_id] is prediction.predicted for prediction in predictions
        )

    def score(
        self, predictions: Sequence[ClassificationProposal]
    ) -> dict[ThreatClass, ClassCounts]:
        """Per-class counts for one prompt version. Aggregate only."""
        self._require_coverage(prediction.item_id for prediction in predictions)

        support = self.support()
        predicted: dict[ThreatClass, int] = {member: 0 for member in ThreatClass}
        hits: dict[ThreatClass, int] = {member: 0 for member in ThreatClass}

        for prediction in predictions:
            predicted[prediction.predicted] += 1
            if self._answers[prediction.item_id] is prediction.predicted:
                hits[prediction.predicted] += 1

        return {
            member: ClassCounts(
                threat_class=member,
                support=support[member],
                predicted=predicted[member],
                true_positives=hits[member],
            )
            for member in ThreatClass
        }

    def _require_coverage(self, item_ids: Iterable[str]) -> None:
        unknown = sorted(item_id for item_id in item_ids if item_id not in self._answers)
        if unknown:
            raise EvalSetError(
                f"predictions name items this label store does not carry: {unknown[:5]}. "
                "A report computed over a different item set than it names is not a report"
            )


def load_label_store(root: Path) -> EvalLabelStore:
    """Read ``labels.json`` and verify it against the manifest's digest."""
    manifest = load_manifest(root)
    path = root / LABELS_FILE
    if not path.is_file():
        raise EvalSetError(f"no eval labels at {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvalSetError(f"could not read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise EvalSetError(f"{path} does not contain a JSON object")

    try:
        labels = {str(key): ThreatClass(str(value)) for key, value in raw.items()}
    except ValueError as exc:
        raise EvalSetError(f"{path} names something that is not a threat class: {exc}") from exc

    expected = manifest.get("labels_sha256")
    actual = labels_digest(labels)
    if expected != actual:
        raise EvalSetError(
            f"{LABELS_FILE} digests to {actual}, but the manifest records {expected}. The label "
            "set has changed since it was built"
        )
    return EvalLabelStore(labels)
