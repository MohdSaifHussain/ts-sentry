# SPDX-License-Identifier: MIT
"""STEP-07 D4: the measurement report, in Markdown and HTML.

Stamped so that every number in it can be traced back to what produced it
(D4's "reproducible-research stamping"): dataset seed and scale, the dataset
digest, the git SHA of the code, the policy corpus version and hash, and a
pointer to the active prompt version per task.

The report's job is to be hard to misread
------------------------------------------
Every section is written so that lifting a number out of it takes the caveat
with it, because that is how these numbers will actually be encountered.

* The **platform lens** headline is a VVR and says which estimand it is. The
  expansion arms sit below it, and the comment-attribution arm is labelled NOT a
  VVR in the same line as its number.
* The **workflow lens** separates measured counts from the modelled minutes
  comparison, and the minutes section leads with the fact that no benchmark
  exists to compare it against.
* **Governance activity is a mandatory section** (3.3) and renders when every
  count is zero, with the note that zero means nothing exercised the gates.
* **Honest Limits is mandatory** and carries forward, per CLAUDE.md. It is not
  an appendix: a reader who stops after it has read the part that constrains
  everything above it.

Language (3.5)
--------------
No causal claims from the workflow lens. Comparative statements carry an
assumption reference. ``BANNED_CAUSAL_PHRASES`` from ``measurement.workflow`` is
asserted absent from the rendered Markdown and HTML by test, so a future edit
cannot reintroduce "reduces" or "saves" into the one document anybody reads.
"""

import html
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ts_sentry.data.tz import IST
from ts_sentry.measurement.frame import ViewFrame, render_stratum_table
from ts_sentry.measurement.recovery import RecoveryTable
from ts_sentry.measurement.sensitivity import Curve
from ts_sentry.measurement.vvr import BootstrapCheck, VvrEstimate
from ts_sentry.measurement.workflow import (
    NO_BENCHMARK_NOTE,
    GovernanceActivity,
    MinutesResult,
)
from ts_sentry.provenance import git_sha

__all__ = [
    "HONEST_LIMITS",
    "MeasurementReport",
    "MeasurementStamp",
    "write_measurement_report",
]

HONEST_LIMITS: tuple[str, ...] = (
    "The baseline VVR estimand is narrow by construction and narrower still on this "
    "corpus. A view counts as violative only when the viewed video's own label is a "
    "non-benign, non-spam class, which is what the published method judges. On the "
    "seed-42 build that is 18 views out of 18,780, all of them from T-02 and T-07; "
    "T-04 receives no view events at all.",
    "The 95% interval covers sampling error only. This replicates the published "
    "method, which states that its confidence intervals do not take into account "
    "rater quality. Rater error is modelled separately and reported as a bias curve, "
    "and at realistic rater accuracy that bias is larger than the interval.",
    "The normal approximation is invalid at every realistic sample size on this "
    "corpus and becomes valid only at a full census. Every estimate reports the "
    "failed condition rather than suppressing it.",
    "Only two of the five risk strata hold any views, because viewed videos take "
    "just two distinct observable profiles. No choice of band cut points can "
    "populate the others.",
    "The risk proxy is an analog of the published method's classifier-score "
    "stratification, built from content-provenance features. It is not a detector, "
    "has no measured precision or recall, and must not be read as a detection result.",
    "Rater error is modelled as independent per rater. Correlated error, such as a "
    "policy misreading a whole panel shares, is not modelled and would not be "
    "suppressed by majority voting.",
    "No published per-case review-time benchmark exists. Every figure in the "
    "analyst-minutes section is a stated assumption, and the section reports a "
    "sensitivity range rather than a result.",
    "Evidence recovery plateaus. The metadata-pivot strategy recovers the "
    "shared-registration-linked core and provably cannot reach ring members "
    "connected only by looser signals: on t02_chan_000_000 it reaches 4 of 8 "
    "members and the budget curve is flat from 5 pivots onward. That is a bounded "
    "limit of a metadata-pivot strategy rather than a defect, and it is the same "
    "shape as the structural recovery ceiling already reported per threat class.",
    "The generator's planted threat volume is fixed and does not vary with scale, "
    "so every rate above is bounded by the corpus rather than by the method. This "
    "bound comes from the data and no parameter can move it.",
    "Figures are byte-identical across two renders in one environment only. "
    "Cross-version stability is not claimed. The reproducibility artifact is the "
    "curve data in JSON and CSV.",
)
"""Carried forward and mandatory (CLAUDE.md), not an appendix.

Each entry is a limit that constrains something stated above it in the report. A
reader who reads only this section should come away with the right amount of
confidence in the rest.
"""


@dataclass(frozen=True, slots=True)
class MeasurementStamp:
    """Everything needed to re-run the measurement this report describes."""

    generated_ts_ist: str
    git_sha: str
    measurement_seed: int
    dataset_digest: str
    dataset_seed: int | None = None
    dataset_scale: int | None = None
    corpus_version: str | None = None
    corpus_sha256: str | None = None
    prompt_versions: Mapping[str, str] | None = None

    @classmethod
    def now(
        cls,
        *,
        measurement_seed: int,
        dataset_digest: str,
        clock: datetime | None = None,
        **fields: object,
    ) -> "MeasurementStamp":
        """Build a stamp, taking the clock as an argument.

        ``clock`` is injectable so a test can assert a byte-stable report. A
        report generator that read the wall clock internally would produce a
        different artifact every run and there would be no way to check that
        anything else about it was stable.
        """
        moment = clock if clock is not None else datetime.now(tz=IST)
        return cls(
            generated_ts_ist=moment.isoformat(),
            git_sha=git_sha(),
            measurement_seed=measurement_seed,
            dataset_digest=dataset_digest,
            **fields,  # type: ignore[arg-type]
        )

    def rows(self) -> tuple[tuple[str, str], ...]:
        prompts = self.prompt_versions or {}
        pointer = (
            ", ".join(f"{task}={digest[:12]}" for task, digest in sorted(prompts.items()))
            if prompts
            else "not recorded"
        )
        return (
            ("generated (IST)", self.generated_ts_ist),
            ("code (git SHA)", self.git_sha),
            ("measurement seed", str(self.measurement_seed)),
            ("dataset digest", self.dataset_digest),
            (
                "dataset seed / scale",
                "not recorded"
                if self.dataset_seed is None
                else f"{self.dataset_seed} / {self.dataset_scale}",
            ),
            (
                "policy corpus",
                "not recorded"
                if self.corpus_version is None
                else f"{self.corpus_version} ({(self.corpus_sha256 or '')[:12]})",
            ),
            ("active prompt versions", pointer),
        )

    def to_json_object(self) -> dict[str, object]:
        return {
            "generated_ts_ist": self.generated_ts_ist,
            "git_sha": self.git_sha,
            "measurement_seed": self.measurement_seed,
            "dataset_digest": self.dataset_digest,
            "dataset_seed": self.dataset_seed,
            "dataset_scale": self.dataset_scale,
            "corpus_version": self.corpus_version,
            "corpus_sha256": self.corpus_sha256,
            "prompt_versions": dict(self.prompt_versions or {}),
        }


@dataclass(frozen=True, slots=True)
class MeasurementReport:
    """Both lenses, their caveats, and the stamp that lets them be re-run.

    The platform-lens fields are optional because D5's ``report --session`` can
    be pointed at a session without a dataset, and a report that silently
    omitted the VVR would be worse than one that says it was not computed.
    """

    stamp: MeasurementStamp
    governance: GovernanceActivity
    minutes: MinutesResult
    session_id: str
    frame: ViewFrame | None = None
    vvr: VvrEstimate | None = None
    bootstrap: BootstrapCheck | None = None
    arms: tuple[VvrEstimate, ...] = ()
    curves: tuple[Curve, ...] = ()
    figures: tuple[str, ...] = ()
    recovery: RecoveryTable | None = None

    def render_markdown(self) -> str:
        lines: list[str] = [
            "# Trust & Safety Sentry: measurement report",
            "",
            f"Session `{self.session_id}`.",
            "",
            "| stamp | value |",
            "|---|---|",
        ]
        lines.extend(f"| {name} | `{value}` |" for name, value in self.stamp.rows())

        lines.extend(["", "## Platform lens: Violative View Rate", ""])
        if self.vvr is None:
            lines.extend(
                [
                    "Not computed. This report was produced from session artifacts alone,",
                    "and the VVR estimate requires the dataset the session ran against.",
                    "Re-run with a build directory to include it.",
                    "",
                ]
            )
        else:
            lines.extend(self._vvr_markdown())

        lines.extend(["## Workflow lens", "", "### Governance activity", ""])
        lines.extend(["```", self.governance.render(), "```", ""])

        if self.recovery is not None:
            lines.extend(
                [
                    "### Evidence recovery at a pivot budget",
                    "",
                    "Measured. Cells are mean recovery of the planted ring / of what an",
                    "evidence pack can structurally hold. The second figure is the one to",
                    "read for strategy performance; the first is bounded by the pack's",
                    "structural ceiling.",
                    "",
                    "```",
                    self.recovery.render(),
                    "```",
                    "",
                ]
            )

        lines.extend(["### Analyst minutes (MODELLED, not measured)", ""])
        lines.extend(["```", self.minutes.render(), "```", ""])

        if self.figures:
            lines.extend(["## Figures", ""])
            lines.extend(f"![{name}]({name})" for name in self.figures)
            lines.append("")

        lines.extend(["## Honest limits", ""])
        lines.extend(f"{index}. {text}" for index, text in enumerate(HONEST_LIMITS, start=1))
        lines.append("")
        return "\n".join(lines)

    def _vvr_markdown(self) -> list[str]:
        assert self.vvr is not None
        lines = [
            f"**{100 * self.vvr.point:.4f}%** "
            f"(95% CI {100 * self.vvr.lower:.4f}% to {100 * self.vvr.upper:.4f}%), "
            f"n={self.vvr.sampled} of N={self.vvr.population}.",
            "",
            "The interval covers sampling error only, replicating the published method's",
            "statement that its confidence intervals do not take into account rater quality.",
            "",
            "```",
            self.vvr.render(),
            "```",
            "",
        ]
        if self.bootstrap is not None:
            if self.bootstrap.applicable:
                lines.extend(
                    [
                        f"Bootstrap cross-check: {100 * self.bootstrap.lower:.4f}% to "
                        f"{100 * self.bootstrap.upper:.4f}% over "
                        f"{self.bootstrap.replicates} replicates, half-width ratio "
                        f"{self.bootstrap.width_ratio:.3f} against an expected "
                        f"{self.bootstrap.expected_ratio:.3f}. The bootstrap ignores the "
                        "finite population correction, so it is expected to be the wider "
                        "of the two.",
                        "",
                    ]
                )
            else:
                lines.extend(
                    [
                        "Bootstrap cross-check: **not applicable**. The point estimate is "
                        "zero, so both intervals collapse to a point and the comparison is "
                        "vacuous. This is not agreement between the two methods.",
                        "",
                    ]
                )
        if self.frame is not None:
            lines.extend(
                [
                    "Strata and the measured gradient:",
                    "",
                    "```",
                    render_stratum_table(self.frame),
                    "```",
                    "",
                ]
            )
        if self.arms:
            lines.extend(["Policy-scope arms:", "", "| scope | rate | is a VVR |", "|---|---|---|"])
            for arm in self.arms:
                marker = "yes" if arm.is_faithful_vvr else "**NO, attribution differs**"
                lines.append(f"| {arm.scope_name} | {100 * arm.point:.4f}% | {marker} |")
            lines.append("")
        return lines

    def render_html(self) -> str:
        """Self-contained HTML, with the figures referenced beside it.

        Escaped throughout. The report carries session ids, scope names and a
        git SHA, none of which is user-authored today, but a report generator
        that interpolates unescaped text into HTML is one platform-derived
        string away from being wrong about that.
        """
        blocks: list[str] = [
            "<!-- generated by ts_sentry.measurement.report -->",
            "<h1>Trust &amp; Safety Sentry: measurement report</h1>",
            f"<p>Session <code>{html.escape(self.session_id)}</code>.</p>",
            "<table><thead><tr><th>stamp</th><th>value</th></tr></thead><tbody>",
        ]
        blocks.extend(
            f"<tr><td>{html.escape(name)}</td><td><code>{html.escape(value)}</code></td></tr>"
            for name, value in self.stamp.rows()
        )
        blocks.append("</tbody></table>")

        blocks.append("<h2>Platform lens: Violative View Rate</h2>")
        if self.vvr is None:
            blocks.append(
                "<p>Not computed. This report was produced from session artifacts alone, "
                "and the VVR estimate requires the dataset the session ran against.</p>"
            )
        else:
            blocks.append(
                f"<p><strong>{100 * self.vvr.point:.4f}%</strong> (95% CI "
                f"{100 * self.vvr.lower:.4f}% to {100 * self.vvr.upper:.4f}%), "
                f"n={self.vvr.sampled} of N={self.vvr.population}. The interval covers "
                "sampling error only.</p>"
            )
            blocks.append(f"<pre>{html.escape(self.vvr.render())}</pre>")
            if self.frame is not None:
                blocks.append(f"<pre>{html.escape(render_stratum_table(self.frame))}</pre>")
            if self.arms:
                blocks.append(
                    "<table><thead><tr><th>scope</th><th>rate</th><th>is a VVR</th>"
                    "</tr></thead><tbody>"
                )
                for arm in self.arms:
                    marker = "yes" if arm.is_faithful_vvr else "<strong>NO</strong>"
                    blocks.append(
                        f"<tr><td>{html.escape(arm.scope_name)}</td>"
                        f"<td>{100 * arm.point:.4f}%</td><td>{marker}</td></tr>"
                    )
                blocks.append("</tbody></table>")

        blocks.append("<h2>Workflow lens</h2>")
        blocks.append("<h3>Governance activity</h3>")
        blocks.append(f"<pre>{html.escape(self.governance.render())}</pre>")
        if self.recovery is not None:
            blocks.append("<h3>Evidence recovery at a pivot budget</h3>")
            blocks.append(f"<pre>{html.escape(self.recovery.render())}</pre>")
        blocks.append("<h3>Analyst minutes (MODELLED, not measured)</h3>")
        blocks.append(f"<p>{html.escape(NO_BENCHMARK_NOTE)}</p>")
        blocks.append(f"<pre>{html.escape(self.minutes.render())}</pre>")

        if self.figures:
            blocks.append("<h2>Figures</h2>")
            blocks.extend(
                f'<figure><img src="{html.escape(name)}" alt="{html.escape(name)}">'
                f"<figcaption>{html.escape(name)}</figcaption></figure>"
                for name in self.figures
            )

        blocks.append("<h2>Honest limits</h2><ol>")
        blocks.extend(f"<li>{html.escape(text)}</li>" for text in HONEST_LIMITS)
        blocks.append("</ol>")
        return "\n".join(blocks) + "\n"

    def to_json_object(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "stamp": self.stamp.to_json_object(),
            "platform_lens": None
            if self.vvr is None
            else {
                "scope": self.vvr.scope_name,
                "is_faithful_vvr": self.vvr.is_faithful_vvr,
                "point": self.vvr.point,
                "lower": self.vvr.lower,
                "upper": self.vvr.upper,
                "sampled": self.vvr.sampled,
                "population": self.vvr.population,
                "validity_holds": self.vvr.validity.holds,
                "arms": [
                    {
                        "scope": arm.scope_name,
                        "point": arm.point,
                        "is_faithful_vvr": arm.is_faithful_vvr,
                    }
                    for arm in self.arms
                ],
            },
            "workflow_lens": {
                "governance": self.governance.to_json_object(),
                "analyst_minutes": self.minutes.to_json_object(),
                "recovery": None if self.recovery is None else self.recovery.to_json_object(),
            },
            "figures": list(self.figures),
            "honest_limits": list(HONEST_LIMITS),
        }


def write_measurement_report(
    report: MeasurementReport,
    out_dir: Path,
    *,
    curves: Sequence[Curve] = (),
) -> tuple[Path, ...]:
    """Write ``report.md``, ``report.html`` and ``report.json``.

    Every write passes ``newline="\\n"`` for the reason
    ``sensitivity.write_curve_data`` does: on Windows the default would rewrite
    each separator to CRLF, and two runs of identical content on different
    platforms would differ in bytes while every value-level check kept passing.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for name, body in (
        ("report.md", report.render_markdown()),
        ("report.html", report.render_html()),
        ("report.json", json.dumps(report.to_json_object(), indent=2, sort_keys=True) + "\n"),
    ):
        path = out_dir / name
        path.write_text(body, encoding="utf-8", newline="\n")
        written.append(path)

    if curves:
        from ts_sentry.measurement.sensitivity import write_curve_data

        written.extend(write_curve_data(curves, out_dir))
    return tuple(written)
