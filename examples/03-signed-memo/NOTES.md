# 03: a drafted memo, verified, then signed by a human

**What this is.** One RECOMMEND session drafting an enforcement memo from
example 02's accepted evidence pack, followed by the human signature path. The
memo is structured as a DSA Article 17 style statement of reasons.

Reproduce:

```bash
ts-sentry run-session --agent memo --seed-dataset build \
    --pack ../02-evidence-t02-ring/evidence_pack.json --out . --analyst-id saif
ts-sentry sign-memo . --analyst-id saif \
    --pack ../02-evidence-t02-ring/evidence_pack.json
```

## What it demonstrates

| Claim | Where to see it |
|---|---|
| Every factual sentence resolves to a real evidence record | Sentence 0 cites `prov-0000`, which `02`'s pack carries |
| Every policy citation resolves to a real anchor and quotes it verbatim | Sentence 1 cites anchor `comment-spam` in document `86ea53c9...`, excerpt `"Comment spam: Using high-volume,"` |
| The four Article 17 roles are all present | `fact`, `policy_ground`, `measure`, `redress` |
| The automated-means disclosure is structural, not agent-written | `automated_means.decision = partially_automated`, `drafted_by = stub/faithful:deterministic-stub-v1` |
| The memo is pinned to the exact evidence and corpus it was checked against | `pack_digest 9dfc0932...`, `corpus_version 1.0.0`, `corpus_sha256 9dd656fb...` |
| Signing is the only path to a final memo | `memo_signature.json`: analyst `saif`, decision `approve_enforcement`, signature over the memo's own digest |
| The AI-DRAFT watermark is present until signed, and absent after | `memo.md` and `memo.html` re-render on signing; the watermark has no suppression parameter |
| Verified first time | 1 attempt, 0 rejected, 0 distinct defects |

## What this deliberately does not claim

- **"These are DSA Article 17 statements of reasons" would be wider than the
  truth.** Article 17(2) says paragraph 1 "shall not apply where the information
  is deceptive high-volume commercial content", which plausibly covers exactly
  the T-01 comment-spam and T-06 slop-farm caseload this system models. These
  memos are regulation-*shaped* best-practice documentation. Territorial scope
  and duration (Art. 17(3)(a)) are not modelled, and there is no `LEGAL_GROUND`
  role, so Art. 17(3)(d) is unreachable by construction: every case here is a
  terms-and-conditions matter and a legal-ground role would invite a memo to
  assert illegality nothing in this system can assess.
- **A signature proves integrity, not identity.** It binds five fields together
  and shows they have not drifted apart. It does **not** authenticate that the
  person named `saif` signed it. Real analyst authentication is out of scope and
  has been named as such since STEP-02.
- **The memo agent's competence is untested.** A deterministic stub drafted
  this. What is demonstrated is that a claim must resolve before it can pass,
  that a citation must point at text that exists, and that nothing becomes final
  without a signature.
- **Passing first time is not evidence the gate is lenient.** The same gate
  refuses eight drafts in a row in `05-overclaim-refused`, which is the run to
  read next.
- **A DRAFT and a SIGNED memo with identical content share a `content_digest`**,
  because `status` is excluded from it. That is deliberate: including it meant
  signing changed the digest the signature was taken over, and the artifact
  failed its own verification the instant it was produced. The consequence is
  stated here rather than left to be discovered.
