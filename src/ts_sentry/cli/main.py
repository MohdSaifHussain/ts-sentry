# SPDX-License-Identifier: MIT
"""The `ts-sentry` CLI.

Two subcommands so far, both documented in README.md:

* ``build-dataset`` (STEP-01 D7) orchestrates the in-memory build, DuckDB
  persistence and Parquet export, a build-time leakage self-check, the
  AnalystKit quality gate, and the build manifest.
* ``verify-ledger`` (STEP-02 D6) recomputes a trajectory-ledger hash chain
  and reports the first broken link.
* ``run-session`` (STEP-03 D6) opens an analyst session, runs one agent turn,
  closes with an anchored manifest, and writes the session artifacts.

Exit codes are allocated across the whole CLI rather than per subcommand, so
no number means two different things: 0 pass, 2 quality-gate fail, 3 leakage
fail, 4 broken chain, 5 input error, 6 chain-head mismatch.

``run-session`` is offline by default and costs nothing. ``--llm-mode stub``
is the default and the CI path; ``live`` additionally requires the
``TS_SENTRY_LLM_MODE`` environment variable and a credential the adapter never
reads, only checks for. A run with no environment configured at all is a
complete, valid session.
"""

import argparse
import json
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import duckdb

from ts_sentry.data.generator import build_dataset
from ts_sentry.data.population import BuildConfig
from ts_sentry.data.quality import QualityGateResult, QualityThresholds, run_quality_gate
from ts_sentry.data.store import export_dataset, persist_dataset
from ts_sentry.governance.canonical import require_sha256_hex
from ts_sentry.governance.ledger import (
    ChainHead,
    LedgerEntry,
    chain_head,
    read_jsonl,
    read_store,
    verify_chain,
)
from ts_sentry.governance.scopes import (
    DataScope,
    ScopeViolation,
    resolve_export_path,
    resolve_scope_by_name,
)
from ts_sentry.orchestrator.adapter import ModelAdapter, ModelMode, Responder, StubAdapter
from ts_sentry.orchestrator.evidence_turn import stub_evidence_responder
from ts_sentry.orchestrator.manifest import ManifestError, read_expected_head
from ts_sentry.orchestrator.review import (
    AnalystReviewer,
    InteractiveReviewer,
    ScriptedReviewer,
)
from ts_sentry.orchestrator.session_runner import run_evidence_session, run_triage_session
from ts_sentry.orchestrator.subject_check import SubjectNotFound
from ts_sentry.orchestrator.triage_turn import stub_triage_responder
from ts_sentry.provenance import DatasetDigestError, git_sha, sha256_file

GENERATOR_VERSION = "0.1.0"

EXIT_OK = 0
EXIT_QUALITY_GATE_FAIL = 2
EXIT_LEAKAGE_FAIL = 3
EXIT_BROKEN_CHAIN = 4
EXIT_INPUT_ERROR = 5
EXIT_HEAD_MISMATCH = 6

_SEALED_ONLY_COLUMNS = frozenset({"threat_class", "ring_id", "generator_params_hash", "planted_ts"})


def _leakage_self_check(out_dir: Path) -> bool:
    """Defense-in-depth build-time check, alongside the pytest leakage
    suite: sealed access must be structurally denied (allowlist has no
    sealed member), and no entity export may carry sealed-only columns.
    """
    try:
        resolve_scope_by_name("sealed._labels")
    except ScopeViolation:
        pass
    else:
        return False  # the allowlist let a sealed name resolve - unreachable today, checked anyway

    for scope in DataScope:
        path = resolve_export_path(scope, out_dir)
        columns = set(
            duckdb.sql(f"SELECT * FROM read_parquet('{path.as_posix()}') LIMIT 0").columns
        )
        if columns & _SEALED_ONLY_COLUMNS:
            return False
    return True


def _quality_gate_manifest(result: QualityGateResult) -> dict[str, object]:
    return {
        "passed": result.passed,
        "profiles": [
            {
                "table": p.table_name,
                "dimensions": [
                    {"name": d.name, "score": d.score, "threshold": d.threshold, "passed": d.passed}
                    for d in p.dimensions
                ],
            }
            for p in result.profiles
        ],
        "validations": [
            {"table": v.table_name, "total_exceptions": v.total_exceptions, "passed": v.passed}
            for v in result.validations
        ],
        "reconciliations": [
            {
                "entity_kind": r.entity_kind.value,
                "left_orphans": r.left_orphans,
                "right_orphans": r.right_orphans,
                "passed": r.passed,
            }
            for r in result.reconciliations
        ],
    }


def _load_thresholds(path: Path | None) -> QualityThresholds:
    if path is None:
        return QualityThresholds()
    data = json.loads(path.read_text())
    defaults = QualityThresholds()
    return QualityThresholds(
        completeness=data.get("completeness", defaults.completeness),
        uniqueness=data.get("uniqueness", defaults.uniqueness),
        validity=data.get("validity", defaults.validity),
        consistency=data.get("consistency", defaults.consistency),
    )


def run_build_dataset(
    seed: int, scale: int, out_dir: Path, quality_thresholds: QualityThresholds
) -> int:
    dataset = build_dataset(BuildConfig(seed=seed, scale=scale))

    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(out_dir / "build.duckdb"))
    persist_dataset(con, dataset)
    export_dataset(con, out_dir)

    if not _leakage_self_check(out_dir):
        print(
            "LEAKAGE CHECK FAILED: sealed-scope data reachable via allowlist or entity export.",
            file=sys.stderr,
        )
        return EXIT_LEAKAGE_FAIL

    with tempfile.TemporaryDirectory(prefix="ts-sentry-quality-gate-") as tmp_dir_name:
        gate_result = run_quality_gate(con, out_dir, Path(tmp_dir_name), quality_thresholds)

    manifest = {
        "seed": seed,
        "scale": scale,
        "generator_version": GENERATOR_VERSION,
        "git_sha": git_sha(),
        "row_counts": {
            "account_meta": len(dataset.accounts),
            "channel": len(dataset.channels),
            "video": len(dataset.videos),
            "comment": len(dataset.comments),
            "engagement_event": len(dataset.engagement_events),
            "infra_hint": len(dataset.infra_hints),
            "sealed_labels": len(dataset.sealed_labels),
        },
        "table_hashes": {
            scope.value: sha256_file(resolve_export_path(scope, out_dir)) for scope in DataScope
        },
        "quality_gate": _quality_gate_manifest(gate_result),
    }
    (out_dir / "build_manifest.json").write_text(json.dumps(manifest, indent=2))

    if not gate_result.passed:
        print("QUALITY GATE FAILED - see build_manifest.json for details.", file=sys.stderr)
        return EXIT_QUALITY_GATE_FAIL

    print(f"Build succeeded: {out_dir}")
    return EXIT_OK


# --------------------------------------------------------------------------
# STEP-03 D6: run-session
# --------------------------------------------------------------------------

TRIAGE_AGENT = "triage"
EVIDENCE_AGENT = "evidence"

SCRIPTED_REVIEW = "scripted"
INTERACTIVE_REVIEW = "interactive"


def _build_adapter(mode: str, responder: Responder = stub_triage_responder) -> ModelAdapter:
    """Resolve the adapter, defaulting to the offline stub.

    ``live`` is refused here unless the environment also says live. The flag
    alone is not enough on purpose: a shell alias or a stray script argument
    should not be able to start spending money, so the intent has to be
    expressed twice, in two different places.
    """
    if mode != ModelMode.LIVE.value:
        return StubAdapter(responder=responder)

    from ts_sentry.orchestrator.adapter import LiveAdapter, resolve_mode

    if resolve_mode() is not ModelMode.LIVE:
        raise InputError(
            "--llm-mode live also requires TS_SENTRY_LLM_MODE=live in the environment. "
            "The stub adapter is the default and runs a complete session offline"
        )
    return LiveAdapter()


def _build_reviewer(review_mode: str, analyst_id: str) -> AnalystReviewer:
    """Resolve the analyst decision boundary.

    ``scripted`` is the default and the CI path: deterministic, declared before
    the session runs, and recorded as scripted in every ledger entry it
    produces. ``interactive`` puts a real person at the prompt. Neither can be
    mistaken for the other after the fact, because ``reviewer_kind`` is inside
    the hash-covered ``HUMAN_DECISION`` payload.
    """
    if review_mode == INTERACTIVE_REVIEW:
        return InteractiveReviewer(reviewer_id=analyst_id)
    return ScriptedReviewer(reviewer_id=analyst_id)


def run_evidence_session_command(
    seed_dataset: Path,
    out_dir: Path,
    *,
    case_id: str,
    subject_id: str | None,
    analyst_id: str,
    llm_mode: str,
    review_mode: str,
    max_hops: int | None,
    seed: int,
    session_id: str | None,
) -> int:
    """Run one evidence session and report where it left its artifacts."""
    if subject_id is None:
        print(
            "run-session: --agent evidence requires --subject naming the entity to investigate",
            file=sys.stderr,
        )
        return EXIT_INPUT_ERROR

    try:
        adapter = _build_adapter(llm_mode, stub_evidence_responder)
        run = run_evidence_session(
            seed_dataset,
            out_dir,
            adapter,
            case_id=case_id,
            subject_id=subject_id,
            reviewer=_build_reviewer(review_mode, analyst_id),
            analyst_id=analyst_id,
            session_id=session_id,
            max_hops=max_hops,
            seed=seed,
        )
    except (InputError, DatasetDigestError, SubjectNotFound) as exc:
        print(f"run-session: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except (FileNotFoundError, duckdb.Error) as exc:
        print(f"run-session: could not open the dataset: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    turn = run.turn
    print(f"session:   {run.manifest.session_id}")
    print(f"analyst:   {run.manifest.analyst_id}")
    print(f"adapter:   {adapter.adapter_id} ({adapter.model_id})")
    print(f"case:      {case_id} (subject {subject_id})")
    print(f"close:     {run.close_reason.value}")
    print(f"hops:      {len(turn.hops)} attempted, {turn.executed_hops} executed")
    print(f"rejected:  {turn.rejected_hops} by the analyst")
    print(f"pack:      {len(turn.pack.nodes)} entities, {len(turn.pack.edges)} relations")
    for hop in turn.hops:
        if hop.attribution is not None:
            print(f"  hop {hop.hop_index}: {hop.pivot_kind} - {hop.attribution}")
    if turn.injection_signals:
        print(f"injection: {turn.injection_signals} signal(s) in pack content")
    print(f"head:      {run.manifest.expected_head.render()}")
    print(f"artifacts: {run.artifacts.out_dir}")

    if not run.intact:
        print(
            f"run-session: the session produced a broken chain at seq "
            f"{run.verification.first_broken_seq}",
            file=sys.stderr,
        )
        return EXIT_BROKEN_CHAIN

    print("result:    session closed with an intact chain")
    return EXIT_OK


def run_run_session(
    seed_dataset: Path,
    out_dir: Path,
    *,
    analyst_id: str,
    llm_mode: str,
    limit: int,
    seed: int,
    session_id: str | None,
) -> int:
    """Run one triage session and report where it left its artifacts."""
    try:
        adapter = _build_adapter(llm_mode)
        run = run_triage_session(
            seed_dataset,
            out_dir,
            adapter,
            analyst_id=analyst_id,
            session_id=session_id,
            limit=limit,
            seed=seed,
        )
    except InputError as exc:
        print(f"run-session: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except DatasetDigestError as exc:
        print(f"run-session: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except (FileNotFoundError, duckdb.Error) as exc:
        print(f"run-session: could not open the dataset: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    queue = run.turn.queue
    print(f"session:   {run.manifest.session_id}")
    print(f"analyst:   {run.manifest.analyst_id}")
    print(f"adapter:   {adapter.adapter_id} ({adapter.model_id})")
    print(f"close:     {run.close_reason.value}")
    print(f"cases:     {0 if queue is None else len(queue.rows)}")
    print(
        f"rationales:{'' if queue is None else ''} {0 if queue is None else queue.rationale_count}"
    )
    if run.turn.rationales is not None and run.turn.rationales.rejected:
        print(f"rejected:  {len(run.turn.rationales.rejected)} rationale(s) failed verification")
    if run.turn.injection_signals:
        print(f"injection: {run.turn.injection_signals} signal(s) in case content")
    print(f"head:      {run.manifest.expected_head.render()}")
    print(f"artifacts: {run.artifacts.out_dir}")

    if not run.intact:
        print(
            f"run-session: the session produced a broken chain at seq "
            f"{run.verification.first_broken_seq}",
            file=sys.stderr,
        )
        return EXIT_BROKEN_CHAIN

    print("result:    session closed with an intact chain")
    return EXIT_OK


# --------------------------------------------------------------------------
# STEP-02 D6: verify-ledger
# --------------------------------------------------------------------------


class InputError(Exception):
    """The path or an argument could not be used. Distinct from an integrity
    failure, which is a finding about a readable chain rather than a problem
    reading one."""


def parse_expect_head(raw: str) -> ChainHead:
    """Parse ``COUNT:HASH``.

    A comparison verb, not an anchor system. This reads an expectation the
    caller already holds; it does not store, derive, or manage one. Anchor
    storage belongs to the STEP-03 session manifest.
    """
    count_text, separator, hash_text = raw.partition(":")
    if not separator:
        raise InputError(f"--expect-head must be COUNT:HASH; got {raw!r}")
    if not count_text.isdigit():
        raise InputError(f"--expect-head COUNT must be a non-negative integer; got {count_text!r}")
    try:
        require_sha256_hex(hash_text, "--expect-head HASH")
    except ValueError as exc:
        raise InputError(str(exc)) from exc
    return ChainHead(count=int(count_text), entry_hash=hash_text)


_READERS: dict[str, Callable[[Path], tuple[LedgerEntry, ...]]] = {
    ".jsonl": read_jsonl,
    ".duckdb": read_store,
}


def _read_entries(path: Path) -> tuple[LedgerEntry, ...]:
    """Dispatch by extension.

    Both readers feed the same ``verify_chain``; only the reading differs, so
    an export and the store it came from cannot disagree about integrity.
    """
    reader = _READERS.get(path.suffix.lower())
    if reader is None:
        supported = ", ".join(sorted(_READERS))
        raise InputError(f"unsupported ledger format {path.suffix!r}; expected one of {supported}")
    if not path.is_file():
        raise InputError(f"no such file: {path}")
    try:
        return reader(path)
    except InputError:  # pragma: no cover - readers do not raise this
        raise
    except Exception as exc:  # noqa: BLE001 - any read failure is an input error
        raise InputError(f"could not read {path}: {type(exc).__name__}: {exc}") from exc


def run_verify_ledger(
    path: Path,
    expect_head_raw: str | None = None,
    expect_head_from: Path | None = None,
) -> int:
    """Verify a ledger chain and report its head.

    Precedence is deliberate: chain integrity is checked before the head
    comparison. A broken chain makes any head claim meaningless, so it is
    reported as a broken chain rather than as a mismatch.

    ``--expect-head-from`` reads the anchor out of a session manifest instead
    of taking it on the command line. STEP-02 shipped the comparison verb and
    deliberately shipped no storage; STEP-03 D1 built the storage, and this is
    the flag that joins them. Without it the anchor exists but nothing reads
    it, which would make the manifest a record rather than a control.

    The two forms are mutually exclusive: an expectation supplied twice could
    disagree with itself, and there is no correct way to resolve that.
    """
    try:
        if expect_head_raw is not None and expect_head_from is not None:
            raise InputError(
                "--expect-head and --expect-head-from are mutually exclusive; "
                "supply the expectation once"
            )
        expected = None if expect_head_raw is None else parse_expect_head(expect_head_raw)
        if expect_head_from is not None:
            try:
                expected = read_expected_head(expect_head_from)
            except ManifestError as exc:
                raise InputError(str(exc)) from exc
        entries = _read_entries(path)
    except InputError as exc:
        print(f"verify-ledger: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    head = chain_head(entries)
    result = verify_chain(entries)

    print(f"path:    {path}")
    print(f"entries: {head.count}")
    print(f"head:    {head.entry_hash}")

    if not result.intact:
        print(f"result:  BROKEN CHAIN at seq {result.first_broken_seq}")
        print(f"reason:  {result.reason.value if result.reason else 'unknown'}")
        print(f"detail:  {result.detail}")
        print(
            f"verify-ledger: broken chain at seq {result.first_broken_seq}",
            file=sys.stderr,
        )
        return EXIT_BROKEN_CHAIN

    if expected is not None and expected != head:
        print("result:  HEAD MISMATCH")
        print(f"expected: {expected.render()}")
        print(f"actual:   {head.render()}")
        print(
            "verify-ledger: chain links are intact but the head does not match the "
            "expectation; entries may have been removed from the end",
            file=sys.stderr,
        )
        return EXIT_HEAD_MISMATCH

    print("result:  intact" + ("" if expected is None else " (head matches)"))
    return EXIT_OK


class _UsageError(Exception):
    """Carries an argparse failure out to ``main`` instead of exiting.

    argparse "terminates the program with a status code of 2"
    (https://docs.python.org/3/library/argparse.html, ``ArgumentParser.error``).
    That is the wrong code twice over here: it collides with
    ``EXIT_QUALITY_GATE_FAIL``, and it makes a malformed argument
    indistinguishable from a build-dataset quality failure. Raising instead
    lets the CLI choose.
    """

    def __init__(self, parser: argparse.ArgumentParser, message: str) -> None:
        super().__init__(message)
        self.parser = parser
        self.message = message


class _RaisingParser(argparse.ArgumentParser):
    """Parser that raises rather than exiting on a usage error."""

    def error(self, message: str) -> NoReturn:
        raise _UsageError(self, message)


VERIFY_LEDGER = "verify-ledger"
RUN_SESSION = "run-session"

TRANSLATES_USAGE_ERRORS = frozenset({VERIFY_LEDGER, RUN_SESSION})
"""Subcommands whose argparse usage errors become ``EXIT_INPUT_ERROR``.

Argparse exits 2 on a usage error, and 2 is ``EXIT_QUALITY_GATE_FAIL`` in this
CLI, so a mistyped flag would be indistinguishable from a failed data-quality
gate. STEP-02 removed that collision for ``verify-ledger``; ``run-session``
reintroduced it by arriving in STEP-03 without the translation, which is a
defect in a documented contract rather than a stylistic gap.

``build-dataset`` is deliberately absent. It has exited 2 on usage errors since
STEP-01, that is its published contract, and changing it here would alter a
closed phase's behavior for tidiness.
"""


def main(argv: list[str] | None = None) -> int:
    argv_list = sys.argv[1:] if argv is None else list(argv)

    # The root parser raises too, not just the subparsers. Unrecognized
    # arguments are reported by the *root* parser even when a subcommand was
    # named, because parse_args() collects leftovers from parse_known_args()
    # and errors on them itself. Leaving the root alone let
    # `verify-ledger FILE --not-a-flag` escape as argparse's status 2, which
    # is the collision this change exists to remove.
    parser = _RaisingParser(prog="ts-sentry")
    subparsers = parser.add_subparsers(dest="command", required=True, parser_class=_RaisingParser)

    build_parser = subparsers.add_parser("build-dataset")
    build_parser.add_argument("--seed", type=int, required=True)
    build_parser.add_argument("--scale", type=int, required=True)
    build_parser.add_argument("--out", type=Path, default=Path("build"))
    build_parser.add_argument("--quality-thresholds", type=Path, default=None)

    verify_parser = subparsers.add_parser(VERIFY_LEDGER)
    verify_parser.add_argument("path", type=Path)
    verify_parser.add_argument(
        "--expect-head",
        type=str,
        default=None,
        metavar="COUNT:HASH",
        help=(
            "Compare the chain head against an expectation you already hold. "
            "Chain verification alone cannot detect entries removed from the end."
        ),
    )
    verify_parser.add_argument(
        "--expect-head-from",
        type=Path,
        default=None,
        metavar="MANIFEST",
        help=(
            "Read the expected head from a session manifest. Mutually exclusive with --expect-head."
        ),
    )

    session_parser = subparsers.add_parser(RUN_SESSION)
    session_parser.add_argument("--agent", choices=[TRIAGE_AGENT, EVIDENCE_AGENT], required=True)
    session_parser.add_argument("--seed-dataset", type=Path, required=True)
    session_parser.add_argument("--out", type=Path, default=Path("session"))
    session_parser.add_argument("--analyst-id", type=str, default="analyst")
    session_parser.add_argument(
        "--llm-mode",
        choices=[ModelMode.STUB.value, ModelMode.LIVE.value],
        default=ModelMode.STUB.value,
        help=(
            "Model adapter. The default stub is deterministic, offline, and free; "
            "live additionally requires TS_SENTRY_LLM_MODE=live in the environment."
        ),
    )
    session_parser.add_argument("--limit", type=int, default=25)
    session_parser.add_argument("--seed", type=int, default=42)
    session_parser.add_argument("--session-id", type=str, default=None)
    session_parser.add_argument(
        "--case",
        type=str,
        default="case-0000",
        help="Case id for an evidence session. Ignored by the triage agent.",
    )
    session_parser.add_argument(
        "--subject",
        type=str,
        default=None,
        help=(
            "Entity the evidence session investigates, typically a channel id taken from a "
            "triage session's ranked_queue.json. Required with --agent evidence."
        ),
    )
    session_parser.add_argument(
        "--review",
        choices=[SCRIPTED_REVIEW, INTERACTIVE_REVIEW],
        default=SCRIPTED_REVIEW,
        help=(
            "Where analyst decisions come from. The scripted reviewer is deterministic and "
            "records itself as scripted in every ledgered decision; interactive prompts a "
            "real person."
        ),
    )
    session_parser.add_argument("--max-hops", type=int, default=None)

    # The root parser has no options of its own, so the first token is the
    # subcommand. Needed because a root-parser error carries no parsed
    # namespace to read the command from.
    # The root parser has no options of its own, so the first token is the
    # subcommand. Needed because a root-parser error carries no parsed
    # namespace to read the command from.
    named = argv_list[0] if argv_list else None
    parsers: dict[argparse.ArgumentParser, str] = {
        verify_parser: VERIFY_LEDGER,
        session_parser: RUN_SESSION,
    }

    try:
        args = parser.parse_args(argv_list)
    except _UsageError as exc:
        # Either the failing subparser is one that translates, or the root
        # parser failed on an invocation that named one. The second case is
        # not hypothetical: unrecognized arguments are reported by the *root*
        # parser even when a subcommand was named, because parse_args()
        # collects leftovers from parse_known_args() and errors on them
        # itself.
        subcommand = parsers.get(exc.parser)
        if subcommand is None and named in TRANSLATES_USAGE_ERRORS:
            subcommand = named
        if subcommand is not None:
            # Every malformed invocation of these subcommands exits 5, on
            # every supported interpreter. Caught by CI on 3.12: argparse
            # resolves a dash-prefixed option *value* like "-1:<hash>"
            # differently across versions, because "positional arguments may
            # only begin with - if they look like negative numbers"
            # (https://docs.python.org/3/library/argparse.html), and
            # "-1:<hash>" does not. 3.12 classifies it as an option token and
            # errors with its own status 2 before our validation runs; 3.14
            # consumes it as a value and reaches parse_expect_head.
            # Translating argparse's exit makes the contract independent of
            # which reading the interpreter takes.
            print(f"{subcommand}: {exc.message}", file=sys.stderr)
            return EXIT_INPUT_ERROR
        # build-dataset keeps argparse's stock behaviour verbatim, so this
        # cannot silently alter the STEP-01 contract.
        exc.parser.print_usage(sys.stderr)
        exc.parser.exit(2, f"{exc.parser.prog}: error: {exc.message}\n")

    if args.command == "build-dataset":
        thresholds = _load_thresholds(args.quality_thresholds)
        return run_build_dataset(args.seed, args.scale, args.out, thresholds)

    if args.command == VERIFY_LEDGER:
        return run_verify_ledger(args.path, args.expect_head, args.expect_head_from)

    if args.command == RUN_SESSION:
        if args.agent == EVIDENCE_AGENT:
            return run_evidence_session_command(
                args.seed_dataset,
                args.out,
                case_id=args.case,
                subject_id=args.subject,
                analyst_id=args.analyst_id,
                llm_mode=args.llm_mode,
                review_mode=args.review,
                max_hops=args.max_hops,
                seed=args.seed,
                session_id=args.session_id,
            )
        return run_run_session(
            args.seed_dataset,
            args.out,
            analyst_id=args.analyst_id,
            llm_mode=args.llm_mode,
            limit=args.limit,
            seed=args.seed,
            session_id=args.session_id,
        )

    parser.error(f"unknown command {args.command!r}")
    return 2  # pragma: no cover - parser.error() above always raises SystemExit


if __name__ == "__main__":
    raise SystemExit(main())
