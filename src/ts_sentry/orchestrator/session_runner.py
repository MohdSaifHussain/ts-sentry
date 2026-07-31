# SPDX-License-Identifier: MIT
"""D6: one whole session, from open to anchored manifest.

The CLI's verb, kept out of ``cli.main`` so the phase's exit criterion is
testable without going through argument parsing. ``cli.main`` owns exit codes
and argument shapes; this owns what a session *is*.

Order matters at the end and is the point of the module
-------------------------------------------------------
``SESSION_CLOSE`` is appended, *then* the chain head is read, *then* the
manifest records it. An anchor written before the close would describe a
session that had not finished, which is precisely the shorter-chain-that-still
-verifies that the anchor exists to detect.

The session self-verifies before returning. A run that produced a broken chain
has produced nothing worth delivering, and finding that out at the end of the
run is better than finding it out when someone tries to verify the export
weeks later.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np

from ts_sentry.agents.triage.scorer import WEIGHTS_VERSION, weights_hash
from ts_sentry.governance.canonical import digest_fields
from ts_sentry.governance.ledger import ChainVerification, Ledger
from ts_sentry.governance.mandate import AgentId
from ts_sentry.orchestrator.adapter import (
    ModelAdapter,
    RealSleeper,
    RetryPolicy,
    Sleeper,
)
from ts_sentry.orchestrator.core import Clock, CloseReason, Session, SystemClock
from ts_sentry.orchestrator.detection_stub import DETECTOR_VERSION
from ts_sentry.orchestrator.fleet import PHASE_THREE_CHECKS, default_mandates
from ts_sentry.orchestrator.manifest import ArtifactRecord, SessionManifest
from ts_sentry.orchestrator.toolspec import ToolResources
from ts_sentry.orchestrator.triage_turn import TriageTurn, run_triage_turn
from ts_sentry.provenance import git_sha, sha256_file

__all__ = ["SessionArtifacts", "SessionRun", "derive_session_id", "run_triage_session"]

LEDGER_JSONL = "ledger.jsonl"
LEDGER_STORE = "ledger.duckdb"
RANKED_QUEUE = "ranked_queue.json"
SESSION_EVENTS = "session_events.json"
SESSION_MANIFEST = "session_manifest.json"


@dataclass(frozen=True, slots=True)
class SessionArtifacts:
    """Where a finished session left its files."""

    out_dir: Path
    ledger_jsonl: Path
    ledger_store: Path
    ranked_queue: Path
    session_events: Path
    manifest: Path


@dataclass(frozen=True, slots=True)
class SessionRun:
    """What a session produced, including whether its own chain held."""

    artifacts: SessionArtifacts
    turn: TriageTurn
    close_reason: CloseReason
    verification: ChainVerification
    manifest: SessionManifest

    @property
    def intact(self) -> bool:
        return self.verification.intact


def derive_session_id(analyst_id: str, dataset_digest: str) -> str:
    """A session id that is a function of its inputs.

    Not random, and not the clock. STEP-01's no-time-based-entropy rule is
    about reproducibility, and an id derived from who ran the session against
    what makes two runs of the same inputs comparable instead of merely
    similar.
    """
    return "session-" + digest_fields("ts-sentry/session-id/v1", analyst_id, dataset_digest)[:12]


def _dataset_path(seed_dataset: Path) -> Path:
    """Accept either the build directory or the store file inside it."""
    if seed_dataset.is_dir():
        return seed_dataset / "build.duckdb"
    return seed_dataset


def run_triage_session(
    seed_dataset: Path,
    out_dir: Path,
    adapter: ModelAdapter,
    *,
    analyst_id: str,
    session_id: str | None = None,
    limit: int = 25,
    seed: int = 42,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
) -> SessionRun:
    """Open a session, run one triage turn, close, and write the artifacts.

    The dataset is opened **read-only**. A triage session is an OBSERVE
    session by mandate, and opening its evidence read-write would leave the
    ceiling depending on nothing but every future author's care.
    """
    store_path = _dataset_path(seed_dataset)
    if not store_path.is_file():
        raise FileNotFoundError(f"no dataset store at {store_path}")

    dataset_digest = sha256_file(store_path)
    resolved_session_id = session_id or derive_session_id(analyst_id, dataset_digest)

    out_dir.mkdir(parents=True, exist_ok=True)
    ledger_store = out_dir / LEDGER_STORE
    ledger_store.unlink(missing_ok=True)

    dataset = duckdb.connect(str(store_path), read_only=True)
    ledger_connection = duckdb.connect(str(ledger_store))
    try:
        session = Session(
            session_id=resolved_session_id,
            analyst_id=analyst_id,
            ledger=Ledger(ledger_connection),
            clock=clock or SystemClock(),
            mandates=default_mandates(),
            dataset_digest=dataset_digest,
        )
        session.open()

        turn = run_triage_turn(
            session,
            adapter,
            resources=ToolResources(connection=dataset, seed=seed),
            checks=PHASE_THREE_CHECKS,
            policy=RetryPolicy(),
            rng=np.random.default_rng(seed),
            sleeper=sleeper or RealSleeper(),
            limit=limit,
        )

        close_reason = turn.close_reason or CloseReason.COMPLETED
        closed = session.close(close_reason)

        # Artifacts first, then the manifest: the manifest records a digest per
        # artifact, so every file it describes has to exist before it is
        # written.
        session.ledger.export_jsonl(out_dir / LEDGER_JSONL)
        _write_json(
            out_dir / RANKED_QUEUE,
            {
                "session_id": resolved_session_id,
                "weights_version": WEIGHTS_VERSION,
                "weights_hash": weights_hash(),
                "detector_version": DETECTOR_VERSION,
                "close_reason": close_reason.value,
                "detail": turn.detail,
                "injection_signals": turn.injection_signals,
                "queue": None if turn.queue is None else turn.queue.to_json_object(),
                "rationale_verification": (
                    None if turn.rationales is None else turn.rationales.to_json_object()
                ),
            },
        )
        _write_json(
            out_dir / SESSION_EVENTS,
            {
                "session_id": resolved_session_id,
                "events": [
                    {
                        "seq": recorded.entry.seq,
                        "event_type": recorded.entry.event_type.value,
                        "timestamp_ist": recorded.entry.timestamp_iso,
                        "payload": dict(recorded.payload),
                    }
                    for recorded in session.recorded_events
                ],
            },
        )

        verification = session.ledger.verify()
        artifacts = SessionArtifacts(
            out_dir=out_dir,
            ledger_jsonl=out_dir / LEDGER_JSONL,
            ledger_store=ledger_store,
            ranked_queue=out_dir / RANKED_QUEUE,
            session_events=out_dir / SESSION_EVENTS,
            manifest=out_dir / SESSION_MANIFEST,
        )

        assert session.opened_ts is not None and session.closed_ts is not None
        manifest = SessionManifest(
            session_id=resolved_session_id,
            analyst_id=analyst_id,
            opened_ts_iso=session.opened_ts.isoformat(),
            closed_ts_iso=session.closed_ts.isoformat(),
            close_reason=close_reason,
            dataset_digest=dataset_digest,
            mandate_set_hash=session.mandate_set_hash,
            mandate_hashes={
                AgentId.TRIAGE.value: session.binding(AgentId.TRIAGE).hash,
            },
            expected_head=closed.head,
            event_counts=session.event_counts(),
            budgets={
                AgentId.TRIAGE.value: session.budget(AgentId.TRIAGE).snapshot(),
            },
            git_sha=git_sha(),
            artifacts=[
                ArtifactRecord.of("ledger_jsonl", artifacts.ledger_jsonl, relative_to=out_dir),
                ArtifactRecord.of("ranked_queue", artifacts.ranked_queue, relative_to=out_dir),
                ArtifactRecord.of("session_events", artifacts.session_events, relative_to=out_dir),
            ],
        )
        manifest.write(artifacts.manifest)

        return SessionRun(
            artifacts=artifacts,
            turn=turn,
            close_reason=close_reason,
            verification=verification,
            manifest=manifest,
        )
    finally:
        dataset.close()
        ledger_connection.close()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=False, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
