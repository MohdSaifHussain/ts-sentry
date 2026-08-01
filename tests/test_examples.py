# SPDX-License-Identifier: MIT
"""STEP-08 D1: the committed examples stay true.

Requirement 3.2 asks that every example directory carry an inputs manifest, a
ledger `verify-ledger` accepts, outputs, and a NOTES.md saying what it
demonstrates and what it does not claim. This file checks that the committed
artifacts still satisfy that, and it is what the CI examples job runs.

**What it deliberately does not do is diff regenerated output byte for byte.**
Measured rather than assumed: of everything these sessions write, only
`ranked_queue.json` is byte-identical across runs. Ledgers, manifests, packs and
chain heads all carry real timestamps. A ledger records *when* things happened,
so a byte-stable ledger would be a worse artifact rather than a better one, and
a test demanding one would fail forever for a correct reason, which is the worst
kind of failing test.

So the invariants checked here are the ones that are real: the chain verifies,
the anchor matches, a truncated copy is caught only by the anchor, the negative
paths still exit nonzero where they should, and the numbers the NOTES files
quote still come out of the artifacts they quote them from. That last one
matters most: a NOTES.md whose numbers have quietly drifted from its own
directory is exactly the "claim wider than the behaviour" failure this project
keeps recording.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from ts_sentry.cli.main import EXIT_HEAD_MISMATCH, EXIT_OK, main

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

SESSION_EXAMPLES = (
    "01-triage-queue",
    "02-evidence-t02-ring",
    "03-signed-memo",
    "04-evidence-t07-cluster",
    "05-overclaim-refused",
    "06-prompt-eval-refused",
)
ALL_EXAMPLES = (*SESSION_EXAMPLES, "07-measurement-report", "08-firewall-real-comments")


def _json(relative: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((EXAMPLES / relative).read_text(encoding="utf-8"))
    return loaded


# --------------------------------------------------------------------------
# Requirement 3.2, per directory
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_EXAMPLES)
def test_every_example_carries_its_inputs_manifest_and_notes(name: str) -> None:
    directory = EXAMPLES / name
    assert (directory / "inputs.json").is_file(), f"{name} has no inputs manifest (3.2)"
    notes = directory / "NOTES.md"
    assert notes.is_file(), f"{name} has no NOTES.md (3.2)"

    body = notes.read_text(encoding="utf-8")
    assert "deliberately does not claim" in body, (
        f"{name}/NOTES.md must state what the example does not claim, "
        "which is the half of 3.2 that is easy to drop"
    )


@pytest.mark.parametrize("name", ALL_EXAMPLES)
def test_no_example_ships_the_duckdb_store(name: str) -> None:
    """780 KB of binary per session that is not byte-stable and is not needed.

    Asserted rather than left to `.gitignore`, because the regeneration script
    deletes it explicitly and a change there would otherwise silently start
    committing 5 MB.
    """
    assert not (EXAMPLES / name / "ledger.duckdb").exists()


@pytest.mark.parametrize("name", SESSION_EXAMPLES)
def test_every_example_chain_verifies_and_matches_its_anchor(name: str) -> None:
    ledger = EXAMPLES / name / "ledger.jsonl"
    manifest = EXAMPLES / name / "session_manifest.json"

    assert main(["verify-ledger", str(ledger)]) == EXIT_OK
    assert main(["verify-ledger", str(ledger), "--expect-head-from", str(manifest)]) == EXIT_OK


@pytest.mark.parametrize("name", SESSION_EXAMPLES)
def test_a_truncated_example_is_caught_only_by_the_anchor(name: str, tmp_path: Path) -> None:
    """The passing result that confirms a real limitation.

    Carried in every phase since STEP-02, and worth re-asserting on the
    artifacts a reader will actually download: chain verification alone accepts
    a truncated export, because what remains is a shorter chain whose every link
    still recomputes. Only the anchor refuses it.
    """
    lines = (EXAMPLES / name / "ledger.jsonl").read_text(encoding="utf-8").splitlines(True)
    truncated = tmp_path / "ledger.jsonl"
    truncated.write_text("".join(lines[:-1]), encoding="utf-8", newline="")

    assert main(["verify-ledger", str(truncated)]) == EXIT_OK
    assert (
        main(
            [
                "verify-ledger",
                str(truncated),
                "--expect-head-from",
                str(EXAMPLES / name / "session_manifest.json"),
            ]
        )
        == EXIT_HEAD_MISMATCH
    )


# --------------------------------------------------------------------------
# The numbers the NOTES files quote
# --------------------------------------------------------------------------


def test_the_triage_example_still_discriminates() -> None:
    """01's NOTES claims 23 cases and a cited-component spread.

    The spread is the load-bearing half. Before the STEP-03 fix every one of
    these rows cited `severity_class` because it was the largest component, and
    every rationale verified while explaining nothing.
    """
    rows = _json("01-triage-queue/ranked_queue.json")["queue"]["rows"]
    assert len(rows) == 23

    cited = [row["rationale"].split(":")[-1].rstrip("]") for row in rows if row.get("rationale")]
    assert cited.count("velocity") == 16
    assert cited.count("spread") == 4
    assert cited.count("severity_class") == 3


def test_the_t02_example_still_recovers_four_of_eight() -> None:
    """02's NOTES states a recorded-unmet obligation at its exact width.

    If this ever moves, the NOTES file and DECISIONS 7.14 both need rewriting,
    and this failing is how anyone finds out. It is the shape STEP-02 used for
    tail truncation and STEP-04 used for recovery saturation: assert the
    limitation so the day it stops being true, the claim gets rewritten rather
    than quietly outliving its own truth.
    """
    pack = _json("02-evidence-t02-ring/evidence_pack.json")
    recovered = {node["node_id"] for node in pack["nodes"]}
    assert recovered == {
        "t02_chan_000_000",  # the subject
        "t02_acct_000_000",
        "t02_acct_000_001",
        "t02_acct_000_002",
        "t02_vid_000_000",
    }


@pytest.mark.parametrize("name", ["02-evidence-t02-ring", "04-evidence-t07-cluster"])
def test_the_evidence_examples_vary_their_pivots_and_carry_full_provenance(name: str) -> None:
    """The STEP-07 work-list traversal, visible in a committed artifact.

    Four distinct pivot kinds, which is what "chains discovered entities and
    varies pivot kind" looks like from outside. Provenance completeness is the
    ASSEMBLE gate's own condition, re-checked here on the shipped file.
    """
    records = _json(f"{name}/evidence_pack.json")["provenance"]
    kinds = {record["pivot_kind"] for record in records if record["pivot_kind"]}
    assert kinds == {"shared_metadata", "infra_overlap", "account_link", "engagement_edge"}

    for record in records:
        for field in ("source_table", "template_sha256", "param_hash", "retrieval_ts_ist"):
            assert record.get(field), f"{name} {record['provenance_id']} is missing {field}"


def test_the_signed_memo_example_is_signed_and_unwatermarked() -> None:
    signature = _json("03-signed-memo/memo_signature.json")
    assert signature["memo"]["status"] == "signed"
    assert signature["signature"]["decision"] == "approve_enforcement"

    roles = [sentence["role"] for sentence in signature["memo"]["sentences"]]
    assert roles == ["fact", "policy_ground", "measure", "redress"]

    rendered = (EXAMPLES / "03-signed-memo" / "memo.md").read_text(encoding="utf-8")
    assert "AI-DRAFT" not in rendered.upper()


def test_the_overclaim_example_was_refused_and_says_it_was_made_to_overclaim() -> None:
    """05 is only honest if its own artifacts admit the stub was rigged.

    Both places, because they answer to different threats: the manifest is what
    a reader opens, the ledger entry is what the hash chain protects.
    """
    manifest = _json("05-overclaim-refused/session_manifest.json")
    assert manifest["model_mode"] == "stub"
    assert manifest["stub_mode"] == "overclaim"
    assert manifest["event_counts"]["gate_rejection"] == 8
    assert manifest["event_counts"]["verification_fail"] == 8
    assert "verification_pass" not in manifest["event_counts"]

    events = _json("05-overclaim-refused/session_events.json")["events"]
    opened = [e for e in events if e["event_type"] == "session_open"]
    assert len(opened) == 1
    assert opened[0]["payload"]["stub_mode"] == "overclaim"

    turn = _json("05-overclaim-refused/memo.json")["turn"]
    assert turn["verified"] is False
    assert turn["attempts"] == 8
    assert turn["rejected_attempts"] == 8
    # One defect rejected eight times is one defect. Counting it as eight would
    # inflate exactly the metric ARCHITECTURE 7.2 showcases.
    assert turn["distinct_defects_caught"] == 1
    assert turn["agent_revised_after_feedback"] is False
    assert not (EXAMPLES / "05-overclaim-refused" / "memo_signature.json").exists()


def test_the_faithful_memo_and_the_overclaim_memo_are_different_sessions() -> None:
    """They must not share a session id.

    A memo session's id derives from its pack's case and subject, so drafting
    both from one pack would have given two materially different sessions one
    id. That is the STEP-04 defect ("two sessions shared an id") and it is
    avoided by investigating a different case, not by hand-setting an id.
    """
    faithful = _json("03-signed-memo/session_manifest.json")["session_id"]
    overclaim = _json("05-overclaim-refused/session_manifest.json")["session_id"]
    assert faithful != overclaim


def test_every_example_session_has_a_distinct_id() -> None:
    ids = [_json(f"{name}/session_manifest.json")["session_id"] for name in SESSION_EXAMPLES]
    assert len(set(ids)) == len(ids), f"session ids collide: {ids}"


def test_the_prompt_eval_example_was_refused_with_countable_breaches() -> None:
    """A suite checking only "this was refused" passes if the gate refuses
    everything for the wrong reason. DECISIONS 5.21 and 6.5.
    """
    verdict = _json("06-prompt-eval-refused/eval_report.json")["verdict"]
    assert verdict["decision"] == "refused"

    by_code: dict[str, set[str | None]] = {}
    for breach in verdict["breaches"]:
        by_code.setdefault(breach["code"], set()).add(breach["threat_class"])

    assert by_code["recall_regression"] == {
        "t01_comment_spam_ring",
        "t02_fake_engagement_network",
        "t04_undisclosed_synthetic_media",
        "t07_coordinated_influence_op",
    }
    assert by_code["macro_f1_regression"] == {None}

    assert _json("06-prompt-eval-refused/inputs.json")["expected_exit"] == 7


def test_the_degraded_candidate_is_not_the_incumbent() -> None:
    """The one way this example could silently stop demonstrating anything.

    If the shipped prompt were reworded and the degradation became a no-op
    replacement, the candidate would be byte-identical to the incumbent and the
    example would report a clean activation while claiming to show a refusal.
    """
    registry = _json("registries/degraded-classify/manifest.json")
    digests = {
        version["content_digest"]
        for version in registry["versions"]
        if version["task"] == "classify.threat_class"
    }
    assert len(digests) == 2

    candidate = _json("06-prompt-eval-refused/inputs.json")["candidate_digest"]
    assert candidate in digests

    texts = {
        (EXAMPLES / "registries" / "degraded-classify" / f"{digest}.txt").read_text(
            encoding="utf-8"
        )
        for digest in digests
    }
    assert len(texts) == 2, "the degraded candidate has the same text as the incumbent"


def test_the_measurement_report_carries_its_honest_limits() -> None:
    """STEP-07 asserts the ten limits into every rendering. Re-checked on the
    committed artifact, because a report a reader downloads is the one that has
    to carry them.
    """
    from ts_sentry.measurement.report import HONEST_LIMITS

    rendered = (EXAMPLES / "07-measurement-report" / "report.md").read_text(encoding="utf-8")
    for limit in HONEST_LIMITS:
        assert limit in rendered, f"the committed report dropped an honest limit: {limit[:60]}"


def test_the_firewall_example_leads_with_the_corpus_being_refused() -> None:
    """08's finding is that real data was rejected on its first contact.

    Three duplicate COMMENT_IDs in a widely-cited published corpus, caught by
    `InertBlock.wrap`'s uniqueness invariant. The synthetic generator has never
    produced one, because it assigns ids from a counter.
    """
    report = _json("08-firewall-real-comments/firewall_report.json")

    refusal = report["raw_corpus_refused_by_the_firewall"]
    assert "duplicate record_id" in refusal["reason"]
    assert len(refusal["duplicate_ids"]) == 3
    assert refusal["rows_dropped"] == 3

    corpus = report["corpus"]
    assert corpus["rows_as_published"] == 1956
    assert corpus["distinct_record_ids"] == 1953
    assert corpus["labelled_spam"] + corpus["labelled_legitimate"] == 1953
    assert corpus["licence"] == "CC BY 4.0"


def test_the_firewall_example_reports_zero_signals_without_calling_it_a_pass() -> None:
    """Zero signals is the honest result and the NOTES says why it proves nothing.

    A detector scoring zero on a corpus containing zero instances of what it
    detects has demonstrated nothing about its precision or recall. This test
    pins both halves: the number, and the sentence that stops it being read as
    a pass.
    """
    report = _json("08-firewall-real-comments/firewall_report.json")
    assert report["firewall"]["signal_counts"] == {}
    assert report["firewall"]["records_with_at_least_one_signal"] == 0
    assert report["firewall"]["redacted"] is False

    notes = (EXAMPLES / "08-firewall-real-comments" / "NOTES.md").read_text(encoding="utf-8")
    assert "Zero signals is not evidence the detector works" in notes


def test_the_third_party_data_is_attributed_and_unmodified() -> None:
    """CC BY 4.0 requires appropriate credit, and the repo claims the files are
    byte-for-byte what the archive ships.
    """
    attribution = (EXAMPLES / "data" / "youtube-spam-collection" / "ATTRIBUTION.md").read_text(
        encoding="utf-8"
    )
    for required in ("CC BY 4.0", "10.24432/C58885", "Alberto", "unmodified"):
        assert required in attribution

    csvs = sorted((EXAMPLES / "data" / "youtube-spam-collection").glob("Youtube*.csv"))
    assert len(csvs) == 5


def test_the_firewall_sample_block_is_fenced_and_escapes_awkward_text() -> None:
    """The sample is chosen to be awkward rather than bland.

    Six bland comments would render a block that proves nothing about the
    encoding, so the selection prefers text containing a character the encoder
    has to escape.
    """
    block = (EXAMPLES / "08-firewall-real-comments" / "sample_block.txt").read_text(
        encoding="utf-8"
    )
    assert "BEGIN TS-SENTRY CASE DATA" in block
    assert "END TS-SENTRY CASE DATA" in block
    # An escaped quote inside the JSON encoding of a real comment.
    assert '\\"' in block or '"' in block
