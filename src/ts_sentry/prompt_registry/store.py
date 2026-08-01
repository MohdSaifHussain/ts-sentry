# SPDX-License-Identifier: MIT
"""D1: the registry as a whole, and its on-disk form.

``prompts/`` holds one file per version, named by the SHA-256 of its own bytes,
plus ``manifest.json`` carrying the version records and the activation history.
This module loads that, verifies it against itself, and writes it back.

The load path trusts nothing the manifest says about itself
-----------------------------------------------------------
Every digest is recomputed from the bytes on disk. The manifest is an index,
not an authority: if it were believed, a registry could report whatever a text
editor last wrote into it, and "content-addressed" would be a naming convention
rather than a property. Six things are checked, and each one has a defect it
exists to catch:

===================================== =====================================
Check                                 What it catches
===================================== =====================================
file name equals digest of its bytes  a version edited in place
manifest digest equals that digest    a manifest pointing at other bytes
``system_prompt_sha256`` re-derives   a prompt_id/text pair changed apart
every referenced file exists          a version deleted to hide it
no extra files in ``prompts/``        a version present but unrecorded
parents and pointers resolve          a lineage or pointer naming nothing
===================================== =====================================

The fifth is the one worth pausing on. A file nobody records is how a prompt
gets run without ever having been evaluated, so an unrecorded file is an error
rather than something politely ignored.

Writing never overwrites
------------------------
``write_registry`` refuses to replace an existing prompt file, even with
identical bytes. Content addressing already makes a *changed* file detectable;
this makes it unattemptable, which is what STEP-06 3.4's "prior versions
retained forever" asks for. The manifest is the one file that is rewritten,
because it is the index, and its own integrity comes from the fact that every
claim in it is re-checked against the files on load.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ts_sentry.orchestrator.firewall import SystemPrompt, system_prompt
from ts_sentry.prompt_registry.activation import ActivationHistory, history_from_json_array
from ts_sentry.prompt_registry.registry import (
    MANIFEST_NAME,
    PROMPT_SUFFIX,
    PromptRegistryError,
    PromptTask,
    PromptVersion,
    build_system_prompt,
    content_digest,
    read_manifest_object,
    version_from_json_object,
)

__all__ = [
    "REGISTRY_SCHEMA",
    "PromptRegistry",
    "load_registry",
    "write_registry",
]

REGISTRY_SCHEMA = "ts-sentry/prompt-registry/v1"
"""Schema tag in the manifest, so a future format change is a visible refusal
rather than a silent misread of fields that happen to share names."""


@dataclass(frozen=True, slots=True)
class PromptRegistry:
    """Every prompt version this project has ever registered, plus the pointers.

    ``texts`` is keyed by content digest and holds what was actually on disk.
    Carrying the text rather than a path means a loaded registry is a value: it
    has already been verified, and nothing downstream can re-read a file that
    changed underneath it between the check and the use.
    """

    versions: tuple[PromptVersion, ...]
    history: ActivationHistory = field(default_factory=ActivationHistory)
    texts: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        by_digest: dict[str, PromptVersion] = {}
        by_task_version: set[tuple[PromptTask, str]] = set()

        for record in self.versions:
            if record.content_digest in by_digest:
                raise PromptRegistryError(
                    f"two version records share content digest {record.content_digest[:12]}"
                )
            key = (record.task, record.version)
            if key in by_task_version:
                raise PromptRegistryError(
                    f"two version records claim to be {record.task.value} {record.version}"
                )
            by_digest[record.content_digest] = record
            by_task_version.add(key)

        for record in self.versions:
            if record.parent is not None and record.parent not in by_digest:
                raise PromptRegistryError(
                    f"{record.prompt_id} names parent {record.parent[:12]}, which this "
                    "registry does not hold. Lineage that points at nothing is not lineage"
                )
            if record.content_digest not in self.texts:
                raise PromptRegistryError(
                    f"{record.prompt_id} is recorded but its text is absent from the registry"
                )

        for entry in self.history.entries:
            target = by_digest.get(entry.content_digest)
            if target is None:
                raise PromptRegistryError(
                    f"activation entry {entry.seq} points {entry.task.value} at "
                    f"{entry.content_digest[:12]}, which this registry does not hold"
                )
            if target.task is not entry.task:
                raise PromptRegistryError(
                    f"activation entry {entry.seq} activates {entry.content_digest[:12]} for "
                    f"{entry.task.value}, but that version is bound to {target.task.value}. "
                    "Task binding is what stops one agent's prompt being activated for another"
                )

    def by_digest(self, digest: str) -> PromptVersion:
        for record in self.versions:
            if record.content_digest == digest:
                return record
        raise PromptRegistryError(f"no prompt version with content digest {digest}")

    def versions_for(self, task: PromptTask) -> tuple[PromptVersion, ...]:
        return tuple(record for record in self.versions if record.task is task)

    def active(self, task: PromptTask) -> PromptVersion:
        """The version currently pointed at for ``task``.

        Refuses rather than falling back to "the only one" or "the newest".
        A task with no activation has no incumbent, and inventing one would be
        the registry deciding what runs, which is the eval harness's job.
        """
        digest = self.history.active(task)
        if digest is None:
            raise PromptRegistryError(
                f"no prompt is active for {task.value}. Activation is a recorded pointer "
                "move, so a task with no history has no incumbent"
            )
        return self.by_digest(digest)

    def text(self, digest: str) -> str:
        try:
            return self.texts[digest]
        except KeyError as exc:
            raise PromptRegistryError(f"no prompt text held for digest {digest}") from exc

    def system_prompt(self, digest: str) -> SystemPrompt:
        """The ``SystemPrompt`` for one version, digests re-checked."""
        record = self.by_digest(digest)
        return build_system_prompt(record, self.text(digest))

    def active_system_prompt(self, task: PromptTask) -> SystemPrompt:
        return self.system_prompt(self.active(task).content_digest)

    def registered(
        self,
        task: PromptTask,
        version: str,
        text: str,
        *,
        parent: str | None,
        created_ist: datetime,
    ) -> "PromptRegistry":
        """A new registry carrying one more version. Mutates nothing.

        The digest is computed from the text here rather than accepted from the
        caller, so there is no way to register a record whose digest and bytes
        disagree.
        """
        digest = content_digest(text)
        record = PromptVersion(
            task=task,
            version=version,
            content_digest=digest,
            system_prompt_sha256=system_prompt(f"{task.value}.{version}", text).sha256,
            parent=parent,
            created_ist=created_ist,
        )
        return PromptRegistry(
            versions=(*self.versions, record),
            history=self.history,
            texts={**self.texts, digest: text},
        )

    def with_history(self, history: ActivationHistory) -> "PromptRegistry":
        """The same versions under a new pointer log."""
        return PromptRegistry(versions=self.versions, history=history, texts=self.texts)

    def to_manifest_object(self) -> dict[str, object]:
        return {
            "schema": REGISTRY_SCHEMA,
            "versions": [record.to_json_object() for record in self.versions],
            "activations": self.history.to_json_array(),
        }


def load_registry(root: Path) -> PromptRegistry:
    """Read ``root`` and verify it against itself. See the module docstring."""
    raw = read_manifest_object(root)

    schema = raw.get("schema")
    if schema != REGISTRY_SCHEMA:
        raise PromptRegistryError(
            f"{root / MANIFEST_NAME} declares schema {schema!r}; this build reads "
            f"{REGISTRY_SCHEMA!r}"
        )

    raw_versions = raw.get("versions")
    if not isinstance(raw_versions, list):
        raise PromptRegistryError(f"{root / MANIFEST_NAME} carries no versions array")
    for item in raw_versions:
        if not isinstance(item, dict):
            raise PromptRegistryError(
                f"each version record must be a JSON object; got {type(item).__name__}"
            )
    versions = tuple(version_from_json_object(item) for item in raw_versions)

    raw_activations = raw.get("activations", [])
    if not isinstance(raw_activations, list):
        raise PromptRegistryError(f"{root / MANIFEST_NAME} carries a non-array activations field")
    history = history_from_json_array(raw_activations)

    texts: dict[str, str] = {}
    for record in versions:
        path = root / record.filename
        if not path.is_file():
            raise PromptRegistryError(
                f"{record.prompt_id} is recorded in the manifest but {record.filename} is "
                "missing. Prior versions are retained forever (STEP-06 3.4)"
            )
        text = path.read_text(encoding="utf-8")
        actual = content_digest(text)
        if actual != record.content_digest:
            raise PromptRegistryError(
                f"{path.name} does not hash to its own name: the bytes digest to {actual}. "
                "A content-addressed file that no longer matches its name has been edited "
                "in place"
            )
        texts[record.content_digest] = text

    _refuse_unrecorded_files(root, versions)

    registry = PromptRegistry(versions=versions, history=history, texts=texts)
    for record in registry.versions:
        # Re-derives and compares `system_prompt_sha256`; raises on drift.
        registry.system_prompt(record.content_digest)
    return registry


def _refuse_unrecorded_files(root: Path, versions: tuple[PromptVersion, ...]) -> None:
    """A prompt file nobody recorded is a prompt nobody evaluated.

    Refused rather than ignored. The whole point of the phase is that a prompt
    earns activation through the eval harness, and a file sitting in the
    registry directory outside the manifest is one that skipped the queue.
    """
    recorded = {record.filename for record in versions}
    stray = sorted(
        path.name for path in root.glob(f"*{PROMPT_SUFFIX}") if path.name not in recorded
    )
    if stray:
        raise PromptRegistryError(
            f"{root} holds prompt files absent from the manifest: {stray}. A prompt file "
            "nobody recorded is a prompt nobody evaluated"
        )


def write_registry(root: Path, registry: PromptRegistry) -> None:
    """Persist ``registry``. Never replaces an existing prompt file.

    The manifest is rewritten because it is the index. Prompt files are written
    once and then refused, which is what makes "prior versions retained
    forever" a property of this function rather than a promise about callers.
    """
    root.mkdir(parents=True, exist_ok=True)

    for record in registry.versions:
        path = root / record.filename
        text = registry.text(record.content_digest)
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing != text:
                raise PromptRegistryError(
                    f"{path.name} already exists with different bytes. A content-addressed "
                    "file cannot legitimately change, so this is corruption or a digest "
                    "collision, and either way it is not something to overwrite"
                )
            continue
        path.write_text(text, encoding="utf-8", newline="\n")

    manifest = root / MANIFEST_NAME
    manifest.write_text(
        json.dumps(registry.to_manifest_object(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
