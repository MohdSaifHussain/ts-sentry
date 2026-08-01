# SPDX-License-Identifier: MIT
"""Provenance stamping shared by every artifact this system writes.

STEP-01 3.1 requires the build manifest to carry the git SHA and a SHA-256 per
exported table; STEP-03 D6 requires the session manifest to carry the same
stamp over its own artifacts. Both helpers started life private to
``cli.main``. They live here because two manifests that stamp provenance
differently are two manifests whose numbers cannot be compared, and because
``orchestrator`` must not import from ``cli`` to get them.

Neither function reads the clock. Timestamps in this system are always
supplied by their caller (``signature.sign`` set the precedent), so nothing
here can make an artifact irreproducible.
"""

import hashlib
import json
import subprocess
from pathlib import Path

from ts_sentry.governance.canonical import digest_fields, require_sha256_hex

__all__ = [
    "BUILD_MANIFEST",
    "UNKNOWN_GIT_SHA",
    "DatasetDigestError",
    "dataset_digest_from_manifest",
    "git_sha",
    "sha256_file",
]

BUILD_MANIFEST = "build_manifest.json"

_DATASET_DIGEST_DOMAIN = "ts-sentry/dataset-digest/v2"
"""Domain separator for the dataset identity, deliberately at v2.

The v1 identity was ``sha256(build.duckdb)``. Bumping the domain rather than
reusing it means a pre-fix and a post-fix digest of the *same* build cannot
collide, so nobody can compare a session id from before this change with one
from after and believe the two runs saw different data. The version is the
difference, and it is structural rather than a note in a changelog.
"""


class DatasetDigestError(Exception):
    """Raised when a build's identity cannot be established.

    Distinct from a missing file: this says "the thing at this path is not a
    build manifest this code can read", which the CLI reports as an input error
    rather than letting a session proceed on an identity nobody can reproduce.
    """


def dataset_digest_from_manifest(build_dir: Path) -> str:
    """The dataset's identity, derived from its content rather than its container.

    Closes the gap STEP-03 recorded and carried here. ``dataset_digest`` was the
    SHA-256 of ``build.duckdb``, and the store's internal layout is not
    byte-stable even when its contents are: two ``--seed 42 --scale 1`` builds
    produce byte-identical Parquet exports for all six tables and *different*
    store files. Session ids derived from it therefore changed on every rebuild,
    which Saif found by re-running and getting an id nobody had seen.

    The build manifest's ``table_hashes`` are the digests of those Parquet
    exports, which STEP-01 verified byte-stable and which CI re-verifies on
    every run. Deriving identity from them makes a session id a function of the
    data rather than of the file that happened to hold it.

    There is deliberately no fallback to hashing the store. A silent fallback
    would restore exactly the defect being closed, in the case where it is
    hardest to notice.
    """
    manifest_path = build_dir / BUILD_MANIFEST
    if not manifest_path.is_file():
        raise DatasetDigestError(
            f"no {BUILD_MANIFEST} beside the dataset at {build_dir}. A session's dataset "
            "identity comes from the build manifest's table hashes, so a build without one "
            "cannot be identified reproducibly; rebuild with 'ts-sentry build-dataset'"
        )

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetDigestError(f"could not read {manifest_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise DatasetDigestError(f"{manifest_path} does not contain a JSON object")

    table_hashes = raw.get("table_hashes")
    if not isinstance(table_hashes, dict) or not table_hashes:
        raise DatasetDigestError(f"{manifest_path} carries no table_hashes")

    fields: list[str] = []
    for table in sorted(table_hashes):
        digest = table_hashes[table]
        if not isinstance(digest, str):
            raise DatasetDigestError(f"{manifest_path}: table_hashes[{table!r}] is not a string")
        try:
            require_sha256_hex(digest, f"table_hashes[{table!r}]")
        except ValueError as exc:
            raise DatasetDigestError(f"{manifest_path}: {exc}") from exc
        fields.append(f"{table}={digest}")

    return digest_fields(_DATASET_DIGEST_DOMAIN, *fields)


UNKNOWN_GIT_SHA = "unknown"
"""Recorded when git is unavailable or the tree is not a repository. An
explicit value rather than an omitted field: a manifest that silently drops
its provenance stamp is worse than one that says it could not take it."""


def git_sha() -> str:
    """Current ``HEAD`` SHA, or ``UNKNOWN_GIT_SHA`` if git cannot answer.

    "Cannot answer" covers git being **absent**, not only git returning
    nonzero. That distinction was a claim wider than the behaviour until
    STEP-08: this docstring and ``UNKNOWN_GIT_SHA``'s both said an unavailable
    git was handled, while ``subprocess.run`` raises ``FileNotFoundError`` when
    the executable does not exist, so the process died instead.

    It survived seven phases because every environment this had ever run in had
    git installed. The published container image is the first that does not,
    deliberately: the runtime stage carries no git, no compiler and no uv. The
    defect surfaced the first time a session was run inside it, as a crash in
    ``build-dataset`` rather than as a missing provenance field.

    Fixed in the code rather than in the docstring, because a released
    container is exactly the environment where a build manifest should record
    that it could not take a provenance stamp, and carry on.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        )
    except OSError:
        # FileNotFoundError when git is not installed; PermissionError when it
        # is present and not executable. Both mean the same thing to a caller.
        return UNKNOWN_GIT_SHA
    return result.stdout.strip() if result.returncode == 0 else UNKNOWN_GIT_SHA


def sha256_file(path: Path) -> str:
    """SHA-256 of a file's bytes, read in chunks so artifact size does not
    bound memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
