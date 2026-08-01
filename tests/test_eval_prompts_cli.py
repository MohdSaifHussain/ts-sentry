# SPDX-License-Identifier: MIT
"""D5 and D6: the ``eval-prompts`` verb and the report artifact.

The exit-code tests carry the STEP-02 discipline forward. ``eval-prompts`` is
the first verb in this CLI with *three* meanings competing for codes (activated,
refused, bad input), so the assertions here are about keeping them apart rather
than about any one of them being right.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from ts_sentry.agents.prompt_eval.prompts import CLASSIFY_SYSTEM_TEXT
from ts_sentry.cli.main import (
    EXIT_INPUT_ERROR,
    EXIT_OK,
    EXIT_QUALITY_GATE_FAIL,
    EXIT_REGRESSION_REFUSED,
    main,
)
from ts_sentry.data.tz import IST
from ts_sentry.orchestrator.eval_report import (
    PRECISION_CAVEAT,
    REPORT_JSON,
    REPORT_MD,
    RESOLUTION_CAVEAT,
)
from ts_sentry.prompt_registry.registry import PromptTask
from ts_sentry.prompt_registry.store import load_registry, write_registry

REPO = Path(__file__).resolve().parent.parent
EVAL_ROOT = REPO / "evals" / "threat_class"
REGISTRY = REPO / "prompts"

DEGRADED = CLASSIFY_SYSTEM_TEXT.replace(
    "- If no coordinated abuse signal is present, the answer is benign. Benign is a \
real answer, not a failure to decide.",
    "- When in doubt, answer benign. Prefer benign unless the case is overwhelming.",
)
NEUTRAL = CLASSIFY_SYSTEM_TEXT + "\n- Be concise."


@pytest.fixture
def registry_with(tmp_path: Path) -> Path:
    """A copy of the committed registry, so no test writes to ``prompts/``."""
    root = tmp_path / "registry"
    root.mkdir()
    for path in REGISTRY.iterdir():
        (root / path.name).write_bytes(path.read_bytes())
    return root


def _register(root: Path, text: str, version: str) -> str:
    registry = load_registry(root)
    parent = registry.active(PromptTask.CLASSIFY_THREAT_CLASS)
    grown = registry.registered(
        PromptTask.CLASSIFY_THREAT_CLASS,
        version,
        text,
        parent=parent.content_digest,
        created_ist=datetime(2026, 8, 1, 14, 0, tzinfo=IST),
    )
    write_registry(root, grown)
    return next(
        record.content_digest
        for record in grown.versions_for(PromptTask.CLASSIFY_THREAT_CLASS)
        if record.version == version
    )


def _run(registry: Path, candidate: str, out: Path) -> int:
    return main(
        [
            "eval-prompts",
            "--candidate",
            candidate,
            "--registry",
            str(registry),
            "--evals",
            str(EVAL_ROOT),
            "--out",
            str(out),
            "--analyst-id",
            "saif",
        ]
    )


# --------------------------------------------------------------------------
# D5: the exit-code contract
# --------------------------------------------------------------------------


def test_a_degraded_candidate_exits_seven(registry_with: Path, tmp_path: Path) -> None:
    """The phase's exit criterion, through the verb an analyst actually runs."""
    candidate = _register(registry_with, DEGRADED, "v2")

    code = _run(registry_with, candidate, tmp_path / "run")

    assert code == EXIT_REGRESSION_REFUSED


def test_a_neutral_candidate_exits_zero(registry_with: Path, tmp_path: Path) -> None:
    """The control. A verb that only ever refused would satisfy the test above
    while being useless."""
    candidate = _register(registry_with, NEUTRAL, "v2")

    code = _run(registry_with, candidate, tmp_path / "run")

    assert code == EXIT_OK


def test_a_regression_refusal_is_distinguishable_from_bad_input(
    registry_with: Path, tmp_path: Path
) -> None:
    """The whole reason D5's literal "exit 5" was deviated from.

    ``EXIT_INPUT_ERROR`` is 5 throughout this CLI. If a regression refusal also
    exited 5, a script could not tell a gate that refused a worse prompt from a
    caller that mistyped a digest, and DECISIONS 2.12 exists because exactly
    that collision was found and removed once already.
    """
    refused = _run(registry_with, _register(registry_with, DEGRADED, "v2"), tmp_path / "a")
    unknown = _run(registry_with, "f" * 64, tmp_path / "b")

    assert refused == EXIT_REGRESSION_REFUSED
    assert unknown == EXIT_INPUT_ERROR
    assert refused != unknown


@pytest.mark.parametrize(
    "argv",
    [
        ["eval-prompts"],
        ["eval-prompts", "--candidate"],
        ["eval-prompts", "--candidate", "abc", "--not-a-flag"],
    ],
)
def test_usage_errors_exit_five_not_two(argv: list[str]) -> None:
    """Argparse exits 2, and 2 is ``EXIT_QUALITY_GATE_FAIL`` in this CLI.

    ``eval-prompts`` joins ``TRANSLATES_USAGE_ERRORS`` from the start, so it
    never acquires the defect ``run-session`` had to have fixed retrospectively.
    """
    code = main(argv)

    assert code == EXIT_INPUT_ERROR
    assert code != EXIT_QUALITY_GATE_FAIL


def test_a_candidate_that_is_already_the_incumbent_is_refused_as_input(
    registry_with: Path, tmp_path: Path
) -> None:
    """Evaluating a version against itself measures the harness, not the prompt."""
    incumbent = load_registry(registry_with).active(PromptTask.CLASSIFY_THREAT_CLASS)

    code = _run(registry_with, incumbent.content_digest, tmp_path / "run")

    assert code == EXIT_INPUT_ERROR


def test_an_existing_output_directory_is_refused(registry_with: Path, tmp_path: Path) -> None:
    """A session writes its own directory. Overwriting one would destroy the
    audit trail it holds, which is the STEP-04 seed-guard reasoning applied to
    the output path rather than the input."""
    candidate = _register(registry_with, NEUTRAL, "v2")
    out = tmp_path / "run"
    out.mkdir()
    (out / "something.txt").write_text("prior artifact", encoding="utf-8")

    assert _run(registry_with, candidate, out) == EXIT_INPUT_ERROR


# --------------------------------------------------------------------------
# D6: the report artifact
# --------------------------------------------------------------------------


def test_the_report_is_written_in_both_formats(registry_with: Path, tmp_path: Path) -> None:
    candidate = _register(registry_with, DEGRADED, "v2")
    out = tmp_path / "run"

    _run(registry_with, candidate, out)

    assert (out / REPORT_MD).is_file()
    assert (out / REPORT_JSON).is_file()
    assert (out / "ledger.jsonl").is_file()
    assert (out / "session_manifest.json").is_file()


def test_the_report_carries_every_stamp_needed_to_rerun_it(
    registry_with: Path, tmp_path: Path
) -> None:
    """D6's governing standard is reproducible evaluation practice.

    An evaluation whose inputs cannot be named is one nobody can re-run, so each
    of these is asserted rather than assumed present.
    """
    candidate = _register(registry_with, DEGRADED, "v2")
    out = tmp_path / "run"
    _run(registry_with, candidate, out)

    payload = json.loads((out / REPORT_JSON).read_text(encoding="utf-8"))
    stamp = payload["stamp"]
    report = payload["report"]

    assert stamp["dataset_seed"] == 42
    assert stamp["dataset_scale"] == 1
    assert stamp["git_sha"]
    assert stamp["tolerances_sha256"]
    assert report["items_sha256"]
    assert report["labels_sha256"]
    assert report["adapter_id"]
    assert report["model_id"]
    assert report["bootstrap_seed"] == 42


def test_the_caveats_travel_in_the_artifact(registry_with: Path, tmp_path: Path) -> None:
    """Both caveats are in the report, not only in the docs.

    DECISIONS 4.9's reasoning for the recovery ceiling: a number reported
    without the bound that makes it readable invites the reader to draw a
    conclusion it does not support, and a reader holding the artifact does not
    have ``docs/`` open beside it.
    """
    candidate = _register(registry_with, DEGRADED, "v2")
    out = tmp_path / "run"
    _run(registry_with, candidate, out)

    markdown = (out / REPORT_MD).read_text(encoding="utf-8")
    payload = json.loads((out / REPORT_JSON).read_text(encoding="utf-8"))

    assert PRECISION_CAVEAT in markdown
    assert RESOLUTION_CAVEAT in markdown
    assert payload["caveats"]["precision"] == PRECISION_CAVEAT
    assert payload["caveats"]["resolution"] == RESOLUTION_CAVEAT


def test_the_report_carries_no_per_item_rows(registry_with: Path, tmp_path: Path) -> None:
    """The report is STEP-06 3.2's boundary artifact.

    It is what leaves the eval boundary and therefore what a prompt author may
    read. A per-item table here would hand back the answer key the rest of the
    phase is built to withhold.
    """
    candidate = _register(registry_with, DEGRADED, "v2")
    out = tmp_path / "run"
    _run(registry_with, candidate, out)

    raw = (out / REPORT_JSON).read_text(encoding="utf-8")
    markdown = (out / REPORT_MD).read_text(encoding="utf-8")

    assert "item-00" not in raw
    assert "item-00" not in markdown


def test_a_refused_report_names_the_breaches_and_the_resolution(
    registry_with: Path, tmp_path: Path
) -> None:
    """ "Why did the gate refuse this" must be answerable from the artifact alone."""
    candidate = _register(registry_with, DEGRADED, "v2")
    out = tmp_path / "run"
    _run(registry_with, candidate, out)

    payload = json.loads((out / REPORT_JSON).read_text(encoding="utf-8"))

    assert payload["verdict"]["decision"] == "refused"
    assert payload["verdict"]["breaches"]
    assert payload["resolution"], "the report must say what this eval set could resolve"


def test_the_session_manifest_anchors_the_chain(registry_with: Path, tmp_path: Path) -> None:
    """The anchor STEP-03 landed, on an eval session's own chain."""
    candidate = _register(registry_with, DEGRADED, "v2")
    out = tmp_path / "run"
    _run(registry_with, candidate, out)

    manifest = json.loads((out / "session_manifest.json").read_text(encoding="utf-8"))

    assert manifest["expected_head"]["count"] > 0
    assert len(manifest["expected_head"]["entry_hash"]) == 64
    assert "prompt_eval" in manifest["mandate_hashes"]


def test_the_tolerances_are_bound_into_session_open(registry_with: Path, tmp_path: Path) -> None:
    """STEP-06 3.3's "ledgered corpus-style events", via DECISIONS 5.8.

    A tolerance set is build-time policy declared when no session is open, so it
    binds at ``SESSION_OPEN`` rather than becoming a twelfth ``EventType``. The
    guarantee that matters is that the verdict is permanently tied to the limits
    it was reached under, hash-chained.
    """
    candidate = _register(registry_with, DEGRADED, "v2")
    out = tmp_path / "run"
    _run(registry_with, candidate, out)

    payload = json.loads((out / REPORT_JSON).read_text(encoding="utf-8"))
    entries = [
        json.loads(line)
        for line in (out / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert entries[0]["event_type"] == "session_open"
    # The payload body is not in the chain (only its digest is), so the binding
    # is asserted through the report's copy of the same value.
    assert payload["stamp"]["tolerances_sha256"]
    assert payload["verdict"]["tolerances_sha256"] == payload["stamp"]["tolerances_sha256"]
