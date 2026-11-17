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

#: key in arrange()'s events dict listing tracks whose clips loop across the
#: song end (their last repeat crossfades into bar 1; no end fade).
LOOP_WRAP = ("__loop_wrap__", None)


def beat_frame(beat: float, bpm: float, sample_rate: int) -> int:
    return round(beat * 60 / bpm * sample_rate)


def render_chop(project: Project, chop: Chop, sample_rate: int, project_bpm: float) -> np.ndarray:
    """Return one grid-sized float32 stereo chop with ``fade_ms`` edge fades
    (previews); see contracts R3/R19. Arrangements use :func:`chop_audio`."""
    frames = beat_frame(chop.length_beats, project_bpm, sample_rate)
    audio = chop_audio(project, chop, sample_rate, project_bpm)
    return fade(fit(audio, frames), sample_rate, chop.fade_ms)


def render_chop_loop(
    project: Project, chop: Chop, sample_rate: int, project_bpm: float, repeats: int = 2
) -> np.ndarray:
    """``repeats`` back-to-back repeats of a chop, crossfaded at every seam
    and at the wrap, so a player looping the file hears no gap (WP13)."""
    xf = xfade_frames(sample_rate, chop.fade_ms)
    audio = chop_audio(project, chop, sample_rate, project_bpm, xf)
    slot = beat_frame(chop.length_beats, project_bpm, sample_rate)
    placements = [(r * slot, (r + 1) * slot, audio, xf) for r in range(repeats)]
    return place_clips(placements, repeats * slot, 0, repeats * slot)[0]


def xfade_frames(sample_rate: int, ms: float) -> int:
    return max(0, round(sample_rate * ms / 1000))


def chop_audio(
    project: Project, chop: Chop, sample_rate: int, project_bpm: float, tail: int | None = None
) -> np.ndarray:
    """Unfaded warped chop: its grid slot followed by ``tail`` frames (default:
    the chop's ``fade_ms``) of the source that continues past the chop's end,
    warped at the same rate, for crossfading into the next repeat (WP13).
    The tail is silence where the source ends."""
    tail = xfade_frames(sample_rate, chop.fade_ms) if tail is None else tail
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
        tail,
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
    total = frames + tail
    if chop.warp.mode == "resample":
        # Extra source before (pre-roll) and after (tail + guard): the input
        # fades that avoid circular-FFT ringing then fall outside the kept part.
        rate = (last - first) / max(1, frames)
        pad = round(0.02 * sample_rate)
        before = round(pad * rate)
        after = math.ceil(tail * rate) + round(pad * rate)
        lo, hi = max(0, first - before), min(len(source), last + after)
        segment = np.zeros((before + (last - first) + after, 2), np.float32)
        segment[lo - (first - before) : hi - (first - before)] = source[lo:hi]
        out = round(len(segment) / rate)
        audio = vinyl(fade(segment, sample_rate, 5), out)
        audio = stretch(audio, sample_rate, out, chop.warp.pitch_semitones)
        skip = round(before / rate)
        audio = audio[skip : skip + total]
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
        if tail:
            # The tail continues at the last beat's warp speed.
            last_beat = (times_s[-1] - times_s[-2]) / max(positions[-1] - positions[-2], 1e-9)
            tail_beats = tail / sample_rate * project_bpm / 60
            positions.append(end_pos + tail_beats)
            times_s.append(end_s + tail_beats * last_beat)
        frames_all = beat_frame(positions[-1] - start_pos, project_bpm, sample_rate)
        audio = np.zeros((frames_all, 2), np.float32)
        weights = np.zeros(frames_all, np.float64)
        cross = round(0.005 * sample_rate)
        context = round(0.08 * sample_rate)
        for i in range(len(positions) - 1):
            a = round(times_s[i] * sample_rate)
            b = round(times_s[i + 1] * sample_rate)
            if a >= len(source):
                continue  # tail past the end of the record: silence
            out_a = beat_frame(positions[i] - start_pos, project_bpm, sample_rate)
            out_b = beat_frame(positions[i + 1] - start_pos, project_bpm, sample_rate)
            speed = (b - a) / max(1, out_b - out_a)
            left = min(context, a)
            right = max(0, min(context, len(source) - b))
            segment = source[min(a, len(source)) - left : min(b, len(source)) + right]
            warped = stretch(
                segment, sample_rate, round(len(segment) / speed), chop.warp.pitch_semitones
            )
            lo = max(0, out_a - (cross if i else 0))
            hi = min(frames_all, out_b + (cross if i < len(positions) - 2 else 0))
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
    audio = fit(audio, total)
    cache_write(project, "chops", key, audio)
    return audio


def _sample_clip(project, clip, song, sr, decoded, tail=0):
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
        extra = math.ceil(tail * consumed / max(1, frames))
        audio = fit(audio, consumed + extra)
        total = round((consumed + extra) * frames / max(1, consumed))
        if clip.warp.mode == "resample":
            audio = vinyl(fade(audio, sr, start=False), total)
            audio = stretch(audio, sr, total, clip.warp.pitch_semitones)
        else:
            audio = stretch(audio, sr, total, clip.warp.pitch_semitones)
    return fit(audio, frames + tail)


#: edge fade / seam crossfade of sample clips (chops use their ``fade_ms``).
SAMPLE_FADE_MS = 5.0


def clip_audio(project: Project, song: Song, clip: Clip, sr: int, decoded=None):
    """``(audio, length_beats, fade_frames)``: the unfaded clip followed by a
    tail of ``fade_frames`` for seam crossfades (WP13)."""
    if clip.chop:
        chop = next(c for c in song.chops if c.id == clip.chop)
        xf = xfade_frames(sr, chop.fade_ms)
        return chop_audio(project, chop, sr, song.meta.bpm, xf), chop.length_beats, xf
    xf = xfade_frames(sr, SAMPLE_FADE_MS)
    audio = _sample_clip(project, clip, song, sr, {} if decoded is None else decoded, xf)
    return audio, clip.length_beats, xf


def _ramp_in(n: int) -> np.ndarray:
    return (0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n))).astype(np.float32)


def _equal_power(n: int) -> tuple[np.ndarray, np.ndarray]:
    t = (np.arange(n) + 0.5) / n * (np.pi / 2)
    return np.sin(t).astype(np.float32), np.cos(t).astype(np.float32)


def place_clips(placements, n_frames: int, lo: int, hi: int) -> tuple[np.ndarray, bool]:
    """Sum clip placements ``[(frame, end, audio_with_tail, fade_frames)]``.

    Contiguous placements (one ends on the exact frame where the next
    starts) are joined by an equal-power crossfade: the first one's tail
    (source past its end) fades out while the next one fades in, so there is
    no level dip at the seam. Fades to/from silence only happen at the first
    start and last end of a contiguous run. When a run reaches the end of
    the song and another starts at frame 0 (inside the track window), the
    last tail wraps onto the song start: the render loops seamlessly.
    Returns ``(audio, wraps)``. Placement stays sample-accurate."""
    out = np.zeros((n_frames, 2), np.float32)
    order = sorted(range(len(placements)), key=lambda i: (placements[i][0], i))
    nxt: dict[int, int] = {}
    prv: dict[int, int] = {}
    open_ends: dict[int, list[int]] = {}
    for i in order:
        frame, end = placements[i][0], placements[i][1]
        waiting = open_ends.get(frame)
        if waiting:
            j = waiting.pop(0)
            nxt[j], prv[i] = i, j
        open_ends.setdefault(end, []).append(i)
    wraps = False
    if lo == 0 and hi >= n_frames:
        firsts = [i for i in order if placements[i][0] == 0 and i not in prv]
        lasts = [i for i in order if placements[i][1] >= n_frames and i not in nxt]
        if firsts and lasts:
            nxt[lasts[0]], prv[firsts[0]] = firsts[0], lasts[0]
            wraps = True

    def xf(j: int, i: int) -> int:
        frame_j, end_j, audio_j, fade_j = placements[j]
        tail_j = len(audio_j) - (end_j - frame_j)
        return max(
            0, min(fade_j, placements[i][3], tail_j, (placements[i][1] - placements[i][0]) // 2)
        )

    for i in order:
        frame, end, audio, fade_n = placements[i]
        length = end - frame
        if length <= 0:
            continue
        body = fit(audio, length)
        if i in prv:
            n = xf(prv[i], i)
            if n:
                body[:n] *= _equal_power(n)[0][:, None]
        else:
            n = min(fade_n, length // 2)
            if n > 1:
                body[:n] *= _ramp_in(n)[:, None]
        tail = None
        if i in nxt:
            n = xf(i, nxt[i])
            if n:
                tail = audio[length : length + n] * _equal_power(n)[1][:, None]
        else:
            n = min(fade_n, length // 2)
            if n > 1:
                body[-n:] *= _ramp_in(n)[::-1, None]
        _add(out, body, frame, hi)
        if tail is not None:
            if end >= n_frames and wraps and nxt[i] in prv and placements[nxt[i]][0] == 0:
                _add(out, tail, 0, hi)
            else:
                _add(out, tail, end, hi)
    return out, wraps


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
    wrapping: set[str] = set()
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
            placements = []
            for clip in track.clips:
                if clip.chop and clip.chop in chops:
                    piece, length, xf = chops[clip.chop]
                else:
                    piece, length, xf = clip_audio(project, song, clip, sr, decoded)
                    if clip.chop:
                        chops[clip.chop] = piece, length, xf
                start = (clip.at_bar - 1) * BEATS_PER_BAR + clip.offset_beats
                for repeat in range(clip.repeat):
                    frame = beat_frame(start + repeat * length, bpm, sr)
                    # At awkward tempi each grid slot can differ by one frame.
                    end = beat_frame(start + (repeat + 1) * length, bpm, sr)
                    placements.append((frame, end, piece, xf))
            placed, wrap = place_clips(placements, n_frames, lo, hi)
            audio += placed
            if wrap:
                wrapping.add(track.id)
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
        # Smooth cuts at track windows, including sustained notes/delay input;
        # a track that loops across the song end keeps its wrap continuous.
        if hi > lo and track.id not in wrapping:
            audio[lo:hi] = fade(audio[lo:hi], sr, start=(lo > 0))
    events[LOOP_WRAP] = sorted(wrapping)
    return raw, lanes, events


#: loop wrap (WP13): a track that loops across the song end is processed
#: WRAP_BLEND_S past the end (its input wraps to bar 1, automation holds its
#: last value) and that warm continuation is crossfaded into the cold start,
#: so effects (filters, chorus LFO, delays) and automation flow into bar 1.
WRAP_BLEND_S = 0.1


def _wrap_fx(raw, track, sr, bpm, auto, project, events, processed):
    n = len(raw)
    post = min(n // 4, round(WRAP_BLEND_S * sr))
    if post < 2:
        return apply_fx(
            raw, track.fx, sr, bpm, auto, f"tracks.{track.id}", project, events, processed
        )
    extended = {key: [*hits, *(e + n for e in hits if e < post)] for key, hits in events.items()}
    keys = {k: np.concatenate([v, v[:post]]) for k, v in processed.items()}
    out = apply_fx(
        np.concatenate([raw, raw[:post]]),
        track.fx,
        sr,
        bpm,
        auto,
        f"tracks.{track.id}",
        project,
        extended,
        keys,
    )
    audio, warm = out[:n].copy(), out[n : n + post]
    t = np.linspace(0, 1, post, dtype=np.float32)[:, None]
    audio[:post] = warm * (1 - t) + audio[:post] * t
    return audio


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
    wrapping = set(events.pop(LOOP_WRAP, []))
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
        elif track_id in wrapping:
            audio = _wrap_fx(
                raw[track_id], track, sr, song.meta.bpm, auto, project, events, processed
            )
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
        if not track.mute:
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
            if track_id not in wrapping:
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
        # A full render is a loop (the UI plays it seamlessly): its end is not
        # faded; tracks that don't wrap were already faded at the song end.
        return fade(audio[a:b], sr, start=False) if b < n else audio[a:b].copy()

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
