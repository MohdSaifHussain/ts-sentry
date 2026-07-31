# SPDX-License-Identifier: MIT
"""Test-suite guarantees that must hold before any test runs.

STEP-03 3.4 makes the deterministic stub adapter the CI path and requires the
whole suite to pass fully offline. That is a property of the *environment* as
much as of the code, and the code alone cannot enforce it: a developer who has
``TS_SENTRY_LLM_MODE=live`` and ``ANTHROPIC_API_KEY`` exported in their shell
would otherwise be one careless default away from a paid API call during an
ordinary ``pytest`` run.

So the variables are removed for the entire session, autouse and
session-scoped. A test that wants to exercise the gate sets them back through
``monkeypatch``, which restores them per test. The effect is that "the suite
costs nothing" stops depending on how anyone's shell is configured.
"""

from collections.abc import Iterator

import pytest

LIVE_MODE_ENV_VARS = ("TS_SENTRY_LLM_MODE", "TS_SENTRY_LLM_MODEL", "ANTHROPIC_API_KEY")


@pytest.fixture(autouse=True, scope="session")
def _offline_by_construction() -> Iterator[None]:
    """Strip every live-mode variable for the whole test session.

    Session-scoped and autouse so it cannot be opted out of by forgetting a
    fixture, and so it covers collection-time work as well as test bodies.
    """
    monkeypatch = pytest.MonkeyPatch()
    for name in LIVE_MODE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield
    monkeypatch.undo()
