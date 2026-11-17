"""Curated aliases and Surge's documented FXP component state format."""

from __future__ import annotations

import os
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from ..errors import MuestraError

# Alias -> (backend, factory patch or external sample map, reason).
CURATED = {
    "finger-bass": ("sfz", "swagbass/muestra-finger.sfz", "Recorded electric bass; four takes."),
    "slap-bass": ("surge-xt", "Basses/FM Slap", "Velocity-sensitive factory FM slap bass."),
    "synth-bass": ("surge-xt", "Basses/Attacky", "Punchy factory synth bass."),
    "rhodes": ("sfz", "jrhodes/muestra-rhodes.sfz", "Recorded 1977 Rhodes, five velocity layers."),
    "brass-stab": ("surge-xt", "Polysynths/Fonk", "Factory funk polysynth for chord stabs."),
    "pad": ("surge-xt", "Pads/MKS-70 Warm Pad", "Factory Roland-style warm pad."),
}


def instrument_root() -> Path:
    return Path.home() / "Documents/MuestraLibrary/instruments"


def surge_roots() -> list[Path]:
    if sys.platform == "win32":
        return [Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Surge XT"]
    if sys.platform == "darwin":
        return [
            Path("/Library/Application Support/Surge XT"),
            Path.home() / "Library/Application Support/Surge XT",
        ]
    return [Path("/usr/share/surge-xt"), Path.home() / ".local/share/surge-xt"]


def surge_presets() -> list[Path]:
    return sorted({p for root in surge_roots() for p in root.rglob("*.fxp")})


def resolve_preset(project, reference: str, *, surge: bool = False) -> Path:
    if reference.startswith("library://"):
        return project.resolve(reference)
    path = Path(reference).expanduser()
    if not path.is_absolute():
        path = project.root / path
    if path.is_file():
        return path.resolve()
    if surge:
        ref = reference.removesuffix(".fxp").replace("\\", "/").casefold()
        matches = [
            p
            for p in surge_presets()
            if p.stem.casefold() == ref
            or p.with_suffix("").as_posix().casefold().endswith("/" + ref)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise MuestraError.one(
                "preset_ambiguous",
                f"Preset {reference!r} matches multiple factory patches.",
                path="instrument.preset",
                hint="Use category/name or the full preset path.",
            )
    raise MuestraError.one(
        "preset_missing",
        f"Preset {reference!r} was not found.",
        path="instrument.preset",
        hint="Run plugins.list and select an available preset name or existing file path.",
    )


def surge_component(path: Path, deterministic: bool = False) -> bytes:
    data = path.read_bytes()
    if data[:4] != b"CcnK" or data[8:12] != b"FPCh" or data[60:64] != b"sub3":
        raise ValueError("Not a Surge XT chunk FXP patch")
    data = data[60:]
    if deterministic:
        size = struct.unpack_from("<I", data, 4)[0]
        xml = ET.fromstring(data[32 : 32 + size].rstrip(b"\0"))
        for element in xml.find("parameters"):
            if element.tag.endswith("_retrigger"):
                element.set("value", "1")
            if element.tag.endswith("_drift"):
                element.set("value", "0")
            if element.tag.endswith("_trigmode"):
                element.set("value", "0")
        replacement = ET.tostring(xml, encoding="utf-8", xml_declaration=True)
        data = (
            data[:4]
            + struct.pack("<I", len(replacement))
            + data[8:32]
            + replacement
            + data[32 + size :]
        )
    return data


def load_preset(plugin, path: Path, *, deterministic: bool = False) -> None:
    if path.suffix.casefold() == ".fxp" and plugin.name == "Surge XT":
        data = surge_component(path, deterministic)
        # Steinberg VST3 preset: header, component bytes, chunk directory.
        plugin.preset_data = (
            b"VST3"
            + struct.pack("<I", 1)
            + plugin.preset_data[8:40]
            + struct.pack("<Q", 48 + len(data))
            + data
            + b"List"
            + struct.pack("<I", 1)
            + b"Comp"
            + struct.pack("<QQ", 48, len(data))
        )
    else:
        plugin.load_preset(str(path))
