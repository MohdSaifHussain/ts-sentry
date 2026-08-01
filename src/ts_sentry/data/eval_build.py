# SPDX-License-Identifier: MIT
"""D2: building the labeled eval set (STEP-06 D2).

Build-time only, like :mod:`ts_sentry.data.policy_fetch`. It runs against a
built dataset, reads ``sealed._labels``, and writes a committed artifact. No
session-time code path reaches this module, and nothing here is importable by
an agent without the import-graph test noticing.

This module is on ``LEGITIMATE_SEALED_CONSUMERS`` (``tests/test_import_graph.py``)
for the reason ``data.quality`` is: the build pipeline legitimately reads ground
truth, and the two-consumer model STEP-01 established reads "measurement code is
the only consumer" as naming the only *agent- or orchestrator-side* consumer.

The artifact is split, and the split is the contamination control
--------------------------------------------------------------------
STEP-06 3.2 requires that prompt authors never see per-item labels through the
tooling. That is not achieved by being careful about what gets passed; it is
achieved by there being nothing to pass:

* ``items.json`` holds ``(item_id, content)`` and **nothing else**. There is no
  label field, no class field, no stratum field. An earlier draft of this module
  carried a ``stratum`` on the item, which for a stratified set *is* the label
  wearing a different name; it was removed before anything was written.
* ``labels.json`` holds ``item_id -> threat_class`` and is loaded only by
  :mod:`ts_sentry.orchestrator.eval_labels`, which is in the import-graph test's
  forbidden set for every module under ``agents.``.

Ids are opaque, and that is load-bearing
-----------------------------------------
Planted entity ids are templated with their own class: ``t02_chan_000_000``
names T-02 in the first three characters. An eval item keyed by entity id would
hand the answer to the model in the record id, through the input firewall, with
every governance control working exactly as designed. So items get opaque ids
(``item-0000``) and the rendering never emits an entity id, a ring id, or an
infrastructure signal *value* (``devhint_t02_000`` leaks the same way).

``_refuse_leaky_item`` enforces that at build time, and a test asserts it over
the committed artifact. Both exist because this is the one defect in this phase
that would leave every metric looking excellent.

What the ceiling is, and why it is not raised here
--------------------------------------------------
Measured on real builds during D2, and recorded because it is the central
finding of STEP-06:

* Threat entities per class are 4 to 12 and **do not vary with ``--scale``**.
  ``RING_COUNT`` and ``MEMBERS_PER_RING`` are fixed constants in each ``t0N``
  module and ``for_budget`` only ever shrinks them, so the per-class abuse
  budget is computed and then left almost entirely unspent. Benign grew 450 ->
  4,500 -> 18,000 across scale 1, 10 and 40 while every threat class stayed
  identical.
* Content does not vary with the **seed** either. Seed 42 and seed 7 return
  byte-identical planted ids, names, descriptions and comment text; the seed
  varies timing and which base entities are targeted. Pooling across seeds would
  therefore add exact duplicates, inflating the item count while narrowing the
  bootstrap CI by replication, which is the "a lucky sample must not read as
  activatable" failure reached from the other side. It is not done.

The consequence is stated where it can be read rather than inferred: this eval
set supports detecting a **class collapse**, not a few-point drift. See
``orchestrator.regression_gate`` for how the tolerance is derived from it, and
the STEP-06 Outcome for the decision. Raising it needs a richer generator (more
templates per class for genuine item independence, plus volume), which
substantially reopens STEP-01 and is explicitly out of scope here.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np

from ts_sentry.data.enums import EntityKind, ThreatClass
from ts_sentry.data.eval_set import (
    EVAL_SCHEMA,
    ITEM_ID_PREFIX,
    ITEMS_FILE,
    LABELS_FILE,
    MANIFEST_FILE,
    EvalItem,
    items_digest,
    labels_digest,
)

__all__ = [
    "BENIGN_CONTROL_FRACTION",
    "EvalBuildError",
    "EvalSet",
    "build_eval_set",
    "write_eval_set",
]

BENIGN_CONTROL_FRACTION = 0.25
"""Benign controls as a fraction of the whole set.

Deliberately nothing like the platform's own >97% benign majority
(ARCHITECTURE 6.1). A stratified eval set over-samples rare classes on purpose,
because per-class recall is a within-class quantity and is unaffected, while
sampling to real prevalence would leave four or five items per threat class
drowned in thousands of benign ones and measure nothing.

The cost is that **precision measured on this set is not deployment precision**,
and that is stated in the report artifact itself rather than only here, in the
shape DECISIONS 4.9 used for the recovery ceiling.
"""

# Every query below is a module constant with bound parameters. Nothing is
# interpolated, so there is no dynamic SQL surface in this module at all.
_SUBJECT_LABELS = """
SELECT entity_kind, entity_id, threat_class
FROM sealed._labels
WHERE entity_kind IN ('account', 'channel')
ORDER BY entity_kind, entity_id
"""

_ACCOUNT_PROFILE = """
SELECT display_name, is_verified
FROM main.account_meta
WHERE account_id = ?
"""

_ACCOUNT_COMMENTS = """
SELECT text, count(*) AS n, count(DISTINCT video_id) AS videos
FROM main.comment
WHERE account_id = ?
GROUP BY text
ORDER BY n DESC, text
"""

_CHANNEL_PROFILE = """
SELECT display_name, description, subscriber_count
FROM main.channel
WHERE channel_id = ?
"""

_CHANNEL_VIDEOS = """
SELECT title, description, synthetic_media_disclosed, provenance_signal
FROM main.video
WHERE channel_id = ?
ORDER BY title
"""

# Counts only. The signal *value* is never selected, because a planted value
# like 'devhint_t02_000' names its own class.
_SHARED_SIGNALS = """
SELECT h1.signal_type,
       (SELECT count(*) FROM main.infra_hint h2
        WHERE h2.signal_value = h1.signal_value AND h2.subject_id <> h1.subject_id) AS shared_with
FROM main.infra_hint h1
WHERE h1.subject_id = ?
ORDER BY h1.signal_type
"""


class EvalBuildError(Exception):
    """Raised when an eval set cannot be built honestly."""


def _forbidden_tokens() -> tuple[str, ...]:
    """Substrings that would hand a classifier its own answer.

    Every ``ThreatClass`` value, plus the planted-id and planted-signal prefixes
    the generator uses. Derived from the enum rather than written out, so a new
    threat class is covered the day it is added instead of the day somebody
    remembers this list.
    """
    classes = tuple(member.value for member in ThreatClass if member is not ThreatClass.BENIGN)
    prefixes = tuple(f"t{index:02d}_" for index in range(1, 8))
    return (*classes, *prefixes, "ring_t", "devhint_t", "ipb_t")


def _refuse_leaky_item(item_id: str, content: str) -> None:
    """Fail the build if a rendering carries the answer.

    Checked here, at the moment the text is produced, rather than only in a
    test. A leaked label does not make anything fail: it makes every metric
    look excellent, which is the one defect in this phase that would be
    invisible in its own results.
    """
    lowered = content.lower()
    found = sorted({token for token in _forbidden_tokens() if token in lowered})
    if found:
        raise EvalBuildError(
            f"{item_id} renders content containing {found}, which names its own threat class. "
            "Eval item content carries case text and counts, never entity ids, ring ids, or "
            "infrastructure signal values"
        )


def _render_account(con: duckdb.DuckDBPyConnection, account_id: str) -> str:
    profile = con.execute(_ACCOUNT_PROFILE, [account_id]).fetchone()
    lines: list[str] = ["Subject: a commenting account."]
    if profile is not None:
        lines.append(f"Display name: {profile[0]}")
        lines.append(f"Verified: {'yes' if profile[1] else 'no'}")

    comments = con.execute(_ACCOUNT_COMMENTS, [account_id]).fetchall()
    total = sum(int(row[1]) for row in comments)
    videos = max((int(row[2]) for row in comments), default=0)
    lines.append(f"Comments posted: {total} across {videos} distinct video(s).")
    for text, count, _ in comments:
        lines.append(f'  posted {count}x: "{text}"')

    lines.extend(_render_signals(con, account_id))
    return "\n".join(lines)


def _render_signals(con: duckdb.DuckDBPyConnection, subject_id: str) -> list[str]:
    """Shared-infrastructure signals, as kind plus a count and nothing else.

    The signal *value* is never rendered. A planted value like
    ``devhint_t02_000`` names its own threat class, so emitting it would leak
    the answer as surely as the entity id would. What survives the omission is
    the part a classifier could legitimately use: that a signal of this kind is
    shared, and with how many other subjects.
    """
    return [
        f"Infrastructure signal ({signal_type}) shared with {int(shared_with)} other subject(s)."
        for signal_type, shared_with in con.execute(_SHARED_SIGNALS, [subject_id]).fetchall()
    ]


def _render_channel(con: duckdb.DuckDBPyConnection, channel_id: str) -> str:
    profile = con.execute(_CHANNEL_PROFILE, [channel_id]).fetchone()
    lines: list[str] = ["Subject: a channel."]
    if profile is not None:
        lines.append(f"Display name: {profile[0]}")
        lines.append(f"Description: {profile[1]}")
        lines.append(f"Subscribers: {int(profile[2])}")

    videos = con.execute(_CHANNEL_VIDEOS, [channel_id]).fetchall()
    lines.append(f"Videos published: {len(videos)}")
    for title, description, disclosed, provenance in videos:
        lines.append(f'  "{title}" - {description}')
        lines.append(
            f"    synthetic media disclosed: {'yes' if disclosed else 'no'}; "
            f"content credentials: {provenance}"
        )

    lines.extend(_render_signals(con, channel_id))
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class EvalSet:
    """Items, labels and provenance, held apart.

    The three travel together only here, in the build-time value that produced
    them. Once written they are three files with three readers, and only one of
    those readers is allowed to hold the labels.
    """

    items: tuple[EvalItem, ...]
    labels: Mapping[str, ThreatClass]
    provenance: Mapping[str, object]

    def __post_init__(self) -> None:
        if len(self.items) != len(self.labels):
            raise EvalBuildError(
                f"{len(self.items)} items carry {len(self.labels)} labels; every item is labeled "
                "exactly once or the set cannot be graded"
            )
        ids = {item.item_id for item in self.items}
        if ids != set(self.labels):
            raise EvalBuildError("item ids and label keys disagree")

    def class_balance(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for label in self.labels.values():
            counts[label.value] = counts.get(label.value, 0) + 1
        return dict(sorted(counts.items()))


def build_eval_set(
    con: duckdb.DuckDBPyConnection,
    *,
    seed: int,
    scale: int,
    dataset_digest: str,
    generator_version: str,
) -> EvalSet:
    """Render every planted subject plus a seeded sample of benign controls.

    Every threat entity is taken, because there are 4 to 12 of them per class
    and sampling from that would be discarding the only data there is. Benign
    controls are sampled with the project's single seeded generator, so the set
    is reproducible from the recorded seed.
    """
    rows = con.execute(_SUBJECT_LABELS).fetchall()
    if not rows:
        raise EvalBuildError(
            "the dataset carries no account or channel labels; build one with "
            "'ts-sentry build-dataset' before building an eval set"
        )

    threats = [
        (EntityKind(k), str(i), ThreatClass(t)) for k, i, t in rows if t != ThreatClass.BENIGN
    ]
    benign = [
        (EntityKind(k), str(i), ThreatClass(t)) for k, i, t in rows if t == ThreatClass.BENIGN
    ]
    if not threats:
        raise EvalBuildError("the dataset carries no planted threat subjects")

    # n_benign / (n_threat + n_benign) == BENIGN_CONTROL_FRACTION
    wanted = round(len(threats) * BENIGN_CONTROL_FRACTION / (1.0 - BENIGN_CONTROL_FRACTION))
    rng = np.random.default_rng(seed)
    chosen = rng.choice(len(benign), size=min(wanted, len(benign)), replace=False)
    controls = [benign[int(index)] for index in sorted(chosen)]

    # Shuffled before ids are assigned, and this is a contamination control
    # rather than tidiness. `_SUBJECT_LABELS` returns rows ordered by entity id,
    # and planted ids are prefixed by class, so the natural order groups the set
    # into contiguous per-class blocks: items 0-5 t01, 6-11 t02, and every benign
    # control at the end. The ordinal would then carry the label as reliably as a
    # label field would, and `items.json` alone - which has no labels in it -
    # would be enough to reconstruct the entire answer key by reading the class
    # boundaries off the content.
    #
    # Found at the STEP-06 review stop by asking what an ordinal leaks, after
    # `_refuse_leaky_item` had been written to check content and only content.
    # The shuffle uses the same seeded generator, so the assignment is
    # reproducible from the recorded seed and is not a source of variation.
    ordered = [*threats, *controls]
    rng.shuffle(ordered)

    items: list[EvalItem] = []
    labels: dict[str, ThreatClass] = {}
    for position, (kind, entity_id, threat_class) in enumerate(ordered):
        item_id = f"{ITEM_ID_PREFIX}{position:04d}"
        content = (
            _render_account(con, entity_id)
            if kind is EntityKind.ACCOUNT
            else _render_channel(con, entity_id)
        )
        _refuse_leaky_item(item_id, content)
        items.append(EvalItem(item_id=item_id, content=content))
        labels[item_id] = threat_class

    provenance: dict[str, object] = {
        "schema": EVAL_SCHEMA,
        "generator_version": generator_version,
        "dataset_seed": seed,
        "dataset_scale": scale,
        "dataset_digest": dataset_digest,
        "benign_control_fraction": BENIGN_CONTROL_FRACTION,
        "label_provenance": (
            "Ground truth from the synthetic generator's own plant, read from sealed._labels at "
            "build time. These are generator plants, not human labels: there is no adjudication, "
            "no inter-rater reliability, and no rater-disagreement modelling."
        ),
    }
    return EvalSet(items=tuple(items), labels=labels, provenance=provenance)


def write_eval_set(root: Path, eval_set: EvalSet) -> None:
    """Write the three files. Items and labels never share one."""
    root.mkdir(parents=True, exist_ok=True)

    _write_json(root / ITEMS_FILE, [item.to_json_object() for item in eval_set.items])
    _write_json(
        root / LABELS_FILE,
        {item_id: label.value for item_id, label in sorted(eval_set.labels.items())},
    )
    _write_json(
        root / MANIFEST_FILE,
        {
            **dict(eval_set.provenance),
            "item_count": len(eval_set.items),
            "class_balance": eval_set.class_balance(),
            "items_sha256": items_digest(eval_set.items),
            "labels_sha256": labels_digest(eval_set.labels),
        },
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
