# SPDX-License-Identifier: MIT
"""D5: one evaluation, run as a governed session end to end.

Assembles what the other session runners assemble, for the prompt-eval agent:
open a session, run the turn, close it, write the ledger export, the manifest
with its chain-head anchor, and the D6 report.

Why this session's ``dataset_digest`` comes from the eval manifest
--------------------------------------------------------------------
Every other session in this system opens against a build directory and derives
its dataset identity from that build's manifest. A prompt evaluation touches no
platform table at all: ``PROMPT_EVAL_MANDATE`` grants no data scopes, and the
items were rendered at build time into a committed artifact.

So the identity comes from the eval set's own manifest, which recorded the
``dataset_digest`` of the build the items were rendered from. That keeps the
session tied to a real dataset without opening one, and it is the honest tie:
what this evaluation depends on is the *items*, and the items depend on that
build.

Tolerances bind at open
-----------------------
``tolerances_sha256`` goes into ``SESSION_OPEN`` (DECISIONS 5.8's precedent for
the corpus), so the chain records the limits the verdict was reached under
before any answer is graded. A tolerance edited afterwards no longer matches the
chain.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np

from ts_sentry.data.eval_set import EvalSetError, items_digest, load_items, load_manifest
from ts_sentry.governance.gates import FailureCode, GateChecks, GateFailure
from ts_sentry.governance.ledger import Ledger
from ts_sentry.governance.mandate import AgentId
from ts_sentry.orchestrator.adapter import ModelAdapter, RealSleeper, RetryPolicy, Sleeper
from ts_sentry.orchestrator.core import CloseReason, FixedClock, Session, SystemClock
from ts_sentry.orchestrator.eval_labels import load_label_store
from ts_sentry.orchestrator.eval_report import write_eval_report
from ts_sentry.orchestrator.fleet import default_mandates
from ts_sentry.orchestrator.manifest import ArtifactRecord, SessionManifest
from ts_sentry.orchestrator.prompt_eval_turn import PromptEvalTurn, run_prompt_eval_turn
from ts_sentry.orchestrator.regression_gate import Tolerances
from ts_sentry.orchestrator.session_runner import derive_session_id
from ts_sentry.prompt_registry.registry import PromptRegistryError, PromptTask
from ts_sentry.prompt_registry.store import PromptRegistry, load_registry
from ts_sentry.provenance import git_sha

__all__ = ["EvalSessionError", "EvalSessionRun", "run_eval_session"]


class EvalSessionError(Exception):
    """Raised when an evaluation cannot be run or written honestly.

    Its own class rather than reusing ``cli.main.InputError``, because
    ``orchestrator`` must not import ``cli`` (STEP-03 recorded that direction
    when ``provenance`` was lifted out of the CLI for the same reason). The CLI
    catches this and maps it to ``EXIT_INPUT_ERROR``.
    """


LEDGER_JSONL = "ledger.jsonl"
SESSION_MANIFEST = "session_manifest.json"


@dataclass(frozen=True, slots=True)
class EvalSessionRun:
    """What one evaluation produced."""

    turn: PromptEvalTurn
    manifest: SessionManifest
    report_md: Path
    report_json: Path
    incumbent_digest: str
    candidate_digest: str

    @property
    def activatable(self) -> bool:
        return self.turn.activatable


def _checks() -> GateChecks:
    """Fail-closed stand-ins for the two levels this session never declares.

    ``GateChecks`` has no defaults (DECISIONS 2.5). A prompt-eval session
    proposes only an OBSERVE action, which needs no checker, so ASSEMBLE and
    RECOMMEND must be spelled as refusals rather than left permissive: an
    unconfigured gate that auto-approved would accept something nothing in this
    session was entitled to produce.
    """

    def unavailable(artifact: object, /) -> tuple[GateFailure, ...]:
        return (
            GateFailure(
                code=FailureCode.CHECKER_ERROR,
                detail=(
                    "a prompt-eval session declares no ASSEMBLE or RECOMMEND action; "
                    "nothing here is entitled to produce one"
                ),
            ),
        )

    return GateChecks(assemble=unavailable, recommend=unavailable)


def _resolve_versions(
    registry: PromptRegistry, task: PromptTask, candidate_digest: str
) -> tuple[str, str]:
    """The incumbent and the candidate, both checked against the registry.

    The candidate must be a *registered* version bound to this task. A candidate
    identified by a digest nobody registered would be an evaluation of a prompt
    with no lineage, no parent and no record, which is the state the registry
    exists to make impossible.
    """
    incumbent = registry.active(task)
    candidate = registry.by_digest(candidate_digest)

    if candidate.task is not task:
        raise PromptRegistryError(
            f"candidate {candidate_digest[:12]} is bound to {candidate.task.value}, not "
            f"{task.value}. Task binding is what stops one agent's prompt being activated "
            "for another"
        )
    if candidate.content_digest == incumbent.content_digest:
        raise PromptRegistryError(
            f"candidate {candidate_digest[:12]} is already the incumbent for {task.value}. "
            "Evaluating a version against itself measures the harness, not the prompt"
        )
    return incumbent.content_digest, candidate.content_digest


def run_eval_session(
    registry_dir: Path,
    evals_dir: Path,
    out_dir: Path,
    adapter: ModelAdapter,
    *,
    candidate_digest: str,
    tolerances: Tolerances,
    analyst_id: str,
    task: PromptTask = PromptTask.CLASSIFY_THREAT_CLASS,
    session_id: str | None = None,
    seed: int = 42,
    sleeper: Sleeper | None = None,
    clock_start: datetime | None = None,
) -> EvalSessionRun:
    """Evaluate one candidate against the incumbent, under a governed session."""
    registry = load_registry(registry_dir)
    incumbent_digest, resolved_candidate = _resolve_versions(registry, task, candidate_digest)

    items = load_items(evals_dir)
    store = load_label_store(evals_dir)
    manifest_obj = load_manifest(evals_dir)

    dataset_digest = str(manifest_obj.get("dataset_digest", ""))
    if len(dataset_digest) != 64:
        raise EvalSetError(
            f"the eval-set manifest at {evals_dir} carries no usable dataset_digest, so this "
            "evaluation cannot be tied to the build its items came from"
        )
    dataset_seed = int(str(manifest_obj.get("dataset_seed", 0)))
    dataset_scale = int(str(manifest_obj.get("dataset_scale", 0)))

    resolved_session_id = session_id or derive_session_id(
        analyst_id, dataset_digest, "prompt_eval", task.value, resolved_candidate
    )

    if out_dir.exists() and any(out_dir.iterdir()):
        raise EvalSessionError(
            f"{out_dir} already exists and is not empty. A session writes its own directory, "
            "and overwriting one would destroy the audit trail it holds"
        )

    session = Session(
        session_id=resolved_session_id,
        analyst_id=analyst_id,
        ledger=Ledger(duckdb.connect(":memory:")),
        clock=SystemClock() if clock_start is None else FixedClock(clock_start),
        mandates=default_mandates(),
        dataset_digest=dataset_digest,
        tolerances_sha256=tolerances.digest,
    )
    session.open()

    turn = run_prompt_eval_turn(
        session,
        adapter,
        items=items,
        store=store,
        incumbent=registry.system_prompt(incumbent_digest),
        candidate=registry.system_prompt(resolved_candidate),
        incumbent_digest=incumbent_digest,
        candidate_digest=resolved_candidate,
        task=task.value,
        items_sha256=items_digest(items),
        tolerances=tolerances,
        checks=_checks(),
        policy=RetryPolicy(),
        rng=np.random.default_rng(seed),
        sleeper=sleeper or RealSleeper(),
        bootstrap_seed=seed,
    )

    # The turn's own close reason when it had one, so a session that ran out of
    # steps and one that ran out of tokens stay distinguishable in the manifest.
    # CloseReason splits those deliberately (a metric that cannot tell them
    # apart cannot inform a budget change), and collapsing them here would undo
    # that at the only place the distinction gets written down.
    close_reason = turn.close_reason or CloseReason.COMPLETED
    closed = session.close(close_reason)

    out_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = out_dir / LEDGER_JSONL
    session.ledger.export_jsonl(ledger_path)

    if turn.report is None or turn.verdict is None:
        raise EvalSessionError(
            f"the evaluation produced no report: {turn.detail}. Nothing was written beyond "
            "the ledger, because a report artifact for an evaluation that did not run would "
            "be an artifact asserting something false"
        )

    report_md, report_json = write_eval_report(
        out_dir,
        turn.report,
        turn.verdict,
        dataset_seed=dataset_seed,
        dataset_scale=dataset_scale,
        tolerances=tolerances,
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
        mandate_hashes={AgentId.PROMPT_EVAL.value: session.binding(AgentId.PROMPT_EVAL).hash},
        expected_head=closed.head,
        event_counts=session.event_counts(),
        budgets={AgentId.PROMPT_EVAL.value: session.budget(AgentId.PROMPT_EVAL).snapshot()},
        git_sha=git_sha(),
        artifacts=[
            ArtifactRecord.of("ledger_jsonl", ledger_path, relative_to=out_dir),
            ArtifactRecord.of("eval_report_md", report_md, relative_to=out_dir),
            ArtifactRecord.of("eval_report_json", report_json, relative_to=out_dir),
        ],
    )
    manifest.write(out_dir / SESSION_MANIFEST)

    return EvalSessionRun(
        turn=turn,
        manifest=manifest,
        report_md=report_md,
        report_json=report_json,
        incumbent_digest=incumbent_digest,
        candidate_digest=resolved_candidate,
    )
