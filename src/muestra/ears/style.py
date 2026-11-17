"""Style similarity hook (spec 10, optional; OUT of scope for v1).

``metrics.style_similarity`` stays ``None`` unless a scorer is
registered. A future WP can plug a CLAP/MERT model in without touching
the analyzer::

    from muestra.ears import style

    class ClapScorer:
        model = "laion-clap"
        def score(self, mix, sr, references): ...  # -> StyleSimilarity

    style.register(ClapScorer())

References would come from the project's ``references/`` folder (spec
9.1). The scorer runs after every other metric and its failures are
reported in ``metrics.skipped``, never raised.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np

from ..schemas.metrics import StyleSimilarity

__all__ = ["StyleScorer", "register", "unregister", "current", "score"]


class StyleScorer(Protocol):
    model: str

    def score(self, mix: np.ndarray, sr: int, references: list[Path]) -> StyleSimilarity | None: ...


_SCORER: StyleScorer | None = None


def register(scorer: StyleScorer) -> None:
    global _SCORER
    _SCORER = scorer


def unregister() -> None:
    global _SCORER
    _SCORER = None


def current() -> StyleScorer | None:
    return _SCORER


def score(
    project_root: Path, mix: np.ndarray, sr: int
) -> tuple[StyleSimilarity | None, str | None]:
    """Run the registered scorer, if any: (result, skipped-reason)."""
    if _SCORER is None:
        return None, None
    refs_dir = project_root / "references"
    refs = sorted(p for p in refs_dir.glob("*") if p.is_file()) if refs_dir.is_dir() else []
    try:
        return _SCORER.score(mix, sr, refs), None
    except Exception as e:  # a broken optional model must never break the render
        return None, f"style_similarity ({_SCORER.model}) falló: {e}"
