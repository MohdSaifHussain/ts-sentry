# SPDX-License-Identifier: MIT
"""The deterministic control layer: state machine, firewall, dispatch, gates.

Not an agent. No model call originates here except on behalf of a mandated
agent, through the D4 adapter boundary (ARCHITECTURE 5).

This package holds the ``OrchestratorToken`` that authorizes ledger writes.
That capability stays inside ``ts_sentry.orchestrator``: agents propose,
deterministic tools dispose, and the orchestrator is the only executor, which
is what makes the kill path trivial (halting one process halts the fleet).
"""
