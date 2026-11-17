"""WP13 finding 7: one ffmpeg resolver; 'cannot decode' is not 'ffmpeg missing'."""

from __future__ import annotations

import subprocess

import numpy as np
import pytest
import soundfile as sf

from muestra import media
from muestra.errors import MuestraError
from muestra.render.audio import decode


@pytest.fixture
def no_ffmpeg(monkeypatch):
    monkeypatch.delenv("MUESTRA_FFMPEG", raising=False)
    monkeypatch.setattr("muestra.media.shutil.which", lambda _: None)
    monkeypatch.setattr("muestra.media.known_ffmpeg_locations", lambda: [])


def test_render_decode_honours_muestra_ffmpeg(project, tmp_path, monkeypatch, no_ffmpeg):
    encoded = np.full((2205, 2), 0.25, "<f4").tobytes()
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=encoded)

    monkeypatch.setenv("MUESTRA_FFMPEG", "C:/tools/ffmpeg.exe")
    monkeypatch.setattr("muestra.media.subprocess.run", run)
    path = tmp_path / "clip.m4a"
    path.write_bytes(b"\x00\x00\x00\x20ftypM4A ")
    audio, _ = decode(project, path, 22050)
    assert calls[0][0] == "C:/tools/ffmpeg.exe" and str(path) in calls[0]
    assert audio.shape == (2205, 2)


def test_corrupt_wav_is_cannot_decode_not_ffmpeg_missing(project, tmp_path, no_ffmpeg):
    path = tmp_path / "broken.wav"
    path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEjunk")
    with pytest.raises(MuestraError) as exc:
        decode(project, path, 22050)
    assert exc.value.issues[0].code == "decode_error"
    assert "damaged or is not audio" in exc.value.issues[0].message
    assert "ffmpeg" not in exc.value.hint


def test_m4a_without_ffmpeg_says_ffmpeg_is_needed(project, tmp_path, no_ffmpeg):
    path = tmp_path / "clip.m4a"
    path.write_bytes(b"\x00\x00\x00\x20ftypM4A ")
    with pytest.raises(MuestraError) as exc:
        decode(project, path, 22050)
    assert exc.value.issues[0].code == "ffmpeg_missing"
    assert "clip.m4a" in exc.value.issues[0].message and "MUESTRA_FFMPEG" in exc.value.hint


def test_resolver_order_and_install_locations(monkeypatch):
    monkeypatch.setenv("MUESTRA_FFMPEG", "env-ffmpeg")
    assert media.find_ffmpeg("override") == "override"
    assert media.find_ffmpeg() == "env-ffmpeg"
    monkeypatch.delenv("MUESTRA_FFMPEG")
    monkeypatch.setattr("muestra.media.shutil.which", lambda _: None)
    monkeypatch.setattr("muestra.media.known_ffmpeg_locations", lambda: ["C:/winget/ffmpeg.exe"])
    assert media.find_ffmpeg() == "C:/winget/ffmpeg.exe"  # PATH stale after winget
    assert media.find_ffmpeg(known_locations=False) is None


def test_wav_still_decodes_natively(project, tmp_path, no_ffmpeg):
    path = tmp_path / "ok.wav"
    sf.write(path, np.zeros((100, 2)) + 0.1, 22050)
    assert decode(project, path, 22050)[0].shape == (100, 2)
