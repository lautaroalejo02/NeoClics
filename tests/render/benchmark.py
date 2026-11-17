"""Run with uv run python -m tests.render.benchmark (no private audio needed)."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import tempfile
import time
from pathlib import Path

import numpy as np
import pedalboard
import soundfile as sf
from scipy.signal import resample_poly

from muestra.project import Project, load_song
from muestra.render.core import render_song
from muestra.schemas.song import Track
from tests.render.conftest import prepare_fixture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--library-root", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="muestra-render-benchmark-") as temporary:
        project = prepare_fixture(Path(temporary))
        project = Project(project.root, project.config.model_copy(update={"sample_rate": 48000}))
        song = load_song(project)
        if args.library_root:
            from muestra.schemas.config import LibraryConfig

            config = project.config.model_copy(
                update={"library": LibraryConfig(roots=[args.library_root.resolve().as_posix()])}
            )
            project = Project(project.root, config)
            song.tracks[1].pattern.lanes[
                "kick"
            ].sample = "library://tr8-hardware-machines/roland-tr-8/kick-tr-8-kick-02.wav"
            song.tracks[1].pattern.lanes[
                "clap"
            ].sample = "library://essential-kits-musicradar/tr-909-kit/clap-tr-909-clap.wav"
            # Keep the fixture hat without copying any private source samples.
            song.tracks[1].pattern.lanes["ohat"].sample = "sources/fixture-hat.wav"
            import shutil

            shutil.copyfile(
                Path(temporary) / "library/kits/disco-01/ohat.wav",
                project.path("sources/fixture-hat.wav"),
            )
        # Actual renderer path for bass, using fixture audio. No plugin fake:
        # plugin-host speed is WP3's separate integration responsibility.
        song.tracks[2] = Track.model_validate(
            {
                "id": "bass",
                "gain_db": -4,
                "clips": [
                    {
                        "sample": "cache/stems/src1/bass.wav",
                        "length_beats": 2,
                        "at_bar": 1,
                        "repeat": 32,
                    }
                ],
            }
        )
        timings, hashes = [], []
        features = None
        for index in range(args.runs):
            version = f"v{100 + index}"
            start = time.perf_counter()
            directory = render_song(project, song, version, (1, 17))
            timings.append(time.perf_counter() - start)
            hashes.append(hashlib.sha256((directory / "mix.wav").read_bytes()).hexdigest())
            if features is None:
                audio, sr = sf.read(directory / "mix.wav", dtype="float32", always_2d=True)
                import pyloudnorm as pyln

                features = {
                    "rms": float(np.sqrt(np.mean(audio**2))),
                    "true_peak_4x": float(np.max(np.abs(resample_poly(audio, 4, 1, axis=0)))),
                    "frames": len(audio),
                    "sample_rate": sr,
                    "lufs": float(pyln.Meter(sr).integrated_loudness(audio)),
                }
        print(
            json.dumps(
                {
                    "platform": platform.platform(),
                    "processor": platform.processor(),
                    "pedalboard": pedalboard.__version__,
                    "seconds": timings,
                    "pcm_file_hashes": hashes,
                    "bit_identical": len(set(hashes)) == 1,
                    "features": features,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
