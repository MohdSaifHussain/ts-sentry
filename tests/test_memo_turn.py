# SPDX-License-Identifier: MIT
"""STEP-05 D4: the draft format, the draft checker, and the memo turn.

Offline. The corpus is the committed one and the pack is built in memory, so
these exercise the real clause text without a network or a build.

The test that matters most here is the revise-loop success path. The offline
stub does *not* revise: told what was wrong, it re-sends the same draft, so a
suite that only ran the stub would exercise the loop's machinery and never its
capability. A responder that corrects itself is what shows the loop working, and
one that never does is what shows it terminating.
"""

from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pytest

from ts_sentry.agents.evidence.pack import EvidencePack
from ts_sentry.agents.memo.draft import parse_draft, render_draft
from ts_sentry.agents.memo.memo import (
    AutomatedDecision,
    AutomatedMeans,
    Measure,
    MemoStatus,
    SentenceRole,
)
from ts_sentry.data.enums import EntityKind
from ts_sentry.data.policy_corpus import PolicyCorpus, load_corpus
from ts_sentry.data.tz import IST
from ts_sentry.governance.ledger import Ledger
from ts_sentry.governance.mandate import AgentId
from ts_sentry.orchestrator.adapter import (
    ModelRequest,
    RecordingSleeper,
    RetryPolicy,
    StubAdapter,
    StubMode,
)
from ts_sentry.orchestrator.core import FixedClock, Session
from ts_sentry.orchestrator.draft_check import DraftRefusal, check_draft
from ts_sentry.orchestrator.fleet import default_mandates, phase_five_checks
from ts_sentry.orchestrator.memo_turn import run_memo_turn, stub_memo_responder
from ts_sentry.orchestrator.pack_export import read_pack_json, write_pack_json

POLICIES_DIR = Path(__file__).resolve().parent.parent / "policies"
_START = datetime(2026, 8, 1, 12, 0, tzinfo=IST)
_TS = _START.isoformat()
_DATASET_DIGEST = "a" * 64


@pytest.fixture(scope="module")
def corpus() -> PolicyCorpus:
    return load_corpus(POLICIES_DIR)


@pytest.fixture
def pack() -> EvidencePack:
    return EvidencePack.seed("case-0001", "t02_chan_000_000", EntityKind.CHANNEL, _TS)


def _means() -> AutomatedMeans:
    return AutomatedMeans(
        detection_automated=True,
        decision=AutomatedDecision.PARTIALLY_AUTOMATED,
        drafted_by="test",
    )


def _session(corpus: PolicyCorpus) -> Session:
    return Session(
        session_id="session-memo-001",
        analyst_id="saif",
        ledger=Ledger(duckdb.connect(":memory:")),
        clock=FixedClock(_START, step=timedelta(seconds=1)),
        mandates=default_mandates(),
        dataset_digest=_DATASET_DIGEST,
        corpus=corpus,
    )


def _good_draft(pack: EvidencePack, corpus: PolicyCorpus) -> str:
    document = corpus.document("youtube-spam")
    assert document is not None
    clause = document.clause("comment-spam")
    assert clause is not None
    excerpt = " ".join(clause.text.split()[:6])
    seed = pack.provenance[0].provenance_id
    return "\n".join(
        (
            f"FACT: The subject entered this investigation as its seed [{seed}].",
            f"GROUND: anchor=comment-spam | excerpt={excerpt} | This is incompatible.",
            "MEASURE: content_demoted",
            "REDRESS: The owner may appeal through the internal complaint-handling system.",
        )
    )


# ---------------------------------------------------------------------------
# The draft format
# ---------------------------------------------------------------------------


def test_a_draft_round_trips(pack: EvidencePack, corpus: PolicyCorpus) -> None:
    text = _good_draft(pack, corpus)

    assert render_draft(parse_draft(text).lines) == text


def test_unrecognised_lines_are_dropped_never_guessed_at() -> None:
    draft = parse_draft("Here is my analysis.\nFACT: something [x].\n\nThanks!")

    assert len(draft.lines) == 1
    assert draft.lines[0].kind == "FACT"


def test_parsing_is_total_on_anything() -> None:
    """Returns an empty draft rather than None, so the refusal lives in one
    place instead of being split between a parse result and a verdict."""
    assert parse_draft("").lines == ()
    assert parse_draft("{'json': true}").lines == ()


def test_a_ground_line_splits_on_pipes_not_on_punctuation_in_the_quote() -> None:
    """The excerpt is verbatim policy text and contains commas, colons and
    semicolons. A separator that appears in the data is how a parser starts
    silently truncating the thing it is quoting."""
    draft = parse_draft(
        "GROUND: anchor=comment-spam | excerpt=Comment spam: Using high-volume, "
        "repetitive | This breaches it."
    )

    line = draft.lines[0]
    assert line.anchor == "comment-spam"
    assert line.excerpt == "Comment spam: Using high-volume, repetitive"
    assert line.text == "This breaches it."


def test_the_last_measure_wins(pack: EvidencePack, corpus: PolicyCorpus) -> None:
    """A model that corrects itself is read as having corrected itself."""
    draft = parse_draft("MEASURE: content_removed\nMEASURE: content_demoted")

    assert draft.measure_value == "content_demoted"


# ---------------------------------------------------------------------------
# The draft checker
# ---------------------------------------------------------------------------


def _check(text: str, pack: EvidencePack, corpus: PolicyCorpus) -> object:
    return check_draft(
        parse_draft(text), pack, corpus, memo_id="memo-0001", automated_means=_means()
    )


def test_a_good_draft_becomes_a_memo(pack: EvidencePack, corpus: PolicyCorpus) -> None:
    verdict = check_draft(
        parse_draft(_good_draft(pack, corpus)),
        pack,
        corpus,
        memo_id="memo-0001",
        automated_means=_means(),
    )

    assert verdict.accepted
    assert verdict.memo is not None
    assert verdict.memo.measure is Measure.CONTENT_DEMOTED
    assert verdict.memo.status is MemoStatus.DRAFT
    assert verdict.memo.pack_digest == pack.content_digest


def test_an_empty_response_is_refused_as_malformed(
    pack: EvidencePack, corpus: PolicyCorpus
) -> None:
    verdict = check_draft(
        parse_draft("I cannot help with that."),
        pack,
        corpus,
        memo_id="m",
        automated_means=_means(),
    )

    assert verdict.code is DraftRefusal.MALFORMED


def test_a_draft_missing_a_role_is_refused(pack: EvidencePack, corpus: PolicyCorpus) -> None:
    seed = pack.provenance[0].provenance_id
    verdict = check_draft(
        parse_draft(f"FACT: a [{seed}].\nMEASURE: content_demoted\nREDRESS: appeal."),
        pack,
        corpus,
        memo_id="m",
        automated_means=_means(),
    )

    assert verdict.code is DraftRefusal.MISSING_ROLE
    assert "GROUND" in verdict.detail


def test_an_invented_measure_is_refused(pack: EvidencePack, corpus: PolicyCorpus) -> None:
    """The vocabulary is closed so a memo cannot propose a sanction nobody has
    to honour."""
    text = _good_draft(pack, corpus).replace(
        "MEASURE: content_demoted", "MEASURE: permanent_shadowban"
    )

    verdict = check_draft(parse_draft(text), pack, corpus, memo_id="m", automated_means=_means())

    assert verdict.code is DraftRefusal.UNKNOWN_MEASURE


def test_an_anchor_no_document_carries_is_refused(pack: EvidencePack, corpus: PolicyCorpus) -> None:
    text = _good_draft(pack, corpus).replace("anchor=comment-spam", "anchor=coordinated-behaviour")

    verdict = check_draft(parse_draft(text), pack, corpus, memo_id="m", automated_means=_means())

    assert verdict.code is DraftRefusal.UNKNOWN_ANCHOR


def test_an_anchor_two_documents_share_is_refused(pack: EvidencePack, corpus: PolicyCorpus) -> None:
    """A real collision in corpus v1, not a hypothetical.

    ``what-this-policy-means-for-you`` is a heading on both the spam page and
    the fake-engagement page. Resolving it to the first would decide which
    policy the memo relies on, which is the memo's job to say.
    """
    text = _good_draft(pack, corpus).replace(
        "anchor=comment-spam", "anchor=what-this-policy-means-for-you"
    )

    verdict = check_draft(parse_draft(text), pack, corpus, memo_id="m", automated_means=_means())

    assert verdict.code is DraftRefusal.AMBIGUOUS_ANCHOR
    assert "youtube-spam" in verdict.detail


def test_a_fact_citing_nothing_is_refused_structurally(
    pack: EvidencePack, corpus: PolicyCorpus
) -> None:
    seed = pack.provenance[0].provenance_id
    text = _good_draft(pack, corpus).replace(f"as its seed [{seed}].", "as its seed, obviously.")

    verdict = check_draft(parse_draft(text), pack, corpus, memo_id="m", automated_means=_means())

    assert verdict.code is DraftRefusal.STRUCTURAL
    assert "FACT citing no evidence" in verdict.detail


def test_the_measure_sentence_is_built_from_the_enum_not_the_model(
    pack: EvidencePack, corpus: PolicyCorpus
) -> None:
    """So the memo's measure and the sentence describing it cannot disagree."""
    verdict = check_draft(
        parse_draft(_good_draft(pack, corpus)),
        pack,
        corpus,
        memo_id="m",
        automated_means=_means(),
    )

    assert verdict.memo is not None
    measure_sentence = next(s for s in verdict.memo.sentences if s.role is SentenceRole.MEASURE)
    assert measure_sentence.text == "The proposed measure is content_demoted."


# ---------------------------------------------------------------------------
# The turn
# ---------------------------------------------------------------------------


def test_a_faithful_draft_verifies_in_one_attempt(pack: EvidencePack, corpus: PolicyCorpus) -> None:
    session = _session(corpus)
    session.open()

    turn = run_memo_turn(
        session,
        StubAdapter(responder=stub_memo_responder),
        pack=pack,
        corpus=corpus,
        checks=phase_five_checks(pack, corpus),
        policy=RetryPolicy(),
        rng=np.random.default_rng(42),
        sleeper=RecordingSleeper(),
    )

    assert turn.verified
    assert len(turn.attempts) == 1
    assert turn.memo is not None
    assert turn.memo.status is MemoStatus.DRAFT  # verified is not signed


def test_an_overclaiming_agent_never_gets_a_verified_memo(
    pack: EvidencePack, corpus: PolicyCorpus
) -> None:
    """The exit criterion, at unit scale: a planted overclaim is caught every
    time and the memo stays DRAFT rather than being accepted on the last try."""
    session = _session(corpus)
    session.open()

    turn = run_memo_turn(
        session,
        StubAdapter(mode=StubMode.OVERCLAIM, responder=stub_memo_responder),
        pack=pack,
        corpus=corpus,
        checks=phase_five_checks(pack, corpus),
        policy=RetryPolicy(),
        rng=np.random.default_rng(42),
        sleeper=RecordingSleeper(),
        max_attempts=3,
    )

    assert not turn.verified
    assert turn.rejected_attempts == 3
    assert turn.memo is not None
    assert turn.memo.status is MemoStatus.DRAFT
    assert all("prov-9999" in flag for record in turn.attempts for flag in record.flagged)


def test_the_stub_does_not_revise_and_that_is_reported(
    pack: EvidencePack, corpus: PolicyCorpus
) -> None:
    """Asserted as a passing test, in the shape STEP-02 used for tail truncation
    and STEP-04 for recovery saturation.

    Told exactly what was wrong, the offline stub re-sends the same draft. Three
    rejections of one unchanged sentence is one defect caught three times, and
    reporting it as three corrections would inflate the metric ARCHITECTURE 7.2
    showcases. The day a stub revises, this test fails and forces the claim to
    be rewritten rather than quietly outliving its truth.
    """
    session = _session(corpus)
    session.open()

    turn = run_memo_turn(
        session,
        StubAdapter(mode=StubMode.OVERCLAIM, responder=stub_memo_responder),
        pack=pack,
        corpus=corpus,
        checks=phase_five_checks(pack, corpus),
        policy=RetryPolicy(),
        rng=np.random.default_rng(42),
        sleeper=RecordingSleeper(),
        max_attempts=3,
    )

    assert turn.rejected_attempts == 3
    assert turn.distinct_defects == 1
    assert turn.revised is False


def test_an_agent_that_corrects_itself_gets_through(
    pack: EvidencePack, corpus: PolicyCorpus
) -> None:
    """The revise loop's success path, which no stub mode exercises.

    A responder that overclaims once and fixes it when told is what shows the
    loop is a capability rather than machinery. Without this the suite would
    prove only that a bad memo is refused forever, which is half the contract in
    STEP-05 3.2.
    """
    attempts: list[int] = []

    def correcting(request: ModelRequest, mode: StubMode) -> str:
        attempts.append(1)
        if len(attempts) == 1:
            return _good_draft(pack, corpus).replace(pack.provenance[0].provenance_id, "prov-9999")
        return _good_draft(pack, corpus)

    session = _session(corpus)
    session.open()

    turn = run_memo_turn(
        session,
        StubAdapter(responder=correcting),
        pack=pack,
        corpus=corpus,
        checks=phase_five_checks(pack, corpus),
        policy=RetryPolicy(),
        rng=np.random.default_rng(42),
        sleeper=RecordingSleeper(),
        max_attempts=3,
    )

    assert turn.verified
    assert len(turn.attempts) == 2
    assert turn.attempts[0].outcome == "gate_rejected"
    assert turn.attempts[1].outcome == "verified"
    assert turn.distinct_defects == 1
    assert turn.revised is True


def test_the_agent_is_told_what_the_verifier_objected_to(
    pack: EvidencePack, corpus: PolicyCorpus
) -> None:
    """Unlike a rejected pivot, where the analyst's reasoning is withheld.

    A verification failure is a mechanical finding about a citation rather than
    a human judgment, so withholding it would spend the step budget on the agent
    guessing which sentence was wrong.
    """
    seen: list[str] = []

    def recording(request: ModelRequest, mode: StubMode) -> str:
        seen.append(request.user_content)
        return _good_draft(pack, corpus).replace(pack.provenance[0].provenance_id, "prov-9999")

    session = _session(corpus)
    session.open()

    run_memo_turn(
        session,
        StubAdapter(responder=recording),
        pack=pack,
        corpus=corpus,
        checks=phase_five_checks(pack, corpus),
        policy=RetryPolicy(),
        rng=np.random.default_rng(42),
        sleeper=RecordingSleeper(),
        max_attempts=2,
    )

    assert "refused these sentences" in seen[1]
    assert "prov-9999" in seen[1]


def test_the_budget_stops_the_loop_and_leaves_a_draft(
    pack: EvidencePack, corpus: PolicyCorpus
) -> None:
    session = _session(corpus)
    session.open()

    turn = run_memo_turn(
        session,
        StubAdapter(mode=StubMode.OVERCLAIM, responder=stub_memo_responder),
        pack=pack,
        corpus=corpus,
        checks=phase_five_checks(pack, corpus),
        policy=RetryPolicy(),
        rng=np.random.default_rng(42),
        sleeper=RecordingSleeper(),
        max_attempts=2,
    )

    assert len(turn.attempts) == 2
    assert not turn.verified


def test_the_memo_mandate_grants_no_data_scopes() -> None:
    """The narrowest mandate in the fleet. The memo agent reaches no platform
    table at all: it works from an accepted pack and the hashed corpus."""
    mandate = default_mandates()[AgentId.MEMO]

    assert mandate.data_scopes == frozenset()


# ---------------------------------------------------------------------------
# The pack round trip the memo binding depends on
# ---------------------------------------------------------------------------


def test_a_pack_survives_a_write_and_read_with_its_digest_intact(
    pack: EvidencePack, tmp_path: Path
) -> None:
    """Without this, a memo drafted in one session and verified in another would
    fail its own binding check for no reason but serialization."""
    path = tmp_path / "evidence_pack.json"
    write_pack_json(pack, path)

    reloaded = read_pack_json(path)

    assert reloaded.content_digest == pack.content_digest
    assert reloaded.to_json_object() == pack.to_json_object()


def test_a_tampered_pack_export_is_refused_on_read(pack: EvidencePack, tmp_path: Path) -> None:
    """Reconstruction goes through the ordinary constructors, so there is no
    bypass for the convenience of reading."""
    import json

    from ts_sentry.orchestrator.pack_export import PackReadError

    path = tmp_path / "evidence_pack.json"
    write_pack_json(pack, path)
    body = json.loads(path.read_text(encoding="utf-8"))
    body["nodes"][0]["node_id"] = "someone-else"
    path.write_text(json.dumps(body), encoding="utf-8", newline="\n")

    with pytest.raises(PackReadError):
        read_pack_json(path)
