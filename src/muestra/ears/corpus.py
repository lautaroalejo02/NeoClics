"""Synthetic test corpus for calibrating ears (no copyrighted audio).

:func:`generate` writes a complete project with one render version
that follows the render output contract (R7): ``mix.wav`` 24-bit
stereo post-master, ``stems/<track>.wav`` float32 pre-master,
``stems/drums.<lane>.wav`` per-lane stems, ``meta.json`` with the track
list, the ``song.yaml`` snapshot, and the source's ``analysis.json`` so
the detune check can compute the chop's local BPM.

The song is a small future funk loop in F#m: a sampled 4-bar chop
(electric-piano chords + the source's own hats/snare and bass), a
four-on-the-floor kit (kick, clap on 2 and 4, open hat on the
off-beats), an off-beat octave bass and a pad. Every defect ears
should catch can be switched on with :class:`Defects`; the default is
a clean mix that should raise no warn/error alarms.

This is a mini-renderer for tests only: it is NOT the product renderer
(WP2) and makes no claim to sound good.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf
import yaml
from scipy import ndimage, signal

from . import dsp

__all__ = ["Defects", "CorpusSpec", "generate", "PROGRESSION"]

#: chord per bar of the 4-bar chop: (root pc, chord pcs) in F#m.
EP_HARMONICS = [1.0, 0.6, 0.45, 0.3, 0.25, 0.2, 0.15, 0.12, 0.1, 0.08, 0.06, 0.05]

PROGRESSION = [
    (6, (6, 9, 1)),  # F#m
    (2, (2, 6, 9)),  # D
    (4, (4, 8, 11)),  # E
    (1, (1, 4, 8)),  # C#m
]


@dataclass
class Defects:
    late_chop_ms: float = 0.0  # sample content late vs the grid in every clip instance
    clip_late: tuple[int, float] | None = None  # (instance index, ms) one instance late
    drift_ms: float = 0.0  # sample tempo mismatch: offset grows to this across the chop
    click: bool = False  # chop fade_ms 0: hard edges on a sustained sample (implies sustained)
    sustained: bool = False  # the record has a sustained strings/organ layer (faded edges)
    collision: bool = False  # bass on the kick, long and loud, no ducking
    sample_low: bool = False  # sample without its highpass: source bass under the kick
    detune_cents: float = 0.0  # resample lands this far from a semitone (analysis-driven)
    source_detune_cents: float = 0.0  # source itself off A440 (resample stays integer)
    transpose_semitones: int = 0  # sample sounds k semitones off (pitch_semitones mistake)
    clip_master_db: float = 0.0  # master driven into hard clipping by this much
    missing_sidechain: bool = False  # sidechain declared but not applied
    weak_sidechain: bool = False  # applied at depth 0.3 while 0.6 is declared
    no_sidechain: bool = False  # no sidechain fx at all
    sample_swing: float = 0.0  # swing of the sample's own 16th hats (pattern semantics)
    sample_triplets: bool = False  # the sample's own hats on triplet 8ths
    dark: bool = False  # sample low-passed at 900 Hz and no hats
    silent_track: bool = False  # bass renders silent while not muted
    dc_offset: float = 0.0  # DC added to the master
    low_phase: bool = False  # bass in opposite polarity on the right channel
    kick_late_ms: float = 0.0  # kick one-shot with leading silence
    inaudible_pad: bool = False  # pad 45 dB down
    over_compress: bool = False  # master pushed to -5 LUFS through the limiter


@dataclass
class CorpusSpec:
    bpm: float = 120.0
    bars: int = 8  # multiple of 4 (the chop is 4 bars)
    sr: int = 48000
    target_lufs: float = -10.0
    drum_swing: float = 0.0
    seed: int = 7
    pad: bool = True
    defects: Defects = field(default_factory=Defects)


# ---------------------------------------------------------------------------
# synthesis helpers
# ---------------------------------------------------------------------------


def _hz(midi: float, cents: float = 0.0) -> float:
    return 440.0 * 2.0 ** ((midi - 69 + cents / 100.0) / 12.0)


def _place(buf: np.ndarray, x: np.ndarray, t: float, sr: int, gain: float = 1.0) -> None:
    i = int(round(t * sr))
    if i >= buf.size or i + x.size <= 0:
        return
    a, b = max(0, i), min(buf.size, i + x.size)
    buf[a:b] += gain * x[a - i : b - i]


def _tone(f0: float, dur: float, sr: int, harmonics: list[float], attack=0.002, decay=0.4):
    n = max(1, int(dur * sr))
    t = np.arange(n) / sr
    y = np.zeros(n)
    for k, amp in enumerate(harmonics, start=1):
        if f0 * k < 0.45 * sr:
            y += amp * np.sin(2 * np.pi * f0 * k * t) * np.exp(-t * k / (decay * 4))
    env = np.minimum(1.0, t / attack) * np.exp(-t / decay)
    rel = min(n, max(1, int(0.01 * sr)))
    env[-rel:] *= np.linspace(1.0, 0.0, rel)
    return y * env


def _kick(sr: int, lead_s: float = 0.0) -> np.ndarray:
    n = int(0.3 * sr)
    t = np.arange(n) / sr
    f = 50 + 100 * np.exp(-t / 0.03)
    ph = 2 * np.pi * np.cumsum(f) / sr
    body = np.sin(ph) * np.exp(-t / 0.12)
    click = np.exp(-t / 0.002) * 0.3 * np.sin(2 * np.pi * 3000 * t)
    y = (body + click) * np.minimum(1.0, t / 0.0005)
    return np.concatenate([np.zeros(int(lead_s * sr)), y])


def _noise_hit(rng, sr, dur, lo, hi, decay, attack=0.0005):
    n = int(dur * sr)
    t = np.arange(n) / sr
    sos = signal.butter(2, [lo, min(hi, 0.45 * sr)], btype="band", fs=sr, output="sos")
    y = signal.sosfilt(sos, rng.standard_normal(n))
    y /= max(1e-9, float(np.max(np.abs(y))))
    return y * np.exp(-t / decay) * np.minimum(1.0, t / attack)


def _clap(rng, sr):
    y = np.zeros(int(0.15 * sr))
    for k, at in enumerate((0.0, 0.012, 0.024)):
        _place(y, _noise_hit(rng, sr, 0.1, 900, 4000, 0.03 if k == 2 else 0.008), at, sr)
    return y * 0.8


def _hat(rng, sr, dur=0.12, decay=0.035):
    return _noise_hit(rng, sr, dur, 3000, 16000, decay)


def _saw(f0: float, dur: float, sr: int, attack=0.003, release=0.02) -> np.ndarray:
    n = max(1, int(dur * sr))
    t = np.arange(n) / sr
    y = np.zeros(n)
    k = 1
    while f0 * k < min(4000.0, 0.45 * sr) and k <= 24:
        y += np.sin(2 * np.pi * f0 * k * t) / k
        k += 1
    env = np.minimum(1.0, t / attack) * np.minimum(1.0, (dur - t) / release).clip(0, 1)
    return y * env * 0.6


def _bright(f0: float, dur: float, sr: int) -> np.ndarray:
    """A bright sustained tone (vocal/guitar register), harmonics to ~9 kHz."""
    n = max(1, int(dur * sr))
    t = np.arange(n) / sr
    y = np.zeros(n)
    k = 1
    while f0 * k < min(9000.0, 0.45 * sr):
        y += np.sin(2 * np.pi * f0 * k * t + 0.3 * k) / k**0.7
        k += 1
    env = np.minimum(1.0, t / 0.01) * np.exp(-t / 1.0)
    rel = min(n, int(0.02 * sr))
    env[-rel:] *= np.linspace(1.0, 0.0, rel)
    return y * env * 0.5


def _limiter(x: np.ndarray, sr: int, ceiling_db: float = -1.5) -> np.ndarray:
    c = 10 ** (ceiling_db / 20.0)
    peak = np.max(np.abs(x), axis=1)
    g = np.minimum(1.0, c / np.maximum(peak, 1e-12))
    la = int(0.002 * sr)
    g = ndimage.minimum_filter1d(g, size=2 * la + 1)
    # release smoothing (50 ms), then never above the instantaneous need
    b, a = signal.butter(1, 1.0 / (0.05 * math.pi), fs=sr)
    g = np.minimum(signal.filtfilt(b, a, g), np.minimum(1.0, c / np.maximum(peak, 1e-12)))
    return x * g[:, None]


def _stereo(y: np.ndarray, pan: float = 0.0) -> np.ndarray:
    lg = math.cos((pan + 1) * math.pi / 4) * math.sqrt(2)
    rg = math.sin((pan + 1) * math.pi / 4) * math.sqrt(2)
    return np.column_stack([y * lg, y * rg])


def _duck(n: int, sr: int, triggers: list[float], depth: float, attack_ms, release_ms):
    env = np.zeros(n)
    a = max(1, int(attack_ms / 1000 * sr))
    length = int(release_ms / 1000 * sr * 2.5)
    shape = np.concatenate(
        [
            np.linspace(0, 1, a, endpoint=False),
            np.exp(-np.arange(length) / (release_ms / 1000 * sr / 4.6)),
        ]
    )
    for t in triggers:
        i = int(round(t * sr))
        if i >= n:
            continue
        seg = shape[: n - i]
        env[i : i + seg.size] = np.maximum(env[i : i + seg.size], seg)
    return 1.0 - depth * env


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


def generate(spec: CorpusSpec, root: Path, version: str = "v001") -> Path:
    """Write a project with one synthetic render at ``root``; returns root."""
    d = spec.defects
    sr, bpm = spec.sr, spec.bpm
    if spec.bars % 4:
        raise ValueError("bars must be a multiple of 4 (the chop is 4 bars).")
    spb = 60.0 / bpm
    n = int(round(spec.bars * 4 * spb * sr))
    dur = n / sr
    rng = np.random.default_rng(spec.seed)

    # --- drums --------------------------------------------------------------
    step = spb / 4
    swing_delay = spec.drum_swing * step / 3.0
    lanes_def = {
        "kick": ("x...x...x...x...", 0.0, _kick(sr, d.kick_late_ms / 1000.0)),
        "clap": ("....x.......x...", 0.0, _clap(rng, sr)),
        "ohat": ("..x...x...x...x.", 0.0, _hat(rng, sr, 0.2, 0.06)),
    }
    lane_audio: dict[str, np.ndarray] = {}
    for name, (steps, gain_db, one) in lanes_def.items():
        buf = np.zeros(n)
        for bar in range(spec.bars):
            for s, ch in enumerate(steps):
                if ch == ".":
                    continue
                t = (bar * 16 + s) * step + (swing_delay if s % 2 else 0.0)
                _place(buf, one, t, sr, 10 ** (gain_db / 20))
        lane_audio[name] = _stereo(buf * 0.5)
    kick_times = [b * 4 * spb + q * spb for b in range(spec.bars) for q in range(4)]
    drums = sum(lane_audio.values())

    # --- the source record and the sample track ------------------------------
    # The source is an "old record" at its own tempo, one semitone below the
    # song key; the sample track is rendered from it like the renderer's
    # resample warp (rate = project_bpm / local_bpm), so detune, late chops
    # and drift come from the same mechanisms as in a real project.
    chop_beats = 16
    inst_s = chop_beats * spb
    n_inst = spec.bars // 4
    k = d.transpose_semitones
    shift = 1.0 + d.detune_cents / 100.0  # semitones the resample will raise it
    local_bpm = bpm / 2.0 ** (shift / 12.0)  # what analysis.json reports
    rate = bpm / local_bpm
    src_spb = 60.0 / local_bpm
    act_spb = src_spb + (d.drift_ms / 1000.0 * rate) / chop_beats  # true record tempo
    first = 2.0  # source time of the chop's downbeat (true transient)
    err = d.late_chop_ms / 1000.0 * rate  # beat tracker error: grid placed early
    pitch_off = -1.0 + k  # record pitch vs the song key, semitones
    src_cents = d.source_detune_cents
    src_dur = first + (chop_beats + 6) * act_spb
    source = np.zeros(int(src_dur * sr))
    for bi in range(-4, chop_beats + 4):
        tb = first + bi * act_spb
        if tb < 0:
            continue
        root_pc, chord = PROGRESSION[(bi // 4) % 4]
        beat = bi % 4
        for pc in chord:
            midi = 68 + ((pc - 8) % 12) + pitch_off  # G#4..G5 voicing
            tone = _tone(_hz(midi, src_cents), act_spb * 1.1, sr, EP_HARMONICS, decay=0.25)
            _place(source, tone, tb, sr, 0.08)
            if beat == 0:  # sustained layer
                pad = _tone(_hz(midi, src_cents), 4 * act_spb, sr, [1, 0.2], attack=0.02, decay=8.0)
                _place(source, pad, tb, sr, 0.05)
        if beat in (1, 3):  # the record's own snare
            _place(source, _noise_hit(rng, sr, 0.15, 1500, 7000, 0.04), tb, sr, 0.6)
        if beat in (0, 2):  # the record's lead line (vocal/guitar register)
            lead_pc = (6, 9, 11, 1, 4)[(bi // 2) % 5]
            lead_midi = 66 + ((lead_pc - 6) % 12) + pitch_off
            _place(source, _bright(_hz(lead_midi, src_cents), act_spb * 1.8, sr), tb, sr, 0.45)
        if d.sample_triplets:
            subs = [0.0, 1 / 3, 2 / 3]
        else:
            sw = d.sample_swing * 0.25 / 3.0
            subs = [0.0, 0.25 + sw, 0.5, 0.75 + sw]
        for j, sub in enumerate(subs):
            _place(source, _hat(rng, sr), tb + sub * act_spb, sr, 0.45 if j else 0.35)
        # the record's bass, leaking into the 'other' stem (old records separate badly)
        bass_midi = 30 + ((root_pc - 6) % 12) + pitch_off
        leak = 0.9 if d.sample_low else 0.15  # sample_low: a chop from the full mix
        _place(source, _saw(_hz(bass_midi, src_cents), act_spb * 0.9, sr), tb, sr, leak)
    if d.sustained or d.click:
        # a sustained strings/organ layer under the whole record: cut it hard
        # and every chop edge clicks
        t_src = np.arange(source.size) / sr
        drone = np.zeros(source.size)
        for h, amp in ((1, 1.0), (2, 0.35), (3, 0.2)):
            drone += amp * np.sin(2 * np.pi * _hz(73 + pitch_off, src_cents) * h * t_src)
        source += 0.25 * drone
    source *= 0.5 / max(1e-9, float(np.max(np.abs(source))))
    # chop = [start_s, end_s) on the ANALYSED grid, resampled onto the project grid
    start_s = first - err
    end_s = start_s + chop_beats * src_spb
    seg = source[int(round(start_s * sr)) : int(round(end_s * sr))]
    length = int(round(inst_s * sr))
    from fractions import Fraction

    fr = Fraction(length, max(1, seg.size)).limit_denominator(2000)
    chop_audio = signal.resample_poly(seg, fr.numerator, fr.denominator)
    chop_audio = np.pad(chop_audio, (0, max(0, length - chop_audio.size)))[:length]
    if not d.click:
        fade = int(0.005 * sr)  # fade_ms 5
        ramp = np.linspace(0.0, 1.0, fade)
        chop_audio[:fade] *= ramp
        chop_audio[-fade:] *= ramp[::-1]
    sample = np.zeros(n)
    clip_starts = []
    late_inst = d.clip_late[0] if d.clip_late else None
    for r in range(n_inst):
        t0 = r * inst_s
        if r == late_inst:
            t0 += d.clip_late[1] / 1000.0
        clip_starts.append(t0)
        _place(sample, chop_audio, t0, sr)
    if not d.sample_low:
        sample = dsp.highpass(sample, sr, 150.0)
    if d.dark:
        sample = dsp.lowpass(sample, sr, 900.0)
    sc_depth_applied = 0.0 if (d.missing_sidechain or d.no_sidechain) else 0.6
    if d.weak_sidechain:
        sc_depth_applied = 0.3
    if sc_depth_applied > 0:
        sample *= _duck(n, sr, kick_times, sc_depth_applied, 1.0, 120.0)
    sample_st = _stereo(sample * 2.5 * 10 ** (-2 / 20))  # the sample leads the mix

    # --- bass (off-beat octaves; collision: on the beat, long, loud) ----------
    notes = []
    for bar in range(spec.bars):
        root_pc = PROGRESSION[bar % 4][0]
        base = 30 + ((root_pc - 6) % 12) + (0 if root_pc >= 6 else 12)
        for q in range(4):
            if d.collision:
                notes.append(
                    {"beat": bar * 4 + q + 1, "pitch": base, "length_beats": 0.9, "velocity": 120}
                )
            else:
                notes.append(
                    {
                        "beat": bar * 4 + q + 1.5,
                        "pitch": base + (12 if q % 2 else 0),
                        "length_beats": 0.4,
                        "velocity": 100,
                    }
                )
    bass = np.zeros(n)
    if not d.silent_track:
        for nt in notes:
            t = (nt["beat"] - 1) * spb
            gain = 0.5 * (1.6 if d.collision else 1.0)
            _place(bass, _saw(_hz(nt["pitch"]), nt["length_beats"] * spb, sr), t, sr, gain)
        bass = dsp.lowpass(bass, sr, 300.0)
    bass_st = _stereo(bass)
    if d.low_phase:
        bass_st[:, 1] *= -1

    # --- pad -------------------------------------------------------------------
    pad_notes = []
    pad = np.zeros(n)
    pad_gain_db = -45.0 if d.inaudible_pad else -6.0
    if spec.pad:
        for bar in range(spec.bars):
            for pc in PROGRESSION[bar % 4][1]:
                midi = 66 + ((pc - 6) % 12)
                pad_notes.append(
                    {"beat": bar * 4 + 1, "pitch": midi, "length_beats": 4, "velocity": 80}
                )
                _place(
                    pad,
                    _tone(_hz(midi), 4 * spb, sr, [1, 0.3, 0.1], attack=0.08, decay=6.0),
                    bar * 4 * spb,
                    sr,
                    0.08,
                )
        pad *= 10 ** (pad_gain_db / 20)
    pad_st = _stereo(pad, 0.0)

    # --- master ----------------------------------------------------------------
    stems = {"sample": sample_st, "drums": drums, "bass": bass_st}
    if spec.pad:
        stems["pad"] = pad_st
    mix = sum(stems.values())
    target = spec.target_lufs
    lufs = dsp.integrated_loudness(mix, sr)
    mix = mix * 10 ** ((target - lufs) / 20)
    mix = _limiter(mix, sr, -2.0)
    tp = dsp.true_peak(mix, sr)  # the renderer's final limiter is true-peak safe
    mix = mix * min(1.0, 10 ** (-1.2 / 20) / max(tp, 1e-9))
    if d.over_compress:  # loudness-war squash: +14 dB into a soft clipper
        mix = np.tanh(mix * 10 ** (14 / 20)) * 10 ** (-1.5 / 20)
    if d.clip_master_db:
        mix = np.clip(mix * 10 ** (d.clip_master_db / 20), -1.0, 1.0)
    mix = mix + d.dc_offset
    mix = np.clip(mix, -1.0, 1.0)

    # R7: tails are cut with a 5 ms fade-out (mix and stems)
    tail = int(0.005 * sr)
    ramp = np.linspace(1.0, 0.0, tail)[:, None]
    mix[-tail:] *= ramp
    for x in stems.values():
        x[-tail:] *= ramp

    # --- song ------------------------------------------------------------------
    if late_inst is None:
        clips = [{"chop": "c1", "at_bar": 1, "repeat": n_inst}]
    else:
        if late_inst >= n_inst - 1:
            raise ValueError("clip_late needs an instance before the last one.")
        clips = []
        if late_inst > 0:
            clips.append({"chop": "c1", "at_bar": 1, "repeat": late_inst})
        clips.append(
            {
                "chop": "c1",
                "at_bar": 1 + 4 * late_inst,
                "offset_beats": round(d.clip_late[1] / 1000.0 / spb, 6),
                "repeat": 1,
            }
        )
        clips.append(
            {"chop": "c1", "at_bar": 1 + 4 * (late_inst + 1), "repeat": n_inst - late_inst - 1}
        )
    sample_fx = []
    if not d.sample_low:
        sample_fx.append({"id": "hp", "type": "highpass", "cutoff_hz": 150})
    if d.dark:
        sample_fx.append({"id": "lp", "type": "lowpass", "cutoff_hz": 900})
    if not d.no_sidechain:
        sample_fx.append(
            {
                "id": "sc",
                "type": "sidechain",
                "key_track": "drums",
                "key_lane": "kick",
                "depth": 0.6,
                "release_ms": 120,
            }
        )
    song = {
        "schema_version": 0,
        "meta": {
            "title": "corpus",
            "bpm": bpm,
            "key": "F#m",
            "length_bars": spec.bars,
            "seed": spec.seed,
        },
        "sources": [{"id": "src1", "file": "sources/corpus-source.wav"}],
        "chops": [
            {
                "id": "c1",
                "source": "src1",
                "stem": "other",
                "start_downbeat": 1,
                "length_beats": chop_beats,
                "warp": {"mode": "resample", "pitch_semitones": float(k)},
                "fade_ms": 0 if d.click else 5,
            }
        ],
        "tracks": [
            {"id": "sample", "clips": clips, "fx": sample_fx, "gain_db": -2},
            {
                "id": "drums",
                "pattern": {
                    "steps_per_bar": 16,
                    "lanes": {
                        name: {
                            "sample": f"library://kits/corpus/{name}.wav",
                            "steps": steps,
                            "gain_db": g,
                        }
                        for name, (steps, g, _one) in lanes_def.items()
                    },
                    "swing": spec.drum_swing,
                },
            },
            {"id": "bass", "instrument": {"plugin": "corpus-bass"}, "notes": notes},
        ],
        "master": {
            "fx": [{"type": "limiter", "ceiling_db": -1.0}],
            "target_lufs": spec.target_lufs,
        },
    }
    if spec.pad:
        song["tracks"].append(
            {
                "id": "pad",
                "instrument": {"plugin": "corpus-pad"},
                "notes": pad_notes,
                "gain_db": pad_gain_db,
            }
        )

    # --- write -----------------------------------------------------------------
    root = Path(root)
    vdir = root / "renders" / version
    (vdir / "stems").mkdir(parents=True, exist_ok=True)
    (root / "cache" / "analysis").mkdir(parents=True, exist_ok=True)
    (root / "muestra.toml").write_text(
        f"schema_version = 0\nsample_rate = {sr}\nexport_bit_depth = 24\n\n"
        '[library]\nroots = ["library"]\n',
        encoding="utf-8",
    )
    song_text = yaml.safe_dump(song, sort_keys=False, allow_unicode=True)
    (root / "song.yaml").write_text(song_text, encoding="utf-8")
    (vdir / "song.yaml").write_text(song_text, encoding="utf-8")
    beats = [round(start_s + i * src_spb, 6) for i in range(chop_beats + 4)]
    analysis = {
        "schema_version": 0,
        "source_id": "src1",
        "file": "sources/corpus-source.wav",
        "duration_s": round(src_dur, 3),
        "sample_rate": sr,
        "bpm_global": round(local_bpm, 4),
        "beats_s": beats,
        "downbeats_s": beats[::4],
        "stems": {"other": "cache/stems/src1/other.wav"},
    }
    (root / "cache" / "stems" / "src1").mkdir(parents=True, exist_ok=True)
    sf.write(
        root / "cache" / "stems" / "src1" / "other.wav",
        source.astype(np.float32),
        sr,
        subtype="FLOAT",
    )
    (root / "cache" / "analysis" / "src1.json").write_text(
        json.dumps(analysis, indent=2), encoding="utf-8"
    )
    sf.write(vdir / "mix.wav", mix.astype(np.float32), sr, subtype="PCM_24")
    tracks_meta = []
    for tid, x in stems.items():
        sf.write(vdir / "stems" / f"{tid}.wav", x.astype(np.float32), sr, subtype="FLOAT")
        entry = {
            "id": tid,
            "kind": "pattern" if tid == "drums" else "clips" if tid == "sample" else "instrument",
            "stem": f"renders/{version}/stems/{tid}.wav",
            "lanes": [],
        }
        if tid == "drums":
            for lane, y in lane_audio.items():
                sf.write(
                    vdir / "stems" / f"drums.{lane}.wav", y.astype(np.float32), sr, subtype="FLOAT"
                )
                entry["lanes"].append(
                    {"id": lane, "stem": f"renders/{version}/stems/drums.{lane}.wav"}
                )
        tracks_meta.append(entry)
    meta = {
        "schema_version": 0,
        "version": version,
        "created_at": "2026-11-17T12:00:00Z",
        "agent": "ears-corpus",
        "duration_s": dur,
        "sample_rate": sr,
        "bpm": bpm,
        "key": "F#m",
        "length_bars": spec.bars,
        "range_bars": [1, spec.bars + 1],
        "mix": f"renders/{version}/mix.wav",
        "tracks": tracks_meta,
    }
    (vdir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return root
