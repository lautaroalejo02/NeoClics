"""Content provenance used before deleting the only saved copy of a render."""

from __future__ import annotations

import hashlib
import json


def render_signature(project, song) -> str:
    from ..project import load_analysis
    from .audio import CACHE_ABI, file_hash

    refs = set()
    grids = {}
    by_id = {c.id: c for c in song.chops}
    sliced = [
        lane.slice.as_chop(by_id)
        for t in song.tracks
        if t.pattern
        for lane in t.pattern.lanes.values()
        if lane.slice is not None
    ]
    for chop in [*song.chops, *(c for c in sliced if c is not None)]:  # WP17 slice lanes
        analysis = load_analysis(project, chop.source)
        grids[chop.source] = [analysis.beats_s, analysis.downbeats_s]
        ref = analysis.file if chop.stem == "mix" else getattr(analysis.stems, chop.stem)
        refs.add(ref)
    for track in song.tracks:
        refs.update(c.sample for c in track.clips or [] if c.sample)
        if track.pattern:
            refs.update(lane.sample for lane in track.pattern.lanes.values() if lane.sample)
            refs.update(h.sample for h in track.pattern.hits if h.sample)  # WP16
        if track.midi:
            refs.add(track.midi)
        if track.instrument and track.instrument.preset:
            # Explicit preset files are content inputs too (factory preset
            # names are already covered by the unverified-plugin prune guard).
            from pathlib import Path

            preset = Path(track.instrument.preset)
            if not preset.is_absolute():
                preset = project.root / preset
            if preset.is_file():
                refs.add(preset)
    contents = []
    for ref in sorted(refs, key=str):
        from pathlib import Path

        path = ref if isinstance(ref, Path) else project.resolve(ref)
        contents.append([str(ref), file_hash(path)])
    payload = [CACHE_ABI, project.config.sample_rate, song.model_dump(mode="json"), grids, contents]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
