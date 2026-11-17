"""Opt-in real-project audit. Only run on disposable copies under --root wp15-*.

uv run python -m tests.integration.wp15_audit --root C:/.../wp15-experiments
Audio stays outside the repository. evidence.json contains objective measurements.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from muestra.project import load_analysis, load_song, open_project, save_song
from muestra.registry import run_tool


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args]).decode().strip()


def state(project):
    return {
        "head": git(project.root, "rev-parse", "HEAD"),
        "tags": git(project.root, "tag"),
        "versions": sorted(p.name for p in project.renders_dir.glob("v[0-9]*")),
    }


def kill_after_commit(project):
    """Kill an actual renderer process between its tag and atomic publication."""
    from muestra.render.staging import recover

    before = state(project)
    ready = project.path("cache/wp15-kill-ready")
    ready.unlink(missing_ok=True)
    code = """
import sys,time
from pathlib import Path
from muestra.render import staging
from muestra.registry import run_tool
def paused(self,target):
 Path(sys.argv[2]).write_text(str(target))
 time.sleep(90)
staging.Stage.publish_new=paused
r=run_tool("render",{"project":sys.argv[1],"range_bars":[1,3],"summary":"kill probe"})
assert r.ok,r.errors
"""
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(
            [sys.executable, "-c", code, str(project.root), str(ready)],
            stdout=log,
            stderr=log,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        try:
            deadline = time.monotonic() + 60
            while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if not ready.exists():
                log.seek(0)
                raise AssertionError(log.read().decode(errors="replace"))
            proc.kill()
            proc.wait(timeout=10)
            removed = recover(project)
            assert state(project) == before, (state(project), before)
            assert not list(project.renders_dir.glob(".staging/*/pending.json"))
            ready.unlink()
            return {"killed_exit": proc.returncode, "recovered": removed, "state_unchanged": True}
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)


def audit(root):
    from muestra.feedback import read_events
    from muestra.render import tools
    from muestra.render.core import render_chop, render_chop_loop
    from muestra.schemas.song import Instrument, MidiNote

    class Evidence(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            (root / "evidence.json").write_text(
                json.dumps(self, indent=2, default=float), encoding="utf-8", newline="\n"
            )

    result = Evidence()

    def call(project, name, **params):
        started = time.perf_counter()
        r = run_tool(name, {"project": str(project.root), **params})
        assert r.ok, (name, r.errors, r.hint)
        return r.data, round(time.perf_counter() - started, 3)

    codex = open_project(root / "session-codex")
    before = state(codex)

    def fail_after_audio(*args, **kwargs):
        from muestra.render.core import render_song

        render_song(*args, **kwargs)
        raise RuntimeError("injected failure after real audio generation")

    with patch.object(tools, "render_song", fail_after_audio):
        failed = run_tool("render", {"project": str(codex.root), "range_bars": [1, 3]})
    assert not failed.ok and state(codex) == before
    result["atomic"] = {
        "failed_code": failed.errors[0].code,
        "state_unchanged": True,
        "kill": kill_after_commit(codex),
    }

    song = load_song(codex)
    chop = song.chops[0]
    analysis = load_analysis(codex, chop.source)
    found, _ = call(
        codex,
        "source.find_chops",
        source_id=chop.source,
        stem=chop.stem,
        length_beats=chop.length_beats * 2,
        at_s=analysis.downbeats_s[chop.start_downbeat - 1],
        n=1,
    )
    chosen, _ = call(
        codex,
        "chops.choose",
        candidate_id=found["set"]["candidates"][0]["id"],
        chop_id=chop.id,
        mode="manual",
    )
    assert chosen["recorded"] and read_events(codex).events[-1].type == "chop_choice"
    result["manual_choice"] = {
        k: chosen[k] for k in ("chop_id", "recorded", "rescaled", "repeat_changes", "agent_todo")
    }
    save_song(codex, song)
    by_time, _ = call(
        codex,
        "source.find_chops",
        source_id=chop.source,
        stem=chop.stem,
        length_beats=4,
        window_s=[0, 20],
        n=2,
    )
    exact, _ = call(
        codex,
        "source.find_chops",
        source_id=chop.source,
        stem=chop.stem,
        length_beats=4,
        at_s="0:15",
        n=1,
    )
    result["time_selection"] = {
        "window": [[c["start_s"], c["end_s"]] for c in by_time["set"]["candidates"]],
        "at_15": exact["set"]["candidates"][0]["start_s"],
    }

    from tests.render.test_seams import _dip_db

    sr = codex.config.sample_rate
    seams = {}
    for mode in ("resample", "stretch"):
        c = chop.model_copy(deep=True)
        c.warp.mode = mode
        x = render_chop_loop(codex, c, sr, song.meta.bpm, 3)
        slot = round(c.length_beats * 60 / song.meta.bpm * sr)
        wrap = np.concatenate([x[-sr // 10 :], x[: sr // 10]])
        seams[mode] = {
            "repeat_min_1ms_db": min(_dip_db(x, k * slot, sr) for k in (1, 2)),
            "wrap_min_1ms_db": _dip_db(wrap, sr // 10, sr),
            "zero_frames_at_boundary": int(
                np.count_nonzero(np.all(x[slot - 24 : slot + 24] == 0, axis=1))
            ),
        }
        from muestra.render.core import render_audio
        from muestra.schemas.song import Clip

        isolated = song.model_copy(deep=True)
        track = isolated.tracks[0]
        track.clips = [
            Clip(chop=c.id, at_bar=1, repeat=int(isolated.meta.length_bars * 4 / c.length_beats))
        ]
        track.fx = []
        isolated.tracks, isolated.chops, isolated.automation = [track], [c], []
        isolated.master.fx, isolated.master.target_lufs = [], None
        full = render_audio(codex, isolated, (1, isolated.meta.length_bars + 1))[0]
        full_wrap = np.concatenate([full[-sr // 10 :], full[: sr // 10]])
        seams[mode]["full_render_wrap_min_1ms_db"] = float(_dip_db(full_wrap, sr // 10, sr))
    result["real_record_seams"] = seams

    rendered, elapsed = call(codex, "render", draft=True, analyze=True)
    metrics = json.loads(codex.path(rendered["metrics_path"]).read_text("utf-8"))
    result["master"] = {
        "wall_s": elapsed,
        "target": metrics["loudness"]["target_lufs"],
        "measured": metrics["loudness"]["integrated_lufs"],
        "hint": rendered["hint"],
    }
    # Real MP3 decoding exercises the shared resolver; corrupt files are tested
    # separately by the codec regression suite.
    audio, rate = sf.read(codex.resolve(analysis.file), always_2d=True)
    result["source_decode"] = {"frames": len(audio), "sr": rate}
    from muestra.errors import MuestraError
    from muestra.ingest.audio import decode as ingest_decode
    from muestra.media import require_ffmpeg
    from muestra.render.audio import decode as render_decode

    ffmpeg = require_ffmpeg()
    codec = codex.path("cache/wp15-codec.m4a")
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            str(codex.resolve(analysis.file)),
            "-t",
            "1",
            "-c:a",
            "aac",
            str(codec),
        ],
        check=True,
    )
    with patch.dict(os.environ, {"MUESTRA_FFMPEG": ffmpeg}):
        imported, import_sr = ingest_decode(codec)
        decoded, _ = render_decode(codex, codec, import_sr)
        bad = codex.path("cache/wp15-corrupt.wav")
        bad.write_bytes(b"this is not a WAV")
        failures = []
        for decoder in (lambda: ingest_decode(bad), lambda: render_decode(codex, bad, 48000)):
            try:
                decoder()
            except MuestraError as exc:
                failures.append(exc.issues[0].code)
        assert failures == ["decode_error", "decode_error"]
    result["ffmpeg"] = {
        "m4a_ingest_frames": len(imported),
        "m4a_render_frames": len(decoded),
        "sr": import_sr,
        "corrupt_codes": failures,
    }

    from tests.feedback.test_concurrency import WRITER

    old_count = len(read_events(codex).events)
    writers = [
        subprocess.Popen(
            [sys.executable, "-c", WRITER, str(codex.root), str(i), "400"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for i in range(3)
    ]
    for writer in writers:
        writer.stdin.write(b"go\n")
        writer.stdin.flush()
    for writer in writers:
        out, err = writer.communicate(timeout=60)
        assert writer.returncode == 0, (out, err)
    count = len(read_events(codex).events) - old_count
    assert count == 1200, count
    result["feedback"] = {"writers": 3, "expected": 1200, "appended": count}

    demo = open_project(root / "demo-e2e")
    # Two real renders provide an old, unliked, reproducible version that is
    # not the base, even when the owner's existing versions are all protected.
    call(demo, "render", range_bars=[1, 3], analyze=False, summary="WP15 prune probe A")
    call(demo, "render", range_bars=[1, 3], analyze=False, summary="WP15 prune probe B")
    dry, _ = call(demo, "versions.prune", keep_latest=0, cache=False)
    assert dry["remove"], "The prune test must exercise a real deletion"
    import hashlib

    fingerprints = {
        item["version"]: hashlib.sha256(
            demo.path(f"renders/{item['version']}/mix.wav").read_bytes()
        ).hexdigest()
        for item in dry["remove"]
    }
    ranges = {
        v: json.loads(demo.path(f"renders/{v}/meta.json").read_text("utf-8"))["range_bars"]
        for v in fingerprints
    }
    tags_before = git(demo.root, "tag")
    real, _ = call(demo, "versions.prune", keep_latest=0, cache=False, dry_run=False)
    assert git(demo.root, "tag") == tags_before
    assert all(not demo.path(f"renders/{item['version']}").exists() for item in dry["remove"])
    result["prune"] = {
        "dry_remove": dry["remove"],
        "freed_bytes": real["freed_bytes"],
        "history_preserved": True,
    }
    restored = next(iter(fingerprints))
    call(demo, "render", version=restored, range_bars=ranges[restored], analyze=False)
    assert (
        hashlib.sha256(demo.path(f"renders/{restored}/mix.wav").read_bytes()).hexdigest()
        == fingerprints[restored]
    )
    result["prune_restore"] = {"version": restored, "bit_exact": True}

    from muestra.ears.dsp import integrated_loudness
    from muestra.plugins.core import render_instrument
    from muestra.plugins.presets import CURATED

    aliases = {}
    for alias in CURATED:
        notes = (
            [
                MidiNote(beat=1 + i, pitch=(33, 36, 38, 40)[i % 4], length_beats=0.9)
                for i in range(16)
            ]
            if "bass" in alias
            else [
                MidiNote(beat=1 + 2 * i, pitch=n, length_beats=1.8)
                for i in range(8)
                for n in (60, 64, 67)
            ]
        )
        x = render_instrument(codex, Instrument(plugin=alias), notes, 48000, 408000, bpm=120)
        aliases[alias] = float(integrated_loudness(x, 48000))
    result["alias_lufs"] = aliases
    c = chop.model_copy(deep=True)
    c.warp.pitch_semitones = 2
    c.warp.formants = None
    default = render_chop(codex, c, sr, song.meta.bpm)
    c.warp.formants = False
    off = render_chop(codex, c, sr, song.meta.bpm)
    c.warp.formants = True
    on = render_chop(codex, c, sr, song.meta.bpm)
    result["formants"] = {
        "other_default_equals_off": bool(np.array_equal(default, off)),
        "on_off_rms_difference": float(np.sqrt(np.mean((on - off) ** 2))),
    }
    c.stem, c.warp.formants = "vocals", None
    vocal_default = render_chop(codex, c, sr, song.meta.bpm)
    c.warp.formants = True
    result["vocal_formants"] = {
        "default_equals_on": bool(
            np.array_equal(vocal_default, render_chop(codex, c, sr, song.meta.bpm))
        )
    }

    # Full Livery source in the Devin copy; no new separation is needed.
    devin = open_project(root / "session-devin")
    analyzed, seconds = call(devin, "source.analyze", source_id="src1")
    a = load_analysis(devin, "src1")
    reliable = [r.model_dump() for r in a.reliability.regions if r.reliable]
    found, _ = call(
        devin, "source.find_chops", source_id="src1", stem="mix", at_s=44, length_beats=8, n=1
    )
    selected = found["set"]["candidates"][0]
    data = {
        "meta": {"title": "Livery grid proof", "bpm": 120, "key": "C", "length_bars": 4},
        "sources": [{"id": "src1", "file": a.file}],
        "chops": [
            {
                "id": "livery",
                "source": "src1",
                "stem": "mix",
                "start_downbeat": selected["start_downbeat"],
                "length_beats": 8,
                "warp": {"mode": "stretch"},
            }
        ],
        "tracks": [{"id": "sample", "clips": [{"chop": "livery", "at_bar": 1, "repeat": 2}]}],
    }
    from muestra.schemas.song import Song

    save_song(devin, Song.model_validate(data))
    draft, wall = call(devin, "render", draft=True, analyze=True)
    output, sr = sf.read(devin.path(draft["mix_path"]), always_2d=True)
    from muestra.render.core import render_chop_loop

    ref = render_chop_loop(devin, load_song(devin).chops[0], sr, 120, 2)
    # Compare each repetition's waveform to a separately rendered grid-mapped
    # reference. Master gain is scalar here; normalized cross correlation tests
    # actual PCM offsets rather than reading the same metadata back.
    offsets = []
    from scipy.signal import correlate

    for beat in range(16):
        at = round(beat * 0.5 * sr)
        width, search = round(0.08 * sr), round(0.03 * sr)
        lo, hi = max(0, at - search), min(len(output), at + width + search)
        template = ref[at : at + width].mean(axis=1)
        correlation = correlate(output[lo:hi].mean(axis=1), template, mode="valid", method="fft")
        offset = int(np.argmax(correlation)) + lo - at
        offsets.append(offset * 1000 / sr)
    result["livery"] = {
        "cached_analysis_s": seconds,
        "bpm": a.bpm_global,
        "confidence": a.reliability.confidence,
        "reliable_regions": reliable,
        "chop": [selected["start_s"], selected["end_s"]],
        "render_wall_s": wall,
        "frames": len(output),
        "expected_frames": 8 * sr,
        "pcm_reference_offsets_ms": offsets,
        "max_abs_offset_ms": max(abs(x) for x in offsets),
    }
    from muestra.ears.dsp import onsets

    attacks = onsets(output.mean(axis=1), sr).strongest(0.3).times
    nearest = np.asarray([min(abs(attacks - beat * 0.5)) * 1000 for beat in range(1, 16)])
    result["livery_onsets"] = {
        "n_detected": len(attacks),
        "median_nearest_grid_ms": float(np.median(nearest)),
        "p90_nearest_grid_ms": float(np.percentile(nearest, 90)),
        "beats_with_attack_within_70ms": int(sum(nearest <= 70)),
        "beats_checked": 15,
    }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    assert root.name.startswith("wp15-"), "Use disposable wp15-* copies only"
    evidence = audit(root)
    path = root / "evidence.json"
    path.write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False, default=float),
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(evidence, indent=2, ensure_ascii=True, default=float))


if __name__ == "__main__":
    main()
