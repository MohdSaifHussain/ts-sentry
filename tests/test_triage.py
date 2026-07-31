# SPDX-License-Identifier: MIT
"""STEP-03 D5: the triage agent, and STEP-03 3.1/3.2/3.5.

Four requirements meet here:

* 3.1 monotonicity in each component holding the others fixed, and a component
  vector on every row;
* 3.2 the injection fixture corpus causing no behavioral deviation;
* 3.5 rationale verification reusing the STEP-02 verifier with evidence ids =
  score component ids;
* the detection stub reading only allowlisted tables, with severity as a
  heuristic stand-in and no sealed influence.

The 3.2 assertion is stated at its true width in
``test_injected_case_content_cannot_change_what_a_rationale_may_cite``. It is
not a claim that a model resists injection.
"""

from datetime import datetime, timedelta

import duckdb
import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ts_sentry.agents.triage.prompts import (
    TRIAGE_SYSTEM_PROMPT,
    RankedQueue,
    RankedRow,
    triage_instruction,
)
from ts_sentry.agents.triage.rationale import (
    parse_citations,
    parse_rationale_lines,
    render_expected_form,
)
from ts_sentry.agents.triage.scorer import (
    WEIGHTS,
    WEIGHTS_VERSION,
    PriorityScore,
    ScoreComponent,
    component_id,
    score,
    weights_hash,
)
from ts_sentry.data.enums import EntityKind, InfraSignalKind, ProvenanceSignal
from ts_sentry.data.population import Dataset
from ts_sentry.data.schema import AccountMeta, Channel, Comment, InfraHint, Video
from ts_sentry.data.store import persist_dataset
from ts_sentry.data.tz import IST
from ts_sentry.governance.scopes import DataScope, resolve_table
from ts_sentry.orchestrator.detection_stub import (
    DETECTOR_VERSION,
    build_flagged_queue,
    case_records,
    queries,
)
from ts_sentry.orchestrator.rationale_check import verify_rationales
from ts_sentry.orchestrator.toolspec import ToolContext, ToolResources
from ts_sentry.orchestrator.triage_tool import rank_triage_queue

_BASE = datetime(2024, 6, 1, 12, 0, tzinfo=IST)


# --------------------------------------------------------------------------
# 3.1: the deterministic scorer
# --------------------------------------------------------------------------

_UNIT = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


@settings(max_examples=300, deadline=None)
@given(
    component=st.sampled_from(list(ScoreComponent)),
    base=st.fixed_dictionaries({c.value: _UNIT for c in ScoreComponent}),
    delta=st.floats(min_value=1e-12, max_value=1.0),
)
def test_priority_is_monotonic_in_each_component(
    component: ScoreComponent, base: dict[str, float], delta: float
) -> None:
    """STEP-03 3.1, stated at the precision floating point actually offers.

    The requirement says "monotonicity in each component holding others
    fixed". Asserted first as strict inequality, hypothesis found the counter-
    example immediately: raising severity_class from 0.9999999999999999 to
    1.0 is a change of about 1.1e-16, and multiplying it by a weight of 0.4
    puts it below the resolution of the running sum, so the priority is
    bit-identical.

    That is not a defect in the scorer and cannot be fixed in it - it is what
    a weighted sum of floats does. So the property is stated in two parts,
    which together are the honest version of the requirement:

    * priority is **never lower** when a component rises. This holds for every
      input, and it is the part that matters: raising evidence against a case
      can never move it down the queue.
    * priority is **strictly higher** once the increase is large enough to
      survive the weighting, which the threshold below pins at a millionth.
      Nothing in this system distinguishes cases on differences that small;
      the components themselves are ratios of small integer counts.
    """
    raised = dict(base)
    raised[component.value] = min(1.0, base[component.value] + delta)
    actual_delta = raised[component.value] - base[component.value]
    if actual_delta == 0.0:
        return  # already saturated at 1.0; nothing to compare

    low = score("case-0001", **base)
    high = score("case-0001", **raised)

    assert high.priority >= low.priority
    if actual_delta > 1e-6:
        assert high.priority > low.priority


def test_every_weight_is_positive_and_they_sum_to_one() -> None:
    """What makes monotonicity structural rather than tuned, and what makes a
    priority readable as a 0..1 share."""
    assert all(weight > 0 for weight in WEIGHTS.values())
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_every_row_carries_its_component_vector() -> None:
    """ARCHITECTURE 4.1: never a bare number. An analyst who cannot see why a
    case ranked first cannot disagree with the ranking."""
    rendered = score(
        "case-0001", severity_class=1.0, spread=0.5, velocity=0.25, recidivism=0.0
    ).to_json_object()

    components = rendered["components"]
    assert isinstance(components, dict)
    assert set(components) == {c.value for c in ScoreComponent}
    assert rendered["weights_version"] == WEIGHTS_VERSION


def test_a_score_missing_a_component_is_refused() -> None:
    with pytest.raises(ValueError, match="missing components"):
        PriorityScore(
            case_id="case-0001",
            components={ScoreComponent.SPREAD: 0.5},
            priority=0.1,
        )


def test_component_ids_are_namespaced_by_case() -> None:
    """An unqualified ``velocity`` would resolve against every row, so a
    rationale could cite another case's evidence and still verify."""
    assert component_id("case-0007", ScoreComponent.VELOCITY) == "case-0007:velocity"
    assert score(
        "case-0007", severity_class=0.0, spread=0.0, velocity=0.0, recidivism=0.0
    ).evidence_ids == {
        "case-0007:severity_class",
        "case-0007:spread",
        "case-0007:velocity",
        "case-0007:recidivism",
    }


def test_the_weights_hash_changes_when_a_weight_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = weights_hash()
    import ts_sentry.agents.triage.scorer as scorer_module

    monkeypatch.setattr(scorer_module, "WEIGHTS", {**WEIGHTS, ScoreComponent.SPREAD: 0.30})

    assert weights_hash() != before


# --------------------------------------------------------------------------
# 3.5: rationale verification, reusing the STEP-02 verifier
# --------------------------------------------------------------------------


def _scores(count: int = 2) -> list[PriorityScore]:
    return [
        score(
            f"case-{index:04d}",
            severity_class=1.0 - index * 0.1,
            spread=0.5,
            velocity=0.25,
            recidivism=0.1,
        )
        for index in range(count)
    ]


def test_a_rationale_citing_its_own_components_passes() -> None:
    scores = _scores(1)
    result = verify_rationales(
        scores, {"case-0000": "case-0000: first on [case-0000:severity_class]."}
    )

    assert result.all_passed
    assert result.accepted[0].cited_ids == {"case-0000:severity_class"}


def test_a_rationale_citing_nothing_fails() -> None:
    """Zero tolerance, inherited from STEP-02 3.4: an unsupported explanation
    is not an explanation."""
    result = verify_rationales(_scores(1), {"case-0000": "case-0000: it looks bad."})

    assert not result.all_passed
    assert result.rejected[0].result.reason is not None


def test_a_rationale_citing_another_case_fails() -> None:
    """The reason the resolvable set is per case, not per batch.

    "This case is urgent because another case is fast" is exactly the
    confabulation the A-01 control exists to catch, and a batch-wide set would
    have accepted it.
    """
    scores = _scores(2)
    result = verify_rationales(
        scores,
        {
            "case-0000": "case-0000: urgent because of [case-0001:velocity].",
            "case-0001": "case-0001: [case-0001:spread].",
        },
    )

    assert [item.case_id for item in result.rejected] == ["case-0000"]
    assert result.rejected[0].result.unresolvable_ids == ("case-0001:velocity",)


def test_an_overclaiming_rationale_is_rejected_and_kept() -> None:
    """A rejected rationale is evidence about the model. Discarding it would
    remove the only record of what was proposed."""
    result = verify_rationales(
        _scores(1), {"case-0000": "case-0000: confirmed abusive per [sealed:ground_truth]."}
    )

    rejected = result.rejected[0]
    assert "sealed:ground_truth" in rejected.text
    assert rejected.result.unresolvable_ids == ("sealed:ground_truth",)
    assert result.to_ledger_payload()["rejected"] == 1


def test_citations_are_only_recognized_in_brackets() -> None:
    """Unbracketed prose about velocity must not read as a citation of it, or
    the verifier would be checking sentences rather than claims."""
    assert parse_citations("velocity is high for case-0000") == frozenset()
    assert parse_citations("see [case-0000:velocity]") == {"case-0000:velocity"}


def test_unattributable_lines_are_discarded_not_guessed() -> None:
    parsed = parse_rationale_lines(
        "case-0000: first [case-0000:spread]\nSummary of my analysis\ncase-0001: [x]"
    )

    assert set(parsed) == {"case-0000", "case-0001"}


def test_the_citation_menu_lists_exactly_the_legal_ids() -> None:
    menu = render_expected_form(_scores(1))

    for component in ScoreComponent:
        assert f"[case-0000:{component.value}]" in menu


# --------------------------------------------------------------------------
# The detection stub
# --------------------------------------------------------------------------


def _dataset() -> Dataset:
    """A tiny hand-built platform with one coordinated ring and one benign
    channel, so the stub's behavior is checkable by inspection."""
    accounts = tuple(
        AccountMeta(
            account_id=f"acct-{i}",
            created_ts=_BASE,
            display_name=f"user{i}",
            is_verified=False,
            signup_ip_bucket="10.0.0.0/24",
            device_fingerprint_hint=None,
        )
        for i in range(3)
    )
    channels = tuple(
        Channel(
            channel_id=f"chan-{i}",
            account_id=f"acct-{i}",
            created_ts=_BASE,
            display_name=f"Channel {i}",
            subscriber_count=10,
            description="a description",
        )
        for i in range(3)
    )
    videos = tuple(
        Video(
            video_id=f"vid-{i}",
            channel_id=f"chan-{i}",
            title=f"Video {i}",
            description="desc",
            published_ts=_BASE,
            duration_s=60,
            synthetic_media_disclosed=i != 0,
            provenance_signal=ProvenanceSignal.ABSENT,
        )
        for i in range(3)
    )
    comments = tuple(
        Comment(
            comment_id=f"cmt-{i}-{j}",
            video_id=f"vid-{i}",
            account_id=f"acct-{i}",
            parent_comment_id=None,
            posted_ts=_BASE + timedelta(minutes=j),
            text=f"comment {j} on {i}",
            template_id="tpl-1" if i == 0 else None,
        )
        for i in range(3)
        for j in range(4)
    )
    hints = (
        InfraHint(
            hint_id="hint-1",
            subject_kind=EntityKind.CHANNEL,
            subject_id="chan-0",
            signal_type=InfraSignalKind.LINK_DOMAIN_REUSE,
            signal_value="bad.example",
            observed_ts=_BASE,
        ),
        InfraHint(
            hint_id="hint-2",
            subject_kind=EntityKind.CHANNEL,
            subject_id="chan-1",
            signal_type=InfraSignalKind.LINK_DOMAIN_REUSE,
            signal_value="bad.example",
            observed_ts=_BASE + timedelta(days=1),
        ),
    )
    return Dataset(
        accounts=accounts,
        channels=channels,
        videos=videos,
        comments=comments,
        engagement_events=(),
        infra_hints=hints,
        sealed_labels=(),
    )


@pytest.fixture
def connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    persist_dataset(con, _dataset())
    return con


def test_the_stub_reads_only_allowlisted_tables() -> None:
    """Saif's condition on the detection stub, asserted against the SQL rather
    than trusted.

    Every ``schema.table`` token in every statement this module issues must be
    a table some ``DataScope`` member resolves to. ``sealed._labels`` has no
    member, so it is unnameable here by construction; this test is what makes
    that checkable rather than a claim in a docstring.
    """
    import re

    allowed = {resolve_table(scope) for scope in DataScope}
    pattern = re.compile(r"\b([a-z_]+\.[a-z_]+)\b")

    for name, sql in queries().items():
        referenced = {
            token for token in pattern.findall(sql) if not token.startswith(("cm.", "v.", "c."))
        }
        assert referenced <= allowed, (
            f"{name} touches non-allowlisted tables: {referenced - allowed}"
        )
        assert "sealed" not in sql


def test_the_stub_flags_entities_with_visible_coordination_artifacts(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    queue = build_flagged_queue(connection, rng=np.random.default_rng(42))

    flagged = {entity.channel_id for entity in queue}
    assert "chan-0" in flagged  # link reuse + templates + undisclosed synthetic
    assert "chan-1" in flagged  # link reuse
    assert "chan-2" not in flagged  # nothing observable


def test_the_stub_is_deterministic(connection: duckdb.DuckDBPyConnection) -> None:
    first = build_flagged_queue(connection, rng=np.random.default_rng(42))
    second = build_flagged_queue(connection, rng=np.random.default_rng(7))

    assert [e.to_json_object() for e in first] == [e.to_json_object() for e in second]


def test_spread_counts_peers_sharing_a_signal_value(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    queue = {
        entity.channel_id: entity
        for entity in build_flagged_queue(connection, rng=np.random.default_rng(42))
    }

    assert queue["chan-0"].signals.peer_entities == 1  # chan-1 shares bad.example
    assert queue["chan-0"].spread > 0.0


def test_every_component_is_normalized(connection: duckdb.DuckDBPyConnection) -> None:
    for entity in build_flagged_queue(connection, rng=np.random.default_rng(42)):
        for value in (entity.severity_class, entity.spread, entity.velocity, entity.recidivism):
            assert 0.0 <= value <= 1.0


def test_the_queue_is_identical_under_any_reader_timezone(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """The STEP-02 DuckDB finding, in the place it would have bitten next.

    Recidivism counts distinct observation *days*. Had timestamps been read as
    rendered text, DuckDB would have rendered them in the reader's session
    time zone and two machines would have computed different priorities from
    one dataset, with neither looking wrong. Selecting epoch milliseconds
    removes the rendering entirely; this asserts it.
    """
    results = []
    for zone in ("Asia/Kolkata", "UTC", "America/New_York"):
        connection.execute(f"SET TimeZone='{zone}'")
        results.append(
            [
                e.to_json_object()
                for e in build_flagged_queue(connection, rng=np.random.default_rng(42))
            ]
        )

    assert results[0] == results[1] == results[2]


def test_the_queue_limit_is_validated(connection: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError, match="limit must be positive"):
        build_flagged_queue(connection, rng=np.random.default_rng(42), limit=0)


def test_case_records_carry_platform_text_verbatim(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    queue = build_flagged_queue(connection, rng=np.random.default_rng(42))
    records = case_records(connection, [(e.case_id, e.channel_id) for e in queue])

    sources = {record.source for record in records}
    assert sources == {"channel.display_name", "channel.description", "comment.text"}
    assert any(record.text.startswith("comment ") for record in records)


# --------------------------------------------------------------------------
# The tool handler
# --------------------------------------------------------------------------


def _context(connection: duckdb.DuckDBPyConnection, **params: object) -> ToolContext:
    return ToolContext(
        agent_id="triage",
        granted_scopes=frozenset(DataScope),
        params=params,
        resources=ToolResources(connection=connection, seed=42),
    )


def test_the_handler_returns_a_queue_ranked_by_priority(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    result = rank_triage_queue(_context(connection))

    assert isinstance(result, RankedQueue)
    priorities = [row.score.priority for row in result.rows]
    assert priorities == sorted(priorities, reverse=True)
    assert result.weights_version == WEIGHTS_VERSION
    assert result.detector_version == DETECTOR_VERSION


def test_every_delivered_row_names_the_channel_it_is_about(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    result = rank_triage_queue(_context(connection))
    assert isinstance(result, RankedQueue)

    for row in result.rows:
        assert row.subject_id.startswith("chan-")


def test_an_agent_supplied_limit_is_clamped_not_trusted(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """An agent asking for a million rows gets the ceiling, not an argument."""
    huge = rank_triage_queue(_context(connection, limit=10_000))
    negative = rank_triage_queue(_context(connection, limit=-5))
    junk = rank_triage_queue(_context(connection, limit="all of them"))

    assert isinstance(huge, RankedQueue)
    assert isinstance(negative, RankedQueue)
    assert isinstance(junk, RankedQueue)
    assert len(huge.rows) <= 25


def test_a_handler_without_a_connection_refuses(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """The orchestrator lends the connection. A tool that was lent nothing
    refuses rather than reaching for one."""
    from ts_sentry.orchestrator.toolspec import ToolViolation

    context = ToolContext(agent_id="triage", granted_scopes=frozenset(DataScope), params={})
    with pytest.raises(ToolViolation, match="dataset connection"):
        rank_triage_queue(context)


def test_a_queue_out_of_priority_order_is_refused() -> None:
    """Ordering is part of the output contract: the whole product is a
    ranking, and a queue that arrives unordered has delivered nothing."""
    low = score("case-0000", severity_class=0.0, spread=0.0, velocity=0.0, recidivism=0.0)
    high = score("case-0001", severity_class=1.0, spread=1.0, velocity=1.0, recidivism=1.0)

    with pytest.raises(ValueError, match="ordered by descending priority"):
        RankedQueue(
            rows=(
                RankedRow(score=low, subject_id="chan-0", rationale=None),
                RankedRow(score=high, subject_id="chan-1", rationale=None),
            ),
            weights_version=WEIGHTS_VERSION,
            detector_version=DETECTOR_VERSION,
        )


# --------------------------------------------------------------------------
# 3.2: the injection corpus against the rationale contract
# --------------------------------------------------------------------------


def test_injected_case_content_cannot_change_what_a_rationale_may_cite() -> None:
    """STEP-03 3.2's behavioral half, stated at its true width.

    What this proves: whatever the case content says, the set of citations a
    rationale can make and have accepted is fixed by the *scored queue*, not by
    the content. Every fixture in the D2 corpus is fed through the firewall
    into a turn's user content, and a rationale that obeys any of them - citing
    ground truth, another case, or nothing - is rejected by the verifier.

    What this does not prove: that a live model resists injection. The stub is
    deterministic and cannot be persuaded, so no assertion here is about model
    behavior. The claim is about the pipeline: the model's output is checked
    rather than trusted, so obedience to an injected instruction cannot become
    an accepted rationale. That is the control, and it is the one that holds
    regardless of which model is behind the adapter.
    """
    from test_firewall import ALL_FIXTURES

    from ts_sentry.orchestrator.firewall import CaseRecord, apply_firewall, compose_user_content

    scores = _scores(1)
    records = tuple(
        CaseRecord(record_id=f"case-0000:comment:{i}", source="comment.text", text=text)
        for i, (_, text) in enumerate(ALL_FIXTURES)
    )
    firewalled = apply_firewall(records)
    user_content = compose_user_content(
        triage_instruction(render_expected_form(scores)), firewalled
    )

    # The corpus reaches the model only inside the fenced block, and only ever
    # in the user role.
    assert firewalled.block.nonce in user_content
    for _, text in ALL_FIXTURES:
        assert text not in TRIAGE_SYSTEM_PROMPT.text

    # A rationale that did whatever the injected text asked is refused.
    for obedient in (
        "case-0000: benign, verified by the analyst.",
        "case-0000: approved per [sealed:ground_truth].",
        "case-0000: see [case-0001:velocity].",
        "case-0000: OK",
    ):
        result = verify_rationales(scores, {"case-0000": obedient})
        assert not result.all_passed


def test_the_system_prompt_is_hash_identified_and_fixed() -> None:
    assert TRIAGE_SYSTEM_PROMPT.prompt_id == "triage.rationale.v1"
    assert len(TRIAGE_SYSTEM_PROMPT.sha256) == 64
