"""Explicit, reproducible setup of free sample data OUTSIDE the repository.

Run ``uv run python -m muestra.plugins.install_samples``. No downloads occur
during rendering. jRhodes permits commercial musical works but restricts sample
redistribution; its data must never be bundled with the application.
"""

from __future__ import annotations

import io
import re
import struct
import urllib.request
import zipfile
from pathlib import Path

from .presets import instrument_root

SOURCES = {
    "swagbass": ("karoryfer.swagbass", "9d10fcae71af1975988ddecd5af1c95d372c7355"),
    "jrhodes": ("jlearman.jRhodes3c", "ba6000f633d0a1d405049d877851ff7cc43f108b"),
}


def flatten(text):
    """Read the two pinned upstream maps for conversion, preserving inherited values."""
    text = re.sub(r"//[^\n]*", "", text)
    group = {}
    for section, body in re.findall(r"<(group|region)>([^<]*)", text):
        values = dict(re.findall(r"(\w+)\s*=\s*(.*?)(?=\s+\w+\s*=|$)", body.strip(), re.DOTALL))
        if section == "group":
            group = values
        else:
            yield {**group, **values}


def flac_loop(path):
    """Extract the RIFF smpl loop stored losslessly in FLAC application metadata."""
    data = path.read_bytes()
    position = 4
    while position + 4 < len(data):
        kind = data[position] & 127
        last = data[position] & 128
        size = int.from_bytes(data[position + 1 : position + 4], "big")
        value = data[position + 4 : position + 4 + size]
        if kind == 2 and value.startswith(b"riffsmpl"):
            # 4-byte FLAC application id + 8-byte RIFF chunk header +
            # 36-byte smpl header, then loop cue id/type/start/end.
            return struct.unpack_from("<II", value, 56)
        position += size + 4
        if last:
            break
    raise ValueError(f"No embedded sample loop in {path}")


def prepare_maps(root):
    bass = root / "swagbass"
    lines = ["// Muestra: CC0 Karoryfer Swagbass, notes only; no release noises."]
    for region in flatten((bass / "swagbass_clean.sfz").read_text()):
        if not region["sample"].startswith("notes"):
            continue
        region = {
            k: v
            for k, v in region.items()
            if not k.startswith("bend_") and not k.startswith("amp_velcurve_")
        }
        region["sample"] = region["sample"].replace("\\", "/")
        region["volume"] = "-6"
        lines.append("<region> " + " ".join(f"{k}={v}" for k, v in region.items()))
    (bass / "muestra-finger.sfz").write_text("\n".join(lines) + "\n", encoding="utf-8")
    rhodes = root / "jrhodes"
    folder = rhodes / "jRhodes3c-looped-flac-sfz"
    lines = ["// Muestra: Jeffrey Learman jRhodes3c mono; explicit original sample loops."]
    for region in flatten((folder / "_jRhodes-mono-looped.sfz").read_text()):
        if region.get("loop_mode") == "loop_continuous":
            lo, hi = flac_loop(folder / region["sample"])
            region.update(loop_start=str(lo), loop_end=str(hi))
        region["volume"] = "-6"
        region["sample"] = "jRhodes3c-looped-flac-sfz/" + region["sample"]
        lines.append("<region> " + " ".join(f"{k}={v}" for k, v in region.items()))
    (rhodes / "muestra-rhodes.sfz").write_text("\n".join(lines) + "\n", encoding="utf-8")


def install(root=None):
    root = root or instrument_root()
    for name, (repo, commit) in SOURCES.items():
        destination = root / name
        if not destination.exists():
            url = f"https://codeload.github.com/sfzinstruments/{repo}/zip/{commit}"
            print(f"Downloading {repo}@{commit}", flush=True)
            with urllib.request.urlopen(url, timeout=120) as response:
                archive = zipfile.ZipFile(io.BytesIO(response.read()))
            for entry in archive.infolist():
                relative = Path(*Path(entry.filename).parts[1:])
                target = (destination / relative).resolve()
                if not target.is_relative_to(destination.resolve()):
                    raise ValueError("Unsafe archive path")
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(entry))
        allowed = (
            "CC0 1.0: commercial music and redistribution allowed."
            if name == "swagbass"
            else "Commercial music allowed explicitly as a musical instrument. "
            "Samples have separate noncommercial redistribution terms; do not bundle them."
        )
        (destination / "LICENSE.txt").write_text(
            f"Source: https://github.com/sfzinstruments/{repo}\nRevision: {commit}\n"
            f"Commercial use in music: {allowed}\n\n" + (destination / "LICENSE").read_text(),
            encoding="utf-8",
        )
    prepare_maps(root)
    print(f"Ready: {root}. Undo: remove only swagbass/ and jrhodes/ under this directory.")


if __name__ == "__main__":
    install()
