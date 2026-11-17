"""Stateful insert effects and sample-aligned true-peak mastering."""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
import pedalboard as pb
from scipy.signal import lfilter, resample_poly, sosfilt

from ..errors import MuestraError
from ..plugins import core as plugins
from ..schemas.song import Fx
from .automation import Automation

# 0.67 ms at 48 kHz. Parameters update without resetting effect state.
BLOCK = 32


def _filter_coefficients(order, cutoff, sr, highpass):
    """Vectorized bilinear Butterworth sections, one set per control block."""
    cutoff = np.atleast_1d(cutoff)
    angle = 2 * np.pi * np.minimum(cutoff, sr * 0.499) / sr
    cosine, sine = np.cos(angle)[:, None], np.sin(angle)[:, None]
    q = 1 / (2 * np.cos((2 * np.arange(order // 2) + 1) * np.pi / (2 * order)))
    alpha = sine / (2 * q)
    sos = np.empty((len(cutoff), order // 2, 6))
    numerator = (1 + cosine) / 2 if highpass else (1 - cosine) / 2
    sos[:, :, 0] = numerator
    sos[:, :, 1] = (-2 if highpass else 2) * numerator
    sos[:, :, 2] = numerator
    sos[:, :, 3] = 1 + alpha
    sos[:, :, 4] = -2 * cosine
    sos[:, :, 5] = 1 - alpha
    sos /= sos[:, :, 3:4]
    return sos


def _dependency(name: str):
    raise MuestraError.one(
        "dependency_missing",
        f"The renderer needs {name}.",
        path="render",
        value=name,
        hint="Install renderer dependencies with uv sync --extra render.",
    )


def _follower(signal, sr, attack, release):
    """Stereo-linked peak follower; coefficient selection at sub-ms blocks."""
    out = np.empty(len(signal), np.float64)
    state = 0.0
    for start in range(0, len(signal), BLOCK):
        end = min(start + BLOCK, len(signal))
        # Select the envelope time constant by direction, retaining filter state.
        rising = np.max(signal[start:end]) > state
        ms = np.asarray(attack if rising else release)
        ms = float(ms if ms.ndim == 0 else ms[start])
        c = math.exp(-1 / (sr * ms / 1000)) if ms > 0 else 0.0
        out[start:end], z = lfilter([1 - c], [1, -c], signal[start:end], zi=[c * state])
        state = out[end - 1]
    return out


def sidechain_envelope(events, frames, sr, attack, release, key=None):
    if events is None:
        return np.clip(_follower(np.max(np.abs(key), axis=1), sr, attack, release), 0, 1)
    env = np.zeros(frames, np.float64)
    events = sorted(set(int(e) for e in events if 0 <= e < frames))
    release = np.broadcast_to(release, (frames,))
    attack = np.broadcast_to(attack, (frames,))
    previous = 0.0
    for index, event in enumerate(events):
        end = events[index + 1] if index + 1 < len(events) else frames
        if event:
            previous = env[event - 1]
        ramp_end = min(end, event + max(1, round(sr * attack[event] / 1000)))
        n = ramp_end - event
        env[event:ramp_end] = previous + (1 - previous) * (np.arange(n) + 1) / n
        if ramp_end < end:
            decay = np.cumsum(1000 / (sr * release[ramp_end:end]))
            env[ramp_end:end] = np.exp(-decay)
    return env


def _native(fx, bpm, project):
    values = fx.model_dump(exclude={"id", "type"})
    if fx.type == "plugin":
        try:
            return plugins.build_plugin_fx(project, fx)
        except NotImplementedError as exc:
            raise MuestraError.one(
                "plugin_unavailable",
                "Plugin hosting is unavailable.",
                path=f"fx.{fx.id or 'plugin'}.plugin",
                value=fx.plugin,
                hint="Install/configure the plugin and complete the WP3 plugin host.",
            ) from exc
    if fx.type == "delay":
        values["delay_seconds"] = values.pop("time_beats") * 60 / bpm
    if fx.type == "saturation":
        return pb.Distortion(drive_db=fx.drive_db)
    cls = {
        "gain": pb.Gain,
        "reverb": pb.Reverb,
        "delay": pb.Delay,
        "chorus": pb.Chorus,
        "phaser": pb.Phaser,
    }[fx.type]
    return cls(**values)


@lru_cache(maxsize=16)
def limiter_latency(sr: int, true_peak: bool = True) -> int:
    """Probe streaming latency, including oversampling, rather than guessing ms."""
    effect = pb.BrickwallLimiter(ceiling_db=0, true_peak=true_peak)
    n = max(4096, sr // 8)
    probe = np.zeros((2, n), np.float32)
    probe[:, 64] = 0.01
    chunks = [
        effect.process(probe[:, i : i + BLOCK], sr, reset=(i == 0)) for i in range(0, n, BLOCK)
    ]
    return int(np.argmax(np.concatenate(chunks, axis=1)[0]) - 64)


def _limit(audio, sr, auto, prefix, fx):
    effect = pb.BrickwallLimiter(ceiling_db=fx.ceiling_db, release_ms=fx.release_ms, true_peak=True)
    if not auto.has(prefix):
        # A single offline call flushes and compensates reported latency;
        # streaming calls below require explicit flush and compensation.
        return effect.process(np.ascontiguousarray(audio.T), sr, reset=True).T.copy()
    latency = limiter_latency(sr)
    padded = np.pad(audio, ((0, latency), (0, 0)))
    output = np.empty_like(padded)
    for i in range(0, len(padded), BLOCK):
        for name in ("ceiling_db", "release_ms"):
            setattr(effect, name, float(auto.value(f"{prefix}.{name}", i, getattr(fx, name))))
        block = effect.process(np.ascontiguousarray(padded[i : i + BLOCK].T), sr, reset=(i == 0))
        output[i : i + len(block.T)] = block.T
    return output[latency : latency + len(audio)]


def apply_fx(audio, effects: list[Fx], sr, bpm, auto: Automation, prefix, project, events, tracks):
    audio = audio.copy()
    positions = np.arange(len(audio), dtype=np.float64)
    for fx in effects:
        path = f"{prefix}.fx.{fx.id}" if fx.id else f"{prefix}.fx"

        def values(name, frames=positions, _fx=fx, _path=path):
            return auto.value(f"{_path}.{name}", frames, getattr(_fx, name))

        if fx.type == "sidechain":
            key_events = events.get((fx.key_track, fx.key_lane))
            env = sidechain_envelope(
                key_events,
                len(audio),
                sr,
                values("attack_ms"),
                values("release_ms"),
                tracks[fx.key_track],
            )
            audio *= (1 - values("depth") * env)[:, None]
        elif fx.type in ("lowpass", "highpass"):
            # Butterworth SOS implements the declared order; pb's native filters
            # are first-order. All banks retain state when order is automated.
            orders = [2, 4, 6, 8] if auto.has(path + ".order") else [fx.order]
            states = {order: np.zeros((order // 2, 2, 2)) for order in orders}
            size = BLOCK if auto.has(path) else len(audio)
            cutoffs = np.broadcast_to(
                values("cutoff_hz", np.arange(0, len(audio), size)), (math.ceil(len(audio) / size),)
            )
            banks = {
                order: _filter_coefficients(order, cutoffs, sr, fx.type == "highpass")
                for order in orders
            }
            for index, i in enumerate(range(0, len(audio), size)):
                chunk = audio[i : i + size].copy()
                outputs = []
                for order in orders:
                    sos = banks[order][index]
                    filtered, states[order] = sosfilt(sos, chunk, axis=0, zi=states[order])
                    outputs.append(filtered)
                if len(orders) == 1:
                    audio[i : i + len(chunk)] = outputs[0]
                else:
                    order = float(values("order", i))
                    weights = np.maximum(0, 1 - np.abs(np.asarray(orders) - order) / 2)
                    audio[i : i + len(chunk)] = sum(
                        w * a for w, a in zip(weights, outputs, strict=True)
                    )
        elif fx.type == "compressor":
            # Soft-knee, linked stereo peak detector. Native Compressor has no
            # knee control; never silently discard the contracted knee_db.
            env = _follower(
                np.max(np.abs(audio), axis=1), sr, values("attack_ms"), values("release_ms")
            )
            over = 20 * np.log10(np.maximum(env, 1e-12)) - values("threshold_db")
            knee = np.asarray(values("knee_db"))
            soft = (over + knee / 2) ** 2 / (2 * np.maximum(knee, 1e-9))
            reduction = np.where(over < -knee / 2, 0, np.where(over > knee / 2, over, soft))
            gain = values("makeup_gain_db") + (1 / values("ratio") - 1) * reduction
            audio *= np.power(10, gain / 20)[:, None]
        elif fx.type == "limiter":
            audio = _limit(audio, sr, auto, path, fx)
        elif fx.type == "gain":
            audio *= np.expand_dims(np.power(10, values("gain_db") / 20), -1)
        else:
            effect = _native(fx, bpm, project)
            automated = auto.has(path)
            size = BLOCK if automated else len(audio)
            for i in range(0, len(audio), size):
                if automated:
                    params = (
                        fx.params if fx.type == "plugin" else fx.model_dump(exclude={"id", "type"})
                    )
                    for name, default in params.items():
                        target = (
                            f"{path}.params.{name}" if fx.type == "plugin" else f"{path}.{name}"
                        )
                        value = float(auto.value(target, i, default))
                        native_name = "delay_seconds" if name == "time_beats" else name
                        if name == "time_beats":
                            value *= 60 / bpm
                        try:
                            setattr(effect, native_name, value)
                        except (AttributeError, ValueError, RuntimeError) as exc:
                            raise MuestraError.one(
                                "plugin_parameter",
                                str(exc),
                                path=target,
                                value=value,
                                hint="Use a writable native plugin parameter and its native range.",
                            ) from exc
                processed = effect.process(
                    np.ascontiguousarray(audio[i : i + size].T), sr, reset=(i == 0)
                )
                audio[i : i + size] = processed.T
    if not np.isfinite(audio).all():
        raise MuestraError.one(
            "invalid_audio",
            "Effects produced NaN/Inf.",
            path=prefix,
            hint="Reduce feedback/drive or check the hosted plugin.",
        )
    return audio


def master(audio, song, sr, auto, project, events, tracks):
    from ..schemas.song import LimiterFx

    audio = apply_fx(
        audio, song.master.fx, sr, song.meta.bpm, auto, "master", project, events, tracks
    )
    gain = auto.value("master.gain_db", np.arange(len(audio)), song.master.gain_db)
    audio *= np.expand_dims(np.power(10, np.asarray(gain) / 20), -1)
    if song.master.target_lufs is not None and np.max(np.abs(audio)) > 1e-9:
        try:
            import pyloudnorm as pyln
        except ImportError:
            _dependency("pyloudnorm")
        # BS.1770 meter needs >=400 ms, pad only for measurement of short previews.
        measured = np.pad(audio, ((0, max(0, math.ceil(sr * 0.4) - len(audio))), (0, 0)))
        loudness = pyln.Meter(sr).integrated_loudness(measured.astype(np.float64))
        if np.isfinite(loudness):
            audio *= 10 ** (min(60, song.master.target_lufs - loudness) / 20)
    ceilings = [fx.ceiling_db for fx in song.master.fx if fx.type == "limiter"]
    ceiling = min(ceilings, default=-1.0)
    audio = _limit(audio, sr, auto, "__safety", LimiterFx(ceiling_db=ceiling))
    # Native limiter's 4x sidechain does not strictly bound reconstruction.
    # Measure after limiting and apply a transparent corrective gain, if needed.
    peak = np.max(np.abs(resample_poly(audio.astype(np.float64), 4, 1, axis=0)))
    bound = 10 ** (ceiling / 20) - 2e-6  # PCM24 quantization reserve.
    if peak > bound:
        audio *= bound / peak
    return audio
