# 05: the gate refusing an agent that overclaims

**What this is.** The same memo agent as example 03, deliberately made to
overclaim, drafting from example 04's pack. It cites `prov-9999`, an evidence id
no pack carries. The RECOMMEND gate refuses it eight times and the memo never
leaves DRAFT.

This is the example the whole governance layer exists for. A verifier that has
never rejected anything is a verifier nobody has tested.

Reproduce:

```bash
ts-sentry run-session --agent memo --seed-dataset build \
    --pack ../04-evidence-t07-cluster/evidence_pack.json --out . \
    --analyst-id saif --stub-mode overclaim
```

## The stub was deliberately made to overclaim, and the artifacts say so

`--stub-mode overclaim` is not a hidden switch. The chosen mode is written into
the `session_open` entry, where the hash chain covers it, and stamped in
`session_manifest.json`:

```json
"model_mode": "stub",
"stub_mode": "overclaim"
```

Both renderings come from one function, so the manifest and the ledger cannot
describe this session two ways, and the mode is read off the adapter that
actually served the calls rather than declared alongside it. An overclaim
session is therefore self-identifying and cannot be presented as a faithful run.

## What it demonstrates

| Claim | Value in this session |
|---|---|
| The gate refused every attempt | `attempts` 8, `rejected_attempts` 8 |
| It refused for a *named* reason, not just "rejected" | `unresolvable_evidence_id` on sentence 0, every time |
| The refusal names the offending id | `'The subject entered this investigation as its seed [prov-9999].'; the pack does not carry prov-9999` |
| Both event types fired | 8 `gate_rejection` + 8 `verification_fail` in `event_counts` |
| The memo never became final | `status: DRAFT`, `verified: false`, no `memo_signature.json` exists |
| Repeated refusals are not counted as repeated corrections | `distinct_defects_caught` 1, against `rejected_attempts` 8 |
| A governance refusal is not a crash | The session **exits 0** with an intact chain: the control worked |

Compare with example 03, the same agent on the faithful path: 1 attempt,
`verification_pass` 1, no `gate_rejection`, and a signed memo.

## What this deliberately does not claim

- **This does not prove the memo agent's real-world error rate, and nothing
  here estimates one.** The agent was told to cite a nonexistent id by a
  deterministic stub. What is proven is narrow and worth exactly its own width:
  **the claim-to-evidence check fires**, it fires on the specific defect, it
  names the defect by a reason code that can be counted, and the memo is held at
  DRAFT until it stops firing.
- **`distinct_defects_caught` is 1, not 8.** The first version of this metric
  reported 8, counting one unchanged sentence rejected eight times as eight
  corrections, which inflated exactly the number ARCHITECTURE 7.2 showcases.
  Reporting it honestly makes the governance layer look *less* busy, which is
  the direction that matters.
- **`agent_revised_after_feedback` is false, and that is a stated limit of the
  stub.** Told exactly what was wrong, it re-sends the same draft. The revise
  loop's success path is covered by a purpose-built responder elsewhere in the
  test suite, not by this stub, and the difference is reported rather than
  smoothed over.
- **Eight attempts is the mandate's step ceiling doing its job**, not a tuned
  number chosen to look impressive.
- **A refusal count is not a security claim.** It shows this defect class is
  caught. It says nothing about defect classes nobody wrote a check for, and the
  prompt-injection detection in this system is incomplete by construction with
  four fixtures asserted as *undetected* to keep that honest.
