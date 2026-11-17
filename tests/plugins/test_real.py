import subprocess
import sys

import numpy as np
import pytest

from muestra.plugins.core import build_plugin_fx, render_instrument
from muestra.plugins.discovery import resolve_plugin
from muestra.plugins.host import PluginHost
from muestra.plugins.presets import CURATED, instrument_root, resolve_preset
from muestra.plugins.sfz import render_sfz
from muestra.plugins.tools import PluginsScanParams, plugins_scan
from muestra.schemas.song import Instrument, MidiNote, PluginFx

from .measure import native_measurements, sample_measurements


@pytest.fixture(scope="module")
def measurements(surge):
    return native_measurements(surge)


@pytest.mark.real_plugin
@pytest.mark.parametrize("alias", ["init-sine", "slap-bass", "synth-bass", "brass-stab", "pad"])
def test_native_audio_quality_and_repeatability(measurements, alias):
    result = measurements[alias]
    audio = result["audio"]
    assert audio["frames"] == 960000 and audio["dtype"] == "float32"
    assert -50 < audio["peak_dbfs"] < 0
    assert result["pre_grid_dbfs"] < -140
    assert audio["tail_dbfs"] < -100
    # The brass has deliberate unison detuning. Its loudest partial lies
    # within 25 cents; other selected patches are within 5 cents.
    cents = 25 if alias == "brass-stab" else 5
    assert abs(audio["A2"]["cents"]) < cents
    assert abs(audio["A4"]["cents"]) < cents
    assert result["same_process"]["rms_dbfs"] < -120
    assert result["same_process"]["peak_dbfs"] < -100
    assert result["separate_processes"]["bit_exact"]


@pytest.mark.real_plugin
def test_public_instrument_seam_is_bit_exact(project, surge):
    notes = [MidiNote(beat=2, pitch=45, length_beats=0.5)]
    instrument = Instrument(plugin="slap-bass")
    a = render_instrument(project, instrument, notes, 48000, 144000, bpm=90)
    b = render_instrument(project, instrument, notes, 48000, 144000, bpm=90)
    assert np.array_equal(a, b)
    assert np.max(np.abs(a)) > 0.01
    onset = round(48000 * 60 / 90)
    assert not np.any(a[:onset])
    assert np.flatnonzero(np.abs(a[:, 0]) > 1e-5)[0] - onset < 128
    assert np.max(np.abs(a[-24000:])) < 1e-6


@pytest.mark.real_plugin
def test_native_params_after_factory_patch(project, surge):
    preset = resolve_preset(project, "Init Sine", surge=True)
    events = [([144, 69, 100], 0.1), ([128, 69, 0], 0.6)]
    with PluginHost(surge, preset, {"global_volume": -12}) as host:
        a = host.render(events, 48000, 96000)
        host.request(
            op="preset", preset=str(preset), params={"global_volume": -24}, deterministic=False
        )
        b = host.render(events, 48000, 96000)
        assert np.max(np.abs(a)) / np.max(np.abs(b)) == pytest.approx(10 ** (12 / 20), rel=0.005)
        with pytest.raises(Exception, match="Unknown parameter"):
            host.request(
                op="preset", preset=str(preset), params={"does_not_exist": 1}, deterministic=False
            )


@pytest.mark.real_plugin
def test_native_effect_automation_and_no_audio_device(project, surge):
    path = resolve_plugin(project, "Surge XT Effects")
    # The default delay is an actual third-party effect, hosted by the same
    # persistent process while the wet/dry control changes between chunks.
    fx = PluginFx(plugin=str(path), params={"output_mix": 0})
    with build_plugin_fx(project, fx) as effect:
        audio = np.zeros((2, 24000), dtype=np.float32)
        audio[:, 100] = 0.1
        a = effect.process(audio, 48000, reset=True)
        assert a.shape == audio.shape and np.isfinite(a).all()
        assert np.max(np.abs(a)) > 0.01
        effect.output_mix = 0.5
        b = effect.process(np.zeros_like(audio), 48000, reset=False)
        assert b.shape == audio.shape and np.isfinite(b).all()


@pytest.mark.real_plugin
@pytest.mark.parametrize("alias", ["brass-stab", "pad"])
def test_curated_seventh_chord_has_headroom(project, surge, alias):
    notes = [MidiNote(beat=1, pitch=pitch, length_beats=3) for pitch in [60, 64, 67, 71]]
    audio = render_instrument(project, Instrument(plugin=alias), notes, 48000, 480000, bpm=120)
    assert 0.01 < np.max(np.abs(audio)) < 0.9


@pytest.mark.real_plugin
def test_real_scan_reports_native_parameter_ranges(project, surge):
    result = plugins_scan(
        PluginsScanParams(
            project=str(project.root), names=[str(surge)], parameters=["global_volume"]
        )
    )
    entry = result.plugins[0]
    assert entry.status == "ready" and entry.is_instrument
    assert entry.parameter_info["global_volume"]["min_value"] == -48
    assert entry.parameter_info["global_volume"]["max_value"] == 0
    assert project.path(result.files[0]).is_file()


@pytest.mark.real_samples
@pytest.mark.parametrize("alias", ["finger-bass", "rhodes"])
def test_recorded_samples_and_cross_process_determinism(project, alias):
    path = instrument_root() / CURATED[alias][1]
    if not path.is_file():
        pytest.skip(
            "Optional recorded samples absent; run python -m muestra.plugins.install_samples."
        )
    report = sample_measurements()[alias]
    assert report["same_process"]["bit_exact"]
    assert report["audio"]["peak_dbfs"] < -6
    assert abs(report["audio"]["A2"]["cents"]) < 10
    assert abs(report["audio"]["A4"]["cents"]) < 10
    notes = [MidiNote(beat=1, pitch=45, length_beats=1)]
    a = render_sfz(path, notes, 48000, 96000, bpm=120, seed=42)
    target = project.root / "separate.npy"
    code = (
        "from pathlib import Path;import numpy as np;"
        "from muestra.plugins.sfz import render_sfz;from muestra.schemas.song import MidiNote;"
        f"a=render_sfz(Path({str(path)!r}),[MidiNote(beat=1,pitch=45,length_beats=1)],"
        f"48000,96000,bpm=120,seed=42);np.save({str(target)!r},a)"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60)
    assert np.array_equal(a, np.load(target))
