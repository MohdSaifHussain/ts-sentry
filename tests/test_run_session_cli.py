# SPDX-License-Identifier: MIT
"""STEP-03 D6: ``run-session``, the session manifest, and ``--expect-head-from``.

The phase's exit criterion lives here: a session that opens, runs one agent
turn, closes with an anchored manifest, and leaves a chain that verifies.

The load-bearing test is
``test_a_truncated_export_is_caught_only_by_the_anchor``. It runs the two
halves of the STEP-02 limitation against a real artifact rather than a
constructed one: bare verification accepts a truncated export, and the
manifest anchor is what refuses it. That is the third obligation carried into
this phase, demonstrated end to end.

Every test here runs offline on the stub and costs nothing.
"""

import json
from pathlib import Path

import duckdb
import pytest
from test_triage import _dataset

from ts_sentry.cli.main import (
    EXIT_BROKEN_CHAIN,
    EXIT_HEAD_MISMATCH,
    EXIT_INPUT_ERROR,
    EXIT_OK,
    main,
)
from ts_sentry.data.store import persist_dataset
from ts_sentry.orchestrator.session_runner import derive_session_id


@pytest.fixture
def dataset_dir(tmp_path: Path) -> Path:
    """A small real dataset, persisted the way ``build-dataset`` would."""
    out = tmp_path / "build"
    out.mkdir()
    con = duckdb.connect(str(out / "build.duckdb"))
    persist_dataset(con, _dataset())
    con.close()
    return out


def _run_session(dataset_dir: Path, out: Path, *extra: str) -> int:
    return main(
        [
            "run-session",
            "--agent",
            "triage",
            "--seed-dataset",
            str(dataset_dir),
            "--out",
            str(out),
            "--analyst-id",
            "saif",
            *extra,
        ]
    )


# --------------------------------------------------------------------------
# The exit criterion
# --------------------------------------------------------------------------


def test_a_full_session_produces_an_intact_ledger_and_every_artifact(
    dataset_dir: Path, tmp_path: Path
) -> None:
    """STEP-03's exit criterion: the first full ledgered session, end to end."""
    out = tmp_path / "session"

    assert _run_session(dataset_dir, out) == EXIT_OK

    for name in (
        "ledger.jsonl",
        "ledger.duckdb",
        "ranked_queue.json",
        "session_events.json",
        "session_manifest.json",
    ):
        assert (out / name).is_file(), f"missing artifact {name}"

    assert main(["verify-ledger", str(out / "ledger.jsonl")]) == EXIT_OK
    assert main(["verify-ledger", str(out / "ledger.duckdb")]) == EXIT_OK


def test_the_export_and_the_store_agree(dataset_dir: Path, tmp_path: Path) -> None:
    """Both readers feed one verification core, so a session's own two
    records of itself cannot disagree."""
    out = tmp_path / "session"
    _run_session(dataset_dir, out)

    manifest = str(out / "session_manifest.json")
    assert (
        main(["verify-ledger", str(out / "ledger.jsonl"), "--expect-head-from", manifest])
        == EXIT_OK
    )
    assert (
        main(["verify-ledger", str(out / "ledger.duckdb"), "--expect-head-from", manifest])
        == EXIT_OK
    )


def test_the_ranked_queue_carries_scores_components_and_rationales(
    dataset_dir: Path, tmp_path: Path
) -> None:
    out = tmp_path / "session"
    _run_session(dataset_dir, out)

    payload = json.loads((out / "ranked_queue.json").read_text(encoding="utf-8"))
    queue = payload["queue"]

    assert queue["row_count"] >= 1
    first = queue["rows"][0]
    assert set(first["components"]) == {"severity_class", "spread", "velocity", "recidivism"}
    assert first["subject_id"].startswith("chan-")
    assert first["rationale"] is not None
    assert payload["weights_version"] and payload["weights_hash"]
    assert payload["detector_version"].endswith("-stub")


def test_every_ledger_entry_has_a_recoverable_payload(dataset_dir: Path, tmp_path: Path) -> None:
    """The chain stores digests; the events artifact stores the bodies. A
    session that cannot show the body behind an entry cannot evidence it."""
    out = tmp_path / "session"
    _run_session(dataset_dir, out)

    entries = [
        json.loads(line)
        for line in (out / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    events = json.loads((out / "session_events.json").read_text(encoding="utf-8"))["events"]

    assert len(entries) == len(events)
    assert [e["seq"] for e in events] == [e["seq"] for e in entries]


def test_the_manifest_anchors_the_finished_chain(dataset_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "session"
    _run_session(dataset_dir, out)

    manifest = json.loads((out / "session_manifest.json").read_text(encoding="utf-8"))
    entries = [
        line
        for line in (out / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    # The anchor counts the SESSION_CLOSE entry, so it was read after the
    # close rather than before it.
    assert manifest["expected_head"]["count"] == len(entries)
    assert manifest["close_reason"] == "completed"
    assert manifest["event_counts"]["session_close"] == 1


# --------------------------------------------------------------------------
# Obligation 3, demonstrated on a real artifact
# --------------------------------------------------------------------------


def test_a_truncated_export_is_caught_only_by_the_anchor(dataset_dir: Path, tmp_path: Path) -> None:
    """Both halves of the STEP-02 limitation, on a session this suite produced.

    Bare verification accepts the truncated file: every remaining link still
    recomputes, so a reader with only that file sees an intact chain and no
    reason to look further. The manifest anchor, written before the
    truncation, is what refuses it. Neither half states the limit correctly on
    its own.
    """
    out = tmp_path / "session"
    _run_session(dataset_dir, out)
    ledger = out / "ledger.jsonl"
    manifest = str(out / "session_manifest.json")

    lines = ledger.read_text(encoding="utf-8").splitlines()
    ledger.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8", newline="\n")

    assert main(["verify-ledger", str(ledger)]) == EXIT_OK  # the limitation
    assert (
        main(["verify-ledger", str(ledger), "--expect-head-from", manifest]) == EXIT_HEAD_MISMATCH
    )


def test_a_tampered_entry_is_caught_without_any_anchor(dataset_dir: Path, tmp_path: Path) -> None:
    """The half chain verification does cover, for contrast."""
    out = tmp_path / "session"
    _run_session(dataset_dir, out)
    ledger = out / "ledger.jsonl"

    lines = ledger.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["payload_digest"] = "9" * 64
    lines[1] = json.dumps(tampered, sort_keys=True)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    assert main(["verify-ledger", str(ledger)]) == EXIT_BROKEN_CHAIN


def test_the_two_expectation_forms_are_mutually_exclusive(
    dataset_dir: Path, tmp_path: Path
) -> None:
    """An expectation supplied twice could disagree with itself, and there is
    no correct way to resolve that."""
    out = tmp_path / "session"
    _run_session(dataset_dir, out)

    assert (
        main(
            [
                "verify-ledger",
                str(out / "ledger.jsonl"),
                "--expect-head",
                f"8:{'a' * 64}",
                "--expect-head-from",
                str(out / "session_manifest.json"),
            ]
        )
        == EXIT_INPUT_ERROR
    )


def test_a_malformed_manifest_is_an_input_error_not_an_integrity_finding(
    dataset_dir: Path, tmp_path: Path
) -> None:
    out = tmp_path / "session"
    _run_session(dataset_dir, out)
    broken = tmp_path / "not-a-manifest.json"
    broken.write_text("{}", encoding="utf-8")

    assert (
        main(["verify-ledger", str(out / "ledger.jsonl"), "--expect-head-from", str(broken)])
        == EXIT_INPUT_ERROR
    )
    assert (
        main(
            [
                "verify-ledger",
                str(out / "ledger.jsonl"),
                "--expect-head-from",
                str(tmp_path / "absent.json"),
            ]
        )
        == EXIT_INPUT_ERROR
    )


# --------------------------------------------------------------------------
# Offline by default, and reproducible
# --------------------------------------------------------------------------


def test_a_session_runs_with_no_environment_configured(
    dataset_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The zero-credential guarantee, at the CLI.

    No mode variable, no key, no vendor package. A complete session still
    runs and still produces every artifact.
    """
    monkeypatch.delenv("TS_SENTRY_LLM_MODE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert _run_session(dataset_dir, tmp_path / "session") == EXIT_OK


def test_live_mode_needs_the_flag_and_the_environment(
    dataset_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The intent has to be expressed twice, in two different places.

    A shell alias or a stray script argument should not be able to start
    spending money, so ``--llm-mode live`` alone is refused.
    """
    monkeypatch.delenv("TS_SENTRY_LLM_MODE", raising=False)

    assert _run_session(dataset_dir, tmp_path / "session", "--llm-mode", "live") == EXIT_INPUT_ERROR


def test_two_runs_of_one_dataset_produce_the_same_queue(dataset_dir: Path, tmp_path: Path) -> None:
    """Reproducibility of the product, which is the ranking.

    The ledger differs between runs because timestamps are real, and that is
    correct - a ledger records when things happened. The ranked queue must
    not.
    """
    first, second = tmp_path / "a", tmp_path / "b"
    _run_session(dataset_dir, first)
    _run_session(dataset_dir, second)

    def queue_of(path: Path) -> object:
        return json.loads((path / "ranked_queue.json").read_text(encoding="utf-8"))["queue"]

    assert queue_of(first) == queue_of(second)


def test_the_session_id_is_derived_from_its_inputs(dataset_dir: Path, tmp_path: Path) -> None:
    """Not random and not the clock, so two runs of the same inputs are
    comparable rather than merely similar."""
    out = tmp_path / "session"
    _run_session(dataset_dir, out)

    manifest = json.loads((out / "session_manifest.json").read_text(encoding="utf-8"))
    assert manifest["session_id"].startswith("session-")
    assert manifest["session_id"] == derive_session_id("saif", manifest["dataset_digest"])


def test_an_explicit_session_id_is_honoured(dataset_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "session"
    _run_session(dataset_dir, out, "--session-id", "session-manual-001")

    manifest = json.loads((out / "session_manifest.json").read_text(encoding="utf-8"))
    assert manifest["session_id"] == "session-manual-001"


def test_a_missing_dataset_is_an_input_error(tmp_path: Path) -> None:
    assert _run_session(tmp_path / "nowhere", tmp_path / "session") == EXIT_INPUT_ERROR


def test_the_limit_bounds_the_queue(dataset_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "session"
    _run_session(dataset_dir, out, "--limit", "1")

    payload = json.loads((out / "ranked_queue.json").read_text(encoding="utf-8"))
    assert payload["queue"]["row_count"] == 1


def test_run_session_rejects_an_unknown_agent(dataset_dir: Path, tmp_path: Path) -> None:
    """Only the agent this build has. ARCHITECTURE names four; three of them
    do not exist yet, and offering them would be offering nothing."""
    with pytest.raises(SystemExit):
        main(
            [
                "run-session",
                "--agent",
                "memo",
                "--seed-dataset",
                str(dataset_dir),
                "--out",
                str(tmp_path / "session"),
            ]
        )
