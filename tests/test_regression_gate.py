# SPDX-License-Identifier: MIT
"""D4: the regression gate, and STEP-06 3.5's purity property.

3.5 asks that "gate decision is a pure function of (report, tolerances); same
inputs, same verdict". That is asserted here two ways, because they fail
differently: a hypothesis property over generated reports, and a direct check
that the module reads nothing else.
"""

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ts_sentry.data.enums import ThreatClass
from ts_sentry.orchestrator.eval_labels import ClassCounts
from ts_sentry.orchestrator.prompt_eval import ClassDelta, EvalReport, VersionMetrics
from ts_sentry.orchestrator.regression_gate import (
    TOLERANCES_FILE,
    ActivationDecision,
    BreachCode,
    ToleranceError,
    Tolerances,
    decide,
    load_tolerances,
    minimum_detectable_drop,
)

EVAL_ROOT = Path(__file__).resolve().parent.parent / "evals" / "threat_class"

THREAT_CLASSES = [member for member in ThreatClass]


def _delta(
    threat_class: ThreatClass = ThreatClass.T01_COMMENT_SPAM_RING,
    *,
    support: int = 6,
    delta: float = 0.0,
    lower: float = 0.0,
    upper: float = 0.0,
) -> ClassDelta:
    return ClassDelta(
        threat_class=threat_class,
        support=support,
        incumbent_recall=0.5,
        candidate_recall=0.5 + delta,
        delta=delta,
        lower=lower,
        upper=upper,
        resamples=2000,
        confidence=0.95,
        discordant=0,
    )


def _metrics(digest: str, *, f1: float = 0.5, unparseable: int = 0) -> VersionMetrics:
    """Counts engineered to yield a chosen macro F1 on one class."""
    hits = round(f1 * 8)
    counts = {
        member: ClassCounts(
            threat_class=member,
            support=8 if member is ThreatClass.T01_COMMENT_SPAM_RING else 0,
            predicted=8 if member is ThreatClass.T01_COMMENT_SPAM_RING else 0,
            true_positives=hits if member is ThreatClass.T01_COMMENT_SPAM_RING else 0,
        )
        for member in ThreatClass
    }
    return VersionMetrics(content_digest=digest, counts=counts, unparseable=unparseable)


def _report(
    *deltas: ClassDelta,
    incumbent_f1: float = 0.5,
    candidate_f1: float = 0.5,
    candidate_unparseable: int = 0,
) -> EvalReport:
    return EvalReport(
        task="classify.threat_class",
        incumbent=_metrics("a" * 64, f1=incumbent_f1),
        candidate=_metrics("b" * 64, f1=candidate_f1, unparseable=candidate_unparseable),
        deltas=deltas or (_delta(),),
        item_count=59,
        items_sha256="c" * 64,
        labels_sha256="d" * 64,
        adapter_id="stub/faithful",
        model_id="deterministic-stub-v1",
        bootstrap_seed=42,
    )


TOLERANT = Tolerances(recall_drop=0.25, macro_f1_drop=0.10, max_unparseable=0)


# --------------------------------------------------------------------------
# Decision D: the lower bound decides, not the point estimate
# --------------------------------------------------------------------------


def test_an_unchanged_candidate_is_activatable() -> None:
    """A zero-width interval at zero clears any tolerance.

    Measured on the committed eval set as well: two identical versions produce
    a difference vector of zeros, and a percentile bootstrap over zeros can only
    resample zeros, so every class reports [0.000, 0.000].
    """
    verdict = decide(_report(_delta(delta=0.0, lower=0.0, upper=0.0)), TOLERANT)

    assert verdict.decision is ActivationDecision.ACTIVATABLE
    assert verdict.breaches == ()


def test_a_measurable_regression_is_refused_as_such() -> None:
    verdict = decide(_report(_delta(delta=-0.667, lower=-1.0, upper=-0.333)), TOLERANT)

    assert verdict.decision is ActivationDecision.REFUSED
    assert [breach.code for breach in verdict.breaches] == [BreachCode.RECALL_REGRESSION]
    assert verdict.breaches[0].threat_class is ThreatClass.T01_COMMENT_SPAM_RING


def test_a_candidate_that_looks_fine_but_cannot_be_cleared_is_refused_separately() -> None:
    """The heart of decision D, and the reason the two codes are distinct.

    The observed change is zero, which is within any tolerance. The interval
    reaches -0.5, so the eval set cannot exclude a drop beyond 0.25. Activation
    requires evidence of non-regression, so this refuses, and it refuses under
    ``REGRESSION_NOT_EXCLUDED`` because the candidate is not measurably worse:
    the eval set is too small to answer, which is a fact about the generator
    rather than about the prompt.
    """
    verdict = decide(_report(_delta(delta=0.0, lower=-0.5, upper=0.5)), TOLERANT)

    assert verdict.decision is ActivationDecision.REFUSED
    assert [breach.code for breach in verdict.breaches] == [BreachCode.REGRESSION_NOT_EXCLUDED]
    assert "cannot be excluded" in verdict.breaches[0].detail


def test_an_improvement_is_never_refused_for_being_an_improvement() -> None:
    """The gate is one-sided. A candidate that got better has a lower bound at
    or above zero, and nothing here penalizes a wide interval on the upside."""
    verdict = decide(_report(_delta(delta=0.5, lower=0.167, upper=0.833)), TOLERANT)

    assert verdict.activatable


def test_a_class_with_no_items_cannot_breach() -> None:
    """Zero support is the absence of a claim, not a claim of equality."""
    verdict = decide(_report(_delta(support=0, delta=0.0, lower=-1.0, upper=1.0)), TOLERANT)

    assert verdict.activatable


def test_every_breach_is_collected_not_just_the_first() -> None:
    """The per-class breach report is the phase's exit criterion.

    A refusal naming one class when three regressed would send its reader back
    for a second run to discover the others.
    """
    verdict = decide(
        _report(
            _delta(ThreatClass.T01_COMMENT_SPAM_RING, delta=-1.0, lower=-1.0, upper=-1.0),
            _delta(ThreatClass.T02_FAKE_ENGAGEMENT_NETWORK, delta=-0.5, lower=-0.75, upper=-0.25),
            _delta(ThreatClass.T06_SLOP_FARM, delta=0.0, lower=0.0, upper=0.0),
        ),
        TOLERANT,
    )

    assert len(verdict.breaches) == 2
    assert {breach.threat_class for breach in verdict.breaches} == {
        ThreatClass.T01_COMMENT_SPAM_RING,
        ThreatClass.T02_FAKE_ENGAGEMENT_NETWORK,
    }


def test_a_broken_output_contract_is_its_own_breach() -> None:
    verdict = decide(_report(candidate_unparseable=3), TOLERANT)

    assert [breach.code for breach in verdict.breaches] == [BreachCode.OUTPUT_CONTRACT_BROKEN]


def test_a_macro_f1_collapse_is_refused() -> None:
    verdict = decide(_report(incumbent_f1=1.0, candidate_f1=0.25), TOLERANT)

    assert BreachCode.MACRO_F1_REGRESSION in {breach.code for breach in verdict.breaches}


# --------------------------------------------------------------------------
# 3.5: the gate decision is a pure function of (report, tolerances)
# --------------------------------------------------------------------------


@st.composite
def _reports(draw: st.DrawFn) -> EvalReport:
    deltas = draw(
        st.lists(
            st.tuples(
                st.sampled_from(THREAT_CLASSES),
                st.integers(min_value=0, max_value=20),
                st.floats(min_value=-1.0, max_value=1.0, allow_nan=False),
                st.floats(min_value=-1.0, max_value=1.0, allow_nan=False),
                st.floats(min_value=-1.0, max_value=1.0, allow_nan=False),
            ),
            min_size=1,
            max_size=8,
            unique_by=lambda row: row[0],
        )
    )
    built = tuple(
        _delta(
            threat_class,
            support=support,
            delta=point,
            lower=min(low, high),
            upper=max(low, high),
        )
        for threat_class, support, point, low, high in deltas
    )
    return _report(*built, candidate_unparseable=draw(st.integers(min_value=0, max_value=5)))


@st.composite
def _tolerances(draw: st.DrawFn) -> Tolerances:
    return Tolerances(
        recall_drop=draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False)),
        macro_f1_drop=draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False)),
        max_unparseable=draw(st.integers(min_value=0, max_value=5)),
    )


@given(report=_reports(), tolerances=_tolerances())
@settings(max_examples=200)
def test_the_verdict_is_a_pure_function_of_its_two_inputs(
    report: EvalReport, tolerances: Tolerances
) -> None:
    """STEP-06 3.5, over generated reports.

    Called twice on the same inputs and required to agree completely, including
    the breach list and its order. A gate that consulted a clock, a file, or a
    generator would eventually disagree with itself here.
    """
    first = decide(report, tolerances)
    second = decide(report, tolerances)

    assert first == second
    assert first.to_json_object() == second.to_json_object()


@given(report=_reports(), tolerances=_tolerances())
@settings(max_examples=200)
def test_the_verdict_is_total(report: EvalReport, tolerances: Tolerances) -> None:
    """No well-formed input raises, and the decision always matches the breaches.

    Totality is what makes the gate safe to call from the turn without a
    try/except that could swallow a governance outcome (STEP-02 2.4).
    """
    verdict = decide(report, tolerances)

    assert verdict.activatable is (verdict.breaches == ())
    assert verdict.tolerances_sha256 == tolerances.digest


def test_the_gate_reads_nothing_but_its_two_arguments() -> None:
    """Purity asserted structurally as well as by property.

    The property above would pass for a function that read a file whose
    contents never changed during the run. This reads the module's own source
    and checks that ``decide`` calls nothing that could reach outside it.
    """
    import ast
    import inspect

    from ts_sentry.orchestrator import regression_gate

    tree = ast.parse(inspect.getsource(regression_gate.decide))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "open" not in called
    assert "load_tolerances" not in called
    assert "minimum_detectable_drop" not in called, (
        "decide must not read the report's own resolution: a gate that set its "
        "tolerance from the evidence would widen its criterion exactly when the "
        "evidence got weaker"
    )


# --------------------------------------------------------------------------
# Tolerances as declared config
# --------------------------------------------------------------------------


def test_the_committed_tolerances_load_and_are_the_measured_ones() -> None:
    tolerances = load_tolerances(EVAL_ROOT / TOLERANCES_FILE)

    assert tolerances.recall_drop == 0.25
    assert tolerances.max_unparseable == 0


def test_a_missing_tolerance_file_refuses_rather_than_defaulting(tmp_path: Path) -> None:
    """``GateChecks`` has no defaults (DECISIONS 2.5), one level up."""
    with pytest.raises(ToleranceError, match="no default limits"):
        load_tolerances(tmp_path / TOLERANCES_FILE)


def test_a_tolerance_change_changes_the_digest() -> None:
    """So a tolerance edit is a hash change, bindable into SESSION_OPEN."""
    base = Tolerances(recall_drop=0.25, macro_f1_drop=0.10, max_unparseable=0)
    loosened = Tolerances(recall_drop=0.26, macro_f1_drop=0.10, max_unparseable=0)

    assert base.digest != loosened.digest


@pytest.mark.parametrize(
    ("field", "value"),
    [("recall_drop", 1.5), ("recall_drop", -0.1), ("macro_f1_drop", 2.0), ("max_unparseable", -1)],
)
def test_unusable_tolerances_are_refused(field: str, value: float) -> None:
    kwargs: dict[str, float | int] = {
        "recall_drop": 0.25,
        "macro_f1_drop": 0.1,
        "max_unparseable": 0,
    }
    kwargs[field] = value

    with pytest.raises(ValueError):
        Tolerances(**kwargs)  # type: ignore[arg-type]


def test_the_tolerance_file_records_why_each_number_is_what_it_is() -> None:
    """A declared limit with no recorded reason is a number nobody can review.

    The rationale lives in the config file rather than only in a docstring,
    because the file is what an operator edits and the docstring is not what
    they will be reading when they do.
    """
    raw = json.loads((EVAL_ROOT / TOLERANCES_FILE).read_text(encoding="utf-8"))

    for key in ("_recall_drop_rationale", "_macro_f1_rationale", "_max_unparseable_rationale"):
        assert raw[key].strip()


def test_minimum_detectable_drop_reports_only_classes_with_items() -> None:
    resolution = minimum_detectable_drop(
        _report(
            _delta(ThreatClass.T01_COMMENT_SPAM_RING, support=6, lower=-0.5, upper=0.5),
            _delta(ThreatClass.T05_AI_PERSONA_AUTHORITY, support=0),
        )
    )

    assert ThreatClass.T01_COMMENT_SPAM_RING in resolution
    assert ThreatClass.T05_AI_PERSONA_AUTHORITY not in resolution
    assert resolution[ThreatClass.T01_COMMENT_SPAM_RING] == pytest.approx(0.5)
