# SPDX-License-Identifier: MIT
"""D2: the eval set's *item* side, and the only side most callers may hold.

Split from :mod:`ts_sentry.data.eval_build` for a governance reason rather than
a filing one, and the split is the same shape STEP-05 used for the policy
corpus: ``policy_fetch`` reaches the network and builds, ``policy_corpus`` reads
what was built. Here, ``eval_build`` reads ``sealed._labels`` and this module
reads the committed artifact.

That matters because the orchestrator needs the items. If ``EvalItem`` lived
beside the builder, then every module that wanted to read an item would import
a module that queries ground truth, and the sealed boundary would be one
attribute access wide. Nothing in this module names or reaches ``sealed``.

Labels are not here either
--------------------------
This module loads ``items.json``. It cannot load ``labels.json``, and there is
no function here that would. The labels are
:mod:`ts_sentry.orchestrator.eval_labels`, which is in the import-graph test's
forbidden set for every module under ``agents.``. So the reader an agent could
legitimately reach carries no labels, and the reader that carries labels is one
an agent cannot reach.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ts_sentry.data.enums import ThreatClass
from ts_sentry.governance.canonical import digest_fields

__all__ = [
    "EVAL_SCHEMA",
    "ITEMS_FILE",
    "ITEM_ID_PREFIX",
    "LABELS_FILE",
    "MANIFEST_FILE",
    "EvalItem",
    "EvalSetError",
    "items_digest",
    "labels_digest",
    "load_items",
    "load_manifest",
]

EVAL_SCHEMA = "ts-sentry/eval-set/v1"
ITEMS_FILE = "items.json"
LABELS_FILE = "labels.json"
MANIFEST_FILE = "manifest.json"

ITEM_ID_PREFIX = "item-"
"""Item ids are opaque and sequential.

Load-bearing rather than cosmetic. Planted entity ids are templated with their
own class (``t02_chan_000_000`` names T-02 in three characters), so an item
keyed by entity id would hand the answer to the model in the record id, pass
through the input firewall untouched, and leave every governance control working
exactly as designed while every metric came back excellent.
"""


class EvalSetError(Exception):
    """Raised when an eval set is missing, unreadable, or self-inconsistent."""


@dataclass(frozen=True, slots=True)
class EvalItem:
    """One case a classifier is asked about.

    Two fields, and the absence of a third is the whole of STEP-06 3.2 on this
    side of the boundary. There is no label field, no class field, and no
    stratum field, so "do not put the label in the prompt" is unwritable rather
    than merely discouraged.

    A first draft of this type carried a ``stratum``, which for a stratified set
    is the label wearing a different name. It was removed before any item was
    written, and it is recorded here because the next person to want a
    convenient grouping field will want exactly that one.
    """

    item_id: str
    content: str

    def __post_init__(self) -> None:
        if not self.item_id.startswith(ITEM_ID_PREFIX):
            raise ValueError(
                f"item ids are opaque and prefixed {ITEM_ID_PREFIX!r}; got {self.item_id!r}. "
                "An id derived from the entity would name its own threat class"
            )
        if not self.content.strip():
            raise ValueError(f"{self.item_id} has no content to classify")

    def to_json_object(self) -> dict[str, object]:
        return {"item_id": self.item_id, "content": self.content}


def items_digest(items: Sequence[EvalItem]) -> str:
    """Identity of the item set, independent of file formatting."""
    return digest_fields(
        "ts-sentry/eval-items/v1", *(f"{item.item_id}={item.content}" for item in items)
    )


def labels_digest(labels: Mapping[str, ThreatClass]) -> str:
    """Identity of the label set.

    Lives beside ``items_digest`` rather than beside the label store, so the
    builder can stamp both digests into one manifest without importing an
    orchestrator module. It takes labels as an argument and holds none.
    """
    return digest_fields(
        "ts-sentry/eval-labels/v1", *(f"{key}={labels[key].value}" for key in sorted(labels))
    )


def load_manifest(root: Path) -> Mapping[str, object]:
    """Read and shape-check the eval-set manifest."""
    path = root / MANIFEST_FILE
    if not path.is_file():
        raise EvalSetError(f"no eval-set manifest at {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvalSetError(f"could not read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise EvalSetError(f"{path} does not contain a JSON object")
    if raw.get("schema") != EVAL_SCHEMA:
        raise EvalSetError(
            f"{path} declares schema {raw.get('schema')!r}; this build reads {EVAL_SCHEMA!r}"
        )
    return raw


def load_items(root: Path) -> tuple[EvalItem, ...]:
    """Read the items and verify them against the manifest's digest.

    The digest check is the point rather than a courtesy: an eval report names
    the item set it was computed over, and an items file edited after the fact
    would make that name false while every number still looked plausible.
    """
    manifest = load_manifest(root)
    path = root / ITEMS_FILE
    if not path.is_file():
        raise EvalSetError(f"no eval items at {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvalSetError(f"could not read {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise EvalSetError(f"{path} does not contain a JSON array")

    items: list[EvalItem] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise EvalSetError("each eval item must be a JSON object")
        try:
            items.append(EvalItem(item_id=str(entry["item_id"]), content=str(entry["content"])))
        except KeyError as exc:
            raise EvalSetError(f"eval item is missing {exc}") from exc
        except ValueError as exc:
            raise EvalSetError(f"eval item is unusable: {exc}") from exc

    expected = manifest.get("items_sha256")
    actual = items_digest(items)
    if expected != actual:
        raise EvalSetError(
            f"{ITEMS_FILE} digests to {actual}, but the manifest records {expected}. The item "
            "set has changed since it was built"
        )
    return tuple(items)
