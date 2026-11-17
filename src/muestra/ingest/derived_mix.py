"""Build instrumental/no-drums chop sources without extending the stem enum.

uv run python -m muestra.ingest.derived_mix PROJECT SOURCE --kind instrumental --import-as src-inst
"""

from __future__ import annotations

import argparse
import hashlib
import json
from importlib.metadata import version
from pathlib import Path

import numpy as np
import soundfile as sf

from ..errors import MuestraError
from ..project import load_analysis, open_project
from .analysis import (
    ANALYSIS_METHOD,
    active_stems,
    estimate_key,
    integrated_loudness,
    spectral_features,
)
from .audio import decode, stereo
from .pulse import QUALITY_VERSION, pulse_evidence
from .reliability import assess_reliability
from .storage import digest, import_source, project_lock, source_file, write_json


def derive_mix(project, source_id: str, kind: str) -> Path:
    choices = {"instrumental": ("drums", "bass", "other"), "no-drums": ("bass", "vocals", "other")}
    if kind not in choices:
        raise MuestraError.one("invalid_value", "Choose instrumental or no-drums.")
    with project_lock(project):
        _, original = source_file(project, source_id)
        active = active_stems(project, source_id, digest(original))
        if not active or any(name not in active["stems"] for name in choices[kind]):
            raise MuestraError.one(
                "ref_not_found", "Aligned stems required; run source.separate first."
            )
        paths = {name: project.resolve(active["stems"][name]) for name in choices[kind]}
        hashes = {name: digest(path) for name, path in paths.items()}
        identity = hashlib.sha256(json.dumps([kind, hashes], sort_keys=True).encode()).hexdigest()[
            :20
        ]
        output = project.path(f"cache/derived/{source_id}/{identity}/{kind}.wav")
        if output.exists() and output.with_suffix(".json").exists():
            return output
        signals = []
        sample_rate = length = None
        for path in paths.values():
            audio, sr = decode(path)
            if sample_rate is not None and (sr != sample_rate or len(audio) != length):
                raise MuestraError.one("separation_error", "Stems are not sample-aligned.")
            sample_rate, length = sr, len(audio)
            signals.append(stereo(audio).astype(np.float64))
        combined = sum(signals).astype(np.float32)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(".part.wav")
        sf.write(temporary, combined, sample_rate, subtype="FLOAT")
        temporary.replace(output)
        write_json(
            output.with_suffix(".json"),
            {
                "parent": source_id,
                "kind": kind,
                "model": active.get("model"),
                "stem_sha256": hashes,
                "operation": "unaltered sample-aligned stem sum; no normalization",
            },
        )
        return output


def import_derived(project, source_id: str, kind: str, imported_id: str):
    output = derive_mix(project, source_id, kind)
    parent = load_analysis(project, source_id)
    new_id, relative = import_source(project, output, imported_id)
    if new_id == source_id:
        raise MuestraError.one("invalid_value", "Derived source must have a new source id.")
    with project_lock(project):
        cached = project.path(f"cache/analysis/{new_id}.json")
        if cached.exists():
            return new_id, relative
        analysis = parent.model_copy(
            deep=True,
            update={
                "source_id": new_id,
                "file": relative,
                "stems": None,
                "separation_model": None,
            },
        )
        audio, sr = decode(output)
        _, _, chroma = spectral_features(audio, sr)
        analysis.key = estimate_key(chroma)
        analysis.loudness_lufs = integrated_loudness(audio, sr)
        pulse = pulse_evidence(audio, sr)
        analysis.reliability = assess_reliability(analysis, pulse)
        write_json(cached, analysis.model_dump(mode="json"))
        identity = f"{digest(project.resolve(relative))}:{ANALYSIS_METHOD}:{version('beat-this')}"
        write_json(
            cached.with_suffix(".meta.json"),
            {
                "identity": identity,
                "method": ANALYSIS_METHOD,
                "parent_grid_snapshot": source_id,
                "quality_version": QUALITY_VERSION,
                "pulse": pulse.to_dict(),
            },
        )
    return new_id, relative


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("source")
    parser.add_argument("--kind", choices=["instrumental", "no-drums"], required=True)
    parser.add_argument("--import-as")
    args = parser.parse_args()
    project = open_project(args.project.resolve())
    if args.import_as:
        print(json.dumps(import_derived(project, args.source, args.kind, args.import_as)))
    else:
        print(derive_mix(project, args.source, args.kind))


if __name__ == "__main__":
    main()
