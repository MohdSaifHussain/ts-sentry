# SPDX-License-Identifier: MIT
"""STEP-04 D3: the Evidence Pack, its invariants, and its provenance.

STEP-04 3.4 asks for hypothesis properties: "pack integrity invariants hold
after arbitrary approved-pivot sequences; provenance is total (no orphan
records)". Those are the two properties at the bottom of this file, and they
are the reason the invariants live in ``__post_init__`` rather than only in the
D4 gate: a property test that had to route every generated pack through a gate
would be testing the gate.

The rest assert each invariant by constructing the thing it forbids, which is
the only way to know a guard fires rather than merely exists.
"""

import json
from datetime import datetime, timedelta
from typing import cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ts_sentry.agents.evidence.pack import (
    SEED_TEMPLATE_ID,
    EdgeRelation,
    EvidenceEdge,
    EvidenceNode,
    EvidencePack,
    PackError,
    Provenance,
    TimelineEvent,
    TimelineKind,
    seed_provenance,
)
from ts_sentry.data.enums import EntityKind
from ts_sentry.data.tz import IST
from ts_sentry.governance.canonical import digest_fields
from ts_sentry.orchestrator.pivots import PIVOT_TEMPLATES, PivotKind, template_sha256

TS = datetime(2026, 7, 31, 14, 30, tzinfo=IST).isoformat()
CASE = "case-0000"
SUBJECT = "chan_000016"


def _provenance(
    hop: int,
    *,
    kind: PivotKind = PivotKind.INFRA_OVERLAP,
    rows: int = 1,
    ts: str = TS,
) -> Provenance:
    template = PIVOT_TEMPLATES[kind]
    return Provenance(
        provenance_id=f"prov-{hop:04d}",
        hop_index=hop,
        pivot_kind=kind,
        source_table="main.infra_hint",
        query_template_id=template.template_id,
        template_sha256=template_sha256(template),
        param_hash=digest_fields("test", str(hop)),
        params={"subject_id": SUBJECT, "limit": 25},
        retrieval_ts_ist=ts,
        row_count=rows,
    )


def _seed() -> EvidencePack:
    return EvidencePack.seed(CASE, SUBJECT, EntityKind.CHANNEL, TS)


def _node(node_id: str, provenance_id: str = "prov-0001") -> EvidenceNode:
    return EvidenceNode(
        node_id=node_id,
        kind=EntityKind.ACCOUNT,
        provenance_id=provenance_id,
        attributes={"signal_value": "ipb_012"},
    )


# --------------------------------------------------------------------------
# The seed
# --------------------------------------------------------------------------


def test_a_seeded_pack_holds_the_subject_and_its_origin() -> None:
    pack = _seed()

    assert pack.node_ids == {SUBJECT}
    assert pack.hops == 0
    assert len(pack.provenance) == 1
    assert pack.provenance[0].query_template_id == SEED_TEMPLATE_ID
    assert pack.provenance[0].pivot_kind is None


def test_the_seed_node_carries_provenance_like_every_other_record() -> None:
    """Provenance completeness has no exemption for the first node. "The
    analyst opened this case" is a real origin, and a pack whose seed had none
    would fail its own invariant."""
    pack = _seed()

    assert pack.nodes[0].provenance_id == pack.provenance[0].provenance_id


def test_only_the_case_selection_record_may_omit_a_pivot_kind() -> None:
    """``pivot_kind is None`` means exactly one thing, so it cannot be used to
    smuggle in a record whose origin nobody stated."""
    with pytest.raises(ValueError, match="carries no pivot_kind"):
        Provenance(
            provenance_id="prov-0001",
            hop_index=1,
            pivot_kind=None,
            source_table="main.infra_hint",
            query_template_id="pivot.infra_overlap.v1",
            template_sha256=digest_fields("test", "a"),
            param_hash=digest_fields("test", "b"),
            params={},
            retrieval_ts_ist=TS,
            row_count=0,
        )

    with pytest.raises(ValueError, match="carries no pivot_kind"):
        Provenance(
            provenance_id="prov-0000",
            hop_index=0,
            pivot_kind=PivotKind.ACCOUNT_LINK,
            source_table="analyst.case_selection",
            query_template_id=SEED_TEMPLATE_ID,
            template_sha256=digest_fields("test", "a"),
            param_hash=digest_fields("test", "b"),
            params={},
            retrieval_ts_ist=TS,
            row_count=1,
        )


# --------------------------------------------------------------------------
# Referential integrity
# --------------------------------------------------------------------------


def test_an_edge_naming_an_unknown_node_is_refused() -> None:
    """ARCHITECTURE 4.2's assembly gate: every edge resolves to two known
    nodes. Enforced at construction so a dangling edge never exists."""
    pack = _seed()
    record = _provenance(1)

    with pytest.raises(PackError, match="which is not a node in this pack"):
        pack.with_hop(
            record,
            edges=(
                EvidenceEdge(
                    edge_id="edge-1",
                    source_id=SUBJECT,
                    target_id="acct_9999999",
                    relation=EdgeRelation.SHARES_INFRA_SIGNAL,
                    provenance_id=record.provenance_id,
                    attributes={},
                ),
            ),
        )


def test_a_self_edge_is_refused() -> None:
    with pytest.raises(ValueError, match="joins .* to itself"):
        EvidenceEdge(
            edge_id="edge-1",
            source_id=SUBJECT,
            target_id=SUBJECT,
            relation=EdgeRelation.SHARES_INFRA_SIGNAL,
            provenance_id="prov-0001",
            attributes={},
        )


def test_a_timeline_event_about_an_unknown_node_is_refused() -> None:
    pack = _seed()
    record = _provenance(1)

    with pytest.raises(PackError, match="is about"):
        pack.with_hop(
            record,
            timeline=(
                TimelineEvent(
                    event_id="ev-1",
                    kind=TimelineKind.SIGNAL_OBSERVED,
                    node_id="acct_9999999",
                    ts_ist=TS,
                    provenance_id=record.provenance_id,
                ),
            ),
        )


def test_the_subject_must_be_a_node_in_its_own_pack() -> None:
    record = seed_provenance(CASE, SUBJECT, TS)

    with pytest.raises(PackError, match="is not a node in its own pack"):
        EvidencePack(
            case_id=CASE,
            subject_id=SUBJECT,
            nodes=(_node("acct_0000001", record.provenance_id),),
            edges=(),
            timeline=(),
            provenance=(record,),
        )


# --------------------------------------------------------------------------
# Provenance completeness (no orphan records)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("orphan", ["node", "edge", "timeline"])
def test_a_record_citing_absent_provenance_is_refused(orphan: str) -> None:
    """ "No orphan records" made structural. Each record type is checked
    separately because each is a different way to lose the trail."""
    record = seed_provenance(CASE, SUBJECT, TS)
    nodes = [
        EvidenceNode(
            node_id=SUBJECT,
            kind=EntityKind.CHANNEL,
            provenance_id=record.provenance_id,
            attributes={},
        )
    ]
    edges: list[EvidenceEdge] = []
    timeline: list[TimelineEvent] = []

    if orphan == "node":
        nodes.append(_node("acct_0000001", "prov-9999"))
    elif orphan == "edge":
        nodes.append(_node("acct_0000001", record.provenance_id))
        edges.append(
            EvidenceEdge(
                edge_id="edge-1",
                source_id=SUBJECT,
                target_id="acct_0000001",
                relation=EdgeRelation.OWNS_CHANNEL,
                provenance_id="prov-9999",
                attributes={},
            )
        )
    else:
        timeline.append(
            TimelineEvent(
                event_id="ev-1",
                kind=TimelineKind.SIGNAL_OBSERVED,
                node_id=SUBJECT,
                ts_ist=TS,
                provenance_id="prov-9999",
            )
        )

    with pytest.raises(PackError, match="does not carry"):
        EvidencePack(
            case_id=CASE,
            subject_id=SUBJECT,
            nodes=tuple(nodes),
            edges=tuple(edges),
            timeline=tuple(timeline),
            provenance=(record,),
        )


def test_a_zero_row_pivot_keeps_its_provenance_record() -> None:
    """The negative result is evidence.

    A pack that dropped the record of a pivot returning nothing could not tell
    an analyst apart from a pivot nobody ran, and the recovery metric would be
    computed against a budget the pack no longer remembers spending.
    """
    pack = _seed().with_hop(_provenance(1, rows=0))

    assert pack.hops == 1
    assert len(pack.nodes) == 1
    assert pack.provenance[-1].row_count == 0
    assert pack.provenance[-1].provenance_id in pack.record_ids


@pytest.mark.parametrize("field_name", ["template_sha256", "param_hash"])
def test_provenance_digests_must_be_real_digests(field_name: str) -> None:
    kwargs: dict[str, object] = {
        "provenance_id": "prov-0001",
        "hop_index": 1,
        "pivot_kind": PivotKind.INFRA_OVERLAP,
        "source_table": "main.infra_hint",
        "query_template_id": "pivot.infra_overlap.v1",
        "template_sha256": digest_fields("test", "a"),
        "param_hash": digest_fields("test", "b"),
        "params": {},
        "retrieval_ts_ist": TS,
        "row_count": 1,
    }
    kwargs[field_name] = "not-a-digest"

    with pytest.raises(ValueError, match=field_name):
        Provenance(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad_ts",
    [
        "2026-07-31T14:30:00",  # naive
        "2026-07-31T14:30:00+00:00",  # UTC, not IST
        "2026-07-31T14:30:00-04:00",
        "not a timestamp",
        "",
    ],
)
def test_retrieval_timestamps_must_be_ist(bad_ts: str) -> None:
    """Parsed and checked, not pattern-matched.

    The pack stores timestamps as strings for JSON and GraphML, and a string
    that looks like a timestamp is not one. This is the same defect class
    STEP-02 D3 and STEP-03 D5 hit from the DuckDB side, closed on the way out
    as well as on the way in.
    """
    with pytest.raises(ValueError):
        _provenance(1, ts=bad_ts)


# --------------------------------------------------------------------------
# Uniqueness and immutability
# --------------------------------------------------------------------------


@pytest.mark.parametrize("field_name", ["node_id", "edge_id", "provenance_id"])
def test_duplicate_ids_are_refused(field_name: str) -> None:
    """A citation resolving to two records is not a citation, which is the
    argument the firewall already makes about its own record ids."""
    record = seed_provenance(CASE, SUBJECT, TS)
    second = _provenance(1)
    subject_node = EvidenceNode(
        node_id=SUBJECT,
        kind=EntityKind.CHANNEL,
        provenance_id=record.provenance_id,
        attributes={},
    )
    edge = EvidenceEdge(
        edge_id="edge-1",
        source_id=SUBJECT,
        target_id="acct_0000001",
        relation=EdgeRelation.OWNS_CHANNEL,
        provenance_id=record.provenance_id,
        attributes={},
    )
    nodes: tuple[EvidenceNode, ...] = (subject_node, _node("acct_0000001", record.provenance_id))
    edges: tuple[EvidenceEdge, ...] = (edge,)
    provenance: tuple[Provenance, ...] = (record, second)

    if field_name == "node_id":
        nodes = (*nodes, _node("acct_0000001", record.provenance_id))
    elif field_name == "edge_id":
        edges = (edge, edge)
    else:
        provenance = (record, second, second)

    with pytest.raises(PackError, match=f"duplicate {field_name}"):
        EvidencePack(
            case_id=CASE,
            subject_id=SUBJECT,
            nodes=nodes,
            edges=edges,
            timeline=(),
            provenance=provenance,
        )


def test_with_hop_returns_a_new_pack_and_leaves_the_old_one_alone() -> None:
    """A hop must not alter a pack that has already passed the assembly gate."""
    pack = _seed()

    grown = pack.with_hop(_provenance(1), nodes=(_node("acct_0000001"),))

    assert pack.hops == 0
    assert pack.node_ids == {SUBJECT}
    assert grown.hops == 1
    assert grown.node_ids == {SUBJECT, "acct_0000001"}


def test_a_hop_cannot_reuse_a_provenance_id() -> None:
    pack = _seed().with_hop(_provenance(1))

    with pytest.raises(PackError, match="already in this pack"):
        pack.with_hop(_provenance(1))


def test_one_hop_returning_an_entity_twice_yields_one_node() -> None:
    """An account that engaged with six of a channel's videos is six rows and
    one node. Without within-batch deduplication the pack's own uniqueness
    invariant would reject a perfectly good pivot result."""
    record = _provenance(1)

    pack = _seed().with_hop(
        record,
        nodes=(
            _node("acct_0000001", record.provenance_id),
            _node("acct_0000001", record.provenance_id),
            _node("acct_0000002", record.provenance_id),
        ),
    )

    assert pack.node_ids == {SUBJECT, "acct_0000001", "acct_0000002"}


def test_a_later_hop_does_not_repossess_an_entity_the_first_one_found() -> None:
    """The provenance of a node is the hop that *first* found it.

    Overwriting it with a later sighting would move evidence forward in time
    and inflate recovery at every smaller budget, which is a number this phase
    reports.
    """
    first = _provenance(1)
    second = _provenance(2)

    pack = (
        _seed()
        .with_hop(first, nodes=(_node("acct_0000001", first.provenance_id),))
        .with_hop(second, nodes=(_node("acct_0000001", second.provenance_id),))
    )

    found = next(node for node in pack.nodes if node.node_id == "acct_0000001")
    assert found.provenance_id == first.provenance_id
    assert pack.nodes_at_hop(1) == {SUBJECT, "acct_0000001"}


def test_nodes_at_hop_reports_what_the_pack_held_at_a_budget() -> None:
    """The read the recovery metric is built on (STEP-04 D5)."""
    pack = _seed()
    for hop in (1, 2, 3):
        record = _provenance(hop)
        pack = pack.with_hop(record, nodes=(_node(f"acct_000000{hop}", record.provenance_id),))

    assert pack.nodes_at_hop(0) == {SUBJECT}
    assert pack.nodes_at_hop(1) == {SUBJECT, "acct_0000001"}
    assert pack.nodes_at_hop(2) == {SUBJECT, "acct_0000001", "acct_0000002"}
    assert pack.nodes_at_hop(99) == pack.node_ids

    with pytest.raises(ValueError, match="non-negative"):
        pack.nodes_at_hop(-1)


def test_record_ids_covers_every_citable_record() -> None:
    """The resolvable set the proposal verifier is handed (STEP-04 3.2)."""
    record = _provenance(1)
    pack = _seed().with_hop(
        record,
        nodes=(_node("acct_0000001", record.provenance_id),),
        edges=(
            EvidenceEdge(
                edge_id="edge-1",
                source_id=SUBJECT,
                target_id="acct_0000001",
                relation=EdgeRelation.SHARES_INFRA_SIGNAL,
                provenance_id=record.provenance_id,
                attributes={},
            ),
        ),
        timeline=(
            TimelineEvent(
                event_id="ev-1",
                kind=TimelineKind.SIGNAL_OBSERVED,
                node_id="acct_0000001",
                ts_ist=TS,
                provenance_id=record.provenance_id,
            ),
        ),
    )

    assert pack.record_ids == {
        SUBJECT,
        "acct_0000001",
        "edge-1",
        "ev-1",
        "prov-0000",
        "prov-0001",
    }


def test_json_round_trip_carries_every_record_and_its_provenance() -> None:
    record = _provenance(1)
    pack = _seed().with_hop(record, nodes=(_node("acct_0000001", record.provenance_id),))

    payload = pack.to_json_object()
    nodes = cast(list[dict[str, object]], payload["nodes"])
    provenance = cast(list[dict[str, object]], payload["provenance"])

    assert payload["hops"] == 1
    assert payload["counts"] == {"nodes": 2, "edges": 0, "timeline": 0, "provenance": 2}
    assert [node["provenance_id"] for node in nodes] == ["prov-0000", "prov-0001"]
    assert provenance[1]["template_sha256"] == template_sha256(
        PIVOT_TEMPLATES[PivotKind.INFRA_OVERLAP]
    )
    assert json.loads(json.dumps(payload)) == payload


# --------------------------------------------------------------------------
# STEP-04 3.4: properties over arbitrary approved-pivot sequences
# --------------------------------------------------------------------------

_HOP = st.tuples(
    st.sampled_from(list(PivotKind)),
    st.lists(st.integers(min_value=0, max_value=8), min_size=0, max_size=6),
    st.integers(min_value=0, max_value=4),
)


def _fold(hops: list[tuple[PivotKind, list[int], int]]) -> EvidencePack:
    """Apply a generated sequence of approved pivots to a seeded pack.

    Node ids are drawn from a small pool on purpose, so sequences repeatedly
    rediscover the same entities. That is what a real investigation does, and
    it is the case where deduplication and first-sighting provenance have to
    hold.
    """
    pack = EvidencePack.seed(CASE, SUBJECT, EntityKind.CHANNEL, TS)
    for index, (kind, node_numbers, event_count) in enumerate(hops, start=1):
        record = _provenance(index, kind=kind, rows=len(node_numbers))
        nodes = tuple(
            _node(f"acct_000000{number}", record.provenance_id) for number in node_numbers
        )
        known = pack.node_ids | {node.node_id for node in nodes}
        edges = tuple(
            EvidenceEdge(
                edge_id=f"edge-{index}-{number}",
                source_id=SUBJECT,
                target_id=f"acct_000000{number}",
                relation=EdgeRelation.SHARES_INFRA_SIGNAL,
                provenance_id=record.provenance_id,
                attributes={},
            )
            for number in node_numbers
            if f"acct_000000{number}" in known and f"acct_000000{number}" != SUBJECT
        )
        timeline = tuple(
            TimelineEvent(
                event_id=f"ev-{index}-{position}",
                kind=TimelineKind.SIGNAL_OBSERVED,
                node_id=SUBJECT,
                ts_ist=(
                    datetime(2026, 7, 31, 14, 30, tzinfo=IST) + timedelta(minutes=position)
                ).isoformat(),
                provenance_id=record.provenance_id,
            )
            for position in range(event_count)
        )
        pack = pack.with_hop(record, nodes=nodes, edges=edges, timeline=timeline)
    return pack


@settings(max_examples=200)
@given(st.lists(_HOP, min_size=0, max_size=8))
def test_pack_invariants_hold_after_any_approved_pivot_sequence(
    hops: list[tuple[PivotKind, list[int], int]],
) -> None:
    """STEP-04 3.4, first half.

    Folding is what an investigation does, so the property is about folding
    rather than about a hand-built pack. Construction raises on any violated
    invariant, so reaching the assertions at all is most of the property; the
    assertions then restate the ones worth naming.
    """
    pack = _fold(hops)

    assert pack.hops == len(hops)
    assert pack.subject_id in pack.node_ids
    assert len(pack.node_ids) == len(pack.nodes)
    assert len({edge.edge_id for edge in pack.edges}) == len(pack.edges)

    node_ids = pack.node_ids
    for edge in pack.edges:
        assert edge.source_id in node_ids
        assert edge.target_id in node_ids


@settings(max_examples=200)
@given(st.lists(_HOP, min_size=0, max_size=8))
def test_provenance_is_total_after_any_approved_pivot_sequence(
    hops: list[tuple[PivotKind, list[int], int]],
) -> None:
    """STEP-04 3.4, second half: provenance is total, and monotone.

    Totality is the no-orphan rule. Monotonicity is the property the recovery
    metric depends on: what the pack held at a budget can only grow as the
    budget grows, so a number reported at 5 pivots is not contradicted by the
    same pack read at 10.
    """
    pack = _fold(hops)
    known = {record.provenance_id for record in pack.provenance}

    for node in pack.nodes:
        assert node.provenance_id in known
    for edge in pack.edges:
        assert edge.provenance_id in known
    for event in pack.timeline:
        assert event.provenance_id in known

    previous: frozenset[str] = frozenset()
    for budget in range(len(hops) + 1):
        current = pack.nodes_at_hop(budget)
        assert previous <= current
        previous = current
    assert previous == pack.node_ids
