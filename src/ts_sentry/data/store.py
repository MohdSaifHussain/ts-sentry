# SPDX-License-Identifier: MIT
"""DuckDB DDL and persistence for the synthetic platform.

Every statement here is a static, parameterized template - table and column
names are fixed string literals in this module, never built from runtime
values; row values are always bound via ``?`` placeholders. No dynamic SQL
anywhere (STEP-01 3.2 / CLAUDE.md standing rule).

Two schemas: ``main`` for the six queryable entity tables (reachable via
``ts_sentry.governance.scopes.DataScope``), ``sealed`` for ``_labels``
(reachable only by the build pipeline and, from STEP-07 on, measurement
code - never by this module's own callers on behalf of an agent).

Persistence (``persist_dataset``) and Parquet export (``export_dataset``)
insert/read rows via bound parameters or the DuckDB relation API - never by
formatting row values or paths into SQL text.
"""

from pathlib import Path

import duckdb
import pandas as pd

from ts_sentry.data.population import Dataset
from ts_sentry.governance.scopes import DataScope, resolve_export_path, resolve_table

_CREATE_SCHEMA_SEALED = "CREATE SCHEMA IF NOT EXISTS sealed;"

_CREATE_ACCOUNT_META = """
CREATE TABLE IF NOT EXISTS main.account_meta (
    account_id VARCHAR PRIMARY KEY,
    created_ts TIMESTAMPTZ NOT NULL,
    display_name VARCHAR NOT NULL,
    is_verified BOOLEAN NOT NULL,
    signup_ip_bucket VARCHAR NOT NULL,
    device_fingerprint_hint VARCHAR
);
"""

_CREATE_CHANNEL = """
CREATE TABLE IF NOT EXISTS main.channel (
    channel_id VARCHAR PRIMARY KEY,
    account_id VARCHAR NOT NULL REFERENCES main.account_meta(account_id),
    created_ts TIMESTAMPTZ NOT NULL,
    display_name VARCHAR NOT NULL,
    subscriber_count INTEGER NOT NULL,
    description VARCHAR NOT NULL
);
"""

_CREATE_VIDEO = """
CREATE TABLE IF NOT EXISTS main.video (
    video_id VARCHAR PRIMARY KEY,
    channel_id VARCHAR NOT NULL REFERENCES main.channel(channel_id),
    title VARCHAR NOT NULL,
    description VARCHAR NOT NULL,
    published_ts TIMESTAMPTZ NOT NULL,
    duration_s INTEGER NOT NULL,
    synthetic_media_disclosed BOOLEAN NOT NULL,
    provenance_signal VARCHAR NOT NULL
);
"""

_CREATE_COMMENT = """
CREATE TABLE IF NOT EXISTS main.comment (
    comment_id VARCHAR PRIMARY KEY,
    video_id VARCHAR NOT NULL REFERENCES main.video(video_id),
    account_id VARCHAR NOT NULL REFERENCES main.account_meta(account_id),
    parent_comment_id VARCHAR,
    posted_ts TIMESTAMPTZ NOT NULL,
    text VARCHAR NOT NULL,
    template_id VARCHAR
);
"""

_CREATE_ENGAGEMENT_EVENT = """
CREATE TABLE IF NOT EXISTS main.engagement_event (
    event_id VARCHAR PRIMARY KEY,
    kind VARCHAR NOT NULL,
    account_id VARCHAR NOT NULL REFERENCES main.account_meta(account_id),
    video_id VARCHAR,
    channel_id VARCHAR,
    ts_ist TIMESTAMPTZ NOT NULL,
    session_id VARCHAR
);
"""

_CREATE_INFRA_HINT = """
CREATE TABLE IF NOT EXISTS main.infra_hint (
    hint_id VARCHAR PRIMARY KEY,
    subject_kind VARCHAR NOT NULL,
    subject_id VARCHAR NOT NULL,
    signal_type VARCHAR NOT NULL,
    signal_value VARCHAR NOT NULL,
    observed_ts TIMESTAMPTZ NOT NULL
);
"""

_CREATE_SEALED_LABELS = """
CREATE TABLE IF NOT EXISTS sealed._labels (
    entity_kind VARCHAR NOT NULL,
    entity_id VARCHAR NOT NULL,
    threat_class VARCHAR NOT NULL,
    ring_id VARCHAR,
    planted_ts TIMESTAMPTZ NOT NULL,
    generator_params_hash VARCHAR NOT NULL,
    PRIMARY KEY (entity_kind, entity_id)
);
"""

# Applied in FK-safe order.
_ENTITY_TABLE_DDL: tuple[str, ...] = (
    _CREATE_ACCOUNT_META,
    _CREATE_CHANNEL,
    _CREATE_VIDEO,
    _CREATE_COMMENT,
    _CREATE_ENGAGEMENT_EVENT,
    _CREATE_INFRA_HINT,
)


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create the `main` entity tables and the `sealed` schema/`_labels` table.

    Idempotent (``IF NOT EXISTS`` throughout) so it is safe to call at the
    start of every build.
    """
    for ddl in _ENTITY_TABLE_DDL:
        con.execute(ddl)
    con.execute(_CREATE_SCHEMA_SEALED)
    con.execute(_CREATE_SEALED_LABELS)


_ACCOUNT_META_COLUMNS = (
    "account_id",
    "created_ts",
    "display_name",
    "is_verified",
    "signup_ip_bucket",
    "device_fingerprint_hint",
)
_CHANNEL_COLUMNS = (
    "channel_id",
    "account_id",
    "created_ts",
    "display_name",
    "subscriber_count",
    "description",
)
_VIDEO_COLUMNS = (
    "video_id",
    "channel_id",
    "title",
    "description",
    "published_ts",
    "duration_s",
    "synthetic_media_disclosed",
    "provenance_signal",
)
_COMMENT_COLUMNS = (
    "comment_id",
    "video_id",
    "account_id",
    "parent_comment_id",
    "posted_ts",
    "text",
    "template_id",
)
_ENGAGEMENT_EVENT_COLUMNS = (
    "event_id",
    "kind",
    "account_id",
    "video_id",
    "channel_id",
    "ts_ist",
    "session_id",
)
_INFRA_HINT_COLUMNS = (
    "hint_id",
    "subject_kind",
    "subject_id",
    "signal_type",
    "signal_value",
    "observed_ts",
)
_SEALED_LABEL_COLUMNS = (
    "entity_kind",
    "entity_id",
    "threat_class",
    "ring_id",
    "planted_ts",
    "generator_params_hash",
)

_BULK_INSERT_VIEW = "_ts_sentry_bulk_insert"


def _bulk_insert(
    con: duckdb.DuckDBPyConnection,
    table_name: str,
    columns: tuple[str, ...],
    rows: list[tuple[object, ...]],
) -> None:
    """Load ``rows`` into ``table_name`` via a registered DataFrame plus a
    set-based ``INSERT ... SELECT`` - orders of magnitude faster than
    ``executemany`` for tens of thousands of rows, and still a fixed SQL
    template: only the registered data varies, never the query text.
    """
    if not rows:
        return
    frame = pd.DataFrame(rows, columns=list(columns))
    con.register(_BULK_INSERT_VIEW, frame)
    try:
        # table_name is always a fixed literal from the call site below,
        # never derived from row data - this is a fixed template per call,
        # not string-built SQL from untrusted input.
        con.execute(f"INSERT INTO {table_name} SELECT * FROM {_BULK_INSERT_VIEW}")
    finally:
        con.unregister(_BULK_INSERT_VIEW)


def persist_dataset(con: duckdb.DuckDBPyConnection, dataset: Dataset) -> None:
    """Write every row of ``dataset`` into the `main` entity tables and
    `sealed._labels`, via ``init_schema`` plus bulk-loaded inserts, in
    FK-safe order. Assumes an empty (freshly opened) connection.
    """
    init_schema(con)

    _bulk_insert(
        con,
        "main.account_meta",
        _ACCOUNT_META_COLUMNS,
        [
            (
                a.account_id,
                a.created_ts,
                a.display_name,
                a.is_verified,
                a.signup_ip_bucket,
                a.device_fingerprint_hint,
            )
            for a in dataset.accounts
        ],
    )
    _bulk_insert(
        con,
        "main.channel",
        _CHANNEL_COLUMNS,
        [
            (
                c.channel_id,
                c.account_id,
                c.created_ts,
                c.display_name,
                c.subscriber_count,
                c.description,
            )
            for c in dataset.channels
        ],
    )
    _bulk_insert(
        con,
        "main.video",
        _VIDEO_COLUMNS,
        [
            (
                v.video_id,
                v.channel_id,
                v.title,
                v.description,
                v.published_ts,
                v.duration_s,
                v.synthetic_media_disclosed,
                v.provenance_signal.value,
            )
            for v in dataset.videos
        ],
    )
    _bulk_insert(
        con,
        "main.comment",
        _COMMENT_COLUMNS,
        [
            (
                c.comment_id,
                c.video_id,
                c.account_id,
                c.parent_comment_id,
                c.posted_ts,
                c.text,
                c.template_id,
            )
            for c in dataset.comments
        ],
    )
    _bulk_insert(
        con,
        "main.engagement_event",
        _ENGAGEMENT_EVENT_COLUMNS,
        [
            (
                e.event_id,
                e.kind.value,
                e.account_id,
                e.video_id,
                e.channel_id,
                e.ts_ist,
                e.session_id,
            )
            for e in dataset.engagement_events
        ],
    )
    _bulk_insert(
        con,
        "main.infra_hint",
        _INFRA_HINT_COLUMNS,
        [
            (
                h.hint_id,
                h.subject_kind.value,
                h.subject_id,
                h.signal_type.value,
                h.signal_value,
                h.observed_ts,
            )
            for h in dataset.infra_hints
        ],
    )
    _bulk_insert(
        con,
        "sealed._labels",
        _SEALED_LABEL_COLUMNS,
        [
            (
                label.entity_kind.value,
                label.entity_id,
                label.threat_class.value,
                label.ring_id,
                label.planted_ts,
                label.generator_params_hash,
            )
            for label in dataset.sealed_labels
        ],
    )


def export_dataset(con: duckdb.DuckDBPyConnection, out_dir: Path) -> None:
    """Export every `main` entity table to Parquet under ``out_dir`` (one
    file per :class:`DataScope` member), and `sealed._labels` separately
    under ``out_dir/sealed/`` - a physically distinct directory, so the
    sealed export can never be mistaken for, or accidentally globbed
    alongside, an entity-table export.

    Uses the DuckDB relation API (``con.table(...).write_parquet(...)``),
    never string-formatted SQL, for both the table reference and the path.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for scope in DataScope:
        path = resolve_export_path(scope, out_dir)
        con.table(resolve_table(scope)).write_parquet(str(path))

    sealed_dir = out_dir / "sealed"
    sealed_dir.mkdir(parents=True, exist_ok=True)
    con.table("sealed._labels").write_parquet(str(sealed_dir / "_labels.parquet"))
