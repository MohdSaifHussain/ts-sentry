# SPDX-License-Identifier: MIT
"""STEP-03 D1: the session manifest and its chain-head anchor.

This file discharges the third obligation the STEP-02 Outcome carried into
STEP-03. The load-bearing test is
``test_tail_truncation_is_caught_by_the_manifest_anchor``: it is the companion
to ``test_tail_truncation_is_invisible_to_chain_verification_alone`` in
``tests/test_ledger_properties.py``, and the two together state the limitation
at exactly its true width. Chain verification alone still cannot see a
truncated tail, and never will; the anchor is what closes the gap, and only
when it is read from a manifest rather than recomputed from the file under
suspicion.
"""

import json
from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from ts_sentry.data.tz import IST
from ts_sentry.governance.ledger import ChainHead, Ledger, chain_head, read_jsonl, verify_chain
from ts_sentry.governance.mandate import AgentId, Consequence, Mandate, ToolId
from ts_sentry.governance.scopes import DataScope
from ts_sentry.orchestrator.core import CloseReason, FixedClock, Session
from ts_sentry.orchestrator.manifest import (
    MANIFEST_VERSION,
    ArtifactRecord,
    ManifestError,
    SessionManifest,
    read_expected_head,
)
from ts_sentry.provenance import UNKNOWN_GIT_SHA, git_sha, sha256_file

_START = datetime(2026, 7, 31, 14, 30, tzinfo=IST)
_DATASET_DIGEST = "a" * 64


class _RankedQueue:
    """Stand-in output schema. The real one arrives with D5."""


def _mandate() -> Mandate:
    return Mandate(
        agent_id=AgentId.TRIAGE,
        version="1.0.0",
        consequence_ceiling=Consequence.OBSERVE,
        allowed_tools=frozenset({ToolId.RANK_TRIAGE_QUEUE}),
        data_scopes=frozenset({DataScope.COMMENT}),
        output_schema=_RankedQueue,
        token_budget=1_000,
        max_steps=4,
    )


def _run_session(out_dir: Path) -> tuple[Session, Path, Path]:
    """Open, do a little work, close, export, and write the manifest.

    Returns the session plus the two paths a truncation attack cares about.
    """
    session = Session(
        session_id="session-001",
        analyst_id="saif",
        ledger=Ledger(duckdb.connect(":memory:")),
        clock=FixedClock(_START),
        mandates={AgentId.TRIAGE: _mandate()},
        dataset_digest=_DATASET_DIGEST,
    )
    session.open()
    session.begin_turn(AgentId.TRIAGE)
    session.end_turn()
    closed = session.close(CloseReason.COMPLETED)

    ledger_path = out_dir / "ledger.jsonl"
    session.ledger.export_jsonl(ledger_path)

    manifest_path = out_dir / "session_manifest.json"
    assert session.opened_ts is not None
    assert session.closed_ts is not None
    SessionManifest(
        session_id=session.session_id,
        analyst_id=session.analyst_id,
        opened_ts_iso=session.opened_ts.isoformat(),
        closed_ts_iso=session.closed_ts.isoformat(),
        close_reason=closed.reason,
        dataset_digest=session.dataset_digest,
        mandate_set_hash=session.mandate_set_hash,
        mandate_hashes={AgentId.TRIAGE.value: session.binding(AgentId.TRIAGE).hash},
        expected_head=closed.head,
        event_counts=session.event_counts(),
        budgets={AgentId.TRIAGE.value: session.budget(AgentId.TRIAGE).snapshot()},
        git_sha=git_sha(),
        artifacts=[ArtifactRecord.of("ledger", ledger_path, relative_to=out_dir)],
    ).write(manifest_path)

    return session, ledger_path, manifest_path


# --------------------------------------------------------------------------
# The anchor
# --------------------------------------------------------------------------


def test_the_manifest_anchors_the_head_of_the_exported_chain(tmp_path: Path) -> None:
    session, ledger_path, manifest_path = _run_session(tmp_path)

    anchored = read_expected_head(manifest_path)

    assert anchored == chain_head(read_jsonl(ledger_path))
    assert anchored == session.ledger.head


def test_tail_truncation_is_caught_by_the_manifest_anchor(tmp_path: Path) -> None:
    """The companion to the limitation in tests/test_ledger_properties.py.

    Both halves are asserted in one place on purpose. The truncated export
    still *verifies*: every remaining link recomputes, so a reader with only
    the file in front of them sees an intact chain and no reason to look
    further. It is the anchor, read from a record written before the
    truncation, that says the chain is shorter than the session it claims to
    describe.
    """
    _, ledger_path, manifest_path = _run_session(tmp_path)
    anchored = read_expected_head(manifest_path)

    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    ledger_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8", newline="\n")

    truncated = read_jsonl(ledger_path)
    assert verify_chain(truncated).intact, "chain verification alone still cannot see this"

    actual = chain_head(truncated)
    assert actual != anchored
    assert actual.count == anchored.count - 1


def test_the_anchor_is_only_as_independent_as_its_custody(tmp_path: Path) -> None:
    """The honest limit, asserted rather than only written down.

    Anyone who can truncate the ledger can also rewrite a manifest sitting
    beside it, and the pair then agrees again. This test exists so the claim
    in the module docstring cannot quietly widen into "the anchor detects
    truncation", full stop.
    """
    _, ledger_path, manifest_path = _run_session(tmp_path)

    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    ledger_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8", newline="\n")
    truncated_head = chain_head(read_jsonl(ledger_path))

    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["expected_head"] = {
        "count": truncated_head.count,
        "entry_hash": truncated_head.entry_hash,
    }
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")

    assert read_expected_head(manifest_path) == truncated_head


# --------------------------------------------------------------------------
# Manifest content and provenance
# --------------------------------------------------------------------------


def test_the_manifest_records_what_produced_the_session(tmp_path: Path) -> None:
    session, ledger_path, manifest_path = _run_session(tmp_path)
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert raw["manifest_version"] == MANIFEST_VERSION
    assert raw["session_id"] == "session-001"
    assert raw["analyst_id"] == "saif"
    assert raw["close_reason"] == CloseReason.COMPLETED.value
    assert raw["dataset_digest"] == _DATASET_DIGEST
    assert raw["mandate_set_hash"] == session.mandate_set_hash
    assert raw["event_counts"]["session_open"] == 1
    assert raw["budgets"]["triage"]["steps_taken"] == 1
    assert raw["artifacts"][0]["path"] == "ledger.jsonl"
    assert raw["artifacts"][0]["sha256"] == sha256_file(ledger_path)


def test_artifact_records_are_relative_so_a_session_directory_stays_movable(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "session" / "exports"
    nested.mkdir(parents=True)
    artifact = nested / "queue.json"
    artifact.write_text("[]", encoding="utf-8")

    record = ArtifactRecord.of("queue", artifact, relative_to=tmp_path)

    assert record.path == "session/exports/queue.json"
    assert record.sha256 == sha256_file(artifact)


def test_artifact_records_validate_their_own_fields(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        ArtifactRecord(name=" ", path="x", sha256="a" * 64)
    with pytest.raises(ValueError, match="sha256"):
        ArtifactRecord(name="x", path="x", sha256="short")


def test_git_sha_is_a_value_rather_than_an_omission() -> None:
    """A manifest that silently drops its provenance stamp is worse than one
    that says it could not take it."""
    sha = git_sha()
    assert sha == UNKNOWN_GIT_SHA or len(sha) == 40


def _manifest(**overrides: object) -> SessionManifest:
    fields: dict[str, object] = {
        "session_id": "s",
        "analyst_id": "a",
        "opened_ts_iso": _START.isoformat(),
        "closed_ts_iso": _START.isoformat(),
        "close_reason": CloseReason.COMPLETED,
        "dataset_digest": _DATASET_DIGEST,
        "mandate_set_hash": "b" * 64,
        "mandate_hashes": {},
        "expected_head": chain_head(()),
        "event_counts": {},
        "budgets": {},
        "git_sha": UNKNOWN_GIT_SHA,
        "artifacts": [],
    }
    fields.update(overrides)
    return SessionManifest(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"dataset_digest": "nope"}, "dataset_digest"),
        ({"mandate_set_hash": "nope"}, "mandate_set_hash"),
        ({"expected_head": ChainHead(count=1, entry_hash="nope")}, "expected_head.entry_hash"),
        ({"expected_head": ChainHead(count=-1, entry_hash="c" * 64)}, "non-negative"),
    ],
)
def test_the_manifest_validates_the_anchor_it_carries(
    override: dict[str, object], message: str
) -> None:
    """An anchor nobody can compare against is worse than no anchor: it looks
    like a control while refusing to act as one."""
    with pytest.raises(ValueError, match=message):
        _manifest(**override)


# --------------------------------------------------------------------------
# Reading an anchor back
# --------------------------------------------------------------------------


def test_a_missing_manifest_is_an_input_error_not_an_integrity_finding(
    tmp_path: Path,
) -> None:
    with pytest.raises(ManifestError, match="could not read"):
        read_expected_head(tmp_path / "absent.json")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("not json at all", "not valid JSON"),
        ("[]", "does not contain a JSON object"),
        ('{"expected_head": {}}', "carries no manifest_version"),
        ('{"manifest_version": "9.0.0", "expected_head": {}}', "manifest format 9.0.0"),
        ('{"manifest_version": "1.0.0"}', "carries no expected_head"),
        (
            '{"manifest_version": "1.0.0", "expected_head": {"count": -1, "entry_hash": "'
            + "a" * 64
            + '"}}',
            "non-negative integer",
        ),
        (
            '{"manifest_version": "1.0.0", "expected_head": {"count": true, "entry_hash": "'
            + "a" * 64
            + '"}}',
            "non-negative integer",
        ),
        (
            '{"manifest_version": "1.0.0", "expected_head": {"count": 1, "entry_hash": 7}}',
            "must be a string",
        ),
        (
            '{"manifest_version": "1.0.0", "expected_head": {"count": 1, "entry_hash": "nope"}}',
            "SHA-256 hex digest",
        ),
    ],
)
def test_a_malformed_manifest_is_refused_with_a_reason(
    tmp_path: Path, body: str, message: str
) -> None:
    path = tmp_path / "session_manifest.json"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(ManifestError, match=message):
        read_expected_head(path)
