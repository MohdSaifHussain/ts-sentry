# SPDX-License-Identifier: MIT
"""D1: the session manifest, and the chain-head anchor it exists to carry.

This discharges the third obligation the STEP-02 Outcome carried into STEP-03.

The limitation it closes
------------------------
Hash-chain verification detects modification, reordering, and interior
deletion. It cannot detect entries dropped from the *end*: what remains is a
shorter chain whose every link still recomputes, indistinguishable from a
session that ended earlier. STEP-02 shipped the comparison verb
(``verify-ledger --expect-head COUNT:HASH``) and deliberately shipped no
storage, leaving the anchor to this phase.

The manifest is that storage. ``expected_head`` is read *after*
``SESSION_CLOSE`` is appended, so it describes the finished chain, and
``verify-ledger --expect-head-from`` compares a later reading of the ledger
against it.

Honest limit, stated at its true width
--------------------------------------
An anchor is only as independent as its custody. A manifest written next to
the ledger it describes is trivially rewritten by anyone who can truncate that
ledger, so co-located files buy tamper-*evidence against accident and against
partial tampering*, not against a determined editor with write access to the
whole session directory. The anchor becomes a real control exactly when a copy
of the manifest is held somewhere the ledger's writer cannot reach: a
reviewer's checkout, a CI artifact store, a countersigning system. That
independence is a deployment property, not something this module can
manufacture, so it is documented rather than claimed.

What is deliberately not here: any notion of signing or countersigning the
manifest. That would be a second integrity system, and STEP-03 does not
specify one.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ts_sentry.governance.canonical import require_sha256_hex
from ts_sentry.governance.ledger import ChainHead
from ts_sentry.orchestrator.core import BudgetSnapshot, CloseReason
from ts_sentry.provenance import sha256_file

__all__ = [
    "MANIFEST_VERSION",
    "ArtifactRecord",
    "ManifestError",
    "SessionManifest",
    "read_expected_head",
]

MANIFEST_VERSION = "1.0.0"
"""SemVer of the manifest *format*, not of the session. A reader that finds a
major version it does not know must refuse rather than guess at fields."""


class ManifestError(Exception):
    """Raised when a manifest cannot be read or does not carry what it claims.

    Distinct from an integrity finding, exactly as ``cli.main.InputError`` is:
    "this file is not a manifest" and "this ledger was truncated" are
    different answers and must not share an exit code.
    """


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """One file the session produced, with the digest it had when written."""

    name: str
    path: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("artifact name must be non-empty")
        require_sha256_hex(self.sha256, f"artifact {self.name} sha256")

    @classmethod
    def of(cls, name: str, path: Path, *, relative_to: Path) -> "ArtifactRecord":
        """Record ``path`` with its current digest, stored relative to the
        manifest's own directory so a session directory stays movable."""
        return cls(
            name=name,
            path=path.relative_to(relative_to).as_posix(),
            sha256=sha256_file(path),
        )

    def to_json_object(self) -> dict[str, object]:
        return {"name": self.name, "path": self.path, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class SessionManifest:
    """Everything needed to re-check a finished session from outside it.

    Carries the seed-and-version stamp discipline STEP-01 3.1 established for
    the build manifest: what produced this, from what, at which code version.
    """

    session_id: str
    analyst_id: str
    opened_ts_iso: str
    closed_ts_iso: str
    close_reason: CloseReason
    dataset_digest: str
    mandate_set_hash: str
    mandate_hashes: Mapping[str, str]
    expected_head: ChainHead
    event_counts: Mapping[str, int]
    budgets: Mapping[str, BudgetSnapshot]
    git_sha: str
    artifacts: Sequence[ArtifactRecord]
    manifest_version: str = MANIFEST_VERSION

    def __post_init__(self) -> None:
        require_sha256_hex(self.dataset_digest, "dataset_digest")
        require_sha256_hex(self.mandate_set_hash, "mandate_set_hash")
        require_sha256_hex(self.expected_head.entry_hash, "expected_head.entry_hash")
        if self.expected_head.count < 0:
            raise ValueError(
                f"expected_head.count must be non-negative; got {self.expected_head.count}"
            )

    def to_json_object(self) -> dict[str, object]:
        return {
            "manifest_version": self.manifest_version,
            "session_id": self.session_id,
            "analyst_id": self.analyst_id,
            "opened_ts_ist": self.opened_ts_iso,
            "closed_ts_ist": self.closed_ts_iso,
            "close_reason": self.close_reason.value,
            "dataset_digest": self.dataset_digest,
            "mandate_set_hash": self.mandate_set_hash,
            "mandate_hashes": dict(sorted(self.mandate_hashes.items())),
            "expected_head": {
                "count": self.expected_head.count,
                "entry_hash": self.expected_head.entry_hash,
            },
            "event_counts": dict(sorted(self.event_counts.items())),
            "budgets": {
                agent: snapshot.to_json_object() for agent, snapshot in sorted(self.budgets.items())
            },
            "git_sha": self.git_sha,
            "artifacts": [artifact.to_json_object() for artifact in self.artifacts],
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_json_object(), indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )


def read_expected_head(path: Path) -> ChainHead:
    """Read just the anchor out of a session manifest.

    Deliberately not a full deserializer. The one consumer is
    ``verify-ledger --expect-head-from``, which needs the head and nothing
    else, and a reader that parses fields nobody checks is a reader that can
    fail for reasons nobody cares about. Every failure mode below is a
    ``ManifestError``, which the CLI reports as an input error rather than as
    an integrity finding.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(f"could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ManifestError(f"{path} does not contain a JSON object")

    version = raw.get("manifest_version")
    if not isinstance(version, str):
        raise ManifestError(f"{path} carries no manifest_version")
    major = version.partition(".")[0]
    if major != MANIFEST_VERSION.partition(".")[0]:
        raise ManifestError(
            f"{path} is manifest format {version}; this build reads major version "
            f"{MANIFEST_VERSION.partition('.')[0]}"
        )

    head = raw.get("expected_head")
    if not isinstance(head, dict):
        raise ManifestError(f"{path} carries no expected_head object")

    count = head.get("count")
    entry_hash = head.get("entry_hash")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ManifestError(f"{path}: expected_head.count must be a non-negative integer")
    if not isinstance(entry_hash, str):
        raise ManifestError(f"{path}: expected_head.entry_hash must be a string")
    try:
        require_sha256_hex(entry_hash, "expected_head.entry_hash")
    except ValueError as exc:
        raise ManifestError(f"{path}: {exc}") from exc

    return ChainHead(count=count, entry_hash=entry_hash)
