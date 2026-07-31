# SPDX-License-Identifier: MIT
"""The `ts-sentry` CLI.

Two subcommands so far, both documented in README.md:

* ``build-dataset`` (STEP-01 D7) orchestrates the in-memory build, DuckDB
  persistence and Parquet export, a build-time leakage self-check, the
  AnalystKit quality gate, and the build manifest.
* ``verify-ledger`` (STEP-02 D6) recomputes a trajectory-ledger hash chain
  and reports the first broken link.

Exit codes are allocated across the whole CLI rather than per subcommand, so
no number means two different things: 0 pass, 2 quality-gate fail, 3 leakage
fail, 4 broken chain, 5 input error, 6 chain-head mismatch.
"""

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import duckdb

from ts_sentry.data.generator import build_dataset
from ts_sentry.data.population import BuildConfig
from ts_sentry.data.quality import QualityGateResult, QualityThresholds, run_quality_gate
from ts_sentry.data.store import export_dataset, persist_dataset
from ts_sentry.governance.canonical import require_sha256_hex
from ts_sentry.governance.ledger import (
    GENESIS_PREV_HASH,
    LedgerEntry,
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

GENERATOR_VERSION = "0.1.0"

EXIT_OK = 0
EXIT_QUALITY_GATE_FAIL = 2
EXIT_LEAKAGE_FAIL = 3
EXIT_BROKEN_CHAIN = 4
EXIT_INPUT_ERROR = 5
EXIT_HEAD_MISMATCH = 6

_SEALED_ONLY_COLUMNS = frozenset({"threat_class", "ring_id", "generator_params_hash", "planted_ts"})


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        "git_sha": _git_sha(),
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
            scope.value: _sha256_file(resolve_export_path(scope, out_dir)) for scope in DataScope
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
# STEP-02 D6: verify-ledger
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChainHead:
    """Where a chain currently ends.

    ``entry_hash`` of an empty chain is the genesis value, so a head is
    always well defined and "nothing has been appended" has a spelling rather
    than being a null.
    """

    count: int
    entry_hash: str

    def render(self) -> str:
        return f"{self.count}:{self.entry_hash}"


def chain_head(entries: tuple[LedgerEntry, ...]) -> ChainHead:
    if not entries:
        return ChainHead(count=0, entry_hash=GENESIS_PREV_HASH)
    return ChainHead(count=len(entries), entry_hash=entries[-1].entry_hash)


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


def run_verify_ledger(path: Path, expect_head_raw: str | None = None) -> int:
    """Verify a ledger chain and report its head.

    Precedence is deliberate: chain integrity is checked before the head
    comparison. A broken chain makes any head claim meaningless, so it is
    reported as a broken chain rather than as a mismatch.
    """
    try:
        expected = None if expect_head_raw is None else parse_expect_head(expect_head_raw)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ts-sentry")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build-dataset")
    build_parser.add_argument("--seed", type=int, required=True)
    build_parser.add_argument("--scale", type=int, required=True)
    build_parser.add_argument("--out", type=Path, default=Path("build"))
    build_parser.add_argument("--quality-thresholds", type=Path, default=None)

    verify_parser = subparsers.add_parser("verify-ledger")
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

    args = parser.parse_args(argv)

    if args.command == "build-dataset":
        thresholds = _load_thresholds(args.quality_thresholds)
        return run_build_dataset(args.seed, args.scale, args.out, thresholds)

    if args.command == "verify-ledger":
        return run_verify_ledger(args.path, args.expect_head)

    parser.error(f"unknown command {args.command!r}")
    return 2  # pragma: no cover - parser.error() above always raises SystemExit


if __name__ == "__main__":
    raise SystemExit(main())
