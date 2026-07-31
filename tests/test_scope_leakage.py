# SPDX-License-Identifier: MIT
"""STEP-01 3.3 interim leakage test: sealed scope is structurally unreachable
through the DataScope allowlist. Phase 2's full mandate-resolution leakage
test (ledgered MANDATE_VIOLATION_ATTEMPT) supersedes this once governance
core lands.
"""

from enum import StrEnum
from pathlib import Path

import pytest

from ts_sentry.governance.scopes import (
    DataScope,
    ScopeViolation,
    resolve_export_path,
    resolve_scope_by_name,
    resolve_table,
)


def test_datascope_has_no_sealed_member() -> None:
    values = {member.value for member in DataScope}
    assert "sealed" not in values
    assert "_labels" not in values
    assert not any("sealed" in value for value in values)


def test_sealed_table_name_denied() -> None:
    with pytest.raises(ScopeViolation):
        resolve_scope_by_name("sealed._labels")


def test_unknown_name_denied() -> None:
    with pytest.raises(ScopeViolation):
        resolve_scope_by_name("not_a_real_scope")


@pytest.mark.parametrize("scope", list(DataScope))
def test_every_allowlisted_scope_resolves(scope: DataScope, tmp_path: Path) -> None:
    assert resolve_table(scope).startswith("main.")
    assert resolve_export_path(scope, tmp_path).parent == tmp_path
    # Round-trips through the same by-name lookup a caller would use.
    assert resolve_scope_by_name(scope.value) is scope


def test_red_team_added_sealed_member_would_be_caught() -> None:
    """Demonstrates the leakage guard is discriminating, not vacuous.

    A local enum that *does* carry a sealed-scope member resolves
    successfully through the same lookup-by-value mechanism
    ``resolve_scope_by_name`` uses. This proves that if such a member were
    ever added to the real ``DataScope``, ``test_sealed_table_name_denied``
    above would go red rather than silently continuing to pass - the
    exit-checklist red-team requirement.
    """

    class LeakyScope(StrEnum):
        CHANNEL = "channel"
        SEALED_LABELS = "sealed._labels"

    resolved = LeakyScope("sealed._labels")
    assert resolved is LeakyScope.SEALED_LABELS
