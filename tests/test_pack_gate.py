# SPDX-License-Identifier: MIT
"""STEP-04 D4: the ASSEMBLE gate's checker.

Two kinds of test here, and the difference matters.

The reachable checks (a non-pack artifact, a cited template this build does not
have, an edited template, a gap in the hop indices) are things the type cannot
know, so they are tested by constructing packs that are perfectly valid as
objects and wrong as evidence.

The unreachable checks (dangling edge, orphan provenance) cannot be produced
through ``EvidencePack(...)``, which refuses to construct them. Testing them
requires bypassing ``__post_init__`` deliberately, which is what
``_unchecked_pack`` does and why it says so in its name. That follows STEP-02's
handling of its two unreachable branches: give them a direct test with a
docstring explaining why they have no public caller, rather than deleting a
guard because the type currently makes it redundant.
"""

from datetime import datetime

import duckdb
import pytest

from ts_sentry.agents.evidence.pack import (
    SEED_TEMPLATE_ID,
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
from ts_sentry.governance.gates import FailureCode, GateChecks, GateDecision, run_gate
from ts_sentry.governance.ledger import Ledger, OrchestratorToken
from ts_sentry.governance.mandate import AgentId, Consequence
from ts_sentry.orchestrator.pack_gate import evidence_pack_check, pack_checker
from ts_sentry.orchestrator.pivots import PIVOT_TEMPLATES, PivotKind, template_sha256

TS = datetime(2026, 7, 31, 14, 30, tzinfo=IST).isoformat()
CASE = "case-0000"
SUBJECT = "chan_000016"


@pytest.fixture
def ledger() -> Ledger:
    return Ledger(duckdb.connect(":memory:"))


def _provenance(hop: int, *, kind: PivotKind = PivotKind.INFRA_OVERLAP) -> Provenance:
    template = PIVOT_TEMPLATES[kind]
    return Provenance(
        provenance_id=f"prov-{hop:04d}",
        hop_index=hop,
        pivot_kind=kind,
        source_table="main.infra_hint",
        query_template_id=template.template_id,
        template_sha256=template_sha256(template),
        param_hash=digest_fields("test", str(hop)),
        params={},
        retrieval_ts_ist=TS,
        row_count=1,
    )


def _node(node_id: str, provenance_id: str) -> EvidenceNode:
    return EvidenceNode(
        node_id=node_id,
        kind=EntityKind.ACCOUNT,
        provenance_id=provenance_id,
        attributes={},
    )


def _valid_pack() -> EvidencePack:
    record = _provenance(1)
    return EvidencePack.seed(CASE, SUBJECT, EntityKind.CHANNEL, TS).with_hop(
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


def _unchecked_pack(**overrides: object) -> EvidencePack:
    """Build a pack **without running its invariants**.

    The only way to exercise the gate's referential-integrity and
    provenance-completeness branches, because ``EvidencePack(...)`` refuses to
    produce a pack that violates either. Deliberately ugly and deliberately
    named, so nothing in the source tree is tempted to reach for it: production
    code constructs packs the checked way, and this exists to prove the gate
    still catches what the type currently prevents.
    """
    base = _valid_pack()
    pack = object.__new__(EvidencePack)
    for field in ("case_id", "subject_id", "nodes", "edges", "timeline", "provenance"):
        object.__setattr__(pack, field, overrides.get(field, getattr(base, field)))
    return pack


# --------------------------------------------------------------------------
# A well-formed pack passes
# --------------------------------------------------------------------------


def test_a_well_formed_pack_passes() -> None:
    assert evidence_pack_check(_valid_pack()) == ()


def test_a_seeded_pack_with_no_hops_passes() -> None:
    """An investigation that has not pivoted yet is not a defective one."""
    assert evidence_pack_check(EvidencePack.seed(CASE, SUBJECT, EntityKind.CHANNEL, TS)) == ()


def test_a_zero_row_hop_passes() -> None:
    """The provenance record of a pivot that found nothing is not an orphan
    problem; it is the record of a question answered in the negative."""
    pack = EvidencePack.seed(CASE, SUBJECT, EntityKind.CHANNEL, TS).with_hop(_provenance(1))

    assert evidence_pack_check(pack) == ()


# --------------------------------------------------------------------------
# Checks the type cannot make
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "artifact",
    [None, "a pack", 42, {"nodes": []}, [], object()],
)
def test_an_artifact_that_is_not_a_pack_is_refused(artifact: object) -> None:
    failures = evidence_pack_check(artifact)

    assert [failure.code for failure in failures] == [FailureCode.SCHEMA_INVALID]
    assert "expected an EvidencePack" in failures[0].detail


def test_a_pack_citing_a_template_this_build_does_not_have_is_refused() -> None:
    """Provenance that resolves to nothing runnable is not provenance."""
    record = Provenance(
        provenance_id="prov-0001",
        hop_index=1,
        pivot_kind=PivotKind.INFRA_OVERLAP,
        source_table="main.infra_hint",
        query_template_id="pivot.removed_in_a_later_phase.v1",
        template_sha256=digest_fields("test", "a"),
        param_hash=digest_fields("test", "b"),
        params={},
        retrieval_ts_ist=TS,
        row_count=0,
    )
    pack = EvidencePack.seed(CASE, SUBJECT, EntityKind.CHANNEL, TS).with_hop(record)

    failures = evidence_pack_check(pack)

    assert [failure.code for failure in failures] == [FailureCode.SCHEMA_INVALID]
    assert "this build does not have" in failures[0].detail


def test_a_pack_whose_template_text_has_since_changed_is_refused() -> None:
    """The digest is the point of recording a digest.

    A template edited after a pack was gathered means the pack's records were
    produced by SQL this build can no longer show. That is exactly the drift
    ``template_sha256`` exists to surface, and it is invisible to an id
    comparison alone.
    """
    template = PIVOT_TEMPLATES[PivotKind.ACCOUNT_LINK]
    stale = Provenance(
        provenance_id="prov-0001",
        hop_index=1,
        pivot_kind=PivotKind.ACCOUNT_LINK,
        source_table="main.channel",
        query_template_id=template.template_id,
        template_sha256=digest_fields("ts-sentry/pivot-template/v1", "an older text"),
        param_hash=digest_fields("test", "b"),
        params={},
        retrieval_ts_ist=TS,
        row_count=0,
    )
    pack = EvidencePack.seed(CASE, SUBJECT, EntityKind.CHANNEL, TS).with_hop(stale)

    failures = evidence_pack_check(pack)

    assert [failure.code for failure in failures] == [FailureCode.SCHEMA_INVALID]
    assert "was edited after these records were gathered" in failures[0].detail


def test_the_seed_record_is_not_compared_against_the_template_registry() -> None:
    """It is not a query, so there is nothing to compare it to. The nullable
    ``pivot_kind`` is tied to this id in both directions by the type, so this
    exemption cannot be used to smuggle a fabricated record past the check."""
    pack = EvidencePack.seed(CASE, SUBJECT, EntityKind.CHANNEL, TS)

    assert pack.provenance[0].query_template_id == SEED_TEMPLATE_ID
    assert evidence_pack_check(pack) == ()


def test_a_gap_in_the_hop_indices_is_refused() -> None:
    """A gap means a hop's provenance was dropped, and a pack that has
    forgotten one of its own hops cannot answer "what did you hold at five
    pivots", which is the number this phase reports."""
    pack = EvidencePack.seed(CASE, SUBJECT, EntityKind.CHANNEL, TS).with_hop(_provenance(3))

    failures = evidence_pack_check(pack)

    assert [failure.code for failure in failures] == [FailureCode.SCHEMA_INVALID]
    assert "not contiguous from zero" in failures[0].detail


def test_every_failure_is_reported_rather_than_the_first() -> None:
    """An analyst reading a GATE_REJECTION is trying to understand what went
    wrong, not to fix one thing and rerun to discover the next."""
    first = Provenance(
        provenance_id="prov-0005",
        hop_index=5,
        pivot_kind=PivotKind.INFRA_OVERLAP,
        source_table="main.infra_hint",
        query_template_id="pivot.not_in_this_build.v1",
        template_sha256=digest_fields("test", "a"),
        param_hash=digest_fields("test", "b"),
        params={},
        retrieval_ts_ist=TS,
        row_count=0,
    )
    pack = EvidencePack.seed(CASE, SUBJECT, EntityKind.CHANNEL, TS).with_hop(first)

    failures = evidence_pack_check(pack)

    assert len(failures) == 2
    assert {failure.code for failure in failures} == {FailureCode.SCHEMA_INVALID}
    assert any("this build does not have" in failure.detail for failure in failures)
    assert any("not contiguous" in failure.detail for failure in failures)


# --------------------------------------------------------------------------
# Checks the type currently makes unreachable, kept and tested directly
# --------------------------------------------------------------------------


def test_a_dangling_edge_is_refused_by_the_gate_as_well_as_by_the_type() -> None:
    """No public caller can produce this input.

    ``EvidencePack.__post_init__`` refuses a pack whose edge names an unknown
    node, so this branch is unreachable today. It is kept because the gate
    receives an ``object`` from a tool handler rather than a guaranteed pack:
    if a future change ever builds a pack by a route that skips the
    constructor, this is the check that remains.
    """
    base = _valid_pack()
    orphan_edge = EvidenceEdge(
        edge_id="edge-2",
        source_id=SUBJECT,
        target_id="acct_9999999",
        relation=EdgeRelation.SHARES_INFRA_SIGNAL,
        provenance_id="prov-0001",
        attributes={},
    )

    failures = evidence_pack_check(_unchecked_pack(edges=(*base.edges, orphan_edge)))

    assert [failure.code for failure in failures] == [FailureCode.REFERENTIAL_INTEGRITY]
    assert "acct_9999999" in failures[0].detail


def test_a_subject_missing_from_its_own_pack_is_refused_by_the_gate() -> None:
    """Unreachable through the constructor. See the docstring above."""
    base = _valid_pack()

    failures = evidence_pack_check(
        _unchecked_pack(
            nodes=tuple(node for node in base.nodes if node.node_id != SUBJECT),
            edges=(),
            timeline=(),
        )
    )

    assert [failure.code for failure in failures] == [FailureCode.REFERENTIAL_INTEGRITY]
    assert "is not a node in its own pack" in failures[0].detail


def test_an_orphan_record_is_refused_by_the_gate() -> None:
    """Unreachable through the constructor. See the docstring above."""
    orphan = _node("acct_0000002", "prov-9999")
    base = _valid_pack()

    failures = evidence_pack_check(_unchecked_pack(nodes=(*base.nodes, orphan)))

    assert [failure.code for failure in failures] == [FailureCode.PROVENANCE_INCOMPLETE]
    assert "does not carry" in failures[0].detail


def test_a_pack_with_no_provenance_at_all_is_refused() -> None:
    failures = evidence_pack_check(_unchecked_pack(nodes=(), edges=(), timeline=(), provenance=()))

    assert FailureCode.PROVENANCE_INCOMPLETE in {failure.code for failure in failures}


# --------------------------------------------------------------------------
# Through the real gate pipeline
# --------------------------------------------------------------------------


def test_the_gate_accepts_a_well_formed_pack_and_ledgers_it(ledger: Ledger) -> None:
    outcome = run_gate(
        ledger,
        OrchestratorToken(session_id="s"),
        timestamp_ist=datetime(2026, 7, 31, 14, 30, tzinfo=IST),
        agent_id=AgentId.EVIDENCE,
        mandate_hash=digest_fields("test", "mandate"),
        consequence=Consequence.ASSEMBLE,
        artifact=_valid_pack(),
        checks=GateChecks(assemble=pack_checker(), recommend=pack_checker()),
    )

    assert outcome.decision is GateDecision.ACCEPTED
    assert outcome.failures == ()
    assert [entry.event_type.value for entry in outcome.ledgered] == ["verification_pass"]


def test_the_gate_rejects_a_malformed_pack_and_ledgers_both_events(
    ledger: Ledger,
) -> None:
    """STEP-02 3.3's pair for a gate failure: VERIFICATION_FAIL then
    GATE_REJECTION. Something was verified and failed."""
    outcome = run_gate(
        ledger,
        OrchestratorToken(session_id="s"),
        timestamp_ist=datetime(2026, 7, 31, 14, 30, tzinfo=IST),
        agent_id=AgentId.EVIDENCE,
        mandate_hash=digest_fields("test", "mandate"),
        consequence=Consequence.ASSEMBLE,
        artifact="not a pack",
        checks=GateChecks(assemble=pack_checker(), recommend=pack_checker()),
    )

    assert outcome.decision is GateDecision.REJECTED
    assert [failure.code for failure in outcome.failures] == [FailureCode.SCHEMA_INVALID]
    assert [entry.event_type.value for entry in outcome.ledgered] == [
        "verification_fail",
        "gate_rejection",
    ]
