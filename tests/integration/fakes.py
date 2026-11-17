"""Fast stand-ins for the two heavy ML steps (separation, beat tracking).

Only these two seams are faked; everything else (import, chop search,
previews, rendering, git versioning, feedback, restore) is the real code.
``install()`` patches module globals, so it works in-process and in the
``tests.integration.host`` subprocess used for MCP/CLI-over-stdio runs.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

BPM = 125.0
BEAT_S = 60.0 / BPM
OFFSET_S = 0.1
SR = 44100
BEATS = 48  # 12 bars


def synth_record(path: Path) -> Path:
    """A 4/4 'record' at 125 BPM: chords + bass + click-ish drums, deterministic."""
    duration = OFFSET_S + BEATS * BEAT_S + 0.5
    t = np.arange(int(duration * SR)) / SR
    rng = np.random.default_rng(7)
    chords = np.zeros_like(t)
    progression = [(311.13, 392.0, 466.16), (415.3, 523.25, 622.25)]  # Eb, Ab
    for bar in range(BEATS // 4):
        start = OFFSET_S + bar * 4 * BEAT_S
        sel = (t >= start) & (t < start + 4 * BEAT_S)
        for f in progression[bar % 2]:
            chords[sel] += 0.06 * np.sin(2 * np.pi * f * t[sel])
        # off-beat stabs give the 'other' stem a pulse
        for k in range(4):
            s = int((start + (k + 0.5) * BEAT_S) * SR)
            n = int(0.12 * SR)
            chords[s : s + n] += 0.1 * np.sin(2 * np.pi * 622.25 * t[:n]) * np.exp(-t[:n] * 25)
    drums = np.zeros_like(t)
    for k in range(BEATS):
        s = int((OFFSET_S + k * BEAT_S) * SR)
        n = int(0.15 * SR)
        env = np.exp(-np.arange(n) / (0.03 * SR))
        drums[s : s + n] += 0.5 * np.sin(2 * np.pi * 60 * np.arange(n) / SR) * env
        drums[s : s + n] += 0.05 * rng.normal(0, 1, n) * env
    bass = 0.12 * np.sin(2 * np.pi * 77.78 * t) * (t > OFFSET_S)
    mix = chords + drums + bass
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.column_stack([mix, mix * 0.98]).astype(np.float32), SR, subtype="PCM_16")
    return path


class FakeSeparation:
    """Frame-aligned 4-stem 'separation' by band splitting (deterministic)."""

    def separate(self, source, output, model, progress=None):
        from scipy.signal import butter, sosfiltfilt

        from muestra.ingest.audio import decode, stereo

        audio, sr = decode(Path(source))
        audio = stereo(audio).astype(np.float64)
        low = sosfiltfilt(butter(4, 120, "lowpass", fs=sr, output="sos"), audio, axis=0)
        high = audio - low
        stems = {"drums": low * 0.6, "bass": low * 0.4, "vocals": high * 0.05, "other": high}
        output.mkdir(parents=True, exist_ok=True)
        result = {}
        for i, (name, data) in enumerate(stems.items()):
            if progress:
                progress(0.1 + 0.2 * i, f"Fake separation: {name}")
            result[name] = output / f"{name}.wav"
            sf.write(result[name], data.astype(np.float32), sr, subtype="FLOAT")
        return result


def fake_beats(audio, sr):
    duration = len(audio) / sr
    beats = np.arange(OFFSET_S, duration - 0.05, BEAT_S)
    return beats, beats[::4]


def install() -> None:
    from muestra.ingest import analysis, separation

    separation.backend_for = lambda name: FakeSeparation()
    analysis.neural_beats = fake_beats


def tiny_sfz(root: Path, name: str, freq: float, *, harmonics: int = 3) -> str:
    """A one-region SFZ bass (sine stack at MIDI 36 keycenter); absolute POSIX path."""
    folder = root / "instruments"
    folder.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(1.5 * 48000)) / 48000
    tone = sum(np.sin(2 * np.pi * freq * (h + 1) * t) / (h + 1) for h in range(harmonics))
    tone *= 0.4 * np.exp(-t * 2)
    sf.write(folder / f"{name}.wav", tone.astype(np.float32), 48000, subtype="FLOAT")
    (folder / f"{name}.sfz").write_text(
        f"<region> sample={name}.wav pitch_keycenter=36 lokey=0 hikey=127\n",
        encoding="utf-8",
        newline="\n",
    )
    return (folder / f"{name}.sfz").as_posix()


def install_into(monkeypatch) -> None:
    """In-process variant of :func:`install`, undone after the test."""
    from muestra.ingest import analysis, separation

    monkeypatch.setattr(separation, "backend_for", lambda name: FakeSeparation())
    monkeypatch.setattr(analysis, "neural_beats", fake_beats)
