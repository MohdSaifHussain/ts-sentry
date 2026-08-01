# SPDX-License-Identifier: MIT
"""D2: the labeled eval set, and STEP-06 3.2's contamination discipline.

The contamination tests are the ones this phase is judged on, and they are
written to check that a label *cannot* reach a prompt author rather than that
it currently does not. Three independent things have to hold, and each has its
own test here so that relaxing one does not quietly pass because another
covered for it:

* the item type has nowhere to put a label;
* no rendered item leaks its class through content or id;
* only aggregates cross the boundary.

The import-graph half lives in ``tests/test_import_graph.py``, where the rest
of the reachability rules are.
"""

import json
from dataclasses import fields
from pathlib import Path

import pytest

from ts_sentry.agents.prompt_eval.prompts import ClassificationProposal
from ts_sentry.data.enums import ThreatClass
from ts_sentry.data.eval_build import BENIGN_CONTROL_FRACTION, EvalBuildError, _refuse_leaky_item
from ts_sentry.data.eval_set import (
    ITEM_ID_PREFIX,
    LABELS_FILE,
    MANIFEST_FILE,
    EvalItem,
    EvalSetError,
    load_items,
    load_manifest,
)
from ts_sentry.orchestrator.eval_labels import EvalLabelStore, load_label_store

EVAL_ROOT = Path(__file__).resolve().parent.parent / "evals" / "threat_class"


# --------------------------------------------------------------------------
# 3.2: labels cannot be passed, rather than are not passed
# --------------------------------------------------------------------------


def test_the_item_type_has_nowhere_to_put_a_label() -> None:
    """The structural half of 3.2, asserted against the dataclass itself.

    Shaped like STEP-05's assertion that ``render`` has no ``watermark``
    parameter (DECISIONS 5.18): a field that can carry a label is a field that
    will carry one, and the way to make "we never pass labels to the prompt
    author" true is to leave nowhere for a label to sit.

    A draft of this type carried a ``stratum``, which for a stratified set is
    the label under another name. This test is what would have caught it.
    """
    names = {field.name for field in fields(EvalItem)}

    assert names == {"item_id", "content"}
    assert not any(
        suspicious in name
        for name in names
        for suspicious in ("label", "class", "threat", "stratum", "truth")
    )


def test_no_committed_item_leaks_its_own_class() -> None:
    """The rendering half. Checked over the real artifact, not a fixture.

    Planted ids name their class in three characters (``t02_chan_000_000``) and
    so do planted signal values (``devhint_t02_000``). An item carrying either
    would hand the model its answer through the input firewall, with every
    governance control working exactly as designed and every metric coming back
    excellent. That is the one defect in this phase that is invisible in its own
    results, which is why it is checked at build time *and* here.
    """
    items = load_items(EVAL_ROOT)
    assert items, "the committed eval set is empty; this test would be vacuous"

    for item in items:
        haystack = f"{item.item_id}\n{item.content}".lower()
        for member in ThreatClass:
            if member is ThreatClass.BENIGN:
                continue
            assert member.value not in haystack, f"{item.item_id} names {member.value}"
        for prefix in ("t01_", "t02_", "t03_", "t04_", "t05_", "t06_", "t07_"):
            assert prefix not in haystack, f"{item.item_id} carries the planted prefix {prefix}"
        for marker in ("ring_t", "devhint_t", "ipb_t"):
            assert marker not in haystack, f"{item.item_id} carries the planted marker {marker}"


def test_item_ids_are_opaque_and_carry_no_entity_identity() -> None:
    items = load_items(EVAL_ROOT)

    for item in items:
        assert item.item_id.startswith(ITEM_ID_PREFIX)
        assert item.item_id[len(ITEM_ID_PREFIX) :].isdigit()


def test_an_entity_derived_item_id_is_refused() -> None:
    """The constructor refuses what the builder would never produce.

    Kept for the ``pack_gate`` reason (DECISIONS 4.8, 5.16): the builder is a
    guarantee only until something constructs an item by another route.
    """
    with pytest.raises(ValueError, match="opaque"):
        EvalItem(item_id="t02_chan_000_000", content="anything")


def test_the_build_time_leak_check_catches_a_planted_class_name() -> None:
    """The guard itself, exercised directly.

    Asserted rather than trusted, because a guard that never fires in any test
    is a guard nobody has seen work.
    """
    with pytest.raises(EvalBuildError, match="names its own threat class"):
        _refuse_leaky_item("item-0000", "Subject: a channel owned by t02_chan_000_000.")

    with pytest.raises(EvalBuildError, match="names its own threat class"):
        _refuse_leaky_item("item-0001", "Infrastructure signal devhint_t02_000 shared.")


def test_the_label_store_exposes_no_per_item_lookup() -> None:
    """Defence in depth on top of the import-graph rule.

    The store grades; it does not answer "what is the label of item-0007". The
    load-bearing control is who may import this module, and this is the
    belt-and-braces: even a caller who has it cannot enumerate the answers.
    """
    store = load_label_store(EVAL_ROOT)

    public = {name for name in dir(store) if not name.startswith("_")}

    assert "label_of" not in public
    assert "labels" not in public
    assert "items" not in public
    assert public == {"correct_flags", "covers", "digest", "score", "support"}


def test_only_aggregates_cross_the_boundary() -> None:
    """``support`` is a histogram, and a histogram names no item.

    Publishing it is what D2's governing standard asks for ("class balance
    documented"), and it is safe for exactly the reason a histogram is safe:
    knowing four items are T-04 says nothing about which four.
    """
    store = load_label_store(EVAL_ROOT)
    support = store.support()

    assert sum(support.values()) == len(store)
    serialized = json.dumps({member.value: count for member, count in support.items()})
    for item in load_items(EVAL_ROOT):
        assert item.item_id not in serialized


# --------------------------------------------------------------------------
# The committed artifact, and what it is honestly able to support
# --------------------------------------------------------------------------


def test_the_committed_eval_set_is_stratified_across_every_threat_class() -> None:
    """D2's own requirement: stratified across T-01..T-07 plus benign controls."""
    store = load_label_store(EVAL_ROOT)
    support = store.support()

    for member in ThreatClass:
        assert support[member] > 0, f"{member.value} has no items; the set is not stratified"


def test_benign_controls_are_the_declared_fraction() -> None:
    store = load_label_store(EVAL_ROOT)
    support = store.support()
    benign = support[ThreatClass.BENIGN]

    assert benign / sum(support.values()) == pytest.approx(BENIGN_CONTROL_FRACTION, abs=0.02)


def test_the_manifest_records_the_ceiling_this_phase_measured() -> None:
    """The per-class support is small, and that is the finding, not a defect.

    Asserted as a passing test in the shape STEP-02 used for tail truncation and
    STEP-04 for recovery saturation: the day the generator plants more, this
    fails and forces the sensitivity claim in the Outcome to be rewritten rather
    than quietly outliving its own truth.
    """
    manifest = load_manifest(EVAL_ROOT)
    balance = manifest["class_balance"]
    assert isinstance(balance, dict)

    threat_support = {
        name: count for name, count in balance.items() if name != ThreatClass.BENIGN.value
    }

    assert max(threat_support.values()) <= 12, (
        "a threat class now has more than 12 items. The generator's planted volume has "
        "changed, so the class-collapse-not-drift sensitivity claim in the STEP-06 Outcome "
        "and in orchestrator.regression_gate has to be re-derived"
    )
    assert manifest["label_provenance"]
    assert manifest["dataset_seed"] == 42


def test_the_items_digest_refuses_an_edited_item_file(tmp_path: Path) -> None:
    """A report names the item set it was computed over. An items file edited
    afterwards would make that name false while every number still looked fine."""
    for name in (MANIFEST_FILE, LABELS_FILE, "items.json"):
        (tmp_path / name).write_text(
            (EVAL_ROOT / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    items = json.loads((tmp_path / "items.json").read_text(encoding="utf-8"))
    items[0]["content"] = "something else entirely"
    (tmp_path / "items.json").write_text(json.dumps(items), encoding="utf-8")

    with pytest.raises(EvalSetError, match="has changed since it was built"):
        load_items(tmp_path)


def test_the_labels_digest_refuses_an_edited_label_file(tmp_path: Path) -> None:
    for name in (MANIFEST_FILE, LABELS_FILE, "items.json"):
        (tmp_path / name).write_text(
            (EVAL_ROOT / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    labels = json.loads((tmp_path / LABELS_FILE).read_text(encoding="utf-8"))
    labels["item-0000"] = ThreatClass.BENIGN.value
    (tmp_path / LABELS_FILE).write_text(json.dumps(labels), encoding="utf-8")

    with pytest.raises(EvalSetError, match="has changed since it was built"):
        load_label_store(tmp_path)


# --------------------------------------------------------------------------
# Grading
# --------------------------------------------------------------------------


def _perfect(store: EvalLabelStore, item_ids: list[str]) -> list[ClassificationProposal]:
    """Predictions that are right about everything, built without reading a
    label: the store is asked to confirm, never to reveal."""
    guesses: list[ClassificationProposal] = []
    for item_id in item_ids:
        for member in ThreatClass:
            candidate = ClassificationProposal(item_id=item_id, predicted=member)
            if store.correct_flags([candidate])[0]:
                guesses.append(candidate)
                break
    return guesses


def test_scoring_a_perfect_run_gives_recall_one_everywhere() -> None:
    store = load_label_store(EVAL_ROOT)
    item_ids = [item.item_id for item in load_items(EVAL_ROOT)]

    counts = store.score(_perfect(store, item_ids))

    for member, entry in counts.items():
        if entry.support:
            assert entry.recall == 1.0, f"{member.value} did not reach recall 1.0"


def test_scoring_an_all_benign_run_collapses_every_threat_class() -> None:
    """The degraded shape D7 plants, checked here at the metric level."""
    store = load_label_store(EVAL_ROOT)
    item_ids = [item.item_id for item in load_items(EVAL_ROOT)]

    counts = store.score(
        [
            ClassificationProposal(item_id=item_id, predicted=ThreatClass.BENIGN)
            for item_id in item_ids
        ]
    )

    for member, entry in counts.items():
        if member is ThreatClass.BENIGN:
            assert entry.recall == 1.0
        elif entry.support:
            assert entry.recall == 0.0


def test_predictions_naming_unknown_items_are_refused() -> None:
    """A report computed over a different item set than it names is not a report."""
    store = load_label_store(EVAL_ROOT)

    with pytest.raises(EvalSetError, match="does not carry"):
        store.score([ClassificationProposal(item_id="item-9999", predicted=ThreatClass.BENIGN)])


def test_precision_and_recall_have_no_zero_division() -> None:
    """Totality, in the shape ``_consequence_rank`` established.

    A class nothing predicted has precision 0.0 rather than an exception, and
    the support beside it is what lets a reader tell "found none of four" from
    "there were none".
    """
    store = load_label_store(EVAL_ROOT)
    item_ids = [item.item_id for item in load_items(EVAL_ROOT)]

    counts = store.score(
        [
            ClassificationProposal(item_id=item_id, predicted=ThreatClass.BENIGN)
            for item_id in item_ids
        ]
    )

    unpredicted = counts[ThreatClass.T05_AI_PERSONA_AUTHORITY]
    assert unpredicted.predicted == 0
    assert unpredicted.precision == 0.0
    assert unpredicted.f1 == 0.0
    assert unpredicted.support > 0
