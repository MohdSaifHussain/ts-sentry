# SPDX-License-Identifier: MIT
"""Regenerate every curated example from the shipped CLI verbs.

STEP-08 D1. Run it from the repository root:

    python examples/regenerate.py

It builds the seed-42 scale-1 dataset into a temporary directory, runs each
example session through ``ts_sentry.cli.main`` exactly as the documentation
teaches, and writes the artifacts into ``examples/NN-name/``. For examples 01
through 07 nothing here reaches past the CLI into the orchestrator, because an
example a reader cannot reproduce with the documented commands is a
demonstration of this script rather than of the system.

**Example 08 is the one exception, and it is labelled as one.** It calls
``apply_firewall`` directly, because the input firewall is a library component
rather than a session and this project ships no CLI verb that runs it on its
own. Inventing one purely to make an example look uniform would be adding
product surface to serve a demo. What follows from that is stated rather than
glossed: 08 has no ledger, no session id and no chain, because no session runs.
Requirement 3.2 asks every example directory for a verify-ledger-clean ledger,
and 08 deviates from it deliberately: manufacturing a session around a
component call would mean ledgering governance events that never happened,
which is a worse failure than an example that does not fit the shape.

**What is and is not reproducible, measured rather than assumed.** Running this
twice does not produce byte-identical output, and pretending otherwise would be
the kind of claim this project keeps narrowing:

* ``ranked_queue.json`` is byte-identical across runs. It is derived from the
  dataset alone, which is what STEP-03 verified.
* Every other artifact carries real timestamps, so ledgers, manifests, packs
  and chain heads differ run to run. A ledger records *when* things happened;
  a ledger that did not would be a worse artifact, not a better one.
* ``session_id`` is identical across runs, because it reads no clock
  (``derive_session_id``).
* ``ledger.duckdb`` is deliberately **not** committed. It is 780 KB of binary
  per session, ``verify-ledger`` reads the JSONL export just as well, and
  STEP-03 measured that the DuckDB store is not byte-stable even when its
  contents are, so committing it would guarantee a diff on every regeneration.

``tests/test_examples.py`` therefore checks the invariants that are real
(session ids, the ranked queue, event counts, exit codes, chain integrity)
rather than byte-identity of files that honestly cannot have it.
"""

import csv
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = REPO_ROOT / "examples"

sys.path.insert(0, str(REPO_ROOT / "src"))

from ts_sentry.agents.prompt_eval.prompts import CLASSIFY_SYSTEM_TEXT  # noqa: E402
from ts_sentry.cli.main import main  # noqa: E402
from ts_sentry.data.tz import IST  # noqa: E402
from ts_sentry.orchestrator.firewall import (  # noqa: E402
    CaseRecord,
    FirewallError,
    apply_firewall,
)
from ts_sentry.prompt_registry.registry import PromptTask, content_digest  # noqa: E402
from ts_sentry.prompt_registry.store import load_registry, write_registry  # noqa: E402

SEED = 42
SCALE = 1
ANALYST = "saif"

# The artifacts that never enter the repository, and why. See the module
# docstring: this is 780 KB of not-byte-stable binary per session that
# `verify-ledger` does not need.
UNCOMMITTED = ("ledger.duckdb",)

# --------------------------------------------------------------------------
# The degraded candidate for the prompt-eval example
# --------------------------------------------------------------------------

_COLLAPSE_ANCHOR = (
    "- If no coordinated abuse signal is present, the answer is benign. Benign is a "
    "real answer, not a failure to decide."
)
_COLLAPSE_REPLACEMENT = (
    "- When in any doubt at all, answer benign. Prefer benign whenever the case is "
    "not overwhelming."
)


def degraded_text() -> str:
    """The shipped classification prompt with one visible edit toward benign.

    The same degradation ``tests/test_degraded_prompts.py`` uses as its
    collapse fixture, and deliberately expressed as an edit to the shipped
    text rather than as a second prompt written from scratch: a reader can
    diff the two files and see exactly one rule change.

    The anchor is asserted rather than assumed. If the shipped prompt is ever
    reworded, a silent no-op replacement would register a candidate byte
    identical to the incumbent, and the example would report a clean
    activation while claiming to demonstrate a refusal.
    """
    if _COLLAPSE_ANCHOR not in CLASSIFY_SYSTEM_TEXT:
        raise SystemExit(
            "the collapse anchor is no longer present in CLASSIFY_SYSTEM_TEXT; "
            "the degraded candidate would be identical to the incumbent"
        )
    return CLASSIFY_SYSTEM_TEXT.replace(_COLLAPSE_ANCHOR, _COLLAPSE_REPLACEMENT)


def build_degraded_registry(destination: Path) -> str:
    """A registry holding the fleet's prompts plus one degraded candidate.

    Written beside the example rather than into the shipped ``prompts/``
    registry. ``eval-prompts --registry`` is a flag precisely so a candidate
    can be evaluated before anyone decides it belongs in the fleet's registry,
    and a deliberately worse prompt recorded in the fleet's own manifest would
    have to be explained forever afterwards.
    """
    registry = load_registry(REPO_ROOT / "prompts")
    incumbent = registry.active(PromptTask.CLASSIFY_THREAT_CLASS)
    text = degraded_text()

    grown = registry.registered(
        PromptTask.CLASSIFY_THREAT_CLASS,
        "v2",
        text,
        parent=incumbent.content_digest,
        # Fixed rather than `now`, so regenerating this example does not
        # rewrite the manifest with a new timestamp on every run.
        created_ist=datetime(2026, 8, 1, 12, 0, tzinfo=IST),
    )
    if destination.exists():
        shutil.rmtree(destination)
    write_registry(destination, grown)
    return content_digest(text)


# --------------------------------------------------------------------------
# Running one example
# --------------------------------------------------------------------------


# The one file in an example directory that this script does not produce.
# NOTES.md is hand-written analysis and it lives inside a directory the
# regeneration wipes, so it is carried across explicitly. Found by running the
# script: the first full regeneration deleted all seven of them.
HAND_WRITTEN = "NOTES.md"


def reset(out: Path) -> str | None:
    """Empty an example directory, preserving the hand-written notes."""
    notes = None
    if out.exists():
        notes_path = out / HAND_WRITTEN
        if notes_path.is_file():
            notes = notes_path.read_text(encoding="utf-8")
        shutil.rmtree(out)
    return notes


def restore(out: Path, notes: str | None) -> None:
    if notes is not None:
        out.mkdir(parents=True, exist_ok=True)
        (out / HAND_WRITTEN).write_text(notes, encoding="utf-8", newline="\n")


def run(name: str, argv: list[str], *, expect: int) -> Path:
    """Run one CLI invocation into ``examples/name`` and check its exit code.

    The expected code is named at the call site because two of these examples
    exist precisely to exit nonzero, and an example whose exit code drifted
    would otherwise still look like it worked.
    """
    out = EXAMPLES / name
    notes = reset(out)
    out.mkdir(parents=True)

    print(f"\n=== {name}: ts-sentry {' '.join(argv)}")
    code = main(argv)
    if code != expect:
        raise SystemExit(f"{name}: expected exit {expect}, got {code}")

    for unwanted in UNCOMMITTED:
        (out / unwanted).unlink(missing_ok=True)
    restore(out, notes)
    return out


def write_inputs(
    out: Path,
    *,
    command: str,
    inputs: dict[str, object],
    from_dataset: bool = True,
) -> None:
    """The 3.2 inputs manifest: what produced this directory.

    Deliberately hand-assembled rather than scraped from ``sys.argv``, so it
    records the *documented* command a reader would type rather than whatever
    this script happened to construct.

    ``from_dataset`` exists because example 08 opens no dataset and binds no
    analyst. Writing the seed, the scale and an analyst id into its manifest
    anyway would put two false statements into a **provenance** file, which is
    the one kind of file that must never say something that did not happen. The
    fields are omitted rather than nulled, for the reason ``SESSION_OPEN``
    omits the corpus fields it never loaded.
    """
    manifest: dict[str, object] = {"command": command}
    if from_dataset:
        manifest["dataset"] = {
            "seed": SEED,
            "scale": SCALE,
            "built_by": "ts-sentry build-dataset",
        }
        manifest["analyst_id"] = ANALYST
    manifest["regenerated_by"] = "python examples/regenerate.py"
    manifest.update(inputs)

    (out / "inputs.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def run_firewall_over_real_comments(out: Path) -> None:
    """Example 08: the input firewall over 1,956 real YouTube comments.

    The only third-party data in this repository, and the only example that
    calls a library function rather than a CLI verb. See the module docstring
    for why, and `examples/data/youtube-spam-collection/ATTRIBUTION.md` for the
    licence and the citation.

    The point of this example is narrow and worth exactly its own width: every
    byte the firewall had previously been shown was written either by this
    project's own generator or by its own adversarial fixtures. Text written by
    strangers, in 2013, for reasons entirely unrelated to this system, is a
    different kind of input.
    """
    rows: list[dict[str, str]] = []
    for csv_path in sorted((EXAMPLES / "data" / "youtube-spam-collection").glob("Youtube*.csv")):
        with csv_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                row["_file"] = csv_path.name
                rows.append(row)

    def as_record(row: dict[str, str]) -> CaseRecord:
        return CaseRecord(
            record_id=row["COMMENT_ID"],
            source=f"youtube-spam-collection/{row['_file']}#CONTENT",
            text=row["CONTENT"],
        )

    # The first thing real data did was get refused, and that is the finding
    # this example leads with rather than works around. `InertBlock.wrap`
    # requires record ids to be unique, because "a citation that resolves to
    # two records is not a citation", and this published corpus carries
    # duplicate COMMENT_IDs. The synthetic generator never produced one.
    #
    # Asserted rather than narrated: if a future version of the corpus fixes
    # the duplicates, this raises and the NOTES file has to be rewritten
    # instead of quietly outliving its own truth.
    try:
        apply_firewall(tuple(as_record(row) for row in rows))
    except FirewallError as refusal:
        raw_refusal = str(refusal)
    else:  # pragma: no cover - only reachable if the corpus itself changes
        raise SystemExit(
            "the raw corpus no longer contains duplicate record ids; "
            "08-firewall-real-comments/NOTES.md leads with that finding and now needs rewriting"
        )

    by_id: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_id.setdefault(row["COMMENT_ID"], []).append(row)
    duplicated = {rid: group for rid, group in by_id.items() if len(group) > 1}

    # Dropping a duplicate is only lossless if the rows are actually identical.
    # If two rows ever share an id and differ in content, dropping one destroys
    # data, and this example would be quietly choosing which comment to believe.
    for rid, group in duplicated.items():
        distinct = {(r["AUTHOR"], r["CONTENT"], r["CLASS"]) for r in group}
        if len(distinct) > 1:
            raise SystemExit(
                f"record id {rid} covers rows that differ in content; de-duplicating "
                "would discard data, so this example refuses rather than choosing one"
            )

    deduplicated = [group[0] for group in by_id.values()]
    records = tuple(as_record(row) for row in deduplicated)
    result = apply_firewall(records)

    # Per-record signals, so the report can say which comments tripped what
    # rather than only how many did.
    flagged: dict[str, set[str]] = {}
    for signal in result.signals:
        flagged.setdefault(signal.record_id, set()).add(signal.pattern_id.value)

    spam = sum(1 for row in deduplicated if row["CLASS"] == "1")
    out.mkdir(parents=True, exist_ok=True)
    (out / "firewall_report.json").write_text(
        json.dumps(
            {
                "corpus": {
                    "name": "UCI YouTube Spam Collection",
                    "doi": "10.24432/C58885",
                    "licence": "CC BY 4.0",
                    "rows_as_published": len(rows),
                    "distinct_record_ids": len(records),
                    "labelled_spam": spam,
                    "labelled_legitimate": len(records) - spam,
                },
                "raw_corpus_refused_by_the_firewall": {
                    "reason": raw_refusal,
                    "duplicate_ids": sorted(duplicated),
                    "rows_dropped": len(rows) - len(records),
                    "lossless": (
                        "every duplicated id covered byte-identical rows; the example "
                        "refuses to proceed if they ever differ"
                    ),
                },
                "firewall": {
                    "pattern_set_version": result.pattern_set_version,
                    "pattern_set_hash": result.pattern_set_hash,
                    "block_nonce": result.block.nonce,
                    "redacted": result.redacted,
                    "signal_counts": result.signal_counts(),
                    "records_with_at_least_one_signal": len(flagged),
                },
                "flagged_records": [
                    {"record_id": rid, "patterns": sorted(patterns)}
                    for rid, patterns in sorted(flagged.items())
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    # A readable slice of the model-facing block, so the fence and the
    # content-derived nonce are visible rather than only counted. Chosen
    # deterministically and in corpus order: any flagged records first, then
    # records whose text contains a character the encoder has to escape (a
    # quote, a backslash or a line break), then whatever is next. Preferring
    # awkward text is the point. A sample of six bland comments would render a
    # block that proves nothing about the encoding.
    def rank(record: CaseRecord) -> tuple[int, int]:
        flagged_rank = 0 if record.record_id in flagged else 1
        awkward = 0 if set(record.text) & set('"\\\n\r\t') else 1
        return (flagged_rank, awkward)

    ordered = sorted(enumerate(records), key=lambda pair: (*rank(pair[1]), pair[0]))
    chosen = tuple(record for _, record in ordered[:6])
    sample = apply_firewall(chosen)
    (out / "sample_block.txt").write_text(
        "# The model-facing block for six of the 1,956 comments.\n"
        "# Everything below the fence is inert data. The fence token is a digest\n"
        "# of the content it fences, so closing it early is a preimage problem.\n\n"
        + sample.model_text
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def git_describe() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):  # pragma: no cover
        return "unknown"


def main_regenerate() -> int:
    with tempfile.TemporaryDirectory(prefix="ts-sentry-examples-") as tmp:
        build = Path(tmp) / "build"
        print(f"=== building the seed-{SEED} scale-{SCALE} dataset into {build}")
        if main(["build-dataset", "--seed", str(SEED), "--scale", str(SCALE), "--out", str(build)]):
            raise SystemExit("build-dataset failed")

        # 01: triage
        out = run(
            "01-triage-queue",
            [
                "run-session", "--agent", "triage",
                "--seed-dataset", str(build),
                "--out", str(EXAMPLES / "01-triage-queue"),
                "--analyst-id", ANALYST,
            ],
            expect=0,
        )
        write_inputs(
            out,
            command="ts-sentry run-session --agent triage --seed-dataset BUILD --out .",
            inputs={"agent": "triage", "limit": 25, "stub_mode": "faithful"},
        )

        # 02: evidence on the T-02 ring
        out = run(
            "02-evidence-t02-ring",
            [
                "run-session", "--agent", "evidence",
                "--seed-dataset", str(build),
                "--out", str(EXAMPLES / "02-evidence-t02-ring"),
                "--analyst-id", ANALYST,
                "--case", "case-0000",
                "--subject", "t02_chan_000_000",
            ],
            expect=0,
        )
        write_inputs(
            out,
            command=(
                "ts-sentry run-session --agent evidence --seed-dataset BUILD --out . "
                "--case case-0000 --subject t02_chan_000_000"
            ),
            inputs={
                "agent": "evidence",
                "case_id": "case-0000",
                "subject_id": "t02_chan_000_000",
                "review": "scripted",
                "stub_mode": "faithful",
            },
        )
        t02_pack = out / "evidence_pack.json"

        # 03: a memo drafted from 02's pack, then signed
        out = run(
            "03-signed-memo",
            [
                "run-session", "--agent", "memo",
                "--seed-dataset", str(build),
                "--pack", str(t02_pack),
                "--out", str(EXAMPLES / "03-signed-memo"),
                "--analyst-id", ANALYST,
            ],
            expect=0,
        )
        if main(
            [
                "sign-memo", str(out),
                "--analyst-id", ANALYST,
                "--pack", str(t02_pack),
            ]
        ):
            raise SystemExit("sign-memo failed")
        write_inputs(
            out,
            command=(
                "ts-sentry run-session --agent memo --seed-dataset BUILD "
                "--pack ../02-evidence-t02-ring/evidence_pack.json --out .\n"
                "ts-sentry sign-memo . --analyst-id saif "
                "--pack ../02-evidence-t02-ring/evidence_pack.json"
            ),
            inputs={
                "agent": "memo",
                "pack": "../02-evidence-t02-ring/evidence_pack.json",
                "memo_id": "memo-0001",
                "decision": "approve_enforcement",
                "stub_mode": "faithful",
            },
        )

        # 04: evidence on a T-07 cluster, which also supplies 05's pack
        out = run(
            "04-evidence-t07-cluster",
            [
                "run-session", "--agent", "evidence",
                "--seed-dataset", str(build),
                "--out", str(EXAMPLES / "04-evidence-t07-cluster"),
                "--analyst-id", ANALYST,
                "--case", "case-0001",
                "--subject", "t07_chan_000",
            ],
            expect=0,
        )
        write_inputs(
            out,
            command=(
                "ts-sentry run-session --agent evidence --seed-dataset BUILD --out . "
                "--case case-0001 --subject t07_chan_000"
            ),
            inputs={
                "agent": "evidence",
                "case_id": "case-0001",
                "subject_id": "t07_chan_000",
                "review": "scripted",
                "stub_mode": "faithful",
            },
        )
        t07_pack = out / "evidence_pack.json"

        # 05: the same memo agent, deliberately made to overclaim, and refused
        out = run(
            "05-overclaim-refused",
            [
                "run-session", "--agent", "memo",
                "--seed-dataset", str(build),
                "--pack", str(t07_pack),
                "--out", str(EXAMPLES / "05-overclaim-refused"),
                "--analyst-id", ANALYST,
                "--stub-mode", "overclaim",
            ],
            expect=0,
        )
        write_inputs(
            out,
            command=(
                "ts-sentry run-session --agent memo --seed-dataset BUILD "
                "--pack ../04-evidence-t07-cluster/evidence_pack.json --out . "
                "--stub-mode overclaim"
            ),
            inputs={
                "agent": "memo",
                "pack": "../04-evidence-t07-cluster/evidence_pack.json",
                "memo_id": "memo-0001",
                "stub_mode": "overclaim",
            },
        )

        # 06: a degraded prompt candidate, refused activation (exit 7)
        #
        # The registry lives beside the example rather than inside it, and not
        # for tidiness: `eval-prompts` refuses to write into a directory that is
        # already non-empty, because a session writes its own directory and
        # overwriting one would destroy the audit trail it holds. A registry
        # subdirectory would have tripped that guard, which is the guard being
        # right.
        registry_dir = EXAMPLES / "registries" / "degraded-classify"
        out = EXAMPLES / "06-prompt-eval-refused"
        notes = reset(out)
        candidate = build_degraded_registry(registry_dir)
        print(f"\n=== 06-prompt-eval-refused: candidate {candidate[:16]}")
        code = main(
            [
                "eval-prompts",
                "--candidate", candidate,
                "--registry", str(registry_dir),
                "--evals", str(REPO_ROOT / "evals" / "threat_class"),
                "--out", str(out),
                "--analyst-id", ANALYST,
            ]
        )
        if code != 7:
            raise SystemExit(f"06-prompt-eval-refused: expected exit 7, got {code}")
        for unwanted in UNCOMMITTED:
            (out / unwanted).unlink(missing_ok=True)
        restore(out, notes)
        write_inputs(
            out,
            command=(
                "ts-sentry eval-prompts --candidate CANDIDATE_DIGEST "
                "--registry examples/registries/degraded-classify "
                "--evals evals/threat_class --out ."
            ),
            inputs={
                "candidate_digest": candidate,
                "candidate_prompt_id": "classify.threat_class.v2",
                "registry": "../registries/degraded-classify",
                "evals": "evals/threat_class",
                "expected_exit": 7,
                "stub_mode": "faithful",
            },
        )

        # 07: the measurement report over 01's session
        out = EXAMPLES / "07-measurement-report"
        notes = reset(out)
        out.mkdir(parents=True)
        print("\n=== 07-measurement-report: ts-sentry report")
        code = main(
            [
                "report",
                "--session", str(EXAMPLES / "01-triage-queue"),
                "--build", str(build),
                "--out", str(out),
            ]
        )
        if code:
            raise SystemExit(f"07-measurement-report: report failed with exit {code}")
        restore(out, notes)
        write_inputs(
            out,
            command=(
                "ts-sentry report --session ../01-triage-queue --build BUILD --out ."
            ),
            inputs={"session": "../01-triage-queue", "sample_size": 9000, "cases": 1},
        )

    # 08: the input firewall over real third-party comment text. Outside the
    # `with` block above because it needs no dataset: it reads committed CSVs.
    print("\n=== 08-firewall-real-comments: apply_firewall over 1,956 real comments")
    firewall_out = EXAMPLES / "08-firewall-real-comments"
    firewall_notes = reset(firewall_out)
    run_firewall_over_real_comments(firewall_out)
    restore(firewall_out, firewall_notes)
    write_inputs(
        firewall_out,
        command="python examples/regenerate.py  (library call, not a CLI verb: see NOTES.md)",
        inputs={
            "corpus": "../data/youtube-spam-collection",
            "corpus_doi": "10.24432/C58885",
            "corpus_licence": "CC BY 4.0",
            "rows_as_published": 1956,
        },
        from_dataset=False,
    )

    print(f"\nregenerated at {git_describe()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_regenerate())
