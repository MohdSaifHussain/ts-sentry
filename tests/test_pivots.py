# SPDX-License-Identifier: MIT
"""STEP-04 D1: the pivot vocabulary, its templates, and the no-dynamic-SQL rule.

STEP-04 3.1 is the requirement these tests exist for: "no string interpolation
of user or model text into SQL; params typed and bounds-checked". The exit
checklist calls for a grep and a review note. A grep proves the absence of one
spelling; these assert the property.

Four of them do the load-bearing work and fail for different reasons on
purpose:

* ``test_the_only_interpolation_is_a_resolved_table_name`` walks the module's
  AST and proves that the only thing any f-string in it can interpolate is a
  name bound to ``resolve_table(DataScope.X)``. This is the test that would
  catch a future author formatting a parameter into a query.
* ``test_no_template_selects_user_authored_text`` keeps the second route into
  a model closed: case text arrives through the firewall or not at all.
* ``test_every_table_named_by_every_template_resolves_through_datascope``
  asserts allowlist containment against the SQL rather than the docstring.
* the parameter corpus asserts that hostile values are refused before binding,
  and that DuckDB treats a bound value as a value even when it looks like SQL.
"""

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from ts_sentry.data.enums import EngagementKind, InfraSignalKind
from ts_sentry.data.generator import build_dataset
from ts_sentry.data.population import BuildConfig
from ts_sentry.data.store import persist_dataset
from ts_sentry.governance.scopes import DataScope, resolve_table
from ts_sentry.orchestrator import pivots
from ts_sentry.orchestrator.pivots import (
    ANY,
    FREE_TEXT_COLUMNS,
    MAX_ROW_LIMIT,
    PIVOT_TEMPLATES,
    ParamFailureCode,
    ParamKind,
    ParamSpec,
    PivotKind,
    PivotTemplate,
    PivotViolation,
    bind_values,
    param_hash,
    pivot_scope_names,
    resolve_pivot_by_name,
    template_sha256,
    validate_params,
)

_MODULE_PATH = Path(pivots.__file__)
_ALL_TABLES = frozenset(resolve_table(scope) for scope in DataScope)


@pytest.fixture(scope="module")
def connection() -> Iterator[duckdb.DuckDBPyConnection]:
    """A real seed-42 build. The templates are checked against the schema they
    will actually run against, not against a hand-made fixture that could drift
    from it."""
    con = duckdb.connect()
    persist_dataset(con, build_dataset(BuildConfig(seed=42, scale=1)))
    yield con
    con.close()


# --------------------------------------------------------------------------
# The vocabulary is complete and keyed by itself
# --------------------------------------------------------------------------


def test_every_pivot_kind_has_a_template() -> None:
    """The no-orphan rule, in the shape ``ToolId`` already carries: a kind an
    agent can name and nothing can run is a hole in the vocabulary."""
    assert set(PIVOT_TEMPLATES) == set(PivotKind)


def test_each_template_is_keyed_by_the_kind_it_declares() -> None:
    for kind, template in PIVOT_TEMPLATES.items():
        assert template.kind is kind


def test_template_ids_are_unique_and_versioned() -> None:
    ids = [template.template_id for template in PIVOT_TEMPLATES.values()]

    assert len(set(ids)) == len(ids)
    for template_id in ids:
        assert template_id.startswith("pivot.")
        assert re.search(r"\.v\d+$", template_id), f"{template_id} carries no version suffix"


def test_pivot_names_resolve_by_allowlist_and_absence_is_denial() -> None:
    assert resolve_pivot_by_name("infra_overlap") is PivotKind.INFRA_OVERLAP

    for name in ("", "sealed._labels", "INFRA_OVERLAP", "infra_overlap ", "drop_table"):
        with pytest.raises(PivotViolation):
            resolve_pivot_by_name(name)


# --------------------------------------------------------------------------
# Zero dynamic SQL (STEP-04 3.1)
# --------------------------------------------------------------------------


def _resolved_table_names() -> set[str]:
    """Module-level names bound to a ``resolve_table(...)`` call."""
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        function = node.value.func
        if isinstance(function, ast.Name) and function.id == "resolve_table":
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
    return names


_SQL_KEYWORDS = re.compile(r"\b(?:SELECT|FROM|JOIN|WHERE|UNION|GROUP BY|HAVING|LIMIT)\b")


def _module_level_fstrings() -> list[tuple[str, ast.JoinedStr]]:
    """Module-scope assignments whose value is an f-string. The SQL lives here."""
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    found: list[tuple[str, ast.JoinedStr]] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.JoinedStr):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.append((target.id, node.value))
    return found


def test_the_only_interpolation_is_a_resolved_table_name() -> None:
    """The central guarantee of this phase, asserted structurally.

    Every SQL-carrying f-string is walked, and every interpolated expression
    must be a bare name bound at module scope to ``resolve_table(DataScope.X)``.
    One carrying a parameter, a call, an attribute, or any other expression
    fails here. A grep could not do this: it would prove one bad spelling
    absent rather than every bad shape impossible.

    Scoped to module-level f-strings because that is where the templates are
    built and because error messages elsewhere in the module are legitimately
    interpolated prose. ``test_no_sql_is_built_inside_a_function`` closes the
    hole that scoping would otherwise open.
    """
    resolved = _resolved_table_names()
    assert resolved, "the AST walk found no resolve_table bindings; the analysis is broken"

    interpolated = 0
    for name, joined in _module_level_fstrings():
        for value in joined.values:
            if not isinstance(value, ast.FormattedValue):
                continue
            interpolated += 1
            expression = value.value
            assert isinstance(expression, ast.Name), (
                f"{name} (line {value.lineno}) interpolates "
                f"{ast.dump(expression)[:80]}; only a resolved table name is allowed"
            )
            assert expression.id in resolved, (
                f"{name} (line {value.lineno}) interpolates {expression.id!r}, which is not "
                f"bound to resolve_table(); allowed names are {sorted(resolved)}"
            )

    assert interpolated >= len(_ALL_TABLES), "expected every entity table to appear in a template"


def test_no_sql_is_built_inside_a_function() -> None:
    """The templates are constants, and only constants.

    Without this, the test above could be satisfied by moving query
    construction into a function body, where its f-strings would no longer be
    module-level and no longer checked. Any f-string carrying a SQL keyword has
    to be one of the reviewed module-level templates.
    """
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    # Keyed by source position, not by object identity: the helper re-parses
    # the file, so its nodes are different objects describing the same code.
    module_level = {(joined.lineno, joined.col_offset) for _, joined in _module_level_fstrings()}

    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        if (node.lineno, node.col_offset) in module_level:
            continue
        literal = "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
        assert _SQL_KEYWORDS.search(literal) is None, (
            f"line {node.lineno}: an f-string outside module scope carries SQL; every query in "
            "this module is a reviewed constant"
        )


def test_the_module_uses_no_other_string_building_mechanism() -> None:
    """``.format`` and ``%`` interpolation are absent entirely.

    The AST test above covers f-strings, which is what this module uses. This
    one closes the two other ways a future edit could build a query, so that
    switching mechanism does not switch off the guarantee.
    """
    source = _MODULE_PATH.read_text(encoding="utf-8")

    assert ".format(" not in source
    assert "%s" not in source
    assert " % " not in source


def test_no_template_contains_a_placeholder_it_does_not_bind() -> None:
    """Placeholder count and binding length agree, per template.

    ``PivotTemplate.__post_init__`` enforces this at construction; asserting it
    over the real table is what makes the enforcement non-vacuous.
    """
    for template in PIVOT_TEMPLATES.values():
        assert template.sql.count("?") == len(template.binding)
        assert set(template.binding) == set(template.param_names)


def test_a_template_whose_placeholders_and_bindings_disagree_is_unconstructible() -> None:
    with pytest.raises(ValueError, match="placeholders"):
        PivotTemplate(
            kind=PivotKind.ACCOUNT_LINK,
            template_id="pivot.broken.v1",
            sql="SELECT 1 WHERE a = ? AND b = ?",
            required_scopes=frozenset({DataScope.CHANNEL}),
            params=(ParamSpec(name="a", kind=ParamKind.ENTITY_ID, description="x"),),
            binding=("a",),
            columns=("one",),
            summary="broken",
        )


def test_a_template_declaring_a_parameter_it_never_binds_is_unconstructible() -> None:
    """The other direction. A declared-but-unbound parameter is a parameter the
    analyst is shown and the query ignores, which makes the approved proposal
    and the executed query two different things."""
    with pytest.raises(ValueError, match="never binds"):
        PivotTemplate(
            kind=PivotKind.ACCOUNT_LINK,
            template_id="pivot.broken.v2",
            sql="SELECT 1 WHERE a = ?",
            required_scopes=frozenset({DataScope.CHANNEL}),
            params=(
                ParamSpec(name="a", kind=ParamKind.ENTITY_ID, description="x"),
                ParamSpec(name="unused", kind=ParamKind.ENTITY_ID, description="y"),
            ),
            binding=("a",),
            columns=("one",),
            summary="broken",
        )


def test_every_table_named_by_every_template_resolves_through_datascope() -> None:
    """Allowlist containment, asserted against the SQL text.

    ``sealed._labels`` has no ``DataScope`` member, so it cannot be produced by
    ``resolve_table`` and cannot appear here. Reading the names back out of the
    finished queries is what turns that from an argument about how the module
    is written into a fact about what it will run.
    """
    referenced = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_.]*)")

    for template in PIVOT_TEMPLATES.values():
        tables = set(referenced.findall(template.sql))
        assert tables, f"{template.template_id} names no table"
        assert tables <= _ALL_TABLES, (
            f"{template.template_id} reads {sorted(tables - _ALL_TABLES)}, which no DataScope "
            "member resolves"
        )
        assert "sealed" not in template.sql


def test_declared_scopes_match_the_tables_each_template_actually_reads() -> None:
    """A template that reads more than it declares would be granted less than
    it needs and fail at the handler; one that declares more than it reads
    would ask a mandate for access nothing uses."""
    referenced = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_.]*)")

    for template in PIVOT_TEMPLATES.values():
        declared = {resolve_table(scope) for scope in template.required_scopes}
        assert set(referenced.findall(template.sql)) == declared, template.template_id


def test_no_template_selects_user_authored_text() -> None:
    """Comment bodies, titles, descriptions and display names reach a model
    through the input firewall or not at all.

    A pivot returning them would open a second route into an artifact no
    firewall inspects. Matched as whole words, so ``cm.template_id`` (a
    generator-assigned marker, not free text) is not a false positive.
    """
    for template in PIVOT_TEMPLATES.values():
        for column in FREE_TEXT_COLUMNS:
            assert re.search(rf"\b{column}\b", template.sql) is None, (
                f"{template.template_id} selects the free-text column {column!r}"
            )


# --------------------------------------------------------------------------
# Parameter typing and bounds
# --------------------------------------------------------------------------


def test_a_valid_parameter_map_passes_and_carries_its_values() -> None:
    template = PIVOT_TEMPLATES[PivotKind.INFRA_OVERLAP]

    result = validate_params(
        template,
        {"subject_id": "acct_0000037", "signal_type": "shared_device", "limit": 10},
        known_ids={"acct_0000037"},
    )

    assert result.ok
    assert result.failures == ()
    assert result.values == {
        "subject_id": "acct_0000037",
        "signal_type": "shared_device",
        "limit": 10,
    }


def test_a_missing_parameter_is_refused() -> None:
    template = PIVOT_TEMPLATES[PivotKind.INFRA_OVERLAP]

    result = validate_params(template, {"subject_id": "acct_0000037"}, known_ids={"acct_0000037"})

    assert not result.ok
    assert {failure.code for failure in result.failures} == {ParamFailureCode.MISSING}
    assert {failure.param for failure in result.failures} == {"signal_type", "limit"}


def test_an_unexpected_parameter_is_refused_rather_than_ignored() -> None:
    """Silently dropping it would let the proposal the analyst approved differ
    from the query that ran."""
    template = PIVOT_TEMPLATES[PivotKind.INFRA_OVERLAP]

    result = validate_params(
        template,
        {"subject_id": "acct_0000037", "signal_type": ANY, "limit": 5, "order_by": "1; DROP"},
        known_ids={"acct_0000037"},
    )

    assert not result.ok
    assert result.failures[0].code is ParamFailureCode.UNEXPECTED
    assert result.failures[0].param == "order_by"


@pytest.mark.parametrize(
    ("limit", "code"),
    [
        (0, ParamFailureCode.OUT_OF_BOUNDS),
        (-1, ParamFailureCode.OUT_OF_BOUNDS),
        (MAX_ROW_LIMIT + 1, ParamFailureCode.OUT_OF_BOUNDS),
        (1_000_000, ParamFailureCode.OUT_OF_BOUNDS),
        ("10", ParamFailureCode.WRONG_TYPE),
        (10.0, ParamFailureCode.WRONG_TYPE),
        (None, ParamFailureCode.WRONG_TYPE),
        (True, ParamFailureCode.WRONG_TYPE),
    ],
)
def test_integer_bounds_and_types_are_enforced(limit: object, code: ParamFailureCode) -> None:
    """``True`` is in this list deliberately. ``bool`` subclasses ``int``, so an
    unguarded check would accept it and bind ``True`` as a row limit."""
    template = PIVOT_TEMPLATES[PivotKind.INFRA_OVERLAP]

    result = validate_params(
        template,
        {"subject_id": "acct_0000037", "signal_type": ANY, "limit": limit},
        known_ids={"acct_0000037"},
    )

    assert not result.ok
    assert [failure.code for failure in result.failures] == [code]
    assert result.failures[0].param == "limit"


def test_a_choice_outside_its_enum_is_refused() -> None:
    template = PIVOT_TEMPLATES[PivotKind.ENGAGEMENT_EDGE]

    result = validate_params(
        template,
        {"channel_id": "chan_000016", "kind": "watch", "min_events": 1, "limit": 5},
        known_ids={"chan_000016"},
    )

    assert not result.ok
    assert result.failures[0].code is ParamFailureCode.NOT_A_CHOICE


def test_choice_parameters_accept_their_enum_and_the_any_sentinel() -> None:
    template = PIVOT_TEMPLATES[PivotKind.ENGAGEMENT_EDGE]

    for value in [ANY, *(kind.value for kind in EngagementKind)]:
        result = validate_params(
            template,
            {"channel_id": "chan_000016", "kind": value, "min_events": 1, "limit": 5},
            known_ids={"chan_000016"},
        )
        assert result.ok, result.detail

    overlap = PIVOT_TEMPLATES[PivotKind.INFRA_OVERLAP]
    for value in [ANY, *(kind.value for kind in InfraSignalKind)]:
        result = validate_params(
            overlap,
            {"subject_id": "acct_0000037", "signal_type": value, "limit": 5},
            known_ids={"acct_0000037"},
        )
        assert result.ok, result.detail


HOSTILE_IDS = (
    "'; DROP TABLE main.channel; --",
    "sealed._labels",
    "acct_0000037' OR '1'='1",
    "main.channel",
    "acct_0000037; SELECT * FROM sealed._labels",
    "ACCT_0000037",
    "acct_0000037 ",
    "../../etc/passwd",
    "acct‮0000037",
    "",
    "a" * 65,
)


@pytest.mark.parametrize("hostile", HOSTILE_IDS)
def test_hostile_entity_ids_are_refused_before_anything_is_bound(hostile: str) -> None:
    """The corpus STEP-04 3.1 implies, run against the id grammar.

    Every one is refused, and the refusal names which check fired: malformed
    for anything outside the grammar, unknown-entity for a well-formed id that
    is not in the pack. Refusal happens at validation, so no hostile value ever
    reaches ``bind_values``, let alone DuckDB.
    """
    template = PIVOT_TEMPLATES[PivotKind.INFRA_OVERLAP]

    result = validate_params(
        template,
        {"subject_id": hostile, "signal_type": ANY, "limit": 5},
        known_ids={"acct_0000037"},
    )

    assert not result.ok
    assert result.failures[0].param == "subject_id"
    assert result.failures[0].code in {
        ParamFailureCode.MALFORMED_ID,
        ParamFailureCode.UNKNOWN_ENTITY,
    }
    assert result.values == {}


def test_a_well_formed_id_outside_the_pack_is_refused() -> None:
    """Decision 7: a pivot expands from what has been gathered. An agent that
    could name any entity could walk the whole platform from one case."""
    template = PIVOT_TEMPLATES[PivotKind.INFRA_OVERLAP]

    result = validate_params(
        template,
        {"subject_id": "acct_0000999", "signal_type": ANY, "limit": 5},
        known_ids={"acct_0000037"},
    )

    assert not result.ok
    assert result.failures[0].code is ParamFailureCode.UNKNOWN_ENTITY


def test_bind_values_refuses_a_map_it_cannot_fill() -> None:
    """Binding ``None`` into a query would run a query nobody proposed."""
    template = PIVOT_TEMPLATES[PivotKind.INFRA_OVERLAP]

    with pytest.raises(PivotViolation, match="cannot bind"):
        bind_values(template, {"subject_id": "acct_0000037"})


def test_bind_values_orders_arguments_by_the_declared_binding() -> None:
    template = PIVOT_TEMPLATES[PivotKind.SHARED_METADATA]
    values = {"account_id": "acct_0000037", "metadata_field": ANY, "limit": 7}

    bound = bind_values(template, values)

    assert bound == [values[name] for name in template.binding]
    assert len(bound) == template.sql.count("?")


# --------------------------------------------------------------------------
# ParamSpec self-validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"kind": ParamKind.INTEGER}, "both bounds"),
        ({"kind": ParamKind.INTEGER, "minimum": 5, "maximum": 1}, "minimum above maximum"),
        (
            {"kind": ParamKind.INTEGER, "minimum": 1, "maximum": 5, "choices": frozenset({"a"})},
            "cannot declare choices",
        ),
        ({"kind": ParamKind.CHOICE}, "declares its choices"),
        (
            {"kind": ParamKind.CHOICE, "choices": frozenset({"a"}), "minimum": 1},
            "cannot declare numeric bounds",
        ),
        ({"kind": ParamKind.ENTITY_ID, "minimum": 1}, "no bounds or choices"),
    ],
)
def test_param_specs_validate_their_own_shape(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ParamSpec(name="p", description="a parameter", **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Digests
# --------------------------------------------------------------------------


def test_template_digest_covers_the_sql_text() -> None:
    """A template edited without a version bump changes its digest, so an old
    pack's provenance stops matching the query it names."""
    template = PIVOT_TEMPLATES[PivotKind.ACCOUNT_LINK]
    edited = PivotTemplate(
        kind=template.kind,
        template_id=template.template_id,
        sql=template.sql + "\n",
        required_scopes=template.required_scopes,
        params=template.params,
        binding=template.binding,
        columns=template.columns,
        summary=template.summary,
    )

    assert template_sha256(edited) != template_sha256(template)
    assert template_sha256(template) == template_sha256(template)


def test_every_template_digest_is_distinct() -> None:
    digests = {template_sha256(template) for template in PIVOT_TEMPLATES.values()}

    assert len(digests) == len(PIVOT_TEMPLATES)


def test_param_hash_separates_values_that_differ_only_by_type() -> None:
    """A pivot run with a string where an integer belonged is a different
    execution and must not be recorded as the same one."""
    assert param_hash({"limit": 5}) != param_hash({"limit": "5"})
    assert param_hash({"limit": 5}) == param_hash({"limit": 5})
    assert param_hash({"a": 1, "b": 2}) == param_hash({"b": 2, "a": 1})


def test_pivot_scope_names_are_sorted_strings() -> None:
    names = pivot_scope_names(PIVOT_TEMPLATES[PivotKind.ACCOUNT_LINK])

    assert names == tuple(sorted(names))
    assert all(isinstance(name, str) for name in names)
    assert set(names) == {"channel", "comment", "video"}


# --------------------------------------------------------------------------
# Against a real dataset
# --------------------------------------------------------------------------


def _seed_case(connection: duckdb.DuckDBPyConnection) -> tuple[str, str, str, int]:
    """A channel with comments, its owner, a subject sharing an infra value,
    and an anchor instant. Derived from the build rather than hard-coded, so
    the fixture cannot drift from the generator."""
    channel_row = connection.execute(
        "SELECT v.channel_id, MIN(ch.account_id), epoch_ms(MIN(cm.posted_ts)) "
        "FROM main.comment cm "
        "JOIN main.video v ON cm.video_id = v.video_id "
        "JOIN main.channel ch ON v.channel_id = ch.channel_id "
        "GROUP BY v.channel_id ORDER BY COUNT(*) DESC, v.channel_id LIMIT 1"
    ).fetchone()
    subject_row = connection.execute(
        "SELECT subject_id FROM main.infra_hint WHERE (signal_type, signal_value) IN "
        "(SELECT signal_type, signal_value FROM main.infra_hint "
        " GROUP BY 1, 2 HAVING COUNT(DISTINCT subject_id) > 1) "
        "ORDER BY subject_id LIMIT 1"
    ).fetchone()
    assert channel_row is not None and subject_row is not None
    channel_id, account_id, anchor = channel_row
    return str(channel_id), str(account_id), str(subject_row[0]), int(anchor)


def _params_for(
    kind: PivotKind, channel: str, account: str, subject: str, anchor: int
) -> dict[str, object]:
    match kind:
        case PivotKind.SHARED_METADATA:
            return {"account_id": account, "metadata_field": ANY, "limit": 25}
        case PivotKind.TEMPORAL_CORRELATION:
            return {
                "channel_id": channel,
                "anchor_epoch_ms": anchor,
                "window_hours": 24,
                "limit": 25,
            }
        case PivotKind.ENGAGEMENT_EDGE:
            return {"channel_id": channel, "kind": ANY, "min_events": 1, "limit": 25}
        case PivotKind.INFRA_OVERLAP:
            return {"subject_id": subject, "signal_type": ANY, "limit": 25}
        case PivotKind.ACCOUNT_LINK:
            return {"channel_id": channel, "min_comments": 1, "limit": 25}


@pytest.mark.parametrize("kind", list(PivotKind))
def test_every_template_executes_and_returns_its_declared_columns(
    connection: duckdb.DuckDBPyConnection, kind: PivotKind
) -> None:
    """Executed against a real seed-42 build, not parsed and trusted.

    ``columns`` is the contract the row-to-evidence mapping is written against,
    so a query returning a different arity has to fail here rather than
    silently populate the wrong field of an evidence record.
    """
    channel, account, subject, anchor = _seed_case(connection)
    template = PIVOT_TEMPLATES[kind]
    params = _params_for(kind, channel, account, subject, anchor)
    result = validate_params(template, params, known_ids={channel, account, subject})
    assert result.ok, result.detail

    rows = connection.execute(template.sql, bind_values(template, result.values)).fetchall()

    assert rows, f"{template.template_id} returned nothing on the seed-42 build"
    for row in rows:
        assert len(row) == len(template.columns)


def test_the_row_limit_is_enforced_by_the_query_not_by_trimming(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    channel, account, subject, anchor = _seed_case(connection)
    template = PIVOT_TEMPLATES[PivotKind.ACCOUNT_LINK]

    counts = []
    for limit in (1, 3, 5):
        result = validate_params(
            template,
            {"channel_id": channel, "min_comments": 1, "limit": limit},
            known_ids={channel},
        )
        counts.append(
            len(connection.execute(template.sql, bind_values(template, result.values)).fetchall())
        )

    assert counts == [1, 3, 5]


def test_the_any_sentinel_widens_rather_than_selecting_a_column(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """The construction the module docstring calls out, measured.

    ``metadata_field`` never becomes a column name. Asking for ``any`` returns
    exactly the union of the two specific answers, which is what a value
    compared in a WHERE clause does and what an interpolated identifier would
    only have appeared to do.
    """
    template = PIVOT_TEMPLATES[PivotKind.SHARED_METADATA]
    account = "t01_acct_000_000"

    def run(field: str) -> list[tuple[object, ...]]:
        result = validate_params(
            template,
            {"account_id": account, "metadata_field": field, "limit": MAX_ROW_LIMIT},
            known_ids={account},
        )
        assert result.ok, result.detail
        return connection.execute(template.sql, bind_values(template, result.values)).fetchall()

    ip_rows = run("signup_ip_bucket")
    device_rows = run("device_fingerprint_hint")
    any_rows = run(ANY)

    assert ip_rows and device_rows
    assert {row[1] for row in ip_rows} == {"signup_ip_bucket"}
    assert {row[1] for row in device_rows} == {"device_fingerprint_hint"}
    assert sorted(any_rows) == sorted(ip_rows + device_rows)


def test_duckdb_treats_a_bound_value_as_a_value_even_when_it_looks_like_sql(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """The measurement the no-dynamic-SQL claim rests on, recorded as a test.

    Verified against DuckDB 1.5.5. A parameter is rejected outright where an
    identifier belongs, and evaluates as a literal where a value belongs. This
    is why the ``'any'`` sentinel is safe and why an injected id could not have
    worked even if the grammar check had let one through.
    """
    template = PIVOT_TEMPLATES[PivotKind.INFRA_OVERLAP]
    hostile = "'; DROP TABLE main.channel; --"

    rows = connection.execute(template.sql, [hostile, ANY, ANY, 5]).fetchall()

    assert rows == []
    surviving = connection.execute("SELECT COUNT(*) FROM main.channel").fetchone()
    assert surviving is not None and surviving[0] > 0

    with pytest.raises(duckdb.ParserException):
        connection.execute("SELECT account_id FROM ?", ["main.channel"]).fetchall()

    assert connection.execute("SELECT ? FROM main.channel LIMIT 1", ["account_id"]).fetchone() == (
        "account_id",
    )
