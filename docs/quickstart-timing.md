# Quickstart timing

STEP-08's exit criterion: **fresh-clone offline quickstart under 10 minutes,
timed and recorded.**

## Result

**Met.** The full documented path completes in **2.1 to 2.3 minutes**.

| Path | Total | Against the 10-minute criterion |
|---|---|---|
| `pip install -e ".[dev]"`, warm pip cache | **139.9 s** (2.3 min) | 23% of budget |
| `pip install -e ".[dev]" --no-cache-dir` | **123.7 s** (2.1 min) | 21% of budget |
| `uv sync --frozen --extra dev` (install step only) | **11.1 s** | vs 94.5 to 108.0 s for pip |

## The conditions, because a timing without them is not a measurement

| | |
|---|---|
| Machine | Saif's development machine: Windows 11 (10.0.26200), Intel64 Family 6 Model 183 |
| Interpreter | **Python 3.14.0** |
| Clone source | The **local repository on disk**, not GitHub |
| Network | Available. Required by the install step only. |
| Date | 2 August 2026 |

Three of those need saying out loud rather than leaving in a table.

**This is a 3.14 result and CI pins 3.12.** Every local green result in this
project has been a 3.14 result since STEP-02, and that limit is carried here
rather than dropped because it is inconvenient. The install step is where a
version difference would most plausibly show up, since it resolves and builds
wheels.

**The clone came from local disk, so clone time excludes network transfer.**
It measured 0.45 to 0.47 s, which is essentially the cost of copying files. A
real `git clone` from GitHub would add the download, which for this repository
is a few MB. Cloning from origin was not possible at the time of measurement:
pushes in this project are checkpoint-gated and the release commits had not been
pushed. Adding even a generous minute for a real clone leaves the total at about
a third of the budget.

**No clean-machine number is claimed, because none was measured.** These figures
are from a machine that has built this project many times. What is genuinely
excluded by `--no-cache-dir` is pip's HTTP cache, and what is not excluded is
everything else that machine has warm: the OS file cache, an installed Python,
an existing git.

## Per-step, warm cache

| Step | Seconds |
|---|---|
| `git clone` (local source) | 0.47 |
| `python -m venv .venv` | 10.75 |
| `pip install -e ".[dev]"` | **107.96** |
| `ts-sentry build-dataset --seed 42 --scale 1` | 13.11 |
| `ts-sentry run-session --agent triage` | 1.22 |
| `ts-sentry run-session --agent evidence` | 1.41 |
| `ts-sentry run-session --agent memo` | 1.17 |
| `ts-sentry sign-memo` | 0.99 |
| `ts-sentry report` | 1.76 |
| `ts-sentry verify-ledger --expect-head-from` | 1.04 |
| **Total** | **139.9** |

Every step exited 0.

**The install dominates: 77% of the total.** Everything the project actually
does, from generating a synthetic platform to reconstructing a ring, drafting
and signing a memo, and producing a measurement report with a bootstrap
cross-check, takes about 21 seconds combined.

## Two findings worth recording rather than smoothing

**The cold-cache run was *faster* than the warm one** (123.7 s against 139.9 s).
That is the opposite of the expected direction, and the honest reading is not
that disabling the cache helps. It is that **the difference between these two
runs is within run-to-run noise on this machine**, so the pip cache is not a
material factor at this scale and neither number should be presented as
attributable to it. Reporting the pair and saying so is more useful than
reporting whichever one flatters the criterion.

**`uv sync --frozen` installs in 11.1 s against pip's 94.5 to 108.0 s**, roughly
nine times faster, which is one of the reasons the lockfile path was chosen. It
is measured separately rather than folded into the headline because it needs
`uv` as a prerequisite that the documented fast path does not, and the two paths
are documented as two.

## Reproducing this

The harness is not committed. It is thirty lines that clone, create a venv,
install, and time each documented command with `time.perf_counter`, and
committing a bespoke benchmark would invite it to be maintained and trusted
beyond what it is. The commands it runs are exactly the ones in
[QUICKSTART.md](../QUICKSTART.md), in that order, so anyone can time them with a
stopwatch and get the same shape.

## What this does not measure

- **Any machine other than this one.** No cloud runner, no cold Windows install,
  no Linux, no macOS.
- **Time to understand the output.** This is time to *produce* the artifacts. The
  measurement report alone carries ten honest limits that deserve longer than
  1.76 seconds.
- **A first-time reader's path.** They will read while they run. The criterion is
  about the tooling not being in the way, not about comprehension.
