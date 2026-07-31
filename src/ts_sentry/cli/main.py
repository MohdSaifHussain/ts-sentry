# SPDX-License-Identifier: MIT
"""D7: `ts-sentry build-dataset` - the single CLI entry point for Phase 1.

Orchestrates, in order: D1+D3 in-memory build, D4 DuckDB persistence and
Parquet export, a build-time leakage self-check (defense-in-depth alongside
the pytest leakage suite), the D6 AnalystKit quality gate, and the D1
build manifest.

Exit codes (documented in README.md): 0 pass, 2 quality-gate fail,
3 leakage fail.
"""

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb

from ts_sentry.data.generator import build_dataset
from ts_sentry.data.population import BuildConfig
from ts_sentry.data.quality import QualityGateResult, QualityThresholds, run_quality_gate
from ts_sentry.data.store import export_dataset, persist_dataset
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ts-sentry")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build-dataset")
    build_parser.add_argument("--seed", type=int, required=True)
    build_parser.add_argument("--scale", type=int, required=True)
    build_parser.add_argument("--out", type=Path, default=Path("build"))
    build_parser.add_argument("--quality-thresholds", type=Path, default=None)

    args = parser.parse_args(argv)

    if args.command == "build-dataset":
        thresholds = _load_thresholds(args.quality_thresholds)
        return run_build_dataset(args.seed, args.scale, args.out, thresholds)

    parser.error(f"unknown command {args.command!r}")
    return 2  # pragma: no cover - parser.error() above always raises SystemExit


if __name__ == "__main__":
    raise SystemExit(main())
