# SPDX-License-Identifier: MIT
"""DataScope allowlist: the STEP-02 pre-seed this phase owes STEP-02.

STEP-02 ("governance.mandate") depends on STEP-01 for the ``DataScope`` enum
and the sealed schema (docs/decisions/STEP-02-governance-core.md, "Depends
on" line). This module ships only that dependency now: the enum and the two
resolvers needed for the STEP-01 3.3 sealing test. It does not implement
Mandate, gates, the ledger, or the verifier - those are STEP-02 scope.
``governance.mandate`` will import ``DataScope`` from here rather than
redefining it.

Allowlist semantics: ``DataScope`` enumerates every table the orchestrator
may query on an agent's behalf. There is deliberately no member that
resolves to the ``sealed`` schema or its Parquet export directory - absence
is denial. Both resolvers below are total (exhaustive match) over the
members that do exist and raise ``ScopeViolation`` for anything else,
so a sealed-scope request is structurally refused rather than merely
unhandled.
"""

from enum import StrEnum
from pathlib import Path
from typing import assert_never


class DataScope(StrEnum):
    """Tables/views the orchestrator may resolve on behalf of an agent."""

    CHANNEL = "channel"
    VIDEO = "video"
    COMMENT = "comment"
    ENGAGEMENT_EVENT = "engagement_event"
    ACCOUNT_META = "account_meta"
    INFRA_HINT = "infra_hint"


class ScopeViolation(Exception):
    """Raised when scope resolution is attempted for a non-allowlisted target."""


def resolve_scope_by_name(name: str) -> DataScope:
    """Resolve an arbitrary requested name (e.g. from an agent) to a
    ``DataScope`` member, or deny it.

    This is the entry point that models the actual attack surface: a
    caller does not hand us a pre-validated ``DataScope``, it hands us a
    string like ``"sealed._labels"``. Lookup is by enum *value*, so any
    name with no matching ``DataScope`` member - including every sealed-scope
    name, since none exists - is denied by construction (allowlist
    semantics: absence is denial).
    """
    try:
        return DataScope(name)
    except ValueError as exc:
        raise ScopeViolation(f"no DataScope member resolves {name!r}") from exc


def resolve_table(scope: DataScope) -> str:
    """Return the DuckDB-qualified table name for an allowlisted scope.

    Total over ``DataScope``: every member maps to exactly one table, and
    mypy's exhaustiveness check (``assert_never``) guarantees this function
    cannot silently fall through to an unhandled member as the enum grows.
    """
    match scope:
        case DataScope.CHANNEL:
            return "main.channel"
        case DataScope.VIDEO:
            return "main.video"
        case DataScope.COMMENT:
            return "main.comment"
        case DataScope.ENGAGEMENT_EVENT:
            return "main.engagement_event"
        case DataScope.ACCOUNT_META:
            return "main.account_meta"
        case DataScope.INFRA_HINT:
            return "main.infra_hint"
        case _:  # pragma: no cover - exhaustiveness guard, unreachable per mypy
            assert_never(scope)


def resolve_export_path(scope: DataScope, out_dir: Path) -> Path:
    """Return the Parquet export path for an allowlisted scope.

    Mirrors ``resolve_table``: total over ``DataScope``, so the sealed
    export directory (``<out_dir>/sealed/``) is unreachable through this
    function by construction, not by convention.
    """
    match scope:
        case DataScope.CHANNEL:
            return out_dir / "channel.parquet"
        case DataScope.VIDEO:
            return out_dir / "video.parquet"
        case DataScope.COMMENT:
            return out_dir / "comment.parquet"
        case DataScope.ENGAGEMENT_EVENT:
            return out_dir / "engagement_event.parquet"
        case DataScope.ACCOUNT_META:
            return out_dir / "account_meta.parquet"
        case DataScope.INFRA_HINT:
            return out_dir / "infra_hint.parquet"
        case _:  # pragma: no cover - exhaustiveness guard, unreachable per mypy
            assert_never(scope)
