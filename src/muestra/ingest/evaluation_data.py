"""Opt-in download of the explicitly CC-BY-NC-SA MDB Drums evaluation set."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

REVISION = "b29e2d63c3a023506f4bf353c5b2e8a558eed135"
REPOSITORY = "https://github.com/CarlSouthall/MDBDrums"
LICENSE = "https://creativecommons.org/licenses/by-nc-sa/4.0/"


def fetch(path: str, root: Path) -> Path:
    destination = root / path
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://raw.githubusercontent.com/CarlSouthall/MDBDrums/{REVISION}/"
        temporary = destination.with_suffix(destination.suffix + ".part")
        urllib.request.urlretrieve(url + urllib.parse.quote(path), temporary)
        temporary.replace(destination)
    return destination


def prepare(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    readme = fetch("README.md", root).read_text(encoding="utf-8")
    if "audio and annotation files" not in readme or LICENSE not in readme:
        raise ValueError("Audio licence could not be verified; refusing download")
    request = urllib.request.Request(
        f"https://api.github.com/repos/CarlSouthall/MDBDrums/git/trees/{REVISION}?recursive=1",
        headers={"User-Agent": "Muestra-evaluation"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        tree = json.load(response)["tree"]
    selected = [
        item["path"]
        for item in tree
        if item["type"] == "blob"
        and (
            "/full_mix/" in item["path"]
            or "/annotations/beats/" in item["path"]
            or item["path"].endswith("_METADATA.yaml")
            or ("_STEMS/" in item["path"] and item["path"].endswith(".wav"))
        )
    ]
    total = sum(item.get("size", 0) for item in tree if item["path"] in selected)
    if total > 1_800_000_000:
        raise ValueError(f"Dataset exceeds download budget: {total}")
    provenance = [
        "# MDB Drums downloads - 2026-11-17",
        "",
        "Carl Southall, Chih-Wei Wu, Alexander Lerch, Jason Hockman (2017).",
        f"Source: {REPOSITORY}, revision {REVISION}.",
        f"Audio and annotations: CC-BY-NC-SA 4.0 ({LICENSE}).",
        "Noncommercial evaluation only; do not distribute with Muestra.",
        "",
        "| Item | URL | Licence / date | SHA-256 |",
        "|---|---|---|---|",
    ]
    for index, path in enumerate(selected):
        local = fetch(path, root)
        with local.open("rb") as handle:
            checksum = hashlib.file_digest(handle, "sha256").hexdigest()
        url = f"{REPOSITORY}/blob/{REVISION}/{urllib.parse.quote(path)}"
        provenance.append(f"| {path} | {url} | CC-BY-NC-SA 4.0 / 2026-11-17 | {checksum} |")
        (root / "SOURCES.md").write_text("\n".join(provenance) + "\n", encoding="utf-8")
        print(f"[{index + 1}/{len(selected)}] {path}", flush=True)
    tracks = []
    for path in selected:
        if not path.endswith("_METADATA.yaml"):
            continue
        metadata = yaml.safe_load((root / path).read_text(encoding="utf-8"))
        name = Path(path).name.removesuffix("_METADATA.yaml")
        stems = {}
        for stem in metadata["stems"].values():
            instrument = stem["instrument"]
            label = (
                "drums"
                if instrument == "drum set"
                else "bass"
                if "bass" in instrument and instrument != "bass clarinet"
                else "vocals"
                if "singer" in instrument or "vocal" in instrument
                else "other"
            )
            stems.setdefault(label, []).append(
                str(Path(path).parent / metadata["stem_dir"] / stem["filename"])
            )
        tracks.append(
            {
                "id": name.lower().replace("musicdelta_", ""),
                "title": f"Music Delta - {metadata['title']}",
                "audio": f"MDB Drums/audio/full_mix/{metadata['mix_filename']}",
                "beats": f"MDB Drums/annotations/beats/{name}_MIX.beats",
                "reference_strength": "published beat-position annotations",
                "reference_stems": stems,
                "genre": metadata["genre"],
                "license": "CC-BY-NC-SA-4.0",
                "source": REPOSITORY,
            }
        )
    manifest = {"revision": REVISION, "download_bytes": total, "tracks": tracks}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    prepare(args.directory.resolve())


if __name__ == "__main__":
    main()
