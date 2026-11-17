"""Regenerate the committed fixture audio deterministically.

Run: ``uv run python fixtures/gen_audio.py``

Everything is synthesized (no copyrighted audio), mono 22050 Hz 16-bit PCM,
pure numpy math + one seeded RNG, so output is byte-stable. Keep total
audio under ~2 MB.

Audio layout (must stay consistent with the fixture analysis.json):

* sources/fake-source.wav: 5.7 s at 120 BPM. 11 beats every 0.5 s starting
  at t=0.25 s; downbeats on beats 0, 4, 8 (t = 0.25, 2.25, 4.25).
* cache/stems/src1/*.wav: 1.2 s synthetic stems.
* cache/previews/src1/cs001/cand1.wav: 1.0 s preview placeholder.
* renders/v001/: range_bars [1, 2) = 1 bar = 2.0 s.
  mix.wav stereo 24-bit PCM; stems/<track>.wav stereo float32
  (same frame count); stems/drums.<lane>.wav per-lane float32 stems
  (kick/clap/ohat). Track stems include the muted-track case: bass is
  silent here.
* library/kits/disco-01/{kick,clap,ohat}.wav: one-shots.
* midi/bass.mid: minimal SMF (generated as bytes, not audio).
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 22050
ROOT = Path(__file__).resolve().parent
PROJ = ROOT / "example-project"
LIB = ROOT / "library"

BPM = 120.0
BEAT_S = 60.0 / BPM  # 0.5
FIRST_BEAT_S = 0.25
N_BEATS = 11
DURATION_S = 5.7


def t(dur_s: float) -> np.ndarray:
    return np.arange(int(dur_s * SR), dtype=np.float64) / SR


def sine(freq: float, dur_s: float, phase: float = 0.0) -> np.ndarray:
    return np.sin(2 * np.pi * freq * t(dur_s) + phase)


def env(dur_s: float, attack_s: float, release_s: float) -> np.ndarray:
    n = int(dur_s * SR)
    e = np.ones(n)
    a = max(1, int(attack_s * SR))
    r = max(1, int(release_s * SR))
    e[:a] = np.linspace(0, 1, a)
    e[-r:] = np.linspace(1, 0, r)
    return e


def add_click(buf: np.ndarray, at_s: float, amp: float, rng: np.random.Generator) -> None:
    """Short deterministic noise burst + high tick, ~12 ms."""
    n = int(0.012 * SR)
    i = int(at_s * SR)
    if i + n > len(buf):
        return
    noise = rng.standard_normal(n) * np.exp(-np.linspace(0, 8, n))
    buf[i : i + n] += amp * noise
    tick = np.sin(2 * np.pi * 4000 * np.arange(n) / SR) * np.exp(-np.linspace(0, 12, n))
    buf[i : i + n] += amp * 0.4 * tick


def normalize(x: np.ndarray, peak: float = 0.7) -> np.ndarray:
    m = np.max(np.abs(x))
    return x * (peak / m) if m > 0 else x


def write(path: Path, data: np.ndarray, subtype: str = "PCM_16") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, data.astype(np.float32), SR, subtype=subtype)
    print(f"{path.relative_to(ROOT)}  {len(data) / SR:.2f}s  {path.stat().st_size / 1024:.0f} KB")


def stereo(x: np.ndarray) -> np.ndarray:
    """Mono -> stereo (n, 2)."""
    return np.column_stack([x, x]).astype(np.float32)


def render_version(rng: np.random.Generator) -> None:
    """renders/v001: 1 bar (bars [1,2) at 120 BPM = 2.0 s) of stereo audio.

    Layout contract: mix.wav stereo 24-bit PCM post-master;
    stems/<track>.wav stereo float32, same frame count, sample-aligned;
    drums.<lane>.wav per-lane stems (pre track fx) for the metrics.
    """
    d = 2.0  # one bar at 120 BPM
    vdir = PROJ / "renders" / "v001"
    kit = make_kit()
    n = int(d * SR)

    def lane(name: str) -> np.ndarray:
        one = kit[name]
        buf = np.zeros(n)
        step = int(BEAT_S * SR)  # 16th-ish tiling: one hit per beat for fixtures
        for i in range(0, n - len(one), step):
            buf[i : i + len(one)] += one
        return buf * 0.5

    kick, clap, ohat = lane("kick"), lane("clap"), lane("ohat")
    drums = kick + clap + ohat
    sample = sine(220.0, d) * 0.4 * env(d, 0.01, 0.05)
    bass = np.zeros(n)  # instrument track: silent in the fixture render
    mix = normalize(sample + drums * 0.6 + bass)

    write(vdir / "mix.wav", stereo(mix), subtype="PCM_24")
    stems = vdir / "stems"
    write(stems / "sample.wav", stereo(sample), subtype="FLOAT")
    write(stems / "drums.wav", stereo(drums), subtype="FLOAT")
    write(stems / "bass.wav", stereo(bass), subtype="FLOAT")
    write(stems / "drums.kick.wav", stereo(kick), subtype="FLOAT")
    write(stems / "drums.clap.wav", stereo(clap), subtype="FLOAT")
    write(stems / "drums.ohat.wav", stereo(ohat), subtype="FLOAT")
    song_copy = vdir / "song.yaml"
    song_copy.write_text((PROJ / "song.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    print(f"{song_copy.relative_to(ROOT)}  byte copy of song.yaml")


def make_source(rng: np.random.Generator) -> np.ndarray:
    x = sine(220.0, DURATION_S) * 0.35 + sine(330.0, DURATION_S) * 0.15
    x *= env(DURATION_S, 0.02, 0.05)
    beats = [FIRST_BEAT_S + i * BEAT_S for i in range(N_BEATS)]
    for i, b in enumerate(beats):
        add_click(x, b, amp=0.9 if i % 4 == 0 else 0.5, rng=rng)
    return normalize(x)


def make_stems(rng: np.random.Generator) -> dict[str, np.ndarray]:
    d = 1.2
    stems = {
        "drums": np.zeros(int(d * SR)),
        "bass": sine(55.0, d) * 0.6 * env(d, 0.01, 0.1),
        "vocals": sine(440.0, d) * 0.3 * env(d, 0.02, 0.1)
        + sine(445.0, d) * 0.2 * env(d, 0.02, 0.1),
        "other": sine(220.0, d) * 0.4 * env(d, 0.01, 0.1),
    }
    for i in range(int(d / BEAT_S)):
        add_click(stems["drums"], i * BEAT_S, 0.8, rng)
    return {k: normalize(v) for k, v in stems.items()}


def make_kit() -> dict[str, np.ndarray]:
    # kick: sine sweep 120 -> 45 Hz, 0.18 s
    n = int(0.18 * SR)
    phase = 2 * np.pi * (120 * np.arange(n) / SR - (75 / 0.18) * (np.arange(n) / SR) ** 2 / 2)
    kick = np.sin(phase) * np.exp(-np.linspace(0, 6, n))
    # clap: 3 noise bursts, seeded
    rng = np.random.default_rng(20261117)
    clap = np.zeros(int(0.12 * SR))
    for at in (0.0, 0.02, 0.045):
        i = int(at * SR)
        m = min(int(0.03 * SR), len(clap) - i)
        clap[i : i + m] += rng.standard_normal(m) * np.exp(-np.linspace(0, 5, m))
    # open hat: decaying highpassed-ish noise, 0.25 s
    n = int(0.25 * SR)
    raw = rng.standard_normal(n)
    ohat = np.diff(raw, prepend=0.0) * np.exp(-np.linspace(0, 7, n))
    return {"kick": kick, "clap": clap, "ohat": ohat}


def make_bass_mid() -> bytes:
    """Minimal SMF format 0: tempo 120bpm, four quarter notes C2."""

    def vlq(n: int) -> bytes:
        out = [n & 0x7F]
        n >>= 7
        while n:
            out.insert(0, (n & 0x7F) | 0x80)
            n >>= 7
        return bytes(out)

    track = b""
    track += vlq(0) + b"\xff\x51\x03" + (500000).to_bytes(3, "big")  # tempo 500000 us/qn
    for _ in range(4):
        track += vlq(0) + bytes([0x90, 36, 100])  # note on  C2 vel 100
        track += vlq(480) + bytes([0x80, 36, 0])  # note off after 480 ticks
    track += vlq(0) + b"\xff\x2f\x00"  # end of track
    return (
        b"MThd"
        + struct.pack(">IHHH", 6, 0, 1, 480)
        + b"MTrk"
        + struct.pack(">I", len(track))
        + track
    )


def main() -> None:
    rng = np.random.default_rng(42)

    write(PROJ / "sources" / "fake-source.wav", make_source(rng))
    for name, data in make_stems(rng).items():
        write(PROJ / "cache" / "stems" / "src1" / f"{name}.wav", data)
    for i, freq in enumerate((220.0, 330.0, 275.0), start=1):
        write(
            PROJ / "cache" / "previews" / "src1" / "cs001" / f"cand{i}.wav",
            normalize(sine(freq, 1.0) * 0.4 * env(1.0, 0.01, 0.05)),
        )
    render_version(rng)
    for name, data in make_kit().items():
        write(LIB / "kits" / "disco-01" / f"{name}.wav", normalize(data, 0.8))
    mid_path = PROJ / "midi" / "bass.mid"
    mid_path.parent.mkdir(parents=True, exist_ok=True)
    mid_path.write_bytes(make_bass_mid())
    print(f"{mid_path.relative_to(ROOT)}  {mid_path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
