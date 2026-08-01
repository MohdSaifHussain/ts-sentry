# SPDX-License-Identifier: MIT
"""The model-boundary vocabulary, split out so the session can record it.

This module holds nothing but enums and one frozen record. It imports nothing
from this package, which is the whole point: ``adapter`` imports ``core``, so
``core`` cannot import ``adapter`` back, and STEP-08 needs the session to write
which model path produced its outputs into the hash-chained ``SESSION_OPEN``
payload. The vocabulary therefore moves below both.

This is the split STEP-03 already made once for the same reason, recorded in
that phase's Outcome: "``orchestrator.toolspec`` was split from
``orchestrator.tools`` to break a cycle between the table and its handlers."
A contract that two layers both need belongs under both of them.

``adapter`` re-exports ``ModelMode`` and ``StubMode``, so every existing
``from ts_sentry.orchestrator.adapter import StubMode`` keeps working and this
split is invisible to callers.
"""

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "ModelMode",
    "ModelProvenance",
    "StubMode",
]


class ModelMode(StrEnum):
    STUB = "stub"
    LIVE = "live"


class StubMode(StrEnum):
    """How the stub behaves.

    ``OVERCLAIM`` and ``TRANSIENT`` are not test scaffolding smuggled into
    production code; they are how the governance layer's failure paths get
    demonstrated. A verifier that has never rejected anything is a verifier
    nobody has tested, and STEP-02 recorded that reasoning about
    ``VERIFICATION_FAIL`` counts being a showcased metric.
    """

    FAITHFUL = "faithful"
    OVERCLAIM = "overclaim"
    TRANSIENT = "transient"
    REFUSE = "refuse"


@dataclass(frozen=True, slots=True)
class ModelProvenance:
    """What produced this session's model outputs, as a recordable fact.

    STEP-08 D1 ships a curated example whose whole subject is the memo gate
    catching a deliberately overclaiming agent. That example is only honest if
    its own artifacts say the agent was made to overclaim, so the mode is
    **provenance rather than a hidden switch**: it binds into ``SESSION_OPEN``,
    where the hash chain covers it, and it is stamped in the session manifest.
    An overclaim session is then self-identifying in the two places a reader
    checks, and cannot be presented as a faithful run.

    Binding into ``SESSION_OPEN`` rather than adding a twelfth ``EventType``
    follows DECISIONS 5.8 and 6.8, which bound ``corpus_sha256`` and
    ``tolerances_sha256`` the same way and for the same reason: ARCHITECTURE
    3.2's eleven event types stay a closed surface.

    ``stub_mode`` has no default. It is the ``ReviewOutcome.reviewer_kind``
    discipline (DECISIONS 4.7) and the ``Retrieval`` discipline (5.7): the one
    field whose job is to stop a run being mistaken for a different kind of run
    must be named at every call site, because a default is the value nobody
    chose and this is exactly the value somebody has to choose.

    **Not part of session identity, deliberately.** ``derive_session_id`` names
    what a session is *about* (analyst, dataset, agent, case, subject); the seed,
    the limit, the hop ceiling and this mode are all *how it was run*, and none
    of them are in the id. Folding the mode in would change every session id
    recorded in STEP-03 through STEP-06 to distinguish something the manifest
    and the chain already distinguish. The authoritative record is the
    hash-covered payload, on DECISIONS 4.7's reasoning that a claim protected by
    the ledger beats one kept in a side channel.
    """

    model_mode: ModelMode
    stub_mode: StubMode | None

    def __post_init__(self) -> None:
        if self.model_mode is ModelMode.STUB and self.stub_mode is None:
            raise ValueError("a stub-mode session must name which stub mode produced it")
        if self.model_mode is ModelMode.LIVE and self.stub_mode is not None:
            raise ValueError(
                f"a live session has no stub mode; got {self.stub_mode.value}. "
                "Recording one would assert a stub was involved when none was."
            )

    def to_json_object(self) -> dict[str, str]:
        """The rendering used by both the ledger payload and the manifest.

        One function, so the two can never disagree about what this session
        ran under. ``stub_mode`` is omitted under live rather than written as
        null, matching how ``SESSION_OPEN`` already omits the corpus fields
        when no corpus was loaded: a field that is present asserts something,
        and there is nothing to assert here.
        """
        rendered = {"model_mode": self.model_mode.value}
        if self.stub_mode is not None:
            rendered["stub_mode"] = self.stub_mode.value
        return rendered
