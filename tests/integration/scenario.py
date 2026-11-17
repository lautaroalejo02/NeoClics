"""The spec section 3 scenario, driven the way an agent drives Muestra.

One :class:`Scenario` runs every step through a *transport* (CLI in-process,
CLI subprocess, or the official MCP client over stdio) and records a timing
row per step. The song is written as TEXT, like an agent editing
``song.yaml``; the tools never see a Python object.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import soundfile as sf

from muestra.registry import REGISTRY

REPO = Path(__file__).resolve().parents[2]
CRLF = b"\r\n"


# ---------------------------------------------------------------------------
# transports: call(tool_name, params) -> ToolResult dict
# ---------------------------------------------------------------------------


def _cli_argv(name: str, params: dict) -> list[str]:
    spec = REGISTRY[name]
    group, action = spec.cli or (spec.group, name.split(".")[-1])
    argv = [group] if action is None else [group, action]
    params = dict(params)
    project = params.pop("project", None)
    if project:
        argv += ["--project", str(project)]
    return [*argv, "--json", "--params-json", json.dumps(params)]


def cli_inprocess(name: str, params: dict) -> dict:
    from muestra.cli.main import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        main(_cli_argv(name, params))
    return json.loads(out.getvalue())


def cli_subprocess(name: str, params: dict, *, module: str = "muestra.cli.main", env=None) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", module, *_cli_argv(name, params)],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=1800,
    )
    if not proc.stdout.strip():
        raise AssertionError(f"{name}: no JSON on stdout; stderr:\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


class McpTransport:
    """The official MCP Python SDK client over stdio, used from sync code."""

    def __init__(self, module: str, env: dict[str, str], project: str | None = None):
        self.module, self.env, self.project = module, env, project
        self.progress: dict[str, list[tuple[float, str | None]]] = {}

    def __enter__(self):
        from anyio.from_thread import start_blocking_portal
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        self._stack = contextlib.ExitStack()
        self.portal = self._stack.enter_context(start_blocking_portal())
        args = ["-m", self.module, "mcp"] + (["--project", self.project] if self.project else [])
        params = StdioServerParameters(command=sys.executable, args=args, cwd=REPO, env=self.env)
        read, write = self._stack.enter_context(
            self.portal.wrap_async_context_manager(stdio_client(params))
        )
        self.session = self._stack.enter_context(
            self.portal.wrap_async_context_manager(ClientSession(read, write))
        )
        self.portal.call(self.session.initialize)
        return self

    def __exit__(self, *exc):
        self._stack.close()

    def tools(self) -> list[str]:
        return [t.name for t in self.portal.call(self.session.list_tools).tools]

    def __call__(self, name: str, params: dict) -> dict:
        mcp_name = REGISTRY[name].mcp_name
        seen = self.progress.setdefault(name, [])

        async def on_progress(value, total, message):
            seen.append((value / (total or 1.0), message))

        async def call():
            return await self.session.call_tool(mcp_name, params, progress_callback=on_progress)

        result = self.portal.call(call)
        envelope = result.structured_content
        assert envelope is not None, result
        assert result.is_error == (not envelope["ok"])
        return envelope


# ---------------------------------------------------------------------------
# the agent-written song
# ---------------------------------------------------------------------------


@dataclass
class Sound:
    """What differs between the fast (fixture) and slow (real library) runs."""

    bass: str  # instrument mapping text for v001, e.g. "{plugin: slap-bass}"
    bass_v2: str  # a different preset for v002
    keys: str | None = None  # optional second instrument (SFZ alias)
    kit: dict[str, str] = field(default_factory=dict)  # lane -> uri, from library.kits
    chop_warp: str = "resample"


def song_text(
    bpm: float, key: str, chop: dict, sound: Sound, *, bass: str, pitch=0, depth=0.5
) -> str:
    lanes = {"kick": "x...x...x...x...", "clap": "....x.......x...", "ohat": "..x...x...x...x."}
    lane_lines = "\n".join(
        f'      {lane}: {{sample: "{uri}", steps: "{lanes.get(lane, "x.x.x.x.x.x.x.x.")}"}}'
        for lane, uri in sound.kit.items()
        if lane in ("kick", "clap", "ohat", "chat")
    )
    phrase = [(0, "Eb2", 0.5), (0.75, "Eb3", 0.25), (1.5, "Bb2", 0.5), (3, "Eb2", 0.75)]
    phrase += [(4, "Ab2", 0.5), (5.5, "G2", 0.5), (6.5, "Bb2", 0.5)]
    notes = "\n".join(
        f"  - {{beat: {1 + r * 8 + b:g}, pitch: {p}, length_beats: {n:g}, velocity: 100}}"
        for r in range(8)
        for b, p, n in phrase
    )
    keys = ""
    if sound.keys:
        chords = "\n".join(
            f"  - {{beat: {1 + r * 8 + b:g}, pitch: {p}, length_beats: 1.5, velocity: 80}}"
            for r in range(8)
            for b, ch in ((1.5, ("Eb3", "G3", "Bb3")), (5.5, ("Ab3", "C4", "Eb4")))
            for p in ch
        )
        keys = f"""- id: keys
  at_bar: 9
  instrument: {sound.keys}
  notes:
{chords}
  gain_db: -9
"""
    return f"""schema_version: 0
# written by the agent (integration test)
meta:
  title: integration
  bpm: {bpm:g}
  key: {key}
  length_bars: 16
  seed: 0
sources:
- id: {chop["source"]}
  file: {chop["file"]}
chops:
- id: c1
  source: {chop["source"]}
  stem: {chop["stem"]}
  start_downbeat: {chop["start_downbeat"]}
  length_beats: {chop["length_beats"]:g}
  warp: {{mode: {sound.chop_warp}, pitch_semitones: {pitch}}}
tracks:
- id: sample
  clips:
  - {{chop: c1, at_bar: 1, repeat: {int(64 // chop["length_beats"])}}}
  fx:
  - {{id: hp, type: highpass, cutoff_hz: 150}}
  - {{id: lp, type: lowpass, cutoff_hz: 18000}}
  - {{id: sc, type: sidechain, key_track: drums, key_lane: kick, depth: {depth}}}
- id: drums
  pattern:
    steps_per_bar: 16
    lanes:
{lane_lines}
  fx:
  - {{id: comp, type: compressor, threshold_db: -18, ratio: 4}}
- id: bass
  at_bar: 5
  instrument: {bass}
  notes:
{notes}
  gain_db: -4
{keys}automation:
- {{target: tracks.sample.fx.lp.cutoff_hz, curve: exp, points: [[1, 700], [5, 18000]]}}
master:
  fx:
  - {{type: compressor, threshold_db: -12, ratio: 2}}
  - {{type: limiter, ceiling_db: -1.0}}
  target_lufs: -12
"""


# ---------------------------------------------------------------------------
# the scenario
# ---------------------------------------------------------------------------


@dataclass
class Step:
    name: str
    seconds: float
    ok: bool
    note: str = ""


@dataclass
class Scenario:
    call: Callable[[str, dict], dict]
    workdir: Path
    record: Path
    sound: Sound
    name: str = "song"
    library: Path | None = None
    steps: list[Step] = field(default_factory=list)
    results: dict[str, Any] = field(default_factory=dict)

    @property
    def project(self) -> Path:
        return self.workdir / self.name

    def run(self, label: str, tool: str, params: dict, *, ok: bool = True) -> dict:
        if tool != "project.create":
            params = {"project": str(self.project), **params}
        start = time.perf_counter()
        result = self.call(tool, params)
        elapsed = time.perf_counter() - start
        note = "" if result["ok"] else json.dumps(result["errors"], ensure_ascii=False)[:300]
        self.steps.append(Step(label, elapsed, result["ok"], note))
        if ok:
            assert result["ok"], f"{label}: {result['errors']} {result.get('hint')}"
        self.results[label] = result
        return result["data"] if result["ok"] else result

    def edit(self, label: str, text: str) -> None:
        start = time.perf_counter()
        (self.project / "song.yaml").write_bytes(text.encode("utf-8"))
        self.steps.append(Step(label, time.perf_counter() - start, True))

    def table(self) -> str:
        rows = [
            f"| {s.name} | {s.seconds:.2f} s | {'ok' if s.ok else 'FAIL'} |" for s in self.steps
        ]
        return "| step | time | status |\n|---|---:|---|\n" + "\n".join(rows)

    # -- the flow ----------------------------------------------------------
    def play(self, *, bpm: float = 125.0, key: str = "Eb", find_section: str | None = None):
        p = self.project
        self.run("project.create", "project.create", {"name": self.name, "dir": str(self.workdir)})
        if self.library is not None:  # point the project at this run's library only
            toml = (p / "muestra.toml").read_text("utf-8")
            root = self.library.as_posix()
            (p / "muestra.toml").write_bytes(
                toml.replace('    "library",\n', f'    "{root}",\n').encode("utf-8")
            )
        imported = self.run("source.import", "source.import", {"path": str(self.record)})
        src = imported["source_id"]
        self.run("source.separate", "source.separate", {"source_id": src})
        self.run("source.analyze", "source.analyze", {"source_id": src})
        # the agent sets the project tempo first, so previews play at it
        song = (p / "song.yaml").read_text("utf-8")
        self.edit("edit tempo", song.replace("bpm: 120.0", f"bpm: {bpm:g}"))
        query = {"source_id": src, "length_beats": 8, "n": 3}
        if find_section:
            query["section"] = find_section
        found = self.run("source.find_chops", "source.find_chops", query)
        pick = found["set"]["pick"]["candidate_id"]
        self.run(
            "chops.choose", "chops.choose", {"candidate_id": pick, "chop_id": "c1", "mode": "auto"}
        )
        self.run("plugins.list", "plugins.list", {})
        kits = self.run("library.kits", "library.kits", {"name": "disco-01"})
        if not self.sound.kit:  # the agent pastes the kit's lanes
            lanes = kits["kits"][0]["lanes"]
            self.sound.kit = {lane["lane"]: lane["uri"] for lane in lanes if lane["present"]}
        chosen = next(c for c in found["set"]["candidates"] if c["id"] == pick)
        chop = {
            "source": src,
            "file": imported["file"],
            "stem": chosen["stem"],
            "start_downbeat": chosen["start_downbeat"],
            "length_beats": chosen["length_beats"],
        }
        self.edit("write song.yaml", song_text(bpm, key, chop, self.sound, bass=self.sound.bass))
        valid = self.run("song.validate", "song.validate", {})
        assert valid["valid"], valid["issues"]
        v1 = self.run(
            "render v001",
            "render",
            {
                "request": "loop future funk de 16 compases",
                "summary": "Primer loop",
                "agent": "integration-test",
            },
        )
        edited = song_text(bpm, key, chop, self.sound, bass=self.sound.bass_v2, pitch=1, depth=0.8)
        self.edit("edit (pitch+1, sidechain, bass)", edited)
        v2 = self.run(
            "render v002",
            "render",
            {
                "request": "medio tono arriba, más bombeo, otro bajo",
                "summary": "Subí el chop un semitono, sidechain más profundo, otro bajo",
                "agent": "integration-test",
            },
        )
        listing = self.run("versions.list", "versions.list", {})
        diff = self.run("versions.diff v001 v002", "versions.diff", {"a": "v001", "b": "v002"})
        self.run(
            "feedback.append rating",
            "feedback.append",
            {"event": {"type": "rating", "version": "v002", "value": "like"}},
        )
        self.run(
            "feedback.append comment",
            "feedback.append",
            {
                "event": {
                    "type": "comment",
                    "version": "v002",
                    "at_s": 12.0,
                    "track": "drums",
                    "text": "acá el bombo se come al sample",
                }
            },
        )
        feedback = self.run("feedback.read", "feedback.read", {})
        self.run("versions.restore v001", "versions.restore", {"version": "v001"})
        v3 = self.run(
            "render v003", "render", {"summary": "Desde v001", "agent": "integration-test"}
        )
        original = (p / "renders/v001/mix.wav").read_bytes()
        shutil.rmtree(p / "renders")
        again = self.run("re-render v001", "render", {"version": "v001"})
        return {
            "v1": v1,
            "v2": v2,
            "v3": v3,
            "again": again,
            "listing": listing,
            "diff": diff,
            "feedback": feedback,
            "found": found,
            "original_mix": original,
            "chop": chop,
        }


def assert_scenario(project: Path, out: dict, bpm: float = 125.0) -> None:
    """The acceptance checks every transport must satisfy."""
    found = out["found"]
    expect_s = 2 * 8 * 60 / bpm  # previews loop the chop twice at PROJECT tempo
    for candidate in found["set"]["candidates"]:
        assert candidate["id"].startswith(found["set_id"] + "/cand")  # global ids
        info = sf.info(project / candidate["preview"])
        assert info.samplerate == 48000 and abs(info.duration - expect_s) < 0.01
    v1, v2, v3 = out["v1"]["meta"], out["v2"]["meta"], out["v3"]["meta"]
    assert (v1["version"], v1["parent"]) == ("v001", None)
    assert (v2["version"], v2["parent"]) == ("v002", "v001")
    assert (v3["version"], v3["parent"]) == ("v003", "v001")  # restore -> parent v001
    assert v1["agent"] == "integration-test" and v1["request"] and v1["summary"] == "Primer loop"
    paths = {d["path"] for d in v2["diff"]}
    assert {"chops.c1.warp.pitch_semitones", "tracks.sample.fx.sc.depth"} <= paths
    assert any(p.startswith("tracks.bass.instrument") for p in paths)
    assert all("from" in d and "from_" not in d for d in v2["diff"])
    assert v3["diff"] == []  # restored song == v001
    assert out["diff"]["changes"] and out["diff"]["musical_summary"]
    assert [v["version"] for v in out["listing"]["versions"]] == ["v002", "v001"]
    events = out["feedback"]["events"]
    assert [e["type"] for e in events] == ["rating", "comment"] and events[1]["at_s"] == 12.0
    # re-render after deleting renders/: same audio, no new version, metadata kept
    again = out["again"]["meta"]
    assert again["version"] == "v001" and again["git_commit"] == v1["git_commit"]
    assert again["summary"] == "Primer loop" and again["agent"] == "integration-test"
    assert (project / "renders/v001/mix.wav").read_bytes() == out["original_mix"]
    assert (project / "renders/v001/song.yaml").is_file()
    tags = subprocess.run(
        ["git", "-C", str(project), "tag"], capture_output=True, text=True, check=True
    ).stdout.split()
    assert tags == ["v001", "v002", "v003"]
    # every text file Muestra wrote uses LF
    text_files = [
        project / name
        for name in (
            "song.yaml",
            "muestra.toml",
            ".gitignore",
            ".gitattributes",
            "feedback.jsonl",
            "docs/decisions.md",
        )
    ]
    text_files += list(project.glob("renders/*/meta.json"))
    text_files += list(project.glob("cache/analysis/*.json"))
    text_files += list(project.glob("cache/chops/*/*.json"))
    for path in text_files:
        assert CRLF not in path.read_bytes(), f"{path} has CRLF"
