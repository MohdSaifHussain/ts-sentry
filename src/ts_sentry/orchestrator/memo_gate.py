# SPDX-License-Identifier: MIT
"""The RECOMMEND gate's checker (STEP-05, ARCHITECTURE 3.3).

ARCHITECTURE 3.3 defines the RECOMMEND gate as "symbolic verification: every
claim sentence must carry at least one resolvable evidence-record ID;
unresolvable claims fail the memo". STEP-02 shipped the pipeline and the
``FailureCode`` vocabulary and left this checker unimplemented on purpose,
because implementing it then would have meant inventing the artifact it checks.
``fleet`` has been failing the RECOMMEND gate closed ever since. This is the
checker that opens it.

Orchestrator-side, like ``pack_gate`` and ``proposal_check``, for the reason the
STEP-03 import-graph test found: an agent holding its own verifier is an agent
nobody is verifying.

Two resolution surfaces, both zero-tolerance
--------------------------------------------
A memo makes two kinds of pointer and they fail differently, so both are
checked and neither is allowed to stand in for the other:

* **claims to evidence**, through ``governance.verifier.verify_claims`` with
  the resolvable set being the pack's own record ids. Reused, not
  reimplemented: that function was made generic in STEP-02 precisely so triage
  rationales, pivot proposals and now memo sentences all resolve through one
  checker. A memo-shaped copy of it would be a second thing to keep correct.
* **citations to policy**, through ``citation_resolver``. A memo can name a
  real document, a plausible anchor and a fluent quotation and be wrong about
  all three.

Zero tolerance on both (STEP-02 3.4): one failing sentence fails the memo.
There is no score and no threshold. Every failure names the sentence index, so
the D4 revise loop can point at the offending sentence rather than hand back a
whole failed document.

The binding check, and why it is not paranoia
---------------------------------------------
``pack_digest`` must match the pack in scope. Without it the gate would verify
a memo's claims against whichever pack it happened to be handed, and a memo
drafted from one investigation could be validated against another's evidence.
That is the same defect DECISIONS 4.6 removed on the assembly side: a check
that resolves an agent's claims against a set the agent could have influenced
is not a check.

Failures are returned, never raised (DECISIONS 2.4).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from ts_sentry.agents.evidence.pack import EvidencePack
from ts_sentry.agents.memo.memo import Memo, MemoSentence, SentenceRole
from ts_sentry.data.policy_corpus import PolicyCorpus
from ts_sentry.governance.gates import ArtifactCheck, FailureCode, GateFailure
from ts_sentry.governance.verifier import Claim, verify_claims
from ts_sentry.orchestrator.citation_resolver import resolve_citation

__all__ = ["MemoCheck", "memo_check", "memo_checker"]


def _check_bindings(memo: Memo, pack: EvidencePack, corpus: PolicyCorpus) -> list[GateFailure]:
    """The memo describes the pack and corpus actually in scope."""
    failures: list[GateFailure] = []

    if memo.pack_digest != pack.content_digest:
        failures.append(
            GateFailure(
                code=FailureCode.SCHEMA_INVALID,
                detail=(
                    f"memo {memo.memo_id} names pack digest {memo.pack_digest[:16]}... and the "
                    f"pack in scope digests to {pack.content_digest[:16]}...; this memo was "
                    "drafted from different evidence than it is being verified against"
                ),
            )
        )
    if memo.corpus_sha256 != corpus.corpus_sha256:
        failures.append(
            GateFailure(
                code=FailureCode.SCHEMA_INVALID,
                detail=(
                    f"memo {memo.memo_id} pins corpus {memo.corpus_sha256[:16]}... and the "
                    f"corpus in scope is {corpus.corpus_sha256[:16]}...; its citations were "
                    "checked against a different corpus version"
                ),
            )
        )
    return failures


def _check_claims(sentences: Sequence[MemoSentence], pack: EvidencePack) -> list[GateFailure]:
    """Every FACT resolves to evidence the pack actually carries.

    Only FACT sentences are submitted. That is not leniency towards the others:
    a MEASURE states what is proposed and a REDRESS states a procedural right,
    and neither is an assertion about the subject, so requiring them to cite
    evidence would be requiring a citation for a sentence with nothing to cite.
    ``MemoSentence.__post_init__`` is what makes a FACT without evidence
    unconstructible; this is what makes a FACT with *unresolvable* evidence
    fail.

    A POLICY_GROUND may carry evidence ids and they are checked when present,
    because a ground sentence that points at the pack is making a claim about
    the pack.
    """
    claimed = [
        sentence
        for sentence in sentences
        if sentence.role is SentenceRole.FACT or sentence.evidence_ids
    ]
    if not claimed:
        return []

    report = verify_claims(
        [
            Claim(text=sentence.text, claimed_evidence_ids=sentence.evidence_ids)
            for sentence in claimed
        ],
        pack.record_ids,
    )

    failures: list[GateFailure] = []
    for result in report.failures:
        sentence = claimed[result.index]
        unresolvable = ", ".join(result.unresolvable_ids)
        failures.append(
            GateFailure(
                code=FailureCode.UNVERIFIED_CLAIM,
                detail=(
                    f"sentence {sentence.index} ({sentence.role.value}, "
                    f"{result.reason.value if result.reason else '?'}): {sentence.text!r}"
                    + (
                        f"; the pack does not carry {unresolvable}"
                        if unresolvable
                        else "; it cites no evidence this pack carries"
                    )
                ),
            )
        )
    return failures


def _check_citations(sentences: Sequence[MemoSentence], corpus: PolicyCorpus) -> list[GateFailure]:
    """Every policy citation resolves to a real clause, quoted accurately."""
    failures: list[GateFailure] = []

    for sentence in sentences:
        if sentence.citation is None:
            continue
        resolution = resolve_citation(sentence.citation, corpus)
        if resolution.resolved:
            continue
        assert resolution.code is not None  # an unresolved resolution carries one
        failures.append(
            GateFailure(
                code=FailureCode.UNVERIFIED_CLAIM,
                detail=(
                    f"sentence {sentence.index} ({resolution.code.value}): {resolution.detail}"
                ),
            )
        )
    return failures


@dataclass(frozen=True, slots=True)
class MemoCheck:
    """Callable adapter satisfying ``gates.ArtifactCheck``.

    A closure over the pack and corpus in scope, in the shape
    ``verifier.claim_check`` uses and for the same reason: the gate's contract
    passes only the artifact, and what counts as resolvable is a property of
    the evidence and the corpus established before the gate runs, never
    something the memo gets to assert about itself.
    """

    pack: EvidencePack
    corpus: PolicyCorpus

    def __call__(self, artifact: object, /) -> tuple[GateFailure, ...]:
        if not isinstance(artifact, Memo):
            return (
                GateFailure(
                    code=FailureCode.SCHEMA_INVALID,
                    detail=f"expected a Memo; got {type(artifact).__name__}",
                ),
            )
        return memo_check(artifact, self.pack, self.corpus)


def memo_check(memo: Memo, pack: EvidencePack, corpus: PolicyCorpus) -> tuple[GateFailure, ...]:
    """Run every RECOMMEND check over one memo.

    Returns every failure it finds rather than the first. A memo with three
    unsupported sentences should be reported as a memo with three unsupported
    sentences: the revise loop fixes what it is told about, and reporting one
    at a time would spend a step per defect.

    Bindings are checked first because a mismatch makes the rest meaningless:
    claims verified against the wrong pack would produce failures that describe
    nothing real, and the binding failure is the finding worth acting on.
    """
    bindings = _check_bindings(memo, pack, corpus)
    if bindings:
        return tuple(bindings)

    failures: list[GateFailure] = []
    failures.extend(_check_claims(memo.sentences, pack))
    failures.extend(_check_citations(memo.sentences, corpus))
    return tuple(failures)


def memo_checker(pack: EvidencePack, corpus: PolicyCorpus) -> ArtifactCheck:
    """The checker, as the protocol type the gate declares.

    Takes the pack and corpus rather than reading them from anywhere, following
    ``GateChecks``' no-defaults rule: there must be no way to run this gate
    without naming what a claim is allowed to resolve against.
    """
    return MemoCheck(pack=pack, corpus=corpus)
