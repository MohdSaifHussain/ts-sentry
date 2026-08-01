# SPDX-License-Identifier: MIT
"""D1: activation as an append-only pointer history (STEP-06 3.4).

3.4 requires that "activation swaps a pointer; prior versions retained forever
(rollback is a pointer move, ledgered)". This module is the pointer, and the
shape it takes is the requirement made structural rather than promised.

Why a history rather than a field
---------------------------------
The obvious implementation is ``PromptVersion.active: bool``. It is wrong, and
wrong in the specific way 3.4 names. Activating v2 would have to set v1's flag
to ``False``, which is a **write to the record of a version that did not
change**. One prompt's activation would rewrite another prompt's record, and
the immutability the phase is built to demonstrate would be violated by the
very operation it is meant to govern.

So the active version of a task is *derived*: it is the target of the last
entry in this history for that task. Version records are written once. Nothing
in the system ever updates one, and there is no code path that could, because
no field on them says anything about activation.

Rollback is not a special case
------------------------------
It is another entry. That is the honest reading of "rollback is a pointer
move": if rolling back deleted the entry that activated the bad version, the
history would end up describing a system that never made the mistake. What
actually happened is that a version was activated and then a different one was
pointed at, and both facts survive.

The one thing rollback is not allowed to be is an activation wearing a
different name: its target must already appear in this task's history, so
``ROLLBACK`` always means "back to something we ran before".

Pure, like ``mandate.validate``
-------------------------------
Every transition here returns a new ``ActivationHistory`` and touches nothing
else. No clock, no file, no ledger. Callers supply the timestamp (STEP-03 D1:
nothing in this system reads the clock behind its caller's back), and the
orchestrator is what persists the result and writes the ledger entry.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ts_sentry.data.tz import require_ist, require_ist_iso
from ts_sentry.governance.canonical import require_sha256_hex
from ts_sentry.prompt_registry.registry import PromptRegistryError, PromptTask

__all__ = [
    "ActivationAction",
    "ActivationEntry",
    "ActivationHistory",
    "entry_from_json_object",
    "history_from_json_array",
]


class ActivationAction(StrEnum):
    """Why the pointer moved.

    Distinguished so the history is countable by cause, in the shape
    ``RefusalCode`` and ``FailureCode`` established: a task whose history is
    mostly rollbacks is telling a reviewer something that a bare sequence of
    pointer targets would not.
    """

    ACTIVATE = "activate"
    ROLLBACK = "rollback"


@dataclass(frozen=True, slots=True)
class ActivationEntry:
    """One pointer move. Immutable, and never removed once written."""

    seq: int
    task: PromptTask
    content_digest: str
    action: ActivationAction
    reason: str
    timestamp_ist: datetime

    def __post_init__(self) -> None:
        if self.seq < 0:
            raise ValueError(f"seq must be non-negative; got {self.seq}")
        require_sha256_hex(self.content_digest, "content_digest")
        if not self.reason.strip():
            raise ValueError("every pointer move states why it happened")
        require_ist(self.timestamp_ist, "timestamp_ist")

    def to_json_object(self) -> dict[str, object]:
        return {
            "seq": self.seq,
            "task": self.task.value,
            "content_digest": self.content_digest,
            "action": self.action.value,
            "reason": self.reason,
            "timestamp_ist": self.timestamp_ist.isoformat(),
        }


def entry_from_json_object(obj: Mapping[str, object]) -> ActivationEntry:
    """Rebuild one entry from the manifest."""
    try:
        raw_timestamp = str(obj["timestamp_ist"])
        require_ist_iso(raw_timestamp, "timestamp_ist")
        return ActivationEntry(
            seq=int(str(obj["seq"])),
            task=PromptTask(str(obj["task"])),
            content_digest=str(obj["content_digest"]),
            action=ActivationAction(str(obj["action"])),
            reason=str(obj["reason"]),
            timestamp_ist=datetime.fromisoformat(raw_timestamp),
        )
    except KeyError as exc:
        raise PromptRegistryError(f"activation entry is missing {exc}") from exc
    except ValueError as exc:
        raise PromptRegistryError(f"activation entry is unreadable: {exc}") from exc


@dataclass(frozen=True, slots=True)
class ActivationHistory:
    """The append-only pointer log, for every task at once.

    One log rather than one per task, so ``seq`` totally orders every pointer
    move the registry has ever made. Two tasks activated in some order is a
    fact about what this system did, and per-task logs would lose it.
    """

    entries: tuple[ActivationEntry, ...] = ()

    def __post_init__(self) -> None:
        seen_by_task: dict[PromptTask, set[str]] = {}
        previous: ActivationEntry | None = None

        for position, entry in enumerate(self.entries):
            if entry.seq != position:
                raise PromptRegistryError(
                    f"activation history is not contiguous: expected seq {position}, "
                    f"found {entry.seq}. An append-only log has no gaps, so this is a "
                    "deletion, a reordering, or an out-of-band insert."
                )
            if previous is not None and entry.timestamp_ist < previous.timestamp_ist:
                raise PromptRegistryError(
                    f"activation entry {entry.seq} is stamped before entry {previous.seq}; "
                    "an append-only log does not move backwards in time"
                )

            seen = seen_by_task.setdefault(entry.task, set())
            if entry.action is ActivationAction.ROLLBACK and entry.content_digest not in seen:
                raise PromptRegistryError(
                    f"activation entry {entry.seq} rolls {entry.task.value} back to "
                    f"{entry.content_digest[:12]}, which this task has never run. A rollback "
                    "returns to a version that was activated before; anything else is an "
                    "activation under the wrong name"
                )
            seen.add(entry.content_digest)
            previous = entry

    @property
    def next_seq(self) -> int:
        return len(self.entries)

    def for_task(self, task: PromptTask) -> tuple[ActivationEntry, ...]:
        return tuple(entry for entry in self.entries if entry.task is task)

    def active(self, task: PromptTask) -> str | None:
        """The content digest currently pointed at for ``task``, or ``None``.

        Derived from the log every time rather than cached. A cached pointer is
        a second source of truth, and the first thing a second source of truth
        does is disagree.
        """
        moves = self.for_task(task)
        return moves[-1].content_digest if moves else None

    def has_run(self, task: PromptTask, digest: str) -> bool:
        """Whether ``digest`` was ever the active version for ``task``."""
        return any(entry.content_digest == digest for entry in self.for_task(task))

    def _appended(
        self,
        *,
        task: PromptTask,
        digest: str,
        action: ActivationAction,
        reason: str,
        timestamp_ist: datetime,
    ) -> "ActivationHistory":
        entry = ActivationEntry(
            seq=self.next_seq,
            task=task,
            content_digest=digest,
            action=action,
            reason=reason,
            timestamp_ist=timestamp_ist,
        )
        return ActivationHistory(entries=(*self.entries, entry))

    def activate(
        self,
        task: PromptTask,
        digest: str,
        *,
        reason: str,
        timestamp_ist: datetime,
    ) -> "ActivationHistory":
        """Point ``task`` at ``digest``. Returns a new history; mutates nothing.

        Re-activating the version already active is refused as a caller bug
        rather than recorded as a no-op. A history in which entries can mean
        nothing is a history a reader has to interpret, and every pointer move
        in this log should correspond to something that actually changed.
        """
        if self.active(task) == digest:
            raise PromptRegistryError(
                f"{task.value} is already pointed at {digest[:12]}; activating it again "
                "would record a pointer move that moved nothing"
            )
        return self._appended(
            task=task,
            digest=digest,
            action=ActivationAction.ACTIVATE,
            reason=reason,
            timestamp_ist=timestamp_ist,
        )

    def rollback(
        self,
        task: PromptTask,
        digest: str,
        *,
        reason: str,
        timestamp_ist: datetime,
    ) -> "ActivationHistory":
        """Point ``task`` back at a version it has run before.

        The precondition is checked here *and* in ``__post_init__``, which is
        the ``pack_gate`` precedent (DECISIONS 4.8, 5.16): the constructor is a
        guarantee only until something builds a history by another route, and
        the constructor is what remains when it does.
        """
        if not self.has_run(task, digest):
            raise PromptRegistryError(
                f"cannot roll {task.value} back to {digest[:12]}: it has never been active. "
                "A rollback returns to a version that ran before"
            )
        if self.active(task) == digest:
            raise PromptRegistryError(
                f"{task.value} is already pointed at {digest[:12]}; there is nothing to roll back"
            )
        return self._appended(
            task=task,
            digest=digest,
            action=ActivationAction.ROLLBACK,
            reason=reason,
            timestamp_ist=timestamp_ist,
        )

    def to_json_array(self) -> list[dict[str, object]]:
        return [entry.to_json_object() for entry in self.entries]


def history_from_json_array(raw: Iterable[object]) -> ActivationHistory:
    """Rebuild the log from the manifest."""
    entries: list[ActivationEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            raise PromptRegistryError("each activation entry must be a JSON object")
        entries.append(entry_from_json_object(item))
    return ActivationHistory(entries=tuple(entries))
