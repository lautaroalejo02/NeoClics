from pathlib import Path

import pytest

from muestra.errors import MuestraError
from muestra.plugins import discovery
from muestra.plugins.discovery import discover, resolve_plugin
from muestra.plugins.presets import resolve_preset
from muestra.plugins.tools import PluginsListParams, PluginsScanParams, plugins_list, plugins_scan
from muestra.schemas.config import PluginPaths


def test_per_os_alias_and_relative_search(project, monkeypatch):
    monkeypatch.setattr(discovery, "default_search_dirs", lambda: [])
    path = project.root / "VST3/Vendor/Studio.vst3"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"dummy")
    project.config.plugins.search_dirs.append("VST3")
    project.config.plugins.aliases["studio"] = PluginPaths(**{discovery.platform_key(): str(path)})
    assert resolve_plugin(project, "studio") == path
    assert resolve_plugin(project, "Studio") == path
    assert discover(project) == [path]


def test_alias_missing_does_not_silently_substitute(project):
    project.config.plugins.aliases["surge-xt"] = PluginPaths(
        windows="missing", macos="missing", linux="missing"
    )
    with pytest.raises(MuestraError) as exc:
        resolve_plugin(project, "surge-xt")
    assert exc.value.issues[0].code == "plugin_missing" and exc.value.hint


def test_windows_bundle_binary(project, monkeypatch):
    monkeypatch.setattr(discovery.sys, "platform", "win32")
    path = project.root / "Synth.vst3/Contents/x86_64-win/Synth.vst3"
    path.parent.mkdir(parents=True)
    path.touch()
    assert discovery.plugin_binary(project.root / "Synth.vst3") == path


def test_macos_defaults(monkeypatch):
    monkeypatch.setattr(discovery.sys, "platform", "darwin")
    assert Path("/Library/Audio/Plug-Ins/VST3") in discovery.default_search_dirs()
    assert any(p.suffix == "" and p.name == "Components" for p in discovery.default_search_dirs())


def test_missing_preset_and_inventory_without_plugins(project, monkeypatch):
    monkeypatch.setattr(discovery, "default_search_dirs", lambda: [])
    with pytest.raises(MuestraError) as exc:
        resolve_preset(project, "missing.vstpreset")
    assert exc.value.issues[0].code == "preset_missing"
    result = plugins_list(PluginsListParams(project=str(project.root)))
    assert not result.plugins
    assert {p.alias for p in result.aliases} >= {
        "finger-bass",
        "synth-bass",
        "rhodes",
        "brass-stab",
        "pad",
    }
    scan = plugins_scan(PluginsScanParams(project=str(project.root), names=["does-not-exist"]))
    assert scan.plugins[0].errors[0].code == "plugin_missing"
    assert project.path(scan.files[0]).is_file()
