# SPDX-License-Identifier: MIT
"""D1: the versioned prompt registry (STEP-06 D1, ARCHITECTURE 4.4).

Policy-as-prompt needs prompts that have an identity, a lineage, and a state.
This module is that registry, and it is the deferral
``orchestrator.firewall.SystemPrompt`` recorded coming due: that docstring says
"a versioned registry with content-addressed files is STEP-06, the prompt-eval
agent. This is the minimum that makes the invariant checkable now, and it is
deliberately not that registry."

Two identities, kept apart on purpose
-------------------------------------
Following DECISIONS 5.2, where the policy corpus learned the same lesson the
expensive way:

* ``content_digest`` is SHA-256 over the prompt text's UTF-8 bytes. It is the
  **file name**, so the registry is content-addressed in the ordinary sense: a
  file whose bytes were edited no longer hashes to its own name, and the
  tampering is visible without consulting anything else.
* ``system_prompt_sha256`` is what a *session* records. It comes from
  ``firewall.system_prompt``, covers ``(prompt_id, text)`` under its own domain
  separator, and is the value already written into every ``PROMPT_SENT`` ledger
  payload (``adapter.call_model``). It is derived and re-checked on load rather
  than trusted from the manifest.

Both are recorded because they answer different questions. "Which bytes are
these" is the first; "which prompt did that session send" is the second, and a
registry that carried only one of them could not answer the other.

Activation state is not a field on a version
--------------------------------------------
D1 asks the manifest to carry "activation state". It lives in the append-only
activation history (:mod:`ts_sentry.prompt_registry.activation`), not on the
version record, and that is the whole of STEP-06 3.4. A mutable ``active``
field on a version record would mean activating a prompt **rewrites** the
record of another one, which is exactly the overwrite 3.4 forbids. The active
version of a task is therefore *derived* from the history, and a version record
is written once and never touched again.

What this module deliberately does not do
-----------------------------------------
It does not ledger, it does not evaluate, and it does not decide. Activation is
a pure state transition here; the orchestrator is what writes it down and
ledgers it, on the same split ``mandate.validate`` established in STEP-02.

It also reaches nothing in the ledger, the gates, dispatch, or the signature
path. No agent imports it today (see the package docstring for why that is a
statement about decision C rather than about this module), and keeping it
importable-by-an-agent is what lets a later phase wire the turns to it without
reopening the governance question.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from ts_sentry.data.tz import require_ist, require_ist_iso
from ts_sentry.governance.canonical import require_sha256_hex
from ts_sentry.orchestrator.firewall import SystemPrompt, system_prompt

__all__ = [
    "MANIFEST_NAME",
    "PROMPT_SUFFIX",
    "PromptRegistryError",
    "PromptTask",
    "PromptVersion",
    "content_digest",
    "read_manifest_object",
    "version_from_json_object",
]

MANIFEST_NAME = "manifest.json"
PROMPT_SUFFIX = ".txt"

_VERSION_PATTERN = re.compile(r"^v[1-9]\d*$")
"""Prompt version labels. Deliberately not SemVer, unlike ``Mandate.version``.

A prompt has no API surface for a patch or minor release to be compatible
*with*: any edit to the text is a new thing that has to earn activation through
the eval harness. One monotonic counter says that; a three-part version invites
the reader to believe a ``1.0.1`` is a safe drop-in for a ``1.0.0``, which is
precisely the silent-drift belief ARCHITECTURE 2.2 A-05 exists to prevent.
"""


class PromptRegistryError(Exception):
    """Raised when the registry is unreadable, inconsistent, or self-contradictory.

    Its own class rather than ``ValueError`` for the reason
    ``DatasetDigestError`` is: the CLI reports it as an input error instead of
    letting an evaluation proceed against a registry nobody can trust.
    """


class PromptTask(StrEnum):
    """What a prompt is *for*. D1's "task binding".

    A version binds to exactly one task, and the eval harness evaluates a
    candidate only against the incumbent of the same task. The binding is what
    stops a memo prompt being activated over a triage prompt because their
    digests were both valid hex.

    The first three are the prompts STEP-03 through STEP-05 already ship, moved
    here by decision C. ``CLASSIFY_THREAT_CLASS`` is new in STEP-06 and is the
    only task this phase's eval set can grade, because it is the only one whose
    output is a class label. See the STEP-06 Honest Limits: no session consumes
    its output yet.
    """

    TRIAGE_RATIONALE = "triage.rationale"
    EVIDENCE_PIVOT = "evidence.pivot"
    MEMO_STATEMENT = "memo.statement"
    CLASSIFY_THREAT_CLASS = "classify.threat_class"


def content_digest(text: str) -> str:
    """SHA-256 over the prompt text's UTF-8 bytes.

    Plain ``hashlib`` rather than ``canonical.digest_fields``: this is a digest
    of one blob, not of a field sequence, so there are no field boundaries to
    disambiguate and a domain separator would only make the file name disagree
    with what ``sha256sum`` reports for the same file. A reviewer must be able
    to check a file name with a standard tool.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PromptVersion:
    """One immutable prompt version record.

    Written once, never updated. Every field is either intrinsic to the text
    (the two digests) or fixed at the moment of registration (task, version,
    parent, timestamp), so there is nothing here that a later activation could
    have cause to change.
    """

    task: PromptTask
    version: str
    content_digest: str
    system_prompt_sha256: str
    parent: str | None
    created_ist: datetime

    def __post_init__(self) -> None:
        if _VERSION_PATTERN.match(self.version) is None:
            raise ValueError(f"version must look like 'v1', 'v2', ...; got {self.version!r}")
        require_sha256_hex(self.content_digest, "content_digest")
        require_sha256_hex(self.system_prompt_sha256, "system_prompt_sha256")
        if self.parent is not None:
            require_sha256_hex(self.parent, "parent")
            if self.parent == self.content_digest:
                raise ValueError("a prompt version cannot be its own parent")
        require_ist(self.created_ist, "created_ist")

    @property
    def prompt_id(self) -> str:
        """The id ``firewall.system_prompt`` hashes alongside the text.

        Derived rather than stored, so the manifest cannot record a
        ``prompt_id`` that disagrees with the task and version beside it. It is
        spelled to reproduce the three ids STEP-03 through STEP-05 already
        wrote (``triage.rationale.v1``), which is what makes decision C's
        migration digest-preserving.
        """
        return f"{self.task.value}.{self.version}"

    @property
    def filename(self) -> str:
        return f"{self.content_digest}{PROMPT_SUFFIX}"

    def to_json_object(self) -> dict[str, object]:
        return {
            "task": self.task.value,
            "version": self.version,
            "prompt_id": self.prompt_id,
            "content_digest": self.content_digest,
            "system_prompt_sha256": self.system_prompt_sha256,
            "parent": self.parent,
            "created_ist": self.created_ist.isoformat(),
        }


def version_from_json_object(obj: Mapping[str, object]) -> PromptVersion:
    """Rebuild a version record from the manifest.

    Strict about shape and about the derived ``prompt_id``: a manifest whose
    stored ``prompt_id`` disagrees with its own task and version is a manifest
    somebody has edited by hand, and accepting it would let a session send one
    prompt while the registry named another.
    """
    try:
        task = PromptTask(str(obj["task"]))
        version = str(obj["version"])
        stored_prompt_id = str(obj["prompt_id"])
        raw_parent = obj["parent"]
        created_raw = str(obj["created_ist"])
    except KeyError as exc:
        raise PromptRegistryError(f"prompt version record is missing {exc}") from exc
    except ValueError as exc:
        raise PromptRegistryError(f"prompt version record is unreadable: {exc}") from exc

    require_ist_iso(created_raw, "created_ist")
    record = PromptVersion(
        task=task,
        version=version,
        content_digest=str(obj["content_digest"]),
        system_prompt_sha256=str(obj["system_prompt_sha256"]),
        parent=None if raw_parent is None else str(raw_parent),
        created_ist=datetime.fromisoformat(created_raw),
    )
    if stored_prompt_id != record.prompt_id:
        raise PromptRegistryError(
            f"manifest records prompt_id {stored_prompt_id!r} for {task.value} {version}, "
            f"which derives {record.prompt_id!r}"
        )
    return record


def build_system_prompt(record: PromptVersion, text: str) -> SystemPrompt:
    """The ``SystemPrompt`` for ``record``, checked against its own digests.

    Both digests are re-derived here rather than read from the manifest. That is
    the point of the whole module: a registry that trusted its own manifest
    would report exactly what an editor wrote into it.
    """
    if content_digest(text) != record.content_digest:
        raise PromptRegistryError(
            f"prompt {record.prompt_id} does not hash to its recorded content_digest "
            f"{record.content_digest}; the file has been edited in place"
        )
    prompt = system_prompt(record.prompt_id, text)
    if prompt.sha256 != record.system_prompt_sha256:
        raise PromptRegistryError(
            f"prompt {record.prompt_id} derives system prompt digest {prompt.sha256}, "
            f"but the manifest records {record.system_prompt_sha256}"
        )
    return prompt


def read_manifest_object(root: Path) -> Mapping[str, object]:
    """Read and shape-check ``manifest.json`` under ``root``."""
    path = root / MANIFEST_NAME
    if not path.is_file():
        raise PromptRegistryError(f"no {MANIFEST_NAME} under {root}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromptRegistryError(f"could not read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PromptRegistryError(f"{path} does not contain a JSON object")
    return raw
