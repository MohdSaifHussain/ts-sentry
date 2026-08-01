# SPDX-License-Identifier: MIT
"""D5: ground-truth network recovery at a pivot budget (STEP-04 D5).

STEP-04's exit criterion: "ground-truth recovery metric reportable". This is
that metric, and it lives here rather than beside the evidence agent for a
governance reason rather than a filing one. It needs ``sealed._labels``, which
no agent mandate can reach and no orchestrator module may import. Computing it
next to the agent would have put ground truth one import away from the thing
being measured.

The definition, stated so it cannot be read wider than it is
------------------------------------------------------------
For a case whose seed subject carries a sealed ``ring_id``:

    recovery@k = |pack nodes after k pivots that are members of that ring|
                 / |ring members, excluding the seed|

The seed is excluded from the denominator because recovering the entity you
started from measures nothing: it was in the pack before the agent did
anything. Cases whose seed carries no ring are reported as a separate count and
never folded in as zeros, because "this investigation had no network to find"
and "this investigation failed to find the network" are different results and
averaging them together would understate the second.

The structural ceiling, reported alongside
-------------------------------------------
A ring contains entities of kinds a pack cannot hold. Comments in particular
enter a pack as timeline events, not as nodes, so a ring whose members are
mostly comments has a recovery ceiling well below 1.0 no matter how well the
agent performs. ``reachable_size`` is that ceiling, and every result carries
it.

This is the difference between a metric and a score. Reporting recovery@20 =
0.35 without saying that 0.42 was the most the pack could structurally hold
would invite the reader to conclude the agent missed most of the network, when
in fact it found most of what it could reach. Both numbers are reported and
``recovered_fraction_of_reachable`` is the one to read for agent performance.

What this does not measure
--------------------------
Precision. A pack that pulled in every account on the platform would score 1.0
recovery and be useless. Recovery at a *budget* is the counterweight the STEP
file chose: an investigation that has to find the network inside five pivots
cannot get there by dragging everything in. Precision against ground truth is
a STEP-07 concern and is not claimed here.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import duckdb

from ts_sentry.agents.evidence.pack import EvidencePack
from ts_sentry.data.enums import EntityKind, ThreatClass

__all__ = [
    "PACK_NODE_KINDS",
    "RecoveryResult",
    "RecoveryTable",
    "RingMembership",
    "read_ring_membership",
    "recovery_for_pack",
    "recovery_table",
]

PACK_NODE_KINDS = frozenset({EntityKind.ACCOUNT, EntityKind.CHANNEL, EntityKind.VIDEO})
"""Entity kinds an evidence pack can hold as nodes.

Comments are deliberately absent. A pivot that surfaces a comment records it as
a timeline event against the account that posted it, because a comment is an
observation rather than an actor in the entity graph. That is a design choice
with a measurable consequence, which is exactly why the ceiling it imposes is
reported rather than left for a reader to discover.
"""

_RING_QUERY = """
SELECT entity_kind, entity_id, threat_class, ring_id
FROM sealed._labels
WHERE ring_id IS NOT NULL
"""
"""Static SQL against the sealed table.

No ``DataScope`` resolution here, and that is the point rather than an
oversight: ``DataScope`` has no member that resolves to anything under
``sealed``, by construction, so an allowlisted path to this table does not and
must not exist. Measurement reads it directly because measurement is the
legitimate consumer, and the import-graph test is what keeps that from becoming
a door anyone else can walk through.
"""


@dataclass(frozen=True, slots=True)
class RingMembership:
    """Sealed ground truth about planted networks, keyed for lookup.

    Read once per measurement run and passed around, so a report cannot
    accidentally hold a connection to the sealed table open across code that
    has no business with it.
    """

    ring_of: Mapping[str, str]
    threat_of: Mapping[str, ThreatClass]
    members: Mapping[str, frozenset[str]]
    kinds: Mapping[str, EntityKind]

    def ring_for(self, entity_id: str) -> str | None:
        return self.ring_of.get(entity_id)

    def threat_for(self, ring_id: str) -> ThreatClass | None:
        return self.threat_of.get(ring_id)


def read_ring_membership(connection: duckdb.DuckDBPyConnection) -> RingMembership:
    """Load planted ring membership from ``sealed._labels``.

    Only rows carrying a ``ring_id`` are read: a benign entity has a label but
    no network, and including it would put every unaffiliated account into a
    denominator it does not belong in.
    """
    ring_of: dict[str, str] = {}
    threat_of: dict[str, ThreatClass] = {}
    members: dict[str, set[str]] = {}
    kinds: dict[str, EntityKind] = {}

    for entity_kind, entity_id, threat_class, ring_id in connection.execute(_RING_QUERY).fetchall():
        entity = str(entity_id)
        ring = str(ring_id)
        ring_of[entity] = ring
        kinds[entity] = EntityKind(str(entity_kind))
        threat_of[ring] = ThreatClass(str(threat_class))
        members.setdefault(ring, set()).add(entity)

    return RingMembership(
        ring_of=ring_of,
        threat_of=threat_of,
        members={ring: frozenset(entities) for ring, entities in members.items()},
        kinds=kinds,
    )


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    """Recovery for one investigation, at each requested budget.

    ``ring_size`` and ``reachable_size`` both exclude the seed, so every
    fraction below is over the same population and none of them is quietly
    measuring a different denominator from its neighbour.
    """

    case_id: str
    subject_id: str
    ring_id: str
    threat_class: ThreatClass
    ring_size: int
    reachable_size: int
    recovered: Mapping[int, int]

    def fraction_of_ring(self, budget: int) -> float:
        """Recovered members over the whole ring. Bounded above by the ceiling."""
        if self.ring_size == 0:
            return 0.0
        return self.recovered[budget] / self.ring_size

    def fraction_of_reachable(self, budget: int) -> float:
        """Recovered members over what a pack could structurally hold.

        The number to read for agent performance. A ring of mostly comments has
        a low ``fraction_of_ring`` ceiling that says nothing about how well the
        investigation went.
        """
        if self.reachable_size == 0:
            return 0.0
        return self.recovered[budget] / self.reachable_size

    def to_json_object(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "subject_id": self.subject_id,
            "ring_id": self.ring_id,
            "threat_class": self.threat_class.value,
            "ring_size": self.ring_size,
            "reachable_size": self.reachable_size,
            "recovered": {str(budget): count for budget, count in sorted(self.recovered.items())},
            "fraction_of_ring": {
                str(budget): round(self.fraction_of_ring(budget), 4)
                for budget in sorted(self.recovered)
            },
            "fraction_of_reachable": {
                str(budget): round(self.fraction_of_reachable(budget), 4)
                for budget in sorted(self.recovered)
            },
        }


def recovery_for_pack(
    pack: EvidencePack,
    membership: RingMembership,
    budgets: Sequence[int],
) -> RecoveryResult | None:
    """Recovery for one pack, or ``None`` when its subject has no planted ring.

    ``None`` rather than a zero-filled result, deliberately. A benign subject is
    an investigation with nothing to find, and recording it as a zero would drag
    every average down with cases that were never winnable. The caller counts
    them separately.
    """
    ring_id = membership.ring_for(pack.subject_id)
    if ring_id is None:
        return None

    threat = membership.threat_for(ring_id)
    if threat is None:  # pragma: no cover - a ring always carries its threat class
        return None

    others = membership.members[ring_id] - {pack.subject_id}
    reachable = {entity for entity in others if membership.kinds.get(entity) in PACK_NODE_KINDS}

    return RecoveryResult(
        case_id=pack.case_id,
        subject_id=pack.subject_id,
        ring_id=ring_id,
        threat_class=threat,
        ring_size=len(others),
        reachable_size=len(reachable),
        recovered={budget: len(pack.nodes_at_hop(budget) & others) for budget in budgets},
    )


@dataclass(frozen=True, slots=True)
class RecoveryTable:
    """Recovery aggregated per threat class, the STEP-04 3.5 reporting shape.

    Carries ``cases_without_a_ring`` rather than dropping it. A table reporting
    only the winnable cases would flatter the result by hiding how many
    investigations had no network to find, and STEP-07's report format needs
    that number to describe the sample honestly.
    """

    budgets: tuple[int, ...]
    per_class: Mapping[ThreatClass, tuple[RecoveryResult, ...]]
    cases_without_a_ring: int

    @property
    def case_count(self) -> int:
        return sum(len(results) for results in self.per_class.values())

    def mean_fraction_of_ring(self, threat: ThreatClass, budget: int) -> float:
        results = self.per_class.get(threat, ())
        if not results:
            return 0.0
        return sum(result.fraction_of_ring(budget) for result in results) / len(results)

    def mean_fraction_of_reachable(self, threat: ThreatClass, budget: int) -> float:
        results = self.per_class.get(threat, ())
        if not results:
            return 0.0
        return sum(result.fraction_of_reachable(budget) for result in results) / len(results)

    def render(self) -> str:
        """A fixed-width table, for the STEP-04 Outcome and the STEP-07 report.

        Both fractions are shown per cell, because either alone misleads:
        recovery over the ring understates a pack that found everything it could
        reach, and recovery over the reachable set overstates coverage of the
        actual network.
        """
        header = "threat class                    cases  " + "  ".join(
            f"@{budget:<12}" for budget in self.budgets
        )
        lines = [header, "-" * len(header)]
        for threat in sorted(self.per_class, key=lambda item: item.value):
            results = self.per_class[threat]
            cells = "  ".join(
                f"{self.mean_fraction_of_ring(threat, budget):.2f}/"
                f"{self.mean_fraction_of_reachable(threat, budget):.2f}    "
                for budget in self.budgets
            )
            lines.append(f"{threat.value:<32}{len(results):<5}  {cells}")
        lines.append("")
        lines.append("cells are mean recovery of the ring / of what a pack can structurally hold")
        lines.append(f"cases whose subject carried no planted ring: {self.cases_without_a_ring}")
        return "\n".join(lines)

    def to_json_object(self) -> dict[str, object]:
        return {
            "budgets": list(self.budgets),
            "case_count": self.case_count,
            "cases_without_a_ring": self.cases_without_a_ring,
            "per_threat_class": {
                threat.value: {
                    "cases": len(results),
                    "mean_fraction_of_ring": {
                        str(budget): round(self.mean_fraction_of_ring(threat, budget), 4)
                        for budget in self.budgets
                    },
                    "mean_fraction_of_reachable": {
                        str(budget): round(self.mean_fraction_of_reachable(threat, budget), 4)
                        for budget in self.budgets
                    },
                    "results": [result.to_json_object() for result in results],
                }
                for threat, results in sorted(
                    self.per_class.items(), key=lambda item: item[0].value
                )
            },
        }


def recovery_table(
    packs: Sequence[EvidencePack],
    membership: RingMembership,
    budgets: Sequence[int] = (5, 10, 20),
) -> RecoveryTable:
    """Aggregate recovery across investigations, grouped by threat class.

    Budgets default to STEP-04 3.5's 5, 10 and 20.
    """
    if not budgets:
        raise ValueError("a recovery table reports at least one budget")

    per_class: dict[ThreatClass, list[RecoveryResult]] = {}
    without_ring = 0

    for pack in packs:
        result = recovery_for_pack(pack, membership, budgets)
        if result is None:
            without_ring += 1
            continue
        per_class.setdefault(result.threat_class, []).append(result)

    return RecoveryTable(
        budgets=tuple(budgets),
        per_class={threat: tuple(results) for threat, results in per_class.items()},
        cases_without_a_ring=without_ring,
    )
