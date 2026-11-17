"""Render seam (WP2 owns the implementation).

The ``render`` tool orchestrates, in order:

1. ``song.validate``
2. allocate the version id (``project.next_version_id`` — max+1,
   never reused)
3. ``muestra.versions.core.record_version`` (WP5): commit + annotated
   tag ``vNNN``
4. render audio via :func:`render_song`
5. write ``renders/vNNN/song.yaml`` (byte copy) + ``meta.json``
6. ``muestra.ears.core.analyze`` (WP6) -> write ``metrics.json``

Output contract (meta.json): ``mix.wav`` stereo, project sample rate,
24-bit PCM, post-master; ``stems/<track>.wav`` stereo float32, POST
track fx/fader/pan, PRE master, same frame count, sample-aligned
(muted tracks get silent stems); pattern tracks also write per-lane
``stems/<track>.<lane>.wav`` (pre track fx). Master order: master fx
in listed order, loudness gain toward ``target_lufs``, final true-peak
safety limiter. The render is exactly ``range_bars`` long; tails are
cut with a 5 ms fade-out.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ..project.core import Project
    from ..schemas.song import Chop, Song

__all__ = ["render_chop", "render_song"]


def render_chop(
    project: Project,
    chop: Chop,
    sample_rate: int,
    project_bpm: float,
) -> np.ndarray:
    """Render one chop to float32 stereo ``(n_frames, 2)`` at ``sample_rate``.

    Shared by the renderer and by WP1's candidate previews. Previews
    play at PROJECT tempo when a song.yaml exists, else at source
    tempo.

    Warp semantics: ``resample`` = one playback rate for the whole
    chop, ``project_bpm / source_local_bpm`` (fills the grid slot
    exactly; the tempo-induced pitch change is the vinyl effect);
    ``stretch`` = pitch-preserving beat-wise time-stretch onto the
    project grid (Rubber Band). ``pitch_semitones`` is an additional
    LENGTH-PRESERVING shift in both modes.
    """
    raise NotImplementedError("WP2")


def render_song(
    project: Project,
    song: Song,
    version: str,
    range_bars: tuple[int, int],
    *,
    stems: bool = True,
) -> Path:
    """Render the song's ``range_bars`` ([start, end), 1-indexed,
    end-exclusive) into ``renders/<version>/`` and return that dir.

    Writes ``mix.wav`` + stems per the layout contract. ``song.yaml``,
    ``meta.json`` and ``metrics.json`` are written by the orchestrating
    render tool, not here.
    """
    raise NotImplementedError("WP2")
