# SPDX-License-Identifier: MIT
"""Sealed ground-truth schema.

Physically separated from ``ts_sentry.data.schema`` on purpose: this module
defines the one artifact (``SealedLabel`` / the ``sealed._labels`` table and
its Parquet export) that must never be reachable from agent or orchestrator
code. It has exactly two legitimate consumers: the build pipeline (write, and
a build-time read for the D6 AnalystKit reconcile gate) and, from STEP-07
onward, measurement code. Access is denied by construction: ``DataScope``
(``ts_sentry.governance.scopes``) has no member that resolves to anything
under this module's schema or export path.

D4 (writer, DuckDB persistence, Parquet export under a segregated
``sealed/`` directory) lands after the Phase-1 review stop.
"""

from dataclasses import dataclass
from datetime import datetime

from ts_sentry.data.enums import EntityKind, ThreatClass
from ts_sentry.data.tz import require_ist


@dataclass(frozen=True, slots=True)
class SealedLabel:
    """One ground-truth label row. Every generated entity gets exactly one,
    including benign entities (``ThreatClass.BENIGN``) - this is what makes
    the label-completeness hypothesis property (STEP-01 3.5) checkable.
    """

    entity_kind: EntityKind
    entity_id: str
    threat_class: ThreatClass
    ring_id: str | None
    planted_ts: datetime
    generator_params_hash: str

    def __post_init__(self) -> None:
        require_ist(self.planted_ts, "planted_ts")
