# 08: the input firewall over real, third-party comment text

**What this is.** The only example in this repository that touches data this
project did not generate. 1,956 real YouTube comments from the UCI YouTube Spam
Collection (CC BY 4.0, DOI [10.24432/C58885](https://doi.org/10.24432/C58885);
see [`../data/youtube-spam-collection/ATTRIBUTION.md`](../data/youtube-spam-collection/ATTRIBUTION.md))
pushed through `apply_firewall`.

It exists to close one specific gap. Every byte the firewall had previously been
shown was written either by this project's own generator or by its own
adversarial fixtures. Text written by strangers, in 2013, for reasons entirely
unrelated to this system, is a different kind of input.

**This example is shaped differently from 01 to 07, deliberately.** It has no
ledger, no session id and no chain, because no session runs: the input firewall
is a library component and this project ships no CLI verb that runs it on its
own. Inventing one purely to make an example look uniform would be adding
product surface to serve a demo, and manufacturing a session around a component
call would mean ledgering governance events that never happened. Requirement 3.2
asks every example directory for a verify-ledger-clean ledger, and this one
deviates from it on purpose. Reproduce it with `python examples/regenerate.py`.

## The first thing real data did was get refused

The corpus was rejected outright:

```
duplicate record_id 'LneaDw26bFvPh9xBHNw1btQoyP60ay_WWthtvXCx37s':
a citation that resolves to two records is not a citation
```

Three `COMMENT_ID` values appear twice, so the 1,956 published rows carry
**1,953 distinct ids**. In every case the duplicated rows are byte-identical
(same author, same content, same label), so dropping the extras is lossless,
and the example asserts that rather than assuming it: if two rows ever share an
id and differ in content, it refuses to run rather than quietly choosing which
comment to believe.

This is the finding worth having. The synthetic generator has never produced a
duplicate id, because it assigns them from a counter. A widely-cited published
research corpus does, and the firewall's uniqueness invariant is what surfaced
it, in the first minute of the first contact with real data.

The refusal is also asserted in the regeneration script: if a future version of
the corpus fixes the duplicates, the script raises and this file has to be
rewritten rather than quietly outliving its own truth.

## What the firewall did with the remaining 1,953

| Result | Value |
|---|---|
| Records fenced into one inert block | 1,953 (1,003 labelled spam, 950 legitimate) |
| Pattern set | `1.0.0`, hash `3fd538cb6c10700d...` |
| Block nonce, derived from the content it fences | `c3ef19b6d90bae5b...` |
| Injection signals raised | **0** |
| Records redacted | **0** |

`sample_block.txt` shows the model-facing rendering for six of them, chosen to
be awkward rather than bland: every one contains a `"` the encoder has to
escape, and several carry a zero-width no-break space (U+FEFF) which is
preserved verbatim inside the JSON encoding rather than stripped.

## What this deliberately does not claim

- **Zero signals is not evidence the detector works.** It is evidence there was
  nothing of that kind to find. This corpus is commercial comment spam from 2013
  to 2015 ("check out my channel", "subscribe me"), collected years before
  prompt injection against LLM applications existed as a technique. A detector
  scoring zero on a corpus containing zero instances has demonstrated nothing
  about its precision or its recall, and reporting the zero as a pass would be
  exactly the inversion this project keeps refusing.
- **What genuinely was exercised on real text is the structural half**: the
  content-derived fence, the JSON encoding, the line-breaker escaping, the
  record-id uniqueness invariant. Those are the load-bearing controls, and they
  ran over 1,953 strings nobody here wrote. The pattern set is the detective
  half and is incomplete by construction, with four adversarial fixtures asserted
  as **undetected** elsewhere in the test suite to keep that honest.
- **This data feeds nothing else, and cannot.** No account metadata,
  registration attributes or infrastructure hints, so no pivot template can run
  and no evidence pack is possible. No view or engagement events, so it cannot
  reach the VVR lens. Binary spam/ham labels rather than T-01 through T-07, so
  mapping it onto this project's threat classes would be inventing labels; the
  eval set stays synthetic. No planted rings, so no recovery metric.
- **1,956 comments from five videos in 2013 to 2015 is not a sample of
  anything.** It is not representative of YouTube then and certainly not now.
- **The dataset's authors are not affiliated with this project** and have not
  endorsed it. Their CSVs are redistributed here unmodified; the de-duplication
  happens in this example's runner, not in the committed data.
