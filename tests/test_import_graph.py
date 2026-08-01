# SPDX-License-Identifier: MIT
"""The signature import-graph test, due now that ``agents/`` exists.

This is the first obligation the STEP-02 Outcome carried into STEP-03. It was
deliberately not shipped then, because ``agents/`` did not exist and a
vacuously green test is worse than an absent one: it reports a guarantee
nobody is providing.

Worded per the sealed two-consumer model, which STEP-01 established and
STEP-02 carried forward. That model reads a rule like "X is the only consumer
of Y" as naming the *legitimate* consumers rather than forbidding all others,
because the build pipeline legitimately reads ``sealed._labels`` and a naive
blocklist test would have failed on it. The same shape applies here:

    ``governance.signature`` has exactly two legitimate consumers - the human
    decision boundary (the CLI, where an analyst signs) and the ENFORCE gate
    (``governance.gates``, which checks the signature). ``ts_sentry.agents.*``
    is never one of them.

Stated as an allowlist, the test is about who *may* import it, so adding a
third legitimate consumer is a deliberate edit to a named list rather than a
silent pass.

Transitive, not direct
----------------------
The closure is walked over first-party imports, so an agent cannot reach a
forbidden module through an innocent-looking one. That matters: a direct-only
test would pass while ``agents.triage`` imported ``governance.gates``, which
imports ``governance.signature``. The point is reachability, not spelling.
"""

import ast
from collections.abc import Mapping
from pathlib import Path

import pytest

PACKAGE = "ts_sentry"
_SRC = Path(__file__).resolve().parent.parent / "src"
_ROOT = _SRC / PACKAGE

SIGNATURE_MODULE = f"{PACKAGE}.governance.signature"

LEGITIMATE_SIGNATURE_CONSUMERS = frozenset(
    {
        f"{PACKAGE}.governance.gates",  # the ENFORCE gate checks the signature
        f"{PACKAGE}.cli.main",  # the human decision boundary
        f"{PACKAGE}.orchestrator.signing",  # STEP-05 D6: the memo signing path
    }
)
"""Who may reach the human-only ENFORCE construction path.

An allowlist, per the sealed two-consumer model. ``cli.main`` is listed as the
analyst-facing boundary; it does not import the module today, and listing it
in advance is the point of an allowlist rather than a description.

``orchestrator.signing`` was added in STEP-05 D6, and the addition is the
mechanism working rather than the rule bending: a third consumer required a
deliberate edit to this named list, in the same commit as the module, instead of
appearing unnoticed. It is where a memo is finalized under an analyst's
signature. ``agents.*`` remains unable to reach it, which is asserted separately
and is the half of the model that actually constrains anything.
"""

FORBIDDEN_FOR_AGENTS = {
    SIGNATURE_MODULE: "the human-only ENFORCE construction path",
    f"{PACKAGE}.governance.ledger": "the chain and its write capability",
    f"{PACKAGE}.governance.gates": "the machinery that judges agent output",
    f"{PACKAGE}.orchestrator.dispatch": "the executor that decides what may run",
    f"{PACKAGE}.orchestrator.eval_labels": "the eval answers (STEP-06 3.2)",
    f"{PACKAGE}.orchestrator.regression_gate": "the verdict on an agent's own successor",
    f"{PACKAGE}.data.eval_build": "ground truth, read at build time to make the eval set",
}
"""What no agent module may reach. Each entry names why, so a future reader
deciding whether to relax one has the argument in front of them.

The three STEP-06 entries are the phase's contamination and self-verification
controls. An agent that could reach ``eval_labels`` could be graded against
answers it had seen; an agent that could reach ``regression_gate`` would be
deciding whether its own successor is good enough to ship. Both are the failure
the prompt-eval phase exists to prevent, and neither is prevented by anybody
remembering not to do it."""


def _module_name(path: Path) -> str:
    relative = path.relative_to(_SRC).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _direct_imports(path: Path, module: str) -> set[str]:
    """First-party modules imported by ``path``, at any nesting depth.

    Function-scope imports count. A guarantee that only holds until someone
    moves an import inside a function is not a guarantee, and the live
    adapter's deferred vendor import shows that the pattern is in use here.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    package = module.rsplit(".", 1)[0] if "." in module else module

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import
                base = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                found.add(f"{base}.{node.module}" if node.module else base)
            elif node.module:
                found.add(node.module)
                # `from x.y import z` may name a module rather than an object.
                found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return {name for name in found if name.split(".")[0] == PACKAGE}


def build_import_graph() -> Mapping[str, set[str]]:
    """First-party import edges for the whole package."""
    known = {_module_name(path) for path in _ROOT.rglob("*.py")}
    graph: dict[str, set[str]] = {}
    for path in _ROOT.rglob("*.py"):
        module = _module_name(path)
        graph[module] = {name for name in _direct_imports(path, module) if name in known}
    return graph


def reachable_from(graph: Mapping[str, set[str]], start: str) -> set[str]:
    """Transitive closure from ``start``, excluding itself."""
    seen: set[str] = set()
    frontier = [start]
    while frontier:
        current = frontier.pop()
        for target in graph.get(current, set()):
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    return seen - {start}


def agent_modules(graph: Mapping[str, set[str]]) -> tuple[str, ...]:
    return tuple(sorted(m for m in graph if m.startswith(f"{PACKAGE}.agents")))


def test_the_agents_package_actually_exists() -> None:
    """Guards the guard.

    If ``agents/`` were deleted or renamed, every assertion below would pass
    over an empty set and this file would report a guarantee about nothing.
    That is precisely the vacuous-green state STEP-02 refused to ship.
    """
    modules = agent_modules(build_import_graph())

    assert len(modules) >= 3
    assert f"{PACKAGE}.agents.triage.scorer" in modules


@pytest.mark.parametrize("forbidden", sorted(FORBIDDEN_FOR_AGENTS))
def test_no_agent_module_can_reach(forbidden: str) -> None:
    """The obligation itself, over the transitive closure."""
    graph = build_import_graph()

    for module in agent_modules(graph):
        reachable = reachable_from(graph, module)
        assert forbidden not in reachable, (
            f"{module} can reach {forbidden} ({FORBIDDEN_FOR_AGENTS[forbidden]}); "
            f"path exists through {sorted(graph[module])}"
        )


def test_the_signature_module_has_only_its_two_legitimate_consumers() -> None:
    """The other half of the two-consumer model.

    The agent-side rule above says who may not reach it. This says who does,
    so a new importer anywhere in the package is a deliberate addition to
    ``LEGITIMATE_SIGNATURE_CONSUMERS`` rather than something nobody noticed.
    """
    graph = build_import_graph()

    importers = {module for module, targets in graph.items() if SIGNATURE_MODULE in targets}

    assert importers <= LEGITIMATE_SIGNATURE_CONSUMERS, (
        f"unexpected importers of {SIGNATURE_MODULE}: "
        f"{sorted(importers - LEGITIMATE_SIGNATURE_CONSUMERS)}"
    )


def test_the_import_graph_helper_finds_a_known_edge() -> None:
    """The analysis is only worth as much as its parser.

    A silently broken graph builder would make every assertion above pass by
    finding no edges at all, so one edge that must exist is checked directly.
    """
    graph = build_import_graph()

    assert f"{PACKAGE}.governance.signature" in graph[f"{PACKAGE}.governance.gates"]
    assert f"{PACKAGE}.governance.canonical" in reachable_from(
        graph, f"{PACKAGE}.orchestrator.firewall"
    )


def test_no_agent_module_reaches_its_own_verifier() -> None:
    """The finding this file produced on its first run, kept as its own test.

    ``agents.triage.rationale`` originally did the verifying, which reached
    ``governance.verifier`` and through it ``gates`` and ``signature``. The
    reachability was the symptom; the design was the defect. Verification is
    the governance layer judging the agent, so it moved to
    ``orchestrator.rationale_check`` and the agent kept only the citation
    format it writes to.

    Asserted separately from the forbidden-module list because the reason is
    different: ``governance.verifier`` is not dangerous to import, it is
    simply not the agent's to hold.
    """
    graph = build_import_graph()

    for module in agent_modules(graph):
        assert f"{PACKAGE}.governance.verifier" not in reachable_from(graph, module)

    assert f"{PACKAGE}.governance.verifier" in reachable_from(
        graph, f"{PACKAGE}.orchestrator.rationale_check"
    )


SEALED_TABLE = "sealed._labels"

SEALED_NEEDLES = ("sealed._labels", "_labels")
"""What counts as naming the sealed table in code.

Two needles rather than one, because the first version of this check missed
``f"SELECT * FROM {schema}._labels"``: a table name assembled at runtime never
contains the full string, so searching only for it would have reported a
guarantee the check could not provide. ``_labels`` is distinctive enough to
catch the fragment and rare enough not to fire on unrelated code.

Stated at its true width: this catches the table *named* in a string the program
evaluates. It cannot catch a name assembled from pieces that are themselves
computed, and nothing here claims otherwise. The load-bearing control remains
structural, as it has been since STEP-01: ``DataScope`` has no member that
resolves anywhere under ``sealed``, both resolvers are exhaustive, and an
orchestrator module cannot obtain a connection it was not lent. This is defense
in depth on top of that, and its product is that writing the name becomes a
deliberate, visible act.
"""

LEGITIMATE_SEALED_CONSUMERS = frozenset(
    {
        f"{PACKAGE}.data.store",  # the build pipeline writes it
        f"{PACKAGE}.data.quality",  # the build-time reconcile gate reads it
        f"{PACKAGE}.measurement.recovery",  # measurement, from STEP-04 onward
        f"{PACKAGE}.cli.main",  # names it only to prove the allowlist denies it
        f"{PACKAGE}.data.eval_build",  # STEP-06 D2: build-time, labels the eval set
    }
)
"""Who may name the sealed table *in code*, as an allowlist.

Worded per the two-consumer model STEP-01 established and STEP-02 and STEP-03
carried: "measurement code is the only consumer of ``sealed._labels``" has to be
read as the only *agent- or orchestrator-side* consumer, because the build
pipeline legitimately writes it and reads it back for the D6 reconcile gate. A
naive blocklist would fail on the build, which is the finding STEP-01 recorded
and the reason this is a named list rather than a prohibition.

``cli.main`` is the odd one and is listed deliberately. It names the table only
to hand it to ``resolve_scope_by_name`` and assert the allowlist refuses it, in
the build-time leakage self-check. That is naming the table in order to prove it
is unreachable, which is the opposite of consuming it, but a mechanical check
cannot tell those apart and pretending otherwise would mean either exempting the
file silently or weakening the check.
"""


def _code_strings(path: Path) -> list[str]:
    """String literals that are code, excluding docstrings.

    A substring search over the file is the wrong instrument here, and the first
    version of this test proved it by flagging eight modules whose only mention
    of the sealed table was prose explaining that they cannot reach it.
    ``scopes.py`` saying "no member resolves to sealed._labels" is the guarantee
    being documented, not a breach of it.

    Walking the AST separates the two properly. Comments never enter the tree at
    all, and docstrings are the string-valued expression statements, so what
    remains is the set of literals the program actually evaluates. A SQL query
    is one of those; a docstring is not.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        # Bare string expressions are docstrings, including this project's
        # attribute docstrings (a string statement after an assignment).
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            docstrings.add(id(node.value))

    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_no_agent_or_orchestrator_module_can_reach_measurement() -> None:
    """STEP-07 3.2, landed early because ``measurement/`` exists as of STEP-04.

    Not vacuous: the package is real and has a module in it. The rule is the
    other half of the sealed boundary. Ground truth is reachable from
    measurement, so anything that can reach measurement can reach ground truth
    by one more import, and an agent that could do that is an agent grading its
    own homework.
    """
    graph = build_import_graph()
    measurement = f"{PACKAGE}.measurement.recovery"

    assert measurement in graph, "the measurement module is missing; this test would be vacuous"

    for module in graph:
        if not module.startswith((f"{PACKAGE}.agents", f"{PACKAGE}.orchestrator")):
            continue
        reachable = reachable_from(graph, module)
        assert measurement not in reachable, (
            f"{module} can reach {measurement}, and through it sealed ground truth"
        )
        assert f"{PACKAGE}.measurement" not in reachable, (
            f"{module} can reach the measurement package"
        )


def test_only_named_modules_mention_the_sealed_table() -> None:
    """Asserted against the source text, not against imports.

    The sealed table is reached by *naming it in SQL*, not by importing a
    module, so an import-graph check alone would miss a query someone wrote by
    hand. This greps the tree the way Saif's exit checklist greps for dynamic
    SQL, and for the same reason: the guarantee is about what the code says,
    not about what it imports.
    """
    offenders: dict[str, int] = {}
    for path in _ROOT.rglob("*.py"):
        module = _module_name(path)
        if module in LEGITIMATE_SEALED_CONSUMERS:
            continue
        count = sum(
            1
            for literal in _code_strings(path)
            if any(needle in literal for needle in SEALED_NEEDLES)
        )
        if count:
            offenders[module] = count

    assert offenders == {}, (
        f"modules naming {SEALED_TABLE} in code outside the allowlist: {sorted(offenders)}. "
        "Adding one is a deliberate edit to LEGITIMATE_SEALED_CONSUMERS, not an accident."
    )


def test_the_sealed_allowlist_is_not_stale() -> None:
    """Guards the guard.

    Every module on the allowlist must actually mention the table. Otherwise the
    list grows entries that permit something nobody is doing, and the next
    reader cannot tell which entries are load-bearing.
    """
    for module in LEGITIMATE_SEALED_CONSUMERS:
        path = _ROOT / (module.removeprefix(f"{PACKAGE}.").replace(".", "/") + ".py")
        assert path.is_file(), f"{module} is on the sealed allowlist but does not exist"
        assert any(
            any(needle in literal for needle in SEALED_NEEDLES) for literal in _code_strings(path)
        ), f"{module} is on the sealed allowlist but never names {SEALED_TABLE} in code"


def test_agents_are_not_isolated_from_what_they_legitimately_need() -> None:
    """The rule is an allowlist on specific modules, not an isolation cell.

    Agents legitimately reach the mandate vocabulary, the data enums, and the
    firewall's prompt type. Asserting that keeps a future tightening from
    quietly severing something the agent needs to do its job.
    """
    graph = build_import_graph()
    reachable = reachable_from(graph, f"{PACKAGE}.agents.triage.prompts")

    assert f"{PACKAGE}.orchestrator.firewall" in reachable
    assert f"{PACKAGE}.agents.triage.scorer" in reachable
