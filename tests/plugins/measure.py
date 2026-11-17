"""Reproducible WP3 audio measurements; run with uv run python tests/plugins/measure.py."""

import json
import tempfile
import time
from pathlib import Path

import numpy as np

from muestra.plugins.core import midi_events
from muestra.plugins.discovery import resolve_plugin
from muestra.plugins.host import PluginHost
from muestra.plugins.presets import CURATED, instrument_root, resolve_preset
from muestra.plugins.sfz import render_sfz
from muestra.project.core import Project
from muestra.schemas.config import ProjectConfig
from muestra.schemas.song import MidiNote


def db(value):
    return float(20 * np.log10(max(1e-30, value)))


def difference(a, b):
    error = a.astype(np.float64) - b.astype(np.float64)
    return {
        "bit_exact": bool(np.array_equal(a, b)),
        "rms_dbfs": db(np.sqrt(np.mean(error**2))),
        "peak_dbfs": db(np.max(np.abs(error))),
    }


def spectrum(audio, rate):
    wave = audio.mean(axis=1)
    ft = np.abs(np.fft.rfft(wave * np.hanning(len(wave)), n=262144))
    frequencies = np.fft.rfftfreq(262144, 1 / rate)
    return ft, frequencies


def metrics(audio, rate=48000):
    result = {
        "peak_dbfs": db(np.max(np.abs(audio))),
        "rms_dbfs": db(np.sqrt(np.mean(audio.astype(np.float64) ** 2))),
        "tail_dbfs": db(np.max(np.abs(audio[-rate:]))),
        "frames": len(audio),
        "dtype": str(audio.dtype),
    }
    for label, start, hz in [("A2", 0.5, 110), ("A4", 3.5, 440)]:
        ft, frequencies = spectrum(audio[int(start * rate) : int((start + 0.5) * rate)], rate)
        # Estimate the fundamental near the expected pitch; electric instruments
        # often have stronger 2nd/3rd harmonics. Also report the strongest peak.
        mask = (frequencies > hz * 0.95) & (frequencies < hz * 1.05)
        peak = frequencies[np.flatnonzero(mask)[np.argmax(ft[mask])]]
        result[label] = {
            "fundamental_hz": float(peak),
            "cents": float(1200 * np.log2(peak / hz)),
            "strongest_hz": float(frequencies[np.argmax(ft)]),
        }
    ft, frequency = spectrum(audio[: rate * 5], rate)
    energies = ft**2
    total = max(1e-30, energies.sum())
    result["spectral_energy_fraction"] = {
        label: float(energies[(frequency >= lo) & (frequency < hi)].sum() / total)
        for label, lo, hi in [("low", 20, 250), ("mid", 250, 4000), ("high", 4000, 20000)]
    }
    return result


def native_measurements(path):
    project = Project(Path.cwd(), ProjectConfig())
    notes = [
        MidiNote(beat=1.5, pitch=45, length_beats=2),
        MidiNote(beat=7.5, pitch=69, length_beats=2),
    ]
    events = midi_events(notes, 48000, 960000, 120)
    results = {}
    t = time.perf_counter()
    with PluginHost(path) as first, PluginHost(path) as second:
        results["two_worker_startup_s"] = time.perf_counter() - t
        for alias, reference in [("init-sine", "Init Sine")] + [
            (a, p) for a, (backend, p, _) in CURATED.items() if backend == "surge-xt"
        ]:
            preset = resolve_preset(project, reference, surge=True)
            deterministic = alias != "init-sine"
            for host in [first, second]:
                host.info = host.request(
                    op="preset", preset=str(preset), params={}, deterministic=deterministic
                )
            a = first.render(events, 48000, 960000)
            b = first.render(events, 48000, 960000)
            c = second.render(events, 48000, 960000)
            result = {
                "same_process": difference(a, b),
                "separate_processes": difference(a, c),
                "audio": metrics(a),
                "latency_samples": first.info["latency_samples"],
                "pre_grid_dbfs": db(np.max(np.abs(a[:12000]))),
            }
            results[alias] = result
            print(alias, json.dumps(result), flush=True)
        preset = resolve_preset(project, CURATED["synth-bass"][1], surge=True)
        first.request(op="preset", preset=str(preset), params={}, deterministic=True)
        bass = [
            MidiNote(beat=i + 1, pitch=[33, 33, 40, 43][i % 4], length_beats=0.65)
            for i in range(64)
        ]
        t = time.perf_counter()
        first.render(midi_events(bass, 48000, 1536000, 120), 48000, 1536000)
        results["16_bar_bass_dsp_s"] = time.perf_counter() - t
    return results


def sample_measurements():
    results = {}
    notes = [
        MidiNote(beat=1.5, pitch=45, length_beats=2),
        MidiNote(beat=7.5, pitch=69, length_beats=2),
    ]
    bass = [
        MidiNote(beat=i + 1, pitch=[33, 33, 40, 43][i % 4], length_beats=0.65) for i in range(64)
    ]
    for alias in ["finger-bass", "rhodes"]:
        path = instrument_root() / CURATED[alias][1]
        if not path.is_file():
            continue
        a = render_sfz(path, notes, 48000, 960000, bpm=120, seed=42)
        b = render_sfz(path, notes, 48000, 960000, bpm=120, seed=42)
        results[alias] = {"audio": metrics(a), "same_process": difference(a, b)}
        if alias == "finger-bass":
            t = time.perf_counter()
            render_sfz(path, bass, 48000, 1536000, bpm=120, seed=42)
            results["16_bar_finger_bass_s"] = time.perf_counter() - t
        print(alias, json.dumps(results[alias]), flush=True)
    return results


if __name__ == "__main__":
    project = Project(Path.cwd(), ProjectConfig())
    report = {
        "native": native_measurements(resolve_plugin(project, "surge-xt")),
        "samples": sample_measurements(),
    }
    destination = Path(tempfile.gettempdir()) / "muestra-wp3-measurements.json"
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(destination, flush=True)
