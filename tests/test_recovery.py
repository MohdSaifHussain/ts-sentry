# SPDX-License-Identifier: MIT
"""STEP-04 D5: ground-truth network recovery at a pivot budget.

The benchmark at the bottom is STEP-04 3.5: seed-42 networks, recovery at 5, 10
and 20 pivots per threat class, driven end to end through real evidence turns
with the offline stub. It produces the table that lands in the STEP-04 Outcome.

Everything above it checks the metric's definition rather than its value,
because a metric whose arithmetic nobody checked is a number, not a
measurement. The two that matter most:

* a subject with no planted ring returns ``None`` rather than a zero, so
  unwinnable cases cannot drag an average down;
* the seed is excluded from the denominator, so an investigation cannot score
  for recovering the entity it started from.
"""

from collections.abc import Iterator
from datetime import datetime, timedelta

import duckdb
import numpy as np
import pytest

from ts_sentry.agents.evidence.pack import EvidencePack, Provenance
from ts_sentry.data.enums import EntityKind, ThreatClass
from ts_sentry.data.generator import build_dataset
from ts_sentry.data.population import BuildConfig
from ts_sentry.data.store import persist_dataset
from ts_sentry.data.tz import IST
from ts_sentry.governance.canonical import digest_fields
from ts_sentry.governance.ledger import Ledger
from ts_sentry.measurement.recovery import (
    PACK_NODE_KINDS,
    RingMembership,
    read_ring_membership,
    recovery_for_pack,
    recovery_table,
)
from ts_sentry.orchestrator.adapter import RetryPolicy, StubAdapter
from ts_sentry.orchestrator.core import FixedClock, Session
from ts_sentry.orchestrator.evidence_turn import run_evidence_turn, stub_evidence_responder
from ts_sentry.orchestrator.fleet import PHASE_FOUR_CHECKS, default_mandates
from ts_sentry.orchestrator.pivots import PIVOT_TEMPLATES, PivotKind, template_sha256
from ts_sentry.orchestrator.review import ScriptedReviewer
from ts_sentry.orchestrator.toolspec import ToolResources

_START = datetime(2026, 7, 31, 14, 30, tzinfo=IST)
_TS = _START.isoformat()
_BUDGETS = (5, 10, 20)


class _NoSleep:
    def sleep(self, seconds: float) -> None:  # pragma: no cover - the stub never retries
        raise AssertionError("the stub adapter does not retry")


@pytest.fixture(scope="module")
def dataset() -> Iterator[duckdb.DuckDBPyConnection]:
    con = duckdb.connect()
    persist_dataset(con, build_dataset(BuildConfig(seed=42, scale=1)))
    yield con
    con.close()


@pytest.fixture(scope="module")
def membership(dataset: duckdb.DuckDBPyConnection) -> RingMembership:
    return read_ring_membership(dataset)


def _provenance(hop: int) -> Provenance:
    template = PIVOT_TEMPLATES[PivotKind.INFRA_OVERLAP]
    return Provenance(
        provenance_id=f"prov-{hop:04d}",
        hop_index=hop,
        pivot_kind=PivotKind.INFRA_OVERLAP,
        source_table="main.infra_hint",
        query_template_id=template.template_id,
        template_sha256=template_sha256(template),
        param_hash=digest_fields("test", str(hop)),
        params={},
        retrieval_ts_ist=_TS,
        row_count=1,
    )


def _pack_with(subject: str, discoveries: dict[int, list[str]]) -> EvidencePack:
    """A pack that found the named entities at the named hops."""
    from ts_sentry.agents.evidence.pack import EvidenceNode

    pack = EvidencePack.seed("case-0000", subject, EntityKind.CHANNEL, _TS)
    for hop in sorted(discoveries):
        record = _provenance(hop)
        pack = pack.with_hop(
            record,
            nodes=tuple(
                EvidenceNode(
                    node_id=found,
                    kind=EntityKind.ACCOUNT,
                    provenance_id=record.provenance_id,
                    attributes={},
                )
                for found in discoveries[hop]
            ),
        )
    return pack


# --------------------------------------------------------------------------
# Reading sealed ground truth
# --------------------------------------------------------------------------


def test_ring_membership_reads_only_planted_networks(membership: RingMembership) -> None:
    """A benign entity has a label but no network. Including it would put every
    unaffiliated account into a denominator it does not belong in."""
    assert membership.ring_of
    assert all(ring for ring in membership.ring_of.values())
    for ring, threat in membership.threat_of.items():
        assert threat is not ThreatClass.BENIGN, ring


def test_every_ring_member_resolves_to_its_ring_and_back(membership: RingMembership) -> None:
    for ring, members in membership.members.items():
        assert members
        for entity in members:
            assert membership.ring_for(entity) == ring


# --------------------------------------------------------------------------
# The definition
# --------------------------------------------------------------------------


def test_a_subject_with_no_planted_ring_returns_nothing(membership: RingMembership) -> None:
    """Not a zero. "Nothing to find" and "failed to find it" are different
    results, and averaging them together understates the second."""
    pack = _pack_with("chan_000000", {})

    if membership.ring_for("chan_000000") is None:
        assert recovery_for_pack(pack, membership, _BUDGETS) is None


def test_the_seed_is_excluded_from_the_denominator(membership: RingMembership) -> None:
    """Recovering the entity you started from measures nothing: it was in the
    pack before the agent did anything."""
    ring, members = next(iter(membership.members.items()))
    subject = sorted(members)[0]
    pack = _pack_with(subject, {})

    result = recovery_for_pack(pack, membership, _BUDGETS)

    assert result is not None
    assert result.ring_size == len(members) - 1
    assert result.recovered[5] == 0
    assert result.fraction_of_ring(5) == 0.0


def test_recovery_counts_only_members_found_within_the_budget(
    membership: RingMembership,
) -> None:
    ring, members = next(
        (ring, members) for ring, members in membership.members.items() if len(members) >= 4
    )
    ordered = sorted(members)
    subject, first, second, third = ordered[0], ordered[1], ordered[2], ordered[3]

    pack = _pack_with(subject, {1: [first], 7: [second], 15: [third]})
    result = recovery_for_pack(pack, membership, _BUDGETS)

    assert result is not None
    assert result.recovered[5] == 1
    assert result.recovered[10] == 2
    assert result.recovered[20] == 3


def test_entities_outside_the_ring_do_not_count(membership: RingMembership) -> None:
    """Recovery is of *this* network, not of entities in general. A pack that
    dragged in half the platform must not score for it."""
    ring, members = next(
        (ring, members) for ring, members in membership.members.items() if len(members) >= 2
    )
    ordered = sorted(members)
    outsider = next(entity for entity in membership.ring_of if membership.ring_for(entity) != ring)

    pack = _pack_with(ordered[0], {1: [ordered[1], outsider]})
    result = recovery_for_pack(pack, membership, _BUDGETS)

    assert result is not None
    assert result.recovered[5] == 1


def test_the_structural_ceiling_is_reported_alongside_the_ring_size(
    membership: RingMembership,
) -> None:
    """A ring of mostly comments has a recovery ceiling well below 1.0 however
    well the agent performs, because a comment enters a pack as a timeline event
    rather than as a node. Reporting recovery without the ceiling would invite
    the reader to blame the agent for a structural bound."""
    for members in membership.members.values():
        subject = sorted(members)[0]
        result = recovery_for_pack(_pack_with(subject, {}), membership, _BUDGETS)
        assert result is not None
        assert result.reachable_size <= result.ring_size
        expected = sum(
            1 for entity in members - {subject} if membership.kinds.get(entity) in PACK_NODE_KINDS
        )
        assert result.reachable_size == expected


def test_fractions_are_zero_rather_than_undefined_on_an_empty_denominator() -> None:
    """A single-entity ring has nothing else to find. The metric reports zero
    rather than dividing by zero, and the ceiling says why."""
    membership = RingMembership(
        ring_of={"acct_1": "ring_x"},
        threat_of={"ring_x": ThreatClass.T01_COMMENT_SPAM_RING},
        members={"ring_x": frozenset({"acct_1"})},
        kinds={"acct_1": EntityKind.ACCOUNT},
    )

    result = recovery_for_pack(_pack_with("acct_1", {}), membership, _BUDGETS)

    assert result is not None
    assert result.ring_size == 0
    assert result.fraction_of_ring(5) == 0.0
    assert result.fraction_of_reachable(5) == 0.0


def test_a_table_reports_unwinnable_cases_separately(membership: RingMembership) -> None:
    """A table showing only the winnable cases would flatter the result by
    hiding how many investigations had no network to find."""
    ring, members = next(iter(membership.members.items()))
    benign = next(
        entity
        for entity in ("chan_000000", "chan_000001", "chan_000002")
        if membership.ring_for(entity) is None
    )

    table = recovery_table(
        [_pack_with(sorted(members)[0], {}), _pack_with(benign, {})],
        membership,
        _BUDGETS,
    )

    assert table.cases_without_a_ring == 1
    assert table.case_count == 1


def test_a_table_needs_at_least_one_budget(membership: RingMembership) -> None:
    with pytest.raises(ValueError, match="at least one budget"):
        recovery_table([], membership, ())


# --------------------------------------------------------------------------
# STEP-04 3.5: the seed-42 benchmark
# --------------------------------------------------------------------------


def _investigate(
    connection: duckdb.DuckDBPyConnection,
    case_id: str,
    subject: str,
    budget: int,
    kind: EntityKind = EntityKind.CHANNEL,
) -> EvidencePack:
    """One full evidence turn, offline, against the real build."""
    session = Session(
        session_id=f"session-{case_id}",
        analyst_id="benchmark",
        ledger=Ledger(duckdb.connect(":memory:")),
        clock=FixedClock(_START, timedelta(seconds=1)),
        mandates=default_mandates(),
        dataset_digest="a" * 64,
    )
    session.open()
    turn = run_evidence_turn(
        session,
        StubAdapter(responder=stub_evidence_responder),
        case_id=case_id,
        subject_id=subject,
        reviewer=ScriptedReviewer(reviewer_id="benchmark"),
        resources=ToolResources(connection=connection, seed=42),
        checks=PHASE_FOUR_CHECKS,
        policy=RetryPolicy(),
        rng=np.random.default_rng(42),
        sleeper=_NoSleep(),
        subject_kind=kind,
        max_hops=budget,
    )
    return turn.pack


def _benchmark_subjects(
    connection: duckdb.DuckDBPyConnection, membership: RingMembership
) -> list[tuple[str, str, EntityKind]]:
    """One subject per threat class, chosen from sealed ground truth.

    Choosing *which* cases to benchmark from ground truth is measurement-side
    and legitimate: it is how you measure recovery on known networks. The agent
    is handed only a subject id and never sees a label, which is the property
    that keeps the number meaningful.

    A channel is preferred as the seed, because that is what a triage queue
    hands an analyst. Two threat classes have none: T-01 and T-03 operate
    through *commenting* accounts and publish nothing, which is the same
    structural fact STEP-03 found when the queue turned out to be blind to
    exactly the rings that matter most. Seeding those on an account is what
    keeps them in the table rather than silently absent from it.
    """
    channels = {
        str(row[0]) for row in connection.execute("SELECT channel_id FROM main.channel").fetchall()
    }
    chosen: dict[ThreatClass, tuple[str, str, EntityKind]] = {}
    for pass_kind in (EntityKind.CHANNEL, EntityKind.ACCOUNT):
        for index, entity in enumerate(sorted(membership.ring_of)):
            if membership.kinds.get(entity) is not pass_kind:
                continue
            if pass_kind is EntityKind.CHANNEL and entity not in channels:
                continue
            threat = membership.threat_of[membership.ring_of[entity]]
            if threat not in chosen:
                chosen[threat] = (f"case-{index:04d}", entity, pass_kind)
    return [chosen[threat] for threat in sorted(chosen, key=lambda item: item.value)]


def test_recovery_at_budget_is_reportable_for_seed_42(
    dataset: duckdb.DuckDBPyConnection, membership: RingMembership
) -> None:
    """STEP-04's exit criterion, and the table that lands in the Outcome.

    Asserts that the metric is *reportable* and internally consistent, not that
    it clears a threshold. There is no target recovery rate in the STEP file and
    inventing one here would be manufacturing a bar to clear.
    """
    subjects = _benchmark_subjects(dataset, membership)
    assert len(subjects) == 7, (
        f"every planted threat class should be represented; got {[threat for threat in subjects]}"
    )

    packs = [
        _investigate(dataset, case_id, subject, 20, kind) for case_id, subject, kind in subjects
    ]
    table = recovery_table(packs, membership, _BUDGETS)

    assert table.case_count == len(subjects)
    for results in table.per_class.values():
        for result in results:
            assert result.recovered[5] <= result.recovered[10] <= result.recovered[20]
            assert result.recovered[20] <= result.ring_size
            assert 0.0 <= result.fraction_of_ring(20) <= 1.0

    rendered = table.render()
    assert "cases whose subject carried no planted ring" in rendered
    print("\n" + rendered)


def test_recovery_saturates_before_the_smallest_reported_budget(
    dataset: duckdb.DuckDBPyConnection, membership: RingMembership
) -> None:
    """A limitation, asserted as a passing test.

    The three columns of the STEP-04 3.5 table are identical for every threat
    class on this build, and the reason is the stub rather than the pivot
    vocabulary: it reaches the accounts, pivots on their shared metadata and
    infrastructure, and has then asked everything it knows how to ask. Every
    later hop re-runs a question whose answer is already in the pack, so the
    budget axis measures nothing.

    That is worth stating in a test rather than a comment, in the shape STEP-02
    used for tail truncation: the day a better strategy makes recovery grow
    between 5 and 20 pivots, this test fails and forces the claim in the STEP-04
    Outcome to be rewritten rather than quietly outliving its own truth.

    What is *not* claimed: that the pivot vocabulary saturates. Nothing here
    measures what a real model would find, and the stub is deterministic by
    design rather than clever.
    """
    subjects = _benchmark_subjects(dataset, membership)
    packs = [
        _investigate(dataset, case_id, subject, 20, kind) for case_id, subject, kind in subjects
    ]
    table = recovery_table(packs, membership, _BUDGETS)

    improved = [
        result
        for results in table.per_class.values()
        for result in results
        if result.recovered[20] > result.recovered[5]
    ]

    assert improved == [], (
        "recovery now grows with budget, which is better than when this test was "
        "written. Rewrite it and the STEP-04 Outcome's saturation note rather than "
        f"deleting either: {[(r.threat_class.value, r.recovered) for r in improved]}"
    )


def test_the_mandate_permits_every_budget_the_table_reports(
    dataset: duckdb.DuckDBPyConnection, membership: RingMembership
) -> None:
    """A reported budget the mandate forbids is not a measurement.

    ``max_steps`` was 12 while STEP-04 3.5 reports at 20, so the largest column
    could never have differed from the 12-pivot result and no reader would have
    known. Pinned here rather than only in the mandate's docstring, because the
    two numbers live in different files and nothing else would notice them
    drifting apart.
    """
    from ts_sentry.orchestrator.fleet import EVIDENCE_MANDATE

    assert EVIDENCE_MANDATE.max_steps >= max(_BUDGETS)
