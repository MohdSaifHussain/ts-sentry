# SPDX-License-Identifier: MIT
"""STEP-04 follow-up: the seed-existence guard.

Found by Saif at phase close. An evidence session had been run on
``t02_chan_003_000``, which does not exist in the seed-42 scale-1 build, and it
produced a fully valid audit trail for an investigation of nothing: exit 0, an
intact anchored chain, twenty ledgered ``HUMAN_DECISION`` approvals, every pack
through the ASSEMBLE gate, complete provenance.

The gap it closes, stated once:

    The assembly gate validates the artifact's internal consistency, not its
    correspondence to reality. Referential integrity is a closed property, and a
    pack describing nothing satisfies it perfectly. Seed-existence is the
    boundary check that ties the audit trail to a real subject.

Two tests, which is the whole requirement. The first is the one that matters:
refusal has to happen *before* anything is created, so the assertion is not
merely about the exit code but about the absence of a session directory. A guard
that refused after opening the session would already have written the chain,
manifest and anchor it exists to prevent.
"""

from pathlib import Path

import pytest

from ts_sentry.cli.main import EXIT_INPUT_ERROR, EXIT_OK, main

_REAL_T02_SUBJECT = "t02_chan_000_000"
_NONEXISTENT_SUBJECT = "t02_chan_003_000"


@pytest.fixture(scope="module")
def build(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("build")
    assert main(["build-dataset", "--seed", "42", "--scale", "1", "--out", str(out)]) == EXIT_OK
    return out


def _run(build: Path, out: Path, subject: str) -> int:
    return main(
        [
            "run-session",
            "--agent",
            "evidence",
            "--seed-dataset",
            str(build),
            "--case",
            "case-0000",
            "--subject",
            subject,
            "--review",
            "scripted",
            "--max-hops",
            "3",
            "--analyst-id",
            "saif",
            "--out",
            str(out),
        ]
    )


def test_a_nonexistent_subject_is_refused_before_a_session_exists(
    build: Path, tmp_path: Path
) -> None:
    """Exit 5, and nothing written.

    ``t02_chan_003_000`` is the real id from Saif's phase-close run: only T-02
    rings 000 and 001 are planted at this seed and scale, so it is absent from
    ``main.channel`` and has no row in ``sealed._labels``.

    The directory assertion is the substance. An exit code alone would pass for
    a guard that refused after opening the session, which is exactly the
    outcome being prevented: no session, no chain, not a short valid one.
    """
    out = tmp_path / "session"

    assert _run(build, out, _NONEXISTENT_SUBJECT) == EXIT_INPUT_ERROR

    assert not out.exists(), "a refused subject must leave no session directory behind"


def test_a_real_subject_proceeds(build: Path, tmp_path: Path) -> None:
    """The guard refuses what is absent and nothing else.

    A guard that refused everything would pass the test above and make the
    product unusable, so the other half is asserted here on a subject confirmed
    present in this build.
    """
    out = tmp_path / "session"

    assert _run(build, out, _REAL_T02_SUBJECT) == EXIT_OK

    assert (out / "evidence_pack.json").is_file()
    assert (out / "ledger.jsonl").is_file()
