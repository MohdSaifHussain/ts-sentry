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
import subprocess
from pathlib import Path

__all__ = ["UNKNOWN_GIT_SHA", "git_sha", "sha256_file"]

UNKNOWN_GIT_SHA = "unknown"
"""Recorded when git is unavailable or the tree is not a repository. An
explicit value rather than an omitted field: a manifest that silently drops
its provenance stamp is worse than one that says it could not take it."""


def git_sha() -> str:
    """Current ``HEAD`` SHA, or ``UNKNOWN_GIT_SHA`` if git cannot answer."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else UNKNOWN_GIT_SHA


def sha256_file(path: Path) -> str:
    """SHA-256 of a file's bytes, read in chunks so artifact size does not
    bound memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
