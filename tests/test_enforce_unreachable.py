# SPDX-License-Identifier: MIT
"""STEP-02 3.5 / exit checklist: ENFORCE unreachability at type level.

Second of the two complementary mechanisms guarding the invariant (the first
is the in-place ``# type: ignore`` plus ``warn_unused_ignores`` in the CI
mypy step; see ``tests/typing/enforce_negative.py``).

Because that ignore comment *suppresses* the error, running mypy on the
fixture as-is proves nothing. This test therefore strips the comment into a
temporary copy and asserts mypy reports the error there, which is what
catches the fixture being deleted, gutted, or made vacuous.
"""

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = Path(__file__).parent / "typing" / "enforce_negative.py"

# Must match the fixture byte for byte. If it drifts, the strip below becomes
# a no-op and this test fails loudly rather than silently stopping to mean
# anything.
_IGNORE_COMMENT = "  # type: ignore[arg-type]"
_FORBIDDEN_ARGUMENT = "consequence_ceiling=Consequence.ENFORCE,"


def _forbidden_line_number(source: str) -> int:
    """1-indexed line of the forbidden construction, located rather than
    hardcoded, so editing the fixture's prose cannot silently misaim the
    assertion below."""
    for index, line in enumerate(source.splitlines(), start=1):
        if _FORBIDDEN_ARGUMENT in line:
            return index
    raise AssertionError(f"fixture no longer contains {_FORBIDDEN_ARGUMENT!r}")


def test_fixture_exists_and_is_not_collected_by_pytest() -> None:
    """STEP-02 3.5 requires the compile-check file be excluded from runtime."""
    assert _FIXTURE.is_file()
    assert not _FIXTURE.name.startswith("test_")


def test_fixture_still_carries_the_suppression_tripwire() -> None:
    """Integrity check on mechanism 1.

    The in-place ignore is what makes ``warn_unused_ignores`` redden CI if
    ENFORCE construction ever becomes legal. Remove the comment and that
    tripwire is gone, so its presence is asserted here.
    """
    assert _IGNORE_COMMENT in _FIXTURE.read_text(encoding="utf-8")


def test_constructing_a_mandate_with_enforce_is_a_mypy_error(tmp_path: Path) -> None:
    source = _FIXTURE.read_text(encoding="utf-8")
    stripped = source.replace(_IGNORE_COMMENT, "")
    assert stripped != source, "ignore comment not found; the strip was a no-op"

    # Stripping a trailing comment does not change line numbering, so the
    # fixture's own line number is the one mypy will report.
    expected_line = _forbidden_line_number(source)

    target = tmp_path / "enforce_negative_stripped.py"
    target.write_text(stripped, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(target)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0, f"mypy accepted an ENFORCE ceiling:\n{result.stdout}"
    assert f"{target.name}:{expected_line}: error:" in result.stdout, result.stdout
    assert "[arg-type]" in result.stdout, result.stdout
    assert "Consequence.ENFORCE" in result.stdout, result.stdout


def test_the_negative_fixture_would_fail_at_runtime_too(tmp_path: Path) -> None:
    """Belt and braces: the same construction the type checker rejects also
    raises at runtime, so the invariant does not depend on anyone running
    mypy.
    """
    from ts_sentry.governance.mandate import EnforceUnreachable

    sys.path.insert(0, str(_FIXTURE.parent))
    try:
        import enforce_negative
    finally:
        sys.path.pop(0)

    with pytest.raises(EnforceUnreachable):
        enforce_negative._enforce_ceiling_must_not_typecheck()
