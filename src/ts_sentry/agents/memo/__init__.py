# SPDX-License-Identifier: MIT
"""The memo agent (ARCHITECTURE 4.3, ceiling RECOMMEND).

Thin by design, as ``agents/`` is throughout: the memo *structure* lives here
and every judgment about a memo lives orchestrator-side. See
:mod:`ts_sentry.agents.memo.memo` for the structure and
:mod:`ts_sentry.orchestrator.memo_gate` for the checker.
"""
