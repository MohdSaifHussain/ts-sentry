# STEP-08: Examples, Documentation, Release v1.0.0

**Project:** Trust & Safety Sentry | **Phase:** 8 of 8 | **Date:** 31 July 2026 (IST)
**Status:** Specified, not started
**Depends on:** STEP-01..07 complete

## 1. Objective
Curated example sessions, complete documentation set, and a reproducible
release. Exit criterion: fresh-clone quickstart under 10 minutes, offline.

## 2. Deliverables

| ID | Deliverable | Governing standard(s) |
|---|---|---|
| D1 | 4+ curated example sessions (one per agent focus; at least one showing a gate rejection and one showing a prompt-regression refusal) | delivery-engine examples precedent: all verified honest, negative paths showcased |
| D2 | Documentation set: README (with Honest Limits), QUICKSTART, USER_GUIDE, ARCHITECTURE (final), model card for LLM usage, data dictionary, decisions/ STEP-01..08 with outcome notes | Model-card practice (documented model deps, offline stub disclosure); Diataxis-informed doc split (tutorial/how-to/reference/explanation) |
| D3 | CHANGELOG finalized; version 1.0.0 tagged | SemVer 2.0.0; Keep a Changelog 1.1.0; annotated git tag |
| D4 | CI release workflow: lint, type, tests, ledger-verify on all example sessions, dataset rebuild determinism check; Docker image to GHCR on GitHub Release | Dockerfile mirrors CI (delivery-engine pattern); GITHUB_TOKEN only, no PAT |
| D5 | AI-collaboration release notes: build directed by Mohd Saif Hussain with Claude as AI collaborator, per documented STEP files | Honest AI-assisted framing (standing resume rule extended to releases) |
| D6 | Interview one-pager: docs/POSITIONING.md mapping every JD responsibility line to a repo artifact | Traceability matrix practice |

## 3. Requirements
- 3.1 Quickstart path: clone -> `pip install -e .` -> `ts-sentry build-dataset
  --seed 42` -> `ts-sentry run-session --agent triage` -> `ts-sentry report`;
  fully offline via stub adapter; timed on a clean machine, time recorded.
- 3.2 Every example directory: inputs manifest, ledger JSONL (verify-ledger
  clean), outputs, and a NOTES.md stating what the example demonstrates and
  what it deliberately does not claim.
- 3.3 POSITIONING.md table columns: JD line (verbatim), Sentry artifact,
  file path, metric or test proving it.
- 3.4 Windows notes section: known CMD/PowerShell/UTF-8 behaviors (carried
  from delivery-engine operational learnings).
- 3.5 Repository metadata: SPDX headers, LICENSE, CITATION.cff, topics set
  (trust-and-safety, ai-governance, agentic, python).

## 4. Out of Scope
- v1.1 roadmap items: free-form pivot exploration behind a gate, dashboard,
  concurrency, automated prompt optimization with contamination review.

## 5. Exit Checklist
- [ ] Fresh-clone offline quickstart timed under 10 minutes, recorded
- [ ] verify-ledger green across every example in CI
- [ ] Gate-rejection and regression-refusal examples present and documented
- [ ] POSITIONING.md covers 100% of the named capabilities
      *(restated from "JD responsibility lines" per decision 8.A; see the
      Outcome's deviation 1)*
- [ ] GHCR image published on release; tag annotated; CHANGELOG cut

## 6. Outcome

Shipped: D1 through D6, plus the approved additions (README badges, Mermaid
diagrams committed in-repo, community files, supply-chain hardening, signed
release artifacts). 1,230 tests pass; `mypy --strict` and `ruff` clean on 164
files; coverage 93.16% against a 90% floor.

Both review stops were observed. Stop 1 passed with one fix; stop 2 halted
before any tag existed, as contracted.

### Exit checklist, evidenced

- [x] **Fresh-clone offline quickstart timed under 10 minutes, recorded.**
      2.1 to 2.3 minutes, 21 to 23% of budget. Conditions recorded in
      `docs/quickstart-timing.md`: Python 3.14 while CI pins 3.12, clone from
      local disk because pushes are checkpoint-gated, and no clean-machine
      number claimed. The install is 77% of the total.
- [x] **verify-ledger green across every example in CI.** The `examples` job
      runs it over all six committed chains, again against each stored anchor,
      and asserts a truncated copy exits 6. It counts what it found and fails if
      it is not 6, because a loop over a glob that matched nothing passes
      silently.
- [x] **Gate-rejection and regression-refusal examples present and documented.**
      `05-overclaim-refused` and `06-prompt-eval-refused`, plus a third refusal
      nobody specified: `08-firewall-real-comments`, where a published research
      corpus was rejected outright.
- [x] **POSITIONING.md covers 100% of the named capabilities.** Restated from
      the original wording; see deviation 1.
- [ ] **GHCR image published on release; tag annotated; CHANGELOG cut.**
      CHANGELOG cut to `[1.0.0]`. The tag and the publish are Stage D, taken in
      two steps per decision 8.D and checkpoint-gated on Saif's explicit go.

### The four decisions taken before implementation

Raised as numbered questions before any code, because each changed the shape of
the work. Recorded in `docs/DECISIONS.md` as entries 8.1 through 8.4: the JD
amendment, `--stub-mode` as session provenance, `uv.lock` as the single locking
artifact, and cutting a release candidate before the release.

### Deviations, recorded

1. **D6 is amended: no JD is quoted, because none exists.** Requirement 3.3 asks
   for JD responsibility lines "verbatim". No job description is committed to
   this repository and none is coming; this is a personal project built against
   the responsibility profile of a role family, not against a posting, and
   quoting lines nobody has would be inventing a source. `docs/POSITIONING.md`
   is therefore a **capability traceability matrix** whose left column is
   labelled representative rather than quoted, with columns 2 to 4 exactly as
   3.3 specifies. The exit-checklist line is restated as "covers 100% of the
   named capabilities". Saif's decision 8.A. Five capabilities have no honest
   artifact and get rows saying so, because a stated gap beats a stretched
   claim.
2. **Eight examples, not four, and one of them has no ledger.** D1 asks for "4+"
   including a gate rejection and a regression refusal. Folding those two into
   one example would make each half illegible, and a second evidence session was
   needed anyway: a memo session's id derives from its pack's case and subject,
   so drafting both the faithful and the overclaiming memo from one pack would
   have given two materially different sessions one id, which is STEP-04's "two
   sessions shared an id" defect. Avoided by investigating a different case
   rather than by hand-setting an id, and asserted by test.
   `08-firewall-real-comments` deviates from **3.2** deliberately: no ledger,
   because the input firewall is a library component and no session runs.
   Inventing a CLI verb to make an example look uniform would be adding product
   surface to serve a demo, and manufacturing a session around a component call
   would mean ledgering governance events that never happened.
3. **`run-session --stub-mode` is a CLI surface STEP-08 does not enumerate**,
   added on the STEP-04 and STEP-05 precedent: the phase's own deliverable
   needed an artifact nothing runnable by hand would otherwise produce. Saif's
   decision 8.B, with two conditions, both met.
4. **`PROJECT_CHARTER.md` was never written.** ARCHITECTURE section 9 names it
   in the docs set; D2's documentation list does not. Inventing a charter at
   release time to satisfy a bullet would produce a document with no authority
   behind it. Recorded in ARCHITECTURE section 10 as an unshipped item rather
   than left looking shipped.
5. **No byte-diff regeneration job in CI**, which the approved plan called for.
   Measured across two runs: only `ranked_queue.json` is byte-identical.
   Everything else carries real timestamps, and a ledger that was byte-stable
   across runs would be a worse artifact rather than a better one. The tests
   check the invariants that are real instead.
6. **The external dataset entered as a bounded firewall demonstration** rather
   than as a committed deliverable, after the licence search Saif asked for.

### The external-dataset decision, and what the search actually found

The structural finding first, because it is the reusable part: **Twitter's
Developer Agreement content-redistribution clause is why essentially the whole
coordinated-behaviour research family publishes ID-only datasets.**

| Candidate | Verdict |
|---|---|
| TwiBot-22 (NeurIPS 2022) | MIT *code*, gated access, redistribution prohibited |
| FiveThirtyEight IRA tweets | **No LICENSE file at all** (404). Redistributing would assert a right nobody granted |
| Cresci-2017 via the Bot Repository | Request-gated |
| Zenodo 7391372, "Fake accounts activity" | Genuinely CC BY 4.0, but IDs only: no text, no features. Needs the paid X API to hydrate, which breaks offline-first absolutely |

Nothing in UCI, Zenodo or Dataverse pairs a permissive licence with
account-level features plus coordination labels.

The one that cleared every bar is the **UCI YouTube Spam Collection** (CC BY
4.0, DOI 10.24432/C58885): 1,956 real comments with actual text, 330 KB, no
hydration, fully offline. Its honest scope is narrow and is stated in the
example's NOTES: no account metadata so no pivot can run, no views so no VVR,
binary spam/ham rather than T-01..T-07 so mapping it would be inventing labels.

**The first thing real data did was get refused.** Three `COMMENT_ID` values
appear twice, so 1,956 published rows carry 1,953 distinct ids, and the
firewall's uniqueness invariant rejected the corpus outright: "a citation that
resolves to two records is not a citation". The synthetic generator has never
produced a duplicate id, because it assigns them from a counter. Zero injection
signals were raised across the 1,953, and that is reported as what it is:
evidence there was nothing of that kind to find in a 2013 to 2015 commercial
spam corpus, not evidence the detector works.

### Defects found by running it, not by inspection

Six, none caught by a test that already existed.

1. **The regeneration script deleted every hand-written `NOTES.md`.** `run()`
   removed directories holding documentation the script does not produce.
   Caught by `test_every_example_carries_its_inputs_manifest_and_notes`, which
   is what that test is for.
2. **`write_inputs` stamped a dataset seed, scale and analyst id into example
   08**, which opens no dataset and binds no analyst: two false statements in a
   *provenance* file, in the phase whose theme is that provenance files must not
   assert what did not happen.
3. **The container entrypoint did not exist.** A virtualenv is not relocatable:
   uv writes the interpreter's absolute path into every console-script shebang,
   so a venv built at `/build/.venv` and copied elsewhere yields
   `#!/build/.venv/bin/python`. The error reads as something else entirely,
   naming the script rather than the interpreter that is actually missing.
4. **`git_sha()` crashed when git was absent**, while its own docstring had
   promised for seven phases that an unavailable git returned
   `UNKNOWN_GIT_SHA`. `subprocess.run` raises `FileNotFoundError` when the
   executable does not exist. It survived that long because every environment it
   had ever run in had git installed; the runtime image is the first that does
   not, deliberately. A claim wider than the behaviour, fixed in the code
   because a released container is exactly where a manifest should record that
   it could not take a provenance stamp and carry on.
5. **`eval-prompts` refused a non-empty output directory** when the example's
   registry was first placed inside it. The guard was right and the layout was
   wrong.
6. **The stale example count reached a third document.** See below.

### The count that got away, and what it cost

The adversarial self-review found that README and `examples/README.md` both said
"seven" example ledgers when six carry one, and corrected both. It **missed a
third occurrence** in `docs/POSITIONING.md`, because it checked the two files it
happened to think of instead of searching for the claim, and then reported the
count as fixed. Saif found the third by reading at the review stop.

That is worth recording as a method failure rather than a typo. A number
restated in prose in several places is exactly the fact that drifts silently:
nothing breaks, every test passes, and one file is simply wrong.
`test_the_number_of_examples_carrying_a_ledger_is_what_the_docs_say` now locates
the claim by pattern across every committed markdown file rather than against a
list of paths someone has to remember to extend, and it was verified to fail on
the reintroduced defect rather than pass vacuously.

### Documentation defects found by measuring rather than re-reading

- **DECISIONS said "the five remaining members of `ring_t02_000`"** and
  STEP-04's Outcome said "the five members". True at 3 of 8, false since entry
  7.13 took recovery to 4 of 8, which entry 7.14 records correctly. The record
  disagreed with itself by one for the whole of Phase 7. Found by running
  `recovery_for_pack` against a committed pack instead of quoting a prior
  sentence. Both corrected, the four now named, pinned by test.
- **The data dictionary was audited rather than assumed current**:
  `information_schema` read out of a real seed-42 build and compared column by
  column against the file. Seven tables, zero undocumented columns, and
  `engagement_event.session_id` still reserved and NULL on every row.

### Supply chain: four recorded gaps closed, three left open

Closed: no SBOM, no hash-locked lockfile, no dependency scanning, and the
AnalystKit tag pin. Open and stated: no upper bounds, the unpinned and
unexercised `live` extra, and that the CycloneDX document describes the Python
environment while the image's own attestation covers its layers. The gap list in
`docs/DECISIONS.md` strikes through what closed rather than deleting it, because
a list showing only what is currently broken teaches nothing about how the
posture got here.

**Signed artifacts with `GITHUB_TOKEN` only: achievable**, through Sigstore
keyless signing against the workflow's OIDC identity. **A GPG-signed tag is
not**, and that is stated rather than implied: it needs a private key in a
secret, which is the credential class D4 exists to avoid. D3 requires an
*annotated* tag, and annotated is not signed.

### Honest limits

- **The release workflow's first real execution is the `v1.0.0-rc.1` run.**
  Every job was written against current official documentation and is
  SHA-pinned, and the Dockerfile and CI jobs were exercised locally, but the
  publish path itself (GHCR push, attestation, release-asset upload) has not
  run. That is precisely why decision 8.D cuts a pre-release first.
- **`artifact-metadata: write` is the one permission not exercised.**
  `actions/attest`'s README lists it alongside `id-token` and `attestations`,
  while GitHub's own GHCR publishing example does not. The documented example's
  permission set was used. If the rc run fails on a permission, that is the
  cheapest possible place to find out.
- **The 3.12 gap persists.** Local Python is 3.14 and CI pins 3.12, so every
  local green result in this phase is a 3.14 result, as in every phase since
  STEP-02.
- **Every honest limit from STEP-01 through STEP-07 is carried forward
  unchanged.** None was narrowed at release and none was deleted for being
  inconvenient. The recorded-unmet t02 traversal obligation is named in the
  release notes roadmap with its blocker, so it visibly has a home.
