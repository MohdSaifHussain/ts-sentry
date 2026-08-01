# 04: a T-07 coordinated influence cluster

**What this is.** A second ASSEMBLE session, on a different threat class, so the
examples set shows the investigation path on more than one shape of network. It
also supplies the evidence pack that example 05 drafts from, which is why it is
a separate session with its own case id: a memo session's identity derives from
its pack's case and subject, so drafting both the faithful and the overclaiming
memo from one pack would have given two different sessions the same session id.

Reproduce:

```bash
ts-sentry run-session --agent evidence --seed-dataset build --out . \
    --analyst-id saif --case case-0001 --subject t07_chan_000
```

## What it demonstrates

| Claim | Where to see it |
|---|---|
| The same strategy works on a different network shape | 5 nodes, 18 edges, 21 provenance records |
| Pivot variety is not specific to one case | Same four kinds: `shared_metadata` 14, `infra_overlap` 4, `account_link` 1, `engagement_edge` 1 |
| Only 1 of 21 hops came back empty here, against 4 in example 02 | `evidence_pack.json` provenance `row_count` |
| Chain intact and anchored | `verify-ledger --expect-head-from session_manifest.json` exits 0 |

## The recovery result, stated exactly

**4 recovered. `ring_t07_000` holds 11 members besides the subject, of which
only 8 are structurally reachable.** So this is 4 of 11 of the ring, and 4 of 8
of what a pack can hold: 0.36 and 0.50.

Both numbers are reported because reporting only the first would blame the
strategy for a structural bound. Three of the eleven members are *comments*, and
a comment enters an evidence pack as a timeline event rather than as a node, so
no amount of pivoting recovers them into the node set. Reporting 0.36 without
saying 0.50 was the maximum reachable invites the reader to read a ceiling as a
failure.

Unrecovered: `t07_chan_001`, `t07_chan_002`, `t07_vid_001`, `t07_vid_002`, and
the three comments `t07_cmt_t07_acct_000/001/002`.

## What this deliberately does not claim

- **Recovery is flat at 5, 10 and 20 pivots here too.** T-06 is the only threat
  class on this build where the budget axis carries information. That is
  recorded in the STEP-07 Outcome rather than engineered away.
- **The structural ceiling is a property of the pack model, not a measured limit
  of investigation.** It says what this artifact can hold, not what an analyst
  could find.
- Everything in `02-evidence-t02-ring`'s "does not claim" section applies here
  unchanged: scripted reviewer, untested agent competence, synthetic data,
  ground truth that exists only because the generator planted it.
