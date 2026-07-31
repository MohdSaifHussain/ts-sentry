# SPDX-License-Identifier: MIT
"""STEP-02 D6: `ts-sentry verify-ledger PATH`.

Exit codes: 0 intact, 4 broken chain (first broken seq printed), 5 input
error, 6 chain-head mismatch.

The equivalence tests are the point of the shared-core requirement: an
exported JSONL and the DuckDB store it came from must not merely both fail,
they must fail at the same seq with the same reason.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from ts_sentry.cli.main import (
    EXIT_BROKEN_CHAIN,
    EXIT_HEAD_MISMATCH,
    EXIT_INPUT_ERROR,
    EXIT_OK,
    InputError,
    main,
    parse_expect_head,
)
from ts_sentry.data.tz import IST

# chain_head moved to governance.ledger in STEP-03: the session manifest needs
# the identical spelling of a chain head and must not import from the CLI.
from ts_sentry.governance.ledger import (
    GENESIS_PREV_HASH,
    EventType,
    Ledger,
    OrchestratorToken,
    chain_head,
    digest_payload,
    read_jsonl,
    read_store,
)
from ts_sentry.governance.mandate import AgentId

_TOKEN = OrchestratorToken(session_id="cli-session")
_MANDATE_HASH = "1" * 64
_TS = datetime(2026, 7, 31, 14, 30, tzinfo=IST)


def _build(store: Path, jsonl: Path, length: int = 5) -> Ledger:
    con = duckdb.connect(str(store))
    ledger = Ledger(con)
    for index in range(length):
        ledger.append(
            _TOKEN,
            timestamp_ist=_TS + timedelta(seconds=index),
            agent_id=None if index == 0 else AgentId.TRIAGE,
            mandate_hash=_MANDATE_HASH,
            event_type=EventType.SESSION_OPEN if index == 0 else EventType.TOOL_CALLED,
            payload_digest=digest_payload({"step": index}),
        )
    ledger.export_jsonl(jsonl)
    con.close()
    return ledger


@pytest.fixture
def artifacts(tmp_path: Path) -> tuple[Path, Path]:
    store = tmp_path / "ledger.duckdb"
    jsonl = tmp_path / "ledger.jsonl"
    _build(store, jsonl)
    return store, jsonl


# --------------------------------------------------------------------------
# Exit 0: intact
# --------------------------------------------------------------------------


def test_intact_jsonl_exits_zero(artifacts: tuple[Path, Path]) -> None:
    _, jsonl = artifacts
    assert main(["verify-ledger", str(jsonl)]) == EXIT_OK


def test_intact_store_exits_zero(artifacts: tuple[Path, Path]) -> None:
    store, _ = artifacts
    assert main(["verify-ledger", str(store)]) == EXIT_OK


def test_head_is_reported_on_stdout(
    artifacts: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Confirmed D6 provision: the head is always reported, so a caller can
    record it without a second tool."""
    _, jsonl = artifacts
    main(["verify-ledger", str(jsonl)])

    out = capsys.readouterr().out
    expected = chain_head(read_jsonl(jsonl))
    assert "entries: 5" in out
    assert expected.entry_hash in out
    assert "intact" in out


def test_an_empty_chain_reports_the_genesis_head(tmp_path: Path) -> None:
    store = tmp_path / "empty.duckdb"
    con = duckdb.connect(str(store))
    Ledger(con)
    con.close()

    assert main(["verify-ledger", str(store)]) == EXIT_OK


# --------------------------------------------------------------------------
# Exit 4: broken chain
# --------------------------------------------------------------------------


def _tamper_store(store: Path, seq: int) -> None:
    con = duckdb.connect(str(store))
    con.execute("UPDATE governance.ledger SET payload_digest = ? WHERE seq = ?;", ["9" * 64, seq])
    con.close()


def _tamper_jsonl(jsonl: Path, seq: int) -> None:
    lines = jsonl.read_text(encoding="utf-8").strip().splitlines()
    obj = json.loads(lines[seq])
    obj["payload_digest"] = "9" * 64
    lines[seq] = json.dumps(obj, sort_keys=True)
    jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_broken_store_exits_four_and_prints_the_seq(
    artifacts: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    store, _ = artifacts
    _tamper_store(store, 3)

    assert main(["verify-ledger", str(store)]) == EXIT_BROKEN_CHAIN

    captured = capsys.readouterr()
    assert "BROKEN CHAIN at seq 3" in captured.out
    assert "seq 3" in captured.err


def test_broken_jsonl_exits_four_and_prints_the_seq(
    artifacts: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _, jsonl = artifacts
    _tamper_jsonl(jsonl, 2)

    assert main(["verify-ledger", str(jsonl)]) == EXIT_BROKEN_CHAIN
    assert "BROKEN CHAIN at seq 2" in capsys.readouterr().out


@pytest.mark.parametrize("seq", [0, 1, 4])
def test_both_readers_report_the_same_first_broken_seq(
    artifacts: tuple[Path, Path], seq: int, capsys: pytest.CaptureFixture[str]
) -> None:
    """Confirmed D6 requirement (c): identical verdicts on a tampered
    fixture, including the same first broken seq, from both readers."""
    store, jsonl = artifacts
    _tamper_store(store, seq)
    _tamper_jsonl(jsonl, seq)

    store_code = main(["verify-ledger", str(store)])
    store_out = capsys.readouterr().out
    jsonl_code = main(["verify-ledger", str(jsonl)])
    jsonl_out = capsys.readouterr().out

    assert store_code == jsonl_code == EXIT_BROKEN_CHAIN
    assert f"BROKEN CHAIN at seq {seq}" in store_out
    assert f"BROKEN CHAIN at seq {seq}" in jsonl_out

    from ts_sentry.governance.ledger import verify_chain

    assert verify_chain(read_store(store)) == verify_chain(read_jsonl(jsonl))


# --------------------------------------------------------------------------
# Exit 5: input errors
# --------------------------------------------------------------------------


def test_missing_file_exits_five(tmp_path: Path) -> None:
    assert main(["verify-ledger", str(tmp_path / "nope.jsonl")]) == EXIT_INPUT_ERROR


def test_unsupported_extension_exits_five(tmp_path: Path) -> None:
    path = tmp_path / "ledger.txt"
    path.write_text("not a ledger", encoding="utf-8")
    assert main(["verify-ledger", str(path)]) == EXIT_INPUT_ERROR


def test_malformed_jsonl_exits_five_not_four(tmp_path: Path) -> None:
    """A file problem must be distinguishable from an integrity failure.

    Unparseable content is not evidence of tampering; it is evidence the tool
    was handed the wrong file.
    """
    path = tmp_path / "ledger.jsonl"
    path.write_text("{ this is not json\n", encoding="utf-8")
    assert main(["verify-ledger", str(path)]) == EXIT_INPUT_ERROR


def test_duckdb_file_without_a_ledger_table_exits_five(tmp_path: Path) -> None:
    path = tmp_path / "dataset.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE main.unrelated (x INTEGER);")
    con.close()
    assert main(["verify-ledger", str(path)]) == EXIT_INPUT_ERROR


# --------------------------------------------------------------------------
# Exit 6: head mismatch
# --------------------------------------------------------------------------


def test_matching_expect_head_exits_zero(artifacts: tuple[Path, Path]) -> None:
    _, jsonl = artifacts
    head = chain_head(read_jsonl(jsonl))
    assert main(["verify-ledger", str(jsonl), "--expect-head", head.render()]) == EXIT_OK


def test_truncated_chain_passes_without_expect_head_and_fails_with_it(
    artifacts: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """The exact gap the comparison verb exists to close.

    A truncated export still verifies, because every remaining link
    recomputes. Only a caller-supplied expectation can catch it, which is why
    this is a comparison and not a claim the chain makes about itself.
    """
    _, jsonl = artifacts
    original_head = chain_head(read_jsonl(jsonl))

    lines = jsonl.read_text(encoding="utf-8").strip().splitlines()
    jsonl.write_text("\n".join(lines[:-2]) + "\n", encoding="utf-8")

    assert main(["verify-ledger", str(jsonl)]) == EXIT_OK
    capsys.readouterr()

    assert (
        main(["verify-ledger", str(jsonl), "--expect-head", original_head.render()])
        == EXIT_HEAD_MISMATCH
    )
    captured = capsys.readouterr()
    assert "HEAD MISMATCH" in captured.out
    assert "removed from the end" in captured.err


def test_a_broken_chain_outranks_a_head_mismatch(
    artifacts: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Precedence: a broken chain makes any head claim meaningless."""
    _, jsonl = artifacts
    _tamper_jsonl(jsonl, 1)

    code = main(["verify-ledger", str(jsonl), "--expect-head", f"99:{'a' * 64}"])
    assert code == EXIT_BROKEN_CHAIN
    assert "BROKEN CHAIN" in capsys.readouterr().out


@pytest.mark.parametrize(
    "raw",
    ["5", "5-abc", "abc:" + "a" * 64, "-1:" + "a" * 64, "5:notahash", "5:" + "A" * 64],
)
def test_malformed_expect_head_exits_five(artifacts: tuple[Path, Path], raw: str) -> None:
    _, jsonl = artifacts
    assert main(["verify-ledger", str(jsonl), "--expect-head", raw]) == EXIT_INPUT_ERROR


def test_missing_option_value_exits_five_not_two(artifacts: tuple[Path, Path]) -> None:
    """Interpreter-independent pin on the 3.12 divergence CI caught.

    On 3.12, ``--expect-head -1:<hash>`` never reaches our validation:
    argparse classifies the dash-prefixed token as an option, because
    "positional arguments may only begin with - if they look like negative
    numbers" and that value does not, so it errors with its own status 2
    before ``parse_expect_head`` runs. On 3.14 the same input is consumed as
    a value and exits 5 through our path.

    This test drives argparse's error path directly, with an option value
    that is genuinely absent on every version, so the translated exit code is
    asserted regardless of which reading the interpreter takes.
    """
    _, jsonl = artifacts
    assert main(["verify-ledger", str(jsonl), "--expect-head"]) == EXIT_INPUT_ERROR


def test_missing_path_exits_five(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["verify-ledger"]) == EXIT_INPUT_ERROR
    assert "verify-ledger:" in capsys.readouterr().err


def test_unknown_flag_exits_five(artifacts: tuple[Path, Path]) -> None:
    _, jsonl = artifacts
    assert main(["verify-ledger", str(jsonl), "--not-a-flag"]) == EXIT_INPUT_ERROR


def test_argparse_never_returns_exit_two_from_verify_ledger(
    artifacts: tuple[Path, Path],
) -> None:
    """The collision this removes.

    argparse's own status 2 means "quality gate failed" elsewhere in this
    CLI. No verify-ledger invocation may produce it, and none may leak a
    bare SystemExit either.
    """
    _, jsonl = artifacts
    malformed = [
        ["verify-ledger"],
        ["verify-ledger", str(jsonl), "--expect-head"],
        ["verify-ledger", str(jsonl), "--expect-head", "-1:" + "a" * 64],
        ["verify-ledger", str(jsonl), "--not-a-flag"],
    ]
    for argv in malformed:
        assert main(argv) == EXIT_INPUT_ERROR


def test_other_subcommands_keep_argparse_behaviour() -> None:
    """The translation is scoped to verify-ledger.

    build-dataset usage errors, and an absent subcommand, still exit through
    argparse exactly as before, so this change cannot silently alter the
    STEP-01 CLI contract.
    """
    with pytest.raises(SystemExit) as missing_args:
        main(["build-dataset"])
    assert missing_args.value.code == 2

    with pytest.raises(SystemExit):
        main([])


def test_parse_expect_head_round_trips() -> None:
    head = parse_expect_head(f"7:{'b' * 64}")
    assert head.count == 7
    assert head.entry_hash == "b" * 64
    assert parse_expect_head(head.render()) == head


def test_parse_expect_head_rejects_a_missing_separator() -> None:
    with pytest.raises(InputError, match="COUNT:HASH"):
        parse_expect_head("7")


def test_empty_chain_head_is_the_genesis_value() -> None:
    head = chain_head(())
    assert head.count == 0
    assert head.entry_hash == GENESIS_PREV_HASH
