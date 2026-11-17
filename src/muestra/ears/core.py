"""Ears seam (WP6 owns the implementation).

The renderer calls :func:`analyze` AFTER writing ``meta.json``: it
reads the version's audio (``renders/<version>/mix.wav`` + stems) and
the version's ``song.yaml`` byte copy (``renders/vNNN/song.yaml`` when
present, ``git show vNNN:song.yaml`` otherwise) to compute the
section-10 metrics into ``metrics.json``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..schemas.metrics import Metrics

if TYPE_CHECKING:
    from ..project.core import Project

__all__ = ["analyze"]


def analyze(project: Project, version: str) -> Metrics:
    """Compute the spec section-10 metrics for ``renders/<version>/``.

    Reads the mix, per-track and per-lane stems, and the version's
    song.yaml byte copy; returns the Metrics model (also written by the
    render tool to ``renders/<version>/metrics.json``).
    """
    raise NotImplementedError("WP6")
