"""Real-server fixture for the UI's Playwright suite (ui/playwright.real.config.ts).

``python -m tests.integration.e2e_server --dir D --port P`` builds a real
project in D (synth record, fixture kit, SFZ bass, separation/beat tracking
faked, everything else real: renders v001 and v002 with the real renderer,
git versioning and ears) and serves it with the real ``muestra serve`` app
and ui/dist. With ``--project`` it serves an existing project instead (the
owner's demo). It prints ``READY <url> <project>`` once listening.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from tests.conftest import FIXTURES

from . import fakes
from .scenario import Sound, song_text


def build(root: Path) -> Path:
    from muestra.registry import run_tool

    def call(tool, **params):
        result = run_tool(tool, params)
        assert result.ok, (tool, result.errors)
        return result.data

    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    library = root / "library"
    shutil.copytree(FIXTURES / "library", library)
    kit = {lane: f"kits/disco-01/{lane}.wav" for lane in ("kick", "clap", "ohat")}
    (library / "starter-kits.json").write_text(
        json.dumps(
            {"schema_version": 0, "kits": {"disco-01": {"description": "e2e", "lanes": kit}}}
        ),
        encoding="utf-8",
    )
    record = fakes.synth_record(root / "inbox" / "record.wav")
    os.environ["MUESTRA_LIBRARY"] = "none"
    fakes.install()
    call("project.create", name="song", dir=str(root))
    project = root / "song"
    toml = (project / "muestra.toml").read_text("utf-8")
    (project / "muestra.toml").write_bytes(
        toml.replace('    "library",\n', f'    "{library.as_posix()}",\n').encode("utf-8")
    )
    src = call("source.import", project=str(project), path=str(record))
    call("source.separate", project=str(project), source_id=src["source_id"])
    call("source.analyze", project=str(project), source_id=src["source_id"])
    song = (project / "song.yaml").read_text("utf-8").replace("bpm: 120.0", "bpm: 125")
    (project / "song.yaml").write_bytes(song.encode("utf-8"))
    found = call(
        "source.find_chops", project=str(project), source_id=src["source_id"], length_beats=8
    )
    pick = found["set"]["pick"]["candidate_id"]
    call("chops.choose", project=str(project), candidate_id=pick, chop_id="c1", mode="auto")
    chosen = next(c for c in found["set"]["candidates"] if c["id"] == pick)
    chop = {
        "source": src["source_id"],
        "file": src["file"],
        "stem": chosen["stem"],
        "start_downbeat": chosen["start_downbeat"],
        "length_beats": chosen["length_beats"],
    }
    bass = fakes.tiny_sfz(root, "bass", 65.4)
    kits = call("library.kits", project=str(project), name="disco-01")
    sound = Sound(bass="", bass_v2="", kit={x["lane"]: x["uri"] for x in kits["kits"][0]["lanes"]})
    inst = f'{{plugin: sfz, preset: "{bass}"}}'
    (project / "song.yaml").write_bytes(song_text(125, "Eb", chop, sound, bass=inst).encode())
    call("render", project=str(project), summary="Primer loop", agent="e2e")
    (project / "song.yaml").write_bytes(
        song_text(125, "Eb", chop, sound, bass=inst, pitch=1, depth=0.8).encode()
    )
    call("render", project=str(project), summary="Chop un semitono arriba", agent="e2e")
    return project


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()
    project = args.project or build(args.dir)
    import uvicorn

    from muestra.api.server import create_app

    app = create_app(project)
    print(f"READY http://127.0.0.1:{args.port} {project}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
