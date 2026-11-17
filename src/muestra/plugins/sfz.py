"""Strict, offline SFZ subset for recorded instruments, without a native plugin.

Supported opcodes are deliberately finite: unsupported instructions fail loudly.
Pitch conversion uses SciPy's polyphase sinc resampler, never linear interpolation.
"""

from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from ..errors import MuestraError
from ..music import parse_note_name

SUPPORTED = {
    "sample",
    "key",
    "lokey",
    "hikey",
    "lovel",
    "hivel",
    "pitch_keycenter",
    "pitch_keytrack",
    "tune",
    "transpose",
    "volume",
    "pan",
    "amp_veltrack",
    "ampeg_attack",
    "ampeg_hold",
    "ampeg_decay",
    "ampeg_sustain",
    "ampeg_release",
    "loop_mode",
    "loop_start",
    "loop_end",
    "offset",
    "end",
    "seq_length",
    "seq_position",
    "lorand",
    "hirand",
}
_TOKEN = re.compile(
    r"<([^>]+)>|([a-zA-Z_][\w]*)\s*=\s*(.*?)(?=\s+[a-zA-Z_]\w*\s*=|\s*<|$)", re.DOTALL
)


def fail(message, path="preset", code="sfz_invalid"):
    return MuestraError.one(
        code,
        message,
        path=path,
        hint="Use the supported SFZ subset in docs/instruments.md or sfizz.",
    )


def key(value):
    try:
        return int(value)
    except ValueError:
        return parse_note_name(value)


def parse_sfz(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise fail(str(e), code="preset_missing") from e
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    if "#" in re.sub(r"[a-gA-G]#[0-9-]+", "", text):
        raise fail("SFZ preprocessor directives are not supported.")
    global_values, group, region, control = {}, {}, None, {}
    regions = []
    section = None
    cursor = 0
    for token in _TOKEN.finditer(text):
        if text[cursor : token.start()].strip():
            raise fail(f"Unparsed SFZ text: {text[cursor : token.start()][:80]}")
        cursor = token.end()
        header, opcode, value = token.groups()
        if header:
            section = header
            if header == "global":
                global_values = {}
            elif header == "group":
                group = {}
            elif header == "region":
                region = {
                    **global_values,
                    **group,
                    "_default_path": control.get("default_path", ""),
                }
                regions.append(region)
            elif header != "control":
                raise fail(f"Unsupported SFZ section <{header}>.")
            continue
        value = value.strip().strip('"')
        if section == "control" and opcode == "default_path":
            control[opcode] = value
            continue
        if opcode not in SUPPORTED:
            raise fail(
                f"Unsupported SFZ opcode {opcode!r}.",
                path=f"preset.{opcode}",
                code="sfz_unsupported",
            )
        target = {"global": global_values, "group": group, "region": region}.get(section)
        if target is None:
            raise fail("SFZ opcode appears before a supported section.")
        target[opcode] = value
    if text[cursor:].strip() or not regions:
        raise fail("SFZ contains no regions or has unparsed trailing text.")
    for r in regions:
        if "sample" not in r:
            raise fail("Every SFZ region must name a recorded sample.")
        if "key" in r:
            r.setdefault("lokey", r["key"])
            r.setdefault("hikey", r["key"])
            r.setdefault("pitch_keycenter", r["key"])
        for name, default in [("lokey", 0), ("hikey", 127), ("pitch_keycenter", 60)]:
            r[name] = key(str(r.get(name, default)))
        for name, default in [("lovel", 0), ("hivel", 127), ("seq_length", 1), ("seq_position", 1)]:
            r[name] = int(r.get(name, default))
        if not (
            0 <= r["lokey"] <= r["hikey"] <= 127
            and 0 <= r["lovel"] <= r["hivel"] <= 127
            and 1 <= r["seq_position"] <= r["seq_length"]
        ):
            raise fail("Invalid SFZ key/velocity/round-robin region bounds.")
        if r.get("loop_mode", "no_loop") not in {
            "no_loop",
            "one_shot",
            "loop_continuous",
            "loop_sustain",
        }:
            raise fail("Unsupported loop_mode.")
        for name in ["ampeg_attack", "ampeg_hold", "ampeg_decay", "ampeg_release"]:
            if float(r.get(name, 0)) < 0:
                raise fail(f"{name} must be nonnegative.")
        for name, value in r.items():
            if name not in {"sample", "_default_path", "loop_mode"} and not np.isfinite(
                float(value)
            ):
                raise fail(f"{name} must be finite.")
    return regions


def envelope(r, frames, gate, sample_rate):
    t = np.arange(frames, dtype=np.float64) / sample_rate
    attack = float(r.get("ampeg_attack", 0.001))
    hold = float(r.get("ampeg_hold", 0))
    decay = float(r.get("ampeg_decay", 0))
    sustain = float(r.get("ampeg_sustain", 100)) / 100
    if not 0 <= sustain <= 1:
        raise fail("ampeg_sustain must be a percentage in 0..100.")
    env = np.ones(frames)
    if attack:
        env = np.minimum(t / attack, 1)
    if decay:
        env *= np.where(
            t <= attack + hold,
            1,
            sustain + (1 - sustain) * np.maximum(0, 1 - (t - attack - hold) / decay),
        )
    else:
        env[t >= attack + hold] *= sustain
    if gate < frames:
        release = max(0, round(float(r.get("ampeg_release", 0.1)) * sample_rate))
        level = env[max(0, gate - 1)] if gate else 0
        env[gate:] = level * np.maximum(0, 1 - np.arange(frames - gate) / max(1, release))
        env[gate + release :] = 0
    return env


def render_sfz(path, notes, sample_rate, n_frames, *, bpm, seed=0, params=None):
    params = params or {}
    unknown = set(params) - {"gain_db"}
    if unknown:
        raise fail(f"Unknown SFZ params: {sorted(unknown)}. Only gain_db is supported.")
    try:
        regions = parse_sfz(path)
        output = np.zeros((n_frames, 2), dtype=np.float64)
        rng = np.random.default_rng(seed)
        # A seed selects the initial RR position; later hits cycle without random repeats.
        cycles = {}
        samples = {}
        pitched = {}
        for note in sorted(notes, key=lambda n: n.beat):
            start = round((note.beat - 1) * 60 / bpm * sample_rate)
            gate = max(1, round(note.length_beats * 60 / bpm * sample_rate))
            if start >= n_frames:
                continue
            random_value = rng.random()
            selected = [
                r
                for r in regions
                if r["lokey"] <= note.pitch <= r["hikey"]
                and r["lovel"] <= note.velocity <= r["hivel"]
                and float(r.get("lorand", 0)) <= random_value < float(r.get("hirand", 1))
            ]
            rr_positions = {}
            for r in selected:
                rrkey = (r["lokey"], r["hikey"], r["lovel"], r["hivel"], r["seq_length"])
                if rrkey not in rr_positions:
                    cycles.setdefault(rrkey, int(rng.integers(r["seq_length"])))
                    rr_positions[rrkey] = cycles[rrkey] % r["seq_length"] + 1
                    cycles[rrkey] += 1
                if r["seq_position"] != rr_positions[rrkey]:
                    continue
                sample_path = (
                    path.parent
                    / r["_default_path"].replace("\\", "/")
                    / r["sample"].replace("\\", "/")
                )
                if sample_path not in samples:
                    samples[sample_path] = sf.read(sample_path, dtype="float32", always_2d=True)
                data, source_rate = samples[sample_path]
                if data.shape[1] not in {1, 2}:
                    raise fail("SFZ samples must be mono or stereo.")
                ratio = 2 ** (
                    (
                        (note.pitch - r["pitch_keycenter"])
                        * float(r.get("pitch_keytrack", 100))
                        / 100
                        + float(r.get("transpose", 0))
                        + float(r.get("tune", 0)) / 100
                    )
                    / 12
                )
                mode = r.get("loop_mode", "no_loop")
                release = round(float(r.get("ampeg_release", 0.1)) * sample_rate)
                frames = min(n_frames - start, gate + release)
                if mode == "one_shot":
                    frames = min(
                        n_frames - start, round(len(data) * sample_rate / source_rate / ratio)
                    )
                cachekey = (
                    sample_path,
                    ratio,
                    mode,
                    frames if mode.startswith("loop") else 0,
                    gate if mode == "loop_sustain" else 0,
                    r.get("offset"),
                    r.get("end"),
                    r.get("loop_start"),
                    r.get("loop_end"),
                )
                if cachekey not in pitched:
                    offset = int(r.get("offset", 0))
                    end = int(r.get("end", len(data) - 1))
                    if not 0 <= offset <= end < len(data):
                        raise fail("SFZ sample offset/end are outside the sample.")
                    source = data[offset : end + 1]
                    if mode.startswith("loop"):
                        if "loop_start" not in r or "loop_end" not in r:
                            raise fail("Looped regions require explicit loop_start and loop_end.")
                        lo, hi = int(r["loop_start"]) - offset, int(r["loop_end"]) - offset
                        if not 0 <= lo < hi < len(source):
                            raise fail("Invalid SFZ loop points (end is inclusive).")
                        length = hi - lo + 1
                        required = int(np.ceil(frames * source_rate * ratio / sample_rate)) + 64
                        indices = np.arange(required)
                        looped = np.where(indices > hi, lo + (indices - lo) % length, indices)
                        if mode == "loop_sustain":
                            off = int(np.ceil(gate * source_rate * ratio / sample_rate))
                            if off < required:
                                looped[off:] = (
                                    looped[max(0, off - 1)] + 1 + np.arange(required - off)
                                )
                        valid = looped < len(source)
                        extended = np.zeros((required, data.shape[1]), dtype=np.float32)
                        extended[valid] = source[looped[valid]]
                        source = extended
                    fraction = Fraction(sample_rate / (source_rate * ratio)).limit_denominator(4096)
                    pitched[cachekey] = resample_poly(
                        source,
                        fraction.numerator,
                        fraction.denominator,
                        axis=0,
                        window=("kaiser", 12.0),
                    )
                voice = pitched[cachekey][:frames].astype(np.float64)
                if voice.shape[1] == 1:
                    voice = np.repeat(voice, 2, axis=1)
                gain = 10 ** ((float(r.get("volume", 0)) + params.get("gain_db", 0)) / 20)
                gain *= (note.velocity / 127) ** (2 * float(r.get("amp_veltrack", 100)) / 100)
                pan = float(r.get("pan", 0)) / 100
                if not -1 <= pan <= 1:
                    raise fail("SFZ pan must be in -100..100.")
                voice *= np.array([min(1, 1 - pan), min(1, 1 + pan)]) * gain
                voice *= envelope(
                    r, len(voice), len(voice) if mode == "one_shot" else gate, sample_rate
                )[:, None]
                output[start : start + len(voice)] += voice
        return output.astype(np.float32)
    except MuestraError:
        raise
    except (OSError, ValueError, RuntimeError, OverflowError) as e:
        raise fail(str(e)) from e
