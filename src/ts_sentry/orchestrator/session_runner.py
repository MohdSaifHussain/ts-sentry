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
from ts_sentry.data.enums import EntityKind
from ts_sentry.data.policy_corpus import load_corpus
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
from ts_sentry.orchestrator.evidence_turn import EvidenceTurn, run_evidence_turn
from ts_sentry.orchestrator.fleet import (
    PHASE_FOUR_CHECKS,
    default_mandates,
    phase_five_checks,
)
from ts_sentry.orchestrator.manifest import ArtifactRecord, SessionManifest
from ts_sentry.orchestrator.memo_export import write_memo_html, write_memo_markdown
from ts_sentry.orchestrator.memo_turn import MemoTurn, run_memo_turn
from ts_sentry.orchestrator.pack_export import read_pack_json, write_pack_graphml, write_pack_json
from ts_sentry.orchestrator.review import AnalystReviewer
from ts_sentry.orchestrator.subject_check import require_subject
from ts_sentry.orchestrator.toolspec import ToolResources
from ts_sentry.orchestrator.triage_turn import TriageTurn, run_triage_turn
from ts_sentry.provenance import dataset_digest_from_manifest, git_sha

__all__ = [
    "EvidenceArtifacts",
    "EvidenceRun",
    "MemoArtifacts",
    "MemoRun",
    "SessionArtifacts",
    "SessionRun",
    "derive_session_id",
    "run_evidence_session",
    "run_memo_session",
    "run_triage_session",
]

LEDGER_JSONL = "ledger.jsonl"
LEDGER_STORE = "ledger.duckdb"
RANKED_QUEUE = "ranked_queue.json"
SESSION_EVENTS = "session_events.json"
SESSION_MANIFEST = "session_manifest.json"
EVIDENCE_PACK = "evidence_pack.json"
EVIDENCE_GRAPH = "evidence_graph.graphml"
MEMO_JSON = "memo.json"
MEMO_MARKDOWN = "memo.md"
MEMO_HTML = "memo.html"


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


def derive_session_id(analyst_id: str, dataset_digest: str, *discriminators: str) -> str:
    """A session id that is a function of its inputs.

    Not random, and not the clock. STEP-01's no-time-based-entropy rule is about
    reproducibility, and an id derived from who ran the session against what
    data makes two runs of the same inputs comparable instead of merely similar.

    Stated at its now-true width. Until STEP-04 this claim was wider than the
    behaviour: ``dataset_digest`` was the SHA-256 of ``build.duckdb``, whose
    internal layout is not byte-stable even when its contents are, so the id
    held for two runs against one build directory and *not* across rebuilds.
    The digest now derives from the build manifest's Parquet table hashes, which
    STEP-01 verified byte-stable, so two rebuilds of the same seed and scale
    produce the same id and the claim holds as written.

    ``discriminators`` name what else makes this session *this* session. They
    exist because the first evidence session run through the CLI came back
    carrying the same id as the triage session before it: with one session type
    in the world, analyst plus dataset identified a session, and with two it
    stopped doing so. Two different sessions sharing an id is not a cosmetic
    problem. Session ids appear in the ``OrchestratorToken``, in every manifest
    and in every artifact directory, and an id that does not distinguish
    sessions makes an audit trail ambiguous exactly where it is supposed to be
    decisive.

    It does not survive a change of *content*. A different seed, scale or
    generator version yields different table hashes and therefore a different
    id, which is the property that makes the id worth having.
    """
    return (
        "session-"
        + digest_fields("ts-sentry/session-id/v1", analyst_id, dataset_digest, *discriminators)[:12]
    )


def _dataset_path(seed_dataset: Path) -> Path:
    """Accept either the build directory or the store file inside it."""
    if seed_dataset.is_dir():
        return seed_dataset / "build.duckdb"
    return seed_dataset


def _build_dir(seed_dataset: Path) -> Path:
    """The directory holding the build, given either it or the store inside it.

    The manifest lives beside the store, so accepting both spellings of
    ``--seed-dataset`` costs one line and keeps the STEP-03 CLI contract intact.
    """
    return seed_dataset if seed_dataset.is_dir() else seed_dataset.parent


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

    dataset_digest = dataset_digest_from_manifest(_build_dir(seed_dataset))
    resolved_session_id = session_id or derive_session_id(
        analyst_id, dataset_digest, AgentId.TRIAGE.value
    )

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
            checks=PHASE_FOUR_CHECKS,
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


# --------------------------------------------------------------------------
# STEP-04: the evidence session
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceArtifacts:
    """Where a finished evidence session left its files."""

    out_dir: Path
    ledger_jsonl: Path
    ledger_store: Path
    evidence_pack: Path
    evidence_graph: Path
    session_events: Path
    manifest: Path


@dataclass(frozen=True, slots=True)
class EvidenceRun:
    """What an evidence session produced, including whether its chain held."""

    artifacts: EvidenceArtifacts
    turn: EvidenceTurn
    close_reason: CloseReason
    verification: ChainVerification
    manifest: SessionManifest

    @property
    def intact(self) -> bool:
        return self.verification.intact


def run_evidence_session(
    seed_dataset: Path,
    out_dir: Path,
    adapter: ModelAdapter,
    *,
    case_id: str,
    subject_id: str,
    reviewer: AnalystReviewer,
    analyst_id: str,
    session_id: str | None = None,
    max_hops: int | None = None,
    seed: int = 42,
    subject_kind: EntityKind = EntityKind.CHANNEL,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
) -> EvidenceRun:
    """Open a session, investigate one case, close, and write the artifacts.

    The dataset is opened **read-only**, as the triage session opens it. An
    ASSEMBLE ceiling is authority to *assemble evidence from* the platform, not
    authority to change it, and a connection that could write would leave that
    distinction resting on every future author's care rather than on the
    connection.

    The subject is checked for existence **before anything is created**, so a
    subject that is not in the dataset leaves no session, no chain, and no
    output directory. The ordering is the deliverable rather than a detail: a
    refusal after the session opened would already have written a chain, a
    manifest and an anchor describing a session that should not exist. See
    :mod:`ts_sentry.orchestrator.subject_check` for the finding this closes.
    """
    store_path = _dataset_path(seed_dataset)
    if not store_path.is_file():
        raise FileNotFoundError(f"no dataset store at {store_path}")

    dataset_digest = dataset_digest_from_manifest(_build_dir(seed_dataset))
    resolved_session_id = session_id or derive_session_id(
        analyst_id, dataset_digest, AgentId.EVIDENCE.value, case_id, subject_id
    )

    dataset = duckdb.connect(str(store_path), read_only=True)
    # Left None until the subject check passes, so the guard's refusal cannot
    # leave a half-created session behind and the `finally` still closes what
    # was actually opened.
    ledger_connection: duckdb.DuckDBPyConnection | None = None
    try:
        # Before out_dir.mkdir and before any ledger exists.
        require_subject(dataset, subject_id, subject_kind)

        out_dir.mkdir(parents=True, exist_ok=True)
        ledger_store = out_dir / LEDGER_STORE
        ledger_store.unlink(missing_ok=True)
        ledger_connection = duckdb.connect(str(ledger_store))
        session = Session(
            session_id=resolved_session_id,
            analyst_id=analyst_id,
            ledger=Ledger(ledger_connection),
            clock=clock or SystemClock(),
            mandates=default_mandates(),
            dataset_digest=dataset_digest,
        )
        session.open()

        turn = run_evidence_turn(
            session,
            adapter,
            case_id=case_id,
            subject_id=subject_id,
            reviewer=reviewer,
            resources=ToolResources(connection=dataset, seed=seed),
            checks=PHASE_FOUR_CHECKS,
            policy=RetryPolicy(),
            rng=np.random.default_rng(seed),
            sleeper=sleeper or RealSleeper(),
            subject_kind=subject_kind,
            max_hops=max_hops,
        )

        close_reason = turn.close_reason or CloseReason.COMPLETED
        closed = session.close(close_reason)

        session.ledger.export_jsonl(out_dir / LEDGER_JSONL)
        write_pack_json(turn.pack, out_dir / EVIDENCE_PACK)
        write_pack_graphml(turn.pack, out_dir / EVIDENCE_GRAPH)
        _write_json(
            out_dir / SESSION_EVENTS,
            {
                "session_id": resolved_session_id,
                "case_id": case_id,
                "reviewer_kind": reviewer.reviewer_kind.value,
                "turn": turn.to_json_object(),
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
        artifacts = EvidenceArtifacts(
            out_dir=out_dir,
            ledger_jsonl=out_dir / LEDGER_JSONL,
            ledger_store=ledger_store,
            evidence_pack=out_dir / EVIDENCE_PACK,
            evidence_graph=out_dir / EVIDENCE_GRAPH,
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
                AgentId.EVIDENCE.value: session.binding(AgentId.EVIDENCE).hash,
            },
            expected_head=closed.head,
            event_counts=session.event_counts(),
            budgets={
                AgentId.EVIDENCE.value: session.budget(AgentId.EVIDENCE).snapshot(),
            },
            git_sha=git_sha(),
            artifacts=[
                ArtifactRecord.of("ledger_jsonl", artifacts.ledger_jsonl, relative_to=out_dir),
                ArtifactRecord.of("evidence_pack", artifacts.evidence_pack, relative_to=out_dir),
                ArtifactRecord.of("evidence_graph", artifacts.evidence_graph, relative_to=out_dir),
                ArtifactRecord.of("session_events", artifacts.session_events, relative_to=out_dir),
            ],
        )
        manifest.write(artifacts.manifest)

        return EvidenceRun(
            artifacts=artifacts,
            turn=turn,
            close_reason=close_reason,
            verification=verification,
            manifest=manifest,
        )
    finally:
        dataset.close()
        if ledger_connection is not None:
            ledger_connection.close()


# --------------------------------------------------------------------------
# STEP-05: the memo session
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MemoArtifacts:
    """Where a finished memo session left its files."""

    out_dir: Path
    ledger_jsonl: Path
    ledger_store: Path
    memo_json: Path
    memo_markdown: Path
    memo_html: Path
    session_events: Path
    manifest: Path


@dataclass(frozen=True, slots=True)
class MemoRun:
    """What a memo session produced, including whether its chain held."""

    artifacts: MemoArtifacts
    turn: MemoTurn
    close_reason: CloseReason
    verification: ChainVerification
    manifest: SessionManifest

    @property
    def intact(self) -> bool:
        return self.verification.intact


def run_memo_session(
    seed_dataset: Path,
    pack_path: Path,
    out_dir: Path,
    adapter: ModelAdapter,
    *,
    analyst_id: str,
    policies_dir: Path,
    session_id: str | None = None,
    memo_id: str = "memo-0001",
    max_attempts: int | None = None,
    seed: int = 42,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
) -> MemoRun:
    """Open a session, draft one memo from an accepted pack, close, write.

    The dataset is opened only to derive the dataset digest, and **never
    queried**: ``MEMO_MANDATE`` grants no data scopes at all, so there is
    nothing the memo agent could ask of it. The connection is not lent to the
    turn, which is the structural version of that statement rather than a
    promise about what the turn happens to do.

    The pack and the corpus are both loaded here and handed to the turn, for the
    reason ``ToolResources`` exists: an agent that could name either could
    supply one it had written.
    """
    store_path = _dataset_path(seed_dataset)
    if not store_path.is_file():
        raise FileNotFoundError(f"no dataset store at {store_path}")

    pack = read_pack_json(pack_path)
    corpus = load_corpus(policies_dir)
    dataset_digest = dataset_digest_from_manifest(_build_dir(seed_dataset))
    resolved_session_id = session_id or derive_session_id(
        analyst_id, dataset_digest, AgentId.MEMO.value, pack.case_id, pack.subject_id
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    ledger_store = out_dir / LEDGER_STORE
    ledger_store.unlink(missing_ok=True)
    ledger_connection = duckdb.connect(str(ledger_store))
    try:
        session = Session(
            session_id=resolved_session_id,
            analyst_id=analyst_id,
            ledger=Ledger(ledger_connection),
            clock=clock or SystemClock(),
            mandates=default_mandates(),
            dataset_digest=dataset_digest,
            corpus=corpus,
        )
        session.open()

        turn = run_memo_turn(
            session,
            adapter,
            pack=pack,
            corpus=corpus,
            checks=phase_five_checks(pack, corpus),
            policy=RetryPolicy(),
            rng=np.random.default_rng(seed),
            sleeper=sleeper or RealSleeper(),
            memo_id=memo_id,
            max_attempts=max_attempts,
        )

        close_reason = turn.close_reason or CloseReason.COMPLETED
        closed = session.close(close_reason)

        session.ledger.export_jsonl(out_dir / LEDGER_JSONL)
        _write_json(
            out_dir / MEMO_JSON,
            {
                "session_id": resolved_session_id,
                "corpus_version": corpus.corpus_version,
                "corpus_sha256": corpus.corpus_sha256,
                "pack_digest": pack.content_digest,
                "turn": turn.to_json_object(),
            },
        )
        # Exports are written whether or not the memo verified, and always
        # unsigned. A memo that failed verification is still the artifact an
        # analyst reads to see what was flagged, and an export nobody can look
        # at because it did not pass is an export that hides its own failure.
        # No signature exists at drafting time, so every one of these carries
        # the AI-DRAFT watermark by construction rather than by choice.
        if turn.memo is not None:
            write_memo_markdown(turn.memo, out_dir / MEMO_MARKDOWN)
            write_memo_html(turn.memo, out_dir / MEMO_HTML)
        _write_json(
            out_dir / SESSION_EVENTS,
            {
                "session_id": resolved_session_id,
                "case_id": pack.case_id,
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
        artifacts = MemoArtifacts(
            out_dir=out_dir,
            ledger_jsonl=out_dir / LEDGER_JSONL,
            ledger_store=ledger_store,
            memo_json=out_dir / MEMO_JSON,
            memo_markdown=out_dir / MEMO_MARKDOWN,
            memo_html=out_dir / MEMO_HTML,
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
            mandate_hashes={AgentId.MEMO.value: session.binding(AgentId.MEMO).hash},
            expected_head=closed.head,
            event_counts=session.event_counts(),
            budgets={AgentId.MEMO.value: session.budget(AgentId.MEMO).snapshot()},
            git_sha=git_sha(),
            artifacts=[
                ArtifactRecord.of("ledger_jsonl", artifacts.ledger_jsonl, relative_to=out_dir),
                ArtifactRecord.of("memo", artifacts.memo_json, relative_to=out_dir),
                ArtifactRecord.of("session_events", artifacts.session_events, relative_to=out_dir),
            ],
        )
        manifest.write(artifacts.manifest)

        return MemoRun(
            artifacts=artifacts,
            turn=turn,
            close_reason=close_reason,
            verification=verification,
            manifest=manifest,
        )
    finally:
        ledger_connection.close()
