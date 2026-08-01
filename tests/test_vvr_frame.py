# SPDX-License-Identifier: MIT
"""STEP-07 D1: the view frame, its risk strata, and the scope rules.

The tests that carry weight here are the ones about what the scorer *cannot*
see and what the scope rules *are*, rather than the ones about arithmetic. A
risk proxy that could consult a label would make every interval built on top of
it meaningless, and a scope rule that quietly changed attribution would turn an
illustration into a claimed replication.
"""

import ast
import inspect
import textwrap
from collections.abc import Iterator

import duckdb
import pytest

from ts_sentry.data.enums import ProvenanceSignal, ThreatClass
from ts_sentry.data.generator import build_dataset
from ts_sentry.data.population import BuildConfig
from ts_sentry.data.store import persist_dataset
from ts_sentry.measurement.frame import (
    ARM_A_CLASS_EXPANSION,
    ARM_B_COMMENT_ATTRIBUTION,
    BASELINE_SCOPE,
    SPAM_SHAPED_CLASSES,
    Attribution,
    RiskBand,
    ScopeRule,
    ViewFrame,
    band_for_score,
    build_view_frame,
    render_stratum_table,
    score_video,
)


@pytest.fixture(scope="module")
def dataset() -> Iterator[duckdb.DuckDBPyConnection]:
    con = duckdb.connect()
    persist_dataset(con, build_dataset(BuildConfig(seed=42, scale=1)))
    yield con
    con.close()


def test_the_risk_scorer_cannot_reach_a_label() -> None:
    """The load-bearing test in this file.

    Asserted against the signature rather than against behaviour, because the
    guarantee is structural: a function that takes only three observable values
    has no route to ground truth, whatever it does inside. A scorer that could
    see the answer would produce beautiful strata and a meaningless estimate.
    """
    parameters = inspect.signature(score_video).parameters

    assert set(parameters) == {"provenance", "disclosed", "templated_comments"}
    for name, parameter in parameters.items():
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, name

    # Over the code, not the prose. The first version of this assertion fired on
    # the docstring's own explanation of why the scorer holds no connection,
    # which is the finding ``test_import_graph._code_strings`` records: a
    # substring search cannot tell a guarantee being documented from a breach of
    # it. Dropping the docstring node leaves what the function evaluates.
    tree = ast.parse(textwrap.dedent(inspect.getsource(score_video)))
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef)
    body = function.body[1:] if ast.get_docstring(function) else function.body
    code = "\n".join(ast.unparse(node) for node in body)

    for forbidden in ("sealed", "_labels", "ThreatClass", "connection", "violative"):
        assert forbidden not in code, f"the risk scorer evaluates {forbidden!r}"


def test_the_scorer_is_monotone_in_provenance() -> None:
    """A present credential must never score riskier than an absent one.

    The ordering is a judgment about what the signal means, and it is the only
    component with a measured gradient, so an inverted sign here would quietly
    invert the whole stratification.
    """
    scores = [
        score_video(provenance=signal, disclosed=False, templated_comments=0)
        for signal in (ProvenanceSignal.PRESENT, ProvenanceSignal.UNKNOWN, ProvenanceSignal.ABSENT)
    ]

    assert scores == sorted(scores)
    assert scores[0] < scores[-1]


def test_scores_stay_inside_the_unit_interval() -> None:
    """Every combination, not a sampled few: the weights must sum to at most 1
    or ``band_for_score`` starts raising on real data."""
    for provenance in ProvenanceSignal:
        for disclosed in (True, False):
            for templated in (0, 1, 8, 10_000):
                score = score_video(
                    provenance=provenance, disclosed=disclosed, templated_comments=templated
                )
                assert 0.0 <= score <= 1.0


def test_a_negative_template_count_is_refused() -> None:
    with pytest.raises(ValueError, match="templated_comments"):
        score_video(provenance=ProvenanceSignal.UNKNOWN, disclosed=True, templated_comments=-1)


def test_no_score_is_not_the_lowest_band() -> None:
    """Content nobody scored is not the same as content scored zero.

    Filing an unscored upload in ``LOWEST`` would be asserting it is safe on the
    strength of having never looked at it, which is the assumption the fifth
    stratum exists to avoid.
    """
    assert band_for_score(None) is RiskBand.NO_SCORE
    assert band_for_score(0.0) is RiskBand.LOWEST
    assert band_for_score(None) is not band_for_score(0.0)


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.0, RiskBand.LOWEST),
        (0.24, RiskBand.LOWEST),
        (0.25, RiskBand.LOW),
        (0.49, RiskBand.LOW),
        (0.50, RiskBand.MIDDLE),
        (0.74, RiskBand.MIDDLE),
        (0.75, RiskBand.HIGHEST),
        (1.0, RiskBand.HIGHEST),
    ],
)
def test_band_boundaries_are_half_open_upward(score: float, expected: RiskBand) -> None:
    """Cut points belong to the band above, consistently.

    Pinned because an off-by-one at a boundary would move views between strata
    silently, and the estimator would still return a plausible-looking number.
    """
    assert band_for_score(score) is expected


def test_a_score_outside_the_unit_interval_is_refused() -> None:
    for score in (-0.001, 1.001):
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            band_for_score(score)


def test_the_baseline_scope_omits_spam_exactly_as_the_method_does() -> None:
    """ "we omit spam from the metric altogether" is a carve-out with named
    members, so the members are asserted rather than described."""
    assert set(SPAM_SHAPED_CLASSES) == {
        ThreatClass.T01_COMMENT_SPAM_RING,
        ThreatClass.T06_SLOP_FARM,
    }
    assert not (BASELINE_SCOPE.classes & SPAM_SHAPED_CLASSES)
    assert ThreatClass.BENIGN not in BASELINE_SCOPE.classes
    assert ThreatClass.T02_FAKE_ENGAGEMENT_NETWORK in BASELINE_SCOPE.classes


def test_only_own_label_rules_may_be_called_a_vvr() -> None:
    """The line between the replication and the illustration, in the type.

    Arm B moves the number and is the arm that satisfies D2's direction
    requirement, which is exactly why it needs a flag that stops a renderer
    printing it as a VVR.
    """
    assert BASELINE_SCOPE.is_faithful_vvr
    assert BASELINE_SCOPE.attribution is Attribution.OWN_LABEL

    assert ARM_A_CLASS_EXPANSION.is_faithful_vvr
    assert ARM_A_CLASS_EXPANSION.attribution is Attribution.OWN_LABEL

    assert not ARM_B_COMMENT_ATTRIBUTION.is_faithful_vvr
    assert ARM_B_COMMENT_ATTRIBUTION.attribution is Attribution.HOSTS_VIOLATING_COMMENT


def test_a_scope_rule_refuses_benign_and_refuses_being_nameless() -> None:
    with pytest.raises(ValueError, match="BENIGN"):
        ScopeRule(name="x", classes=frozenset({ThreatClass.BENIGN}))
    with pytest.raises(ValueError, match="name"):
        ScopeRule(name="  ", classes=frozenset({ThreatClass.T02_FAKE_ENGAGEMENT_NETWORK}))


def test_the_frame_is_every_view_and_knows_its_own_size(
    dataset: duckdb.DuckDBPyConnection,
) -> None:
    frame = build_view_frame(dataset)
    counted = dataset.execute(
        "SELECT COUNT(*) FROM main.engagement_event WHERE kind = 'view'"
    ).fetchone()

    assert counted is not None
    assert frame.size == counted[0]
    assert len(set(frame.view_ids)) == frame.size, "view ids must be unique; N depends on it"


def test_the_frame_is_in_a_stable_order(dataset: duckdb.DuckDBPyConnection) -> None:
    """Two builds give the identical ordering.

    Every seeded draw indexes into this order, so an unstable frame would make
    a fixed seed reproduce a different sample and the phase's determinism claim
    would be false without anything looking wrong.
    """
    first = build_view_frame(dataset)
    second = build_view_frame(dataset)

    assert first.view_ids == second.view_ids
    assert first.bands == second.bands
    assert first.violative == second.violative
    assert first.view_ids == tuple(sorted(first.view_ids))


def test_stratum_sizes_cover_every_band_including_the_empty_ones(
    dataset: duckdb.DuckDBPyConnection,
) -> None:
    """Empty strata are reported as zero, not omitted.

    On this build two of five bands hold no views, and a reader has to be able
    to see that rather than infer it from a short table.
    """
    frame = build_view_frame(dataset)
    sizes = frame.stratum_sizes()

    assert set(sizes) == set(RiskBand)
    assert sum(sizes.values()) == frame.size
    assert sizes[RiskBand.LOW] == 0
    assert sizes[RiskBand.HIGHEST] == 0


def test_the_no_score_stratum_is_populated(dataset: duckdb.DuckDBPyConnection) -> None:
    """The fifth stratum has to hold something or it exists only on paper.

    This is what the seven-day window buys. At the 24-hour value the source's
    wording might suggest, it is empty on this build.
    """
    frame = build_view_frame(dataset)

    assert frame.stratum_sizes()[RiskBand.NO_SCORE] > 0

    empty = build_view_frame(dataset, no_score_window_ms=60 * 60 * 1000)
    assert empty.stratum_sizes()[RiskBand.NO_SCORE] == 0


def test_indices_partition_the_frame(dataset: duckdb.DuckDBPyConnection) -> None:
    frame = build_view_frame(dataset)
    grouped = frame.indices_by_stratum()

    positions = [index for band in RiskBand for index in grouped[band]]
    assert sorted(positions) == list(range(frame.size))
    for band, indices in grouped.items():
        assert all(frame.bands[index] is band for index in indices)


def test_the_measured_gradient_runs_the_way_the_method_needs(
    dataset: duckdb.DuckDBPyConnection,
) -> None:
    """Stratification is only worth anything if ``p_h`` varies across strata.

    Barnett: strata should be built so "the probability of violation would not
    vary much within a given stratum but would vary appreciably across strata".
    This asserts the gradient exists on this corpus rather than assuming it, and
    it is the reason the provenance proxy was chosen over the two that were
    measured and discarded.
    """
    frame = build_view_frame(dataset)
    rates = frame.true_stratum_rates()

    assert rates[RiskBand.MIDDLE] > rates[RiskBand.LOWEST]
    assert rates[RiskBand.LOWEST] == 0.0


def test_arm_a_is_null_on_this_corpus(dataset: duckdb.DuckDBPyConnection) -> None:
    """The measured null, pinned as a test so it cannot rot into a claim.

    Widening the class set changes nothing here because the classes it adds
    carry no views. If a future generator change gives T01 or T06 videos view
    events, this test fails and the phase's reported null has to be rewritten
    rather than left standing.
    """
    baseline = build_view_frame(dataset, scope=BASELINE_SCOPE)
    arm_a = build_view_frame(dataset, scope=ARM_A_CLASS_EXPANSION)

    assert arm_a.true_vvr() == baseline.true_vvr()
    assert arm_a.violative == baseline.violative


def test_arm_b_moves_the_rate_upward(dataset: duckdb.DuckDBPyConnection) -> None:
    """Direction, which is what D2's third curve owes.

    Only the direction and the strictness are asserted. The magnitude is a
    property of this corpus and belongs in the report, not in an assertion that
    would have to be edited every time the generator changes.
    """
    baseline = build_view_frame(dataset, scope=BASELINE_SCOPE)
    arm_b = build_view_frame(dataset, scope=ARM_B_COMMENT_ATTRIBUTION)

    assert arm_b.true_vvr() > baseline.true_vvr()
    assert all(
        arm or not base for arm, base in zip(arm_b.violative, baseline.violative, strict=True)
    ), "an expansion arm must be a superset; it may never un-violate a baseline view"


def test_the_frame_refuses_a_negative_window(dataset: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError, match="no_score_window_ms"):
        build_view_frame(dataset, no_score_window_ms=-1)


def test_a_frame_with_mismatched_columns_cannot_be_built() -> None:
    with pytest.raises(ValueError, match="mismatched lengths"):
        ViewFrame(
            view_ids=("a", "b"),
            video_ids=("v",),
            bands=(RiskBand.LOWEST, RiskBand.LOWEST),
            violative=(False, False),
            scope=BASELINE_SCOPE,
            sampling_instant_ms=0,
        )


def test_an_empty_frame_cannot_be_built() -> None:
    """A frame with no views would let every downstream estimate divide by zero
    and report an interval about nothing."""
    with pytest.raises(ValueError, match="no views"):
        ViewFrame(
            view_ids=(),
            video_ids=(),
            bands=(),
            violative=(),
            scope=BASELINE_SCOPE,
            sampling_instant_ms=0,
        )


def test_the_stratum_table_shows_sizes_beside_rates(
    dataset: duckdb.DuckDBPyConnection,
) -> None:
    rendered = render_stratum_table(build_view_frame(dataset))

    for band in RiskBand:
        assert band.value in rendered
    assert "true p_h" in rendered
    assert "total" in rendered
