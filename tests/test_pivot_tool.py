# SPDX-License-Identifier: MIT
"""STEP-04 D2: the pivot handler, exercised on every kind.

Every one of the five pivots is executed here against a real seed-42 build and
its rows folded into a pack. That is the point of the file rather than a
completeness gesture: the offline stub proposes three of the five, so
``TEMPORAL_CORRELATION`` and ``ENGAGEMENT_EDGE`` had row mappings that no test
had ever run. A mapping that has never executed is a mapping whose column order
is a guess, and it would have failed the first time a real model proposed one.

The handler's refusal paths are covered here too. They raise rather than
returning a value, and that asymmetry is deliberate: by the time a proposal
reaches this function it has been parsed, resolved, citation-checked,
parameter-checked and approved by an analyst, so anything still wrong is the
orchestrator dispatching what it already rejected. Dispatch reports that as
FAILED rather than as a refusal, because a defect must not look like the
governance layer working.
"""

from collections.abc import Iterator
from datetime import datetime

import duckdb
import pytest

from ts_sentry.agents.evidence.pack import EvidenceNode, EvidencePack, Provenance
from ts_sentry.data.enums import EntityKind
from ts_sentry.data.generator import build_dataset
from ts_sentry.data.population import BuildConfig
from ts_sentry.data.store import persist_dataset
from ts_sentry.data.tz import IST
from ts_sentry.governance.canonical import digest_fields
from ts_sentry.governance.scopes import DataScope
from ts_sentry.orchestrator.pack_gate import evidence_pack_check
from ts_sentry.orchestrator.pivot_tool import PIVOT_KIND_PARAM, run_parameterized_pivot
from ts_sentry.orchestrator.pivots import (
    PIVOT_TEMPLATES,
    PivotKind,
    PivotViolation,
    template_sha256,
)
from ts_sentry.orchestrator.toolspec import ToolContext, ToolResources, ToolViolation

_NOW = datetime(2026, 7, 31, 14, 30, tzinfo=IST)
CASE = "case-0000"


@pytest.fixture(scope="module")
def connection() -> Iterator[duckdb.DuckDBPyConnection]:
    con = duckdb.connect()
    persist_dataset(con, build_dataset(BuildConfig(seed=42, scale=1)))
    yield con
    con.close()


@pytest.fixture(scope="module")
def fixture_ids(connection: duckdb.DuckDBPyConnection) -> tuple[str, str, str, int]:
    """A channel with comments and engagement, its owner, a shared-signal
    subject, and an anchor instant. Derived from the build, never hard-coded."""
    row = connection.execute(
        "SELECT v.channel_id, MIN(ch.account_id), epoch_ms(MIN(cm.posted_ts)) "
        "FROM main.comment cm "
        "JOIN main.video v ON cm.video_id = v.video_id "
        "JOIN main.channel ch ON v.channel_id = ch.channel_id "
        "GROUP BY v.channel_id ORDER BY COUNT(*) DESC, v.channel_id LIMIT 1"
    ).fetchone()
    assert row is not None
    subject = connection.execute(
        "SELECT subject_id FROM main.infra_hint WHERE (signal_type, signal_value) IN "
        "(SELECT signal_type, signal_value FROM main.infra_hint "
        " GROUP BY 1, 2 HAVING COUNT(DISTINCT subject_id) > 1) "
        "ORDER BY subject_id LIMIT 1"
    ).fetchone()
    assert subject is not None
    return str(row[0]), str(row[1]), str(subject[0]), int(row[2])


def _seed_pack(*node_ids: str) -> EvidencePack:
    """A pack seeded on the first id, with the rest folded in as known nodes.

    Entity-id parameters must resolve to a node already in the pack, so a test
    of a pivot on an account has to have reached that account first. Building
    that precondition explicitly keeps the test honest about it.
    """
    pack = EvidencePack.seed(CASE, node_ids[0], EntityKind.CHANNEL, _NOW.isoformat())
    if len(node_ids) == 1:
        return pack

    template = PIVOT_TEMPLATES[PivotKind.ACCOUNT_LINK]
    record = Provenance(
        provenance_id="prov-0001",
        hop_index=1,
        pivot_kind=PivotKind.ACCOUNT_LINK,
        source_table="main.channel",
        query_template_id=template.template_id,
        template_sha256=template_sha256(template),
        param_hash=digest_fields("test", "seed"),
        params={},
        retrieval_ts_ist=_NOW.isoformat(),
        row_count=len(node_ids) - 1,
    )
    return pack.with_hop(
        record,
        nodes=tuple(
            EvidenceNode(
                node_id=node_id,
                kind=EntityKind.ACCOUNT,
                provenance_id=record.provenance_id,
                attributes={},
            )
            for node_id in node_ids[1:]
        ),
    )


def _run(
    connection: duckdb.DuckDBPyConnection,
    pack: EvidencePack,
    kind: PivotKind,
    params: dict[str, object],
    *,
    scopes: frozenset[DataScope] | None = None,
) -> object:
    template = PIVOT_TEMPLATES[kind]
    context = ToolContext(
        agent_id="evidence",
        granted_scopes=template.required_scopes if scopes is None else scopes,
        params={PIVOT_KIND_PARAM: kind.value, **params},
        resources=ToolResources(connection=connection, seed=42, pack=pack, retrieval_ts=_NOW),
    )
    return run_parameterized_pivot(context)


def _params_for(
    kind: PivotKind, channel: str, account: str, subject: str, anchor: int
) -> dict[str, object]:
    match kind:
        case PivotKind.SHARED_METADATA:
            return {"account_id": account, "metadata_field": "any", "limit": 25}
        case PivotKind.TEMPORAL_CORRELATION:
            return {
                "channel_id": channel,
                "anchor_epoch_ms": anchor,
                "window_hours": 24,
                "limit": 25,
            }
        case PivotKind.ENGAGEMENT_EDGE:
            return {"channel_id": channel, "kind": "any", "min_events": 1, "limit": 25}
        case PivotKind.INFRA_OVERLAP:
            return {"subject_id": subject, "signal_type": "any", "limit": 25}
        case PivotKind.ACCOUNT_LINK:
            return {"channel_id": channel, "min_comments": 1, "limit": 25}


# --------------------------------------------------------------------------
# Every kind, executed
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", list(PivotKind))
def test_every_pivot_kind_executes_and_grows_a_valid_pack(
    connection: duckdb.DuckDBPyConnection, fixture_ids: tuple[str, str, str, int], kind: PivotKind
) -> None:
    """The gap this file exists to close.

    Two of the five row mappings had never run, because the offline stub
    proposes three. A mapping that has never executed is a mapping whose column
    order is a guess.
    """
    channel, account, subject, anchor = fixture_ids
    pack = _seed_pack(channel, account, subject)

    grown = _run(connection, pack, kind, _params_for(kind, channel, account, subject, anchor))

    assert isinstance(grown, EvidencePack)
    assert grown.hops == pack.hops + 1
    assert grown.provenance[-1].pivot_kind is kind
    assert set(pack.node_ids) <= set(grown.node_ids)
    # The pack the ASSEMBLE gate will see must pass it, for every kind.
    assert evidence_pack_check(grown) == ()


@pytest.mark.parametrize("kind", list(PivotKind))
def test_the_provenance_record_names_the_query_that_produced_it(
    connection: duckdb.DuckDBPyConnection, fixture_ids: tuple[str, str, str, int], kind: PivotKind
) -> None:
    channel, account, subject, anchor = fixture_ids
    params = _params_for(kind, channel, account, subject, anchor)

    grown = _run(connection, _seed_pack(channel, account, subject), kind, params)

    assert isinstance(grown, EvidencePack)
    record = grown.provenance[-1]
    assert record.query_template_id == PIVOT_TEMPLATES[kind].template_id
    assert record.params == params
    assert record.hop_index == grown.hops
    assert record.retrieval_ts_ist == _NOW.isoformat()


def test_a_zero_row_pivot_still_records_that_it_ran(
    connection: duckdb.DuckDBPyConnection, fixture_ids: tuple[str, str, str, int]
) -> None:
    """A window with nothing in it is an answer, and the pack keeps it."""
    channel, account, subject, _anchor = fixture_ids

    grown = _run(
        connection,
        _seed_pack(channel, account, subject),
        PivotKind.TEMPORAL_CORRELATION,
        {"channel_id": channel, "anchor_epoch_ms": 0, "window_hours": 1, "limit": 25},
    )

    assert isinstance(grown, EvidencePack)
    assert grown.provenance[-1].row_count == 0
    assert grown.hops == 2


def test_rerunning_one_pivot_does_not_duplicate_what_it_already_found(
    connection: duckdb.DuckDBPyConnection, fixture_ids: tuple[str, str, str, int]
) -> None:
    """Edge and event ids are natural keys, so a repeated question contributes
    the same records and they are deduplicated against the pack rather than
    accumulating a second copy of an unchanged fact."""
    channel, account, subject, anchor = fixture_ids
    params = _params_for(PivotKind.ACCOUNT_LINK, channel, account, subject, anchor)

    once = _run(connection, _seed_pack(channel, account, subject), PivotKind.ACCOUNT_LINK, params)
    assert isinstance(once, EvidencePack)
    twice = _run(connection, once, PivotKind.ACCOUNT_LINK, params)

    assert isinstance(twice, EvidencePack)
    assert twice.node_ids == once.node_ids
    assert {edge.edge_id for edge in twice.edges} == {edge.edge_id for edge in once.edges}
    assert twice.hops == once.hops + 1  # the hop is still recorded


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_the_handler_refuses_without_the_pack_it_extends(
    connection: duckdb.DuckDBPyConnection, fixture_ids: tuple[str, str, str, int]
) -> None:
    """The pack is a resource, not a parameter. An agent that could supply it
    could supply one containing entities it invented, and every "must already
    be in the pack" check would then be checking the agent's claims against
    themselves."""
    channel, _account, _subject, _anchor = fixture_ids
    context = ToolContext(
        agent_id="evidence",
        granted_scopes=PIVOT_TEMPLATES[PivotKind.ACCOUNT_LINK].required_scopes,
        params={PIVOT_KIND_PARAM: PivotKind.ACCOUNT_LINK.value, "channel_id": channel},
        resources=ToolResources(connection=connection, seed=42, retrieval_ts=_NOW),
    )

    with pytest.raises(ToolViolation, match="evidence pack"):
        run_parameterized_pivot(context)


def test_the_handler_refuses_without_the_session_timestamp(
    connection: duckdb.DuckDBPyConnection, fixture_ids: tuple[str, str, str, int]
) -> None:
    """Nothing in this system reads the clock behind its caller's back."""
    channel, account, subject, _anchor = fixture_ids
    context = ToolContext(
        agent_id="evidence",
        granted_scopes=PIVOT_TEMPLATES[PivotKind.ACCOUNT_LINK].required_scopes,
        params={PIVOT_KIND_PARAM: PivotKind.ACCOUNT_LINK.value, "channel_id": channel},
        resources=ToolResources(
            connection=connection, seed=42, pack=_seed_pack(channel, account, subject)
        ),
    )

    with pytest.raises(ToolViolation, match="timestamp"):
        run_parameterized_pivot(context)


def test_the_handler_refuses_a_pivot_whose_scopes_were_not_granted(
    connection: duckdb.DuckDBPyConnection, fixture_ids: tuple[str, str, str, int]
) -> None:
    """Least privilege per template, on top of dispatch's per-tool check.

    The tool declares all six entity scopes because the agent may run any
    pivot; this is where a single pivot is held to only the tables it reads.
    """
    channel, account, subject, anchor = fixture_ids

    with pytest.raises(ToolViolation, match="did not grant"):
        _run(
            connection,
            _seed_pack(channel, account, subject),
            PivotKind.ACCOUNT_LINK,
            _params_for(PivotKind.ACCOUNT_LINK, channel, account, subject, anchor),
            scopes=frozenset({DataScope.CHANNEL}),
        )


def test_the_handler_revalidates_parameters_at_execution(
    connection: duckdb.DuckDBPyConnection, fixture_ids: tuple[str, str, str, int]
) -> None:
    """The fail-closed boundary.

    ``proposal_check`` already refused this before the analyst saw it, so
    reaching here means the orchestrator dispatched something it had rejected.
    That is a defect rather than an outcome, and it raises so dispatch reports
    FAILED rather than a refusal.
    """
    channel, account, subject, _anchor = fixture_ids

    with pytest.raises(PivotViolation, match="did not validate at execution"):
        _run(
            connection,
            _seed_pack(channel, account, subject),
            PivotKind.ACCOUNT_LINK,
            {"channel_id": channel, "min_comments": 1, "limit": 10_000},
        )


def test_an_unknown_pivot_name_is_refused(
    connection: duckdb.DuckDBPyConnection, fixture_ids: tuple[str, str, str, int]
) -> None:
    channel, account, subject, _anchor = fixture_ids
    context = ToolContext(
        agent_id="evidence",
        granted_scopes=frozenset(DataScope),
        params={PIVOT_KIND_PARAM: "sealed._labels"},
        resources=ToolResources(
            connection=connection,
            seed=42,
            pack=_seed_pack(channel, account, subject),
            retrieval_ts=_NOW,
        ),
    )

    with pytest.raises(PivotViolation, match="no PivotKind member resolves"):
        run_parameterized_pivot(context)


def test_a_missing_pivot_kind_is_refused(
    connection: duckdb.DuckDBPyConnection, fixture_ids: tuple[str, str, str, int]
) -> None:
    channel, account, subject, _anchor = fixture_ids
    context = ToolContext(
        agent_id="evidence",
        granted_scopes=frozenset(DataScope),
        params={"channel_id": channel},
        resources=ToolResources(
            connection=connection,
            seed=42,
            pack=_seed_pack(channel, account, subject),
            retrieval_ts=_NOW,
        ),
    )

    with pytest.raises(ToolViolation, match="must name a pivot"):
        run_parameterized_pivot(context)
