# SPDX-License-Identifier: MIT
"""D7: `ts-sentry build-dataset` CLI, exit codes, and the build manifest."""

import json
from pathlib import Path

import duckdb
import pytest

from ts_sentry.cli.main import (
    EXIT_OK,
    EXIT_QUALITY_GATE_FAIL,
    _leakage_self_check,
    _load_thresholds,
    main,
)
from ts_sentry.data.quality import QualityThresholds
from ts_sentry.governance.scopes import DataScope, resolve_export_path


def test_build_dataset_succeeds_and_writes_expected_tree(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    exit_code = main(["build-dataset", "--seed", "5", "--scale", "1", "--out", str(out_dir)])

    assert exit_code == EXIT_OK
    assert (out_dir / "build.duckdb").is_file()
    assert (out_dir / "sealed" / "_labels.parquet").is_file()
    for table in ("account_meta", "channel", "video", "comment", "engagement_event", "infra_hint"):
        assert (out_dir / f"{table}.parquet").is_file()

    manifest = json.loads((out_dir / "build_manifest.json").read_text())
    assert manifest["seed"] == 5
    assert manifest["scale"] == 1
    assert manifest["quality_gate"]["passed"] is True
    assert set(manifest["row_counts"]) == {
        "account_meta",
        "channel",
        "video",
        "comment",
        "engagement_event",
        "infra_hint",
        "sealed_labels",
    }
    # No scratch reconcile artifacts should leak into the final build tree.
    assert not any(out_dir.rglob("*_ids.csv"))
    assert not any(out_dir.rglob("*_labels.csv"))


def test_build_dataset_fails_quality_gate_on_unmeetable_threshold(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    thresholds_path = tmp_path / "strict.json"
    thresholds_path.write_text(json.dumps({"completeness": 99.9}))

    exit_code = main(
        [
            "build-dataset",
            "--seed",
            "5",
            "--scale",
            "1",
            "--out",
            str(out_dir),
            "--quality-thresholds",
            str(thresholds_path),
        ]
    )

    assert exit_code == EXIT_QUALITY_GATE_FAIL
    manifest = json.loads((out_dir / "build_manifest.json").read_text())
    assert manifest["quality_gate"]["passed"] is False


def test_load_thresholds_partial_override_keeps_other_defaults(tmp_path: Path) -> None:
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps({"completeness": 42.0}))

    thresholds = _load_thresholds(path)

    defaults = QualityThresholds()
    assert thresholds.completeness == 42.0
    assert thresholds.uniqueness == defaults.uniqueness
    assert thresholds.validity == defaults.validity
    assert thresholds.consistency == defaults.consistency


def test_load_thresholds_none_path_returns_defaults() -> None:
    assert _load_thresholds(None) == QualityThresholds()


def test_main_requires_seed_and_scale() -> None:
    with pytest.raises(SystemExit):
        main(["build-dataset"])


def test_main_requires_a_command() -> None:
    with pytest.raises(SystemExit):
        main([])


def test_leakage_self_check_passes_on_a_clean_build(tmp_path: Path) -> None:
    exit_code = main(["build-dataset", "--seed", "6", "--scale", "1", "--out", str(tmp_path)])
    assert exit_code == EXIT_OK
    assert _leakage_self_check(tmp_path) is True


def test_leakage_self_check_detects_a_real_leak(tmp_path: Path) -> None:
    """Proves the check is discriminating, not vacuous: a deliberately
    leaky export (a label column denormalized onto an entity table) must
    make it fail, mirroring the red-team requirement on the scope
    allowlist itself (tests/test_scope_leakage.py).
    """
    exit_code = main(["build-dataset", "--seed", "6", "--scale", "1", "--out", str(tmp_path)])
    assert exit_code == EXIT_OK

    leaky_path = resolve_export_path(DataScope.CHANNEL, tmp_path)
    con = duckdb.connect(":memory:")
    con.sql(
        f"SELECT *, 'benign' AS threat_class FROM read_parquet('{leaky_path.as_posix()}')"
    ).write_parquet(str(leaky_path))

    assert _leakage_self_check(tmp_path) is False
