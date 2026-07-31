# SPDX-License-Identifier: MIT
"""D3: the Evidence Pack, and the provenance every record in it carries.

ARCHITECTURE 4.2 names the agent's output as "an Evidence Pack: entity graph,
timeline, per-record provenance (source table, query hash, retrieval
timestamp)". STEP-04 D3 fixes the field set and names the standard: provenance
completeness (NIST AI 600-1 information integrity), with W3C PROV-inspired
fields and a documented mapping.

Where this lives, and why that is not filing
--------------------------------------------
An agent's output schema lives under ``agents/`` (ARCHITECTURE 10, "agents/ ...
thin: prompts + schemas"), exactly as ``RankedQueue`` does, and the orchestrator
imports it. What deliberately does *not* live here is the checker that judges a
pack: that is ``orchestrator.pack_gate``, because a module reaching
``governance.gates`` from inside ``agents/`` is an agent holding its own
verifier, which the STEP-03 import-graph test caught once already and which was
fixed by moving the judgment rather than widening the rule.

Provenance completeness, stated as an invariant rather than an aspiration
------------------------------------------------------------------------
Every node, every edge, and every timeline entry names a ``provenance_id`` that
resolves to a record in this pack. It is checked in ``__post_init__``, because a
docstring-only invariant is not an invariant. There is no way to construct a
pack containing a record nobody can trace, which is the whole content of the
phrase "no orphan records" in the STEP-04 D4 assembly gate.

A pivot that returned **zero rows keeps its provenance record**. Nothing points
at it, and that is correct: it is the record that a question was asked and
answered in the negative. Dropping it would let a pack silently forget an
investigative step, and an analyst reading the pack later could not tell the
difference between a pivot that found nothing and a pivot nobody ran.

W3C PROV mapping (documented, not implemented)
----------------------------------------------
STEP-04 D3 asks for PROV-inspired fields with a documented mapping. This is
that mapping. It is not an implementation of PROV-DM, produces no PROV
serialization, and claims no conformance. Definitions quoted from
https://www.w3.org/TR/prov-dm/ :

============================ ======================================================
This model                   PROV-DM
============================ ======================================================
``EvidenceNode`` /           ``Entity``: "a physical, digital, conceptual, or
``EvidenceEdge`` /           other kind of thing with some fixed aspects".
``TimelineEvent``
``Provenance``               ``Activity``: "something that occurs over a period of
                             time and acts upon or with entities". One record is
                             one pivot execution.
the evidence agent under     ``Agent``: "something that bears some form of
its mandate                  responsibility for an activity taking place".
``record.provenance_id``     ``wasGeneratedBy``: "the completion of production of
                             a new entity by an activity".
``Provenance.source_table``  ``used``: "the beginning of utilizing an entity by an
                             activity".
``Provenance.hop_index``     ``wasAssociatedWith``: "an assignment of
plus the session's mandate   responsibility to an agent for an activity". The
binding                      responsible agent is named in the ledger, not here.
============================ ======================================================

The last row is the one worth being precise about. This model does not record
*who* ran a pivot, because the pack is an artifact and the ledger is the record
of agency: the ``TOOL_CALLED`` and ``HUMAN_DECISION`` entries for a hop carry
the agent id, the mandate hash, and the analyst decision. Duplicating agency
into the pack would create a second, unchained account of who did what.

Immutability
------------
The pack is frozen, and ``with_hop`` returns a new one. A hop cannot mutate a
pack that has already passed the assembly gate, so "this artifact was accepted"
stays a statement about a specific set of bytes.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ts_sentry.data.enums import EntityKind
from ts_sentry.data.tz import require_ist
from ts_sentry.governance.canonical import digest_fields, require_sha256_hex
from ts_sentry.orchestrator.pivots import PivotKind

__all__ = [
    "SEED_TEMPLATE_ID",
    "EdgeRelation",
    "EvidenceEdge",
    "EvidenceNode",
    "EvidencePack",
    "PackError",
    "Provenance",
    "TimelineEvent",
    "TimelineKind",
    "seed_provenance",
]

SEED_TEMPLATE_ID = "analyst.case_selection.v1"
"""The origin recorded for the one record no pivot produced.

A pack starts with the entity the analyst chose to investigate, and that node
needs provenance like every other record. Its origin is the case selection
rather than a query, which is why ``Provenance.pivot_kind`` is nullable: ``None``
means "this did not come from a pivot", and it is the only way to say so.
"""

_SEED_DOMAIN = "ts-sentry/evidence-seed/v1"


class PackError(Exception):
    """Raised when a pack cannot be assembled from what it was given.

    Distinct from a gate rejection. A gate rejection is a governed finding about
    a well-formed artifact; this is a caller handing the constructor something
    that is not an evidence pack at all, which is a bug rather than an outcome.
    """


class EdgeRelation(StrEnum):
    """What one edge in the entity graph asserts.

    Named for the observable relation rather than for a conclusion about it.
    ``SHARES_INFRA_SIGNAL`` says two subjects carry the same signal value; it
    does not say they are the same operator, which is the analyst's call to
    make and the memo's job to argue.
    """

    OWNS_CHANNEL = "owns_channel"
    SHARES_METADATA = "shares_metadata"
    SHARES_INFRA_SIGNAL = "shares_infra_signal"
    COMMENTED_ON = "commented_on"
    ENGAGED_WITH = "engaged_with"


class TimelineKind(StrEnum):
    """What one timeline entry records happening."""

    COMMENT_POSTED = "comment_posted"
    ENGAGEMENT_OBSERVED = "engagement_observed"
    SIGNAL_OBSERVED = "signal_observed"


def _require_id(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must be a non-empty identifier")


def _require_ist_iso(value: str, field_name: str) -> None:
    """Parse and check an ISO 8601 string, rather than trusting its shape.

    The pack stores timestamps as strings because it is serialized to JSON and
    GraphML, but a string that merely looks like a timestamp is not one. Parsing
    it here and running the same ``require_ist`` the entity schemas use means a
    pack cannot carry a UTC-rendered or naive timestamp, which is the defect
    STEP-02 D3 and STEP-03 D5 both hit from the DuckDB side.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO 8601 timestamp; got {value!r}") from exc
    require_ist(parsed, field_name)


@dataclass(frozen=True, slots=True)
class Provenance:
    """One pivot execution, and everything needed to re-derive what it returned.

    STEP-04 D3's four required fields are ``source_table``,
    ``query_template_id``, ``param_hash`` and ``retrieval_ts_ist``. Three more
    are carried because they answer questions the four leave open:

    * ``template_sha256`` names the query by content, so a template edited
      without a version bump stops matching the packs that cite it. An id alone
      would let the text drift under a stable label.
    * ``hop_index`` places the record in the investigation, which is what lets
      recovery be measured *at a budget* (STEP-04 D5) rather than only at the
      end.
    * ``row_count`` makes a zero-row pivot legible as a zero-row pivot.

    ``pivot_kind`` is ``None`` for exactly one record per pack: the analyst's
    case selection, which produced the seed node and was not a query.
    """

    provenance_id: str
    hop_index: int
    pivot_kind: PivotKind | None
    source_table: str
    query_template_id: str
    template_sha256: str
    param_hash: str
    params: Mapping[str, object]
    retrieval_ts_ist: str
    row_count: int

    def __post_init__(self) -> None:
        _require_id(self.provenance_id, "provenance_id")
        _require_id(self.source_table, "source_table")
        _require_id(self.query_template_id, "query_template_id")
        if self.hop_index < 0:
            raise ValueError(f"hop_index must be non-negative; got {self.hop_index}")
        if self.row_count < 0:
            raise ValueError(f"row_count must be non-negative; got {self.row_count}")
        require_sha256_hex(self.template_sha256, "template_sha256")
        require_sha256_hex(self.param_hash, "param_hash")
        _require_ist_iso(self.retrieval_ts_ist, "retrieval_ts_ist")
        if (self.pivot_kind is None) is not (self.query_template_id == SEED_TEMPLATE_ID):
            raise ValueError(
                "exactly the case-selection record carries no pivot_kind; a pivot record names "
                "its kind and a seed record does not"
            )

    def to_json_object(self) -> dict[str, object]:
        return {
            "provenance_id": self.provenance_id,
            "hop_index": self.hop_index,
            "pivot_kind": None if self.pivot_kind is None else self.pivot_kind.value,
            "source_table": self.source_table,
            "query_template_id": self.query_template_id,
            "template_sha256": self.template_sha256,
            "param_hash": self.param_hash,
            "params": {name: self.params[name] for name in sorted(self.params)},
            "retrieval_ts_ist": self.retrieval_ts_ist,
            "row_count": self.row_count,
        }


def seed_provenance(case_id: str, subject_id: str, retrieval_ts_ist: str) -> Provenance:
    """The origin record for the entity the analyst chose to investigate.

    Its ``template_sha256`` is a digest over the selection itself rather than
    over a query, because there is no query. That is stated here and in
    ``SEED_TEMPLATE_ID`` rather than left for a reader to infer from a digest
    that does not verify against any template.
    """
    return Provenance(
        provenance_id="prov-0000",
        hop_index=0,
        pivot_kind=None,
        source_table="analyst.case_selection",
        query_template_id=SEED_TEMPLATE_ID,
        template_sha256=digest_fields(_SEED_DOMAIN, SEED_TEMPLATE_ID),
        param_hash=digest_fields(_SEED_DOMAIN, case_id, subject_id),
        params={"case_id": case_id, "subject_id": subject_id},
        retrieval_ts_ist=retrieval_ts_ist,
        row_count=1,
    )


@dataclass(frozen=True, slots=True)
class EvidenceNode:
    """One entity in the graph.

    ``attributes`` carries observable facts the pivot returned (a shared IP
    bucket, a signal value, an engagement count) as strings. It never carries
    user-authored text: no pivot template selects a free-text column, so there
    is nothing here for the input firewall to have missed.
    """

    node_id: str
    kind: EntityKind
    provenance_id: str
    attributes: Mapping[str, str]

    def __post_init__(self) -> None:
        _require_id(self.node_id, "node_id")
        _require_id(self.provenance_id, "provenance_id")

    def to_json_object(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "kind": self.kind.value,
            "provenance_id": self.provenance_id,
            "attributes": {name: self.attributes[name] for name in sorted(self.attributes)},
        }


@dataclass(frozen=True, slots=True)
class EvidenceEdge:
    """One asserted relation between two entities in the graph."""

    edge_id: str
    source_id: str
    target_id: str
    relation: EdgeRelation
    provenance_id: str
    attributes: Mapping[str, str]

    def __post_init__(self) -> None:
        _require_id(self.edge_id, "edge_id")
        _require_id(self.source_id, "source_id")
        _require_id(self.target_id, "target_id")
        _require_id(self.provenance_id, "provenance_id")
        if self.source_id == self.target_id:
            raise ValueError(
                f"edge {self.edge_id} joins {self.source_id} to itself; a self-edge asserts "
                "nothing about a network"
            )

    def to_json_object(self) -> dict[str, object]:
        return {
            "edge_id": self.edge_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation": self.relation.value,
            "provenance_id": self.provenance_id,
            "attributes": {name: self.attributes[name] for name in sorted(self.attributes)},
        }


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    """One dated observation about one node."""

    event_id: str
    kind: TimelineKind
    node_id: str
    ts_ist: str
    provenance_id: str

    def __post_init__(self) -> None:
        _require_id(self.event_id, "event_id")
        _require_id(self.node_id, "node_id")
        _require_id(self.provenance_id, "provenance_id")
        _require_ist_iso(self.ts_ist, "ts_ist")

    def to_json_object(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "kind": self.kind.value,
            "node_id": self.node_id,
            "ts_ist": self.ts_ist,
            "provenance_id": self.provenance_id,
        }


@dataclass(frozen=True, slots=True)
class EvidencePack:
    """The evidence agent's output contract (ARCHITECTURE 4.2).

    This is the type the evidence ``Mandate`` declares as its ``output_schema``,
    so dispatch's schema check is a check against *this* class, and the ASSEMBLE
    gate validates this object after every hop.

    Every invariant below is enforced here rather than only in the gate, and the
    duplication is deliberate. The gate is a governed judgment that produces a
    ledgered rejection; these are structural facts about the type. A pack that
    violated one would not be a rejected pack, it would be a malformed object
    that a rejection could not describe.
    """

    case_id: str
    subject_id: str
    nodes: tuple[EvidenceNode, ...]
    edges: tuple[EvidenceEdge, ...]
    timeline: tuple[TimelineEvent, ...]
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        _require_id(self.case_id, "case_id")
        _require_id(self.subject_id, "subject_id")

        _reject_duplicates([node.node_id for node in self.nodes], "node_id")
        _reject_duplicates([edge.edge_id for edge in self.edges], "edge_id")
        _reject_duplicates([event.event_id for event in self.timeline], "event_id")
        _reject_duplicates([record.provenance_id for record in self.provenance], "provenance_id")

        if not self.provenance:
            raise PackError("a pack carries at least the case-selection provenance record")

        node_ids = {node.node_id for node in self.nodes}
        if self.subject_id not in node_ids:
            raise PackError(
                f"subject {self.subject_id} is not a node in its own pack; the entity under "
                "investigation is the seed the pivots expand from"
            )

        # Referential integrity: every edge resolves to two known nodes
        # (STEP-04 D4, ARCHITECTURE 4.2 "every edge resolves to two known
        # nodes"). Checked here so a dangling edge cannot exist even briefly.
        for edge in self.edges:
            for role, endpoint in (("source", edge.source_id), ("target", edge.target_id)):
                if endpoint not in node_ids:
                    raise PackError(
                        f"edge {edge.edge_id} names {role} {endpoint}, which is not a node in "
                        "this pack"
                    )

        # Provenance completeness: no orphan records.
        known = {record.provenance_id for record in self.provenance}
        for label, cited in (
            ("node", [(node.node_id, node.provenance_id) for node in self.nodes]),
            ("edge", [(edge.edge_id, edge.provenance_id) for edge in self.edges]),
            ("timeline event", [(item.event_id, item.provenance_id) for item in self.timeline]),
        ):
            for record_id, provenance_id in cited:
                if provenance_id not in known:
                    raise PackError(
                        f"{label} {record_id} cites provenance {provenance_id}, which this pack "
                        "does not carry; a record nobody can trace is not evidence"
                    )

        for event in self.timeline:
            if event.node_id not in node_ids:
                raise PackError(
                    f"timeline event {event.event_id} is about {event.node_id}, which is not a "
                    "node in this pack"
                )

    # -- reads -------------------------------------------------------------

    @property
    def hops(self) -> int:
        """Pivots executed against this pack.

        Derived from the provenance records rather than counted alongside them,
        so the number cannot disagree with the evidence. The seed record is not
        a hop, which is why this is one less than the record count.
        """
        return len(self.provenance) - 1

    @property
    def record_ids(self) -> frozenset[str]:
        """Everything a proposal's reason may cite (STEP-04 3.2).

        The resolvable set handed to the symbolic verifier, exactly as a triage
        rationale's set is one row's component ids. An agent proposing a pivot
        has to point at something already in the pack, so a reason is anchored
        to gathered evidence rather than asserted about it.
        """
        return frozenset(
            [node.node_id for node in self.nodes]
            + [edge.edge_id for edge in self.edges]
            + [event.event_id for event in self.timeline]
            + [record.provenance_id for record in self.provenance]
        )

    @property
    def node_ids(self) -> frozenset[str]:
        """The entity ids a pivot parameter may name (decision 7)."""
        return frozenset(node.node_id for node in self.nodes)

    def nodes_at_hop(self, budget: int) -> frozenset[str]:
        """Node ids present after ``budget`` pivots.

        The read the recovery metric is built on (STEP-04 D5): recovery at a
        budget is meaningless unless the pack can say what it held at that
        budget. Because ``hop_index`` is on the provenance record and every node
        cites one, this is a filter rather than a replay.
        """
        if budget < 0:
            raise ValueError(f"budget must be non-negative; got {budget}")
        within = {record.provenance_id for record in self.provenance if record.hop_index <= budget}
        return frozenset(node.node_id for node in self.nodes if node.provenance_id in within)

    @classmethod
    def seed(
        cls,
        case_id: str,
        subject_id: str,
        subject_kind: EntityKind,
        retrieval_ts_ist: str,
    ) -> "EvidencePack":
        """The pack an investigation starts from: one node, one origin record.

        The seed carries provenance like every other record, because "the
        analyst opened this case" is a real and checkable origin and a pack with
        one untraceable node would fail its own completeness invariant.
        """
        record = seed_provenance(case_id, subject_id, retrieval_ts_ist)
        return cls(
            case_id=case_id,
            subject_id=subject_id,
            nodes=(
                EvidenceNode(
                    node_id=subject_id,
                    kind=subject_kind,
                    provenance_id=record.provenance_id,
                    attributes={"role": "subject"},
                ),
            ),
            edges=(),
            timeline=(),
            provenance=(record,),
        )

    def with_hop(
        self,
        record: Provenance,
        *,
        nodes: Sequence[EvidenceNode] = (),
        edges: Sequence[EvidenceEdge] = (),
        timeline: Sequence[TimelineEvent] = (),
    ) -> "EvidencePack":
        """Fold one executed pivot into a new pack.

        Returns a new object; the receiver is untouched. A hop cannot alter a
        pack that has already passed the assembly gate, so "this artifact was
        accepted" stays a statement about a specific set of bytes.

        Records already present are **kept as they were**, not replaced. The
        first sighting is the one that says when an entity entered the
        investigation, and overwriting it with a later hop's provenance would
        quietly move evidence forward in time and inflate recovery at every
        smaller budget.
        """
        if record.provenance_id in {existing.provenance_id for existing in self.provenance}:
            raise PackError(
                f"provenance {record.provenance_id} is already in this pack; each hop records "
                "its own execution"
            )

        return EvidencePack(
            case_id=self.case_id,
            subject_id=self.subject_id,
            nodes=self.nodes + _keep_first_unseen(nodes, lambda node: node.node_id, self.nodes),
            edges=self.edges + _keep_first_unseen(edges, lambda edge: edge.edge_id, self.edges),
            timeline=self.timeline
            + _keep_first_unseen(timeline, lambda event: event.event_id, self.timeline),
            provenance=self.provenance + (record,),
        )

    def to_json_object(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "subject_id": self.subject_id,
            "hops": self.hops,
            "counts": {
                "nodes": len(self.nodes),
                "edges": len(self.edges),
                "timeline": len(self.timeline),
                "provenance": len(self.provenance),
            },
            "nodes": [node.to_json_object() for node in self.nodes],
            "edges": [edge.to_json_object() for edge in self.edges],
            "timeline": [event.to_json_object() for event in self.timeline],
            "provenance": [record.to_json_object() for record in self.provenance],
        }


def _reject_duplicates(values: Sequence[str], field_name: str) -> None:
    """A duplicate id makes a citation ambiguous, which makes it not a citation.

    The same argument ``firewall.InertBlock.wrap`` makes about record ids: two
    records answering to one name means a reference resolves to both and
    identifies neither.
    """
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise PackError(
                f"duplicate {field_name} {value!r}: a citation resolving to two "
                "records is not a citation"
            )
        seen.add(value)


def _keep_first_unseen[T](
    incoming: Sequence[T],
    identify: Callable[[T], str],
    existing: Sequence[T],
) -> tuple[T, ...]:
    """The records in ``incoming`` whose ids appear in neither ``existing`` nor
    earlier in ``incoming`` itself.

    Both halves are load-bearing. Filtering against the pack keeps an entity's
    provenance pinned to the hop that first found it, so recovery at a smaller
    budget cannot be inflated by a later sighting. Filtering within the batch
    handles a query legitimately returning one entity several times: an account
    that engaged with six of a channel's videos is six rows and one node, and
    without this the pack's own uniqueness invariant would reject a perfectly
    good pivot result.
    """
    seen = {identify(item) for item in existing}
    kept: list[T] = []
    for item in incoming:
        key = identify(item)
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
    return tuple(kept)
