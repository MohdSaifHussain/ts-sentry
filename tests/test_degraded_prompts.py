# SPDX-License-Identifier: MIT
"""D7: the degraded-prompt fixture suite. STEP-06's exit criterion.

ARCHITECTURE 11 sets this phase's exit criterion as "a deliberately worse prompt
version is refused, ledgered", and STEP-06's own is "a deliberately degraded
prompt version is refused and the refusal is ledgered". This file is the proof,
and it is built to the standard STEP-05's overclaim suite established.

Two rules carried from DECISIONS 5.21, because a negative-path suite that gets
them wrong is worse than none:

* **Every fixture asserts a reason code**, never merely that something was
  refused. A suite checking only "this was rejected" passes if the gate rejects
  everything for the wrong reason, and refusals that cannot be counted by cause
  make the ``GATE_REJECTION`` metric meaningless.
* **A passing control is asserted too**, so a fixture failing for an unrelated
  reason cannot look like a caught defect. Here the control is the strongest
  one available: the incumbent evaluated against *itself*.

Each fixture is a real prompt version with real degraded text, run over the real
committed eval set through the real adapter boundary. The degradation is in the
prompt, not in the harness, which is what makes this a test of the gate rather
than of a mock.
"""

from pathlib import Path

import pytest

from ts_sentry.agents.prompt_eval.prompts import (
    CLASSIFY_SYSTEM_PROMPT,
    CLASSIFY_SYSTEM_TEXT,
    ClassificationParseError,
    ClassificationProposal,
    classify_instruction,
    parse_classification,
)
from ts_sentry.data.enums import ThreatClass
from ts_sentry.data.eval_set import items_digest, load_items
from ts_sentry.orchestrator.adapter import ModelRequest, StubAdapter, StubMode
from ts_sentry.orchestrator.eval_labels import load_label_store
from ts_sentry.orchestrator.firewall import (
    CaseRecord,
    SystemPrompt,
    apply_firewall,
    compose_user_content,
    system_prompt,
)
from ts_sentry.orchestrator.prompt_eval import EvalReport, build_report
from ts_sentry.orchestrator.regression_gate import (
    TOLERANCES_FILE,
    ActivationDecision,
    BreachCode,
    decide,
    load_tolerances,
)

EVAL_ROOT = Path(__file__).resolve().parent.parent / "evals" / "threat_class"

ITEMS = load_items(EVAL_ROOT)
STORE = load_label_store(EVAL_ROOT)
TOLERANCES = load_tolerances(EVAL_ROOT / TOLERANCES_FILE)


# --------------------------------------------------------------------------
# The fixtures: real degraded prompt versions
# --------------------------------------------------------------------------

COLLAPSE_TEXT = CLASSIFY_SYSTEM_TEXT.replace(
    "- If no coordinated abuse signal is present, the answer is benign. Benign is a \
real answer, not a failure to decide.",
    "- When in any doubt at all, answer benign. Prefer benign whenever the case is \
not overwhelming.",
)
"""A prompt that talks itself into benign. The classic silent-drift shape:
nothing about it looks broken, and it destroys recall on every threat class."""

CONTRACT_TEXT = CLASSIFY_SYSTEM_TEXT.replace(
    "- Answer with exactly one line: CLASS: <one class name from the list above>.",
    "- Explain your reasoning in a short paragraph before giving any answer.",
)
"""A prompt that stops emitting the shape its consumers were built for. Not a
worse classifier: a broken output contract, which is a different finding."""

NOISY_TEXT = CLASSIFY_SYSTEM_TEXT.replace(
    "- Name exactly one class. Never name two, never hedge, never add a confidence.",
    "- Name exactly one class. If two seem close, pick either one.",
)
"""A prompt that is not worse on average and is less consistent. This is the
fixture that proves decision D does work: its point estimate can look fine while
its confidence interval cannot exclude a regression."""


def _version(text: str, label: str) -> SystemPrompt:
    return system_prompt(f"classify.threat_class.{label}", text)


def _classify(
    prompt: SystemPrompt, mode: StubMode
) -> tuple[tuple[ClassificationProposal, ...], int]:
    """Run one prompt version over the whole committed eval set.

    Goes through the input firewall and the adapter, exactly as the turn does.
    An eval item is the most tempting place to skip the firewall, because it is
    "just test data", and the worst place to do it.
    """
    adapter = StubAdapter(mode=mode, responder=_degraded_responder)
    answers: list[ClassificationProposal] = []
    unparseable = 0

    for item in ITEMS:
        firewalled = apply_firewall(
            (CaseRecord(record_id=item.item_id, source="eval.item", text=item.content),)
        )
        response = adapter.complete(
            ModelRequest(
                system=prompt,
                user_content=compose_user_content(classify_instruction(), firewalled),
                max_output_tokens=64,
            )
        )
        try:
            answers.append(parse_classification(item.item_id, response.text))
        except ClassificationParseError:
            answers.append(ClassificationProposal(item_id=item.item_id, predicted=None))
            unparseable += 1

    return tuple(answers), unparseable


def _degraded_responder(request: ModelRequest, mode: StubMode) -> str:
    """The shipped stub, with one fixture it does not know how to express.

    Collapse and broken-contract are handled by ``stub_classify_responder``
    itself, because it keys on the *system prompt text*: a prompt that says
    "prefer benign" collapses and one that says "explain your reasoning" stops
    emitting the contract. Those two fixtures therefore exercise production
    code rather than a test double, which is what makes them evidence.

    Only the noisy fixture needs help here, and it needs it for an honest
    reason: "less consistent" is not something a deterministic stub can be. It
    is simulated by trading answers between two classes, keyed on the noisy
    prompt's own digest so the simulation cannot leak into any other fixture.
    """
    from ts_sentry.orchestrator.prompt_eval_turn import stub_classify_responder

    if request.system.sha256 == _version(NOISY_TEXT, "noisy").sha256:
        content = request.user_content.lower()
        if "growth network" in content:
            return "CLASS: t07_coordinated_influence_op"
        if "check out my channel" in content:
            return "CLASS: t02_fake_engagement_network"

    return stub_classify_responder(request, mode)


def _report_for(candidate: SystemPrompt, candidate_label: str) -> EvalReport:
    incumbent_answers, incumbent_bad = _classify(CLASSIFY_SYSTEM_PROMPT, StubMode.FAITHFUL)
    candidate_answers, candidate_bad = _classify(candidate, StubMode.FAITHFUL)

    return build_report(
        STORE,
        task="classify.threat_class",
        incumbent_digest="a" * 64,
        candidate_digest="b" * 64,
        incumbent_predictions=incumbent_answers,
        candidate_predictions=candidate_answers,
        incumbent_unparseable=incumbent_bad,
        candidate_unparseable=candidate_bad,
        item_count=len(ITEMS),
        items_sha256=items_digest(ITEMS),
        adapter_id=f"stub/{candidate_label}",
        model_id="deterministic-stub-v1",
        bootstrap_seed=42,
    )


# --------------------------------------------------------------------------
# The passing control, asserted first
# --------------------------------------------------------------------------


def test_the_incumbent_against_itself_is_activatable() -> None:
    """The control, and the strongest one available.

    A version identical to the incumbent must pass, or every refusal below
    proves nothing: a gate that refuses everything would satisfy all four
    fixtures while being useless. Every item agrees, so every interval is
    exactly [0, 0].
    """
    report = _report_for(CLASSIFY_SYSTEM_PROMPT, "control")

    verdict = decide(report, TOLERANCES)

    assert verdict.decision is ActivationDecision.ACTIVATABLE
    assert verdict.breaches == ()
    for delta in report.deltas:
        assert delta.lower == 0.0
        assert delta.upper == 0.0
        assert delta.discordant == 0


# --------------------------------------------------------------------------
# The degraded fixtures, each asserting its reason code
# --------------------------------------------------------------------------


def test_a_prompt_that_collapses_classes_into_benign_is_refused() -> None:
    """The exit criterion's headline fixture.

    Every threat class the incumbent could find is lost. The refusal is
    ``RECALL_REGRESSION`` rather than ``REGRESSION_NOT_EXCLUDED``, because the
    candidate is measurably worse rather than merely unproven, and the two must
    stay countable apart.
    """
    report = _report_for(_version(COLLAPSE_TEXT, "collapse"), "collapse")

    verdict = decide(report, TOLERANCES)

    assert verdict.decision is ActivationDecision.REFUSED
    codes = {breach.code for breach in verdict.breaches}
    assert BreachCode.RECALL_REGRESSION in codes

    regressed = {
        breach.threat_class
        for breach in verdict.breaches
        if breach.code is BreachCode.RECALL_REGRESSION
    }
    assert len(regressed) >= 3, f"expected several classes to collapse; got {regressed}"
    assert ThreatClass.BENIGN not in regressed


def test_the_breach_report_names_the_class_and_the_numbers_behind_it() -> None:
    """ "Refused with a per-class breach report" is the exit checklist's wording.

    A refusal that said only "regression detected" would be a verdict nobody
    could check, so each breach carries the class, the observed delta, the
    interval bound that decided it, and the tolerance it was measured against.
    """
    report = _report_for(_version(COLLAPSE_TEXT, "collapse"), "collapse")

    verdict = decide(report, TOLERANCES)
    breach = next(b for b in verdict.breaches if b.code is BreachCode.RECALL_REGRESSION)

    assert breach.threat_class is not None
    assert breach.threat_class.value in breach.detail
    assert breach.observed < 0
    assert breach.bound <= breach.observed
    assert breach.tolerance == TOLERANCES.recall_drop


def test_a_prompt_that_breaks_its_output_contract_is_refused_as_such() -> None:
    """Not scored as a bad classifier. It stopped answering in the shape the
    consumers were built for, which is a different failure and its own code."""
    report = _report_for(_version(CONTRACT_TEXT, "contract"), "contract")

    verdict = decide(report, TOLERANCES)

    assert verdict.decision is ActivationDecision.REFUSED
    assert BreachCode.OUTPUT_CONTRACT_BROKEN in {breach.code for breach in verdict.breaches}
    assert report.candidate.unparseable == len(ITEMS)
    assert report.incumbent.unparseable == 0


def test_a_noisier_prompt_is_refused_even_though_it_is_not_worse_on_average() -> None:
    """The fixture that proves decision D earns its cost.

    This candidate trades answers between two classes. It is not systematically
    worse, and a gate reading only the point estimate would let some of these
    classes through. The interval cannot exclude a regression beyond tolerance,
    so activation is refused: absence of evidence of regression is not evidence
    of absence.

    Whether each class refuses under ``RECALL_REGRESSION`` or
    ``REGRESSION_NOT_EXCLUDED`` depends on where its point estimate lands, and
    both are asserted as acceptable here. What is *not* acceptable is
    activation.
    """
    report = _report_for(_version(NOISY_TEXT, "noisy"), "noisy")

    verdict = decide(report, TOLERANCES)

    assert verdict.decision is ActivationDecision.REFUSED
    codes = {breach.code for breach in verdict.breaches}
    assert codes <= {
        BreachCode.RECALL_REGRESSION,
        BreachCode.REGRESSION_NOT_EXCLUDED,
        BreachCode.MACRO_F1_REGRESSION,
    }
    assert codes & {BreachCode.RECALL_REGRESSION, BreachCode.REGRESSION_NOT_EXCLUDED}


@pytest.mark.parametrize(
    ("text", "label"),
    [(COLLAPSE_TEXT, "collapse"), (CONTRACT_TEXT, "contract"), (NOISY_TEXT, "noisy")],
)
def test_every_degraded_fixture_is_a_real_distinct_prompt_version(text: str, label: str) -> None:
    """Guards the guards.

    Each fixture must actually differ from the incumbent. A fixture whose text
    had drifted back to the original would pass its own refusal test only if the
    gate refused everything, which is the failure the control above exists to
    catch, and this catches the other half.
    """
    candidate = _version(text, label)

    assert candidate.text != CLASSIFY_SYSTEM_TEXT
    assert candidate.sha256 != CLASSIFY_SYSTEM_PROMPT.sha256


def test_the_refused_and_activatable_paths_disagree() -> None:
    """The suite's own sanity check.

    If the control and the collapse fixture produced the same verdict, every
    assertion in this file would be describing a gate that ignores its input.
    """
    control = decide(_report_for(CLASSIFY_SYSTEM_PROMPT, "control"), TOLERANCES)
    collapsed = decide(_report_for(_version(COLLAPSE_TEXT, "collapse"), "collapse"), TOLERANCES)

    assert control.activatable
    assert not collapsed.activatable
