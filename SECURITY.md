# Security policy

## Scope, stated first

Trust & Safety Sentry is a **portfolio and research project**. It runs offline
against synthetic data, holds no user data, exposes no network service, and
requires no credentials to build, test or run. It is not deployed anywhere and
there is no production instance to compromise.

That is the honest scope, and it should set your expectations for what a report
here can achieve. There is no bug bounty and no SLA.

## Supported versions

| Version | Supported |
|---|---|
| 1.0.x | Yes |
| < 1.0 | No, pre-release development only |

## Reporting a vulnerability

Use **GitHub's private vulnerability reporting** on this repository
(Security → Report a vulnerability). That keeps the report private until there
is something to disclose.

Please do not open a public issue for anything you believe is exploitable.

Expect an acknowledgement within a week. This is a personal project maintained
by one person, so that is a realistic figure rather than a generous one.

## What is genuinely interesting here

The parts worth attacking are the ones this project makes claims about. A finding
against any of these is valuable even if it is not "a vulnerability" in the
conventional sense, because the claim would then be wider than the behaviour,
which is the failure this repository is built to avoid:

- **Reaching `Consequence.ENFORCE` through any agent path.** The documented claim
  is narrow and exact: no agent action can reach the ENFORCE gate. It is not that
  the enum member cannot be named.
- **Getting case content treated as instructions.** The input firewall fences
  content behind a token derived from the content itself. Closing that fence
  early should be a preimage problem. Forging a redaction marker should fail
  because markers are bound to the block nonce.
- **Forging a record inside a fenced block.** A hypothesis property already found
  one route: `str.splitlines` breaks on U+2028, U+2029, NEL, VT, FF, FS, GS and
  RS, and JSON escapes none of them. That is fixed. Another route would be a real
  finding.
- **Reaching `sealed._labels` from agent or orchestrator code.** No `DataScope`
  member resolves to it and an import-graph test enforces the boundary
  transitively.
- **Making the ledger accept a chain it should not**, or making a tampered entry
  verify.
- **Getting a memo past the RECOMMEND gate with a claim that resolves to
  nothing**, or a citation quoting text the corpus does not contain.
- **Composing SQL through the pivot surface.** The agent names a template and
  supplies typed parameters; no identifier reaches the query from a caller.

## Known limits, already documented

These are **not** vulnerabilities. They are recorded limitations, and reporting
them tells us nothing we have not written down:

- **Tail truncation is invisible to chain verification alone.** A truncated
  ledger is a shorter chain whose every link still recomputes. Only the stored
  anchor catches it, and an anchor co-located with the ledger it describes can be
  rewritten by anyone who can truncate that ledger. Both halves are asserted by
  test.
- **Prompt-injection detection is incomplete by construction.** Four adversarial
  fixtures are asserted as *undetected* in the test suite, deliberately. The
  load-bearing controls are structural, not the pattern set. A novel *class* of
  bypass against the structural controls is interesting; another undetected
  phrase is already accounted for.
- **A signature proves integrity, not identity.** There is no analyst
  authentication and none is claimed.
- **`reviewer_kind` proves which mechanism decided, not who.**
- **The `live` extra is unpinned and has never been executed.**

## Dependencies

Direct dependencies are few and deliberately so: the governance core touches no
third-party code beyond the store, and the one vendor client is imported inside
the call so an offline install never loads it. A test parses every module with
`ast` and fails on any module-scope import of it.

`uv.lock` pins every dependency including the exact `analystkit` git revision.
Dependabot and CodeQL run on this repository.

If you find a compromised or malicious dependency, that is in scope and worth
reporting privately.
