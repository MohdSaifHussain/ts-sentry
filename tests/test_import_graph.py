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
    }
)
"""Who may reach the human-only ENFORCE construction path.

An allowlist, per the sealed two-consumer model. ``cli.main`` is listed as the
analyst-facing boundary; it does not import the module today, and listing it
in advance is the point of an allowlist rather than a description.
"""

FORBIDDEN_FOR_AGENTS = {
    SIGNATURE_MODULE: "the human-only ENFORCE construction path",
    f"{PACKAGE}.governance.ledger": "the chain and its write capability",
    f"{PACKAGE}.governance.gates": "the machinery that judges agent output",
    f"{PACKAGE}.orchestrator.dispatch": "the executor that decides what may run",
}
"""What no agent module may reach. Each entry names why, so a future reader
deciding whether to relax one has the argument in front of them."""


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
