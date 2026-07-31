# SPDX-License-Identifier: MIT
"""D4: the ASSEMBLE gate's checker (STEP-04 D4, ARCHITECTURE 3.3).

ARCHITECTURE 3.3 defines the ASSEMBLE gate as "deterministic validation before
acceptance (schema, referential integrity, provenance completeness)". STEP-02
shipped the pipeline and the ``FailureCode`` vocabulary and deliberately left
the checker to this phase, because implementing it then would have meant
inventing the artifact it checks. This is that checker.

Orchestrator-side, not in ``agents/``. The gate is the governance layer judging
the agent's output, and an agent that imported its own gate would be an agent
nobody is gating. The import-graph test enforces it.

What this adds over the type's own invariants
---------------------------------------------
``EvidencePack.__post_init__`` already refuses a dangling edge or an orphan
record, so a pack that exists is already referentially intact. That makes two
of the three checks below **unreachable through the public construction path**,
and saying so plainly is more useful than implying the gate is catching things
it cannot.

They are kept, for the reason STEP-02 kept its two unreachable branches: the
gate receives an ``object`` from a tool handler, not a guaranteed pack. If a
future change constructs a pack by a route that skips ``__post_init__``, the
type stops being a guarantee and the gate is what remains. Both are tested
directly, against packs built by bypassing the constructor.

Three checks here are genuinely reachable, and they are the gate's real
contribution, because they check facts the type cannot know:

1. **The artifact is a pack at all.** A handler returning something else is
   caught here rather than by an ``isinstance`` somewhere downstream.
2. **Every cited query template exists in this build, with the exact text the
   pack recorded.** ``Provenance.template_sha256`` is a digest of the SQL that
   produced the records. The type validates its shape; only the gate can
   compare it against the template registry. A pack citing a template this
   build does not have, or one whose text has since been edited, is a pack
   whose provenance no longer resolves to anything runnable.
3. **Hop indices are contiguous from zero.** A gap means a hop's provenance was
   dropped, which is how a pack quietly loses the record of a question it
   asked, and how recovery at a budget stops meaning what it says.

Failures are returned, never raised (STEP-02 2.4). ``run_gate`` converts an
exception into ``CHECKER_ERROR`` anyway, so raising would degrade a precise
finding into a generic one.
"""

from collections.abc import Sequence

from ts_sentry.agents.evidence.pack import SEED_TEMPLATE_ID, EvidencePack, Provenance
from ts_sentry.governance.gates import ArtifactCheck, FailureCode, GateFailure
from ts_sentry.orchestrator.pivots import PIVOT_TEMPLATES, template_sha256

__all__ = ["evidence_pack_check", "pack_checker"]


def _check_templates(provenance: Sequence[Provenance]) -> list[GateFailure]:
    """Every cited template exists in this build with the text the pack recorded."""
    failures: list[GateFailure] = []
    by_id = {
        template.template_id: template_sha256(template) for template in PIVOT_TEMPLATES.values()
    }

    for record in provenance:
        if record.query_template_id == SEED_TEMPLATE_ID:
            # The analyst's case selection is not a query, and there is no
            # template to compare it against. `Provenance.__post_init__` has
            # already tied that id to a null pivot_kind in both directions, so
            # this branch cannot be used to smuggle past the check below.
            continue

        expected = by_id.get(record.query_template_id)
        if expected is None:
            failures.append(
                GateFailure(
                    code=FailureCode.SCHEMA_INVALID,
                    detail=(
                        f"provenance {record.provenance_id} cites query template "
                        f"{record.query_template_id!r}, which this build does not have"
                    ),
                )
            )
        elif expected != record.template_sha256:
            failures.append(
                GateFailure(
                    code=FailureCode.SCHEMA_INVALID,
                    detail=(
                        f"provenance {record.provenance_id} cites {record.query_template_id} at "
                        f"digest {record.template_sha256[:16]}, but this build's text digests to "
                        f"{expected[:16]}; the template was edited after these records were "
                        "gathered"
                    ),
                )
            )
    return failures


def _check_hop_indices(provenance: Sequence[Provenance]) -> list[GateFailure]:
    """Hop indices run 0, 1, 2, ... with no gaps and no repeats."""
    indices = sorted(record.hop_index for record in provenance)
    expected = list(range(len(indices)))
    if indices == expected:
        return []
    return [
        GateFailure(
            code=FailureCode.SCHEMA_INVALID,
            detail=(
                f"hop indices {indices} are not contiguous from zero; a gap means a hop's "
                "provenance was dropped, and recovery at a budget cannot be read from a pack "
                "that has forgotten one of its own hops"
            ),
        )
    ]


def _check_referential_integrity(pack: EvidencePack) -> list[GateFailure]:
    """Every edge resolves to two known nodes; every timeline entry to one.

    Unreachable through ``EvidencePack(...)``, which refuses to construct such a
    pack. See the module docstring: kept because the gate receives an object,
    not a guarantee.
    """
    failures: list[GateFailure] = []
    node_ids = pack.node_ids

    for edge in pack.edges:
        for role, endpoint in (("source", edge.source_id), ("target", edge.target_id)):
            if endpoint not in node_ids:
                failures.append(
                    GateFailure(
                        code=FailureCode.REFERENTIAL_INTEGRITY,
                        detail=(
                            f"edge {edge.edge_id} names {role} {endpoint}, which is not a node "
                            "in this pack"
                        ),
                    )
                )

    for event in pack.timeline:
        if event.node_id not in node_ids:
            failures.append(
                GateFailure(
                    code=FailureCode.REFERENTIAL_INTEGRITY,
                    detail=(
                        f"timeline event {event.event_id} is about {event.node_id}, which is not "
                        "a node in this pack"
                    ),
                )
            )

    if pack.subject_id not in node_ids:
        failures.append(
            GateFailure(
                code=FailureCode.REFERENTIAL_INTEGRITY,
                detail=(
                    f"subject {pack.subject_id} is not a node in its own pack; the entity under "
                    "investigation is the seed the pivots expand from"
                ),
            )
        )
    return failures


def _check_provenance_completeness(pack: EvidencePack) -> list[GateFailure]:
    """No orphan records: every record cites provenance this pack carries.

    Unreachable through the constructor, like the check above, and kept for the
    same reason.
    """
    failures: list[GateFailure] = []
    known = {record.provenance_id for record in pack.provenance}

    if not pack.provenance:
        return [
            GateFailure(
                code=FailureCode.PROVENANCE_INCOMPLETE,
                detail="the pack carries no provenance records at all",
            )
        ]

    cited: list[tuple[str, str, str]] = []
    cited.extend(("node", node.node_id, node.provenance_id) for node in pack.nodes)
    cited.extend(("edge", edge.edge_id, edge.provenance_id) for edge in pack.edges)
    cited.extend(("timeline event", event.event_id, event.provenance_id) for event in pack.timeline)

    for label, record_id, provenance_id in cited:
        if provenance_id not in known:
            failures.append(
                GateFailure(
                    code=FailureCode.PROVENANCE_INCOMPLETE,
                    detail=(
                        f"{label} {record_id} cites provenance {provenance_id}, which this pack "
                        "does not carry; a record nobody can trace is not evidence"
                    ),
                )
            )
    return failures


def evidence_pack_check(artifact: object, /) -> tuple[GateFailure, ...]:
    """The ASSEMBLE gate's deterministic validation pass.

    Satisfies ``gates.ArtifactCheck``. Runs on every hop, over the whole pack
    rather than over the rows one pivot added, so referential integrity is
    re-established after each hop instead of being assumed to have survived.

    Returns every failure it finds rather than the first. A pack with three
    problems should be reported as a pack with three problems: an analyst
    reading a ``GATE_REJECTION`` payload is trying to understand what went
    wrong, not to fix one thing and run again to discover the next.
    """
    if not isinstance(artifact, EvidencePack):
        return (
            GateFailure(
                code=FailureCode.SCHEMA_INVALID,
                detail=f"expected an EvidencePack; got {type(artifact).__name__}",
            ),
        )

    failures: list[GateFailure] = []
    failures.extend(_check_templates(artifact.provenance))
    failures.extend(_check_hop_indices(artifact.provenance))
    failures.extend(_check_referential_integrity(artifact))
    failures.extend(_check_provenance_completeness(artifact))
    return tuple(failures)


def pack_checker() -> ArtifactCheck:
    """The checker, as the protocol type the gate declares.

    A named function rather than a lambda or a bare reference so the fleet's
    ``GateChecks`` reads as a list of named checks, and so mypy checks the
    protocol conformance at exactly one place.
    """
    return evidence_pack_check
