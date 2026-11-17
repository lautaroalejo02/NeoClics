"""Audio probing for the sample library (WP4).

:func:`probe` turns an audio file into an :class:`AudioProbe`: container
facts (duration, rate, depth, channels), level facts (peak, RMS,
BS.1770 loudness, clipping) and the descriptive features an agent that
cannot hear needs — fundamental frequency, attack/decay times,
brightness, per-band energy share and tonality — plus optional BPM and
key estimates for loops.

Pure numpy/scipy/soundfile, no optional dependencies. Feature
definitions and thresholds are documented in docs/library.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import fft as sfft
from scipy import signal as sg
from scipy.ndimage import maximum_filter1d, uniform_filter1d

from ..music import PITCH_CLASS_NAMES, note_name
from ..schemas.library import AudioFeatures

__all__ = [
    "AUDIO_EXTENSIONS",
    "BAND_EDGES_HZ",
    "AudioProbe",
    "detect_bpm",
    "detect_key",
    "probe",
]

#: extensions treated as audio (everything libsndfile reads).
AUDIO_EXTENSIONS = frozenset({".wav", ".flac", ".aif", ".aiff", ".ogg", ".mp3"})

#: |sample| at or above this counts as "at full scale" (-0.009 dBFS).
CLIP_THRESHOLD = 0.999
#: hard clipping = at least this many consecutive full-scale samples that
#: are FLAT (identical to within 2 LSB). A normalised sine/kick peak is
#: full-scale for a few samples too, but never flat for four in a row.
CLIP_MIN_RUN = 4
#: clipped samples (in flat runs) needed to raise the ``clipping`` flag.
CLIP_MIN_SAMPLES = 4

#: band edges (Hz): sub / low / mid / high / air.
BAND_EDGES_HZ = (20.0, 120.0, 400.0, 2000.0, 8000.0)

#: attack/decay/fundamental are only measured on files up to this long.
_ENVELOPE_MAX_S = 6.0
#: files longer than this use Welch averaging instead of one FFT.
_SINGLE_FFT_MAX_S = 6.0

# Krumhansl-Schmuckler key profiles (C major / C minor).
_KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

_SUBTYPE_BIT_DEPTH = {
    "PCM_S8": 8,
    "PCM_U8": 8,
    "PCM_16": 16,
    "PCM_24": 24,
    "PCM_32": 32,
    "FLOAT": 32,
    "DOUBLE": 64,
}


@dataclass
class AudioProbe:
    """Everything measurable about one audio file."""

    duration_s: float
    sample_rate: int
    channels: int
    bit_depth: int | None
    peak_dbfs: float
    rms_dbfs: float
    loudness_lufs: float | None
    clipping: bool
    clipped_samples: int
    features: AudioFeatures
    side_ratio: float = 0.0  # side/mid RMS ratio; 0 = mono-compatible
    bpm: float | None = None
    bpm_confidence: float | None = None
    periodic: bool = False  # steady repeating grid (loop-ish)
    key: str | None = None  # canonical 'F#m' / 'F#'
    key_confidence: float | None = None
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# loudness (ITU-R BS.1770 K-weighting, any sample rate)
# ---------------------------------------------------------------------------


def _k_weighting_filters(fs: float) -> list[tuple[np.ndarray, np.ndarray]]:
    """The two BS.1770 K-weighting biquads for sample rate ``fs``.

    Stage 1: high shelf (f0 = 1681.97 Hz, +4 dB). Stage 2: high-pass
    (f0 = 38.14 Hz). Bilinear-transformed analogue prototypes, the same
    construction libebur128 / pyloudnorm use; at 48 kHz they reproduce
    the constants published in BS.1770.
    """

    def biquad(f0: float, q: float, vh: float, vb: float, shelf: bool):
        k = math.tan(math.pi * f0 / fs)
        denom = 1.0 + k / q + k * k
        if shelf:
            b = np.array(
                [
                    (vh + vb * k / q + k * k) / denom,
                    2.0 * (k * k - vh) / denom,
                    (vh - vb * k / q + k * k) / denom,
                ]
            )
        else:
            b = np.array([1.0, -2.0, 1.0]) / denom
        a = np.array([1.0, 2.0 * (k * k - 1.0) / denom, (1.0 - k / q + k * k) / denom])
        return b, a

    vh = 10.0 ** (3.999843853973347 / 20.0)
    vb = vh**0.4996667741545416
    return [
        biquad(1681.974450955533, 0.7071752369554196, vh, vb, True),
        biquad(38.13547087602444, 0.5003270373238773, 1.0, 1.0, False),
    ]


def integrated_lufs(data: np.ndarray, sr: int) -> float | None:
    """BS.1770 integrated loudness of ``data`` (frames x channels).

    Files shorter than one 400 ms gating block are measured over their
    whole length, ungated. Returns None for digital silence.
    """
    if data.size == 0:
        return None
    chans = []
    for c in range(min(data.shape[1], 2)):
        y = data[:, c].astype(np.float64)
        for b, a in _k_weighting_filters(float(sr)):
            y = sg.lfilter(b, a, y)
        chans.append(y)
    power = sum(c * c for c in chans)  # channel weights are all 1.0 for L/R
    block = int(round(0.400 * sr))
    hop = int(round(0.100 * sr))
    if power.size < block:
        ms = float(np.mean(power))
        return -0.691 + 10.0 * math.log10(ms) if ms > 1e-12 else None
    csum = np.concatenate(([0.0], np.cumsum(power)))
    starts = np.arange(0, power.size - block + 1, hop)
    ms = (csum[starts + block] - csum[starts]) / block
    loud = -0.691 + 10.0 * np.log10(ms + 1e-20)
    gated = loud[loud > -70.0]
    if gated.size == 0:
        return None
    rel_gate = -0.691 + 10.0 * math.log10(float(np.mean(10.0 ** (gated / 10.0)))) - 10.0
    gated = gated[gated > rel_gate]
    if gated.size == 0:
        return None
    return -0.691 + 10.0 * math.log10(float(np.mean(10.0 ** (gated / 10.0))))


# ---------------------------------------------------------------------------
# clipping
# ---------------------------------------------------------------------------


def count_clipped(data: np.ndarray, lsb: float = 2.0**-15) -> int:
    """Samples belonging to flat full-scale runs (>= CLIP_MIN_RUN
    consecutive samples at/above CLIP_THRESHOLD, all within 2 ``lsb`` of
    each other), summed over channels."""
    total = 0
    for c in range(data.shape[1]):
        mag = np.abs(data[:, c])
        hot = mag >= CLIP_THRESHOLD
        if not hot.any():
            continue
        padded = np.concatenate(([False], hot, [False]))
        edges = np.flatnonzero(padded[1:] != padded[:-1])
        for start, stop in zip(edges[0::2], edges[1::2], strict=True):
            if stop - start >= CLIP_MIN_RUN:
                run = mag[start:stop]
                if float(run.max() - run.min()) <= 2.0 * lsb:
                    total += int(stop - start)
    return total


# ---------------------------------------------------------------------------
# spectrum
# ---------------------------------------------------------------------------


def _power_spectrum(x: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """(freqs, power) of a one-shot or loop.

    Short files: a single FFT whose window has NO start taper (the
    attack is the point of a one-shot) and a short end fade. Long files:
    Welch averaging.
    """
    n = x.size
    if n < 8:
        return np.zeros(1), np.zeros(1)
    if n <= _SINGLE_FFT_MAX_S * sr:
        w = np.ones(n)
        fade = max(2, int(0.1 * n))
        w[-fade:] = 0.5 * (1.0 + np.cos(np.linspace(0.0, np.pi, fade)))
        n_fft = sfft.next_fast_len(max(n, 2048), real=True)
        spec = np.abs(sfft.rfft(x * w, n=n_fft)) ** 2
        freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
        return freqs, spec
    freqs, spec = sg.welch(x, fs=sr, window="hann", nperseg=65536, noverlap=32768)
    return freqs, spec


def _band_shares(freqs: np.ndarray, spec: np.ndarray) -> list[float]:
    edges = list(BAND_EDGES_HZ) + [float(freqs[-1]) + 1.0]
    sel = freqs >= edges[0]
    total = float(spec[sel].sum())
    if total <= 0:
        return [0.0] * 5
    out = []
    for lo, hi in zip(edges, edges[1:], strict=False):
        out.append(float(spec[(freqs >= lo) & (freqs < hi)].sum()) / total)
    return out


def _tonality(freqs: np.ndarray, spec: np.ndarray) -> float:
    """Chance-corrected share of energy in the 6 strongest narrow peaks."""
    sel = freqs >= 20.0
    s = spec[sel]
    if s.size < 32 or float(s.sum()) <= 0:
        return 0.0
    peaks, _ = sg.find_peaks(s, height=float(s.max()) * 1e-3)
    if peaks.size == 0:
        return 0.0
    top = peaks[np.argsort(s[peaks])[::-1][:6]]
    mask = np.zeros(s.size, dtype=bool)
    for p in top:
        mask[max(0, p - 3) : p + 4] = True
    share = float(s[mask].sum() / s.sum())
    chance = float(mask.sum()) / s.size
    return float(np.clip((share - chance) / max(1e-9, 1.0 - chance), 0.0, 1.0))


def _parabolic(spec: np.ndarray, i: int) -> float:
    """Fractional bin index of the peak around bin ``i``."""
    if 0 < i < spec.size - 1:
        y0, y1, y2 = float(spec[i - 1]), float(spec[i]), float(spec[i + 1])
        denom = y0 - 2.0 * y1 + y2
        if denom != 0.0:
            return i + 0.5 * (y0 - y2) / denom
    return float(i)


def _fundamental(x: np.ndarray, sr: int, i_peak: int) -> float | None:
    """Settled low-frequency pitch of the body (30-300 Hz), or None.

    Looks at the 200 ms starting 15 ms after the envelope peak (past the
    beater click and, for 808s, most of the pitch sweep) and takes the
    strongest narrow spectral peak between 30 and 300 Hz. Requires the
    peak to be prominent AND the low band to hold a real share of the
    segment, so unpitched snares/hats report None.
    """
    start = i_peak + int(0.015 * sr)
    seg = x[start : start + int(0.2 * sr)]
    if seg.size < int(0.04 * sr):
        seg = x[i_peak : i_peak + int(0.2 * sr)]
    if seg.size < 256:
        return None
    n_fft = 1 << max(14, int(math.ceil(math.log2(seg.size))) + 2)
    spec = np.abs(sfft.rfft(seg * np.hanning(seg.size), n=n_fft)) ** 2
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    total = float(spec.sum())
    band = (freqs >= 30.0) & (freqs <= 300.0)
    if total <= 0 or not band.any():
        return None
    band_power = float(spec[band].sum())
    if band_power / total < 0.25:
        return None
    idx = int(np.argmax(np.where(band, spec, 0.0)))
    # prominence: the peak neighbourhood (+-4 Hz) must dominate the band
    local = (freqs >= freqs[idx] - 6.0) & (freqs <= freqs[idx] + 6.0)
    if float(spec[local].sum()) / band_power < 0.3:
        return None
    return float(_parabolic(spec, idx) * (freqs[1] - freqs[0]))


def _envelope(x: np.ndarray, sr: int) -> np.ndarray:
    """Peak-hold amplitude envelope: |x| max-filtered over 12 ms (so the
    ripple of a 40 Hz sine does not read as decay) then smoothed 2 ms.

    (A Hilbert envelope was rejected: it rings at the file edges and
    reported spurious -22 dB tails on short low-frequency hits.)
    """
    hold = max(1, int(0.012 * sr))
    env = maximum_filter1d(np.abs(x), size=hold, mode="constant")
    k = max(1, int(0.002 * sr))
    if k > 1:
        env = uniform_filter1d(env, size=k, mode="constant")
    return env


def _attack_decay(env: np.ndarray, sr: int) -> tuple[float | None, float | None, int]:
    """(attack_ms, decay_ms, peak_index) from the envelope."""
    if env.size == 0:
        return None, None, 0
    peak = float(env.max())
    if peak <= 0:
        return None, None, 0
    i_peak = int(np.argmax(env))
    rise = env[: i_peak + 1]
    i10 = int(np.argmax(rise >= 0.10 * peak))
    i90 = int(np.argmax(rise >= 0.90 * peak))
    attack_ms = max(0.0, (i90 - i10) / sr * 1000.0)
    # 5 ms smoothing so momentary dips in a tail don't end the decay early
    k = max(1, int(0.005 * sr))
    smooth = uniform_filter1d(env, size=k, mode="constant") if k > 1 else env
    above = np.flatnonzero(smooth[i_peak:] >= peak * 10.0 ** (-30.0 / 20.0))
    decay_ms = float(above[-1]) / sr * 1000.0 if above.size else 0.0
    return attack_ms, decay_ms, i_peak


def _features(x: np.ndarray, sr: int) -> AudioFeatures:
    freqs, spec = _power_spectrum(x, sr)
    shares = _band_shares(freqs, spec)
    sel = freqs >= 20.0
    total = float(spec[sel].sum())
    centroid = float((freqs[sel] * spec[sel]).sum() / total) if total > 0 else None

    attack = decay = f0 = None
    if x.size / sr <= _ENVELOPE_MAX_S and x.size > 16:
        env = _envelope(x, sr)
        attack, decay, i_peak = _attack_decay(env, sr)
        f0 = _fundamental(x, sr, i_peak)

    return AudioFeatures(
        fundamental_hz=round(f0, 1) if f0 else None,
        note=note_name(int(round(69 + 12 * math.log2(f0 / 440.0))))
        if f0 and 8 < f0 < 8000
        else None,
        attack_ms=round(attack, 1) if attack is not None else None,
        decay_ms=round(decay, 0) if decay is not None else None,
        spectral_centroid_hz=round(centroid, 0) if centroid is not None else None,
        sub=round(shares[0], 3),
        low=round(shares[1], 3),
        mid=round(shares[2], 3),
        high=round(shares[3], 3),
        air=round(shares[4], 3),
        tonality=round(_tonality(freqs, spec), 3),
    )


# ---------------------------------------------------------------------------
# tempo
# ---------------------------------------------------------------------------

_ONSET_RATE_HZ = 200.0


def _onset_envelope(x: np.ndarray, sr: int) -> tuple[np.ndarray, float]:
    """Positive log-spectral flux at ~200 Hz; returns (env, env_rate)."""
    hop = max(1, int(round(sr / _ONSET_RATE_HZ)))
    win = 1024 if sr <= 32000 else 2048
    if x.size < win * 4:
        return np.zeros(0), sr / hop
    peak = float(np.abs(x).max())
    if peak <= 0:
        return np.zeros(0), sr / hop
    x = x / peak
    n = 1 + (x.size - win) // hop
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(n, win), strides=(x.strides[0] * hop, x.strides[0])
    )
    mag = np.abs(sfft.rfft(frames * np.hanning(win), axis=1))
    lo = int(40.0 * win / sr)
    logmag = np.log1p(100.0 * mag[:, lo:])
    flux = np.maximum(np.diff(logmag, axis=0), 0.0).sum(axis=1)
    env = flux - flux.mean()
    return env, sr / hop


def _tempo_prior(bpm: float) -> float:
    if 80.0 <= bpm <= 160.0:
        return 1.0
    if 70.0 <= bpm <= 190.0:
        return 0.93
    return 0.85


def detect_bpm(x: np.ndarray, sr: int) -> tuple[float | None, float, bool]:
    """Estimate ``(bpm, confidence, periodic)`` of a loop or phrase.

    Onset-strength autocorrelation. When the file length is a whole
    number of beats at a plausible tempo (the normal case for loops),
    the estimate snaps to that exact length-derived tempo — which also
    settles the half/double-time ambiguity by pulse strength.
    """
    env, rate = _onset_envelope(x, sr)
    duration = x.size / sr
    if env.size < int(rate * 2.0) or float(np.dot(env, env)) <= 0:
        return None, 0.0, False
    n = env.size
    n_fft = sfft.next_fast_len(2 * n, real=True)
    spec = sfft.rfft(env, n_fft)
    ac = sfft.irfft(np.abs(spec) ** 2, n_fft)[: n // 2]
    ac = ac / ac[0]

    def ac_at(lag_s: float) -> float:
        i = lag_s * rate
        j = int(i)
        if j + 1 >= ac.size or j < 1:
            return 0.0
        frac = i - j
        # take the local max in a +-1 frame neighbourhood (tempo wobble)
        lo = max(1, j - 1)
        return float(max(ac[lo : j + 3].max(), (1 - frac) * ac[j] + frac * ac[j + 1]))

    candidates: list[tuple[float, float]] = []  # (score, bpm)
    for beats in (2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128):
        bpm = 60.0 * beats / duration
        if not 60.0 <= bpm <= 200.0:
            continue
        lag = 60.0 / bpm
        score = (ac_at(lag) + 0.5 * ac_at(2 * lag)) / 1.5 * _tempo_prior(bpm)
        candidates.append((score, bpm))
    # free estimate (no length assumption)
    lo = int(rate * 60.0 / 200.0)
    hi = min(ac.size - 2, int(rate * 60.0 / 60.0))
    free_bpm = None
    free_score = 0.0
    if hi > lo + 2:
        i = int(np.argmax(ac[lo:hi])) + lo
        free_bpm = 60.0 * rate / _parabolic(ac, i)
        free_score = float(ac[i])

    if candidates:
        score, bpm = max(candidates)
        if score >= 0.25 or free_bpm is None:
            return round(bpm, 2), float(np.clip(score * 1.3, 0.0, 1.0)), score >= 0.25
    if free_bpm is None:
        return None, 0.0, False
    return round(free_bpm, 1), float(np.clip(free_score, 0.0, 1.0)) * 0.6, free_score >= 0.35


# ---------------------------------------------------------------------------
# key
# ---------------------------------------------------------------------------


def detect_key(x: np.ndarray, sr: int) -> tuple[str | None, float]:
    """Krumhansl-Schmuckler key estimate over a magnitude chromagram.

    Returns canonical ``('F#m' | 'F#', confidence 0..1)``. Heuristic: a
    chroma profile match, reliable for sustained harmonic material (pads,
    chords, basslines) and meaningless for drums.
    """
    win = 16384 if sr >= 32000 else 8192
    if x.size < win:
        win = 1 << max(8, int(math.floor(math.log2(max(x.size, 256)))))
    if x.size < 2048:
        return None, 0.0
    hop = win // 2
    n = 1 + (x.size - win) // hop
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(n, win), strides=(x.strides[0] * hop, x.strides[0])
    )
    mag = np.abs(sfft.rfft(frames * np.hanning(win), axis=1))
    freqs = np.fft.rfftfreq(win, 1.0 / sr)
    valid = (freqs >= 65.0) & (freqs <= 3000.0)
    with np.errstate(divide="ignore"):
        midi = 69.0 + 12.0 * np.log2(freqs[valid] / 440.0)
    pcs = np.floor(midi + 0.5).astype(int) % 12
    power = mag[:, valid].sum(axis=0)
    chroma = np.bincount(pcs, weights=power, minlength=12).astype(np.float64)
    if chroma.sum() <= 0:
        return None, 0.0
    chroma /= chroma.sum()
    if float(chroma.std()) < 1e-6:
        return None, 0.0
    scores: dict[tuple[int, str], float] = {}
    for root in range(12):
        for mode, prof in (("major", _KS_MAJOR), ("minor", _KS_MINOR)):
            scores[(root, mode)] = float(np.corrcoef(chroma, np.roll(prof, root))[0, 1])
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (root, mode), best = ranked[0]
    runner_up = next(v for k, v in ranked if k != (root, mode))
    margin = best - runner_up
    confidence = float(np.clip(0.6 * max(0.0, best) + 2.0 * max(0.0, margin), 0.0, 1.0))
    return PITCH_CLASS_NAMES[root] + ("m" if mode == "minor" else ""), confidence


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------


def probe(
    path: Path,
    *,
    want_bpm: bool = False,
    want_key: bool = False,
) -> AudioProbe:
    """Measure everything about one audio file.

    Raises whatever soundfile raises on unreadable files — the caller
    (the indexer) converts that into a reported problem.
    """
    info = sf.info(path)
    data, _ = sf.read(path, dtype="float32", always_2d=True)
    sr = info.samplerate
    if data.shape[0] == 0:
        raise ValueError("file contains no audio frames")
    if not np.isfinite(data).all():
        raise ValueError("file contains NaN/inf samples")
    x = data.mean(axis=1, dtype=np.float64)
    duration = data.shape[0] / sr

    peak = float(np.abs(data).max())
    rms = float(np.sqrt(np.mean(np.square(data, dtype=np.float64))))
    bits = _SUBTYPE_BIT_DEPTH.get(info.subtype) or 16
    clipped = count_clipped(data, lsb=2.0 ** -(min(bits, 24) - 1))

    side = 0.0
    if data.shape[1] >= 2:
        mid_rms = float(np.sqrt(np.mean(((data[:, 0] + data[:, 1]) / 2.0) ** 2)))
        side_rms = float(np.sqrt(np.mean(((data[:, 0] - data[:, 1]) / 2.0) ** 2)))
        side = side_rms / mid_rms if mid_rms > 1e-9 else 0.0

    bpm = bpm_conf = None
    periodic = False
    if want_bpm and duration >= 1.5:
        bpm, bpm_conf, periodic = detect_bpm(x, sr)

    key = key_conf = None
    if want_key and duration >= 1.0:
        key, key_conf = detect_key(x, sr)

    return AudioProbe(
        duration_s=duration,
        sample_rate=sr,
        channels=info.channels,
        bit_depth=_SUBTYPE_BIT_DEPTH.get(info.subtype),
        peak_dbfs=20.0 * math.log10(peak) if peak > 0 else -120.0,
        rms_dbfs=20.0 * math.log10(rms) if rms > 0 else -120.0,
        loudness_lufs=integrated_lufs(data, sr),
        clipping=clipped >= CLIP_MIN_SAMPLES,
        clipped_samples=clipped,
        features=_features(x, sr),
        side_ratio=side,
        bpm=bpm,
        bpm_confidence=round(bpm_conf, 3) if bpm_conf is not None else None,
        periodic=periodic,
        key=key,
        key_confidence=round(key_conf, 3) if key_conf is not None else None,
    )
