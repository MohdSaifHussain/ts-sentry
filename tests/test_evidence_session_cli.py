# SPDX-License-Identifier: MIT
"""STEP-04 D6: the evidence session end to end, and the dataset-digest fix.

Two things are proved here that no unit test can prove.

The first is STEP-04's exit checklist item "all hops present as HUMAN_DECISION
events in ledger", which is a statement about a real ledger file rather than
about a function's return value, so it is asserted against the exported JSONL.

The second is the STEP-03 gap this phase owes. ``dataset_digest`` was the
SHA-256 of ``build.duckdb``, which is not byte-stable across rebuilds even
though the Parquet exports are, so session ids changed every time anyone
rebuilt. Two independent builds of the same seed are made here and their
digests compared, which is the only way to check a claim about rebuilds.

These build real datasets and are correspondingly slow. They are worth it: the
defect they cover was found by Saif re-running a build, not by any test that
existed at the time.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from ts_sentry.cli.main import EXIT_INPUT_ERROR, EXIT_OK, main
from ts_sentry.orchestrator.session_runner import derive_session_id
from ts_sentry.provenance import (
    BUILD_MANIFEST,
    DatasetDigestError,
    dataset_digest_from_manifest,
    sha256_file,
)


@pytest.fixture(scope="module")
def build_a(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("build-a")
    assert main(["build-dataset", "--seed", "42", "--scale", "1", "--out", str(out)]) == EXIT_OK
    return out


@pytest.fixture(scope="module")
def build_b(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A second, independent build of the same seed.

    The whole point of the fixture: a claim about rebuild stability cannot be
    checked against one build.
    """
    out = tmp_path_factory.mktemp("build-b")
    assert main(["build-dataset", "--seed", "42", "--scale", "1", "--out", str(out)]) == EXIT_OK
    return out


@pytest.fixture(scope="module")
def subject(build_a: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """The top-ranked subject from a real triage session.

    Taken from the queue rather than hard-coded, because that is how an analyst
    gets one and because a hard-coded id would silently stop being the top case
    the moment the scorer changed.
    """
    out = tmp_path_factory.mktemp("triage")
    assert (
        main(
            [
                "run-session",
                "--agent",
                "triage",
                "--seed-dataset",
                str(build_a),
                "--out",
                str(out),
            ]
        )
        == EXIT_OK
    )
    queue = json.loads((out / "ranked_queue.json").read_text(encoding="utf-8"))
    yield str(queue["queue"]["rows"][0]["subject_id"])


# --------------------------------------------------------------------------
# The carried STEP-03 gap
# --------------------------------------------------------------------------


def test_the_store_is_still_not_byte_stable_across_rebuilds(build_a: Path, build_b: Path) -> None:
    """The finding this fix exists for, asserted rather than assumed.

    If DuckDB ever made its store byte-stable this would fail, and the right
    response would be to rewrite the claim rather than delete the test: the
    reason for deriving identity from the Parquet hashes would have changed.
    """
    assert sha256_file(build_a / "build.duckdb") != sha256_file(build_b / "build.duckdb")


def test_the_dataset_digest_is_identical_across_rebuilds(build_a: Path, build_b: Path) -> None:
    """The gap closed. Same seed, same scale, two independent builds, one
    identity, because the digest now derives from the Parquet table hashes
    STEP-01 verified byte-stable."""
    assert dataset_digest_from_manifest(build_a) == dataset_digest_from_manifest(build_b)


def test_session_ids_match_across_rebuilds(build_a: Path, build_b: Path) -> None:
    """What the digest fix buys, at the level anyone actually notices it."""
    digest_a = dataset_digest_from_manifest(build_a)
    digest_b = dataset_digest_from_manifest(build_b)

    assert derive_session_id("saif", digest_a, "triage") == derive_session_id(
        "saif", digest_b, "triage"
    )


def test_two_different_sessions_do_not_share_an_id(build_a: Path) -> None:
    """Found by running the CLI, not by a test.

    The first evidence session came back carrying the same id as the triage
    session before it, because analyst plus dataset identified a session only
    while there was one kind of session. Ids appear in the OrchestratorToken and
    in every manifest, so an id that does not distinguish sessions makes an
    audit trail ambiguous exactly where it should be decisive.
    """
    digest = dataset_digest_from_manifest(build_a)

    triage = derive_session_id("saif", digest, "triage")
    evidence = derive_session_id("saif", digest, "evidence", "case-0000", "chan_000001")
    other_case = derive_session_id("saif", digest, "evidence", "case-0001", "chan_000002")

    assert len({triage, evidence, other_case}) == 3


def test_a_build_without_a_manifest_is_refused(build_a: Path, tmp_path: Path) -> None:
    """No fallback to hashing the store.

    A silent fallback would restore exactly the defect being closed, in the case
    where it is hardest to notice.
    """
    orphan = tmp_path / "no-manifest"
    orphan.mkdir()
    (orphan / "build.duckdb").write_bytes((build_a / "build.duckdb").read_bytes())

    with pytest.raises(DatasetDigestError, match=BUILD_MANIFEST):
        dataset_digest_from_manifest(orphan)

    assert (
        main(
            [
                "run-session",
                "--agent",
                "triage",
                "--seed-dataset",
                str(orphan),
                "--out",
                str(tmp_path / "out"),
            ]
        )
        == EXIT_INPUT_ERROR
    )


def test_a_changed_table_hash_changes_the_identity(build_a: Path, tmp_path: Path) -> None:
    """The property that makes the id worth having: it tracks content."""
    edited = tmp_path / "edited"
    edited.mkdir()
    manifest = json.loads((build_a / BUILD_MANIFEST).read_text(encoding="utf-8"))
    manifest["table_hashes"]["channel"] = "f" * 64
    (edited / BUILD_MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")

    assert dataset_digest_from_manifest(edited) != dataset_digest_from_manifest(build_a)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("[]", "does not contain a JSON object"),
        ("{}", "carries no table_hashes"),
        ('{"table_hashes": {}}', "carries no table_hashes"),
        ('{"table_hashes": {"channel": 7}}', "is not a string"),
        ('{"table_hashes": {"channel": "nope"}}', "SHA-256"),
        ("not json", "could not read"),
    ],
)
def test_a_malformed_manifest_is_refused_with_its_reason(
    tmp_path: Path, payload: str, message: str
) -> None:
    (tmp_path / BUILD_MANIFEST).write_text(payload, encoding="utf-8")

    with pytest.raises(DatasetDigestError, match=message):
        dataset_digest_from_manifest(tmp_path)


# --------------------------------------------------------------------------
# The evidence session
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def evidence_session(build_a: Path, subject: str, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("evidence")
    assert (
        main(
            [
                "run-session",
                "--agent",
                "evidence",
                "--seed-dataset",
                str(build_a),
                "--case",
                "case-0000",
                "--subject",
                subject,
                "--max-hops",
                "4",
                "--analyst-id",
                "saif",
                "--out",
                str(out),
            ]
        )
        == EXIT_OK
    )
    return out


def test_the_session_writes_every_artifact_it_claims(evidence_session: Path) -> None:
    manifest = json.loads((evidence_session / "session_manifest.json").read_text(encoding="utf-8"))

    for record in manifest["artifacts"]:
        path = evidence_session / record["path"]
        assert path.is_file(), f"manifest names {record['path']}, which does not exist"
        assert sha256_file(path) == record["sha256"], (
            f"{record['name']} does not match the digest the manifest recorded"
        )


def test_every_hop_is_a_human_decision_in_the_exported_ledger(evidence_session: Path) -> None:
    """STEP-04's exit checklist, asserted against the artifact rather than the
    return value, because the artifact is what an auditor reads."""
    lines = (evidence_session / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines if line]

    decisions = [entry for entry in entries if entry["event_type"] == "human_decision"]
    tool_calls = [entry for entry in entries if entry["event_type"] == "tool_called"]

    assert decisions, "an evidence session with no ledgered human decision"
    assert len(decisions) >= len(tool_calls)
    assert decisions[0]["seq"] < tool_calls[0]["seq"]


def test_the_ledgered_decisions_say_what_decided_them(evidence_session: Path) -> None:
    """Saif's requirement, checked on the session artifact.

    The payload bodies live in ``session_events.json`` because the chain stores
    only digests. Each one carries ``reviewer_kind``, and the digest in the
    chain covers it, which the unit suite proves separately by forging the field
    and recomputing.
    """
    events = json.loads((evidence_session / "session_events.json").read_text(encoding="utf-8"))
    decisions = [event for event in events["events"] if event["event_type"] == "human_decision"]

    assert decisions
    for decision in decisions:
        assert decision["payload"]["reviewer_kind"] == "scripted"
        assert decision["payload"]["by_human"] is False


def test_no_artifact_renders_a_scripted_approval_as_a_human_one(
    evidence_session: Path,
) -> None:
    """Swept over every file the session wrote, rather than the ones we thought
    to check."""
    for path in sorted(evidence_session.iterdir()):
        if path.suffix not in {".json", ".jsonl", ".graphml"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert "human analyst" not in text, f"{path.name} attributes a scripted decision to a human"


def test_the_pack_and_the_graph_describe_the_same_investigation(
    evidence_session: Path,
) -> None:
    pack = json.loads((evidence_session / "evidence_pack.json").read_text(encoding="utf-8"))
    graph = (evidence_session / "evidence_graph.graphml").read_text(encoding="utf-8")

    assert pack["counts"]["nodes"] >= 1
    for node in pack["nodes"]:
        assert f'id="{node["node_id"]}"' in graph


def test_the_evidence_session_requires_a_subject(build_a: Path, tmp_path: Path) -> None:
    """A case id alone does not name an entity to investigate."""
    assert (
        main(
            [
                "run-session",
                "--agent",
                "evidence",
                "--seed-dataset",
                str(build_a),
                "--case",
                "case-0000",
                "--out",
                str(tmp_path / "out"),
            ]
        )
        == EXIT_INPUT_ERROR
    )
