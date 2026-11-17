"""Ears seam (WP6): objective audio metrics for one render version.

The render tool calls :func:`analyze` AFTER writing ``meta.json``: it
reads ``renders/<version>/mix.wav``, the track and drum-lane stems, the
version's ``song.yaml`` snapshot (``renders/vNNN/song.yaml``, falling
back to ``git show vNNN:song.yaml``) and the chop sources'
``analysis.json``, and returns the spec section-10 :class:`Metrics`.
:func:`analyze` itself writes nothing; :func:`write_metrics` (used by
the ``ears.analyze`` tool) also writes ``renders/<version>/metrics.json``.

:func:`compare` gives the metrics delta between two versions (louder /
brighter / alarms fixed and introduced) for versions.diff and the UI;
``Metrics.summary`` is the compact text to return inline from render.

Methods, thresholds, detection limits and blind spots: docs/ears.md.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ..errors import MuestraError, issues_from_validation_error
from ..schemas.metrics import (
    AlignmentReport,
    ClickReport,
    LowEndMasking,
    Metrics,
    MetricsDelta,
    TonalReport,
)
from . import style
from .alarms import sort_alarms
from .clicks import measure_clicks
from .context import RenderContext, load_context, render_dir
from .loudness import measure_loudness
from .lowend import measure_low_end
from .report import compare_metrics, summarize
from .spectral import measure_spectral
from .technical import measure_technical
from .timing import measure_alignment
from .tonal import measure_tonal

if TYPE_CHECKING:
    from ..project.core import Project

__all__ = [
    "EARS_VERSION",
    "analyze",
    "write_metrics",
    "load_metrics",
    "compare",
    "summarize",
    "metrics_path",
]

EARS_VERSION = "wp6-1"


def _guard(ctx: RenderContext, name: str, fn: Callable, fallback):
    """Run one optional metric; a failure is reported, never fatal."""
    try:
        return fn(ctx)
    except (ValueError, IndexError, FloatingPointError, ZeroDivisionError) as e:
        ctx.skipped.append(f"{name}: error interno ({type(e).__name__}: {e})")
        return fallback, []


def analyze(project: Project, version: str) -> Metrics:
    """Compute the spec section-10 metrics for ``renders/<version>/``."""
    t0 = time.perf_counter()
    ctx = load_context(project, version)
    alarms = []
    loudness, a = measure_loudness(ctx)
    alarms += a
    spectral, a = measure_spectral(ctx)
    alarms += a
    low_end, a = _guard(ctx, "low_end", measure_low_end, LowEndMasking())
    alarms += a
    alignment, a = _guard(
        ctx, "alignment", measure_alignment, AlignmentReport(max_abs_offset_ms=0.0)
    )
    alarms += a
    tonal, a = _guard(ctx, "tonal", measure_tonal, TonalReport(target_key=ctx.song.meta.key))
    alarms += a
    clicks, a = _guard(ctx, "clicks", measure_clicks, ClickReport(count=0))
    alarms += a
    technical, a = _guard(ctx, "technical", measure_technical, None)
    alarms += a
    similarity, why = style.score(project.root, ctx.mix, ctx.sr)
    if why:
        ctx.skipped.append(why)
    m = Metrics(
        version=version,
        loudness=loudness,
        spectral=spectral,
        low_end_masking=low_end,
        alignment=alignment,
        tonal=tonal,
        clicks=clicks,
        style_similarity=similarity,
        alarms=sort_alarms(alarms),
        technical=technical,
        skipped=ctx.skipped,
        ears_version=EARS_VERSION,
    )
    m.summary = summarize(m)
    m.analysis_time_s = round(time.perf_counter() - t0, 3)
    return m


def metrics_path(project: Project, version: str) -> Path:
    return project.renders_dir / version / "metrics.json"


def write_metrics(project: Project, version: str) -> tuple[Metrics, Path]:
    """Analyze and write ``renders/<version>/metrics.json``."""
    m = analyze(project, version)
    path = metrics_path(project, version)
    path.write_text(
        json.dumps(m.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return m, path


def load_metrics(project: Project, version: str) -> Metrics:
    """Read ``renders/<version>/metrics.json``."""
    path = render_dir(project, version) / "metrics.json"
    rel = f"renders/{version}/metrics.json"
    if not path.is_file():
        raise MuestraError.one(
            "file_not_found",
            f"{rel} does not exist.",
            path=rel,
            value=version,
            hint="Run ears.analyze on that version first.",
        )
    try:
        return Metrics.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as e:
        raise MuestraError.one(
            "parse_error",
            f"{rel} is not valid JSON: {e}",
            path=rel,
            hint="Re-run ears.analyze to regenerate it.",
        ) from e
    except ValidationError as e:
        raise MuestraError(issues_from_validation_error(e, path_prefix=rel)) from e


def _metrics_for(project: Project, version: str, reanalyze: bool) -> Metrics:
    if not reanalyze and metrics_path(project, version).is_file():
        try:
            return load_metrics(project, version)
        except MuestraError:
            pass  # stale/invalid: regenerate (metrics.json is derived data)
    return write_metrics(project, version)[0]


def compare(
    project: Project, version_a: str, version_b: str, *, reanalyze: bool = False
) -> MetricsDelta:
    """Metrics delta from ``version_a`` to ``version_b``.

    Uses each version's metrics.json, analysing (and writing) it first
    when missing or invalid, or always with ``reanalyze``.
    """
    a = _metrics_for(project, version_a, reanalyze)
    b = _metrics_for(project, version_b, reanalyze)
    return compare_metrics(a, b)
