# SPDX-License-Identifier: MIT
"""D6: end-to-end AnalystKit quality gate against a real build.

This is an integration test (spawns the real `analystkit` subprocess
several times) rather than a pure unit test, since the whole point of D6
is that our text-parsing wrapper matches AnalystKit's actual CLI output -
verified once here rather than mocked, per the review requirement that
invariants be tested, not just documented.
"""

from pathlib import Path

import duckdb

from ts_sentry.data.generator import build_dataset
from ts_sentry.data.population import BuildConfig
from ts_sentry.data.quality import run_quality_gate
from ts_sentry.data.store import export_dataset, persist_dataset


def test_quality_gate_passes_on_a_real_build(tmp_path: Path) -> None:
    dataset = build_dataset(BuildConfig(seed=11, scale=1))
    con = duckdb.connect(":memory:")
    persist_dataset(con, dataset)
    out_dir = tmp_path / "out"
    export_dataset(con, out_dir)
    tmp_dir = tmp_path / "reconcile_tmp"
    tmp_dir.mkdir()

    result = run_quality_gate(con, out_dir, tmp_dir)

    assert result.passed, [
        (p.table_name, d.name, d.score, d.threshold)
        for p in result.profiles
        for d in p.dimensions
        if not d.passed
    ]
    assert len(result.profiles) == 6
    assert len(result.validations) == 6
    assert len(result.reconciliations) == 4
    for reconciliation in result.reconciliations:
        assert reconciliation.left_orphans == 0
        assert reconciliation.right_orphans == 0
