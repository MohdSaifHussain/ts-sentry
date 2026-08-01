# SPDX-License-Identifier: MIT
"""D4: the regression gate (STEP-06 3.3-3.5, ARCHITECTURE 4.4, A-05).

"Activation is refused if any monitored metric drops beyond a declared
tolerance. Refusals are ledgered." This module is the refusal. It does not
ledger, and that separation is deliberate.

Pure, total, and that is a testable property
--------------------------------------------
``decide(report, tolerances)`` is a pure function: no I/O, no clock, no
randomness, no ledger write, and no exception on any well-formed input. It is
the ``mandate.validate`` precedent exactly (STEP-02: "Pure and total... The
orchestrator ledgers the refusal; keeping that out of here is what makes this
function testable in isolation and safe to call from a gate").

That is what makes STEP-06 3.5's hypothesis property meaningful: the same
``(report, tolerances)`` yields the same verdict, always, because there is
nothing else in scope for it to depend on. The bootstrap that produced the
intervals ran upstream, in D3, and its seed is stamped in the report.

Fail-closed: activation requires evidence of non-regression
------------------------------------------------------------
Saif's decision D. The gate reads the **lower bound** of the confidence
interval on each per-class recall delta, not the point estimate. A candidate is
activatable only if, for every class, the interval excludes a drop beyond
tolerance.

The consequence is intended: a candidate whose point estimate looks fine but
whose interval is wide is **refused**, because the eval set could not establish
that it was not worse. That is the same posture as the seed guard, which
refuses an investigation of an entity nobody proved exists (DECISIONS 4.15), and
the claim verifier, which refuses a sentence nobody proved resolves. Absence of
evidence of regression is not evidence of absence.

Two refusals that must not be conflated
---------------------------------------
Both refuse, and a metric that counted them together would be useless, on the
reasoning DECISIONS 5.21 recorded for the overclaim fixtures:

* ``RECALL_REGRESSION`` - the candidate is *measurably* worse. The observed
  drop itself breaches tolerance and the interval agrees.
* ``REGRESSION_NOT_EXCLUDED`` - the candidate looks fine and the eval set
  cannot prove it. The observed drop is within tolerance; the interval is too
  wide to rule out a breach.

The first says fix the prompt. The second says the eval set is too small to
answer, which on this project's data is the common case and is a fact about the
generator rather than about the candidate. Reporting the second as the first
would blame a prompt for the eval set's resolution.

The tolerance is declared, never derived here
----------------------------------------------
``minimum_detectable_drop`` reports what this eval set could resolve, and it is
**reporting only**. The tolerance is a config value a human sets once, having
read that number. Deriving the tolerance from the report *at gate time* would
let a candidate widen its own acceptance criterion by being noisy, which is a
gate that passes everything and calls it rigour. The two are kept apart in
code, not just in intent: ``decide`` never reads
``minimum_detectable_drop``.

What this eval set can actually detect
--------------------------------------
Measured in D2 and carried here because this is where a reader will ask: the
generator plants 4 to 12 entities per threat class regardless of ``--scale``,
so a class's recall moves in steps of a quarter to a twelfth. This gate
therefore detects a **class collapse**, not a few-point drift. That bound comes
from the data, not from this module's design, and no tolerance setting can move
it.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ts_sentry.data.enums import ThreatClass
from ts_sentry.governance.canonical import digest_fields
from ts_sentry.orchestrator.prompt_eval import ClassDelta, EvalReport

__all__ = [
    "TOLERANCES_FILE",
    "ActivationDecision",
    "Breach",
    "BreachCode",
    "GateVerdict",
    "ToleranceError",
    "Tolerances",
    "decide",
    "load_tolerances",
    "minimum_detectable_drop",
]

TOLERANCES_FILE = "tolerances.json"


class ToleranceError(Exception):
    """Raised when declared tolerances are missing or unreadable.

    A gate with no declared limits does not fall back to a default. That is
    ``GateChecks`` having no defaults (DECISIONS 2.5) applied one level up: an
    unconfigured gate must refuse to run rather than silently pick a number,
    because the number it picked would be the one nobody reviewed.
    """


class ActivationDecision(StrEnum):
    """Whether a candidate may become the incumbent.

    Named apart from ``governance.gates.GateDecision`` (ACCEPTED/REJECTED) on
    purpose. That gate judges one artifact against its own schema; this one
    judges a version against its predecessor, and a shared vocabulary would
    invite a reader to think one implies the other.
    """

    ACTIVATABLE = "activatable"
    REFUSED = "refused"


class BreachCode(StrEnum):
    """Why activation was refused. Countable by cause, like ``RefusalCode``."""

    RECALL_REGRESSION = "recall_regression"
    REGRESSION_NOT_EXCLUDED = "regression_not_excluded"
    MACRO_F1_REGRESSION = "macro_f1_regression"
    OUTPUT_CONTRACT_BROKEN = "output_contract_broken"


@dataclass(frozen=True, slots=True)
class Tolerances:
    """The declared limits, as config (STEP-06 3.3).

    A frozen value with a digest, so a tolerance change is a hash change. The
    digest binds into ``SESSION_OPEN`` rather than becoming a twelfth
    ``EventType``, per DECISIONS 5.8: a tolerance set is build-time policy
    rather than a session action, and binding it at open ties every eval run
    permanently to the limits it ran under, hash-chained.
    """

    recall_drop: float
    macro_f1_drop: float
    max_unparseable: int

    def __post_init__(self) -> None:
        if not 0.0 <= self.recall_drop <= 1.0:
            raise ValueError(
                f"recall_drop is an absolute recall difference; got {self.recall_drop}"
            )
        if not 0.0 <= self.macro_f1_drop <= 1.0:
            raise ValueError(
                f"macro_f1_drop is an absolute F1 difference; got {self.macro_f1_drop}"
            )
        if self.max_unparseable < 0:
            raise ValueError("max_unparseable must not be negative")

    @property
    def digest(self) -> str:
        return digest_fields(
            "ts-sentry/eval-tolerances/v1",
            f"recall_drop={self.recall_drop!r}",
            f"macro_f1_drop={self.macro_f1_drop!r}",
            f"max_unparseable={self.max_unparseable}",
        )

    def to_json_object(self) -> dict[str, object]:
        return {
            "recall_drop": self.recall_drop,
            "macro_f1_drop": self.macro_f1_drop,
            "max_unparseable": self.max_unparseable,
            "tolerances_sha256": self.digest,
        }


@dataclass(frozen=True, slots=True)
class Breach:
    """One reason activation was refused, naming the class it happened on."""

    code: BreachCode
    threat_class: ThreatClass | None
    observed: float
    bound: float
    tolerance: float
    detail: str

    def to_json_object(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "threat_class": None if self.threat_class is None else self.threat_class.value,
            "observed": round(self.observed, 6),
            "bound": round(self.bound, 6),
            "tolerance": self.tolerance,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class GateVerdict:
    """The decision, and every breach behind it.

    Structured rather than a bool, and returned rather than raised, on STEP-02
    2.4's reasoning: a governance layer that signals refusal by throwing is one
    whose refusals can be swallowed by an ``except``.
    """

    decision: ActivationDecision
    breaches: tuple[Breach, ...]
    tolerances_sha256: str
    task: str

    def __post_init__(self) -> None:
        activatable = self.decision is ActivationDecision.ACTIVATABLE
        if activatable is bool(self.breaches):
            raise ValueError(
                "an ACTIVATABLE verdict carries no breaches; a REFUSED one carries at least one"
            )

    @property
    def activatable(self) -> bool:
        return self.decision is ActivationDecision.ACTIVATABLE

    def to_json_object(self) -> dict[str, object]:
        return {
            "task": self.task,
            "decision": self.decision.value,
            "tolerances_sha256": self.tolerances_sha256,
            "breaches": [breach.to_json_object() for breach in self.breaches],
        }


def _recall_breach(delta: ClassDelta, tolerance: float) -> Breach | None:
    """The per-class rule, and the whole of decision D in four lines.

    Refuse unless the interval excludes a drop beyond tolerance. Which of the
    two refusals it is depends on whether the *observed* drop breaches too.
    """
    if delta.support == 0 or delta.lower >= -tolerance:
        return None

    measurably_worse = delta.delta < -tolerance
    code = BreachCode.RECALL_REGRESSION if measurably_worse else BreachCode.REGRESSION_NOT_EXCLUDED
    detail = (
        (
            f"{delta.threat_class.value}: recall fell {abs(delta.delta):.3f} "
            f"({delta.incumbent_recall:.3f} to {delta.candidate_recall:.3f}) on {delta.support} "
            f"item(s), and the {delta.confidence:.0%} interval lower bound {delta.lower:.3f} "
            f"is beyond the tolerated drop of {tolerance:.3f}"
        )
        if measurably_worse
        else (
            f"{delta.threat_class.value}: the observed change {delta.delta:+.3f} is within "
            f"tolerance, but the {delta.confidence:.0%} interval reaches {delta.lower:.3f}, so a "
            f"drop beyond {tolerance:.3f} cannot be excluded on {delta.support} item(s). "
            "Activation requires evidence of non-regression, not absence of evidence of "
            "regression"
        )
    )
    return Breach(
        code=code,
        threat_class=delta.threat_class,
        observed=delta.delta,
        bound=delta.lower,
        tolerance=tolerance,
        detail=detail,
    )


def decide(report: EvalReport, tolerances: Tolerances) -> GateVerdict:
    """Whether ``report``'s candidate may be activated. Pure and total.

    Every monitored metric is checked and every breach is collected, rather than
    returning on the first. A refusal report naming one class when three
    regressed would send its reader back for a second run to discover the
    others, and the per-class breach report is the phase's exit criterion.
    """
    breaches: list[Breach] = []

    for delta in report.deltas:
        breach = _recall_breach(delta, tolerances.recall_drop)
        if breach is not None:
            breaches.append(breach)

    macro_change = report.candidate.macro_f1() - report.incumbent.macro_f1()
    if macro_change < -tolerances.macro_f1_drop:
        breaches.append(
            Breach(
                code=BreachCode.MACRO_F1_REGRESSION,
                threat_class=None,
                observed=macro_change,
                bound=macro_change,
                tolerance=tolerances.macro_f1_drop,
                detail=(
                    f"macro F1 fell {abs(macro_change):.3f} "
                    f"({report.incumbent.macro_f1():.3f} to {report.candidate.macro_f1():.3f}), "
                    f"beyond the tolerated {tolerances.macro_f1_drop:.3f}. Reported as a point "
                    "estimate: this is an aggregate over classes rather than a per-item quantity, "
                    "so the paired item bootstrap does not apply to it"
                ),
            )
        )

    if report.candidate.unparseable > tolerances.max_unparseable:
        breaches.append(
            Breach(
                code=BreachCode.OUTPUT_CONTRACT_BROKEN,
                threat_class=None,
                observed=float(report.candidate.unparseable),
                bound=float(report.candidate.unparseable),
                tolerance=float(tolerances.max_unparseable),
                detail=(
                    f"{report.candidate.unparseable} answer(s) could not be parsed as a threat "
                    f"class, above the tolerated {tolerances.max_unparseable}. A prompt that stops "
                    "producing the shape its consumers were built for has broken its output "
                    "contract, which is a different failure from classifying badly"
                ),
            )
        )

    return GateVerdict(
        decision=ActivationDecision.REFUSED if breaches else ActivationDecision.ACTIVATABLE,
        breaches=tuple(breaches),
        tolerances_sha256=tolerances.digest,
        task=report.task,
    )


def minimum_detectable_drop(report: EvalReport) -> Mapping[ThreatClass, float]:
    """Per class, the smallest recall drop this eval set could establish.

    **Reporting only.** Never read by ``decide``, and the separation is the
    control rather than a convention: a gate that set its own tolerance from the
    report would widen its acceptance criterion exactly when the evidence got
    weaker, which is a gate that passes everything and calls it rigour.

    The number is the interval's half-width. A tolerance below it cannot be
    cleared by any candidate that differs from the incumbent at all, because the
    interval alone is wider than the limit; a tolerance at or above it is
    something a candidate can actually satisfy. That is what makes this the
    right figure for a human to set the tolerance from, once, having seen it.

    Measured on the committed eval set during D4, against a candidate that
    differs from the incumbent: half-widths ran 0.167 to 0.500 across the
    classes that moved. Against an *identical* candidate every class reported
    0.000, because a percentile bootstrap over a difference vector of zeros can
    only ever resample zeros. So an unchanged prompt is activatable at any
    tolerance, and a changed one is judged at the resolution its class support
    allows.
    """
    return {delta.threat_class: delta.half_width for delta in report.deltas if delta.support}


def load_tolerances(path: Path) -> Tolerances:
    """Read declared tolerances from config. No defaults, no fallback."""
    if not path.is_file():
        raise ToleranceError(
            f"no declared tolerances at {path}. The regression gate has no default limits: "
            "a gate that invented its own would be enforcing a number nobody reviewed"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToleranceError(f"could not read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ToleranceError(f"{path} does not contain a JSON object")

    try:
        return Tolerances(
            recall_drop=float(raw["recall_drop"]),
            macro_f1_drop=float(raw["macro_f1_drop"]),
            max_unparseable=int(raw["max_unparseable"]),
        )
    except KeyError as exc:
        raise ToleranceError(f"{path} is missing {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise ToleranceError(f"{path} carries an unusable tolerance: {exc}") from exc
