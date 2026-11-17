"""source.regrid / source.preview_grid: fix and audition a source's beat grid."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
import soundfile as sf

from ..errors import MuestraError
from ..project import Project, chop_times, edit_song_document, load_analysis, load_song
from ..schemas.analysis import Analysis, GridCorrection, GridStep
from . import grid as g
from .audio import decode, stereo
from .storage import project_lock, write_json

#: a chop "still cuts the same audio" when both edges move less than this.
SAME_AUDIO_TOLERANCE_S = 0.005


@dataclass
class ChopChange:
    chop_id: str
    before: dict
    after: dict | None
    updated: bool
    same_audio: bool
    moved_ms: float | None
    rate_before: float | None
    rate_after: float | None
    note: str


@dataclass
class RegridOutcome:
    analysis: Analysis
    before: Analysis
    chops: list[ChopChange] = field(default_factory=list)
    stale_sets: list[str] = field(default_factory=list)
    suggested_bpm: float | None = None
    files: list[str] = field(default_factory=list)
    hint: str | None = None


def _chop_state(analysis: Analysis, start_downbeat, offset, length, project_bpm) -> dict | None:
    times = chop_times(analysis, start_downbeat, length, offset)
    if times is None:
        return None
    start, end = times
    local = 60 * length / (end - start) if end > start else None
    return {
        "start_downbeat": start_downbeat,
        "start_offset_beats": offset,
        "length_beats": length,
        "start_s": round(start, 4),
        "end_s": round(end, 4),
        "local_bpm": round(local, 3) if local else None,
        "rate": round(project_bpm / local, 4) if (local and project_bpm) else None,
    }


def resolve_params(
    current: GridCorrection | None,
    analysis: Analysis,
    *,
    tempo_factor: float | None,
    beat_phase: int | None,
    downbeat_shift_beats: int | None,
    beats_per_bar: int | None,
    reset: bool,
) -> tuple[float, int, int, int]:
    raw = g.raw_of(analysis)
    if reset:
        return 1.0, 0, 0, 4
    cur_factor = current.tempo_factor if current else 1.0
    factor = cur_factor if tempo_factor is None else float(tempo_factor)
    changed = factor != cur_factor
    if beat_phase is not None:
        phase = beat_phase
    elif changed or current is None:
        phase = (
            g.default_beat_phase(np.asarray(raw.beats_s), np.asarray(raw.downbeats_s))
            if factor == 0.5
            else 0
        )
    else:
        phase = current.beat_phase
    if factor != 0.5:
        phase = 0
    if downbeat_shift_beats is not None:
        shift = downbeat_shift_beats
    else:
        shift = 0 if (changed or current is None) else current.downbeat_shift_beats
    bpb = beats_per_bar if beats_per_bar is not None else (current.beats_per_bar if current else 4)
    return factor, phase, shift, bpb


def regrid(
    project: Project,
    source_id: str,
    *,
    tempo_factor: float | None = None,
    beat_phase: int | None = None,
    downbeat_shift_beats: int | None = None,
    beats_per_bar: int | None = None,
    reset: bool = False,
    update_song: bool = True,
) -> RegridOutcome:
    """Re-derive the effective grid from the raw tracker output (absolute params)."""
    with project_lock(project):
        before = load_analysis(project, source_id)
        raw = g.raw_of(before)
        current = before.grid
        factor, phase, shift, bpb = resolve_params(
            current,
            before,
            tempo_factor=tempo_factor,
            beat_phase=beat_phase,
            downbeat_shift_beats=downbeat_shift_beats,
            beats_per_bar=beats_per_bar,
            reset=reset,
        )
        history = list(current.history) if current else []
        history.append(
            GridStep(
                at=datetime.now(UTC),
                origin="reset" if reset else "manual",
                tempo_factor=factor,
                beat_phase=phase,
                downbeat_shift_beats=shift,
                beats_per_bar=bpb,
            )
        )
        tempo_range = current.tempo_range_bpm if current else g.DEFAULT_TEMPO_RANGE
        correction = g.assess(
            raw,
            origin="manual",
            tempo_factor=factor,
            beat_phase=phase,
            downbeat_shift_beats=shift,
            beats_per_bar=bpb,
            tempo_range=tempo_range,
            meter_consistency=g.meter_consistency(raw.beats_s, raw.downbeats_s),
            history=history[-20:],
        )
        after = g.apply_correction(before, correction)
        outcome = RegridOutcome(analysis=after, before=before)

        # ---- song.yaml chops on this source: keep the same audio, report every move
        song = load_song(project) if project.song_path.exists() else None
        edits: dict[str, g.ChopPlacement] = {}
        if song is not None:
            bpm = song.meta.bpm
            old_factor = current.tempo_factor if current else 1.0
            for chop in song.chops:
                if chop.source != source_id:
                    continue
                old = _chop_state(
                    before, chop.start_downbeat, chop.start_offset_beats, chop.length_beats, bpm
                )
                same_fields = _chop_state(
                    after, chop.start_downbeat, chop.start_offset_beats, chop.length_beats, bpm
                )
                placement = g.place_on_grid(after, old["start_s"], old["end_s"]) if old else None
                new = (
                    _chop_state(
                        after,
                        placement.start_downbeat,
                        placement.start_offset_beats,
                        placement.length_beats,
                        bpm,
                    )
                    if placement
                    else None
                )
                if update_song and placement and new:
                    unchanged = (
                        placement.start_downbeat == chop.start_downbeat
                        and placement.start_offset_beats == chop.start_offset_beats
                        and placement.length_beats == chop.length_beats
                    )
                    if not unchanged:
                        edits[chop.id] = placement
                    snap = g.nearest_downbeat(after, old["start_s"])
                    outcome.chops.append(
                        _describe(chop.id, old, new, not unchanged, chop.warp.mode, snap)
                    )
                else:
                    outcome.chops.append(
                        _describe(chop.id, old, same_fields, False, chop.warp.mode, None)
                    )
            if factor != old_factor and any(c.source == source_id for c in song.chops):
                outcome.suggested_bpm = round(bpm * factor / old_factor, 3)

        if edits:
            from ..schemas.song import Song

            def mutate(doc):
                for entry in doc.get("chops", []):
                    placement = edits.get(entry["id"])
                    if placement is None:
                        continue
                    entry["start_downbeat"] = placement.start_downbeat
                    if "start_offset_beats" in entry:
                        entry["start_offset_beats"] = _num(placement.start_offset_beats)
                    elif placement.start_offset_beats:
                        keys = list(entry.keys())
                        pos = keys.index("start_downbeat") + 1 if "start_downbeat" in keys else 0
                        entry.insert(pos, "start_offset_beats", _num(placement.start_offset_beats))
                    entry["length_beats"] = _num(placement.length_beats)
                edited = Song.model_validate(doc)
                if issues := edited.check():
                    raise MuestraError(issues)

        if edits:
            # Validated inside mutate; nothing is written if the edit is invalid.
            edit_song_document(project, mutate)
            outcome.files.append("song.yaml")
        path = project.path(f"cache/analysis/{source_id}.json")
        write_json(path, after.model_dump(mode="json"))
        outcome.files.append(f"cache/analysis/{source_id}.json")
        grid_moved = before.beats_s != after.beats_s or before.downbeats_s != after.downbeats_s
        if grid_moved:  # same effective grid (e.g. a no-op call): candidates stay valid
            outcome.stale_sets = _retire_candidate_sets(project, source_id, outcome.files)

    hints = []
    if outcome.stale_sets:
        hints.append(
            f"Candidatos {', '.join(outcome.stale_sets)} quedaron con la grilla vieja (movidos a "
            f"cache/chops/{source_id}/stale/): corré source.find_chops de nuevo."
        )
    if outcome.suggested_bpm is not None:
        hints.append(
            f"El tempo de la fuente cambió x{factor / (current.tempo_factor if current else 1):g}: "
            f"para que los chops suenen igual poné meta.bpm {outcome.suggested_bpm:g} y pasá "
            "patterns/compases a la grilla nueva."
        )
    if correction.ask_human:
        hints.append("Pedile al humano que escuche source.preview_grid y confirme.")
    outcome.hint = " ".join(hints) or None
    return outcome


def _num(x: float) -> float | int:
    return int(x) if float(x).is_integer() else x


def _describe(chop_id, old, new, updated, mode, snap) -> ChopChange:
    if old is None:
        return ChopChange(
            chop_id,
            {},
            new,
            False,
            False,
            None,
            None,
            None,
            f"{chop_id} no entraba en la grilla anterior; revisalo con song.validate.",
        )
    if new is None:
        return ChopChange(
            chop_id,
            old,
            None,
            False,
            False,
            None,
            old.get("rate"),
            None,
            f"{chop_id} ya no entra en la grilla nueva (start_downbeat "
            f"{old['start_downbeat']}): elegilo de nuevo con source.find_chops.",
        )
    moved = (new["start_s"] - old["start_s"]) * 1000
    same = (
        abs(new["start_s"] - old["start_s"]) < SAME_AUDIO_TOLERANCE_S
        and abs(new["end_s"] - old["end_s"]) < SAME_AUDIO_TOLERANCE_S
    )
    fields = (
        f"start_downbeat {new['start_downbeat']}, start_offset_beats "
        f"{_num(new['start_offset_beats'])}, length_beats {_num(new['length_beats'])}"
    )
    was = (
        f"antes {old['start_downbeat']}/{_num(old['start_offset_beats'])}/"
        f"{_num(old['length_beats'])}"
    )
    same_fields = all(
        new[k] == old[k] for k in ("start_downbeat", "start_offset_beats", "length_beats")
    )
    if same and same_fields:
        note = f"{chop_id} no cambia: corta el mismo audio con los mismos campos."
    elif same:
        note = (
            f"{chop_id} sigue cortando el mismo audio ({old['start_s']:.2f}-{old['end_s']:.2f} s); "
            f"en la grilla nueva es {fields} ({was})."
        )
    elif updated or new.get("start_downbeat") != old.get("start_downbeat"):
        note = f"{chop_id} se reubicó en {fields} ({was}); el audio se corrió {moved:+.0f} ms."
    else:
        note = (
            f"{chop_id} no se tocó: con los mismos campos ahora corta "
            f"{new['start_s']:.2f}-{new['end_s']:.2f} s (antes {old['start_s']:.2f}-"
            f"{old['end_s']:.2f} s): cambió lo que suena."
        )
    rb, ra = old.get("rate"), new.get("rate")
    if rb and ra and abs(ra / rb - 1) > 0.005:
        if mode == "resample":
            st = g.semitones(ra / rb)
            note += (
                f" Con el meta.bpm actual suena x{ra / rb:.2f} de velocidad ({st:+.1f} st) "
                "respecto de antes."
            )
        else:
            note += f" Con el meta.bpm actual se estira x{ra / rb:.2f} respecto de antes."
    if new.get("start_offset_beats"):
        note += " No empieza en un uno de la grilla nueva (start_offset_beats ≠ 0)"
        note += (
            f"; para que arranque en el uno corregido más cercano: start_downbeat {snap}, "
            "start_offset_beats 0."
            if snap
            else "."
        )
    return ChopChange(chop_id, old, new, updated, same, round(moved, 1), rb, ra, note)


def _retire_candidate_sets(project: Project, source_id: str, files: list[str]) -> list[str]:
    """Move candidate sets scored on the old grid out of the live listing.

    Preview folders stay (they reserve the set ids, which are never reused).
    """
    from .chops import set_paths

    retired = []
    stale_dir = project.path(f"cache/chops/{source_id}/stale")
    for path in set_paths(project, source_id):
        stale_dir.mkdir(parents=True, exist_ok=True)
        meta = path.with_suffix(".meta.json")
        shutil.move(str(path), str(stale_dir / path.name))
        if meta.exists():
            shutil.move(str(meta), str(stale_dir / meta.name))
        retired.append(path.stem)
        files.append(f"cache/chops/{source_id}/stale/{path.name}")
    return retired


# ---------------------------------------------------------------------------
# audible grid
# ---------------------------------------------------------------------------


@dataclass
class GridPreview:
    path: str
    label: str
    tempo_factor: float
    beat_phase: int
    downbeat_shift_beats: int
    beats_per_bar: int
    bpm: float
    start_downbeat: int
    start_s: float
    end_s: float
    bars: int
    applied: bool


def preview_grid(
    project: Project,
    source_id: str,
    *,
    start_downbeat: int | None = None,
    bars: int = 8,
    stem: str = "mix",
    tempo_factor: float | None = None,
    beat_phase: int | None = None,
    downbeat_shift_beats: int | None = None,
    beats_per_bar: int | None = None,
    alternatives: bool = False,
) -> list[GridPreview]:
    """Render the source with a click on every beat (accent on the one).

    Without correction params it plays the CURRENT grid. With them it plays
    that hypothetical grid without applying it, so the human can compare.
    ``alternatives`` adds one file per listed alternative interpretation.
    """
    analysis = load_analysis(project, source_id)
    if stem == "mix":
        ref = analysis.file
    else:
        ref = getattr(analysis.stems, stem, None) if analysis.stems else None
        if ref is None:
            raise MuestraError.one(
                "ref_not_found",
                f"No {stem} stem for {source_id}.",
                path="stem",
                hint="Use stem 'mix' or run source.separate.",
            )
    audio, sr = decode(project.resolve(ref))
    audio = stereo(audio)
    current = analysis.grid
    hypothetical = any(
        v is not None for v in (tempo_factor, beat_phase, downbeat_shift_beats, beats_per_bar)
    )
    variants: list[tuple[str, float, int, int, int, bool]] = []
    if hypothetical:
        f, ph, sh, bpb = resolve_params(
            current,
            analysis,
            tempo_factor=tempo_factor,
            beat_phase=beat_phase,
            downbeat_shift_beats=downbeat_shift_beats,
            beats_per_bar=beats_per_bar,
            reset=False,
        )
        variants.append(("propuesta", f, ph, sh, bpb, False))
    else:
        f = current.tempo_factor if current else 1.0
        variants.append(
            (
                "grilla actual",
                f,
                current.beat_phase if current else 0,
                current.downbeat_shift_beats if current else 0,
                current.beats_per_bar if current else 4,
                True,
            )
        )
    if alternatives:
        alts = current.alternatives if current else g.auto_correct(analysis).grid.alternatives
        for alt in alts[:4]:
            variants.append(
                (
                    alt.label,
                    alt.tempo_factor,
                    alt.beat_phase,
                    alt.downbeat_shift_beats,
                    current.beats_per_bar if current else 4,
                    False,
                )
            )
    raw = g.raw_of(analysis)
    out_dir = project.path(g.preview_dir(source_id))
    out_dir.mkdir(parents=True, exist_ok=True)
    # Anchor every variant on the same audio moment: the current grid's downbeat.
    anchor_index = start_downbeat or 1
    if not 1 <= anchor_index <= len(analysis.downbeats_s):
        raise MuestraError.one(
            "out_of_range",
            f"start_downbeat {anchor_index} is outside 1..{len(analysis.downbeats_s)}.",
            path="start_downbeat",
            hint=f"Pick a value in [1, {len(analysis.downbeats_s)}].",
        )
    anchor_s = analysis.downbeats_s[anchor_index - 1]
    results = []
    for label, f, ph, sh, bpb, applied in variants:
        beats, downbeats = g.derive_grid(
            raw, tempo_factor=f, beat_phase=ph, downbeat_shift_beats=sh, beats_per_bar=bpb
        )
        k = int(np.argmin(abs(downbeats - anchor_s)))
        start = float(downbeats[k])
        stop_index = min(len(downbeats) - 1, k + bars)
        end = float(downbeats[stop_index]) if stop_index > k else float(beats[-1])
        if stop_index - k < bars:
            end = min(analysis.duration_s, end + 60 / g.grid_bpm(beats) * bpb)
        pre = min(start, 0.25)
        a, b = round((start - pre) * sr), min(len(audio), round(end * sr))
        mixed = g.overlay_clicks(audio[a:b], sr, beats, downbeats, start_s=start - pre)
        n = min(len(mixed), round(0.005 * sr))
        if n:
            ramp = np.linspace(0, 1, n, dtype=np.float32)[:, None]
            mixed[:n] *= ramp
            mixed[-n:] *= ramp[::-1]
        key = hashlib.sha256(
            json.dumps([raw.beats_s[:4], f, ph, sh, bpb, start, end, stem]).encode()
        ).hexdigest()[:10]
        path = out_dir / f"{stem}-{key}.wav"
        sf.write(path, mixed, sr, subtype="PCM_24")
        results.append(
            GridPreview(
                path=path.relative_to(project.root).as_posix(),
                label=label,
                tempo_factor=f,
                beat_phase=ph,
                downbeat_shift_beats=sh,
                beats_per_bar=bpb,
                bpm=round(g.grid_bpm(beats), 2),
                start_downbeat=k + 1,
                start_s=round(start, 3),
                end_s=round(end, 3),
                bars=min(bars, stop_index - k) or 1,
                applied=applied,
            )
        )
    # The UI lists the latest batch (GET /project grid_previews); older click files
    # are derived data and are pruned so the folder stays small.
    keep = {r.path.rsplit("/", 1)[-1] for r in results}
    for old in out_dir.glob("*.wav"):
        if old.name not in keep:
            old.unlink(missing_ok=True)
    write_json(
        out_dir / "index.json",
        {
            "source_id": source_id,
            "created_at": datetime.now(UTC).isoformat(),
            "bpm": round(analysis.bpm_global, 3),
            "raw_bpm": round(raw.bpm_global, 3),
            "previews": [vars(r) for r in results],
        },
    )
    return results
