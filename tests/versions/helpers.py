"""Shared helpers for the versions tests (no renderer here: renders/ is faked)."""

from __future__ import annotations

import copy
import os
import shutil
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from muestra.project import Project, next_version_id
from muestra.schemas.metrics import Metrics
from muestra.schemas.render_meta import RenderMeta
from muestra.schemas.song import Song
from muestra.versions.core import record_version

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_SONG = REPO_ROOT / "fixtures" / "example-project" / "song.yaml"

#: the example song as a plain dict, for building variants.
BASE_SONG: dict[str, Any] = yaml.safe_load(EXAMPLE_SONG.read_text(encoding="utf-8"))


def song_dict(mutate=None) -> dict[str, Any]:
    d = copy.deepcopy(BASE_SONG)
    if mutate is not None:
        mutate(d)
    return d


def make_song(mutate=None) -> Song:
    return Song.model_validate(song_dict(mutate))


def track(d: dict, tid: str) -> dict:
    return next(t for t in d["tracks"] if t["id"] == tid)


def song_text(mutate=None) -> str:
    return yaml.safe_dump(song_dict(mutate), sort_keys=False, allow_unicode=True)


def git(project: Project | Path, *args: str, check: bool = True) -> str:
    """Run a plain git command in the project (stdout text, stripped)."""
    root = project.root if isinstance(project, Project) else project
    proc = subprocess.run(
        ["git", "-C", str(root), "-c", "core.autocrlf=false", *args],
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"git {args} failed: {proc.stderr.decode(errors='replace')}")
    return proc.stdout.decode("utf-8", errors="replace").strip()


def git_bytes(project: Project, *args: str) -> bytes:
    proc = subprocess.run(["git", "-C", str(project.root), *args], capture_output=True, check=True)
    return proc.stdout


def write_song(project: Project, text_or_bytes: str | bytes) -> None:
    data = text_or_bytes.encode("utf-8") if isinstance(text_or_bytes, str) else text_or_bytes
    project.song_path.write_bytes(data)


def fake_render(
    project: Project,
    *,
    request: str | None = "una request",
    summary: str | None = "un resumen",
    agent: str | None = "tests",
    parent: str | None = None,
    stems: bool = True,
    metrics: Metrics | None = None,
) -> str:
    """What the ``render`` tool does around ``record_version`` (audio faked)."""
    version = next_version_id(project)
    sha = record_version(project, version, request, summary, agent, parent)
    rdir = project.renders_dir / version
    (rdir / "stems").mkdir(parents=True, exist_ok=True)
    (rdir / "song.yaml").write_bytes(project.song_path.read_bytes())
    (rdir / "mix.wav").write_bytes(b"RIFF....WAVE")
    if stems:
        (rdir / "stems" / "bass.wav").write_bytes(b"RIFF....WAVE")
    meta = RenderMeta(
        version=version,
        parent=parent,
        git_commit=sha,
        created_at=datetime.now(UTC),
        request=request,
        agent=agent or "tests",
        summary=summary,
        sample_rate=22050,
        bpm=120,
        key="F#m",
        length_bars=16,
        range_bars=(1, 17),
        mix=f"renders/{version}/mix.wav",
    )
    (rdir / "meta.json").write_text(meta.model_dump_json(by_alias=True), encoding="utf-8")
    if metrics is not None:
        (rdir / "metrics.json").write_text(
            metrics.model_copy(update={"version": version}).model_dump_json(), encoding="utf-8"
        )
    return version


def rm_rf(path: Path) -> None:
    """rmtree that also removes git's read-only object files on Windows."""

    def onerror(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    shutil.rmtree(path, onerror=onerror)


def delete_renders(project: Project) -> None:
    rm_rf(project.renders_dir)
    project.renders_dir.mkdir()


def make_metrics(
    lufs: float = -14.0,
    low: float = -20.0,
    mid: float = -25.0,
    high: float = -30.0,
    clipping: bool = False,
    dominant: str | None = "bass",
) -> Metrics:
    return Metrics.model_validate(
        {
            "version": "v001",
            "loudness": {
                "integrated_lufs": lufs,
                "true_peak_dbtp": -1.0,
                "clipping": clipping,
                "clipped_samples": 0,
            },
            "spectral": {
                "mix": {"low_db": low, "mid_db": mid, "high_db": high},
            },
            "low_end_masking": {"dominant_track": dominant},
            "alignment": {"max_abs_offset_ms": 0.0},
            "tonal": {},
            "clicks": {"count": 0},
        }
    )
