# 02: reconstructing the T-02 fake-engagement ring

**What this is.** One ASSEMBLE session investigating `t02_chan_000_000`, a
channel planted by the T-02 fake-engagement generator. The agent proposes a
pivot from a fixed vocabulary of five, the orchestrator validates it, the
analyst approves or rejects, and only then does a parameterized query run.
Twenty hops.

Reproduce:

```bash
ts-sentry run-session --agent evidence --seed-dataset build --out . \
    --analyst-id saif --case case-0000 --subject t02_chan_000_000
```

## What it demonstrates

| Claim | Where to see it |
|---|---|
| The agent never writes SQL | `evidence_pack.json` provenance: every hop names a `query_template_id` and its `template_sha256`; the agent supplies typed parameters only |
| The investigation traverses and varies its questions | Four distinct pivot kinds across 21 provenance records: `shared_metadata` 14, `infra_overlap` 4, `account_link` 1, `engagement_edge` 1 |
| Provenance is complete on every record | All 21 carry `source_table`, `template_sha256`, `param_hash`, `retrieval_ts_ist`, `row_count` |
| Every hop is a ledgered human decision | 20 `human_decision` entries, each carrying `reviewer_kind` inside the hashed payload |
| The ring is genuinely reconstructed | 5 nodes, 20 edges: the subject channel plus `t02_acct_000_000/001/002` and `t02_vid_000_000`, linked through the planted device fingerprint `devhint_t02_000` and signup IP bucket `ipb_t02_000` |
| An empty pivot is not a dead end | 4 of 21 hops returned `row_count` 0 and the work list simply advanced to the next question |

## The recovery result, stated exactly

**4 of 8 ring members, flat at pivot budgets 5, 10 and 20.**

`ring_t02_000` holds 8 members besides the subject and all 8 are structurally
reachable (they are node kinds a pack can hold). The four recovered are the
accounts and the video reachable through a shared registration value. The four
**not** recovered are named, because a number without them invites the reader to
guess: `t02_chan_000_001`, `t02_chan_000_002`, `t02_vid_000_001`,
`t02_vid_000_002`.

## What this deliberately does not claim

- **This is a recorded-unmet obligation, not a success.** STEP-04 named this
  exact subject as the concrete target for STEP-07: recovery at 20 pivots
  strictly greater than at 5. It went from 3 of 8 to 4 of 8 and **did not become
  budget-sensitive**. The obligation is recorded as unmet in the STEP-07 Outcome
  and in DECISIONS 7.14 rather than counted as discharged because a different
  threat class (T-06) moved. It is carried into the v1.1 roadmap as the
  traversal-enrichment task, whose blocker is named: `TEMPORAL_CORRELATION`
  needs an `anchor_epoch_ms` and no timestamp appears in the prompt the agent
  reads, so proposing it today would mean inventing a parameter.
- **The plateau is a bounded limit of a metadata-pivot strategy, not a defect
  being hidden.** The four missed members are connected by looser evidence:
  behavioural co-occurrence, temporal proximity, weaker attribute overlap. None
  of those is expressible in the pivots this strategy can ask about.
- **Recovery is measured against planted ground truth on synthetic data.** It is
  not a claim about real investigations, and the sample here is one case.
- **`reviewer_kind` is `scripted` on all 20 hops.** No human approved anything
  in this session. The field records which *mechanism* decided, not who, and
  no output of this system renders a scripted approval as a human one. Real
  analyst authentication is out of scope.
- **The agent's competence is untested.** Every pivot here was chosen by a
  deterministic stub. What is tested is that a proposal is checked rather than
  trusted, that nothing runs without an approval, and that every hop is
  traceable. None of it is evidence about how a model would investigate.
- **The pack proves internal consistency, not correspondence to the world.** The
  ASSEMBLE gate validates the artifact. That the subject exists at all is a
  separate boundary check (`subject_check`), added in STEP-04 after a session
  produced a fully valid audit trail for an investigation of an entity that was
  never there.
