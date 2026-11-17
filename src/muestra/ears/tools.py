"""Tool stub owned by WP6 (ears): ears.analyze."""

from __future__ import annotations

from pydantic import Field

from ..schemas._base import RelPath, VersionStr
from ..schemas.metrics import Metrics
from ..toolbase import ProjectScoped, ToolOutput

__all__ = ["EarsAnalyzeParams", "EarsAnalyzeResult", "ears_analyze"]

_WP = "WP6"


class EarsAnalyzeParams(ProjectScoped):
    version: VersionStr = Field(description="Render version to analyze, e.g. 'v001'.")


class EarsAnalyzeResult(ToolOutput):
    version: VersionStr
    metrics: Metrics
    metrics_path: RelPath


def ears_analyze(p: EarsAnalyzeParams) -> EarsAnalyzeResult:
    """Compute the section-10 metrics for a rendered version into
    renders/<version>/metrics.json (loudness, spectral balance, low-end
    masking, grid alignment, tonal coherence, clicks, optional style
    similarity)."""
    raise NotImplementedError(_WP)
