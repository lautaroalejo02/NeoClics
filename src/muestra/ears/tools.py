"""Tools owned by WP6 (ears): ``ears.analyze`` and ``ears.compare``."""

from __future__ import annotations

from pydantic import Field

from ..project.core import open_project
from ..schemas._base import RelPath, VersionStr
from ..schemas.metrics import Metrics, MetricsDelta
from ..toolbase import ProjectScoped, ToolOutput
from .core import compare, write_metrics

__all__ = [
    "EarsAnalyzeParams",
    "EarsAnalyzeResult",
    "EarsCompareParams",
    "EarsCompareResult",
    "ears_analyze",
    "ears_compare",
    "TOOLS",
]


class EarsAnalyzeParams(ProjectScoped):
    version: VersionStr = Field(description="Render version to analyze, e.g. 'v001'.")


class EarsAnalyzeResult(ToolOutput):
    version: VersionStr
    metrics: Metrics
    metrics_path: RelPath
    summary: str = Field(
        default="",
        description="Compact Spanish summary (same text as metrics.summary).",
    )


def ears_analyze(p: EarsAnalyzeParams) -> EarsAnalyzeResult:
    """Compute the section-10 metrics for a rendered version into
    renders/<version>/metrics.json (loudness, spectral balance, low-end
    masking + sidechain, grid alignment, tonal coherence + detune,
    clicks, technical checks) with alarms that each carry a suggested
    song.yaml fix. ``summary`` is a short text for the agent; the full
    detail is in ``metrics``."""
    project = open_project(p.project)
    m, _path = write_metrics(project, p.version)
    rel = f"renders/{p.version}/metrics.json"
    return EarsAnalyzeResult(
        version=p.version, metrics=m, metrics_path=rel, summary=m.summary, files=[rel]
    )


class EarsCompareParams(ProjectScoped):
    version_a: VersionStr = Field(description="Base version, e.g. 'v001'.")
    version_b: VersionStr = Field(description="Version compared against the base, e.g. 'v002'.")
    reanalyze: bool = Field(
        default=False, description="Recompute both metrics.json instead of reusing them."
    )


class EarsCompareResult(ToolOutput):
    delta: MetricsDelta


def ears_compare(p: EarsCompareParams) -> EarsCompareResult:
    """Metrics delta between two versions (b relative to a): louder or
    quieter, brighter or darker, heavier or lighter low end, per-track
    level changes, and which alarms were fixed or introduced."""
    project = open_project(p.project)
    files = [
        f"renders/{v}/metrics.json"
        for v in (p.version_a, p.version_b)
        if p.reanalyze or not (project.renders_dir / v / "metrics.json").is_file()
    ]
    delta = compare(project, p.version_a, p.version_b, reanalyze=p.reanalyze)
    return EarsCompareResult(delta=delta, files=files)


#: tools exported by this subpackage (collected by muestra.registry).
TOOLS = [
    (
        "ears.analyze",
        ears_analyze,
        EarsAnalyzeParams,
        EarsAnalyzeResult,
        {"long_running": True, "cli": ("ears", "analyze")},
    ),
    (
        "ears.compare",
        ears_compare,
        EarsCompareParams,
        EarsCompareResult,
        {"cli": ("ears", "compare")},
    ),
]
