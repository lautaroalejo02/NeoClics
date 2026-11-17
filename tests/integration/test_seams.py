"""Seams that only show up across processes (WP11).

* ``library.index`` runs a process pool: on Windows (spawn) it must work
  when launched from the real ``muestra`` console-script MCP server and from
  a ``muestra serve`` job, report progress, and really use the pool (its
  silent single-process fallback would hide a broken pool).
* ``DiffEntry`` serialises ``from`` through the plain registry envelope.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from muestra.registry import run_tool

N_FILES = 30  # above library.index's pool threshold (24)


def _library(root: Path) -> Path:
    lib = root / "lib"
    folder = lib / "kits" / "pool-kit"
    folder.mkdir(parents=True)
    t = np.arange(4800) / 48000
    for i in range(N_FILES):
        kind = ("kick", "clap", "hat")[i % 3]
        tone = np.sin(2 * np.pi * (50 + 7 * i) * t) * np.exp(-t * (20 + i))
        sf.write(folder / f"{kind}-{i:02d}.wav", tone.astype(np.float32), 48000, subtype="PCM_16")
    return lib


def _project(tmp_path: Path) -> Path:
    created = run_tool("project.create", {"name": "song", "dir": str(tmp_path)})
    assert created.ok, created.errors
    return Path(created.data["project_path"])


def _muestra_executable() -> str:
    scripts = Path(sys.executable).parent
    found = shutil.which("muestra", path=str(scripts)) or shutil.which("muestra")
    assert found, "the muestra console script is not installed in this environment"
    return found


def test_library_index_pool_from_the_mcp_console_script(tmp_path, monkeypatch):
    import anyio
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    monkeypatch.setenv("MUESTRA_LIBRARY", "none")
    project, lib = _project(tmp_path), _library(tmp_path)
    progress: list[float] = []

    async def scenario():
        params = StdioServerParameters(
            command=_muestra_executable(),
            args=["mcp", "--project", str(project)],
            env={**os.environ, "PYTHONUTF8": "1", "MUESTRA_LIBRARY": "none"},
        )
        with anyio.fail_after(240):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()

                    async def on_progress(value, total, message):
                        progress.append(value / (total or 1.0))

                    result = await client.call_tool(
                        "library_index",
                        {"path": str(lib), "workers": 2, "force": True},
                        progress_callback=on_progress,
                    )
                    # the protocol stream survived the pool's child processes
                    listing = await client.list_tools()
                    assert listing.tools
                    return result

    result = anyio.run(scenario)
    envelope = result.structured_content
    assert envelope["ok"], envelope["errors"]
    root = envelope["data"]["roots"][0]
    assert root["workers"] == 2, "the process pool fell back to a single process"
    assert envelope["data"]["indexed"] == N_FILES
    assert len(progress) >= 5 and progress[-1] == 1.0


def test_library_index_pool_in_a_serve_job(tmp_path, monkeypatch):
    from muestra.api.server import create_app

    monkeypatch.setenv("MUESTRA_LIBRARY", "none")
    project, lib = _project(tmp_path), _library(tmp_path)
    with TestClient(create_app(project, watch_interval=0.05, debounce=0.05)) as client:
        started = client.post(
            "/tools/library_index", json={"path": str(lib), "workers": 2, "force": True}
        )
        assert started.status_code == 202
        job_id = started.json()["job_id"]
        deadline = time.monotonic() + 240
        seen_progress = set()
        while time.monotonic() < deadline:
            job = client.get(f"/jobs/{job_id}").json()
            if job["progress"] is not None:
                seen_progress.add(round(job["progress"], 2))
            if job["status"] in ("done", "failed"):
                break
            time.sleep(0.05)
        assert job["status"] == "done", job
        data = job["result"]["data"]
        assert data["roots"][0]["workers"] == 2 and data["indexed"] == N_FILES
        assert len(seen_progress) >= 2


def test_diff_entries_say_from_through_every_envelope(tmp_path):
    """meta.diff returned by render via run_tool used to say 'from_'."""
    from muestra.schemas.render_meta import DiffEntry, RenderMeta

    entry = DiffEntry.model_validate({"path": "meta.bpm", "from": 120, "to": 125})
    assert entry.model_dump() == {"path": "meta.bpm", "from": 120, "to": 125}
    assert '"from":120' in entry.model_dump_json()
    meta = RenderMeta.model_validate_json(
        (Path(__file__).parents[2] / "fixtures/example-project/renders/v001/meta.json").read_text(
            "utf-8"
        )
    ).model_copy(update={"diff": [entry]})
    assert meta.model_dump(mode="json")["diff"] == [{"path": "meta.bpm", "from": 120, "to": 125}]
