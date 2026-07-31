# SPDX-License-Identifier: MIT
"""Smoke test for the D2 DuckDB DDL: confirms the static templates are valid
SQL and produce the expected `main` entity tables plus the `sealed._labels`
table, idempotently.
"""

import duckdb

from ts_sentry.data.store import init_schema


def test_init_schema_creates_expected_tables() -> None:
    con = duckdb.connect(":memory:")
    init_schema(con)

    tables = {
        (row[0], row[1])
        for row in con.execute(
            "SELECT table_schema, table_name FROM information_schema.tables"
        ).fetchall()
    }

    expected = {
        ("main", "account_meta"),
        ("main", "channel"),
        ("main", "video"),
        ("main", "comment"),
        ("main", "engagement_event"),
        ("main", "infra_hint"),
        ("sealed", "_labels"),
    }
    assert expected <= tables


def test_init_schema_is_idempotent() -> None:
    con = duckdb.connect(":memory:")
    init_schema(con)
    init_schema(con)  # must not raise
