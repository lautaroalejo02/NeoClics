"""Shared fixtures for the library tests: a synthesised sample library with
KNOWN properties (no copyrighted audio, nothing from the real library)."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

SR = 44100


def _t(dur: float) -> np.ndarray:
    return np.arange(int(dur * SR)) / SR


def kick(f0: float = 60.0, decay_s: float = 0.08, dur: float = 0.5, peak: float = 0.9):
    t = _t(dur)
    return np.sin(2 * np.pi * f0 * t) * np.exp(-t / decay_s) * peak


def hat(decay_s: float = 0.02, dur: float = 0.2, seed: int = 1, peak: float = 0.5):
    rng = np.random.default_rng(seed)
    t = _t(dur)
    noise = rng.standard_normal(t.size) * np.exp(-t / decay_s)
    hp = np.diff(np.concatenate([[0.0], noise]))  # crude high-pass: bright noise
    return hp / np.abs(hp).max() * peak


def snare(dur: float = 0.3, seed: int = 2):
    rng = np.random.default_rng(seed)
    t = _t(dur)
    body = np.sin(2 * np.pi * 190 * t) * np.exp(-t / 0.06)
    noise = rng.standard_normal(t.size) * np.exp(-t / 0.09) * 0.7
    x = body + noise
    return x / np.abs(x).max() * 0.8


def click_loop(bpm: float = 120.0, bars: int = 4, sr: int = SR):
    beat = 60.0 / bpm
    n = int(bars * 4 * beat * sr)
    x = np.zeros(n)
    for i in range(bars * 4):
        s = int(i * beat * sr)
        c = np.hanning(220) * np.sin(2 * np.pi * 1500 * np.arange(220) / sr)
        x[s : s + 220] += c * (1.0 if i % 4 == 0 else 0.6)
    return x * 0.8


def chord_loop(root_hz: float = 220.0, minor: bool = True, dur: float = 4.0):
    """A-minor-ish triad pad: A C E (minor) held."""
    t = _t(dur)
    third = 1.189207 if minor else 1.259921
    x = sum(np.sin(2 * np.pi * root_hz * m * t) for m in (1.0, third, 1.498307))
    x += 0.5 * sum(np.sin(2 * np.pi * root_hz * 2 * m * t) for m in (1.0, third, 1.498307))
    return x / np.abs(x).max() * 0.5


def write(path: Path, data: np.ndarray, subtype: str = "PCM_24", sr: int = SR) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, data, sr, subtype=subtype)
    return path


@pytest.fixture(autouse=True)
def _hermetic_discovery(monkeypatch, tmp_path):
    """Keep the machine's real library out of every test."""
    monkeypatch.setenv("MUESTRA_LIBRARY", "none")
    monkeypatch.setattr(
        "muestra.library.discovery.default_library_path", lambda: tmp_path / "no-default-library"
    )


def _build_library(root: Path) -> Path:
    """A small library laid out like the real one: kits/<kit>/<file>.wav."""
    k1 = root / "kits" / "machine-a"
    k2 = root / "kits" / "acoustic-b"
    write(k1 / "kick-m-kick-01.wav", kick(60.0, 0.08))
    write(k1 / "kick-m-kick-02.wav", kick(52.0, 0.30, dur=0.9))  # longer, lower
    write(k1 / "chat-m-closed-hat-01.wav", hat(0.012, 0.15))
    write(k1 / "ohat-m-open-hat-01.wav", hat(0.18, 0.6))
    write(k1 / "snare-m-snare-01.wav", snare())
    write(k2 / "kick-ac-kick-01.wav", kick(80.0, 0.05, dur=0.4, peak=0.5), subtype="PCM_16")
    write(k2 / "snare-ac-snare-01.wav", snare(0.4, seed=5), subtype="PCM_16")
    # a clipped kick (hard-clipped, long runs at full scale)
    write(k2 / "kick-ac-clipped-01.wav", np.clip(kick(60.0, 0.1) * 3.0, -1.0, 1.0))
    # a mislabelled file: named kick, but it is a bright hat
    write(k2 / "kick-ac-liar-01.wav", hat(0.015, 0.2, seed=9))
    # loops with BPM in the name
    write(k1 / "loops" / "misc-m-beat-120-01.wav", click_loop(120.0, 4))
    write(k1 / "loops" / "misc-m-beat-128-01.wav", click_loop(128.0, 4))
    # a loop whose NAME lies about the tempo (named 100, plays 120)
    write(k1 / "loops" / "misc-m-beat-100-02.wav", click_loop(120.0, 4))
    # tonal loops in known keys
    write(k1 / "instruments" / "misc-m-pad-a-120-a.wav", chord_loop(220.0, True, 8.0))
    (k1 / "LICENSE.txt").write_text(
        "SOURCE PACK: Test Pack A\nSOURCE URL: https://example.test/a\n\n"
        'LICENCE (verbatim):\n"Royalty-free in your music. You may not re-distribute them."\n\n'
        "INTERPRETATION:\n- Commercial use in music: YES.\n- Redistribution: NO.\n",
        encoding="utf-8",
    )
    (k2 / "LICENSE.txt").write_text(
        'SOURCE PACK: Test Pack B\n\nLICENCE:\n"Use freely. You may not re-distribute them."\n'
        'ADDITIONAL RESTRICTION: "I do not permit use of these samples for machine learning '
        'or any AI-related process."\n\nINTERPRETATION:\n- Use in AI/ML training: NO.\n',
        encoding="utf-8",
    )
    return root


@pytest.fixture(scope="session")
def _template(tmp_path_factory) -> Path:
    """The synthetic library, built once (un-indexed)."""
    return _build_library(tmp_path_factory.mktemp("template") / "MuestraLibrary")


@pytest.fixture(scope="session")
def _indexed_template(_template, tmp_path_factory) -> Path:
    """Same library with its index, measured once."""
    from muestra.library.index import index_root

    root = tmp_path_factory.mktemp("template-indexed") / "MuestraLibrary"
    shutil.copytree(_template, root)  # copy2 keeps size + mtime, so the index stays valid
    index_root(root, workers=1)
    return root


@pytest.fixture
def lib(_template, tmp_path) -> Path:
    """A fresh, un-indexed copy of the synthetic library (safe to mutate)."""
    root = tmp_path / "MuestraLibrary"
    shutil.copytree(_template, root)
    return root


@pytest.fixture
def indexed(lib, _indexed_template) -> Path:
    """`lib` plus the pre-measured index (identical content; nothing re-probed)."""
    shutil.copy2(_indexed_template / ".muestra-index.json", lib / ".muestra-index.json")
    return lib


@pytest.fixture
def project(tmp_path, lib):
    """A project whose [library] roots point at the synthetic library."""
    from muestra.project import create_project
    from muestra.schemas import LibraryConfig, ProjectConfig

    cfg = ProjectConfig(library=LibraryConfig(roots=[lib.as_posix()]))
    return create_project(tmp_path / "proj", cfg)
