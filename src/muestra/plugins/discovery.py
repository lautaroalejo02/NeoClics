"""Portable plugin discovery; discovery never executes third-party code."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from ..errors import MuestraError


def platform_key() -> str:
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")


def default_search_dirs() -> list[Path]:
    home = Path.home()
    if sys.platform == "win32":
        return [
            Path(os.environ.get("COMMONPROGRAMFILES", "C:/Program Files/Common Files")) / "VST3",
            Path(os.environ.get("LOCALAPPDATA", str(home / "AppData/Local")))
            / "Programs/Common/VST3",
            Path(os.environ.get("LOCALAPPDATA", str(home / "AppData/Local"))) / "VST3",
        ]
    if sys.platform == "darwin":
        return [
            Path("/Library/Audio/Plug-Ins/VST3"),
            home / "Library/Audio/Plug-Ins/VST3",
            Path("/Library/Audio/Plug-Ins/Components"),
            home / "Library/Audio/Plug-Ins/Components",
        ]
    return [home / ".vst3", Path("/usr/lib/vst3"), Path("/usr/local/lib/vst3")]


def absolute(project, value: str) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    return (path if path.is_absolute() else project.root / path).resolve()


def plugin_binary(path: Path) -> Path:
    """Pedalboard on Windows needs the binary inside a modern VST3 bundle."""
    if sys.platform == "win32" and path.is_dir() and path.suffix.lower() == ".vst3":
        binary = path / "Contents/x86_64-win" / path.name
        if binary.is_file():
            return binary
    return path


def discover(project) -> list[Path]:
    roots = [absolute(project, p) for p in project.config.plugins.search_dirs]
    roots += default_search_dirs()
    found: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for current, dirs, files in os.walk(root):
            for name in list(dirs):
                if Path(name).suffix.lower() in {".vst3", ".component"}:
                    found.add(plugin_binary(Path(current) / name).resolve())
                    dirs.remove(name)
            for name in files:
                if Path(name).suffix.lower() in {".vst3", ".sfz"}:
                    found.add((Path(current) / name).resolve())
    for entry in project.config.plugins.aliases.values():
        value = getattr(entry, platform_key())
        if value:
            path = absolute(project, value)
            if path.exists():
                found.add(plugin_binary(path))
    return sorted(found, key=lambda p: str(p).casefold())


def normalized(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def resolve_plugin(project, name: str) -> Path:
    """Resolve explicit aliases first, then paths and unambiguous installed names."""
    aliases = project.config.plugins.aliases
    if name in aliases:
        value = getattr(aliases[name], platform_key())
        if not value:
            raise MuestraError.one(
                "plugin_missing",
                f"Alias {name!r} has no {platform_key()} path.",
                path=f"plugins.aliases.{name}",
                hint="Add this OS's installed plugin path.",
            )
        path = absolute(project, value)
        if path.exists():
            return plugin_binary(path)
    else:
        path = absolute(project, name)
        if path.exists():
            return plugin_binary(path)
        matches = [p for p in discover(project) if normalized(p.stem) == normalized(name)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise MuestraError.one(
                "plugin_ambiguous",
                f"Multiple plugins match {name!r}: {matches}",
                path="instrument.plugin",
                hint="Set a per-OS alias to the exact plugin path.",
            )
    raise MuestraError.one(
        "plugin_missing",
        f"Plugin {name!r} is not installed at the configured location.",
        path="instrument.plugin",
        value=name,
        hint="Install the free plugin or fix [plugins] aliases/search_dirs; run plugins.list.",
    )
