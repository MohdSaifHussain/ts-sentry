# SPDX-License-Identifier: MIT
"""The seed-existence guard: does the analyst's subject actually exist?

Added at STEP-04's phase close, from a finding of Saif's. It is a small check
and it closes a gap that nothing else in this system could have caught, so the
reasoning is recorded here at the point it applies rather than only in the STEP
file.

The gap
-------
An evidence session was run on a subject that did not exist in the dataset. It
produced a **fully valid audit trail for an investigation of nothing**: exit 0,
an intact and anchored 122-entry chain, twenty ledgered ``HUMAN_DECISION``
approvals, every pack through the ASSEMBLE gate, and complete provenance on all
twenty-one records. Every governance claim this system makes held. All of them
were about an entity that was never there.

Nothing was broken, and that is the point worth stating plainly:

    **The assembly gate validates the artifact's internal consistency, not its
    correspondence to reality.** A pack whose edges resolve to its own nodes and
    whose records all cite provenance it carries is a *well-formed* pack. It is
    not thereby a pack about something real. Referential integrity is a closed
    property: it can be perfectly satisfied by a pack describing nothing.

Seed-existence is the boundary check that ties the audit trail to a real
subject. It is the one link in the chain of claims that has to reach outside the
artifact, and until now no component owned it: the pack checks itself, the gate
checks the pack, the ledger checks the gate's record, and the anchor checks the
ledger. Each of those is sound, and a tower of sound checks over an empty
premise still attests to nothing.

Why it is here, before the session opens
----------------------------------------
A refusal after the session opened would already have written a chain, a
manifest and an anchor describing a session that should not have existed. The
check therefore runs before the output directory is created and before any
ledger connection is made, so a bad subject leaves **no session and no chain**,
not a short valid one. That ordering is the deliverable, not an implementation
detail.

Same discipline as every other query in this system
----------------------------------------------------
Table names come from ``resolve_table``, so the sealed schema is unnameable
here; the id is bound as a parameter and never formatted into SQL; and the
column per kind is a fixed literal in the reviewed text. The existence question
is asked of the entity tables the analyst's mandate could see, never of
``sealed._labels``: whether an entity is *planted* is ground truth, and asking
that here would leak the very thing STEP-01 sealed. A real benign channel passes
this check exactly as a real abusive one does.
"""

from collections.abc import Mapping
from typing import assert_never

import duckdb

from ts_sentry.data.enums import EntityKind
from ts_sentry.governance.scopes import DataScope, resolve_table

__all__ = ["SUBJECT_QUERIES", "SubjectNotFound", "require_subject", "subject_exists"]


class SubjectNotFound(Exception):
    """Raised when the analyst's chosen subject is not in the dataset.

    An input error, in the same family as a missing dataset file, rather than a
    governed refusal. It is raised rather than returned for the reason STEP-02
    gave for illegal state transitions: a governed outcome is something the
    system decided about a well-formed request, and this is a request that
    cannot be acted on at all. The CLI reports it as ``EXIT_INPUT_ERROR``.
    """


_ACCOUNT_META = resolve_table(DataScope.ACCOUNT_META)
_CHANNEL = resolve_table(DataScope.CHANNEL)
_COMMENT = resolve_table(DataScope.COMMENT)
_VIDEO = resolve_table(DataScope.VIDEO)

SUBJECT_QUERIES: Mapping[EntityKind, str] = {
    EntityKind.ACCOUNT: f"SELECT 1 FROM {_ACCOUNT_META} WHERE account_id = ? LIMIT 1",
    EntityKind.CHANNEL: f"SELECT 1 FROM {_CHANNEL} WHERE channel_id = ? LIMIT 1",
    EntityKind.COMMENT: f"SELECT 1 FROM {_COMMENT} WHERE comment_id = ? LIMIT 1",
    EntityKind.VIDEO: f"SELECT 1 FROM {_VIDEO} WHERE video_id = ? LIMIT 1",
}
"""One existence query per entity kind, exposed as data so a test can assert
every table it names resolves through ``DataScope`` and none mentions the sealed
schema. A module that only *claims* to stay inside the allowlist is a module
nobody can check."""


def _query_for(kind: EntityKind) -> str:
    """Exhaustive over ``EntityKind``, closed by ``assert_never``.

    The construction ``scopes.resolve_table`` uses, for the same reason: a new
    entity kind that nobody wrote an existence query for is a type error here
    rather than a subject that silently cannot be validated.
    """
    match kind:
        case EntityKind.ACCOUNT | EntityKind.CHANNEL | EntityKind.COMMENT | EntityKind.VIDEO:
            return SUBJECT_QUERIES[kind]
        case _:  # pragma: no cover - exhaustiveness guard, unreachable per mypy
            assert_never(kind)


def subject_exists(
    connection: duckdb.DuckDBPyConnection, subject_id: str, kind: EntityKind
) -> bool:
    """Whether ``subject_id`` names a real entity of ``kind``.

    Pure with respect to the dataset: one bound read, no writes, and nothing
    that could alter what a later query sees. The id is a parameter, so an id
    shaped like SQL is a value that matches nothing rather than a query.
    """
    return connection.execute(_query_for(kind), [subject_id]).fetchone() is not None


def require_subject(
    connection: duckdb.DuckDBPyConnection, subject_id: str, kind: EntityKind
) -> None:
    """Refuse to proceed unless the subject exists. Called before a session opens.

    The message names the kind, because the commonest way to trip this is a real
    id of the wrong kind: the CLI seeds an evidence session as a channel, so an
    account id that exists perfectly well is still not a channel and cannot be
    investigated as one.
    """
    if not subject_id.strip():
        raise SubjectNotFound("a subject id is required and must not be blank")
    if not subject_exists(connection, subject_id, kind):
        raise SubjectNotFound(
            f"no {kind.value} named {subject_id!r} exists in this dataset. A session is not "
            "opened for a subject that is not there: an investigation of a nonexistent entity "
            "would produce a perfectly valid audit trail attesting to nothing"
        )
