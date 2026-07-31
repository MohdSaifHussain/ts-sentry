# SPDX-License-Identifier: MIT
"""The fleet: four narrow agents, deliberately thin (ARCHITECTURE 4).

An agent here is prompts, schemas, and a deterministic core. It is not a
process, it holds no capability, and it decides nothing about whether its own
proposals may run.

What no module under this package may import, and why
-----------------------------------------------------
* ``governance.signature`` - the human-only ENFORCE construction path. An
  agent that could import it could name the one function that produces
  ENFORCE for use. The invariant does not depend on that being impossible
  (``validate`` refuses ENFORCE under every mandate, before any other check),
  but keeping the module out of agents' reach means the guarantee has two
  independent supports rather than one.
* ``governance.ledger`` - the chain and its write capability. Agents propose;
  the orchestrator records. An agent that could append to the ledger could
  write its own account of what it did.
* ``governance.gates`` and ``orchestrator.dispatch`` - the machinery that
  judges agent output. An agent that imports its own judge is not being
  judged.

``tests/test_import_graph.py`` enforces all of this over the transitive
first-party import closure, so reaching a forbidden module through an
innocent-looking one fails too.
"""
