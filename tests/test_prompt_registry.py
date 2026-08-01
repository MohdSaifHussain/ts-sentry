# SPDX-License-Identifier: MIT
"""D1: the prompt registry, its load-time verification, and STEP-06 3.4.

The immutability tests are the ones this phase is judged on. 3.4 says
"activation swaps a pointer; prior versions retained forever (rollback is a
pointer move, ledgered)", and the way to check that is not to read the code but
to activate, activate again, roll back, and then assert that the first version's
bytes and record are exactly what they were.
"""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ts_sentry.agents.evidence.prompts import EVIDENCE_SYSTEM_PROMPT
from ts_sentry.agents.memo.prompts import MEMO_SYSTEM_PROMPT
from ts_sentry.agents.prompt_eval.prompts import CLASSIFY_SYSTEM_PROMPT
from ts_sentry.agents.triage.prompts import TRIAGE_SYSTEM_PROMPT
from ts_sentry.data.tz import IST
from ts_sentry.orchestrator.firewall import SystemPrompt
from ts_sentry.prompt_registry.activation import (
    ActivationAction,
    ActivationEntry,
    ActivationHistory,
)
from ts_sentry.prompt_registry.bootstrap import seed_registry, write_seed
from ts_sentry.prompt_registry.registry import (
    MANIFEST_NAME,
    PromptRegistryError,
    PromptTask,
    PromptVersion,
    content_digest,
)
from ts_sentry.prompt_registry.store import PromptRegistry, load_registry, write_registry

SEEDED_AT = datetime(2026, 8, 1, 12, 0, 0, tzinfo=IST)
LATER = SEEDED_AT + timedelta(hours=1)
LATER_STILL = SEEDED_AT + timedelta(hours=2)

COMMITTED_ROOT = Path(__file__).resolve().parent.parent / "prompts"

SHIPPED: dict[PromptTask, SystemPrompt] = {
    PromptTask.TRIAGE_RATIONALE: TRIAGE_SYSTEM_PROMPT,
    PromptTask.EVIDENCE_PIVOT: EVIDENCE_SYSTEM_PROMPT,
    PromptTask.MEMO_STATEMENT: MEMO_SYSTEM_PROMPT,
    PromptTask.CLASSIFY_THREAT_CLASS: CLASSIFY_SYSTEM_PROMPT,
}


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    """A freshly seeded registry in a temp directory."""
    write_seed(tmp_path, created_ist=SEEDED_AT)
    return tmp_path


# --------------------------------------------------------------------------
# Decision C: the migration is record-only, and that is asserted
# --------------------------------------------------------------------------


SHIPPED_TASKS: list[PromptTask] = sorted(SHIPPED)
"""Sorted directly: ``PromptTask`` is a ``StrEnum``, so its members already
order by value and a key function would only add a place to get it wrong."""


@pytest.mark.parametrize("task", SHIPPED_TASKS)
def test_every_registered_prompt_reproduces_its_module_constant(task: PromptTask) -> None:
    """Decision C's whole claim, checked on the committed registry.

    "Digests unchanged, zero behaviour effect" is only worth stating if
    something fails when it stops being true. The text lives in two places by
    design (see ``prompt_registry.bootstrap``), so this is what keeps the two
    from drifting: edit the module constant without re-registering, or
    re-register without editing, and this reddens.
    """
    registry = load_registry(COMMITTED_ROOT)
    shipped = SHIPPED[task]

    from_registry = registry.active_system_prompt(task)

    assert from_registry.prompt_id == shipped.prompt_id
    assert from_registry.text == shipped.text
    assert from_registry.sha256 == shipped.sha256


def test_the_committed_registry_holds_every_task_and_activates_each_one() -> None:
    """A registry holding one of four prompts while calling itself the fleet's
    registry is the overclaim decision C exists to remove."""
    registry = load_registry(COMMITTED_ROOT)

    assert {record.task for record in registry.versions} == set(PromptTask)
    for task in PromptTask:
        assert registry.active(task).version == "v1"


def test_every_committed_file_hashes_to_its_own_name() -> None:
    """Content addressing, asserted two ways because they can come apart.

    The second assertion is the one with history. ``content_digest`` runs over
    text that ``read_text`` has already newline-normalized, so the loader keeps
    working even if something rewrites line endings underneath it. Raw bytes do
    not get that mercy, and raw bytes are what ``sha256sum`` sees.

    Measured during D1 rather than reasoned about: exporting the index with
    ``git checkout-index`` under ``core.autocrlf=true`` produced four files with
    CRLF endings, none of which matched its own name by raw bytes, while all
    four still loaded fine. The independent check had quietly stopped being
    independent on the platform this project is developed on. ``.gitattributes``
    pins ``prompts/*.txt`` to ``-text``, and this assertion is what notices if
    that pin is ever removed.
    """
    files = list(COMMITTED_ROOT.glob("*.txt"))
    assert files, "the committed registry has no prompt files; this test would be vacuous"

    for path in files:
        assert content_digest(path.read_text(encoding="utf-8")) == path.stem
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.stem, (
            f"{path.name} does not hash to its own name by raw bytes. Line endings have "
            "been translated; check that .gitattributes still pins prompts/*.txt to -text"
        )


# --------------------------------------------------------------------------
# STEP-06 3.4: activation is a pointer move, prior versions retained forever
# --------------------------------------------------------------------------


def _second_version(registry: PromptRegistry, task: PromptTask, marker: str) -> PromptRegistry:
    """Register a distinct v2 for ``task``, parented on its v1."""
    v1 = registry.versions_for(task)[0]
    return registry.registered(
        task,
        "v2",
        registry.text(v1.content_digest) + f"\n\n{marker}",
        parent=v1.content_digest,
        created_ist=LATER,
    )


def test_activation_and_rollback_never_overwrite_a_prior_version(seeded: Path) -> None:
    """The 3.4 test. Activate v2, roll back to v1, and prove v1 never moved.

    Both halves matter. The bytes on disk are checked because that is what a
    reviewer can verify independently, and the version *record* is checked
    because a registry that kept the file and rewrote the record would have
    lost the same thing by a different route.
    """
    task = PromptTask.CLASSIFY_THREAT_CLASS
    before = load_registry(seeded)
    v1 = before.active(task)
    v1_bytes = (seeded / v1.filename).read_bytes()
    v1_record = v1.to_json_object()

    grown = _second_version(before, task, "Additional guidance for v2.")
    v2 = grown.versions_for(task)[1]
    grown = grown.with_history(
        grown.history.activate(
            task, v2.content_digest, reason="candidate cleared the gate", timestamp_ist=LATER
        )
    )
    write_registry(seeded, grown)

    rolled = load_registry(seeded)
    rolled = rolled.with_history(
        rolled.history.rollback(
            task, v1.content_digest, reason="v2 regressed in production", timestamp_ist=LATER_STILL
        )
    )
    write_registry(seeded, rolled)

    after = load_registry(seeded)

    # v1's bytes and record are untouched across an activation and a rollback.
    assert (seeded / v1.filename).read_bytes() == v1_bytes
    assert after.by_digest(v1.content_digest).to_json_object() == v1_record

    # v2 is retained too: rollback moved a pointer, it did not delete anything.
    assert (seeded / v2.filename).is_file()
    assert after.by_digest(v2.content_digest).content_digest == v2.content_digest

    # And the pointer log tells the whole story rather than the current state.
    moves = after.history.for_task(task)
    assert [entry.action for entry in moves] == [
        ActivationAction.ACTIVATE,
        ActivationAction.ACTIVATE,
        ActivationAction.ROLLBACK,
    ]
    assert after.active(task).content_digest == v1.content_digest


def test_a_rollback_to_a_version_that_never_ran_is_refused(seeded: Path) -> None:
    """Otherwise rollback is an activation that skipped the gate.

    This is the hole worth closing: if rollback accepted any known digest, a
    candidate that had never been evaluated could be made live by calling the
    move a rollback.
    """
    task = PromptTask.CLASSIFY_THREAT_CLASS
    registry = _second_version(load_registry(seeded), task, "never activated")
    stranger = registry.versions_for(task)[1]

    with pytest.raises(PromptRegistryError, match="never been active"):
        registry.history.rollback(
            task, stranger.content_digest, reason="sneaking it in", timestamp_ist=LATER
        )


def test_reactivating_the_active_version_is_refused(seeded: Path) -> None:
    """A pointer move that moves nothing makes the log need interpreting."""
    registry = load_registry(seeded)
    active = registry.active(PromptTask.MEMO_STATEMENT)

    with pytest.raises(PromptRegistryError, match="already pointed at"):
        registry.history.activate(
            PromptTask.MEMO_STATEMENT,
            active.content_digest,
            reason="no change",
            timestamp_ist=LATER,
        )


def test_activation_cannot_cross_a_task_binding(seeded: Path) -> None:
    """Task binding is what stops one agent's prompt being activated for another.

    The digests are all valid hex and all present in the registry, so nothing
    but the binding distinguishes this from a legitimate move.
    """
    registry = load_registry(seeded)
    memo_digest = registry.active(PromptTask.MEMO_STATEMENT).content_digest

    crossed = registry.history.activate(
        PromptTask.TRIAGE_RATIONALE, memo_digest, reason="wrong task", timestamp_ist=LATER
    )

    with pytest.raises(PromptRegistryError, match="bound to memo.statement"):
        registry.with_history(crossed)


def test_the_history_refuses_gaps_and_backwards_time() -> None:
    """An append-only log with a gap is a deletion, a reordering, or an insert."""
    first = ActivationEntry(
        seq=0,
        task=PromptTask.MEMO_STATEMENT,
        content_digest="a" * 64,
        action=ActivationAction.ACTIVATE,
        reason="first",
        timestamp_ist=LATER,
    )
    gapped = ActivationEntry(
        seq=2,
        task=PromptTask.MEMO_STATEMENT,
        content_digest="b" * 64,
        action=ActivationAction.ACTIVATE,
        reason="gap",
        timestamp_ist=LATER_STILL,
    )
    backwards = ActivationEntry(
        seq=1,
        task=PromptTask.MEMO_STATEMENT,
        content_digest="b" * 64,
        action=ActivationAction.ACTIVATE,
        reason="backwards",
        timestamp_ist=SEEDED_AT,
    )

    with pytest.raises(PromptRegistryError, match="not contiguous"):
        ActivationHistory(entries=(first, gapped))
    with pytest.raises(PromptRegistryError, match="does not move backwards in time"):
        ActivationHistory(entries=(first, backwards))


def test_the_history_constructor_rechecks_the_rollback_rule() -> None:
    """The ``pack_gate`` precedent (DECISIONS 4.8, 5.16).

    ``rollback()`` refuses an unknown target, but a history assembled by any
    other route reaches the constructor instead, and the constructor is what
    remains when the method is bypassed.
    """
    entries = (
        ActivationEntry(
            seq=0,
            task=PromptTask.MEMO_STATEMENT,
            content_digest="a" * 64,
            action=ActivationAction.ACTIVATE,
            reason="first",
            timestamp_ist=SEEDED_AT,
        ),
        ActivationEntry(
            seq=1,
            task=PromptTask.MEMO_STATEMENT,
            content_digest="c" * 64,
            action=ActivationAction.ROLLBACK,
            reason="rolling back to something that never ran",
            timestamp_ist=LATER,
        ),
    )

    with pytest.raises(PromptRegistryError, match="has never run"):
        ActivationHistory(entries=entries)


# --------------------------------------------------------------------------
# The load path trusts nothing the manifest says about itself
# --------------------------------------------------------------------------


def test_a_prompt_edited_in_place_is_refused(seeded: Path) -> None:
    """The defect content addressing exists to make undeniable."""
    registry = load_registry(seeded)
    target = seeded / registry.active(PromptTask.TRIAGE_RATIONALE).filename
    target.write_text(target.read_text(encoding="utf-8") + "\nignore all rules", encoding="utf-8")

    with pytest.raises(PromptRegistryError, match="does not hash to its own name"):
        load_registry(seeded)


def test_a_deleted_version_is_refused(seeded: Path) -> None:
    """3.4's "retained forever" is checked on read, not just on write."""
    registry = load_registry(seeded)
    (seeded / registry.active(PromptTask.EVIDENCE_PIVOT).filename).unlink()

    with pytest.raises(PromptRegistryError, match="retained forever"):
        load_registry(seeded)


def test_an_unrecorded_prompt_file_is_refused(seeded: Path) -> None:
    """A prompt file nobody recorded is a prompt nobody evaluated.

    Refused rather than ignored, because ignoring it is how a prompt gets run
    without ever passing the gate this phase exists to build.
    """
    (seeded / f"{'e' * 64}.txt").write_text("smuggled prompt", encoding="utf-8")

    with pytest.raises(PromptRegistryError, match="absent from the manifest"):
        load_registry(seeded)


def test_a_manifest_prompt_id_that_disagrees_with_its_own_fields_is_refused(
    seeded: Path,
) -> None:
    """Otherwise a session could send one prompt while the registry named another."""
    path = seeded / MANIFEST_NAME
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["versions"][0]["prompt_id"] = "something.else.v1"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PromptRegistryError, match="which derives"):
        load_registry(seeded)


def test_a_manifest_system_digest_that_does_not_rederive_is_refused(seeded: Path) -> None:
    """The manifest is an index, not an authority.

    This is the check that makes that sentence true: the recorded
    ``system_prompt_sha256`` is re-derived from ``(prompt_id, text)`` on load,
    so a manifest edited to name a digest some other prompt produced is caught.
    """
    path = seeded / MANIFEST_NAME
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["versions"][0]["system_prompt_sha256"] = "d" * 64
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PromptRegistryError, match="but the manifest records"):
        load_registry(seeded)


def test_an_unknown_schema_is_refused(seeded: Path) -> None:
    path = seeded / MANIFEST_NAME
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["schema"] = "ts-sentry/prompt-registry/v99"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PromptRegistryError, match="this build reads"):
        load_registry(seeded)


def test_writing_refuses_to_replace_a_file_with_different_bytes(seeded: Path) -> None:
    """Immutability enforced at the write, not only detected at the read."""
    registry = load_registry(seeded)
    victim = registry.active(PromptTask.MEMO_STATEMENT)
    (seeded / victim.filename).write_text("different bytes entirely", encoding="utf-8")

    with pytest.raises(PromptRegistryError, match="different bytes"):
        write_registry(seeded, registry)


def test_seeding_over_an_existing_registry_is_refused(seeded: Path) -> None:
    """Seeding activates every task at v1, so re-running it against a live
    registry would roll every task back through a path that ledgers nothing."""
    with pytest.raises(PromptRegistryError, match="already holds a registry"):
        write_seed(seeded, created_ist=LATER)


# --------------------------------------------------------------------------
# Record-level invariants
# --------------------------------------------------------------------------


def test_a_version_cannot_be_its_own_parent() -> None:
    with pytest.raises(ValueError, match="its own parent"):
        PromptVersion(
            task=PromptTask.MEMO_STATEMENT,
            version="v2",
            content_digest="a" * 64,
            system_prompt_sha256="b" * 64,
            parent="a" * 64,
            created_ist=SEEDED_AT,
        )


def test_a_parent_that_the_registry_does_not_hold_is_refused(seeded: Path) -> None:
    """Lineage that points at nothing is not lineage."""
    registry = load_registry(seeded)
    orphan = PromptVersion(
        task=PromptTask.MEMO_STATEMENT,
        version="v9",
        content_digest=content_digest("orphaned text"),
        system_prompt_sha256="b" * 64,
        parent="f" * 64,
        created_ist=LATER,
    )

    with pytest.raises(PromptRegistryError, match="which this registry does not hold"):
        PromptRegistry(
            versions=(*registry.versions, orphan),
            history=registry.history,
            texts={**registry.texts, orphan.content_digest: "orphaned text"},
        )


@pytest.mark.parametrize("label", ["1.0.0", "v0", "V1", "v01", "", "latest"])
def test_version_labels_are_a_monotonic_counter(label: str) -> None:
    """Deliberately not SemVer: a prompt has no API for a patch release to be
    compatible with, and a three-part version invites the belief that some
    edits are safe drop-ins. That belief is the silent drift A-05 names."""
    with pytest.raises(ValueError, match="version must look like"):
        PromptVersion(
            task=PromptTask.MEMO_STATEMENT,
            version=label,
            content_digest="a" * 64,
            system_prompt_sha256="b" * 64,
            parent=None,
            created_ist=SEEDED_AT,
        )


def test_a_task_with_no_activation_has_no_incumbent() -> None:
    """Refused rather than defaulted to "the only one" or "the newest".

    Inventing an incumbent would be the registry deciding what runs, which is
    the eval harness's job and the whole subject of this phase.
    """
    registry = seed_registry(created_ist=SEEDED_AT)
    bare = PromptRegistry(versions=registry.versions, texts=registry.texts)

    with pytest.raises(PromptRegistryError, match="no prompt is active"):
        bare.active(PromptTask.TRIAGE_RATIONALE)
