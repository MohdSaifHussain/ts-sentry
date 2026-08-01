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
    EXIT_QUALITY_GATE_FAIL,
    main,
)
from ts_sentry.data.store import export_dataset, persist_dataset
from ts_sentry.governance.scopes import DataScope, resolve_export_path
from ts_sentry.orchestrator.session_runner import derive_session_id
from ts_sentry.provenance import BUILD_MANIFEST, dataset_digest_from_manifest, sha256_file


@pytest.fixture
def dataset_dir(tmp_path: Path) -> Path:
    """A small real dataset, persisted and exported the way ``build-dataset`` would.

    The Parquet exports and the build manifest are written too, and that stopped
    being optional in STEP-04. A session's ``dataset_digest`` now derives from
    the manifest's table hashes rather than from ``build.duckdb``, because the
    store is not byte-stable across rebuilds and the Parquet exports are. A
    fixture that skipped them was a fixture describing a build that
    ``build-dataset`` cannot produce.
    """
    out = tmp_path / "build"
    out.mkdir()
    con = duckdb.connect(str(out / "build.duckdb"))
    persist_dataset(con, _dataset())
    export_dataset(con, out)
    con.close()
    (out / BUILD_MANIFEST).write_text(
        json.dumps(
            {
                "seed": 42,
                "scale": 1,
                "table_hashes": {
                    scope.value: sha256_file(resolve_export_path(scope, out)) for scope in DataScope
                },
            }
        ),
        encoding="utf-8",
    )
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


# --------------------------------------------------------------------------
# STEP-08 8.B: the stub mode is provenance, not a hidden switch
# --------------------------------------------------------------------------


def test_a_faithful_run_says_so_rather_than_staying_silent(
    dataset_dir: Path, tmp_path: Path
) -> None:
    """The default is faithful, and a default run positively records that.

    Recording it only when it is interesting would make silence mean faithful,
    and then an artifact that never recorded the field at all would read as a
    guarantee nobody gave.
    """
    out = tmp_path / "session"
    assert _run_session(dataset_dir, out) == EXIT_OK

    manifest = json.loads((out / "session_manifest.json").read_text(encoding="utf-8"))
    assert manifest["model_mode"] == "stub"
    assert manifest["stub_mode"] == "faithful"


def test_an_overclaim_run_is_self_identifying_in_both_artifacts(
    dataset_dir: Path, tmp_path: Path
) -> None:
    """The manifest stamp and the ledgered ``SESSION_OPEN`` entry both say it.

    Two places because they answer to different threats. The manifest is what
    a reader opens; the ledger entry is what a hash chain protects. An
    overclaim session that only admitted it in the manifest could be laundered
    into a faithful-looking one by editing a file nothing verifies.
    """
    out = tmp_path / "session"
    assert _run_session(dataset_dir, out, "--stub-mode", "overclaim") == EXIT_OK

    manifest = json.loads((out / "session_manifest.json").read_text(encoding="utf-8"))
    assert manifest["stub_mode"] == "overclaim"

    entries = [json.loads(line) for line in (out / "ledger.jsonl").read_text().splitlines()]
    opens = [e for e in entries if e["event_type"] == "session_open"]
    assert len(opens) == 1

    events = json.loads((out / "session_events.json").read_text(encoding="utf-8"))["events"]
    open_payloads = [e["payload"] for e in events if e["event_type"] == "session_open"]
    assert len(open_payloads) == 1
    assert open_payloads[0]["stub_mode"] == "overclaim"

    # The chain still verifies, so the field is inside what the hashes cover
    # rather than appended somewhere convenient.
    assert main(["verify-ledger", str(out / "ledger.jsonl")]) == EXIT_OK


def test_a_stub_mode_is_refused_under_live_rather_than_ignored(
    dataset_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """There is no stub to put in a mode, so the combination is a broken call.

    Ignoring the flag would let a command line say something about a run that
    was not true of it, which is the failure this whole field exists to
    prevent, reintroduced at the argument parser.
    """
    monkeypatch.setenv("TS_SENTRY_LLM_MODE", "live")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-read-by-this-repository")

    assert (
        _run_session(
            dataset_dir, tmp_path / "session", "--llm-mode", "live", "--stub-mode", "overclaim"
        )
        == EXIT_INPUT_ERROR
    )


def test_the_transient_and_refuse_modes_are_not_on_the_command_line(
    dataset_dir: Path, tmp_path: Path
) -> None:
    """Only the two modes an example needs are exposed, and that is deliberate.

    ``TRANSIENT`` and ``REFUSE`` simulate failures at the model boundary rather
    than governance outcomes, so they demonstrate nothing a curated session
    artifact would show. Asserted rather than left to the choices list, so
    widening the surface is a visible decision rather than a one-word edit.
    """
    assert _run_session(dataset_dir, tmp_path / "s", "--stub-mode", "transient") == EXIT_INPUT_ERROR
    assert _run_session(dataset_dir, tmp_path / "s", "--stub-mode", "refuse") == EXIT_INPUT_ERROR


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
    comparable rather than merely similar.

    The agent is part of the derivation as of STEP-04. With one kind of session
    in the world, analyst plus dataset identified a session; with two, an
    evidence session came back carrying the same id as the triage session before
    it. See ``tests/test_evidence_session_cli.py`` for that case.
    """
    out = tmp_path / "session"
    _run_session(dataset_dir, out)

    manifest = json.loads((out / "session_manifest.json").read_text(encoding="utf-8"))
    assert manifest["session_id"].startswith("session-")
    assert manifest["session_id"] == derive_session_id("saif", manifest["dataset_digest"], "triage")
    assert manifest["dataset_digest"] == dataset_digest_from_manifest(dataset_dir)


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
    do not exist yet, and offering them would be offering nothing.

    Asserted as exit 5 rather than a raised ``SystemExit``: naming an agent
    that does not exist is an input error, and since the STEP-03 follow-up
    every malformed ``run-session`` invocation returns that code instead of
    letting argparse exit 2.
    """
    assert (
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
        == EXIT_INPUT_ERROR
    )


# --------------------------------------------------------------------------
# The exit-code contract (STEP-03 follow-up)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["run-session"], id="no-arguments"),
        pytest.param(["run-session", "--agent", "triage"], id="missing-seed-dataset"),
        pytest.param(["run-session", "--seed-dataset", "x"], id="missing-agent"),
        pytest.param(["run-session", "--agent", "memo", "--seed-dataset", "x"], id="unknown-agent"),
        pytest.param(
            ["run-session", "--agent", "triage", "--seed-dataset", "x", "--not-a-flag"],
            id="unrecognized-flag",
        ),
        pytest.param(
            ["run-session", "--agent", "triage", "--seed-dataset", "x", "--limit", "nope"],
            id="non-integer-limit",
        ),
    ],
)
def test_a_malformed_run_session_invocation_exits_five(argv: list[str]) -> None:
    """Argparse exits 2 on a usage error, and 2 is EXIT_QUALITY_GATE_FAIL in
    this CLI, so a mistyped flag would be indistinguishable from a failed
    data-quality gate.

    STEP-02 removed that collision for verify-ledger. ``run-session`` arrived
    in STEP-03 without the translation and reintroduced it, which is a defect
    in a documented contract rather than a stylistic gap: the README listed
    run-session as exiting 0, 4 or 5, and it exited 2.

    The unrecognized-flag case is the one that needs the root-parser branch:
    leftovers are reported by the *root* parser even when a subcommand was
    named, because ``parse_args`` collects them from ``parse_known_args`` and
    errors on them itself.
    """
    assert main(argv) == EXIT_INPUT_ERROR


def test_no_run_session_invocation_returns_exit_two() -> None:
    """The never-exits-2 invariant, extended from verify-ledger to
    run-session.

    Stated as "never 2" rather than "5 for these inputs" on purpose: the point
    is that this subcommand can never collide with the quality-gate code, for
    any input, not that a particular list of inputs happens to map correctly.
    """
    invocations = [
        ["run-session"],
        ["run-session", "--agent"],
        ["run-session", "--agent", "triage"],
        ["run-session", "--agent", "nonsense"],
        ["run-session", "--seed-dataset"],
        ["run-session", "--llm-mode", "telepathy"],
        ["run-session", "--agent", "triage", "--seed-dataset", "x", "--seed", "not-a-number"],
        ["run-session", "--agent", "triage", "--seed-dataset", "x", "-1"],
    ]
    for argv in invocations:
        assert main(argv) != EXIT_QUALITY_GATE_FAIL, argv


def test_build_dataset_keeps_argparse_exit_two() -> None:
    """The STEP-01 contract is untouched.

    ``build-dataset`` has exited 2 on usage errors since STEP-01 and that is
    its published behavior. Translating it here would alter a closed phase for
    tidiness, so it is deliberately absent from the translating set.
    """
    with pytest.raises(SystemExit) as excinfo:
        main(["build-dataset", "--seed", "42", "--not-a-flag"])

    assert excinfo.value.code == 2
