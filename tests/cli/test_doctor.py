"""muestra doctor says exactly what is missing and the one command that fixes it."""

from __future__ import annotations

import importlib.util

from muestra.cli import doctor as doctor_module


def _by_name(report):
    return {c["name"]: c for c in report["checks"]}


def test_missing_separation_is_optional_with_one_fix(tmp_project, monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(
        doctor_module.importlib.util,
        "find_spec",
        lambda name, *a: None if name in ("demucs_infer", "audio_separator") else real(name, *a),
    )
    report = doctor_module.doctor(tmp_project.root)
    sep = _by_name(report)["python-separation"]
    assert sep["status"] == "optional"
    assert "demucs_infer" in sep["detail"] and "everything else works" in sep["detail"]
    assert sep["fix"].startswith("uv sync --extra separation")


def test_missing_core_package_fails_the_report(tmp_project, monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(
        doctor_module.importlib.util,
        "find_spec",
        lambda name, *a: None if name == "mcp" else real(name, *a),
    )
    report = doctor_module.doctor(tmp_project.root)
    core = _by_name(report)["python-core"]
    assert core["status"] == "missing" and not report["ok"]
    assert "mcp (MCP server for agents)" in core["detail"]
    assert core["fix"].startswith("uv sync ") and "--extra" not in core["fix"]


def test_ffmpeg_installed_but_not_on_path(tmp_project, monkeypatch):
    monkeypatch.delenv("MUESTRA_FFMPEG", raising=False)
    monkeypatch.setattr(
        doctor_module.shutil, "which", lambda name: None if name == "ffmpeg" else "x"
    )
    monkeypatch.setattr(doctor_module, "_ffmpeg_candidates", lambda: ["C:/ff/bin/ffmpeg.exe"])
    ffmpeg = _by_name(doctor_module.doctor(tmp_project.root))["ffmpeg"]
    assert ffmpeg["status"] == "optional"  # wav/flac/mp3 decode natively
    assert "not on this terminal's PATH" in ffmpeg["detail"]
    assert "MUESTRA_FFMPEG" in ffmpeg["fix"]


def test_plugin_aliases_use_the_real_resolver(tmp_project):
    """The fixture's surge-xt alias points at a path that doesn't exist."""
    plugin = _by_name(doctor_module.doctor(tmp_project.root))["plugin:surge-xt"]
    assert plugin["status"] == "missing" and plugin["fix"]
