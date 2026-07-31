# SPDX-License-Identifier: MIT
"""D6: the build-time quality gate, wrapping the `analystkit` CLI rather
than reimplementing DAMA checks (Saif's own package, pinned to v2.1.0).

Empirically verified against the installed analystkit==2.1.0 (its own
`--help` text and docs don't cover all of this):

- `profile`, `validate`, and `reconcile` are purely diagnostic: every one
  of them exits 0 regardless of findings ("exceptions are REPORTED, never
  dropped"). There is no built-in pass/fail gate to call into - this
  module supplies the threshold interpretation and exit-code mapping
  ourselves, by parsing their text output.
- `profile` and `validate` read Parquet directly (undocumented but works,
  confirmed by direct invocation). `reconcile` currently accepts CSV on
  both sides only - confirmed by direct invocation, not documentation -
  so the accuracy-dimension reconcile step below exports small CSV
  side-files just for that one step.
- On Windows, `profile`'s unicode progress-bar characters crash under the
  default cp1252 console codec; every invocation sets
  PYTHONIOENCODING=utf-8.
- `profile`'s Timeliness dimension is a wall-clock-relative linear decay
  (0 at >=90 days old). Our build window is a fixed historical range for
  reproducibility (docs/data-dictionary.md Assumptions), so Timeliness is
  0% by design, not a defect - it is reported but never gated, matching
  the accuracy dimension's "requires reconcile" carve-out already built
  into analystkit itself.
"""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import duckdb

from ts_sentry.data.enums import EntityKind
from ts_sentry.governance.scopes import DataScope, resolve_export_path, resolve_table

_QUALITY_RULES_DIR = Path(__file__).parent / "quality_rules"

_DIMENSION_PATTERN = re.compile(
    r"^(Completeness|Uniqueness|Validity|Consistency)\s+([\d.]+)%", re.MULTILINE
)
_TOTAL_EXCEPTIONS_PATTERN = re.compile(r"\|\s*([\d,]+)\s+total exceptions")
_LEFT_ORPHANS_PATTERN = re.compile(r"Left orphans\s*:\s*([\d,]+)")
_RIGHT_ORPHANS_PATTERN = re.compile(r"Right orphans\s*:\s*([\d,]+)")

# (DataScope, id column, rules filename, EntityKind for the reconcile step)
_ENTITY_TARGETS: tuple[tuple[DataScope, str, str, EntityKind], ...] = (
    (DataScope.ACCOUNT_META, "account_id", "account_meta.json", EntityKind.ACCOUNT),
    (DataScope.CHANNEL, "channel_id", "channel.json", EntityKind.CHANNEL),
    (DataScope.VIDEO, "video_id", "video.json", EntityKind.VIDEO),
    (DataScope.COMMENT, "comment_id", "comment.json", EntityKind.COMMENT),
)
# Tables with no sealed-label counterpart to reconcile against (engagement
# events and infra hints are signals, not EntityKind-labelable rows).
_PROFILE_ONLY_TARGETS: tuple[tuple[DataScope, str], ...] = (
    (DataScope.ENGAGEMENT_EVENT, "engagement_event.json"),
    (DataScope.INFRA_HINT, "infra_hint.json"),
)


@dataclass(frozen=True, slots=True)
class QualityThresholds:
    """Declared pass/fail thresholds (percent) for the four AnalystKit
    profile dimensions we gate on. Timeliness and Accuracy are handled
    separately (see module docstring); they have no threshold here.

    Calibrated against measured scores across all six Phase-1 tables, not
    guessed: uniqueness/validity/consistency are 100% on every table, so
    99/95/95 leaves margin while staying a real, non-vacuous gate.
    Completeness ranges 71.4% (engagement_event, where session_id is a
    reserved always-null Phase-1 field) to 100%; 60% leaves ~11 points of
    margin over the lowest legitimate score without being trivially
    satisfiable.
    """

    completeness: float = 60.0
    uniqueness: float = 99.0
    validity: float = 95.0
    consistency: float = 95.0


@dataclass(frozen=True, slots=True)
class DimensionResult:
    name: str
    score: float
    threshold: float

    @property
    def passed(self) -> bool:
        return self.score >= self.threshold


@dataclass(frozen=True, slots=True)
class ProfileReport:
    table_name: str
    dimensions: tuple[DimensionResult, ...]

    @property
    def passed(self) -> bool:
        return all(d.passed for d in self.dimensions)


@dataclass(frozen=True, slots=True)
class ValidateReport:
    table_name: str
    total_exceptions: int

    @property
    def passed(self) -> bool:
        return self.total_exceptions == 0


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    entity_kind: EntityKind
    left_orphans: int
    right_orphans: int

    @property
    def passed(self) -> bool:
        return self.left_orphans == 0 and self.right_orphans == 0


@dataclass(frozen=True, slots=True)
class QualityGateResult:
    profiles: tuple[ProfileReport, ...]
    validations: tuple[ValidateReport, ...]
    reconciliations: tuple[ReconcileReport, ...]

    @property
    def passed(self) -> bool:
        return (
            all(p.passed for p in self.profiles)
            and all(v.passed for v in self.validations)
            and all(r.passed for r in self.reconciliations)
        )


def _run_analystkit(args: list[str]) -> str:
    exe = shutil.which("analystkit")
    if exe is None:
        raise RuntimeError(
            "analystkit executable not found on PATH (is it installed? see pyproject.toml)"
        )
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    result = subprocess.run([exe, *args], capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"analystkit {args[0]} failed (exit {result.returncode}): {result.stderr}"
        )
    return result.stdout


def profile_table(path: Path, thresholds: QualityThresholds) -> ProfileReport:
    output = _run_analystkit(["profile", str(path)])
    threshold_by_name = {
        "Completeness": thresholds.completeness,
        "Uniqueness": thresholds.uniqueness,
        "Validity": thresholds.validity,
        "Consistency": thresholds.consistency,
    }
    found = dict(_DIMENSION_PATTERN.findall(output))
    if found.keys() != threshold_by_name.keys():
        raise RuntimeError(
            f"could not find all expected dimensions in analystkit profile output for {path}: "
            f"found {sorted(found)}, expected {sorted(threshold_by_name)}"
        )
    dimensions = tuple(
        DimensionResult(name=name, score=float(found[name]), threshold=threshold_by_name[name])
        for name in threshold_by_name
    )
    return ProfileReport(table_name=path.name, dimensions=dimensions)


def validate_table(path: Path, rules_path: Path) -> ValidateReport:
    output = _run_analystkit(["validate", str(path), "--rules", str(rules_path)])
    match = _TOTAL_EXCEPTIONS_PATTERN.search(output)
    if match is None:
        raise RuntimeError(f"could not parse analystkit validate output for {path}")
    return ValidateReport(
        table_name=path.name, total_exceptions=int(match.group(1).replace(",", ""))
    )


def reconcile_entity_labels(
    con: duckdb.DuckDBPyConnection,
    scope: DataScope,
    id_column: str,
    entity_kind: EntityKind,
    tmp_dir: Path,
) -> ReconcileReport:
    """Reconcile one entity table's ids against `sealed._labels` for that
    kind - the accuracy-dimension gate. Both sides are exported as small
    CSVs (reconcile's only accepted format) with a shared `entity_id`
    column so `--key entity_id` ties them out directly.
    """
    left_csv = tmp_dir / f"{scope.value}_ids.csv"
    right_csv = tmp_dir / f"{scope.value}_labels.csv"
    con.table(resolve_table(scope)).project(f"{id_column} AS entity_id").write_csv(str(left_csv))
    con.sql(
        "SELECT entity_id FROM sealed._labels WHERE entity_kind = ?", params=[entity_kind.value]
    ).write_csv(str(right_csv))

    output = _run_analystkit(["reconcile", str(left_csv), str(right_csv), "--key", "entity_id"])
    left_match = _LEFT_ORPHANS_PATTERN.search(output)
    right_match = _RIGHT_ORPHANS_PATTERN.search(output)
    if left_match is None or right_match is None:
        raise RuntimeError(f"could not parse analystkit reconcile output for {scope}")
    return ReconcileReport(
        entity_kind=entity_kind,
        left_orphans=int(left_match.group(1).replace(",", "")),
        right_orphans=int(right_match.group(1).replace(",", "")),
    )


_DEFAULT_THRESHOLDS = QualityThresholds()


def run_quality_gate(
    con: duckdb.DuckDBPyConnection,
    out_dir: Path,
    tmp_dir: Path,
    thresholds: QualityThresholds = _DEFAULT_THRESHOLDS,
) -> QualityGateResult:
    """Run profile + validate on every entity table, plus reconcile against
    `sealed._labels` for the four labelable entity kinds. Assumes
    ``export_dataset`` has already written Parquet exports to ``out_dir``.
    """
    all_scopes_and_rules = [(s, r) for s, _id, r, _k in _ENTITY_TARGETS] + list(
        _PROFILE_ONLY_TARGETS
    )

    profiles = []
    validations = []
    for scope, rules_filename in all_scopes_and_rules:
        path = resolve_export_path(scope, out_dir)
        profiles.append(profile_table(path, thresholds))
        validations.append(validate_table(path, _QUALITY_RULES_DIR / rules_filename))

    reconciliations = [
        reconcile_entity_labels(con, scope, id_column, entity_kind, tmp_dir)
        for scope, id_column, _rules_filename, entity_kind in _ENTITY_TARGETS
    ]

    return QualityGateResult(
        profiles=tuple(profiles),
        validations=tuple(validations),
        reconciliations=tuple(reconciliations),
    )
