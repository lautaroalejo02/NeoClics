"""Deterministic grid arrangement, chop rendering, and audio export.

All positions are rounded from absolute musical time. Ranges warm up from
song start, then crop, preserving insert/delay/sidechain history.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import soundfile as sf

from ..errors import MuestraError
from ..library import core as library
from ..plugins import core as plugins
from ..project import chop_times, load_analysis
from ..project.core import Project
from ..schemas._base import VersionStr
from ..schemas.song import BEATS_PER_BAR, Chop, Clip, MidiNote, Song, Track, parse_steps
from .audio import CACHE_ABI, cache_read, cache_write, decode, digest, fade, fit, stretch, vinyl
from .automation import Automation, pan_gain
from .dsp import _dependency, apply_fx, master

__all__ = ["render_chop", "render_song"]


def beat_frame(beat: float, bpm: float, sample_rate: int) -> int:
    return round(beat * 60 / bpm * sample_rate)


def render_chop(project: Project, chop: Chop, sample_rate: int, project_bpm: float) -> np.ndarray:
    """Return one grid-sized float32 stereo chop; see contracts R3/R19."""
    analysis = load_analysis(project, chop.source)
    times = chop_times(analysis, chop.start_downbeat, chop.length_beats, chop.start_offset_beats)
    path = f"chops.{chop.id}"
    if times is None:
        raise MuestraError.one(
            "chop_overrun",
            "The chop does not fit the analyzed beat grid.",
            path=path,
            value=chop.model_dump(),
            hint="Choose an earlier downbeat or shorten length_beats.",
        )
    start_s, end_s = times
    ref = analysis.file if chop.stem == "mix" else getattr(analysis.stems, chop.stem, None)
    if ref is None:
        raise MuestraError.one(
            "ref_not_found",
            "Analysis has no requested stem.",
            path=path + ".stem",
            value=chop.stem,
            hint="Run source.separate or choose stem: mix.",
        )
    source, content = decode(project, project.resolve(ref), sample_rate)
    frames = beat_frame(chop.length_beats, project_bpm, sample_rate)
    key = digest(
        CACHE_ABI,
        content,
        analysis.model_dump(mode="json"),
        chop.model_dump(mode="json"),
        sample_rate,
        project_bpm,
    )
    cached = cache_read(project, "chops", key)
    if cached is not None:
        return cached
    first, last = round(start_s * sample_rate), round(end_s * sample_rate)
    if last > len(source) + 1:
        raise MuestraError.one(
            "chop_overrun",
            "The audio ends before the analyzed chop.",
            path=path,
            value=end_s,
            hint="Regenerate analysis for this source file.",
        )
    if chop.warp.mode == "resample":
        # A small input fade avoids circular-FFT ringing from a hard cut.
        audio = vinyl(fade(source[first:last], sample_rate, chop.fade_ms), frames)
        audio = stretch(audio, sample_rate, frames, chop.warp.pitch_semitones)
    else:
        # Split at SOURCE grid beats, including fractional endpoints. Give R3
        # context on both sides; overlap adjacent results by 5 ms with a
        # raised-cosine crossfade, while keeping their exact grid boundaries.
        grid = np.asarray(analysis.beats_s)
        base = int(np.argmin(np.abs(grid - analysis.downbeats_s[chop.start_downbeat - 1])))
        start_pos = base + chop.start_offset_beats
        end_pos = start_pos + chop.length_beats
        positions = [start_pos, *range(math.floor(start_pos) + 1, math.ceil(end_pos)), end_pos]
        times_s = [start_s, *grid[math.floor(start_pos) + 1 : math.ceil(end_pos)], end_s]
        audio = np.zeros((frames, 2), np.float32)
        weights = np.zeros(frames, np.float64)
        cross = round(0.005 * sample_rate)
        context = round(0.08 * sample_rate)
        for i in range(len(positions) - 1):
            a = round(times_s[i] * sample_rate)
            b = round(times_s[i + 1] * sample_rate)
            out_a = beat_frame(positions[i] - start_pos, project_bpm, sample_rate)
            out_b = beat_frame(positions[i + 1] - start_pos, project_bpm, sample_rate)
            speed = (b - a) / max(1, out_b - out_a)
            left = min(context, a)
            right = min(context, len(source) - b)
            segment = source[a - left : b + right]
            warped = stretch(
                segment, sample_rate, round(len(segment) / speed), chop.warp.pitch_semitones
            )
            lo = max(0, out_a - (cross if i else 0))
            hi = min(frames, out_b + (cross if i < len(positions) - 2 else 0))
            offset = round(left / speed) + lo - out_a
            piece = fit(warped[max(0, offset) : max(0, offset) + hi - lo], hi - lo)
            window = np.ones(hi - lo)
            if i and out_a > lo:
                n = min(2 * cross, len(window))
                window[:n] = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n))
            if i < len(positions) - 2 and hi > out_b:
                n = min(2 * cross, len(window))
                window[-n:] = 0.5 + 0.5 * np.cos(np.linspace(0, np.pi, n))
            audio[lo:hi] += piece * window[:, None]
            weights[lo:hi] += window
        audio /= np.maximum(weights, 1e-12)[:, None]
    audio = fade(fit(audio, frames), sample_rate, chop.fade_ms)
    cache_write(project, "chops", key, audio)
    return audio


def _sample_clip(project, clip, song, sr, decoded):
    path = (
        library.resolve(project, clip.sample)
        if clip.sample.startswith("library://")
        else project.resolve(clip.sample)
    )
    if path not in decoded:
        decoded[path] = decode(project, path, sr)[0]
    audio = decoded[path]
    frames = beat_frame(clip.length_beats, song.meta.bpm, sr)
    if clip.warp:
        hit = library.lookup(project, clip.sample)
        if hit is None or hit.bpm is None:
            raise MuestraError.one(
                "invalid_value",
                "Sample warp requires an indexed BPM.",
                path="clips.sample.warp",
                value=clip.sample,
                hint="Index the sample BPM or remove warp.",
            )
        consumed = beat_frame(clip.length_beats, hit.bpm, sr)
        audio = fit(audio, consumed)
        if clip.warp.mode == "resample":
            audio = vinyl(fade(audio, sr), frames)
            audio = stretch(audio, sr, frames, clip.warp.pitch_semitones)
        else:
            audio = stretch(audio, sr, frames, clip.warp.pitch_semitones)
    return fade(fit(audio, frames), sr)


def clip_audio(project: Project, song: Song, clip: Clip, sr: int, decoded=None):
    if clip.chop:
        chop = next(c for c in song.chops if c.id == clip.chop)
        return render_chop(project, chop, sr, song.meta.bpm), chop.length_beats
    return _sample_clip(
        project, clip, song, sr, {} if decoded is None else decoded
    ), clip.length_beats


def read_notes(project: Project, track: Track) -> list[MidiNote]:
    if track.notes is not None:
        return list(track.notes)
    try:
        import mido
    except ImportError:
        _dependency("mido")
    try:
        midi = mido.MidiFile(project.resolve(track.midi))
        if midi.type == 2 or midi.ticks_per_beat <= 0:
            raise ValueError("MIDI must use a shared PPQ timeline (type 0 or 1).")
        active = {}
        notes = []
        tick = 0
        for msg in mido.merge_tracks(midi.tracks):
            tick += msg.time
            if msg.type == "note_on" and msg.velocity:
                active.setdefault((msg.channel, msg.note), []).append((tick, msg.velocity))
            elif msg.type in ("note_on", "note_off"):
                pending = active.get((msg.channel, msg.note), [])
                if pending:
                    start, velocity = pending.pop(0)
                    if tick > start:
                        notes.append(
                            MidiNote(
                                beat=1 + start / midi.ticks_per_beat,
                                pitch=msg.note,
                                velocity=velocity,
                                length_beats=(tick - start) / midi.ticks_per_beat,
                            )
                        )
        if any(active.values()):
            raise ValueError("MIDI contains note-on events with no note-off.")
        return sorted(notes, key=lambda n: (n.beat, n.pitch))
    except (OSError, ValueError, EOFError) as exc:
        raise MuestraError.one(
            "invalid_midi",
            str(exc),
            path=f"tracks.{track.id}.midi",
            value=track.midi,
            hint="Export a valid type 0/1 MIDI file with paired notes.",
        ) from exc


def _add(destination, audio, frame, end=None):
    end = min(len(destination), frame + len(audio), end if end is not None else len(destination))
    if end > max(frame, 0):
        destination[max(frame, 0) : end] += audio[max(0, -frame) : end - frame]


def arrange(project: Project, song: Song, sr: int, n_frames: int):
    """Return raw tracks, pre-insert lanes and sample-position key events."""
    raw, lanes, events = {}, {}, {}
    decoded, chops = {}, {}
    bpm = song.meta.bpm
    for track in song.tracks:
        audio = np.zeros((n_frames, 2), np.float32)
        raw[track.id] = audio
        lo = beat_frame((track.at_bar - 1) * BEATS_PER_BAR, bpm, sr)
        hi = min(
            n_frames,
            beat_frame(
                ((track.until_bar or song.meta.length_bars + 1) - 1) * BEATS_PER_BAR, bpm, sr
            ),
        )
        if track.clips is not None:
            for clip in track.clips:
                if clip.chop and clip.chop in chops:
                    piece, length = chops[clip.chop]
                else:
                    piece, length = clip_audio(project, song, clip, sr, decoded)
                    if clip.chop:
                        chops[clip.chop] = piece, length
                start = (clip.at_bar - 1) * BEATS_PER_BAR + clip.offset_beats
                for repeat in range(clip.repeat):
                    frame = beat_frame(start + repeat * length, bpm, sr)
                    end = beat_frame(start + (repeat + 1) * length, bpm, sr)
                    # At awkward tempi each grid slot can differ by one frame.
                    _add(audio, fit(piece, end - frame), frame, hi)
        elif track.pattern is not None:
            pattern = track.pattern
            all_events = []
            step = BEATS_PER_BAR / pattern.steps_per_bar
            for name, lane in pattern.lanes.items():
                ref = (
                    library.resolve(project, lane.sample)
                    if lane.sample.startswith("library://")
                    else project.resolve(lane.sample)
                )
                if ref not in decoded:
                    decoded[ref] = decode(project, ref, sr)[0]
                # One-shots retain their attacks; tiny fade-out only at file end.
                sample = pan_gain(fade(decoded[ref], sr, 1, start=False), lane.gain_db, lane.pan)
                lane_audio = np.zeros_like(audio)
                hits = []
                velocities = parse_steps(lane.steps)
                for bar in range(track.at_bar - 1, song.meta.length_bars, pattern.bars):
                    for index, velocity in enumerate(velocities):
                        if velocity is None:
                            continue
                        beat = (
                            bar * BEATS_PER_BAR
                            + index * step
                            + (pattern.swing * step / 3 if index % 2 else 0)
                        )
                        frame = beat_frame(beat, bpm, sr)
                        if lo <= frame < hi:
                            _add(lane_audio, sample * velocity, frame, hi)
                            hits.append(frame)
                lanes[track.id, name] = lane_audio
                lane_audio[lo:hi] = fade(lane_audio[lo:hi], sr, start=False)
                events[track.id, name] = [] if track.mute else hits
                all_events.extend(hits)
                audio += lane_audio
            events[track.id, None] = [] if track.mute else sorted(set(all_events))
        else:
            # Notes use absolute song beats; the window gates audio without
            # shifting or retriggering an already sustained note at entry.
            notes = read_notes(project, track)
            events[track.id, None] = (
                []
                if track.mute
                else [
                    beat_frame(n.beat - 1, bpm, sr)
                    for n in notes
                    if lo <= beat_frame(n.beat - 1, bpm, sr) < hi
                ]
            )
            try:
                rendered = (
                    np.zeros_like(audio)
                    if track.mute
                    else np.asarray(
                        plugins.render_instrument(
                            project, track.instrument, notes, sr, n_frames, bpm=bpm
                        )
                    )
                )
            except NotImplementedError as exc:
                raise MuestraError.one(
                    "instrument_unavailable",
                    "Instrument rendering is unavailable.",
                    path=f"tracks.{track.id}.instrument.plugin",
                    value=track.instrument.plugin,
                    hint="Configure the instrument plugin and complete the WP3 plugin host.",
                ) from exc
            if (
                rendered.shape != audio.shape
                or rendered.dtype != np.float32
                or not np.isfinite(rendered).all()
            ):
                raise MuestraError.one(
                    "invalid_audio",
                    "Instrument returned invalid audio.",
                    path=f"tracks.{track.id}.instrument",
                    value=str(rendered.shape),
                    hint="Plugin host must return finite float32 (n_frames, 2).",
                )
            audio[:] = rendered
        audio[:lo] = 0
        audio[hi:] = 0
        # Smooth cuts at track windows, including sustained notes/delay input.
        if hi > lo:
            audio[lo:hi] = fade(audio[lo:hi], sr, start=(lo > 0))
    return raw, lanes, events


def render_audio(project: Project, song: Song, range_bars: tuple[int, int]):
    issues = song.check()
    if issues:
        raise MuestraError(issues)
    start, end = range_bars
    if not 1 <= start < end <= song.meta.length_bars + 1:
        raise MuestraError.one(
            "invalid_range",
            "range_bars is [start, end), end exclusive.",
            path="range_bars",
            value=range_bars,
            hint=f"Use 1 <= start < end <= {song.meta.length_bars + 1}.",
        )
    sr = project.config.sample_rate
    # Process the entire song so meter/normalization and dynamics history are
    # identical for full and range renders; crop only after mastering.
    n = beat_frame(song.meta.length_bars * BEATS_PER_BAR, song.meta.bpm, sr)
    raw, lanes, events = arrange(project, song, sr, n)
    auto = Automation(song, sr)
    processed = {}
    tracks = {t.id: t for t in song.tracks}
    positions = np.arange(n)

    def process(track_id):
        if track_id in processed:
            return processed[track_id]
        track = tracks[track_id]
        for fx in track.fx:
            if fx.type == "sidechain":
                process(fx.key_track)
        if track.mute:
            audio = np.zeros_like(raw[track_id])
        else:
            audio = apply_fx(
                raw[track_id],
                track.fx,
                sr,
                song.meta.bpm,
                auto,
                f"tracks.{track_id}",
                project,
                events,
                processed,
            )
            audio = pan_gain(
                audio,
                auto.value(f"tracks.{track_id}.gain_db", positions, track.gain_db),
                auto.value(f"tracks.{track_id}.pan", positions, track.pan),
            )
            lo = beat_frame((track.at_bar - 1) * BEATS_PER_BAR, song.meta.bpm, sr)
            hi = beat_frame(
                ((track.until_bar or song.meta.length_bars + 1) - 1) * BEATS_PER_BAR,
                song.meta.bpm,
                sr,
            )
            audio[:lo] = 0
            audio[hi:] = 0
            audio[lo:hi] = fade(audio[lo:hi], sr, start=(lo > 0))
        processed[track_id] = audio
        return audio

    for track in song.tracks:
        process(track.id)
    premaster = sum((processed[t.id] for t in song.tracks), start=np.zeros((n, 2), np.float32))
    mix = master(premaster, song, sr, auto, project, events, processed)
    a = beat_frame((start - 1) * BEATS_PER_BAR, song.meta.bpm, sr)
    b = beat_frame((end - 1) * BEATS_PER_BAR, song.meta.bpm, sr)

    def crop(audio):
        return fade(audio[a:b], sr, start=False)

    return (
        crop(mix),
        {k: crop(v) for k, v in processed.items()},
        {k: crop(np.zeros_like(v) if tracks[k[0]].mute else v) for k, v in lanes.items()},
        crop(premaster),
    )


def render_song(
    project: Project,
    song: Song,
    version: str,
    range_bars: tuple[int, int],
    *,
    stems: bool = True,
    directory: Path | None = None,
) -> Path:
    """Write stereo PCM24 mix and aligned FLOAT stems into renders/<version>
    (or ``directory``: the render tool stages there, see render/staging.py)."""
    from pydantic import TypeAdapter, ValidationError

    try:
        TypeAdapter(VersionStr).validate_python(version)
    except ValidationError as exc:
        raise MuestraError.one(
            "invalid_value",
            "Invalid version id.",
            path="version",
            value=version,
            hint="Use v001, v002, ...",
        ) from exc
    mix, tracks, lanes, _ = render_audio(project, song, range_bars)
    directory = directory or project.renders_dir / version
    directory.mkdir(parents=True, exist_ok=True)
    sf.write(directory / "mix.wav", mix, project.config.sample_rate, subtype="PCM_24")
    stem_dir = directory / "stems"
    # Remove only renderer-owned, stale WAVs on a re-render (e.g. stems=False).
    if stem_dir.is_dir():
        for old in stem_dir.glob("*.wav"):
            old.unlink()
    if stems:
        stem_dir.mkdir(exist_ok=True)
        for name, audio in tracks.items():
            sf.write(stem_dir / f"{name}.wav", audio, project.config.sample_rate, subtype="FLOAT")
        for (track, lane), audio in lanes.items():
            sf.write(
                stem_dir / f"{track}.{lane}.wav", audio, project.config.sample_rate, subtype="FLOAT"
            )
    return directory
