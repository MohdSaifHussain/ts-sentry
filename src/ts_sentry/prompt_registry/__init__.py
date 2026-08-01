# SPDX-License-Identifier: MIT
"""D1: the versioned prompt registry (STEP-06 D1, ARCHITECTURE 4.4).

Three modules, split by what they are responsible for:

* :mod:`ts_sentry.prompt_registry.registry` - what a prompt version *is*: its
  two digests, its task binding, its lineage.
* :mod:`ts_sentry.prompt_registry.activation` - the append-only pointer log,
  which is where STEP-06 3.4's "activation swaps a pointer, prior versions
  retained forever" is made structural.
* :mod:`ts_sentry.prompt_registry.store` - the aggregate and its on-disk form,
  including a load path that re-derives every digest rather than believing the
  manifest.

Who imports this, stated accurately
-----------------------------------
Build-time tooling and the orchestrator. **No agent module imports it today**,
and saying otherwise would be the overclaim this project refuses everywhere
else. Under decision C the three shipped prompts still run from their module
constants; what the registry adds is that it holds the same bytes, and a test
asserts the two agree digest for digest, so neither can drift from the other
without the suite reddening.

The package is nonetheless written so that an agent *could* hold it: it reaches
nothing in the ledger, the gates, dispatch, or the signature path. That is not
decoration. Wiring the turns to load their prompts from here is a behaviour
change, which decision C deliberately did not authorise this phase, and the
phase that does authorise it should find the governance argument already
settled rather than have to reopen it.

Activation is a pure state transition in this package; persisting it and
ledgering it is the orchestrator's, on the split ``mandate.validate``
established in STEP-02.
"""
