# SPDX-License-Identifier: MIT
"""D1: the pivot vocabulary and its parameterized query templates (STEP-04 D1).

ARCHITECTURE 4.2: the evidence agent "proposes pivots ... Each pivot is a
**deterministic, parameterized query** the analyst approves or rejects; the LLM
proposes which query to run next, it never composes free SQL (injection surface
removed)."

This module is that vocabulary. Five kinds, one reviewed template each, and
nothing else. It is one file on purpose, for the same reason
``orchestrator.tools`` keeps the allowlist flat and ``firewall.PATTERNS`` keeps
the detection set flat: a security surface nobody can read off in one sitting
is a security surface nobody audits. This one was read line by line before the
agent that proposes from it existed.

Zero dynamic SQL, and how that is achieved rather than asserted
--------------------------------------------------------------
STEP-04 3.1 is absolute: no string interpolation of user or model text into
SQL, params typed and bounds-checked. Four properties carry it, and each is
enforced somewhere rather than promised here:

1. **Table names come from ``resolve_table``**, the exhaustive ``match`` over
   ``DataScope`` closed by ``assert_never``. A table with no ``DataScope``
   member is unnameable in this module, so ``sealed._labels`` cannot be written
   here even by a typo. Same construction ``data.store`` and
   ``orchestrator.detection_stub`` already use.
2. **Every runtime value is bound with ``?``.** No value is formatted,
   concatenated, or interpolated into SQL text at any point. ``binding`` names
   which parameter feeds each placeholder, in order, so the mapping is data a
   test can check rather than a convention a reader has to trust.
3. **No parameter ever selects a column or a table.** Where a pivot spans two
   metadata fields or filters on an optional category, the template covers all
   the cases in its own fixed text and the parameter is a *value* compared in a
   ``WHERE`` clause against an ``'any'`` sentinel. This is the construction
   most worth reading closely below.
4. **Parameters are typed and bounds-checked before they are bound**, by
   ``validate_params``, and identifier-shaped parameters must additionally
   resolve to an entity already in the evidence pack.

Property 3 rests on a fact about DuckDB rather than about this code, so it was
verified against the installed version (1.5.5) rather than recalled:

* ``SELECT a FROM ?`` raises ``ParserException: syntax error at or near "?"``.
  A parameter cannot be a table identifier.
* ``SELECT ? FROM t`` with the value ``'a'`` returns the *string* ``'a'`` once
  per row, not the column ``a``. A bound parameter is always a value and never
  becomes an identifier, which is exactly what makes the sentinel comparison
  safe.
* ``LIMIT ?`` and ``OFFSET ?`` bind normally, so row ceilings are enforced
  through the parameter path like every other bound value.

Consulted for the placeholder contract itself:
https://duckdb.org/docs/current/sql/query_syntax/prepared_statements.html and
https://duckdb.org/docs/current/clients/python/dbapi.html, which specify the
auto-incremented ``?`` style and that values are passed positionally in a
sequence. Neither page addresses ``LIMIT`` or identifiers, which is why the
three behaviours above were measured.

No pivot returns user-authored text
-----------------------------------
Comment bodies, channel descriptions, video titles and display names are the
material an attacker controls, and they have exactly one route into this
system: ``detection_stub.case_records`` to the input firewall, which fences
them as inert data. A pivot that also returned them would open a second route
into an artifact that no firewall inspects. So the templates below select
identifiers, categories, counts and timestamps, and never a free-text column.
``FREE_TEXT_COLUMNS`` records the rule and a test enforces it against the SQL.

Every projection is aliased, and no query orders by position
-----------------------------------------------------------
From Saif's D1 review note: positional ``ORDER BY`` is safe as reviewed
literals but brittle if a SELECT list is later edited. The brittleness has a
silent half. Reordering a projection would make ``ORDER BY 2`` sort by a
different column *and* leave ``PivotTemplate.columns`` mislabelling every field
of every evidence record built from it, with the row count unchanged and
nothing failing.

So every projection carries an alias matching its declared column, ordering is
by alias, and a test compares ``columns`` against the names DuckDB reports for
the actual result set. That turns a hand-maintained mapping into a checked one:
a renamed or reordered projection fails a test rather than silently
mislabelling evidence.

Timestamps
----------
Selected as ``epoch_ms(...)``, never as ``TIMESTAMPTZ`` and never cast to text.
DuckDB renders a ``TIMESTAMPTZ`` in the *reader's* session time zone, so a text
cast would make two machines derive different evidence from one dataset with
neither looking wrong. That defect was avoided in STEP-02 D3 and found again in
STEP-03 D5; this is the same class, avoided in a third place.
"""

import re
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

from ts_sentry.data.enums import EngagementKind, InfraSignalKind
from ts_sentry.governance.canonical import digest_fields
from ts_sentry.governance.scopes import DataScope, resolve_table

__all__ = [
    "ANY",
    "FREE_TEXT_COLUMNS",
    "MAX_ROW_LIMIT",
    "PIVOT_TEMPLATES",
    "ParamFailure",
    "ParamFailureCode",
    "ParamKind",
    "ParamResult",
    "ParamSpec",
    "PivotKind",
    "PivotTemplate",
    "PivotViolation",
    "bind_values",
    "param_hash",
    "pivot_scope_names",
    "resolve_pivot_by_name",
    "template_sha256",
    "validate_params",
]

ANY = "any"
"""The sentinel that widens a categorical filter to every value.

A bound value, never a piece of SQL. ``(? = 'any' OR e.kind = ?)`` is a
comparison between a parameter and a literal that lives in the reviewed
template text; it is not a branch the caller can author.
"""

MAX_ROW_LIMIT = 200
"""Ceiling on rows any single pivot may return.

A pivot is one hop of an investigation, not a bulk export. The ceiling is
enforced as a parameter bound rather than by trimming afterwards, so an agent
asking for a million rows is refused at proposal time and the analyst sees what
was actually asked for.
"""

MAX_WINDOW_HOURS = 168
"""One week. Beyond this a temporal correlation is not a correlation."""

MAX_EVENT_FLOOR = 1000
MAX_EPOCH_MS = 4_102_444_800_000
"""2100-01-01T00:00:00Z in milliseconds. A static upper bound on an anchor
instant, so a bounds check exists at all rather than only a type check."""

FREE_TEXT_COLUMNS = frozenset({"text", "description", "title", "display_name"})
"""Columns no pivot template may select. See the module docstring: user
authored text reaches a model through the input firewall or not at all."""

_ENTITY_ID_PATTERN = re.compile(r"^[a-z0-9_]{1,64}$")
"""The id grammar this dataset actually uses (``acct_0000001``,
``t01_chan_003_001``). Deliberately narrow, and deliberately not the only check:
an id must also resolve to a node already in the pack."""

_TEMPLATE_DOMAIN = "ts-sentry/pivot-template/v1"
_PARAM_DOMAIN = "ts-sentry/pivot-params/v1"


class PivotKind(StrEnum):
    """The five pivots an evidence agent may propose (STEP-04 D1).

    Fixed by the STEP file, and fixed here: this is a vocabulary, not a
    registry. An agent proposes a member of this enum and a parameter map,
    which is the whole of what it may express about a query.
    """

    SHARED_METADATA = "shared_metadata"
    TEMPORAL_CORRELATION = "temporal_correlation"
    ENGAGEMENT_EDGE = "engagement_edge"
    INFRA_OVERLAP = "infra_overlap"
    ACCOUNT_LINK = "account_link"


class PivotViolation(Exception):
    """Raised when a pivot name resolves to nothing, or when binding is asked
    for values that were never validated.

    Mirrors ``scopes.ScopeViolation`` and ``toolspec.ToolViolation``, and for
    the same reason: an agent hands the orchestrator a string, so resolution is
    a real boundary rather than a formality.
    """


class ParamKind(StrEnum):
    """What a parameter is allowed to be."""

    ENTITY_ID = "entity_id"
    INTEGER = "integer"
    CHOICE = "choice"


class ParamFailureCode(StrEnum):
    """Why one parameter was refused. Countable by cause, like ``RefusalCode``."""

    MISSING = "missing"
    UNEXPECTED = "unexpected"
    WRONG_TYPE = "wrong_type"
    OUT_OF_BOUNDS = "out_of_bounds"
    NOT_A_CHOICE = "not_a_choice"
    MALFORMED_ID = "malformed_id"
    UNKNOWN_ENTITY = "unknown_entity"


@dataclass(frozen=True, slots=True)
class ParamFailure:
    param: str
    code: ParamFailureCode
    detail: str

    def to_json_object(self) -> dict[str, str]:
        return {"param": self.param, "code": self.code.value, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """One parameter's type and bounds.

    ``minimum``/``maximum`` apply to ``INTEGER`` and are inclusive; ``choices``
    applies to ``CHOICE``. The combination is validated at construction, so a
    spec that declares bounds on a string or choices on an integer cannot be
    written.
    """

    name: str
    kind: ParamKind
    description: str
    minimum: int | None = None
    maximum: int | None = None
    choices: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("every parameter is named")
        if not self.description.strip():
            raise ValueError(f"parameter {self.name} states what it is")
        bounded = self.minimum is not None or self.maximum is not None
        if self.kind is ParamKind.INTEGER:
            if self.minimum is None or self.maximum is None:
                raise ValueError(f"integer parameter {self.name} declares both bounds")
            if self.minimum > self.maximum:
                raise ValueError(f"parameter {self.name} has minimum above maximum")
            if self.choices:
                raise ValueError(f"integer parameter {self.name} cannot declare choices")
        elif self.kind is ParamKind.CHOICE:
            if not self.choices:
                raise ValueError(f"choice parameter {self.name} declares its choices")
            if bounded:
                raise ValueError(f"choice parameter {self.name} cannot declare numeric bounds")
        else:
            if bounded or self.choices:
                raise ValueError(f"entity-id parameter {self.name} takes no bounds or choices")

    def to_json_object(self) -> dict[str, object]:
        payload: dict[str, object] = {"name": self.name, "kind": self.kind.value}
        if self.kind is ParamKind.INTEGER:
            payload["minimum"] = self.minimum
            payload["maximum"] = self.maximum
        if self.kind is ParamKind.CHOICE:
            payload["choices"] = sorted(self.choices)
        return payload


def _entity_id(name: str, description: str) -> ParamSpec:
    return ParamSpec(name=name, kind=ParamKind.ENTITY_ID, description=description)


def _integer(name: str, description: str, minimum: int, maximum: int) -> ParamSpec:
    return ParamSpec(
        name=name,
        kind=ParamKind.INTEGER,
        description=description,
        minimum=minimum,
        maximum=maximum,
    )


def _choice(name: str, description: str, values: frozenset[str]) -> ParamSpec:
    return ParamSpec(
        name=name,
        kind=ParamKind.CHOICE,
        description=description,
        choices=frozenset({ANY}) | values,
    )


_ROW_LIMIT = _integer("limit", "Maximum rows this pivot may return.", 1, MAX_ROW_LIMIT)


@dataclass(frozen=True, slots=True)
class PivotTemplate:
    """One reviewed, parameterized query, and everything about how it is run.

    ``binding`` names the parameter feeding each ``?`` in order, so a template
    whose placeholders and parameters drift apart fails a test rather than
    binding the wrong value to the wrong slot. ``columns`` states the result
    shape, so the row-to-evidence mapping is checked against the query rather
    than against someone's memory of it.
    """

    kind: PivotKind
    template_id: str
    sql: str
    required_scopes: frozenset[DataScope]
    params: tuple[ParamSpec, ...]
    binding: tuple[str, ...]
    columns: tuple[str, ...]
    summary: str

    def __post_init__(self) -> None:
        if not self.template_id.strip():
            raise ValueError("every template carries an id")
        if not self.summary.strip():
            raise ValueError(f"template {self.template_id} states what it asks")
        if not self.required_scopes:
            raise ValueError(f"template {self.template_id} names the scopes it reads")
        names = {spec.name for spec in self.params}
        if len(names) != len(self.params):
            raise ValueError(f"template {self.template_id} declares a parameter twice")
        if self.sql.count("?") != len(self.binding):
            raise ValueError(
                f"template {self.template_id} has {self.sql.count('?')} placeholders and "
                f"{len(self.binding)} bindings; the two must agree exactly"
            )
        unknown = sorted(set(self.binding) - names)
        if unknown:
            raise ValueError(
                f"template {self.template_id} binds undeclared parameters: {', '.join(unknown)}"
            )
        unused = sorted(names - set(self.binding))
        if unused:
            raise ValueError(
                f"template {self.template_id} declares parameters it never binds: "
                f"{', '.join(unused)}"
            )

    @property
    def param_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.params)

    def spec(self, name: str) -> ParamSpec:
        for candidate in self.params:
            if candidate.name == name:
                return candidate
        raise PivotViolation(f"template {self.template_id} declares no parameter {name!r}")

    def to_json_object(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "template_id": self.template_id,
            "template_sha256": template_sha256(self),
            "summary": self.summary,
            "params": [spec.to_json_object() for spec in self.params],
            "columns": list(self.columns),
            "required_scopes": sorted(scope.value for scope in self.required_scopes),
        }


# --------------------------------------------------------------------------
# The templates
#
# Table names below come from `resolve_table`, so each SQL string is a fixed
# template per call rather than SQL built from runtime values. Every other
# varying part of every query is a `?` placeholder. Read with the module
# docstring's four properties in hand.
# --------------------------------------------------------------------------

_ACCOUNT_META = resolve_table(DataScope.ACCOUNT_META)
_CHANNEL = resolve_table(DataScope.CHANNEL)
_COMMENT = resolve_table(DataScope.COMMENT)
_ENGAGEMENT_EVENT = resolve_table(DataScope.ENGAGEMENT_EVENT)
_INFRA_HINT = resolve_table(DataScope.INFRA_HINT)
_VIDEO = resolve_table(DataScope.VIDEO)

_SHARED_METADATA_SQL = f"""
SELECT peer.account_id AS peer_account_id,
       'signup_ip_bucket' AS metadata_field,
       subject.signup_ip_bucket AS shared_value
FROM {_ACCOUNT_META} AS subject
JOIN {_ACCOUNT_META} AS peer
  ON peer.signup_ip_bucket = subject.signup_ip_bucket
WHERE subject.account_id = ?
  AND peer.account_id <> subject.account_id
  AND (? = 'any' OR ? = 'signup_ip_bucket')
UNION ALL
SELECT peer.account_id AS peer_account_id,
       'device_fingerprint_hint' AS metadata_field,
       subject.device_fingerprint_hint AS shared_value
FROM {_ACCOUNT_META} AS subject
JOIN {_ACCOUNT_META} AS peer
  ON peer.device_fingerprint_hint = subject.device_fingerprint_hint
WHERE subject.account_id = ?
  AND peer.account_id <> subject.account_id
  AND subject.device_fingerprint_hint IS NOT NULL
  AND (? = 'any' OR ? = 'device_fingerprint_hint')
ORDER BY metadata_field, peer_account_id
LIMIT ?
"""
"""Accounts sharing a registration-time metadata value with one account.

The ``metadata_field`` parameter is the construction worth reading twice. Both
fields are covered by the template's own fixed text, as two ``UNION ALL``
branches, and the parameter only ever appears as a *value* on the left of a
comparison against a literal that is part of the reviewed query. Selecting the
column by parameter would have been the natural shape and would have been
dynamic SQL; this is the same feature with the identifier taken out of the
caller's hands.
"""

_TEMPORAL_CORRELATION_SQL = f"""
SELECT cm.comment_id AS comment_id,
       cm.account_id AS account_id,
       v.video_id AS video_id,
       epoch_ms(cm.posted_ts) AS posted_epoch_ms,
       cm.template_id AS template_id
FROM {_COMMENT} AS cm
JOIN {_VIDEO} AS v ON cm.video_id = v.video_id
WHERE v.channel_id = ?
  AND epoch_ms(cm.posted_ts) BETWEEN ? - (? * 3600000) AND ? + (? * 3600000)
ORDER BY posted_epoch_ms, comment_id
LIMIT ?
"""
"""Comments on one channel's videos inside a window around an anchor instant.

The window arithmetic is in the template, over bound integers. Converting hours
to milliseconds in Python and binding the product would work equally well;
doing it here keeps the reviewed text stating the unit conversion, so a reader
can see that ``window_hours`` means hours without leaving this file.
"""

_ENGAGEMENT_EDGE_SQL = f"""
SELECT e.account_id AS account_id,
       'video' AS target_kind,
       v.video_id AS target_id,
       e.kind AS engagement_kind,
       COUNT(*) AS event_count,
       MIN(epoch_ms(e.ts_ist)) AS first_epoch_ms,
       MAX(epoch_ms(e.ts_ist)) AS last_epoch_ms
FROM {_ENGAGEMENT_EVENT} AS e
JOIN {_VIDEO} AS v ON e.video_id = v.video_id
WHERE v.channel_id = ?
  AND (? = 'any' OR e.kind = ?)
GROUP BY e.account_id, v.video_id, e.kind
HAVING COUNT(*) >= ?
UNION ALL
SELECT e.account_id AS account_id,
       'channel' AS target_kind,
       e.channel_id AS target_id,
       e.kind AS engagement_kind,
       COUNT(*) AS event_count,
       MIN(epoch_ms(e.ts_ist)) AS first_epoch_ms,
       MAX(epoch_ms(e.ts_ist)) AS last_epoch_ms
FROM {_ENGAGEMENT_EVENT} AS e
WHERE e.channel_id = ?
  AND (? = 'any' OR e.kind = ?)
GROUP BY e.account_id, e.channel_id, e.kind
HAVING COUNT(*) >= ?
ORDER BY event_count DESC, account_id, target_id
LIMIT ?
"""
"""Accounts engaging with one channel, by target and kind.

Two branches because ``EngagementEvent`` targets exactly one of ``video_id`` or
``channel_id`` depending on its kind, and a subscribe is a channel-level event.
A single video-joined query would have silently dropped every subscribe, which
is the engagement signal a sub-for-sub ring (T-02) is built from.
"""

_INFRA_OVERLAP_SQL = f"""
SELECT peer.subject_id AS peer_subject_id,
       peer.subject_kind AS peer_subject_kind,
       peer.signal_type AS signal_type,
       peer.signal_value AS signal_value,
       COUNT(*) AS hint_count,
       MIN(epoch_ms(peer.observed_ts)) AS first_epoch_ms,
       MAX(epoch_ms(peer.observed_ts)) AS last_epoch_ms
FROM {_INFRA_HINT} AS subject
JOIN {_INFRA_HINT} AS peer
  ON peer.signal_type = subject.signal_type
 AND peer.signal_value = subject.signal_value
WHERE subject.subject_id = ?
  AND peer.subject_id <> subject.subject_id
  AND (? = 'any' OR subject.signal_type = ?)
GROUP BY peer.subject_id, peer.subject_kind, peer.signal_type, peer.signal_value
ORDER BY signal_type, peer_subject_id
LIMIT ?
"""
"""Other subjects carrying an infrastructure signal value this subject carries.

The self-join is the pivot: shared infrastructure is what turns a set of
unrelated accounts into a ring, and the ``signal_value`` is the shared evidence
the analyst is being shown, not an inference about it.
"""

_ACCOUNT_LINK_SQL = f"""
SELECT ch.account_id AS account_id, 'owner' AS relation, 1 AS weight
FROM {_CHANNEL} AS ch
WHERE ch.channel_id = ?
UNION ALL
SELECT cm.account_id AS account_id, 'commenter' AS relation, COUNT(*) AS weight
FROM {_COMMENT} AS cm
JOIN {_VIDEO} AS v ON cm.video_id = v.video_id
WHERE v.channel_id = ?
GROUP BY cm.account_id
HAVING COUNT(*) >= ?
ORDER BY relation, weight DESC, account_id
LIMIT ?
"""
"""Accounts linked to one channel: the owner, and the accounts commenting on it.

Owner and commenters arrive from one query rather than two so that the hop is
one ledgered execution with one provenance record. They are distinguished by
the ``relation`` column, which is template text.
"""


PIVOT_TEMPLATES: Mapping[PivotKind, PivotTemplate] = {
    PivotKind.SHARED_METADATA: PivotTemplate(
        kind=PivotKind.SHARED_METADATA,
        template_id="pivot.shared_metadata.v1",
        sql=_SHARED_METADATA_SQL,
        required_scopes=frozenset({DataScope.ACCOUNT_META}),
        params=(
            _entity_id("account_id", "The account whose registration metadata is matched."),
            _choice(
                "metadata_field",
                "Which metadata field to match on, or 'any' for both.",
                frozenset({"signup_ip_bucket", "device_fingerprint_hint"}),
            ),
            _ROW_LIMIT,
        ),
        binding=(
            "account_id",
            "metadata_field",
            "metadata_field",
            "account_id",
            "metadata_field",
            "metadata_field",
            "limit",
        ),
        columns=("peer_account_id", "metadata_field", "shared_value"),
        summary="Accounts sharing a signup IP bucket or device fingerprint with this account.",
    ),
    PivotKind.TEMPORAL_CORRELATION: PivotTemplate(
        kind=PivotKind.TEMPORAL_CORRELATION,
        template_id="pivot.temporal_correlation.v1",
        sql=_TEMPORAL_CORRELATION_SQL,
        required_scopes=frozenset({DataScope.COMMENT, DataScope.VIDEO}),
        params=(
            _entity_id("channel_id", "The channel whose videos are searched."),
            _integer(
                "anchor_epoch_ms",
                "Centre of the window, in epoch milliseconds.",
                0,
                MAX_EPOCH_MS,
            ),
            _integer(
                "window_hours",
                "Half-width of the window, in hours.",
                1,
                MAX_WINDOW_HOURS,
            ),
            _ROW_LIMIT,
        ),
        binding=(
            "channel_id",
            "anchor_epoch_ms",
            "window_hours",
            "anchor_epoch_ms",
            "window_hours",
            "limit",
        ),
        columns=("comment_id", "account_id", "video_id", "posted_epoch_ms", "template_id"),
        summary="Comments on this channel's videos within a window around an anchor instant.",
    ),
    PivotKind.ENGAGEMENT_EDGE: PivotTemplate(
        kind=PivotKind.ENGAGEMENT_EDGE,
        template_id="pivot.engagement_edge.v1",
        sql=_ENGAGEMENT_EDGE_SQL,
        required_scopes=frozenset({DataScope.ENGAGEMENT_EVENT, DataScope.VIDEO}),
        params=(
            _entity_id("channel_id", "The channel whose engagement is summarized."),
            _choice(
                "kind",
                "Engagement kind to count, or 'any' for all kinds.",
                frozenset(kind.value for kind in EngagementKind),
            ),
            _integer(
                "min_events",
                "Minimum events before an account appears.",
                1,
                MAX_EVENT_FLOOR,
            ),
            _ROW_LIMIT,
        ),
        binding=(
            "channel_id",
            "kind",
            "kind",
            "min_events",
            "channel_id",
            "kind",
            "kind",
            "min_events",
            "limit",
        ),
        columns=(
            "account_id",
            "target_kind",
            "target_id",
            "engagement_kind",
            "event_count",
            "first_epoch_ms",
            "last_epoch_ms",
        ),
        summary="Accounts engaging with this channel's videos or the channel itself.",
    ),
    PivotKind.INFRA_OVERLAP: PivotTemplate(
        kind=PivotKind.INFRA_OVERLAP,
        template_id="pivot.infra_overlap.v1",
        sql=_INFRA_OVERLAP_SQL,
        required_scopes=frozenset({DataScope.INFRA_HINT}),
        params=(
            _entity_id("subject_id", "The account or channel whose signals are matched."),
            _choice(
                "signal_type",
                "Infrastructure signal to match on, or 'any' for all types.",
                frozenset(kind.value for kind in InfraSignalKind),
            ),
            _ROW_LIMIT,
        ),
        binding=("subject_id", "signal_type", "signal_type", "limit"),
        columns=(
            "peer_subject_id",
            "peer_subject_kind",
            "signal_type",
            "signal_value",
            "hint_count",
            "first_epoch_ms",
            "last_epoch_ms",
        ),
        summary="Other subjects carrying an infrastructure signal value this subject carries.",
    ),
    PivotKind.ACCOUNT_LINK: PivotTemplate(
        kind=PivotKind.ACCOUNT_LINK,
        template_id="pivot.account_link.v1",
        sql=_ACCOUNT_LINK_SQL,
        required_scopes=frozenset({DataScope.CHANNEL, DataScope.COMMENT, DataScope.VIDEO}),
        params=(
            _entity_id("channel_id", "The channel whose linked accounts are listed."),
            _integer(
                "min_comments",
                "Minimum comments before a commenter appears.",
                1,
                MAX_EVENT_FLOOR,
            ),
            _ROW_LIMIT,
        ),
        binding=("channel_id", "channel_id", "min_comments", "limit"),
        columns=("account_id", "relation", "weight"),
        summary="Accounts linked to this channel: its owner, and accounts commenting on it.",
    ),
}
"""Every ``PivotKind``, one reviewed template each.

Keyed by kind and exhaustive over it, asserted by a test: a kind with no
template is a pivot an agent could name and nothing could run, which is the
orphan problem ``ToolId`` already carries a rule about.
"""


def template_sha256(template: PivotTemplate) -> str:
    """Digest over a template's id and its exact SQL text.

    Recorded in every provenance record, so a stored evidence record names the
    query that produced it by content rather than by label. Editing a template
    without bumping its id changes this digest, which is what makes a silent
    edit visible in an old pack.
    """
    return digest_fields(_TEMPLATE_DOMAIN, template.template_id, template.sql)


def param_hash(values: Mapping[str, object]) -> str:
    """Digest over the parameter values one execution was given.

    STEP-04 D3 requires a param hash per record. Values are sorted by name and
    rendered with their type, so ``{"limit": 5}`` and ``{"limit": "5"}`` do not
    collide: a pivot run with a string where an integer belonged is a different
    execution and must not be recorded as the same one.
    """
    return digest_fields(
        _PARAM_DOMAIN,
        *(f"{name}={type(values[name]).__name__}:{values[name]}" for name in sorted(values)),
    )


def resolve_pivot_by_name(name: str) -> PivotKind:
    """Resolve an agent-supplied pivot *name*, or deny it.

    Allowlist semantics, identical to ``scopes.resolve_scope_by_name`` and
    ``toolspec.resolve_tool_by_name``: lookup is by enum value, so a name with
    no member is denied by construction rather than by a list of things to
    reject.
    """
    try:
        return PivotKind(name)
    except ValueError as exc:
        raise PivotViolation(f"no PivotKind member resolves {name!r}") from exc


def pivot_scope_names(template: PivotTemplate) -> tuple[str, ...]:
    """The scope names this template reads, sorted.

    Mirrors ``toolspec.required_scope_names``: the handler checks the granted
    set covers this, so a pivot cannot read a table the dispatch that let it
    run never granted.
    """
    return tuple(sorted(scope.value for scope in template.required_scopes))


# --------------------------------------------------------------------------
# Parameter validation
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParamResult:
    """Outcome of checking a proposed parameter map. A value, never an exception.

    Failures are returned rather than raised, following STEP-02 2.4: a
    governance layer that signals rejection by throwing is one whose rejections
    can be swallowed by an ``except``. ``values`` is populated only when the
    whole map passed, so there is no half-validated map for a caller to reach
    for by mistake.
    """

    ok: bool
    values: Mapping[str, object]
    failures: tuple[ParamFailure, ...]

    def __post_init__(self) -> None:
        if self.ok is bool(self.failures):
            raise ValueError("a passing result carries no failures; a failing one carries some")
        if not self.ok and self.values:
            raise ValueError("a failing result carries no validated values")

    @property
    def detail(self) -> str:
        return "; ".join(f"{failure.param}: {failure.detail}" for failure in self.failures)

    def to_json_object(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "failures": [failure.to_json_object() for failure in self.failures],
        }


def _check_entity_id(
    spec: ParamSpec, raw: object, known_ids: AbstractSet[str]
) -> ParamFailure | None:
    if not isinstance(raw, str):
        return ParamFailure(
            param=spec.name,
            code=ParamFailureCode.WRONG_TYPE,
            detail=f"expected an entity id string; got {type(raw).__name__}",
        )
    if _ENTITY_ID_PATTERN.match(raw) is None:
        return ParamFailure(
            param=spec.name,
            code=ParamFailureCode.MALFORMED_ID,
            detail=f"{raw!r} is not a well-formed entity id",
        )
    if raw not in known_ids:
        return ParamFailure(
            param=spec.name,
            code=ParamFailureCode.UNKNOWN_ENTITY,
            detail=(
                f"{raw!r} is not an entity already in this evidence pack; a pivot expands "
                "from what has been gathered, it does not name new entities"
            ),
        )
    return None


def _check_integer(spec: ParamSpec, raw: object) -> ParamFailure | None:
    # `bool` is a subclass of `int`, so True would otherwise pass as 1 and be
    # bound as a row limit. The manifest reader rejects it the same way.
    if not isinstance(raw, int) or isinstance(raw, bool):
        return ParamFailure(
            param=spec.name,
            code=ParamFailureCode.WRONG_TYPE,
            detail=f"expected an integer; got {type(raw).__name__}",
        )
    assert spec.minimum is not None and spec.maximum is not None  # ParamSpec guarantees both
    if not spec.minimum <= raw <= spec.maximum:
        return ParamFailure(
            param=spec.name,
            code=ParamFailureCode.OUT_OF_BOUNDS,
            detail=f"{raw} is outside the allowed range {spec.minimum}..{spec.maximum}",
        )
    return None


def _check_choice(spec: ParamSpec, raw: object) -> ParamFailure | None:
    if not isinstance(raw, str):
        return ParamFailure(
            param=spec.name,
            code=ParamFailureCode.WRONG_TYPE,
            detail=f"expected one of {sorted(spec.choices)}; got {type(raw).__name__}",
        )
    if raw not in spec.choices:
        return ParamFailure(
            param=spec.name,
            code=ParamFailureCode.NOT_A_CHOICE,
            detail=f"{raw!r} is not one of {sorted(spec.choices)}",
        )
    return None


def validate_params(
    template: PivotTemplate,
    raw: Mapping[str, object],
    *,
    known_ids: AbstractSet[str],
) -> ParamResult:
    """Type and bounds check a proposed parameter map (STEP-04 3.1).

    Pure and total: no I/O, no ledger write, no exception on any input,
    including inputs that are the wrong type entirely. That matters because the
    map arrives from a model, so "arbitrary object under an arbitrary key" is
    the real domain rather than a hostile edge case.

    Every declared parameter must be present and every present parameter must
    be declared. An unexpected key is refused rather than ignored: a silently
    dropped parameter would let a proposal read as something other than what
    was executed, and the analyst approves the proposal.

    ``known_ids`` is the set of entity ids already in the evidence pack.
    Identifier-shaped parameters must resolve against it, so an agent cannot
    name an entity the investigation has not reached. Pivots expand outward
    from the analyst-selected seed; they do not teleport.
    """
    failures: list[ParamFailure] = []
    values: dict[str, object] = {}

    for name in sorted(set(raw) - set(template.param_names)):
        failures.append(
            ParamFailure(
                param=name,
                code=ParamFailureCode.UNEXPECTED,
                detail=f"template {template.template_id} declares no parameter {name!r}",
            )
        )

    for spec in template.params:
        if spec.name not in raw:
            failures.append(
                ParamFailure(
                    param=spec.name,
                    code=ParamFailureCode.MISSING,
                    detail=f"{spec.name} is required: {spec.description}",
                )
            )
            continue

        supplied = raw[spec.name]
        # Exhaustive and closed by `assert_never`, the same construction
        # `scopes.resolve_table` uses: a new ParamKind that nobody wrote a check
        # for is a type error here rather than a parameter that validates by
        # falling through.
        match spec.kind:
            case ParamKind.ENTITY_ID:
                failure = _check_entity_id(spec, supplied, known_ids)
            case ParamKind.INTEGER:
                failure = _check_integer(spec, supplied)
            case ParamKind.CHOICE:
                failure = _check_choice(spec, supplied)
            case _:  # pragma: no cover - exhaustiveness guard, unreachable per mypy
                assert_never(spec.kind)

        if failure is None:
            values[spec.name] = supplied
        else:
            failures.append(failure)

    if failures:
        return ParamResult(ok=False, values={}, failures=tuple(failures))
    return ParamResult(ok=True, values=values, failures=())


def bind_values(template: PivotTemplate, values: Mapping[str, object]) -> list[object]:
    """The positional argument vector for ``connection.execute(sql, ...)``.

    Ordered by ``template.binding``, which names the parameter behind each
    placeholder. Refuses a map missing any bound name rather than binding
    ``None`` into a query: a query that runs with a silently absent parameter
    is a query nobody proposed.
    """
    missing = sorted({name for name in template.binding} - set(values))
    if missing:
        raise PivotViolation(
            f"template {template.template_id} cannot bind without {', '.join(missing)}; "
            "validate_params produces the map this takes"
        )
    return [values[name] for name in template.binding]
