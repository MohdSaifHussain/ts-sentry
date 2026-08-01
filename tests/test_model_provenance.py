# SPDX-License-Identifier: MIT
"""STEP-08 8.B: which model path produced a session, recorded where it counts.

D1 ships a curated example whose entire subject is the memo gate catching an
agent that was deliberately made to overclaim. An example like that is only
honest if its own artifacts say so, which is why the stub mode is treated as
**provenance rather than a hidden switch**: it binds into the hash-chained
``SESSION_OPEN`` entry and is stamped in the session manifest.

The load-bearing test here is
``test_the_declared_mode_cannot_disagree_with_the_adapter_that_ran``. The
obvious implementation passes the mode alongside the adapter, and that
implementation is wrong in a way nothing downstream could catch: a caller
could hand the session an overclaiming adapter while declaring a faithful run,
and the ledger would then carry the declaration rather than the truth. Reading
the provenance off the adapter that actually served the calls makes the
disagreement unexpressible instead of merely detectable, which is
ARCHITECTURE 1.1's preventive-over-detective posture applied to a field whose
whole job is telling two kinds of run apart.

Same reasoning as ``ReviewOutcome.reviewer_kind`` (DECISIONS 4.7): a scripted
stand-in must never be renderable as a human decision, so the discriminator
lives inside the payload the hash chain covers rather than beside it.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from ts_sentry.data.tz import IST
from ts_sentry.governance.ledger import EventType, Ledger, digest_payload
from ts_sentry.governance.mandate import AgentId, Consequence, Mandate, ToolId
from ts_sentry.governance.scopes import DataScope
from ts_sentry.orchestrator.adapter import (
    LiveAdapter,
    ModelMode,
    ModelProvenance,
    StubAdapter,
    StubMode,
)
from ts_sentry.orchestrator.core import CloseReason, FixedClock, Session
from ts_sentry.orchestrator.manifest import ArtifactRecord, SessionManifest
from ts_sentry.provenance import git_sha

_START = datetime(2026, 8, 1, 14, 30, tzinfo=IST)
_DATASET_DIGEST = "a" * 64


class _RankedQueue:
    """Stand-in output schema, as the other session tests use."""


def _mandate() -> Mandate:
    return Mandate(
        agent_id=AgentId.TRIAGE,
        version="1.0.0",
        consequence_ceiling=Consequence.OBSERVE,
        allowed_tools=frozenset({ToolId.RANK_TRIAGE_QUEUE}),
        data_scopes=frozenset({DataScope.COMMENT, DataScope.CHANNEL}),
        output_schema=_RankedQueue,
        token_budget=1_000,
        max_steps=4,
    )


def _session(provenance: ModelProvenance | None) -> Session:
    mandate = _mandate()
    return Session(
        session_id="session-001",
        analyst_id="saif",
        ledger=Ledger(duckdb.connect(":memory:")),
        clock=FixedClock(_START, step=timedelta(seconds=1)),
        mandates={mandate.agent_id: mandate},
        dataset_digest=_DATASET_DIGEST,
        model_provenance=provenance,
    )


# --------------------------------------------------------------------------
# The record's own invariant
# --------------------------------------------------------------------------


def test_a_stub_session_must_name_which_stub_mode_produced_it() -> None:
    """No default, following DECISIONS 4.7 and 5.7.

    The one field whose job is stopping a run being mistaken for a different
    kind of run must be named at every call site. A default is the value
    nobody chose, and this is precisely the value somebody has to choose.
    """
    with pytest.raises(ValueError, match="must name which stub mode"):
        ModelProvenance(model_mode=ModelMode.STUB, stub_mode=None)


def test_a_live_session_cannot_claim_a_stub_mode() -> None:
    """The other direction, and it is not symmetry for its own sake.

    A live run carrying ``stub_mode: faithful`` would assert that a
    deterministic stub was involved when none was, which is a false statement
    about provenance in the artifact whose purpose is provenance.
    """
    with pytest.raises(ValueError, match="no stub mode"):
        ModelProvenance(model_mode=ModelMode.LIVE, stub_mode=StubMode.FAITHFUL)


def test_the_live_rendering_omits_the_field_rather_than_writing_null() -> None:
    """Matching how ``SESSION_OPEN`` already omits the corpus fields.

    A field that is present asserts something. Under live there is nothing to
    assert, and ``"stub_mode": null`` invites a reader to treat absence as a
    value.
    """
    assert ModelProvenance(model_mode=ModelMode.LIVE, stub_mode=None).to_json_object() == {
        "model_mode": "live"
    }


# --------------------------------------------------------------------------
# The load-bearing one
# --------------------------------------------------------------------------


def test_the_declared_mode_cannot_disagree_with_the_adapter_that_ran() -> None:
    """Provenance is read off the adapter, so there is nothing to declare.

    This is the test that pins the design rather than the behaviour. If
    ``provenance`` ever becomes a parameter a caller supplies alongside the
    adapter, a session can be told it ran faithfully while an overclaiming
    adapter served every call, and every artifact downstream would repeat the
    lie with a valid hash chain over it.
    """
    for mode in StubMode:
        adapter = StubAdapter(mode=mode)
        assert adapter.provenance.stub_mode is mode
        assert adapter.provenance.model_mode is ModelMode.STUB
        # And it agrees with the id the adapter reports for itself, which is
        # the other place a reader looks.
        assert adapter.adapter_id == f"stub/{mode.value}"


def test_the_live_adapter_reports_live_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LiveAdapter`` refuses to construct without the environment, so the
    environment is supplied here and nowhere else.

    ``tests/conftest.py`` strips these variables session-wide on purpose, so
    setting them is a deliberate local act. No network call follows:
    construction never touches the vendor client, which ``complete`` imports
    inside itself, and the credential's value is never read by this repository.
    """
    monkeypatch.setenv("TS_SENTRY_LLM_MODE", "live")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-read-by-this-repository")
    adapter = LiveAdapter()
    assert adapter.provenance == ModelProvenance(model_mode=ModelMode.LIVE, stub_mode=None)


# --------------------------------------------------------------------------
# Where it lands
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [StubMode.FAITHFUL, StubMode.OVERCLAIM])
def test_session_open_carries_the_mode_inside_the_hash_chain(mode: StubMode) -> None:
    """Inside ``SESSION_OPEN``, not beside it.

    A payload field is covered by ``payload_digest`` and therefore by
    ``entry_hash``, so editing it after the fact breaks the chain. That is the
    difference between a claim this system protects and a claim it merely
    prints.
    """
    session = _session(StubAdapter(mode=mode).provenance)
    recorded = session.open()

    assert recorded.entry.event_type is EventType.SESSION_OPEN
    assert recorded.payload["model_mode"] == "stub"
    assert recorded.payload["stub_mode"] == mode.value

    # The field is inside what the hashes cover, not beside it. Recomputing the
    # digest over the body and matching it against what the chain stored is the
    # difference between a claim this system protects and one it merely prints:
    # drop `stub_mode` from the payload and this digest no longer agrees.
    assert digest_payload(recorded.payload) == recorded.entry.payload_digest
    assert digest_payload({k: v for k, v in recorded.payload.items() if k != "stub_mode"}) != (
        recorded.entry.payload_digest
    )


def test_a_session_told_nothing_asserts_nothing() -> None:
    """Omitted entirely rather than defaulted to faithful.

    Silence meaning ``faithful`` would make an unrecorded session
    indistinguishable from a positively faithful one, and the whole point of
    the field is that a faithful run says so out loud.
    """
    payload = _session(None).open().payload
    assert "model_mode" not in payload
    assert "stub_mode" not in payload


def test_the_manifest_and_the_ledger_render_from_one_source(tmp_path: Path) -> None:
    """The manifest stamp and the ledgered entry cannot describe one session
    two ways, because both come from ``ModelProvenance.to_json_object``.

    Checked on the written JSON rather than on the objects, because the file
    is what a reviewer opens.
    """
    session = _session(StubAdapter(mode=StubMode.OVERCLAIM).provenance)
    opened = session.open()
    closed = session.close(CloseReason.COMPLETED)

    assert session.opened_ts is not None and session.closed_ts is not None
    artifact = tmp_path / "ledger.jsonl"
    session.ledger.export_jsonl(artifact)
    manifest_path = tmp_path / "session_manifest.json"
    SessionManifest(
        session_id=session.session_id,
        analyst_id=session.analyst_id,
        opened_ts_iso=session.opened_ts.isoformat(),
        closed_ts_iso=session.closed_ts.isoformat(),
        close_reason=CloseReason.COMPLETED,
        dataset_digest=_DATASET_DIGEST,
        mandate_set_hash=session.mandate_set_hash,
        mandate_hashes={AgentId.TRIAGE.value: session.binding(AgentId.TRIAGE).hash},
        expected_head=closed.head,
        event_counts=session.event_counts(),
        budgets={AgentId.TRIAGE.value: session.budget(AgentId.TRIAGE).snapshot()},
        git_sha=git_sha(),
        model_provenance=session.model_provenance,
        artifacts=[ArtifactRecord.of("ledger_jsonl", artifact, relative_to=tmp_path)],
    ).write(manifest_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["model_mode"] == "stub"
    assert manifest["stub_mode"] == "overclaim"
    assert manifest["stub_mode"] == opened.payload["stub_mode"]
