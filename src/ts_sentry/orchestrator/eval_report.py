# SPDX-License-Identifier: MIT
"""D6: the eval report artifact, in Markdown and JSON.

STEP-06 D6 asks for a report "stamped with dataset seed, eval-set hash, model
adapter id", under the standard "reproducible evaluation practice". The stamp is
the deliverable as much as the numbers are: an evaluation whose inputs cannot be
named is one nobody can re-run, and this project's whole target is claims a
reader can check.

What the stamp covers, and why each field is on it
---------------------------------------------------
======================== ==================================================
Field                    What it pins
======================== ==================================================
``dataset_seed``         which synthetic build the items came from
``dataset_scale``        the same, and it is not implied by the seed
``items_sha256``         the exact items, independent of file formatting
``labels_sha256``        the exact answers they were graded against
``incumbent``/``candidate`` digest  which two prompt versions ran
``adapter_id``/``model_id``         what answered
``bootstrap_seed``       which resamples produced the intervals
``tolerances_sha256``    the limits the verdict was reached under
``git_sha``              the code that computed all of it
======================== ==================================================

Take any one of them away and the report describes a run nobody can reproduce.
``bootstrap_seed`` is the one most easily forgotten and the one that makes the
intervals reproducible rather than merely plausible.

The caveat travels in the artifact, not in the docs
-----------------------------------------------------
Precision measured on this eval set is **not** a deployment estimate: the set
over-samples rare classes against a >97% benign platform, and precision moves
with prevalence while recall does not. That sentence is written into both the
Markdown and the JSON rather than left in ``docs/``, on the reasoning DECISIONS
4.9 recorded for the recovery ceiling: reporting a number without the bound that
makes it readable invites the reader to draw the conclusion the number does not
support, and a reader with the artifact in hand does not have the docs open.

The same applies to the resolution figures. A per-class support of 4 is printed
beside every recall, and the minimum detectable drop is printed beside every
interval, so "why did the gate refuse a candidate that looks fine" is answerable
from the artifact alone.

No per-item rows
----------------
The report is the boundary artifact of STEP-06 3.2. It carries per-class counts
and intervals and nothing per item, because a report is what leaves the eval
boundary and therefore what a prompt author may read. A per-item table here
would hand back the answer key the rest of the phase is built to withhold.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from ts_sentry.data.enums import ThreatClass
from ts_sentry.orchestrator.prompt_eval import EvalReport
from ts_sentry.orchestrator.regression_gate import (
    GateVerdict,
    Tolerances,
    minimum_detectable_drop,
)
from ts_sentry.provenance import git_sha

__all__ = [
    "REPORT_JSON",
    "REPORT_MD",
    "PRECISION_CAVEAT",
    "RESOLUTION_CAVEAT",
    "write_eval_report",
]

REPORT_JSON = "eval_report.json"
REPORT_MD = "eval_report.md"

PRECISION_CAVEAT = (
    "Precision here is not a deployment estimate. This eval set deliberately "
    "over-samples rare classes against a platform that is more than 97% benign "
    "(ARCHITECTURE 6.1). Per-class recall is a within-class quantity and is "
    "unaffected by that choice; precision moves with prevalence and is not."
)

RESOLUTION_CAVEAT = (
    "This gate detects a class collapse, not a few-point drift. The generator "
    "plants 4 to 12 entities per threat class regardless of --scale, so a "
    "class's recall moves in steps of a quarter to a twelfth and no tolerance "
    "setting can resolve anything finer. The bound is a property of the data, "
    "not of the gate."
)


@dataclass(frozen=True, slots=True)
class ReportStamp:
    """Everything needed to re-run the evaluation this report describes."""

    dataset_seed: int
    dataset_scale: int
    tolerances: Tolerances
    git_sha: str

    def to_json_object(self) -> dict[str, object]:
        return {
            "dataset_seed": self.dataset_seed,
            "dataset_scale": self.dataset_scale,
            "git_sha": self.git_sha,
            **self.tolerances.to_json_object(),
        }


def _percent(value: float) -> str:
    return f"{value:.3f}"


def render_markdown(
    report: EvalReport, verdict: GateVerdict, stamp: ReportStamp, *, task: str
) -> str:
    """The human-readable half.

    Written so the verdict is legible before any table: a reader who opens this
    to find out whether a prompt shipped should not have to parse a confusion
    matrix first.
    """
    resolution = minimum_detectable_drop(report)
    decision = "ACTIVATABLE" if verdict.activatable else "REFUSED"

    lines: list[str] = [
        f"# Prompt evaluation: {task}",
        "",
        f"**Decision: {decision}**",
        "",
        f"- Incumbent: `{report.incumbent.content_digest[:16]}`",
        f"- Candidate: `{report.candidate.content_digest[:16]}`",
        f"- Items: {report.item_count}",
        f"- Answered by: `{report.adapter_id}` / `{report.model_id}`",
        "",
    ]

    if verdict.breaches:
        lines += ["## Why activation was refused", ""]
        for breach in verdict.breaches:
            where = "overall" if breach.threat_class is None else breach.threat_class.value
            lines.append(f"- **{breach.code.value}** ({where}): {breach.detail}")
        lines.append("")
    else:
        lines += [
            "No monitored metric breached its declared tolerance, and every "
            "per-class interval excluded a drop beyond it.",
            "",
        ]

    lines += [
        "## Per-class results",
        "",
        "| class | support | incumbent recall | candidate recall | delta | "
        "95% CI | min detectable drop |",
        "|---|---|---|---|---|---|---|",
    ]
    for delta in report.deltas:
        floor = resolution.get(delta.threat_class)
        lines.append(
            f"| {delta.threat_class.value} | {delta.support} | "
            f"{_percent(delta.incumbent_recall)} | {_percent(delta.candidate_recall)} | "
            f"{delta.delta:+.3f} | [{delta.lower:+.3f}, {delta.upper:+.3f}] | "
            f"{'n/a' if floor is None else _percent(floor)} |"
        )

    lines += [
        "",
        "## Precision and F1, per class",
        "",
        "| class | support | incumbent precision | incumbent F1 | candidate precision "
        "| candidate F1 |",
        "|---|---|---|---|---|---|",
    ]
    for member in ThreatClass:
        incumbent = report.incumbent.counts[member]
        candidate = report.candidate.counts[member]
        if not incumbent.support:
            continue
        lines.append(
            f"| {member.value} | {incumbent.support} | {_percent(incumbent.precision)} | "
            f"{_percent(incumbent.f1)} | {_percent(candidate.precision)} | "
            f"{_percent(candidate.f1)} |"
        )

    lines += [
        "",
        f"Macro F1: incumbent {_percent(report.incumbent.macro_f1())}, "
        f"candidate {_percent(report.candidate.macro_f1())}.",
        "",
        f"Unparseable answers: incumbent {report.incumbent.unparseable}, "
        f"candidate {report.candidate.unparseable}.",
        "",
        "## How to read these numbers",
        "",
        f"- {PRECISION_CAVEAT}",
        f"- {RESOLUTION_CAVEAT}",
        "",
        "## Reproducing this run",
        "",
        f"- Dataset seed: `{stamp.dataset_seed}`, scale `{stamp.dataset_scale}`",
        f"- Eval items: `{report.items_sha256}`",
        f"- Eval labels: `{report.labels_sha256}`",
        f"- Bootstrap seed: `{report.bootstrap_seed}` over {report.deltas[0].resamples} resamples",
        f"- Tolerances: `{verdict.tolerances_sha256}`",
        f"- Code: `{stamp.git_sha}`",
        "",
    ]
    return "\n".join(lines)


def write_eval_report(
    out_dir: Path,
    report: EvalReport,
    verdict: GateVerdict,
    *,
    dataset_seed: int,
    dataset_scale: int,
    tolerances: Tolerances,
) -> tuple[Path, Path]:
    """Write both halves and return their paths.

    The git SHA is read here rather than passed in, because it is a property of
    the code doing the writing and there is no caller better placed to know it.
    Everything else is supplied, so nothing about the evaluation itself is
    discovered by this function.
    """
    stamp = ReportStamp(
        dataset_seed=dataset_seed,
        dataset_scale=dataset_scale,
        tolerances=tolerances,
        git_sha=git_sha(),
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    payload: dict[str, object] = {
        "stamp": stamp.to_json_object(),
        "verdict": verdict.to_json_object(),
        "report": report.to_json_object(),
        "resolution": {
            member.value: round(value, 6)
            for member, value in minimum_detectable_drop(report).items()
        },
        "caveats": {
            "precision": PRECISION_CAVEAT,
            "resolution": RESOLUTION_CAVEAT,
        },
    }

    json_path = out_dir / REPORT_JSON
    md_path = out_dir / REPORT_MD
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    md_path.write_text(
        render_markdown(report, verdict, stamp, task=report.task), encoding="utf-8", newline="\n"
    )
    return md_path, json_path
