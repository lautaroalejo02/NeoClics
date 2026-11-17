"""Headless instruments and persistent, automation-friendly plugin effects."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ..errors import MuestraError
from .discovery import resolve_plugin
from .host import PluginHost
from .presets import CURATED, CURATED_PARAMS, CURATED_TRIM_DB, instrument_root, resolve_preset
from .sfz import render_sfz

if TYPE_CHECKING:
    from ..project.core import Project
    from ..schemas.song import Instrument, MidiNote, PluginFx

__all__ = ["resolve_plugin", "render_instrument", "build_plugin_fx"]


def instrument_spec(project, instrument):
    name, reference = instrument.plugin, instrument.preset
    curated = name in CURATED and name not in project.config.plugins.aliases
    if curated:
        name, default, _ = CURATED[name]
        reference = reference or default
        if name == "sfz" and instrument.preset is None:
            reference = str(instrument_root() / reference)
    if name == "sfz":
        if not reference:
            raise MuestraError.one(
                "preset_missing",
                "The SFZ instrument needs a preset path.",
                hint="Set preset to a .sfz file or use finger-bass/rhodes.",
            )
        return name, resolve_preset(project, reference), curated
    path = resolve_plugin(project, name)
    if path.suffix.lower() == ".sfz":
        return "sfz", path, curated
    preset = (
        resolve_preset(project, reference, surge="surge" in path.name.casefold())
        if reference
        else None
    )
    return path, preset, curated


def midi_events(notes, sample_rate, n_frames, bpm):
    """Frame-rounded MIDI with note-offs before note-ons at a shared timestamp."""
    events = []
    for note in notes:
        on = round((note.beat - 1) * 60 / bpm * sample_rate)
        off = max(on + 1, round((note.beat - 1 + note.length_beats) * 60 / bpm * sample_rate))
        if on >= n_frames:
            continue
        events.extend(
            [
                (on, 1, [0x90, note.pitch, note.velocity]),
                (min(off, n_frames), 0, [0x80, note.pitch, 0]),
            ]
        )
    return [(message, frame / sample_rate) for frame, _, message in sorted(events)]


def render_instrument(
    project: Project,
    instrument: Instrument,
    notes: list[MidiNote],
    sample_rate: int,
    n_frames: int,
    *,
    bpm: float,
) -> np.ndarray:
    """Render project-beat notes, release tails included, to exactly (n_frames, 2).

    Pedalboard compensates reported plugin latency internally. Do not trim it twice.
    The renderer owns track windows, final truncation fades and MIDI-file decoding.
    """
    if not np.isfinite(bpm) or bpm <= 0 or sample_rate <= 0 or n_frames < 0:
        raise MuestraError.one(
            "invalid_value",
            "Positive bpm/sample_rate and nonnegative frames required.",
            hint="Pass meta.bpm, project sample_rate and the render frame count.",
        )
    if not notes or n_frames == 0:
        return np.zeros((n_frames, 2), dtype=np.float32)
    path, preset, curated = instrument_spec(project, instrument)
    if path == "sfz":
        from ..project.validate import load_song

        seed = load_song(project).meta.seed
        audio = render_sfz(
            preset, notes, sample_rate, n_frames, bpm=bpm, seed=seed, params=instrument.params
        )
    else:
        params = (
            {**CURATED_PARAMS[instrument.plugin], **instrument.params}
            if curated
            else instrument.params
        )
        with PluginHost(path, preset, params, deterministic=curated) as host:
            audio = host.render(
                midi_events(notes, sample_rate, n_frames, bpm), sample_rate, n_frames
            )
    if curated and instrument.preset in (None, CURATED[instrument.plugin][1]):
        trim = CURATED_TRIM_DB.get(instrument.plugin, 0.0)
        audio = np.asarray(audio, dtype=np.float32) * np.float32(10 ** (trim / 20))
    if audio.shape != (n_frames, 2) or not np.isfinite(audio).all():
        raise MuestraError.one(
            "plugin_audio",
            f"Invalid audio returned: shape {audio.shape}.",
            hint="Choose another plugin/preset; check its reported latency.",
        )
    return np.ascontiguousarray(audio, dtype=np.float32)


class PluginEffect:
    """Pedalboard process interface; native parameter attributes and set_parameters.

    Owns a worker until close() or context-manager exit. Calls on one instance must
    be sequential, like a native plugin. Audio orientation follows Pedalboard.
    """

    def __init__(self, host, params):
        object.__setattr__(self, "_host", host)
        object.__setattr__(self, "_params", dict(params))

    @property
    def parameters(self):
        return tuple(self._host.info["parameters"])

    @property
    def reported_latency_samples(self):
        return self._host.info["latency_samples"]

    def set_parameters(self, params):
        for name, value in params.items():
            if name not in self.parameters or not np.isfinite(value):
                raise MuestraError.one(
                    "plugin_parameter",
                    f"Unknown or invalid parameter {name!r}.",
                    path=f"params.{name}",
                    hint="Use plugins.scan native parameters.",
                )
        self._params.update(params)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            self.set_parameters({name: value})

    def __getattr__(self, name):
        if name in self._params:
            return self._params[name]
        raise AttributeError(name)

    def process(self, audio, sample_rate, buffer_size=8192, reset=False):
        return self._host.effect(
            audio, sample_rate, reset=reset, params=self._params, buffer_size=buffer_size
        )

    __call__ = process

    def close(self):
        self._host.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def build_plugin_fx(project: Project, fx: PluginFx) -> object:
    """Build an isolated effect with process(audio, sample_rate, reset=False)."""
    path = resolve_plugin(project, fx.plugin)
    preset = resolve_preset(project, fx.preset) if fx.preset else None
    host = PluginHost(path, preset, fx.params)
    if not host.info["is_effect"]:
        host.close()
        raise MuestraError.one(
            "plugin_kind",
            f"{path.name} is not an effect.",
            hint="Select a third-party effect from plugins.scan.",
        )
    return PluginEffect(host, fx.params)
