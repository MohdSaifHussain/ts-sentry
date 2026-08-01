# SPDX-License-Identifier: MIT
"""D3: the ``RUN_PROMPT_EVAL`` handler, the last entry in the tool table.

STEP-03's no-orphan reading promised "entry per ID now, handler per ID by its
own phase", and ``tools.py`` states that "by STEP-06 the table is fully
executable". This module discharges that: with it, ``pending_handlers`` is
empty for the first time since the table was written.

The handler does not call a model
---------------------------------
It grades. Predictions are already in hand by the time it runs, and this
handler turns them into an ``EvalReport``.

That split is DECISIONS 3.10, kept: "the model call sits after the tool, not
inside it... a tool that could prompt would be an agent wearing an allowlist
entry". The prompt-eval turn makes the calls; the tool is the deterministic
step, so a report is reproducible from the predictions alone.

Predictions arrive as a resource, never as a parameter
-------------------------------------------------------
``ToolResources``, exactly as the evidence pack does (DECISIONS 4.6). An agent
that could supply the predictions could supply ones it invented, and the whole
report would then be grading the agent's account of its own answers. The
orchestrator collected them from the adapter, so the orchestrator lends them.

The same argument covers the label store: an agent that could supply the answers
would be marking its own paper.
"""

from ts_sentry.data.eval_set import EvalSetError
from ts_sentry.orchestrator.eval_labels import EvalLabelStore
from ts_sentry.orchestrator.prompt_eval import EvalReport, build_report
from ts_sentry.orchestrator.toolspec import ToolContext, ToolViolation

__all__ = [
    "CANDIDATE_DIGEST_PARAM",
    "INCUMBENT_DIGEST_PARAM",
    "TASK_PARAM",
    "run_prompt_eval",
]

TASK_PARAM = "task"
INCUMBENT_DIGEST_PARAM = "incumbent_digest"
CANDIDATE_DIGEST_PARAM = "candidate_digest"


def run_prompt_eval(context: ToolContext, /) -> object:
    """Grade the collected predictions and return an ``EvalReport``.

    The params an agent supplies are the three *names* of what is being
    compared: the task and the two content digests. They are labels on the
    report rather than inputs to the grading, which is why an agent may name
    them at all. Everything the arithmetic depends on comes from resources.
    """
    resources = context.resources
    store = resources.eval_labels
    if not isinstance(store, EvalLabelStore):
        raise ToolViolation(
            "this tool needs the eval label store, which the orchestrator supplies through "
            "ToolResources; an agent cannot provide one, because an agent that supplied the "
            "answers would be marking its own paper"
        )

    predictions = resources.eval_predictions
    if not isinstance(predictions, tuple) or len(predictions) != 2:
        raise ToolViolation(
            "this tool needs the (incumbent, candidate) prediction pair from ToolResources"
        )
    incumbent, candidate = predictions
    if not isinstance(incumbent, tuple) or not isinstance(candidate, tuple):
        raise ToolViolation("eval predictions must arrive as two tuples of proposals")

    unparseable = resources.eval_unparseable
    if not isinstance(unparseable, tuple) or len(unparseable) != 2:
        raise ToolViolation("this tool needs the (incumbent, candidate) unparseable counts")

    try:
        return build_report(
            store,
            task=str(context.params[TASK_PARAM]),
            incumbent_digest=str(context.params[INCUMBENT_DIGEST_PARAM]),
            candidate_digest=str(context.params[CANDIDATE_DIGEST_PARAM]),
            incumbent_predictions=incumbent,
            candidate_predictions=candidate,
            incumbent_unparseable=int(unparseable[0]),
            candidate_unparseable=int(unparseable[1]),
            item_count=len(incumbent),
            items_sha256=str(resources.eval_items_sha256),
            adapter_id=str(resources.eval_adapter_id),
            model_id=str(resources.eval_model_id),
            bootstrap_seed=resources.seed,
        )
    except KeyError as exc:
        raise ToolViolation(f"prompt-eval proposal is missing parameter {exc}") from exc
    except EvalSetError as exc:
        raise ToolViolation(f"the predictions do not match the eval set: {exc}") from exc


def report_or_none(result: object) -> EvalReport | None:
    """Narrow a dispatch result to a report, or nothing.

    A helper rather than an ``isinstance`` at three call sites, and it returns
    ``None`` rather than raising because a dispatch that did not execute is an
    ordinary governed outcome here, not an error.
    """
    return result if isinstance(result, EvalReport) else None
