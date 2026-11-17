"""Spec section 3 on the REAL stack, driven over MCP (``MUESTRA_RUN_SLOW=1``).

Real htdemucs separation, real Beat This! tracking, the owner's sample
library (``disco-01`` starter kit) and real instruments (Surge XT
``slap-bass``, SFZ ``rhodes``), real ears. Skips with the reason when any
of them is missing on this machine.

Environment knobs:

* ``MUESTRA_SLOW_RECORD``: the record to import (default: the 30 s CC0
  Tiger Rag excerpt in ``~/Documents/MuestraLibrary/_test-audio``);
* ``MUESTRA_DEMO_DIR`` / ``MUESTRA_DEMO_NAME``: keep the project there
  (it must not exist yet) instead of a temporary folder: this is how the
  owner's demo project ``MuestraProjects/demo-e2e`` is produced;
* ``MUESTRA_SLOW_SECTION``: restrict find_chops to that section label.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from muestra.errors import MuestraError
from muestra.plugins.discovery import resolve_plugin
from muestra.project.core import Project
from muestra.schemas.config import ProjectConfig

from .scenario import McpTransport, Scenario, Sound, assert_scenario, cli_subprocess

LIBRARY = Path.home() / "Documents/MuestraLibrary"
RECORD = Path(os.environ.get("MUESTRA_SLOW_RECORD", LIBRARY / "_test-audio/tiger-rag-first30s.wav"))


def _require_real_stack() -> None:
    if not RECORD.is_file():
        pytest.skip(f"test record missing: {RECORD}")
    if not (LIBRARY / ".muestra-index.json").is_file():
        pytest.skip("the owner's indexed library is missing (~/Documents/MuestraLibrary)")
    if not (LIBRARY / "instruments/jrhodes").is_dir():
        pytest.skip("the SFZ instruments are missing (python -m muestra.plugins.install_samples)")
    try:
        resolve_plugin(Project(Path.cwd(), ProjectConfig()), "surge-xt")
    except MuestraError:
        pytest.skip("Surge XT is not installed")
    for module in ("demucs_infer", "beat_this"):
        try:
            __import__(module)
        except ImportError:
            pytest.skip(f"{module} missing: uv sync --extra separation")


@pytest.mark.slow
def test_real_stack_over_mcp(tmp_path):
    _require_real_stack()
    keep = os.environ.get("MUESTRA_DEMO_DIR")
    workdir = Path(keep) if keep else tmp_path
    workdir.mkdir(parents=True, exist_ok=True)
    name = os.environ.get("MUESTRA_DEMO_NAME", "real-song")
    env = {k: v for k, v in os.environ.items() if k not in ("MUESTRA_LIBRARY", "MUESTRA_PROJECT")}
    env["PYTHONUTF8"] = "1"
    sound = Sound(
        bass="{plugin: slap-bass}",
        # a different, curated (bit-exact) preset; arbitrary Surge patches may
        # use noise LFOs and are flagged by render (docs/instruments.md)
        bass_v2="{plugin: synth-bass}",
        keys="{plugin: rhodes}",
        chop_warp="stretch",  # old records track fast; stretch keeps the key
        sample_gain_db=6,
    )
    with McpTransport("muestra.cli.main", env) as mcp:
        scenario = Scenario(
            call=mcp, workdir=workdir, record=RECORD, sound=sound, name=name, library=None
        )
        out = scenario.play(find_section=os.environ.get("MUESTRA_SLOW_SECTION"))
        project = scenario.project
        print(f"\nMCP, real stack ({RECORD.name})\n" + scenario.table())
        assert_scenario(project, out)
        # separation streamed progress notifications over MCP
        separate = mcp.progress["source.separate"]
        assert separate and separate[-1][0] == 1.0, separate[-5:]
        # ears ran on the real render and found the sidechain via cache/stems
        v1 = out["v1"]
        assert v1["metrics_summary"] and v1["metrics_path"]
        metrics = json.loads((project / "renders/v001/metrics.json").read_text("utf-8"))
        codes = {a["code"] for a in metrics["alarms"]}
        assert not codes & {"sidechain_missing", "clipping", "click", "track_silent"}, codes
        sidechain = metrics["low_end_masking"]["sidechain"][0]
        assert sidechain["measured_dip_db"] > 1.5
        # v002/v003 back for the listener; v003 through MCP...
        mcp("render", {"project": str(project), "version": "v003"})
    # ...and v002 from a separate CLI process: Surge XT + SFZ render the same
    # audio in another process (deterministic instruments)
    again = cli_subprocess("render", {"project": str(project), "version": "v002"}, env=env)
    assert again["ok"], again["errors"]
    assert (project / "renders/v002/mix.wav").read_bytes() == out["mixes"]["v002"]
    if not keep:
        shutil.rmtree(project, ignore_errors=True)
