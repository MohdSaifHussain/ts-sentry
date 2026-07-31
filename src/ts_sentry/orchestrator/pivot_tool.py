# SPDX-License-Identifier: MIT
"""D2: the ``run_parameterized_pivot`` handler, the second executable tool.

Orchestrator-side, for the reason ``triage_tool`` is: the agent owns the
proposal format, the orchestrator owns the database connection and the act of
running anything. The handler is deterministic and makes no model call. A tool
that could prompt would be an agent wearing an allowlist entry.

Why the handler returns the whole grown pack
--------------------------------------------
``dispatch`` runs the consequence gate over whatever a tool returns, and this
tool's declared consequence is ASSEMBLE, so the artifact the assembly gate
validates *is* whatever comes back from here. Returning only this hop's rows
would mean the gate validated a fragment and nothing ever validated the pack.

So the current pack arrives through ``ToolResources``, not through
``ToolContext.params``, and the distinction is the control rather than
bookkeeping: resources are what the orchestrator already held, params are what
the agent asked for. An agent that could supply the pack could supply a pack
containing entities it invented, and every "must already be in the pack" check
in this phase would be checking the agent's own claims against themselves.

Validated twice, and why that is not belt and braces
-----------------------------------------------------
``proposal_check`` validates parameters before the analyst is asked, so a bad
parameter is a *refused proposal*. This module validates them again and raises
on failure, so a bad parameter here is a *failed tool*. Those are different
findings and they should look different: the first is the governance layer
working, the second means the orchestrator dispatched something it had already
rejected, which is a defect in this code rather than an outcome.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

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
from ts_sentry.data.tz import ist_from_epoch_ms
from ts_sentry.governance.scopes import resolve_table
from ts_sentry.orchestrator.pivots import (
    PIVOT_TEMPLATES,
    PivotKind,
    PivotTemplate,
    PivotViolation,
    bind_values,
    param_hash,
    resolve_pivot_by_name,
    template_sha256,
    validate_params,
)
from ts_sentry.orchestrator.toolspec import ToolContext, ToolViolation

__all__ = ["PIVOT_KIND_PARAM", "run_parameterized_pivot"]

PIVOT_KIND_PARAM = "pivot_kind"
"""The one parameter that names *which* template runs.

Kept out of the templates' own parameter lists so that the thing selecting the
query is never confused with the values bound into it. It selects a reviewed
template from a closed set; it is never interpolated into SQL.
"""


@dataclass(frozen=True, slots=True)
class _Hop:
    """What one executed pivot contributed, before it is folded into the pack."""

    nodes: tuple[EvidenceNode, ...]
    edges: tuple[EvidenceEdge, ...]
    timeline: tuple[TimelineEvent, ...]


def _entity_kind(raw: str) -> EntityKind:
    """Resolve a subject kind the database reported.

    ``infra_hint.subject_kind`` is written by the generator from ``EntityKind``,
    so an unresolvable value means the data disagrees with the schema rather
    than that the caller passed something odd, and that is worth failing on.
    """
    try:
        return EntityKind(raw)
    except ValueError as exc:
        raise ToolViolation(f"no EntityKind resolves subject kind {raw!r}") from exc


def _node(node_id: str, kind: EntityKind, provenance_id: str, **attributes: object) -> EvidenceNode:
    return EvidenceNode(
        node_id=node_id,
        kind=kind,
        provenance_id=provenance_id,
        attributes={name: str(value) for name, value in attributes.items() if value is not None},
    )


def _edge(
    source_id: str,
    target_id: str,
    relation: EdgeRelation,
    provenance_id: str,
    *,
    discriminator: str = "",
    **attributes: object,
) -> EvidenceEdge:
    """Build an edge with a content-derived id.

    Ids are natural keys rather than positions, so re-running a pivot that
    rediscovers the same relation contributes the same edge id and is
    deduplicated against the pack instead of accumulating a second copy of an
    unchanged fact. The first sighting keeps its provenance, which is what
    makes recovery at a budget mean what it says.
    """
    suffix = f":{discriminator}" if discriminator else ""
    return EvidenceEdge(
        edge_id=f"edge:{relation.value}:{source_id}:{target_id}{suffix}",
        source_id=source_id,
        target_id=target_id,
        relation=relation,
        provenance_id=provenance_id,
        attributes={name: str(value) for name, value in attributes.items() if value is not None},
    )


def _as_epoch_ms(value: object, column: str) -> int:
    """Read a timestamp column DuckDB returned as ``epoch_ms(...)``.

    Rows arrive untyped, so this is a boundary rather than a cast. A column
    that is not an integer means the query and this mapping disagree about the
    result shape, which is a defect worth failing on rather than coercing past.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolViolation(
            f"expected {column} to be epoch milliseconds; got {type(value).__name__}"
        )
    return value


def _event(
    event_id: str,
    kind: TimelineKind,
    node_id: str,
    epoch_ms: object,
    provenance_id: str,
    *,
    column: str = "epoch_ms",
) -> TimelineEvent:
    return TimelineEvent(
        event_id=event_id,
        kind=kind,
        node_id=node_id,
        ts_ist=ist_from_epoch_ms(_as_epoch_ms(epoch_ms, column)).isoformat(),
        provenance_id=provenance_id,
    )


def _shared_metadata_hop(
    rows: Sequence[tuple[object, ...]], values: Mapping[str, object], provenance_id: str
) -> _Hop:
    subject = str(values["account_id"])
    nodes: list[EvidenceNode] = []
    edges: list[EvidenceEdge] = []
    for peer_account_id, metadata_field, shared_value in rows:
        peer = str(peer_account_id)
        nodes.append(
            _node(peer, EntityKind.ACCOUNT, provenance_id, **{str(metadata_field): shared_value})
        )
        edges.append(
            _edge(
                subject,
                peer,
                EdgeRelation.SHARES_METADATA,
                provenance_id,
                discriminator=str(metadata_field),
                metadata_field=metadata_field,
                shared_value=shared_value,
            )
        )
    return _Hop(tuple(nodes), tuple(edges), ())


def _temporal_correlation_hop(
    rows: Sequence[tuple[object, ...]], values: Mapping[str, object], provenance_id: str
) -> _Hop:
    channel = str(values["channel_id"])
    nodes: list[EvidenceNode] = []
    edges: list[EvidenceEdge] = []
    timeline: list[TimelineEvent] = []
    for comment_id, account_id, video_id, posted_epoch_ms, template_id in rows:
        account = str(account_id)
        video = str(video_id)
        nodes.append(_node(video, EntityKind.VIDEO, provenance_id))
        nodes.append(_node(account, EntityKind.ACCOUNT, provenance_id))
        edges.append(_edge(channel, video, EdgeRelation.PUBLISHED_ON, provenance_id))
        edges.append(
            _edge(
                account,
                video,
                EdgeRelation.COMMENTED_ON,
                provenance_id,
                comment_template=template_id,
            )
        )
        timeline.append(
            _event(
                f"ev:comment:{comment_id}",
                TimelineKind.COMMENT_POSTED,
                account,
                posted_epoch_ms,
                provenance_id,
            )
        )
    return _Hop(tuple(nodes), tuple(edges), tuple(timeline))


def _engagement_edge_hop(
    rows: Sequence[tuple[object, ...]], values: Mapping[str, object], provenance_id: str
) -> _Hop:
    channel = str(values["channel_id"])
    nodes: list[EvidenceNode] = []
    edges: list[EvidenceEdge] = []
    timeline: list[TimelineEvent] = []
    for account_id, target_kind, target_id, kind, count, first_ms, _last_ms in rows:
        account = str(account_id)
        target = str(target_id)
        nodes.append(_node(account, EntityKind.ACCOUNT, provenance_id))
        if str(target_kind) == "video":
            nodes.append(_node(target, EntityKind.VIDEO, provenance_id))
            edges.append(_edge(channel, target, EdgeRelation.PUBLISHED_ON, provenance_id))
        edges.append(
            _edge(
                account,
                target,
                EdgeRelation.ENGAGED_WITH,
                provenance_id,
                discriminator=str(kind),
                engagement_kind=kind,
                event_count=count,
            )
        )
        timeline.append(
            _event(
                f"ev:engagement:{account}:{target}:{kind}",
                TimelineKind.ENGAGEMENT_OBSERVED,
                account,
                first_ms,
                provenance_id,
            )
        )
    return _Hop(tuple(nodes), tuple(edges), tuple(timeline))


def _infra_overlap_hop(
    rows: Sequence[tuple[object, ...]], values: Mapping[str, object], provenance_id: str
) -> _Hop:
    subject = str(values["subject_id"])
    nodes: list[EvidenceNode] = []
    edges: list[EvidenceEdge] = []
    timeline: list[TimelineEvent] = []
    for peer_id, peer_kind, signal_type, signal_value, count, first_ms, _last_ms in rows:
        peer = str(peer_id)
        nodes.append(
            _node(
                peer,
                _entity_kind(str(peer_kind)),
                provenance_id,
                **{str(signal_type): signal_value},
            )
        )
        edges.append(
            _edge(
                subject,
                peer,
                EdgeRelation.SHARES_INFRA_SIGNAL,
                provenance_id,
                discriminator=str(signal_type),
                signal_type=signal_type,
                signal_value=signal_value,
                hint_count=count,
            )
        )
        timeline.append(
            _event(
                f"ev:signal:{peer}:{signal_type}:{signal_value}",
                TimelineKind.SIGNAL_OBSERVED,
                peer,
                first_ms,
                provenance_id,
            )
        )
    return _Hop(tuple(nodes), tuple(edges), tuple(timeline))


def _account_link_hop(
    rows: Sequence[tuple[object, ...]], values: Mapping[str, object], provenance_id: str
) -> _Hop:
    channel = str(values["channel_id"])
    nodes: list[EvidenceNode] = []
    edges: list[EvidenceEdge] = []
    for account_id, relation, weight in rows:
        account = str(account_id)
        nodes.append(_node(account, EntityKind.ACCOUNT, provenance_id))
        edges.append(
            _edge(
                account,
                channel,
                EdgeRelation.OWNS_CHANNEL
                if str(relation) == "owner"
                else EdgeRelation.COMMENTED_ON,
                provenance_id,
                weight=weight,
            )
        )
    return _Hop(tuple(nodes), tuple(edges), ())


def _map_rows(
    kind: PivotKind,
    rows: Sequence[tuple[object, ...]],
    values: Mapping[str, object],
    provenance_id: str,
) -> _Hop:
    """Turn one pivot's rows into pack records.

    Exhaustive over ``PivotKind``. Not closed by ``assert_never`` because the
    mapping is a dispatch table rather than a match: a kind with no mapper
    fails loudly here, and the pivot-vocabulary test already asserts every kind
    has a template, so a new kind arriving without a mapper cannot reach
    production silently.
    """
    mappers = {
        PivotKind.SHARED_METADATA: _shared_metadata_hop,
        PivotKind.TEMPORAL_CORRELATION: _temporal_correlation_hop,
        PivotKind.ENGAGEMENT_EDGE: _engagement_edge_hop,
        PivotKind.INFRA_OVERLAP: _infra_overlap_hop,
        PivotKind.ACCOUNT_LINK: _account_link_hop,
    }
    mapper = mappers.get(kind)
    if mapper is None:  # pragma: no cover - every kind has a mapper, asserted by a test
        raise ToolViolation(f"pivot {kind.value} has no row mapping in this build")
    return mapper(rows, values, provenance_id)


def _source_table(template: PivotTemplate) -> str:
    """The table a provenance record names as its source.

    A pivot may read more than one table, so this is the primary one: the
    lowest-ordered scope the template declares, resolved through the allowlist.
    Recording one name where several were read would be a lie if the record
    claimed completeness, so ``Provenance`` carries the template digest too, and
    that is what actually re-derives the query.
    """
    primary = sorted(template.required_scopes, key=lambda scope: scope.value)[0]
    return resolve_table(primary)


def run_parameterized_pivot(context: ToolContext, /) -> object:
    """Execute one analyst-approved pivot and return the grown pack.

    Every failure here raises, and that is deliberate. By the time a proposal
    reaches this function it has been parsed, resolved, citation-checked,
    parameter-checked and approved by an analyst. Anything still wrong is the
    orchestrator dispatching something it already rejected, which dispatch
    ledgers as ``TOOL_RESULT`` with ``ok: false`` and reports as ``FAILED``
    rather than as a refusal. A defect must not be able to look like the
    governance layer working.
    """
    connection = context.require_connection()
    # `ToolResources.pack` is typed `object` so the general tool contract does
    # not depend on one agent's artifact. This is where that is paid for, and
    # the check is the fail-closed refusal for a caller that lends the wrong
    # thing rather than a formality.
    pack = context.resources.pack
    if not isinstance(pack, EvidencePack):
        raise ToolViolation(
            "this tool needs the evidence pack it is extending, which the orchestrator supplies "
            f"through ToolResources; got {type(pack).__name__}"
        )
    retrieval = context.resources.retrieval_ts
    if retrieval is None:
        raise ToolViolation(
            "this tool needs the session's timestamp, which the orchestrator supplies through "
            "ToolResources; nothing here reads the clock"
        )

    raw_kind = context.params.get(PIVOT_KIND_PARAM)
    if not isinstance(raw_kind, str):
        raise ToolViolation(f"{PIVOT_KIND_PARAM} must name a pivot; got {type(raw_kind).__name__}")
    kind = resolve_pivot_by_name(raw_kind)
    template = PIVOT_TEMPLATES[kind]

    missing = template.required_scopes - context.granted_scopes
    if missing:
        raise ToolViolation(
            f"pivot {kind.value} reads {sorted(scope.value for scope in missing)}, which this "
            "dispatch did not grant"
        )

    supplied = {name: value for name, value in context.params.items() if name != PIVOT_KIND_PARAM}
    checked = validate_params(template, supplied, known_ids=pack.node_ids)
    if not checked.ok:
        raise PivotViolation(
            f"parameters for {kind.value} did not validate at execution: {checked.detail}"
        )

    rows = connection.execute(template.sql, bind_values(template, checked.values)).fetchall()

    record = Provenance(
        provenance_id=f"prov-{pack.hops + 1:04d}",
        hop_index=pack.hops + 1,
        pivot_kind=kind,
        source_table=_source_table(template),
        query_template_id=template.template_id,
        template_sha256=template_sha256(template),
        param_hash=param_hash(checked.values),
        params=dict(checked.values),
        retrieval_ts_ist=retrieval.isoformat(),
        row_count=len(rows),
    )
    hop = _map_rows(kind, rows, checked.values, record.provenance_id)

    return pack.with_hop(record, nodes=hop.nodes, edges=hop.edges, timeline=hop.timeline)
