# SPDX-License-Identifier: MIT
"""STEP-07 D1: the view frame, its risk strata, and the sealed truth join.

This is the only module in the VVR lens that reads ``sealed._labels``. The
estimator, the rater panel and the sensitivity curves all work from what this
module hands them, so ground truth enters the lens at exactly one place and the
import-graph allowlist edit is a single entry.

What is being replicated, and from where
----------------------------------------
YouTube's published method, as described by the Google Transparency Report help
centre and by the independent statistical assessment Google commissioned from
Arnold Barnett (MIT, September 2021), reproduced in
``docs/barnett-vvr-assessment.txt``:

    "We first take a sample of all videos that have been viewed on YouTube. The
    videos in that sample are then sent for review, and our teams determine
    whether each video does or does not violate our community guidelines."
    - https://support.google.com/transparencyreport/answer/9209072

    "YouTube then moved to an exercise in stratified sampling, based on creating
    non overlapping ranges for the video scores. The aim was to devise a set of
    strata such that the probability of violation would not vary much within a
    given stratum but would vary appreciably across strata. YouTube decided that
    it would create five strata, namely, lowest risk, 2nd lowest, 2nd highest,
    highest, and 'no score available.' Random sampling would then take place
    among the views in each stratum, meaning that a given video's probability of
    selection would be proportional to the number of people who viewed it."
    - Barnett, section III

Three structural features come straight from that and are implemented here: the
sampling unit is the **view**, not the video; the strata are **non-overlapping
ranges of a risk score**; and there is a fifth **"no score available"** stratum
for content too recent to have been scored ("videos uploaded very close to the
time that sampling was done", Barnett footnote 7).

The risk proxy, stated so it cannot be read as more than it is
--------------------------------------------------------------
**This is an analog of YouTube's classifier-risk stratification, using
observable content-provenance features as the risk proxy, since the synthetic
frame has no production classifier.** YouTube bands a machine-learning
classifier's 0..1 score. There is no such classifier here and inventing one
would be inventing a detection capability this project does not have and says
repeatedly that it does not have. What is replicated is the *method*: a score
computed from observable features only, banded into non-overlapping ranges,
with sampling effort allocated across the bands.

``score_video`` never sees a label. It takes three observable columns and
returns a float. That is checked by a test rather than promised here.

What was rejected, and why it matters
--------------------------------------
The strongest-looking stratifier on this data is *views per video*: videos with
few distinct viewers are 100% violative and carry 0.1% of the frame. It is not
used, because it is not a risk signal. The threat modules plant 2 to 6 views per
video while the base generator gives roughly fifty, so "few distinct viewers"
is the generator's engagement budget wearing a disguise. Stratifying on it would
recover ground truth almost exactly and report a spectacular interval while
measuring nothing, which is the same defect class as the STEP-06 eval ordinal
that leaked its own answer key. Recorded here so the rejection stays visible.

The measured gradient on the seed-42 build, view-weighted, is carried by
provenance alone: views of ``present``-provenance videos are 0.0000% violative
(49.3% of the frame) against 0.1890% for ``unknown`` (50.7%). Channel template
density was measured and discarded: 0.0984% against 0.0878% is not a gradient.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

import duckdb

from ts_sentry.data.enums import EngagementKind, EntityKind, ProvenanceSignal, ThreatClass
from ts_sentry.governance.scopes import DataScope, resolve_table

__all__ = [
    "ARM_A_CLASS_EXPANSION",
    "ARM_B_COMMENT_ATTRIBUTION",
    "BAND_CUTS",
    "BASELINE_SCOPE",
    "BASELINE_VVR_SCOPE",
    "EXPANDED_VVR_SCOPE",
    "NO_SCORE_WINDOW_MS",
    "RISK_WEIGHTS",
    "SPAM_SHAPED_CLASSES",
    "Attribution",
    "RiskBand",
    "ScopeRule",
    "ViewFrame",
    "band_for_score",
    "build_view_frame",
    "score_video",
]


class RiskBand(StrEnum):
    """The five strata, named as Barnett names them.

    ``NO_SCORE`` is not a risk level and never participates in the score-band
    ordering. It is the stratum for content the scorer declines to score, which
    in YouTube's case is content uploaded too close to sampling time.
    """

    LOWEST = "lowest_risk"
    LOW = "low_risk"
    MIDDLE = "middle_risk"
    HIGHEST = "highest_risk"
    NO_SCORE = "no_score_available"


SPAM_SHAPED_CLASSES = frozenset({ThreatClass.T01_COMMENT_SPAM_RING, ThreatClass.T06_SLOP_FARM})
"""The classes held out of the baseline estimand.

"we omit spam from the metric altogether because spam channel removals make up
the majority of spam removals" (Transparency Report help centre). T01 is a
comment spam ring and T06 is a slop farm; both are the spam shape that sentence
excludes. Holding them out is what makes the baseline number a replication
rather than a metric of our own.

They are not discarded. They are the arm of the D2 policy-scope expansion, which
is the experiment STEP-07 D2 asks for and which mirrors YouTube's own note that
the rate rises when policy scope expands.
"""

BASELINE_VVR_SCOPE = frozenset(
    threat
    for threat in ThreatClass
    if threat is not ThreatClass.BENIGN and threat not in SPAM_SHAPED_CLASSES
)
"""Classes that make a *viewed video* violative for the baseline VVR.

Membership is by the video's **own** label. A video is not violative because its
channel is labelled or because labelled accounts comment on it, because the
published method judges "whether each video does or does not violate our
community guidelines" and nothing wider.

The consequence is narrow and is reported rather than softened: of the five
classes in this set, only T02 and T07 plant view events, so they carry every
violative view on the seed-42 build. T04, the one class whose name suggests it
should dominate a provenance-stratified estimate, contributes **zero views**.
"""

EXPANDED_VVR_SCOPE = frozenset(threat for threat in ThreatClass if threat is not ThreatClass.BENIGN)
"""Every non-benign class, for the D2 policy-scope expansion arm A."""


class Attribution(StrEnum):
    """How a view's violative status is decided from labels.

    The distinction exists because one of D2's expansion arms crosses it, and a
    number produced under ``HOSTS_VIOLATING_COMMENT`` is not a VVR. Carrying the
    rule in the type means a report cannot print an arm without knowing which
    side of the line it came from.
    """

    OWN_LABEL = "own_label"
    HOSTS_VIOLATING_COMMENT = "hosts_violating_comment"


@dataclass(frozen=True, slots=True)
class ScopeRule:
    """What makes a viewed video violative, as data rather than a branch.

    ``classes`` is matched against the video's own sealed label.
    ``hosted_comment_classes`` is matched against labels on comments posted to
    that video, and is empty for every rule that judges the video itself.
    """

    name: str
    classes: frozenset[ThreatClass]
    hosted_comment_classes: frozenset[ThreatClass] = frozenset()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("a scope rule needs a name; it is printed on every result")
        if ThreatClass.BENIGN in self.classes | self.hosted_comment_classes:
            raise ValueError("BENIGN is not a violation and cannot be brought into scope")

    @property
    def attribution(self) -> Attribution:
        if self.hosted_comment_classes:
            return Attribution.HOSTS_VIOLATING_COMMENT
        return Attribution.OWN_LABEL

    @property
    def is_faithful_vvr(self) -> bool:
        """Whether a rate computed under this rule may be called a VVR.

        True only for rules that judge the video by its own label, because the
        published method determines "whether each video does or does not violate
        our community guidelines" and says nothing about comments hosted on it.
        A rule that fails this is a scope-question illustration and every
        renderer is expected to label it as one.
        """
        return self.attribution is Attribution.OWN_LABEL


BASELINE_SCOPE = ScopeRule(name="baseline", classes=BASELINE_VVR_SCOPE)
"""The replication estimand. The one number in this phase that is a VVR."""

ARM_A_CLASS_EXPANSION = ScopeRule(name="arm_a_class_expansion", classes=EXPANDED_VVR_SCOPE)
"""D2 arm A: widen the class set, keeping the video-judges-itself attribution.

Faithful, and **measured to be exactly null on this corpus**: 0.0958% before and
after, because T01 labels comments rather than videos and T06's videos receive
zero view events. That null is the result, not a failure to produce one. It is
reported with its explanation because "expanding policy scope did not move this
corpus's rate, and here is why" is a true finding about the generator, and
suppressing it would leave the reader assuming the arm was never run.
"""

ARM_B_COMMENT_ATTRIBUTION = ScopeRule(
    name="arm_b_comment_attribution",
    classes=BASELINE_VVR_SCOPE,
    hosted_comment_classes=frozenset({ThreatClass.T01_COMMENT_SPAM_RING}),
)
"""D2 arm B: a video is in scope if it hosts a comment-spam-ring comment.

Moves the rate 0.0958% -> 3.1097% on the seed-42 build, which is where D2's
required scope-effect direction comes from, and the direction matches YouTube's
own note that the rate rises as systems ramp up on "content that is newly
classified as violative". The arm is a union: the baseline classes stay in scope
and hosted spam comments are added, so 3.1097% is 3.0138% of hosted-comment
views plus the 0.0958% baseline, less their overlap.

**This arm changes the attribution rule, not merely the class set, and that is a
deviation rather than a refinement.** YouTube judges the video itself, not
comments hosted on it. So this is an illustration of a policy-scope *question* a
platform genuinely faces - does hosting violating comments taint the video - and
it is not a VVR and must never be reported as one. ``is_faithful_vvr`` is False
for exactly this reason, and the baseline estimand above is left untouched.
"""

RISK_WEIGHTS: Mapping[str, float] = {
    "provenance": 0.60,
    "undisclosed": 0.25,
    "templated_channel": 0.15,
}
"""Component weights for the risk proxy, published rather than buried.

Follows the precedent ``orchestrator.detection_stub.SIGNAL_SEVERITY`` set:
these are **judgments about how alarming a signal looks, not fitted
likelihoods**. Nothing here was tuned against the labels, and tuning them
against the labels would turn the proxy into a leak.

Provenance carries the majority weight because it is the only component with a
measured gradient. The other two are carried because a score built from one
observable is a band assignment with extra steps, and because Barnett's own
Section V experiment is about what additional strata buy.
"""

_PROVENANCE_RISK: Mapping[ProvenanceSignal, float] = {
    ProvenanceSignal.PRESENT: 0.0,
    ProvenanceSignal.UNKNOWN: 0.5,
    ProvenanceSignal.ABSENT: 1.0,
}
"""Content-credentials signal as a risk component.

Ordered the way a C2PA-direction signal reads: a present credential is the
reassuring case, an absent one is the alarming case, and unknown sits between
them. This ordering is a judgment about the signal's meaning, not a measurement.
"""

_TEMPLATE_CAP = 8
"""Templated comments on the owning channel at which this component saturates.

A presentation choice so the component reads as a 0..1 share, the same role
``_SPREAD_CAP`` plays in the detection stub.
"""

BAND_CUTS: tuple[float, float, float] = (0.25, 0.50, 0.75)
"""Cut points splitting [0, 1] into the four scored bands.

**Equal quarters, deliberately untuned.** Barnett describes YouTube solving for
boundaries that minimise sampling error, which requires a prior belief about how
violation probability varies with score. Choosing cuts here by looking at where
the labels fall would be fitting the strata to ground truth, so the neutral
split is taken instead and what it yields is reported.

What it yields on the seed-42 build is two non-empty strata, not four: viewed
videos take only two distinct observable profiles, so ``LOW`` and ``HIGHEST``
hold no views. That is a property of the generator, not of the cuts, and no
choice of cuts can manufacture strata the data does not contain.
"""

NO_SCORE_WINDOW_MS = 7 * 24 * 60 * 60 * 1000
"""How close to sampling time an upload must be to land in ``NO_SCORE``.

Barnett footnote 7: videos with no classifier score "are, for example, videos
uploaded very close to the time that sampling was done". Sampling time here is
the last view in the frame, so this is a window back from that instant.

Seven days is a **choice, not a finding**, and the source specifies no number.
It is set where the stratum is non-empty on the seed-42 build: the last
publication falls 4.8 days before the last view, so a 24-hour window leaves
``NO_SCORE`` holding nothing and the fifth stratum would exist only on paper.
Seven days puts 2 videos and 97 views in it. No label was consulted in picking
it, and it is a parameter rather than a constant so a different corpus can set
its own.
"""

_VIEW_QUERY = f"""
SELECT event_id, video_id
FROM {resolve_table(DataScope.ENGAGEMENT_EVENT)}
WHERE kind = ?
ORDER BY event_id
"""

_SAMPLING_INSTANT_QUERY = f"""
SELECT MAX(epoch_ms(ts_ist))
FROM {resolve_table(DataScope.ENGAGEMENT_EVENT)}
WHERE kind = ?
"""

_VIDEO_FEATURE_QUERY = f"""
SELECT
    v.video_id,
    v.provenance_signal,
    v.synthetic_media_disclosed,
    epoch_ms(v.published_ts),
    COALESCE(t.templated, 0)
FROM {resolve_table(DataScope.VIDEO)} v
LEFT JOIN (
    SELECT v2.channel_id, COUNT(*) AS templated
    FROM {resolve_table(DataScope.COMMENT)} cm
    JOIN {resolve_table(DataScope.VIDEO)} v2 ON v2.video_id = cm.video_id
    WHERE cm.template_id IS NOT NULL
    GROUP BY v2.channel_id
) t ON t.channel_id = v.channel_id
ORDER BY v.video_id
"""

_HOSTED_COMMENT_LABEL_QUERY = f"""
SELECT DISTINCT cm.video_id, l.threat_class
FROM {resolve_table(DataScope.COMMENT)} cm
JOIN sealed._labels l
  ON l.entity_id = cm.comment_id AND l.entity_kind = ?
"""
"""Comment labels lifted to the videos hosting them, for arm B only.

Runs only when a rule names hosted-comment classes, so the faithful path never
issues it. Joining it unconditionally would put comment ground truth into the
baseline estimand's code path for no reason.
"""

_VIDEO_LABEL_QUERY = """
SELECT entity_id, threat_class
FROM sealed._labels
WHERE entity_kind = ?
"""
"""Static SQL against the sealed table, video rows only.

No ``DataScope`` resolution, for the reason ``measurement.recovery`` records at
its own query: ``DataScope`` has no member resolving anywhere under ``sealed``
and must not grow one. Measurement reads it directly because measurement is the
legitimate consumer, and the import-graph test is what stops that from becoming
a door anyone else can open.
"""


def score_video(
    *,
    provenance: ProvenanceSignal,
    disclosed: bool,
    templated_comments: int,
) -> float:
    """Risk proxy for one video, from observable columns only.

    Takes three platform-observable values and returns a score in [0, 1]. It
    receives no label, no connection and no ring membership, so it cannot
    consult ground truth even by accident. That is asserted by a test over this
    signature, because a scorer that could see the answer would make every
    interval below it meaningless.
    """
    if templated_comments < 0:
        raise ValueError(f"templated_comments cannot be negative; got {templated_comments}")

    components = {
        "provenance": _PROVENANCE_RISK[provenance],
        "undisclosed": 0.0 if disclosed else 1.0,
        "templated_channel": min(1.0, templated_comments / _TEMPLATE_CAP),
    }
    return sum(RISK_WEIGHTS[name] * value for name, value in components.items())


def band_for_score(score: float | None) -> RiskBand:
    """Map a risk score to its stratum; ``None`` means the scorer declined.

    ``None`` is the "no score available" case and is kept distinct from a score
    of 0.0. Conflating them would file recently-uploaded content in the lowest
    risk band, which is an assumption about content nobody has scored.
    """
    if score is None:
        return RiskBand.NO_SCORE
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"risk score must lie in [0, 1]; got {score}")

    low, middle, high = BAND_CUTS
    if score < low:
        return RiskBand.LOWEST
    if score < middle:
        return RiskBand.LOW
    if score < high:
        return RiskBand.MIDDLE
    return RiskBand.HIGHEST


@dataclass(frozen=True, slots=True)
class ViewFrame:
    """Every view in the population, with its stratum and its sealed truth.

    Parallel tuples rather than a tuple of records: the estimator indexes into
    these by position for every bootstrap replicate, and a per-view object would
    make that the dominant cost of the sensitivity curves for no gain in
    clarity.

    ``violative`` is ground truth, so a ``ViewFrame`` is a sealed-bearing
    object. It is safe for the rest of the lens to hold one only because no
    agent or orchestrator module can import any measurement module at all.
    """

    view_ids: tuple[str, ...]
    video_ids: tuple[str, ...]
    bands: tuple[RiskBand, ...]
    violative: tuple[bool, ...]
    scope: ScopeRule
    sampling_instant_ms: int

    def __post_init__(self) -> None:
        lengths = {
            len(self.view_ids),
            len(self.video_ids),
            len(self.bands),
            len(self.violative),
        }
        if len(lengths) != 1:
            raise ValueError(f"view frame columns have mismatched lengths: {sorted(lengths)}")
        if not self.view_ids:
            raise ValueError("a view frame with no views cannot support an estimate")

    @property
    def size(self) -> int:
        """``N``, the population size. Known exactly, which is why the finite
        population correction is available at all."""
        return len(self.view_ids)

    def stratum_sizes(self) -> Mapping[RiskBand, int]:
        """``N_h`` per stratum, over every band including the empty ones.

        Empty bands are reported as zero rather than omitted, so a reader can
        see that a stratum exists and holds nothing. Silently dropping them
        would hide the finding that this frame populates two of five.
        """
        counts = dict.fromkeys(RiskBand, 0)
        for band in self.bands:
            counts[band] += 1
        return counts

    def indices_by_stratum(self) -> Mapping[RiskBand, tuple[int, ...]]:
        """Positions of each stratum's views, in frame order.

        Frame order is ``event_id`` order, which is stable across rebuilds, so a
        seeded draw over these indices reproduces exactly.
        """
        grouped: dict[RiskBand, list[int]] = {band: [] for band in RiskBand}
        for index, band in enumerate(self.bands):
            grouped[band].append(index)
        return {band: tuple(positions) for band, positions in grouped.items()}

    def true_vvr(self) -> float:
        """The population rate, computable only because this is synthetic.

        The number every estimate in this phase is checked against. A real
        platform cannot compute it, which is the entire reason VVR is estimated
        by sampling rather than counted.
        """
        return sum(self.violative) / self.size

    def true_stratum_rates(self) -> Mapping[RiskBand, float]:
        """``p_h`` per stratum from ground truth.

        For reporting the measured gradient and for checking the estimator, and
        for nothing else. It must never reach the allocator: allocation is
        seeded from a pilot's rater decisions, and seeding it from here would
        optimise the design against answers the method does not have.
        """
        totals: dict[RiskBand, int] = dict.fromkeys(RiskBand, 0)
        hits: dict[RiskBand, int] = dict.fromkeys(RiskBand, 0)
        for band, is_violative in zip(self.bands, self.violative, strict=True):
            totals[band] += 1
            hits[band] += int(is_violative)
        return {band: (hits[band] / totals[band] if totals[band] else 0.0) for band in RiskBand}


def build_view_frame(
    connection: duckdb.DuckDBPyConnection,
    *,
    scope: ScopeRule = BASELINE_SCOPE,
    no_score_window_ms: int = NO_SCORE_WINDOW_MS,
) -> ViewFrame:
    """Assemble the population of views, stratified and truth-joined.

    The frame is every ``VIEW`` engagement event. Because the sampling unit is
    the view rather than the video, a video's chance of selection is already
    proportional to how many people watched it, which is the property Barnett
    describes and the reason a widely-watched violative video weighs more than a
    rarely-seen one.
    """
    if no_score_window_ms < 0:
        raise ValueError(f"no_score_window_ms cannot be negative; got {no_score_window_ms}")

    instant_row = connection.execute(
        _SAMPLING_INSTANT_QUERY, [EngagementKind.VIEW.value]
    ).fetchone()
    if instant_row is None or instant_row[0] is None:
        raise ValueError("the frame holds no view events; there is nothing to sample")
    sampling_instant_ms = int(instant_row[0])
    no_score_after = sampling_instant_ms - no_score_window_ms

    band_of_video: dict[str, RiskBand] = {}
    for video_id, provenance, disclosed, published_ms, templated in connection.execute(
        _VIDEO_FEATURE_QUERY
    ).fetchall():
        score = (
            None
            if int(published_ms) > no_score_after
            else score_video(
                provenance=ProvenanceSignal(str(provenance)),
                disclosed=bool(disclosed),
                templated_comments=int(templated),
            )
        )
        band_of_video[str(video_id)] = band_for_score(score)

    violative_video: dict[str, bool] = {}
    for entity_id, threat_class in connection.execute(
        _VIDEO_LABEL_QUERY, [EntityKind.VIDEO.value]
    ).fetchall():
        violative_video[str(entity_id)] = ThreatClass(str(threat_class)) in scope.classes

    if scope.hosted_comment_classes:
        for video_id, threat_class in connection.execute(
            _HOSTED_COMMENT_LABEL_QUERY, [EntityKind.COMMENT.value]
        ).fetchall():
            if ThreatClass(str(threat_class)) in scope.hosted_comment_classes:
                violative_video[str(video_id)] = True

    view_ids: list[str] = []
    video_ids: list[str] = []
    bands: list[RiskBand] = []
    violative: list[bool] = []
    for event_id, video_id in connection.execute(
        _VIEW_QUERY, [EngagementKind.VIEW.value]
    ).fetchall():
        video = str(video_id)
        view_ids.append(str(event_id))
        video_ids.append(video)
        bands.append(band_of_video[video])
        violative.append(violative_video.get(video, False))

    return ViewFrame(
        view_ids=tuple(view_ids),
        video_ids=tuple(video_ids),
        bands=tuple(bands),
        violative=tuple(violative),
        scope=scope,
        sampling_instant_ms=sampling_instant_ms,
    )


def render_stratum_table(frame: ViewFrame) -> str:
    """The measured gradient, as a fixed-width table for the phase record.

    Prints ``p_h`` beside ``N_h`` because a stratum's usefulness is the pair:
    a band holding 0.1% of the frame at a 100% rate is a leak, and a band
    holding half the frame at a flat rate is decoration. Both are visible here
    and neither is visible from ``N_h`` alone.
    """
    sizes = frame.stratum_sizes()
    rates = frame.true_stratum_rates()
    lines = [
        f"{'stratum':<20}{'N_h':>8}{'share':>9}{'true p_h':>11}",
        "-" * 48,
    ]
    for band in RiskBand:
        share = 100.0 * sizes[band] / frame.size
        lines.append(f"{band.value:<20}{sizes[band]:>8}{share:>8.1f}%{100.0 * rates[band]:>10.4f}%")
    lines.append("-" * 48)
    lines.append(f"{'total':<20}{frame.size:>8}{100.0:>8.1f}%{100.0 * frame.true_vvr():>10.4f}%")
    return "\n".join(lines)
