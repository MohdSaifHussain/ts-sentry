# SPDX-License-Identifier: MIT
"""STEP-07 D3: the workflow lens.

Two kinds of number live here and they are kept apart everywhere, in the types
and in the rendering, because conflating them is the specific dishonesty this
phase is most exposed to:

* **Measured.** Counted from real session artifacts. Governance activity
  (gate rejections, mandate violation attempts, verification outcomes, prompt
  injection signals), memo verification pass rate, and evidence recovery at a
  pivot budget. These are facts about runs that happened.
* **Modelled.** The analyst-minutes comparison. Every input is a stated
  assumption, none of it is measured, and it is reported as a sensitivity model
  over an assumption table rather than as a result.

The analyst-minutes model, and why it is not a benchmark
--------------------------------------------------------
STEP-07 D3 asks for an "analyst-minutes model (baseline vs assisted, assumptions
table)". It is a model, and the reason it cannot be anything better is worth
stating rather than leaving as a limitation nobody reads.

**There is no published per-case review-time benchmark to compare against.** The
Trust & Safety Professional Association, which is the body that defines the
metric, describes it and then warns against exactly the comparison a benchmark
would invite:

    "Review time refers to the amount of time taken to complete a review. This
    may include time waiting in queues or going through automatic processes, or
    only the time when the reviewer is actively working on a specific review."

    "There is considerable industry variation in both precise definitions and
    naming conventions, including the same or similar names being used for
    different metrics. Please use caution when looking at terminology from
    different platforms."

    "It is typical for reviews and processes on different types of content and
    with different levels of scrutiny to take substantially different amounts of
    time, so averages are mostly used on subsets where reviews are broadly
    similar to ensure fair comparisons and reduce volatility."

    - https://www.tspa.org/curriculum/ts-fundamentals/content-moderation-and-operations/metrics-for-content-moderation/
      and https://www.tspa.org/curriculum/ts-curriculum/how-trust-safety-teams-use-data/common-types-of-data-in-trust-safety/

So the metric's own definition is not fixed across platforms, and the same name
can mean the queue wait or the active handling time. A number lifted from one
platform and set beside a number from this workbench would not be measuring the
same quantity. The Digital Trust & Safety Partnership does not close the gap
either: its Safe Framework assesses trust and safety practice against a
five-level maturity scale from ad hoc to optimized, which is a qualitative
assessment rather than a source of quantitative handling-time figures
(<https://dtspartnership.org/best-practices/>).

What follows from that, and is enforced by the shape of this module rather than
by anyone's restraint:

* baseline and assisted times are **tunable assumptions in a visible table**,
  carried with every result and rendered above it;
* the model reports the delta **and its sensitivity**, never a headline figure;
* the honest summary statistic is the **break-even**: the assisted time at which
  the delta vanishes. It converts "we think this saves time" into "this saves
  time only if assisted handling is under N minutes, which nobody here has
  measured";
* no causal language, per 3.5. The model states what follows *from the
  assumptions*, and ``render`` is asserted against a banned-phrase list so a
  future edit cannot quietly reintroduce "reduces", "saves" or "improves".

Governance activity is mandatory, including when it is zero
------------------------------------------------------------
3.3 requires the governance sections to render even when every count is zero,
with an explicit note that zero means the gates went untested in that session.
A governance layer that never fired is not a governance layer that works; it is
one nothing tried. That note is part of the rendering, not an optional caveat.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ts_sentry.governance.ledger import EventType

__all__ = [
    "BANNED_CAUSAL_PHRASES",
    "DEFAULT_ASSUMPTIONS",
    "NO_BENCHMARK_NOTE",
    "AnalystMinutesModel",
    "GovernanceActivity",
    "MinutesResult",
    "SensitivitySpan",
    "SessionCounts",
    "TimeAssumption",
    "read_session_counts",
]

NO_BENCHMARK_NOTE = (
    "No published per-case review-time benchmark exists to compare these figures "
    "against. TSPA, which defines the metric, notes that review time 'may include "
    "time waiting in queues or going through automatic processes, or only the time "
    "when the reviewer is actively working on a specific review', and warns that "
    "'there is considerable industry variation in both precise definitions and "
    "naming conventions'. Every minute figure below is a stated assumption, not a "
    "measurement, and the delta is a property of the assumption table."
)
"""Carried into every rendering of the minutes model, by construction.

Sourced rather than asserted, because "there is no benchmark" is itself a claim
and this project does not make claims it cannot cite.
"""

BANNED_CAUSAL_PHRASES = (
    "saves",
    "saved",
    "savings",
    "reduces",
    "reduced",
    "speeds up",
    "faster",
    "improves",
    "improved",
    "efficiency gain",
    "productivity gain",
    "because of the workbench",
)
"""Language the workflow lens may not use about itself (3.5).

A model over assumed inputs cannot establish that anything caused anything. The
list is enforced by a test over ``render`` output rather than by a convention,
because prose drifts and the drift is always in the flattering direction.
"""


@dataclass(frozen=True, slots=True)
class TimeAssumption:
    """One step of the analyst workflow, with assumed times for both arms.

    Both fields are assumptions. There is no measured variant of this type, and
    that absence is deliberate: a field named ``measured_minutes`` would invite
    someone to fill it with a number from a source that does not mean the same
    thing.
    """

    step: str
    baseline_minutes: float
    assisted_minutes: float
    rationale: str

    def __post_init__(self) -> None:
        if not self.step.strip():
            raise ValueError("an assumption needs a step name; it is printed in the table")
        if not self.rationale.strip():
            raise ValueError(
                f"assumption {self.step!r} needs a rationale; an unexplained number in an "
                "assumptions table is a number a reader cannot argue with"
            )
        for name in ("baseline_minutes", "assisted_minutes"):
            value: float = getattr(self, name)
            if value < 0:
                raise ValueError(f"{name} cannot be negative; got {value}")

    @property
    def delta_minutes(self) -> float:
        """Baseline minus assisted, under these assumed values."""
        return self.baseline_minutes - self.assisted_minutes


DEFAULT_ASSUMPTIONS: tuple[TimeAssumption, ...] = (
    TimeAssumption(
        step="triage and case selection",
        baseline_minutes=4.0,
        assisted_minutes=1.0,
        rationale=(
            "Assumed. The workbench presents a ranked queue with its score components "
            "visible, against reading an unordered flagged list. No timing was collected "
            "for either arm."
        ),
    ),
    TimeAssumption(
        step="evidence gathering",
        baseline_minutes=25.0,
        assisted_minutes=8.0,
        rationale=(
            "Assumed, and the least defensible line in this table. Baseline stands for "
            "manual querying across six tables; assisted stands for a bounded pivot "
            "sequence. The Phase 4 finding that the scripted strategy does not traverse "
            "means the assisted arm here describes an intended capability."
        ),
    ),
    TimeAssumption(
        step="memo drafting",
        baseline_minutes=20.0,
        assisted_minutes=7.0,
        rationale=(
            "Assumed. Assisted covers reviewing a drafted memo and its citations rather "
            "than writing one. Review of a draft is not free and the assumed figure is "
            "deliberately not close to zero."
        ),
    ),
    TimeAssumption(
        step="citation and policy verification",
        baseline_minutes=8.0,
        assisted_minutes=3.0,
        rationale=(
            "Assumed. The citation resolver checks quoted excerpts against the corpus, so "
            "the assisted arm is the analyst confirming a machine check rather than "
            "performing the lookup."
        ),
    ),
    TimeAssumption(
        step="human decision and sign-off",
        baseline_minutes=3.0,
        assisted_minutes=3.0,
        rationale=(
            "Assumed identical, deliberately. ENFORCE is human-only by construction, so "
            "this step cannot be assisted and must not appear to contribute to any delta."
        ),
    ),
)
"""The default assumption table.

Every figure is a judgment and is labelled as one. They are set out here rather
than embedded in a computation so that a reader who disagrees can change one
number and re-run, which is the only useful thing to do with a model like this.
"""


@dataclass(frozen=True, slots=True)
class SensitivitySpan:
    """How far the modelled delta moves when one assumption is varied alone."""

    step: str
    low_delta: float
    high_delta: float
    break_even_assisted: float

    @property
    def swing(self) -> float:
        return self.high_delta - self.low_delta


@dataclass(frozen=True, slots=True)
class MinutesResult:
    """The modelled comparison, its assumptions, and its sensitivity.

    Deliberately has no ``minutes_saved`` attribute and no property that reads
    as one. ``delta_minutes`` is the difference between two assumed totals and
    is meaningless without the table beside it, which is why ``render`` prints
    the table first and the number second.
    """

    assumptions: tuple[TimeAssumption, ...]
    cases: int
    spans: tuple[SensitivitySpan, ...]
    variation: float

    @property
    def baseline_total(self) -> float:
        return sum(item.baseline_minutes for item in self.assumptions) * self.cases

    @property
    def assisted_total(self) -> float:
        return sum(item.assisted_minutes for item in self.assumptions) * self.cases

    @property
    def delta_minutes(self) -> float:
        return self.baseline_total - self.assisted_total

    @property
    def widest_span(self) -> SensitivitySpan | None:
        """The assumption the modelled delta is most sensitive to.

        Reported because a delta driven almost entirely by one assumed number is
        a delta about that assumption rather than about the workbench.
        """
        return max(self.spans, key=lambda span: span.swing) if self.spans else None

    def render(self) -> str:
        lines = [
            "Analyst minutes: a MODELLED comparison over stated assumptions",
            "=" * 66,
            "",
            NO_BENCHMARK_NOTE,
            "",
            "Assumptions (every figure below is assumed, none is measured)",
            "-" * 66,
            f"{'step':<34}{'baseline':>10}{'assisted':>10}{'delta':>9}",
        ]
        for item in self.assumptions:
            lines.append(
                f"{item.step:<34}{item.baseline_minutes:>10.1f}"
                f"{item.assisted_minutes:>10.1f}{item.delta_minutes:>9.1f}"
            )
        lines.extend(
            [
                "-" * 66,
                f"{'per case':<34}{sum(i.baseline_minutes for i in self.assumptions):>10.1f}"
                f"{sum(i.assisted_minutes for i in self.assumptions):>10.1f}"
                f"{sum(i.delta_minutes for i in self.assumptions):>9.1f}",
                "",
                f"Over {self.cases} case(s), the assumption table implies a difference of "
                f"{self.delta_minutes:.1f} minutes",
                "between the two arms. That figure is a property of the table above and carries no",
                "evidence that either arm would occur in practice.",
                "",
                f"One-way sensitivity (+/-{100 * self.variation:.0f}% on each assumed "
                "assisted time, alone)",
                "-" * 66,
                f"{'step':<34}{'delta low':>11}{'delta high':>12}{'break-even':>12}",
            ]
        )
        for span in self.spans:
            lines.append(
                f"{span.step:<34}{span.low_delta:>11.1f}{span.high_delta:>12.1f}"
                f"{span.break_even_assisted:>12.1f}"
            )
        widest = self.widest_span
        if widest is not None:
            lines.extend(
                [
                    "-" * 66,
                    f"The modelled difference is most sensitive to '{widest.step}', which "
                    f"moves it by {widest.swing:.1f} minutes on its own.",
                ]
            )
        lines.extend(
            [
                "",
                "'break-even' is the assisted minutes at which that step contributes "
                "nothing to the",
                "difference. Read it as the threshold an assumption would have to cross before the",
                "sign of the comparison changed. None of these thresholds has been measured.",
            ]
        )
        return "\n".join(lines)

    def to_json_object(self) -> dict[str, object]:
        return {
            "kind": "modelled",
            "no_benchmark_note": NO_BENCHMARK_NOTE,
            "cases": self.cases,
            "variation": self.variation,
            "assumptions": [
                {
                    "step": item.step,
                    "baseline_minutes": item.baseline_minutes,
                    "assisted_minutes": item.assisted_minutes,
                    "delta_minutes": item.delta_minutes,
                    "rationale": item.rationale,
                    "source": "assumption",
                }
                for item in self.assumptions
            ],
            "baseline_total_minutes": self.baseline_total,
            "assisted_total_minutes": self.assisted_total,
            "delta_minutes": self.delta_minutes,
            "sensitivity": [
                {
                    "step": span.step,
                    "delta_low": span.low_delta,
                    "delta_high": span.high_delta,
                    "break_even_assisted_minutes": span.break_even_assisted,
                    "swing": span.swing,
                }
                for span in self.spans
            ],
        }


@dataclass(frozen=True, slots=True)
class AnalystMinutesModel:
    """The model itself: an assumption table and a variation width."""

    assumptions: tuple[TimeAssumption, ...] = DEFAULT_ASSUMPTIONS
    variation: float = 0.5

    def __post_init__(self) -> None:
        if not self.assumptions:
            raise ValueError("a minutes model needs at least one assumption")
        steps = [item.step for item in self.assumptions]
        if len(set(steps)) != len(steps):
            raise ValueError(f"assumption steps must be distinct; got {sorted(steps)}")
        if not 0.0 < self.variation <= 1.0:
            raise ValueError(f"variation must lie in (0, 1]; got {self.variation}")

    def evaluate(self, *, cases: int) -> MinutesResult:
        """Compute the modelled delta and a one-way sensitivity over each step.

        One-way rather than joint: varying every assumption at once produces a
        range so wide it says nothing, while varying them singly shows which one
        the answer actually rests on. That is the question worth asking of a
        model whose inputs are all judgments.
        """
        if cases <= 0:
            raise ValueError(f"cases must be positive; got {cases}")

        spans: list[SensitivitySpan] = []
        for index, item in enumerate(self.assumptions):
            others = sum(
                other.delta_minutes
                for position, other in enumerate(self.assumptions)
                if position != index
            )
            low_assisted = item.assisted_minutes * (1.0 - self.variation)
            high_assisted = item.assisted_minutes * (1.0 + self.variation)
            spans.append(
                SensitivitySpan(
                    step=item.step,
                    # A larger assisted time gives a smaller delta, so the low
                    # end of the delta comes from the high end of the assumption.
                    low_delta=(others + item.baseline_minutes - high_assisted) * cases,
                    high_delta=(others + item.baseline_minutes - low_assisted) * cases,
                    break_even_assisted=item.baseline_minutes,
                )
            )

        return MinutesResult(
            assumptions=self.assumptions,
            cases=cases,
            spans=tuple(spans),
            variation=self.variation,
        )


@dataclass(frozen=True, slots=True)
class GovernanceActivity:
    """Counts of the governance layer doing its job, from real session events.

    Mandatory in every report, zero or not (3.3). A zero here does not mean the
    gates are sound; it means nothing in that session tried them, and the
    rendering says so rather than leaving a reader to infer competence from an
    empty table.
    """

    gate_rejections: int
    mandate_violation_attempts: int
    verification_passes: int
    verification_failures: int
    human_decisions: int
    injection_signals: int
    rejected_hops: int

    @property
    def total_exercised(self) -> int:
        """How many times a control actually fired."""
        return (
            self.gate_rejections
            + self.mandate_violation_attempts
            + self.verification_failures
            + self.injection_signals
            + self.rejected_hops
        )

    @property
    def untested(self) -> bool:
        return self.total_exercised == 0

    @property
    def verification_pass_rate(self) -> float | None:
        """Share of verifications that passed, or ``None`` when none were run.

        ``None`` rather than 1.0. A session that verified nothing has no pass
        rate, and reporting a perfect one would be the most flattering possible
        reading of having done no work.
        """
        total = self.verification_passes + self.verification_failures
        return self.verification_passes / total if total else None

    def render(self) -> str:
        rate = self.verification_pass_rate
        rendered_rate = "n/a (nothing verified)" if rate is None else f"{100 * rate:.1f}%"
        lines = [
            "Governance activity (MEASURED, counted from session events)",
            "-" * 66,
            f"  gate rejections                {self.gate_rejections}",
            f"  mandate violation attempts     {self.mandate_violation_attempts}",
            f"  prompt injection signals       {self.injection_signals}",
            f"  pivots refused before review   {self.rejected_hops}",
            f"  verification passes            {self.verification_passes}",
            f"  verification failures          {self.verification_failures}",
            f"  human decisions recorded       {self.human_decisions}",
            f"  memo/output verification pass rate: {rendered_rate}",
        ]
        if self.untested:
            lines.extend(
                [
                    "",
                    "  NOTE: no control above fired. Every rejection, violation attempt,",
                    "  injection signal, refused pivot and verification failure is zero.",
                    "  That is not evidence the governance layer works: it means nothing in",
                    "  this session exercised a gate, a mandate ceiling, the firewall or the",
                    "  verifier, so this session supports no claim about any of them.",
                    "  Passing verifications and recorded human decisions are counted above",
                    "  and are not controls firing; they are the ordinary path.",
                ]
            )
        return "\n".join(lines)

    def to_json_object(self) -> dict[str, object]:
        return {
            "kind": "measured",
            "gate_rejections": self.gate_rejections,
            "mandate_violation_attempts": self.mandate_violation_attempts,
            "injection_signals": self.injection_signals,
            "rejected_hops": self.rejected_hops,
            "verification_passes": self.verification_passes,
            "verification_failures": self.verification_failures,
            "human_decisions": self.human_decisions,
            "verification_pass_rate": self.verification_pass_rate,
            "untested": self.untested,
        }


@dataclass(frozen=True, slots=True)
class SessionCounts:
    """What one session directory reports about itself."""

    session_id: str
    case_id: str | None
    governance: GovernanceActivity
    attempted_hops: int
    executed_hops: int
    event_counts: Mapping[str, int] = field(default_factory=dict)


def read_session_counts(session_dir: Path) -> SessionCounts:
    """Count governance activity from a real session's artifacts.

    Reads ``session_events.json``, which carries the full event payloads, rather
    than the manifest's pre-aggregated ``event_counts``. The manifest is a
    summary written by the same run; counting the events directly means the
    report and the manifest are two independent readings of one artifact and can
    disagree, which is the point of having both.
    """
    events_path = session_dir / "session_events.json"
    if not events_path.is_file():
        raise FileNotFoundError(f"{events_path} does not exist; not a session directory")

    payload = json.loads(events_path.read_text(encoding="utf-8"))
    events: Sequence[Mapping[str, object]] = payload.get("events", ())
    turn: Mapping[str, object] = payload.get("turn") or {}

    def whole(key: str) -> int:
        """A non-negative count from the turn record, or zero when absent.

        Session artifacts are JSON, so every value arrives as ``object`` and a
        bare ``int(...)`` would both fail type checking and accept a float or a
        string that happened to parse. A turn that reports a negative or
        non-integral count is a malformed artifact rather than something to
        coerce quietly.
        """
        raw = turn.get(key)
        if raw is None:
            return 0
        if not isinstance(raw, int) or isinstance(raw, bool):
            raise ValueError(f"{events_path}: turn.{key} must be an integer; got {raw!r}")
        if raw < 0:
            raise ValueError(f"{events_path}: turn.{key} cannot be negative; got {raw}")
        return raw

    counts: dict[str, int] = {}
    for event in events:
        name = str(event.get("event_type", ""))
        counts[name] = counts.get(name, 0) + 1

    return SessionCounts(
        session_id=str(payload.get("session_id", "")),
        case_id=None if payload.get("case_id") is None else str(payload["case_id"]),
        governance=GovernanceActivity(
            gate_rejections=counts.get(EventType.GATE_REJECTION.value, 0),
            mandate_violation_attempts=counts.get(EventType.MANDATE_VIOLATION_ATTEMPT.value, 0),
            verification_passes=counts.get(EventType.VERIFICATION_PASS.value, 0),
            verification_failures=counts.get(EventType.VERIFICATION_FAIL.value, 0),
            human_decisions=counts.get(EventType.HUMAN_DECISION.value, 0),
            injection_signals=whole("injection_signals"),
            rejected_hops=whole("rejected_hops"),
        ),
        attempted_hops=whole("attempted_hops"),
        executed_hops=whole("executed_hops"),
        event_counts=counts,
    )
