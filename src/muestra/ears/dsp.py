"""DSP primitives for muestra.ears — pure numpy/scipy, no project state.

Audio arrays are ``(n_frames, n_channels)`` float64 plus a sample rate,
except where a function says it takes mono. Everything here is
deterministic and vectorised: ears runs after every render, so the
whole analysis has a ~2 s budget for a 16-bar loop.

Loudness follows ITU-R BS.1770-4 (K-weighting, 400 ms blocks with 75 %
overlap, -70 LUFS absolute and -10 LU relative gates) and EBU Tech 3342
(loudness range); the implementation is verified against the EBU Tech
3341/3342 reference signals in ``tests/ears/test_dsp.py``.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import soundfile as sf
from numpy.lib.stride_tricks import sliding_window_view
from scipy import ndimage, signal

__all__ = [
    "FLOOR_DB",
    "read_audio",
    "mono",
    "db",
    "power_db",
    "k_weighting_sos",
    "weighted_power",
    "loudness_blocks",
    "integrated_loudness",
    "short_term_loudness",
    "loudness_range",
    "true_peak",
    "clipped_samples",
    "Spectrogram",
    "spectrogram",
    "band_power_db",
    "band_frames",
    "lowpass",
    "highpass",
    "band_envelope",
    "rms_envelope_db",
    "ms_envelope",
    "Onsets",
    "onsets",
    "chroma",
    "tuning_cents",
    "estimate_key",
    "key_scale",
    "KK_MAJOR",
    "KK_MINOR",
    "AUDIO_MAJOR",
    "AUDIO_MINOR",
    "HARMONIC_TEMPLATE",
    "correlation",
    "edge_discontinuity",
]

#: floor used for dB conversions of silence (dB); keeps everything finite.
FLOOR_DB = -150.0
_EPS = 10.0 ** (FLOOR_DB / 10.0)


# ---------------------------------------------------------------------------
# basics
# ---------------------------------------------------------------------------


def read_audio(path: Path) -> tuple[np.ndarray, int]:
    """Read a wav as float64 ``(n, ch)`` plus its sample rate."""
    data, sr = sf.read(str(path), dtype="float64", always_2d=True)
    return np.ascontiguousarray(data), int(sr)


def mono(x: np.ndarray) -> np.ndarray:
    """Average channels to mono (1-D input passes through)."""
    return x.mean(axis=1) if x.ndim == 2 else np.asarray(x, dtype=np.float64)


def db(amplitude: float) -> float:
    """Linear amplitude -> dB (floored at FLOOR_DB)."""
    return max(FLOOR_DB, 20.0 * math.log10(max(float(amplitude), 1e-300)))


def power_db(power: float | np.ndarray) -> float | np.ndarray:
    """Power (mean square) -> dB, floored. Works on scalars and arrays."""
    if np.isscalar(power):
        return max(FLOOR_DB, 10.0 * math.log10(max(float(power), 1e-300)))
    return np.maximum(FLOOR_DB, 10.0 * np.log10(np.maximum(power, 1e-300)))


# ---------------------------------------------------------------------------
# loudness (ITU-R BS.1770-4 / EBU Tech 3342)
# ---------------------------------------------------------------------------


def k_weighting_sos(sr: int) -> np.ndarray:
    """K-weighting (pre-filter shelf + RLB high-pass) as second-order sections.

    Analog-prototype parameters from libebur128; at 48 kHz they reproduce
    the coefficients tabulated in BS.1770-4 to ~1e-8.
    """
    f0, gain_db, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / sr)
    vh = 10.0 ** (gain_db / 20.0)
    vb = vh**0.4996667741545416
    a0 = 1.0 + k / q + k * k
    shelf = [
        (vh + vb * k / q + k * k) / a0,
        2.0 * (k * k - vh) / a0,
        (vh - vb * k / q + k * k) / a0,
        1.0,
        2.0 * (k * k - 1.0) / a0,
        (1.0 - k / q + k * k) / a0,
    ]
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / sr)
    a0 = 1.0 + k / q + k * k
    hp = [1.0, -2.0, 1.0, 1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0]
    return np.array([shelf, hp])


def weighted_power(x: np.ndarray, sr: int) -> np.ndarray:
    """Per-sample K-weighted power summed over channels (G=1 for L/R)."""
    y = signal.sosfilt(k_weighting_sos(sr), x, axis=0)
    return np.sum(y * y, axis=1)


def loudness_blocks(
    x: np.ndarray, sr: int, block_s: float, hop_s: float = 0.1, *, power: np.ndarray | None = None
) -> np.ndarray:
    """Mean K-weighted power of each ``block_s`` window, hopping ``hop_s``."""
    p = weighted_power(x, sr) if power is None else power
    hop = max(1, int(round(hop_s * sr)))
    n_seg = len(p) // hop
    k = max(1, int(round(block_s / hop_s)))
    if n_seg < k:
        return np.empty(0)
    seg = p[: n_seg * hop].reshape(n_seg, hop).sum(axis=1)
    csum = np.concatenate([[0.0], np.cumsum(seg)])
    return (csum[k:] - csum[:-k]) / (k * hop)


def _lufs(power: np.ndarray | float) -> np.ndarray | float:
    return -0.691 + 10.0 * np.log10(np.maximum(power, 1e-300))


def integrated_loudness(x: np.ndarray, sr: int, *, power: np.ndarray | None = None) -> float:
    """Gated integrated loudness in LUFS (FLOOR_DB for silence).

    Renders shorter than one 400 ms block are measured ungated.
    """
    p = weighted_power(x, sr) if power is None else power
    blocks = loudness_blocks(x, sr, 0.4, power=p)
    if blocks.size == 0:
        mean = float(np.mean(p)) if p.size else 0.0
        return float(max(FLOOR_DB, _lufs(mean))) if mean > 0 else FLOOR_DB
    lj = _lufs(blocks)
    above_abs = blocks[lj > -70.0]
    if above_abs.size == 0:
        return FLOOR_DB
    rel_gate = _lufs(np.mean(above_abs)) - 10.0
    gated = blocks[(lj > -70.0) & (lj > rel_gate)]
    return float(_lufs(np.mean(gated)))


def short_term_loudness(x: np.ndarray, sr: int, *, power: np.ndarray | None = None) -> np.ndarray:
    """Short-term (3 s) loudness series in LUFS, 10 Hz. Empty if < 3 s."""
    blocks = loudness_blocks(x, sr, 3.0, power=power)
    return _lufs(blocks) if blocks.size else blocks


def loudness_range(st_lufs: np.ndarray) -> float | None:
    """EBU Tech 3342 LRA from a short-term loudness series (LU)."""
    if st_lufs.size == 0:
        return None
    st = st_lufs[st_lufs > -70.0]
    if st.size == 0:
        return None
    power = 10.0 ** ((st + 0.691) / 10.0)
    rel = _lufs(np.mean(power)) - 20.0
    st = st[st > rel]
    if st.size == 0:
        return None
    lo, hi = np.percentile(st, [10.0, 95.0])
    return float(hi - lo)


def true_peak(x: np.ndarray, sr: int) -> float:
    """Oversampled (inter-sample) peak, linear. 4x below 96 kHz, else 2x."""
    if x.size == 0:
        return 0.0
    factor = 4 if sr < 96000 else 2
    up = signal.resample_poly(x, factor, 1, axis=0)
    return float(max(np.max(np.abs(up)), np.max(np.abs(x))))


def clipped_samples(x: np.ndarray, threshold: float = 0.9999) -> tuple[int, int]:
    """(samples at |x| >= threshold, samples inside runs of >= 2 such samples).

    A run of consecutive full-scale samples is a flat top = real clipping;
    an isolated full-scale sample is just a hot peak.
    """
    hot = np.abs(x) >= threshold
    total = int(np.count_nonzero(hot))
    if total == 0:
        return 0, 0
    prev = np.zeros_like(hot)
    prev[1:] = hot[:-1]
    nxt = np.zeros_like(hot)
    nxt[:-1] = hot[1:]
    return total, int(np.count_nonzero(hot & (prev | nxt)))


# ---------------------------------------------------------------------------
# spectra
# ---------------------------------------------------------------------------


class Spectrogram:
    """One-sided power spectrogram of a mono signal.

    ``power[f, t]`` is scaled so that summing over all bins gives the
    mean square of the frame (a full-scale sine reads -3 dB).
    """

    def __init__(self, power: np.ndarray, freqs: np.ndarray, times: np.ndarray, hop_s: float):
        self.power = power
        self.freqs = freqs
        self.times = times
        self.hop_s = hop_s

    def band(self, lo: float, hi: float) -> np.ndarray:
        """Band power per frame (sum of bins in [lo, hi))."""
        sel = (self.freqs >= lo) & (self.freqs < hi)
        if not np.any(sel):
            return np.zeros(self.power.shape[1])
        return self.power[sel].sum(axis=0)


def _frames(y: np.ndarray, nfft: int, hop: int) -> np.ndarray:
    if len(y) < nfft:
        y = np.pad(y, (0, nfft - len(y)))
    return sliding_window_view(y, nfft)[::hop]


def spectrogram(y: np.ndarray, sr: int, nfft: int = 2048, hop: int = 512) -> Spectrogram:
    """Power spectrogram of mono ``y`` (Hann window, frames start at 0)."""
    win = np.hanning(nfft)
    frames = _frames(np.asarray(y, dtype=np.float64), nfft, hop)
    spec = np.fft.rfft(frames * win, axis=1)
    p = (spec.real**2 + spec.imag**2) * (2.0 / (nfft * np.sum(win**2)))
    p[:, 0] *= 0.5
    if nfft % 2 == 0:
        p[:, -1] *= 0.5
    freqs = np.fft.rfftfreq(nfft, 1.0 / sr)
    times = (np.arange(p.shape[0]) * hop + nfft / 2) / sr
    return Spectrogram(p.T, freqs, times, hop / sr)


def band_power_db(spec: Spectrogram, lo: float, hi: float) -> float:
    """Mean band power over the whole spectrogram, dB."""
    return float(power_db(float(np.mean(spec.band(lo, hi)))))


def band_frames(spec: Spectrogram, edges: dict[str, tuple[float, float]]) -> np.ndarray:
    """(n_bands, n_frames) band powers for an ordered dict of edges."""
    return np.stack([spec.band(lo, hi) for lo, hi in edges.values()])


# ---------------------------------------------------------------------------
# filters + envelopes
# ---------------------------------------------------------------------------


def lowpass(y: np.ndarray, sr: int, hz: float, order: int = 4) -> np.ndarray:
    sos = signal.butter(order, min(hz, 0.45 * sr), btype="low", fs=sr, output="sos")
    return signal.sosfilt(sos, y, axis=0)


def highpass(y: np.ndarray, sr: int, hz: float, order: int = 4) -> np.ndarray:
    sos = signal.butter(order, min(hz, 0.45 * sr), btype="high", fs=sr, output="sos")
    return signal.sosfilt(sos, y, axis=0)


def band_envelope(
    y: np.ndarray, sr: int, lo: float, hi: float, hop_s: float = 0.005, win_s: float = 0.02
) -> tuple[np.ndarray, np.ndarray]:
    """Band-limited mean-square envelope of mono ``y``: (times_s, power).

    Zero-phase band-pass (forward-backward), so envelope peaks are not
    delayed relative to the events that cause them.
    """
    sos = signal.butter(4, [lo, min(hi, 0.45 * sr)], btype="band", fs=sr, output="sos")
    z = signal.sosfiltfilt(sos, y)
    return ms_envelope(z, sr, hop_s, win_s)


def ms_envelope(
    z: np.ndarray, sr: int, hop_s: float, win_s: float
) -> tuple[np.ndarray, np.ndarray]:
    hop = max(1, int(round(hop_s * sr)))
    win = max(hop, int(round(win_s * sr)))
    sq = z * z
    csum = np.concatenate([[0.0], np.cumsum(sq)])
    starts = np.arange(0, max(1, len(z) - win + 1), hop)
    power = (csum[np.minimum(starts + win, len(z))] - csum[starts]) / win
    times = (starts + win / 2) / sr
    return times, power


def rms_envelope_db(
    y: np.ndarray, sr: int, hop_s: float = 0.002, win_s: float = 0.004
) -> tuple[np.ndarray, np.ndarray]:
    """Broadband short-window level envelope of mono ``y`` in dB."""
    t, p = ms_envelope(np.asarray(y, dtype=np.float64), sr, hop_s, win_s)
    return t, power_db(p)


# ---------------------------------------------------------------------------
# onsets
# ---------------------------------------------------------------------------


class Onsets:
    """Detected onsets: refined times (s) and strengths (0..1, relative)."""

    def __init__(self, times: np.ndarray, strength: np.ndarray):
        self.times = times
        self.strength = strength

    def __len__(self) -> int:
        return int(self.times.size)

    def strongest(self, share: float) -> Onsets:
        """Onsets whose strength is at least ``share`` of the strongest."""
        keep = self.strength >= share
        return Onsets(self.times[keep], self.strength[keep])


def onsets(
    y: np.ndarray,
    sr: int,
    *,
    min_gap_s: float = 0.05,
    delta: float = 0.08,
    refine: bool = True,
) -> Onsets:
    """Spectral-flux onsets of mono ``y`` with time-domain refinement.

    1. Log-magnitude spectral flux (nfft ~21 ms, hop ~2.7 ms), normalised
       by its 99th percentile; local maxima above a moving median +
       ``delta`` (``min_gap_s`` apart) are onsets; their time is
       parabolic-interpolated on the flux curve.
    2. Each onset is refined on a 3 ms linear-power envelope (0.25 ms
       hop): the time where the level crosses halfway between the
       pre-onset floor and the attack peak. Kept only when the rise is
       clear (> 6 dB) and the refined time is within 8 ms of step 1, so
       noisy material falls back to the flux estimate.
    """
    y = np.asarray(y, dtype=np.float64)
    if y.size < 2048 or not np.any(y):
        return Onsets(np.empty(0), np.empty(0))
    nfft = 1 << int(round(math.log2(0.021 * sr)))
    hop = nfft // 8
    spec = spectrogram(y, sr, nfft=nfft, hop=hop)
    logmag = np.log1p(np.sqrt(spec.power) * 1000.0)
    flux = np.concatenate([[0.0], np.maximum(np.diff(logmag, axis=1), 0.0).sum(axis=0)])
    ref = float(np.percentile(flux, 99.0))
    if ref <= 0:
        return Onsets(np.empty(0), np.empty(0))
    flux = flux / ref
    med = ndimage.median_filter(flux, size=max(3, int(0.3 * sr / hop) | 1), mode="nearest")
    gap = max(1, int(round(min_gap_s * sr / hop)))
    local_max = ndimage.maximum_filter1d(flux, size=2 * gap + 1, mode="nearest")
    cand = np.flatnonzero((flux >= local_max) & (flux > med + delta) & (flux > 0.05))
    cand = cand[(cand > 0) & (cand < flux.size - 1)]
    if cand.size == 0:
        return Onsets(np.empty(0), np.empty(0))
    y0, y1, y2 = flux[cand - 1], flux[cand], flux[cand + 1]
    den = y0 - 2.0 * y1 + y2
    shift = np.where(np.abs(den) > 1e-12, 0.5 * (y0 - y2) / np.where(den == 0, 1.0, den), 0.0)
    pos = cand + np.clip(shift, -0.5, 0.5)
    # the flux peaks when the new energy reaches the middle of the window
    frame_t = (pos * hop + nfft / 2) / sr
    strength = np.clip(flux[cand] / max(float(np.max(flux[cand])), 1e-9), 0, 1)
    if not refine:
        return Onsets(frame_t, strength)
    return Onsets(_refine(y, sr, frame_t), strength)


def _refine(y: np.ndarray, sr: int, approx: np.ndarray) -> np.ndarray:
    hop = max(1, int(round(0.00025 * sr)))
    win = max(2, int(round(0.003 * sr)))
    csum = np.concatenate([[0.0], np.cumsum(y * y)])
    out = approx.copy()
    before, after = int(0.025 * sr), int(0.010 * sr)
    guard = 0.008
    for i, t in enumerate(approx):
        c = int(t * sr)
        a, b = c - before, c + after
        if a < 0 or b + win >= y.size:
            continue
        starts = np.arange(a, b, hop)
        e = (csum[starts + win] - csum[starts]) / win
        centers = (starts + win / 2.0) / sr
        split = np.searchsorted(centers, t - guard)
        if split < 4 or split >= e.size - 2:
            continue
        floor = float(np.percentile(e[:split], 20))
        k_peak = split + int(np.argmax(e[split:]))
        peak = float(e[k_peak])
        if peak < 4.0 * max(floor, 1e-20):  # rise < 6 dB: not a clean attack
            continue
        half = floor + 0.5 * (peak - floor)
        below = np.flatnonzero(e[:k_peak] <= half)
        if below.size == 0:
            continue
        k = int(below[-1])
        frac = (half - e[k]) / max(e[k + 1] - e[k], 1e-30)
        tr = float(centers[k] + np.clip(frac, 0.0, 1.0) * hop / sr)
        if abs(tr - t) <= guard:
            out[i] = tr
    return out


# ---------------------------------------------------------------------------
# tonal
# ---------------------------------------------------------------------------

#: Krumhansl-Kessler probe-tone profiles, C..B.
KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

#: Chroma of ONE harmonic note (pc 0) as seen by :func:`chroma`, measured
#: on harmonic tones (electric piano, saw, bright lead; MIDI 40-80): the
#: 3rd and 6th harmonics put ~1/3 of the root's weight on the fifth, the
#: 5th harmonic some on the major third. Key profiles are convolved with
#: it so that a fifth lit up by harmonics is not mistaken for a tonic.
HARMONIC_TEMPLATE = np.array([1.0, 0, 0.03, 0, 0.12, 0, 0.02, 0.32, 0, 0, 0.06, 0])


def _audio_profile(profile: np.ndarray) -> np.ndarray:
    return sum(profile[p] * np.roll(HARMONIC_TEMPLATE, p) for p in range(12))


_MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
_MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10, 11)  # natural minor + leading tone


def key_scale(tonic_pc: int, mode: str) -> set[int]:
    """Pitch classes that belong to the key (minor adds the leading tone)."""
    steps = _MAJOR_SCALE if mode == "major" else _MINOR_SCALE
    return {(tonic_pc + s) % 12 for s in steps}


def _peaks(y: np.ndarray, sr: int, fmin: float, fmax: float):
    """Spectral peaks over frames: (midi_float, weight) arrays."""
    nfft = 1 << int(round(math.log2(0.17 * sr)))  # 8192 @ 48 kHz
    hop = nfft // 2
    win = np.hanning(nfft)
    frames = _frames(y, nfft, hop)
    mag = np.abs(np.fft.rfft(frames * win, axis=1))  # (t, f)
    lo = max(2, int(fmin * nfft / sr))
    hi = min(mag.shape[1] - 2, int(fmax * nfft / sr))
    m = mag[:, lo - 1 : hi + 2]
    c = m[:, 1:-1]
    is_peak = (c > m[:, :-2]) & (c >= m[:, 2:])
    frame_max = c.max(axis=1, keepdims=True)
    is_peak &= c > frame_max * 0.03  # within ~30 dB of the frame's max
    t_idx, f_idx = np.nonzero(is_peak)
    if t_idx.size == 0:
        return np.empty(0), np.empty(0)
    lm = np.log(m + 1e-12)
    y0 = lm[t_idx, f_idx]
    y1 = lm[t_idx, f_idx + 1]
    y2 = lm[t_idx, f_idx + 2]
    den = y0 - 2.0 * y1 + y2
    shift = np.where(np.abs(den) > 1e-12, 0.5 * (y0 - y2) / np.where(den == 0, 1, den), 0.0)
    shift = np.clip(shift, -0.5, 0.5)
    freq = (lo + f_idx + shift) * sr / nfft
    midi = 69.0 + 12.0 * np.log2(freq / 440.0)
    weight = c[t_idx, f_idx]
    return midi, weight


def chroma(y: np.ndarray, sr: int, *, tuning: float = 0.0) -> np.ndarray:
    """12-bin pitch-class profile (C..B, sums to 1) from spectral peaks.

    Peaks 55-2000 Hz, compressed (sqrt) weights so one loud partial does
    not dominate, folded onto the nearest semitone after removing
    ``tuning`` cents. Returns zeros for silence.
    """
    midi, w = _peaks(np.asarray(y, dtype=np.float64), sr, 55.0, 2000.0)
    if midi.size == 0:
        return np.zeros(12)
    pc = np.round(midi - tuning / 100.0).astype(int) % 12
    out = np.bincount(pc, weights=np.sqrt(w), minlength=12).astype(np.float64)
    s = float(out.sum())
    return out / s if s > 0 else out


def tuning_cents(y: np.ndarray, sr: int) -> tuple[float | None, float]:
    """Tuning offset vs A440 12-TET in cents [-50, 50), plus a 0..1 strength.

    Mode of a magnitude-weighted circular histogram of spectral-peak
    deviations (peaks 80-4000 Hz). The mode, not the mean, so the
    naturally flat 5th/7th harmonics of rich tones do not bias it.
    Returns (None, 0) when there are too few peaks.
    """
    midi, w = _peaks(np.asarray(y, dtype=np.float64), sr, 80.0, 4000.0)
    if midi.size < 8:
        return None, 0.0
    dev = (midi - np.round(midi)) * 100.0  # [-50, 50]
    bins = np.arange(-50, 50)
    hist = np.bincount(((np.round(dev).astype(int) + 50) % 100), weights=w, minlength=100)
    # circular gaussian smoothing, sigma 4 cents
    k = np.exp(-0.5 * (np.arange(-12, 13) / 4.0) ** 2)
    padded = np.concatenate([hist[-12:], hist, hist[:12]])
    smooth = np.convolve(padded, k, mode="same")[12:-12]
    j = int(np.argmax(smooth))
    # parabolic refinement of the circular mode
    y0, y1, y2 = smooth[(j - 1) % 100], smooth[j], smooth[(j + 1) % 100]
    den = y0 - 2 * y1 + y2
    shift = 0.0 if abs(den) < 1e-12 else float(np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5))
    cents = float(bins[j] + shift)
    if cents >= 50.0:
        cents -= 100.0
    total = float(np.sum(hist))
    concentration = float(y1) / total if total > 0 else 0.0  # ~0.1 uniform .. 1 single
    return cents, float(np.clip((concentration - 0.1) / 0.9, 0.0, 1.0))


def correlation(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    den = float(np.sqrt(np.sum(a * a) * np.sum(b * b)))
    return float(np.sum(a * b) / den) if den > 1e-12 else 0.0


AUDIO_MAJOR = _audio_profile(KK_MAJOR)
AUDIO_MINOR = _audio_profile(KK_MINOR)


def estimate_key(profile: np.ndarray) -> tuple[int, str, float, float]:
    """Krumhansl-Kessler key estimate from a 12-bin chroma.

    Returns ``(tonic_pc, mode, r_best, confidence)``. Confidence is the
    margin of the winner over the best candidate that is neither the
    winner nor its relative major/minor (relative keys share a scale and
    are not counted as a competing hypothesis), scaled to 0..1.
    """
    if not np.any(profile):
        return 0, "major", 0.0, 0.0
    scores: list[tuple[float, int, str]] = []
    for prof, mode in ((AUDIO_MAJOR, "major"), (AUDIO_MINOR, "minor")):
        for pc in range(12):
            scores.append((correlation(profile, np.roll(prof, pc)), pc, mode))
    scores.sort(reverse=True)
    r1, pc1, mode1 = scores[0]
    rel = ((pc1 + 9) % 12, "minor") if mode1 == "major" else ((pc1 + 3) % 12, "major")
    r2 = next(r for r, pc, m in scores[1:] if (pc, m) != rel)
    confidence = float(np.clip((r1 - r2) / 0.25, 0.0, 1.0) * np.clip(r1 / 0.6, 0.0, 1.0))
    return pc1, mode1, float(r1), round(confidence, 3)


# ---------------------------------------------------------------------------
# clicks
# ---------------------------------------------------------------------------


def edge_discontinuity(
    y: np.ndarray, i: int, sr: int, search: int = 3, pre_gap: int = 0
) -> tuple[float, float, int]:
    """Click score at a KNOWN edge (sample index ``i``) of mono ``y``.

    The prediction error of a 2-sample linear predictor,
    ``|x[n] - (2x[n-1] - x[n-2])|`` (the second difference), spikes where
    the waveform steps or kinks and is ~(2*pi*f/sr)**2 times smaller than
    the signal for smooth low-frequency content. Its maximum within
    +-``search`` samples of the edge (``jump``) is compared with its RMS
    over 5 ms BEFORE the edge, ending ``pre_gap`` samples earlier (pass
    the declared fade length, so the reference is the material being cut
    and not its fade-out). The audio after the edge is often a new attack
    and is deliberately not used as reference. Returns
    ``(ratio, jump, index)``.
    """
    w = max(16, int(0.005 * sr))
    pre_end = i - search - pre_gap - 1
    pre_start = pre_end - w
    if pre_start - 1 < 0 or i + search + 2 > y.size:
        return 0.0, 0.0, i
    a = i - search
    seg = y[a - 1 : i + search + 2]
    near = np.abs(seg[2:] - 2.0 * seg[1:-1] + seg[:-2])  # centres a .. i + search
    k = int(np.argmax(near))
    jump = float(near[k])
    ps = y[pre_start - 1 : pre_end + 1]
    pre = ps[2:] - 2.0 * ps[1:-1] + ps[:-2]
    ref = float(np.sqrt(np.mean(pre * pre)))
    return jump / (ref + 1e-7), jump, a + k
