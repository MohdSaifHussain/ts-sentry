# SPDX-License-Identifier: MIT
"""D1: seeding the registry with the fleet's four v1 prompts.

Build-time, like ``data.policy_fetch``: it runs once to produce a committed
artifact, and nothing at session time calls it. The import direction is
deliberate and one-way. This module reads the agents' prompt texts; no agent
module imports the registry (see the package docstring for why that is decision
C rather than an accident).

Why the texts still live in the agent modules
---------------------------------------------
Decision C is **record-only**: the three shipped prompts enter the registry with
their digests unchanged and with zero behaviour effect. Moving the text out of
``agents/*/prompts.py`` and loading it from disk at import would be a behaviour
change, and a large one: it would make three agents depend on a directory being
present and readable, which is a new failure mode for a path that has none
today.

So the module constant remains the runtime source, and the registry holds the
same bytes. The duplication is real and is not left to discipline:
``tests/test_prompt_registry.py`` asserts that every registered version's text,
``prompt_id`` and both digests match the constant it was seeded from, so editing
one without the other reddens the suite. That is the same shape STEP-02 used for
the ledger's two timestamp columns, which are also duplicated and also asserted
never to drift.

The fourth prompt is different, and worth naming
------------------------------------------------
``classify.threat_class.v1`` has no session consumer at all (decision A), so for
that task the registry is the only record there is. It is the one prompt here
whose registry entry is authoritative rather than corroborating.
"""

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from ts_sentry.agents.evidence.prompts import EVIDENCE_SYSTEM_PROMPT
from ts_sentry.agents.memo.prompts import MEMO_SYSTEM_PROMPT
from ts_sentry.agents.prompt_eval.prompts import CLASSIFY_SYSTEM_PROMPT
from ts_sentry.agents.triage.prompts import TRIAGE_SYSTEM_PROMPT
from ts_sentry.orchestrator.firewall import SystemPrompt
from ts_sentry.prompt_registry.registry import MANIFEST_NAME, PromptRegistryError, PromptTask
from ts_sentry.prompt_registry.store import PromptRegistry, write_registry

__all__ = [
    "SEED_PROMPTS",
    "SEED_REASON",
    "seed_registry",
    "write_seed",
]

SEED_PROMPTS: Mapping[PromptTask, SystemPrompt] = {
    PromptTask.TRIAGE_RATIONALE: TRIAGE_SYSTEM_PROMPT,
    PromptTask.EVIDENCE_PIVOT: EVIDENCE_SYSTEM_PROMPT,
    PromptTask.MEMO_STATEMENT: MEMO_SYSTEM_PROMPT,
    PromptTask.CLASSIFY_THREAT_CLASS: CLASSIFY_SYSTEM_PROMPT,
}
"""Every task's v1, taken from the module constant that already defines it.

Keyed by task so a task with no seed is a missing key rather than a silently
short registry, and so the ``PromptTask`` enum and this mapping have to stay in
step: a task added without a seed fails the completeness check below.
"""

SEED_REASON = (
    "STEP-06 D1: initial activation of the fleet's v1 prompts. The three shipped "
    "prompts are recorded at the bytes they have run under since their own phase; "
    "classify.threat_class.v1 is new in this phase and has no session consumer."
)


def _require_v1(prompt: SystemPrompt, task: PromptTask) -> None:
    """The seed's own precondition: every seeded prompt is its task's ``v1``.

    Checked rather than assumed, because the whole seed rests on it. A constant
    whose ``prompt_id`` had drifted to ``.v2`` would be registered here as the
    lineage root of a task whose real root was never recorded, and the parent
    chain would be wrong from its first link with nothing to notice.
    """
    expected = f"{task.value}.v1"
    if prompt.prompt_id != expected:
        raise PromptRegistryError(
            f"seed for {task.value} carries prompt_id {prompt.prompt_id!r}; the registry "
            f"seed registers each task's first version and expects {expected!r}"
        )


def seed_registry(*, created_ist: datetime) -> PromptRegistry:
    """Build the v1 registry, with every task activated.

    ``created_ist`` is supplied by the caller rather than read from the clock,
    as every timestamp in this system is (STEP-03 D1). It is the one input that
    makes this function's output vary, which is why the CLI passes a value a
    reviewer can see rather than letting the function find one.

    Each version is registered with ``parent=None``: these are lineage roots,
    and a v1 claiming a parent would be claiming a history that did not happen.
    """
    missing = sorted(task.value for task in PromptTask if task not in SEED_PROMPTS)
    if missing:
        raise PromptRegistryError(
            f"no seed prompt for {missing}. Every task needs a v1, or the registry ships "
            "with a task nothing can be activated for"
        )

    registry = PromptRegistry(versions=(), texts={})
    for task in PromptTask:
        prompt = SEED_PROMPTS[task]
        _require_v1(prompt, task)
        registry = registry.registered(
            task,
            "v1",
            prompt.text,
            parent=None,
            created_ist=created_ist,
        )

    history = registry.history
    for task in PromptTask:
        history = history.activate(
            task,
            registry.versions_for(task)[0].content_digest,
            reason=SEED_REASON,
            timestamp_ist=created_ist,
        )
    return registry.with_history(history)


def write_seed(root: Path, *, created_ist: datetime) -> PromptRegistry:
    """Seed and persist in one step, refusing to seed over an existing registry.

    A registry that already has versions is not something to re-seed: the seed
    activates every task at v1, and running it against a populated registry
    would point live tasks back at their first version through a code path that
    calls itself a bootstrap. Rolling back is a deliberate, ledgered pointer
    move, and it has its own verb.
    """
    if (root / MANIFEST_NAME).exists():
        raise PromptRegistryError(
            f"{root} already holds a registry. Seeding is a one-time act; moving a pointer "
            "afterwards is an activation or a rollback, both of which are ledgered"
        )
    registry = seed_registry(created_ist=created_ist)
    write_registry(root, registry)
    return registry
