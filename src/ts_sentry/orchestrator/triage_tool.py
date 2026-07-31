# SPDX-License-Identifier: MIT
"""D5: the ``rank_triage_queue`` handler, the first executable tool.

Orchestrator-side rather than agent-side, deliberately. The agent owns the
scorer and the prompts; the orchestrator owns the database connection and the
act of running anything. Putting the handler here keeps ``agents.triage``
importable without a database and keeps the tool table's only dependency
pointing inward.

The handler is deterministic and makes no model call. A tool that could prompt
would be an agent wearing an allowlist entry, and the ranking has to be
reproducible from the dataset alone: that is what lets a published queue be
re-derived from a manifest months later. Rationales are attached afterwards,
by the turn (``orchestrator.triage_turn``), and are separately verified and
separately ledgered.
"""

import numpy as np

from ts_sentry.agents.triage.prompts import RankedQueue, RankedRow
from ts_sentry.agents.triage.scorer import WEIGHTS_VERSION, score
from ts_sentry.orchestrator.detection_stub import DETECTOR_VERSION, build_flagged_queue
from ts_sentry.orchestrator.toolspec import ToolContext

__all__ = ["DEFAULT_QUEUE_LIMIT", "rank_triage_queue"]

DEFAULT_QUEUE_LIMIT = 25


def rank_triage_queue(context: ToolContext, /) -> object:
    """Build the flagged queue, score it, and return it ranked.

    ``limit`` may come from the agent through ``params`` and is clamped rather
    than trusted: an agent asking for a million rows gets the ceiling, not an
    argument. The connection comes from the orchestrator's resources, so the
    agent cannot name a file to open.
    """
    connection = context.require_connection()
    raw_limit = context.params.get("limit", DEFAULT_QUEUE_LIMIT)
    limit = raw_limit if isinstance(raw_limit, int) and raw_limit > 0 else DEFAULT_QUEUE_LIMIT
    limit = min(limit, DEFAULT_QUEUE_LIMIT)

    rng = np.random.default_rng(context.resources.seed)
    flagged = build_flagged_queue(connection, rng=rng, limit=limit)

    subjects = {entity.case_id: entity.channel_id for entity in flagged}
    scored = [
        score(
            entity.case_id,
            severity_class=entity.severity_class,
            spread=entity.spread,
            velocity=entity.velocity,
            recidivism=entity.recidivism,
        )
        for entity in flagged
    ]
    # Sorted by priority here rather than relying on the detector's severity
    # order: the scorer weighs four components, so its ranking can legitimately
    # differ from the order the flags arrived in.
    scored.sort(key=lambda item: (-item.priority, item.case_id))

    return RankedQueue(
        rows=tuple(
            RankedRow(score=item, subject_id=subjects[item.case_id], rationale=None)
            for item in scored
        ),
        weights_version=WEIGHTS_VERSION,
        detector_version=DETECTOR_VERSION,
    )
