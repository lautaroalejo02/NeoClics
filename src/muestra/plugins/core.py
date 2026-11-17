"""Plugin hosting seam (WP3 owns the implementation).

Plugin resolution comes from ``muestra.toml`` ``[plugins]``:

* ``search_dirs`` — extra directories scanned for VST3/AU plugins.
* alias tables — ``surge-xt = {windows = "...", macos = "..."}`` maps a
  name usable in song.yaml (``instrument.plugin``, ``fx plugin``) to a
  per-OS install path.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ..project.core import Project
    from ..schemas.song import Instrument, MidiNote, PluginFx

__all__ = ["resolve_plugin", "render_instrument", "build_plugin_fx"]


def resolve_plugin(project: Project, name: str) -> Path:
    """Resolve a plugin name/alias to its install path for this OS.

    Looks in ``muestra.toml`` ``[plugins]`` alias tables first, then
    scans ``search_dirs`` (and OS defaults). Raises MuestraError with a
    hint when the plugin can't be found.
    """
    raise NotImplementedError("WP3")


def render_instrument(
    project: Project,
    instrument: Instrument,
    notes: list[MidiNote],
    sample_rate: int,
    n_frames: int,
) -> np.ndarray:
    """Render ``notes`` through ``instrument`` to float32 stereo.

    Returns ``np.ndarray`` of dtype float32, shape ``(n_frames, 2)``.
    ``notes`` carry project-beat positions (1-indexed from song start),
    MIDI pitch 0-127 and velocity 1-127; the caller converts to frames
    at ``meta.bpm``.
    """
    raise NotImplementedError("WP3")


def build_plugin_fx(project: Project, fx: PluginFx) -> object:
    """Instantiate a hosted plugin fx.

    Returns an object with a pedalboard-compatible
    ``process(audio, sample_rate, reset=False)`` method, so the
    renderer can insert it into a track's fx chain like any
    pedalboard plugin.
    """
    raise NotImplementedError("WP3")
