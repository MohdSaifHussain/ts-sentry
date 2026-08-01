# SPDX-License-Identifier: MIT
"""STEP-04 D6: GraphML and JSON export of an Evidence Pack.

Round-tripped rather than pattern-matched. A test that asserted the writer
emits certain substrings would pass on output no parser accepts, which is the
opposite of what "interoperable graph format" is supposed to buy. So the XML is
parsed back with ``ElementTree`` and checked against the pack it came from.

The assertion that matters most is that provenance survives. An export that
dropped it would turn an evidence pack into a picture, and a picture is not
evidence.
"""

import json
import xml.etree.ElementTree as ElementTree
from datetime import datetime
from pathlib import Path

import pytest

from ts_sentry.agents.evidence.pack import (
    EdgeRelation,
    EvidenceEdge,
    EvidenceNode,
    EvidencePack,
    Provenance,
    TimelineEvent,
    TimelineKind,
)
from ts_sentry.data.enums import EntityKind
from ts_sentry.data.tz import IST
from ts_sentry.governance.canonical import digest_fields
from ts_sentry.orchestrator.pack_export import (
    GRAPHML_NS,
    graphml_document,
    write_pack_graphml,
    write_pack_json,
)
from ts_sentry.orchestrator.pivots import PIVOT_TEMPLATES, PivotKind, template_sha256

TS = datetime(2026, 7, 31, 14, 30, tzinfo=IST).isoformat()
CASE = "case-0000"
SUBJECT = "chan_000016"
_NS = {"g": GRAPHML_NS}


@pytest.fixture
def pack() -> EvidencePack:
    template = PIVOT_TEMPLATES[PivotKind.INFRA_OVERLAP]
    record = Provenance(
        provenance_id="prov-0001",
        hop_index=1,
        pivot_kind=PivotKind.INFRA_OVERLAP,
        source_table="main.infra_hint",
        query_template_id=template.template_id,
        template_sha256=template_sha256(template),
        param_hash=digest_fields("test", "a"),
        params={"subject_id": SUBJECT, "limit": 25},
        retrieval_ts_ist=TS,
        row_count=1,
    )
    return EvidencePack.seed(CASE, SUBJECT, EntityKind.CHANNEL, TS).with_hop(
        record,
        nodes=(
            EvidenceNode(
                node_id="acct_0000001",
                kind=EntityKind.ACCOUNT,
                provenance_id=record.provenance_id,
                attributes={"shared_ip_bucket": "ipb_012"},
            ),
        ),
        edges=(
            EvidenceEdge(
                edge_id="edge:shares_infra_signal:chan_000016:acct_0000001",
                source_id=SUBJECT,
                target_id="acct_0000001",
                relation=EdgeRelation.SHARES_INFRA_SIGNAL,
                provenance_id=record.provenance_id,
                attributes={"signal_value": "ipb_012"},
            ),
        ),
        timeline=(
            TimelineEvent(
                event_id="ev:signal:acct_0000001",
                kind=TimelineKind.SIGNAL_OBSERVED,
                node_id="acct_0000001",
                ts_ist=TS,
                provenance_id=record.provenance_id,
            ),
        ),
    )


# --------------------------------------------------------------------------
# GraphML
# --------------------------------------------------------------------------


def test_the_document_has_the_structure_the_specification_requires(pack: EvidencePack) -> None:
    """Namespace, ``edgedefault``, and declared keys.

    Checked against the specification rather than against what the writer
    happens to emit: these are exactly the details memory gets subtly wrong,
    which is why the primer was consulted before the writer was written.
    """
    root = graphml_document(pack).getroot()
    assert root is not None

    assert root.tag == f"{{{GRAPHML_NS}}}graphml"
    graph = root.find("g:graph", _NS)
    assert graph is not None
    assert graph.get("edgedefault") == "directed"
    assert graph.get("id") == CASE

    keys = root.findall("g:key", _NS)
    assert {key.get("attr.name") for key in keys} == {
        "kind",
        "relation",
        "provenance_id",
        "attributes",
    }
    assert {key.get("for") for key in keys} == {"node", "edge"}
    # `for` is part of a key's identity in GraphML, so the node and edge keys
    # that share an attribute name must still be distinct declarations.
    assert len({key.get("id") for key in keys}) == len(keys)
    assert len({(key.get("for"), key.get("attr.name")) for key in keys}) == len(keys)
    for key in keys:
        assert key.get("attr.type") in {"boolean", "int", "long", "float", "double", "string"}
        assert key.get("id")


def test_every_node_and_edge_survives_the_round_trip(pack: EvidencePack, tmp_path: Path) -> None:
    path = tmp_path / "graph.graphml"
    write_pack_graphml(pack, path)

    root = ElementTree.parse(path).getroot()
    graph = root.find("g:graph", _NS)
    assert graph is not None

    assert {node.get("id") for node in graph.findall("g:node", _NS)} == set(pack.node_ids)
    edges = graph.findall("g:edge", _NS)
    assert {edge.get("id") for edge in edges} == {edge.edge_id for edge in pack.edges}
    for edge in edges:
        assert edge.get("source") in pack.node_ids
        assert edge.get("target") in pack.node_ids


def test_provenance_survives_the_export(pack: EvidencePack, tmp_path: Path) -> None:
    """The assertion this format exists to support.

    A graph pulled into a drawing tool must still be traceable to the query
    that produced each element. Without this, the export is a picture, and a
    picture is not evidence.
    """
    path = tmp_path / "graph.graphml"
    write_pack_graphml(pack, path)

    root = ElementTree.parse(path).getroot()
    graph = root.find("g:graph", _NS)
    assert graph is not None
    known = {record.provenance_id for record in pack.provenance}

    for element in (*graph.findall("g:node", _NS), *graph.findall("g:edge", _NS)):
        values = {data.get("key"): data.text for data in element.findall("g:data", _NS)}
        provenance = values.get("d_node_provenance") or values.get("d_edge_provenance")
        assert provenance in known, f"{element.get('id')} exported without resolvable provenance"


def test_node_attributes_round_trip_as_json(pack: EvidencePack, tmp_path: Path) -> None:
    """GraphML values are scalars, so a map has to become one value.

    JSON rather than an ad-hoc ``k=v`` join, because attribute values are data
    and a join is ambiguous the moment one contains the separator. The same
    argument ``governance.canonical`` makes about the field separator.
    """
    path = tmp_path / "graph.graphml"
    write_pack_graphml(pack, path)

    root = ElementTree.parse(path).getroot()
    graph = root.find("g:graph", _NS)
    assert graph is not None
    node = next(
        element for element in graph.findall("g:node", _NS) if element.get("id") == "acct_0000001"
    )
    attributes = next(
        data.text for data in node.findall("g:data", _NS) if data.get("key") == "d_attributes"
    )

    assert attributes is not None
    assert json.loads(attributes) == {"shared_ip_bucket": "ipb_012"}


def test_the_export_is_written_with_stable_bytes(pack: EvidencePack, tmp_path: Path) -> None:
    """UTF-8 and ``\\n``, like every other artifact, so a file digest does not
    depend on the platform that produced it."""
    first = tmp_path / "a.graphml"
    second = tmp_path / "b.graphml"
    write_pack_graphml(pack, first)
    write_pack_graphml(pack, second)

    raw = first.read_bytes()
    assert raw == second.read_bytes()
    assert b"\r\n" not in raw
    assert raw.startswith(b'<?xml version="1.0" encoding="UTF-8"?>\n')


def test_an_empty_pack_still_exports_a_valid_document(tmp_path: Path) -> None:
    """A seeded investigation that has not pivoted is not a defective one."""
    seeded = EvidencePack.seed(CASE, SUBJECT, EntityKind.CHANNEL, TS)
    path = tmp_path / "graph.graphml"

    write_pack_graphml(seeded, path)

    graph = ElementTree.parse(path).getroot().find("g:graph", _NS)
    assert graph is not None
    assert len(graph.findall("g:node", _NS)) == 1
    assert graph.findall("g:edge", _NS) == []


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------


def test_the_json_export_carries_what_the_graph_omits(pack: EvidencePack, tmp_path: Path) -> None:
    """The two formats answer different questions.

    GraphML is the entity graph for a tool that draws graphs. JSON is the
    complete artifact, including the timeline and every provenance record, and
    it is the file an auditor checks a claim against.
    """
    path = tmp_path / "pack.json"
    write_pack_json(pack, path)

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload == pack.to_json_object()
    assert len(payload["timeline"]) == len(pack.timeline)
    assert len(payload["provenance"]) == len(pack.provenance)
    assert payload["provenance"][1]["template_sha256"] == template_sha256(
        PIVOT_TEMPLATES[PivotKind.INFRA_OVERLAP]
    )
