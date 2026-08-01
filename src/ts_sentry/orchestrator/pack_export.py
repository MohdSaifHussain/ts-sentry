# SPDX-License-Identifier: MIT
"""D6: Evidence Pack export, as GraphML and JSON (STEP-04 D6).

Two formats because they answer different questions. JSON is the complete
artifact: every node, edge, timeline entry and provenance record, which is what
a later phase re-reads and what an auditor checks a claim against. GraphML is
the entity graph for a tool that draws graphs, and it deliberately carries less.

Written with ``xml.etree.ElementTree`` rather than a graph library. Adding a
dependency to serialize a small XML vocabulary would widen the supply-chain
surface ``docs/DECISIONS.md`` already lists gaps against, and GraphML's
structure is four element types.

Consulted per CLAUDE.md's official-sources rule, because the namespace URI,
the mandatory ``edgedefault`` attribute and the legal ``attr.type`` values are
exactly the kind of detail memory gets subtly wrong:
https://graphml.ethz.ch/primer/graphml-primer.html

Provenance survives the export
------------------------------
Every node and edge carries its ``provenance_id`` as a GraphML ``<data>``
value, so a graph pulled into a drawing tool can still be traced back to the
query that produced each element. An export that dropped provenance would turn
an evidence pack into a picture, and a picture is not evidence. A test asserts
it by round-tripping rather than by reading the writer.

The graph is directed, and that is a claim
------------------------------------------
``edgedefault="directed"`` because the relations are directed: an account owns
a channel, a channel published a video. ``SHARES_METADATA`` and
``SHARES_INFRA_SIGNAL`` are symmetric in meaning and are still emitted with the
subject as source, because the direction records *which entity the pivot
started from*, which is investigative history rather than a claim about the
relation. That is stated here because a reader of the graph could otherwise
read asymmetry into a symmetric fact.
"""

import json
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from ts_sentry.agents.evidence.pack import EvidencePack

__all__ = [
    "GRAPHML_NS",
    "graphml_document",
    "write_pack_graphml",
    "write_pack_json",
]

GRAPHML_NS = "http://graphml.graphdrawing.org/xmlns"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
_SCHEMA_LOCATION = f"{GRAPHML_NS} http://graphml.graphdrawing.org/xmlns/1.0/graphml.xsd"

_NODE_KEYS: tuple[tuple[str, str, str], ...] = (
    ("d_kind", "kind", "string"),
    ("d_node_provenance", "provenance_id", "string"),
    ("d_attributes", "attributes", "string"),
)
_EDGE_KEYS: tuple[tuple[str, str, str], ...] = (
    ("d_relation", "relation", "string"),
    ("d_edge_provenance", "provenance_id", "string"),
    ("d_edge_attributes", "attributes", "string"),
)
"""Declared keys, one per attribute a node or edge carries.

Node and edge keys are declared separately with distinct ids even where the
attribute name matches, because ``for`` is part of a key's identity in GraphML
and a single key declared ``for="all"`` would make a node attribute and an edge
attribute indistinguishable to a reader of the schema.
"""


def _render_attributes(attributes: dict[str, str]) -> str:
    """Flatten an attribute map into one GraphML-safe string.

    GraphML values are scalars, so a map has to become one value. JSON is used
    rather than an ad-hoc ``k=v`` join because attribute values are data and a
    join would be ambiguous the moment one contained the separator, which is
    the same argument ``governance.canonical`` makes about the field separator.
    """
    return json.dumps(attributes, sort_keys=True, ensure_ascii=False)


def graphml_document(pack: EvidencePack) -> ElementTree.ElementTree:
    """Build the GraphML tree for a pack's entity graph.

    Returned as a tree rather than written, so a test can inspect the structure
    without touching a filesystem.
    """
    ElementTree.register_namespace("", GRAPHML_NS)
    root = ElementTree.Element(
        f"{{{GRAPHML_NS}}}graphml",
        {f"{{{_XSI_NS}}}schemaLocation": _SCHEMA_LOCATION},
    )

    for key_id, name, attr_type in (*_NODE_KEYS, *_EDGE_KEYS):
        ElementTree.SubElement(
            root,
            f"{{{GRAPHML_NS}}}key",
            {
                "id": key_id,
                "for": "node" if (key_id, name, attr_type) in _NODE_KEYS else "edge",
                "attr.name": name,
                "attr.type": attr_type,
            },
        )

    graph = ElementTree.SubElement(
        root,
        f"{{{GRAPHML_NS}}}graph",
        {"id": pack.case_id, "edgedefault": "directed"},
    )

    for node in pack.nodes:
        element = ElementTree.SubElement(graph, f"{{{GRAPHML_NS}}}node", {"id": node.node_id})
        for key_id, value in (
            ("d_kind", node.kind.value),
            ("d_node_provenance", node.provenance_id),
            ("d_attributes", _render_attributes(dict(node.attributes))),
        ):
            data = ElementTree.SubElement(element, f"{{{GRAPHML_NS}}}data", {"key": key_id})
            data.text = value

    for edge in pack.edges:
        element = ElementTree.SubElement(
            graph,
            f"{{{GRAPHML_NS}}}edge",
            {"id": edge.edge_id, "source": edge.source_id, "target": edge.target_id},
        )
        for key_id, value in (
            ("d_relation", edge.relation.value),
            ("d_edge_provenance", edge.provenance_id),
            ("d_edge_attributes", _render_attributes(dict(edge.attributes))),
        ):
            data = ElementTree.SubElement(element, f"{{{GRAPHML_NS}}}data", {"key": key_id})
            data.text = value

    return ElementTree.ElementTree(root)


def write_pack_graphml(pack: EvidencePack, path: Path) -> None:
    """Write the entity graph as GraphML.

    ``\\n`` newlines and UTF-8 explicitly, matching every other artifact this
    system writes, so a file digest does not depend on the platform that
    produced it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    document = graphml_document(pack)
    ElementTree.indent(document, space="  ")
    root = document.getroot()
    assert root is not None  # graphml_document always builds one
    text = ElementTree.tostring(root, encoding="unicode", xml_declaration=False)
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n' + text + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_pack_json(pack: EvidencePack, path: Path) -> None:
    """Write the complete pack as JSON.

    The full artifact, including the timeline and every provenance record, which
    the GraphML deliberately omits. This is the file a later phase re-reads and
    the one an auditor checks a claim against.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(pack.to_json_object(), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
