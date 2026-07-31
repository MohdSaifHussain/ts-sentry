# SPDX-License-Identifier: MIT
"""D4: sealed ground-truth writer, entity persistence, and Parquet export.

Includes the leakage check STEP-01 3.3 calls for beyond the DuckDB schema:
entity-table Parquet exports must carry no label/threat columns (no leakage
via denormalization), and the sealed export must live in a directory an
entity-table reader would never glob.
"""

from pathlib import Path

import duckdb

from ts_sentry.data.generator import build_dataset
from ts_sentry.data.population import BuildConfig, Dataset
from ts_sentry.data.store import export_dataset, persist_dataset
from ts_sentry.governance.scopes import DataScope, resolve_export_path


def _small_dataset() -> Dataset:
    return build_dataset(BuildConfig(seed=7, scale=1))


def test_persist_dataset_row_counts_match(tmp_path: Path) -> None:
    dataset = _small_dataset()
    con = duckdb.connect(str(tmp_path / "build.duckdb"))
    persist_dataset(con, dataset)

    assert con.execute("SELECT count(*) FROM main.account_meta").fetchone() == (
        len(dataset.accounts),
    )
    assert con.execute("SELECT count(*) FROM main.channel").fetchone() == (len(dataset.channels),)
    assert con.execute("SELECT count(*) FROM main.video").fetchone() == (len(dataset.videos),)
    assert con.execute("SELECT count(*) FROM main.comment").fetchone() == (len(dataset.comments),)
    assert con.execute("SELECT count(*) FROM main.engagement_event").fetchone() == (
        len(dataset.engagement_events),
    )
    assert con.execute("SELECT count(*) FROM main.infra_hint").fetchone() == (
        len(dataset.infra_hints),
    )
    assert con.execute("SELECT count(*) FROM sealed._labels").fetchone() == (
        len(dataset.sealed_labels),
    )


def test_export_dataset_writes_one_parquet_per_scope_plus_sealed(tmp_path: Path) -> None:
    dataset = _small_dataset()
    con = duckdb.connect(":memory:")
    persist_dataset(con, dataset)
    export_dataset(con, tmp_path)

    for scope in DataScope:
        assert resolve_export_path(scope, tmp_path).is_file()

    sealed_path = tmp_path / "sealed" / "_labels.parquet"
    assert sealed_path.is_file()
    # Physically separate directory, not a sibling an entity-table glob
    # (e.g. `out_dir/*.parquet`) would ever pick up.
    assert sealed_path.parent != tmp_path


def test_entity_exports_carry_no_label_columns(tmp_path: Path) -> None:
    dataset = _small_dataset()
    con = duckdb.connect(":memory:")
    persist_dataset(con, dataset)
    export_dataset(con, tmp_path)

    label_only_columns = {"threat_class", "ring_id", "generator_params_hash", "planted_ts"}
    for scope in DataScope:
        path = resolve_export_path(scope, tmp_path)
        columns = set(
            duckdb.sql(f"SELECT * FROM read_parquet('{path.as_posix()}') LIMIT 0").columns
        )
        assert not (columns & label_only_columns), f"{scope} export leaks label columns: {columns}"
