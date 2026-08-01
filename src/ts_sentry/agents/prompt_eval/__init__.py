# SPDX-License-Identifier: MIT
"""The prompt-eval agent (ARCHITECTURE 4.4, mandate ceiling OBSERVE).

Thin by design, per ARCHITECTURE 10: this package holds the classification
prompt STEP-06 evaluates, the schema its answers are parsed into, and the
proposal format the agent writes to. Everything that *judges* a prompt is
orchestrator-side.

The separation matters more here than anywhere else in the fleet, because the
thing being evaluated and the thing doing the evaluating would otherwise be the
same component. An agent that could reach the eval labels could be graded
against answers it had seen, and an agent that could reach the regression gate
would be deciding whether its own successor was good enough to ship. Both are
refused structurally: :mod:`ts_sentry.orchestrator.eval_labels` and the gate are
in the import-graph test's forbidden set for every module under ``agents.``.
"""
