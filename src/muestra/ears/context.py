"""Everything the metrics need about one render version, loaded once.

:class:`RenderContext` holds the decoded mix, track stems and drum-lane
stems, the version's song snapshot, ``meta.json``, the per-chop source
analysis and the :class:`Timeline` (the song's musical events converted
to render-relative seconds). Loading is the only I/O ears does; every
metric module works on this object.

Timeline semantics follow docs/contracts.md (render output format R7,
patterns, swing R13, MIDI R4, track windows R20). Two points the
contract leaves open match the renderer (confirmed in WP11, see
docs/ears.md): a pattern's tiles are anchored at the track's ``at_bar``,
and clip instances outside the track window are silent.
"""

from __future__ import annotations

import json
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml
from pydantic import ValidationError

from ..errors import MuestraError, issues_from_validation_error
from ..project.core import Project
from ..project.validate import chop_times, load_analysis
from ..schemas.analysis import Analysis
from ..schemas.render_meta import RenderMeta, RenderTrackMeta, StemLaneRef
from ..schemas.song import BEATS_PER_BAR, Song, Track, parse_steps
from . import dsp
from .config import EarsConfig, load_config

__all__ = ["RenderContext", "Timeline", "Event", "ClipInstance", "load_context", "render_dir"]


@dataclass(frozen=True)
class Event:
    """A sounding event from the song: a drum hit or a note."""

    t: float  # render-relative seconds
    velocity: float = 1.0  # 0..1
    lane: str | None = None
    pitch: int | None = None
    dur_s: float | None = None


@dataclass(frozen=True)
class ClipInstance:
    track: str
    clip_index: int
    repeat_index: int
    chop: str | None
    start_s: float
    end_s: float

    @property
    def ref(self) -> str:
        return f"tracks.{self.track}.clips[{self.clip_index}]"


class Timeline:
    """Song positions <-> render seconds and the song's expected events."""

    def __init__(self, song: Song, meta: RenderMeta, project_root: Path, duration_s: float):
        self.song = song
        self.bpm = float(meta.bpm)
        self.spb = 60.0 / self.bpm
        self.start_bar, self.end_bar = meta.range_bars
        self.offset_beats = (self.start_bar - 1) * BEATS_PER_BAR
        self.duration_s = duration_s
        self.root = project_root
        self.tracks: dict[str, Track] = {t.id: t for t in song.tracks}

    # -- conversions -------------------------------------------------------
    def to_s(self, song_beat0: float) -> float:
        """0-indexed song beats -> render-relative seconds."""
        return (song_beat0 - self.offset_beats) * self.spb

    def where(self, t_s: float) -> tuple[int, float]:
        """Render seconds -> (1-indexed bar, 1-indexed beat in bar)."""
        pos = t_s / self.spb + self.offset_beats
        bar = int(pos // BEATS_PER_BAR) + 1
        beat = round(pos - (bar - 1) * BEATS_PER_BAR + 1.0, 2)
        if beat >= BEATS_PER_BAR + 1.0:  # rounding spill
            bar, beat = bar + 1, 1.0
        return max(bar, 1), max(beat, 1.0)

    def window_s(self, track: Track) -> tuple[float, float]:
        """Render-relative [start, end) seconds in which a track may sound."""
        until = track.until_bar or self.song.meta.length_bars + 1
        lo = self.to_s((max(track.at_bar, self.start_bar) - 1) * BEATS_PER_BAR)
        hi = self.to_s((min(until, self.end_bar) - 1) * BEATS_PER_BAR)
        return max(0.0, lo), min(self.duration_s, max(lo, hi))

    def _in_render(self, t: float) -> bool:
        return -1e-9 <= t < self.duration_s - 1e-9

    # -- events ------------------------------------------------------------
    def lane_hits(self, track_id: str) -> dict[str, list[Event]]:
        """Drum hits per lane (pattern tracks), honouring tiles, swing, window."""
        track = self.tracks.get(track_id)
        if track is None or track.pattern is None or track.mute:
            return {}
        pat = track.pattern
        step_beats = BEATS_PER_BAR / pat.steps_per_bar
        swing = pat.swing * step_beats / 3.0
        until = track.until_bar or self.song.meta.length_bars + 1
        parsed = {name: parse_steps(lane.steps) for name, lane in pat.lanes.items()}
        out: dict[str, list[Event]] = {name: [] for name in pat.lanes}
        for bar in range(max(track.at_bar, self.start_bar), min(until, self.end_bar)):
            tile = (bar - track.at_bar) % pat.bars
            base = tile * pat.steps_per_bar
            for name, steps in parsed.items():
                for s in range(pat.steps_per_bar):
                    vel = steps[base + s]
                    if vel is None:
                        continue
                    beat0 = (bar - 1) * BEATS_PER_BAR + s * step_beats
                    if s % 2 == 1:
                        beat0 += swing
                    t = self.to_s(beat0)
                    if self._in_render(t):
                        out[name].append(Event(t=t, velocity=float(vel), lane=name))
        return out

    def notes(self, track_id: str) -> list[Event]:
        """Note events of an instrument track (inline notes or the MIDI file)."""
        track = self.tracks.get(track_id)
        if track is None or track.instrument is None or track.mute:
            return []
        raw: list[tuple[float, int, float, float]] = []  # beat0, pitch, len_beats, vel
        inline = self.song.track_notes(track)  # notes + looped phrases (WP12)
        if inline is not None:
            raw = [(n.beat - 1.0, int(n.pitch), n.length_beats, n.velocity / 127.0) for n in inline]
        elif track.midi is not None:
            raw = read_midi_notes(self._resolve(track.midi))
        until = track.until_bar or self.song.meta.length_bars + 1
        lo_b = (max(track.at_bar, self.start_bar) - 1) * BEATS_PER_BAR
        hi_b = (min(until, self.end_bar) - 1) * BEATS_PER_BAR
        events = []
        for beat0, pitch, length, vel in raw:
            if lo_b - 1e-9 <= beat0 < hi_b - 1e-9:
                end_b = min(beat0 + length, hi_b)
                events.append(
                    Event(
                        t=self.to_s(beat0),
                        velocity=vel,
                        pitch=pitch,
                        dur_s=(end_b - beat0) * self.spb,
                    )
                )
        return sorted(events, key=lambda e: e.t)

    def clip_instances(self, track_id: str) -> list[ClipInstance]:
        """Every clip repeat that overlaps the render, in time order."""
        track = self.tracks.get(track_id)
        if track is None or track.clips is None or track.mute:
            return []
        chops = {c.id: c for c in self.song.chops}
        w_lo, w_hi = self.window_s(track)
        out: list[ClipInstance] = []
        for ci, clip in enumerate(track.clips):
            if clip.chop is not None:
                chop = chops.get(clip.chop)
                length = chop.length_beats if chop else 0.0
            else:
                length = clip.length_beats or 0.0
            if length <= 0:
                continue
            start0 = (clip.at_bar - 1) * BEATS_PER_BAR + clip.offset_beats
            for r in range(clip.repeat):
                s = self.to_s(start0 + r * length)
                e = s + length * self.spb
                if e <= w_lo + 1e-9 or s >= w_hi - 1e-9:
                    continue
                out.append(ClipInstance(track.id, ci, r, clip.chop, s, e))
        return sorted(out, key=lambda c: (c.start_s, c.clip_index))

    def triggers(self, key_track: str, key_lane: str | None) -> list[float]:
        """Sidechain trigger times from the key track's events (R8)."""
        track = self.tracks.get(key_track)
        if track is None:
            return []
        if track.pattern is not None:
            hits = self.lane_hits(key_track)
            lanes = [key_lane] if key_lane else list(hits)
            times = sorted(e.t for ln in lanes for e in hits.get(ln, []))
        elif track.instrument is not None:
            times = [e.t for e in self.notes(key_track)]
        else:
            return []
        dedup: list[float] = []
        for t in times:
            if not dedup or t - dedup[-1] > 0.002:
                dedup.append(t)
        return dedup

    def _resolve(self, ref: str) -> Path | None:
        try:
            return self.root / Path(ref)
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# MIDI (minimal SMF reader; ticks are project beats, R4)
# ---------------------------------------------------------------------------


def read_midi_notes(path: Path | None) -> list[tuple[float, int, float, float]]:
    """(beat0, pitch, length_beats, velocity 0..1) from a format 0/1 SMF.

    Tempo/meter events are ignored (R4: ticks/ppq are project beats).
    Returns [] when the file is missing or unreadable.
    """
    if path is None or not path.is_file():
        return []
    try:
        data = path.read_bytes()
        if data[:4] != b"MThd":
            return []
        hlen, _fmt, ntrk, ppq = struct.unpack(">IHHH", data[4:14])
        if ppq & 0x8000 or ppq == 0:
            return []
        pos = 8 + hlen
        notes: list[tuple[float, int, float, float]] = []
        for _ in range(ntrk):
            if data[pos : pos + 4] != b"MTrk":
                break
            (tlen,) = struct.unpack(">I", data[pos + 4 : pos + 8])
            notes += _smf_track(data[pos + 8 : pos + 8 + tlen], ppq)
            pos += 8 + tlen
        return sorted(notes)
    except (OSError, IndexError, struct.error):
        return []


def _vlq(buf: bytes, i: int) -> tuple[int, int]:
    v = 0
    while True:
        b = buf[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, i


def _smf_track(buf: bytes, ppq: int) -> list[tuple[float, int, float, float]]:
    out = []
    open_notes: dict[tuple[int, int], list[tuple[int, int]]] = {}
    tick = 0
    i = 0
    running = 0
    while i < len(buf):
        delta, i = _vlq(buf, i)
        tick += delta
        status = buf[i]
        if status & 0x80:
            i += 1
            if status < 0xF0:
                running = status
        else:
            status = running
        kind, ch = status & 0xF0, status & 0x0F
        if status == 0xFF:
            i += 1  # meta type
            length, i = _vlq(buf, i)
            i += length
        elif status in (0xF0, 0xF7):
            length, i = _vlq(buf, i)
            i += length
        elif kind in (0x80, 0x90):
            pitch, vel = buf[i], buf[i + 1]
            i += 2
            if kind == 0x90 and vel > 0:
                open_notes.setdefault((ch, pitch), []).append((tick, vel))
            else:
                stack = open_notes.get((ch, pitch))
                if stack:
                    start, v = stack.pop(0)
                    out.append((start / ppq, pitch, max(tick - start, 1) / ppq, v / 127.0))
        elif kind in (0xA0, 0xB0, 0xE0):
            i += 2
        elif kind in (0xC0, 0xD0):
            i += 1
        else:
            break
    return out


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


@dataclass
class RenderContext:
    project: Project
    version: str
    dir: Path
    meta: RenderMeta
    song: Song
    sr: int
    mix: np.ndarray  # (n, 2)
    stems: dict[str, np.ndarray]  # track id -> (n, 2)
    lanes: dict[str, dict[str, np.ndarray]]  # track id -> lane -> (n, 2)
    timeline: Timeline
    config: EarsConfig
    analyses: dict[str, Analysis | None] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    _mono: dict[str, np.ndarray] = field(default_factory=dict)
    _spec: dict[str, dsp.Spectrogram] = field(default_factory=dict)
    _wpower: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return self.mix.shape[0] / self.sr

    def mono(self, key: str) -> np.ndarray:
        """Cached mono of 'mix', '<track>' or '<track>.<lane>'."""
        if key not in self._mono:
            self._mono[key] = dsp.mono(self.audio(key))
        return self._mono[key]

    def audio(self, key: str) -> np.ndarray:
        if key == "mix":
            return self.mix
        if "." in key:
            tid, lane = key.split(".", 1)
            return self.lanes[tid][lane]
        return self.stems[key]

    def wpower(self, key: str) -> np.ndarray:
        """Cached per-sample K-weighted power of 'mix'/'<track>'/'<track>.<lane>'."""
        if key not in self._wpower:
            self._wpower[key] = dsp.weighted_power(self.audio(key).astype(np.float64), self.sr)
        return self._wpower[key]

    def spec(self, key: str, *, fine: bool = False) -> dsp.Spectrogram:
        """Cached power spectrogram of 'mix'/'<track>'/'<track>.<lane>'.

        Default: ~43 ms frames (2048 @ 48 kHz), 75 % overlap — for
        time-resolved checks. ``fine``: ~170 ms frames (8192 @ 48 kHz,
        5.9 Hz bins), 50 % overlap — for long-term band balance, so the
        20-60 Hz band is resolved and DC/infrasonic energy does not leak
        into it.
        """
        ck = f"{key}|fine" if fine else key
        if ck not in self._spec:
            secs = 0.17 if fine else 0.043
            nfft = 1 << int(round(np.log2(secs * self.sr)))
            hop = nfft // 2 if fine else nfft // 4
            self._spec[ck] = dsp.spectrogram(self.mono(key), self.sr, nfft=nfft, hop=hop)
        return self._spec[ck]

    def analysis_for_chop(self, chop_id: str) -> tuple[Analysis | None, tuple[float, float] | None]:
        chop = next((c for c in self.song.chops if c.id == chop_id), None)
        if chop is None:
            return None, None
        if chop.source not in self.analyses:
            try:
                self.analyses[chop.source] = load_analysis(self.project, chop.source)
            except MuestraError:
                self.analyses[chop.source] = None
        a = self.analyses[chop.source]
        if a is None:
            return None, None
        return a, chop_times(a, chop.start_downbeat, chop.length_beats, chop.start_offset_beats)

    def track(self, tid: str) -> Track | None:
        return self.timeline.tracks.get(tid)


def render_dir(project: Project, version: str) -> Path:
    d = project.renders_dir / version
    if not d.is_dir():
        raise MuestraError.one(
            "file_not_found",
            f"No render at renders/{version}/.",
            value=version,
            hint="Render the song first (render tool) or pick an existing version.",
        )
    return d


def _load_meta(d: Path) -> RenderMeta:
    path = d / "meta.json"
    if not path.is_file():
        raise MuestraError.one(
            "file_not_found",
            f"renders/{d.name}/meta.json is missing.",
            path=f"renders/{d.name}/meta.json",
            hint="meta.json is written by the render tool before ears runs; re-render.",
        )
    try:
        return RenderMeta.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as e:
        raise MuestraError.one(
            "parse_error",
            f"renders/{d.name}/meta.json is not valid JSON: {e}",
            path=f"renders/{d.name}/meta.json",
            hint="Re-render this version.",
        ) from e
    except ValidationError as e:
        raise MuestraError(
            issues_from_validation_error(e, path_prefix=f"renders/{d.name}/meta.json")
        ) from e


def _load_song(project: Project, d: Path, version: str) -> Song:
    snap = d / "song.yaml"
    if snap.is_file():
        text = snap.read_text(encoding="utf-8")
    else:
        proc = subprocess.run(
            ["git", "-C", str(project.root), "show", f"{version}:song.yaml"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if proc.returncode != 0:
            raise MuestraError.one(
                "file_not_found",
                f"No song.yaml for {version}: renders/{version}/song.yaml is missing and "
                f"'git show {version}:song.yaml' failed.",
                value=version,
                hint="Ears needs the version's song to know where kicks, clips and notes are.",
            )
        text = proc.stdout
    try:
        return Song.model_validate(yaml.safe_load(text))
    except yaml.YAMLError as e:
        raise MuestraError.one(
            "parse_error",
            f"renders/{version}/song.yaml is not valid YAML: {e}",
            hint="Restore the snapshot (versions.restore) or re-render.",
        ) from e
    except ValidationError as e:
        raise MuestraError(
            issues_from_validation_error(e, path_prefix=f"renders/{version}/song.yaml")
        ) from e


def _fit(x: np.ndarray, n: int) -> np.ndarray:
    """Force (n, 2) stereo (dtype as read: float32)."""
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    elif x.shape[1] > 2:
        x = x[:, :2]
    if x.shape[0] > n:
        x = x[:n]
    elif x.shape[0] < n:
        x = np.pad(x, ((0, n - x.shape[0]), (0, 0)))
    return x


def _conventional_tracks(song: Song, version: str) -> list[RenderTrackMeta]:
    """Fallback when meta.json has no track list: the R7 path convention."""
    out = []
    for t in song.tracks:
        kind = "clips" if t.clips is not None else "pattern" if t.pattern else "instrument"
        lanes = [
            StemLaneRef(id=ln, stem=f"renders/{version}/stems/{t.id}.{ln}.wav")
            for ln in (t.pattern.lanes if t.pattern else {})
        ]
        out.append(
            RenderTrackMeta(
                id=t.id, kind=kind, stem=f"renders/{version}/stems/{t.id}.wav", lanes=lanes
            )
        )
    return out


def load_context(project: Project, version: str) -> RenderContext:
    d = render_dir(project, version)
    meta = _load_meta(d)
    song = _load_song(project, d, version)
    mix_path = project.path(meta.mix)
    if not mix_path.is_file():
        raise MuestraError.one(
            "file_not_found",
            f"{meta.mix} is missing.",
            path=meta.mix,
            hint="Re-render this version.",
        )
    mix, sr = dsp.read_audio(mix_path)
    n = mix.shape[0]
    mix = _fit(mix, n)
    skipped: list[str] = []
    stems: dict[str, np.ndarray] = {}
    lanes: dict[str, dict[str, np.ndarray]] = {}
    track_refs = meta.tracks or _conventional_tracks(song, version)
    for tm in track_refs:
        p = project.path(tm.stem)
        if not p.is_file():
            skipped.append(f"stem de '{tm.id}' no encontrado ({tm.stem})")
            continue
        x, s2 = dsp.read_audio(p)
        if s2 != sr:
            skipped.append(f"stem de '{tm.id}' a {s2} Hz != mix {sr} Hz: ignorado")
            continue
        if x.shape[0] != n:
            skipped.append(f"stem de '{tm.id}' tiene {x.shape[0]} frames != mix {n}: ajustado")
        stems[tm.id] = _fit(x, n)
        for lane in tm.lanes:
            lp = project.path(lane.stem)
            if not lp.is_file():
                skipped.append(f"stem del carril '{tm.id}.{lane.id}' no encontrado")
                continue
            y, s3 = dsp.read_audio(lp)
            if s3 == sr:
                lanes.setdefault(tm.id, {})[lane.id] = _fit(y, n)
    timeline = Timeline(song, meta, project.root, n / sr)
    return RenderContext(
        project=project,
        version=version,
        dir=d,
        meta=meta,
        song=song,
        sr=sr,
        mix=mix,
        stems=stems,
        lanes=lanes,
        timeline=timeline,
        config=load_config(project.root),
        skipped=skipped,
    )
