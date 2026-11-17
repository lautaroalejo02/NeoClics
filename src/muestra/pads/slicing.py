"""Cut a region of a chop or a source into pad slices (WP17, "cortar en pads").

The classic MPC / future-funk chop: a riff cut on its beats (or half beats, or
at its attacks) across consecutive pads so it can be re-played in a new
order. Slices are :class:`LaneSlice` specs of the SAME origin as the region
(a chop region gives chop slices, a source region source slices), so a slice
renders through the chop warp path (``speed: project``) or at the record's
own speed (``speed: source``).
"""

from __future__ import annotations

import bisect
import math
from typing import Literal

from ..errors import MuestraError
from ..project import load_analysis
from ..project.core import Project
from ..project.validate import _beat_index_at, chop_times
from ..schemas.song import LaneSlice, Song, Warp

__all__ = ["slice_region", "MIN_SLICE_BEATS"]

#: transient slices shorter than this (beats) are merged into the previous one
MIN_SLICE_BEATS = 0.125


def _time_to_pos(beats_s: list[float], t: float) -> float:
    """Fractional beats_s index of time ``t`` (linear inside the grid)."""
    n = len(beats_s)
    if n < 2:
        return 0.0
    i = bisect.bisect_right(beats_s, t) - 1
    i = min(max(i, 0), n - 2)
    a, b = beats_s[i], beats_s[i + 1]
    return i + (t - a) / max(b - a, 1e-9)


def slice_region(
    project: Project,
    song: Song,
    *,
    chop: str | None = None,
    source: str | None = None,
    stem: str = "mix",
    start_downbeat: int | None = None,
    start_beats: float = 0.0,
    length_beats: float | None = None,
    by: Literal["beat", "half", "transients"] = "beat",
    speed: Literal["project", "source"] = "project",
    warp: Warp | None = None,
    max_slices: int = 16,
) -> list[LaneSlice]:
    """Slices covering ``[start_beats, start_beats + length_beats)`` of the region."""
    if (chop is None) == (source is None):
        raise MuestraError.one(
            "invalid_value",
            "Elegí un chop o una fuente para cortar.",
            path="chop",
            hint="Pass chop=<id> or source=<id> + start_downbeat.",
        )
    if chop is not None:
        base = next((c for c in song.chops if c.id == chop), None)
        if base is None:
            raise MuestraError.one(
                "ref_not_found", f"No existe el chop «{chop}».", path="chop", value=chop
            )
        length = length_beats if length_beats is not None else base.length_beats - start_beats
        src_id, src_stem, downbeat = base.source, base.stem, base.start_downbeat
        offset0 = base.start_offset_beats + start_beats
    else:
        if start_downbeat is None:
            raise MuestraError.one(
                "missing_field",
                "Para cortar una fuente hace falta el compás de inicio (start_downbeat).",
                path="start_downbeat",
            )
        if length_beats is None:
            raise MuestraError.one(
                "missing_field", "Falta el largo (length_beats).", path="length_beats"
            )
        length = length_beats
        src_id, src_stem, downbeat = source, stem, start_downbeat
        offset0 = start_beats
    if length <= 0:
        raise MuestraError.one(
            "out_of_range", "La región no tiene largo.", path="length_beats", value=length
        )
    analysis = load_analysis(project, src_id)
    if chop_times(analysis, downbeat, length, offset0) is None:
        raise MuestraError.one(
            "chop_overrun",
            "Esa región se sale de la grilla analizada de la fuente.",
            path="length_beats",
            value=length,
            hint="Use a shorter region or an earlier start.",
        )
    # region-relative cut points (beats)
    if by in ("beat", "half"):
        step = 1.0 if by == "beat" else 0.5
        n = max(1, math.floor(length / step + 1e-9))
        cuts = [i * step for i in range(n)]
    else:
        from ..ears.dsp import mono, onsets
        from ..render.audio import decode

        ref = analysis.file if src_stem == "mix" else getattr(analysis.stems, src_stem, None)
        if ref is None:
            raise MuestraError.one(
                "ref_not_found",
                f"La fuente no tiene el stem {src_stem}.",
                path="stem",
                value=src_stem,
            )
        sr = project.config.sample_rate
        audio, _ = decode(project, project.resolve(ref), sr)
        t0, t1 = chop_times(analysis, downbeat, length, offset0)
        a, b = max(0, round(t0 * sr) - round(0.02 * sr)), min(len(audio), round(t1 * sr))
        found = onsets(mono(audio[a:b]), sr).times + a / sr
        db_i, _ = _beat_index_at(analysis.beats_s, analysis.downbeats_s[downbeat - 1])
        origin = db_i + offset0
        cuts = [0.0]
        for t in sorted(found):
            pos = _time_to_pos(analysis.beats_s, float(t)) - origin
            if pos <= MIN_SLICE_BEATS or pos >= length - MIN_SLICE_BEATS:
                continue
            if pos - cuts[-1] >= MIN_SLICE_BEATS:
                cuts.append(round(pos, 4))
    cuts = cuts[:max_slices]
    out = []
    for i, c in enumerate(cuts):
        end = cuts[i + 1] if i + 1 < len(cuts) else length
        if len(cuts) == max_slices and i + 1 == len(cuts) and by != "transients":
            end = min(length, c + (cuts[1] - cuts[0] if len(cuts) > 1 else length))
        common = {
            "start_beats": round(start_beats + c if chop else offset0 + c, 6),
            "length_beats": round(end - c, 6),
            "speed": speed,
        }
        if chop is not None:
            out.append(LaneSlice(chop=chop, **common))
        else:
            out.append(
                LaneSlice(
                    source=src_id,
                    stem=src_stem,
                    start_downbeat=downbeat,
                    warp=warp,
                    **common,
                )
            )
    return out
