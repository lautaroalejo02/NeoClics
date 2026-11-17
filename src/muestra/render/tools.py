"""Render and preview tools, with git versioning and ears orchestration.

The ``render`` tool orchestrates: validate -> allocate version id
(max+1) -> ``versions.core.record_version`` (commit + tag) -> render
audio -> write ``renders/vNNN/song.yaml`` (byte copy) + meta.json ->
``ears.core.analyze`` -> write metrics.json.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime

import soundfile as sf
from pydantic import Field, ValidationError

from ..ears import core as ears
from ..errors import MuestraError
from ..project import load_song, open_project, validate_song
from ..schemas._base import IdStr, RelPath, VersionStr
from ..schemas.metrics import Metrics
from ..schemas.render_meta import RenderMeta
from ..schemas.song import BEATS_PER_BAR
from ..toolbase import ProjectScoped, ToolOutput
from ..versions import core as versions
from ..versions.tools import VersionsDiffParams, VersionsListParams, versions_diff, versions_list
from .audio import fade
from .core import beat_frame, render_audio, render_chop, render_song

__all__ = [
    "PreviewParams",
    "PreviewResult",
    "RenderParams",
    "RenderResult",
    "preview",
    "render",
    "TOOLS",
]


class PreviewParams(ProjectScoped):
    target: str = Field(
        description="What to preview: 'mix', 'chop:<id>', 'track:<id>', "
        "or 'clip:<track_id>:<index>' (0-based clip index)."
    )
    bars: float | None = Field(
        default=None, gt=0, description="Max length in bars (default: whole target)."
    )


class PreviewResult(ToolOutput):
    target: str
    preview_path: RelPath = Field(description="cache/previews/... audio path.")


def preview(p: PreviewParams) -> PreviewResult:
    """Render a short preview of a chop, clip, track or the mix.
    Plays at PROJECT tempo when a song.yaml exists, else at source
    tempo."""
    project = open_project(p.project)
    song = load_song(project)
    sr, bpm = project.config.sample_rate, song.meta.bpm
    parts = p.target.split(":")
    audio = None
    if len(parts) == 2 and parts[0] == "chop":
        chop = next((c for c in song.chops if c.id == parts[1]), None)
        if chop is not None:
            audio = render_chop(project, chop, sr, bpm)
    elif p.target == "mix":
        audio = render_audio(project, song, (1, song.meta.length_bars + 1))[0]
    elif (len(parts) == 2 and parts[0] == "track") or (len(parts) == 3 and parts[0] == "clip"):
        track = next((t for t in song.tracks if t.id == parts[1]), None)
        if track is not None:
            selected = {track.id}

            def include_keys(t):
                for fx in t.fx:
                    if fx.type == "sidechain" and fx.key_track not in selected:
                        selected.add(fx.key_track)
                        include_keys(next(t for t in song.tracks if t.id == fx.key_track))

            issues = song.check()
            if issues:
                raise MuestraError(issues)
            include_keys(track)
            start = (track.at_bar - 1) * BEATS_PER_BAR
            end = ((track.until_bar or song.meta.length_bars + 1) - 1) * BEATS_PER_BAR
            if parts[0] == "clip":
                try:
                    index = int(parts[2])
                except ValueError:
                    index = -1
                if track.clips is None or not 0 <= index < len(track.clips):
                    raise MuestraError.one(
                        "out_of_range",
                        "Clip index is 0-based.",
                        path="target",
                        value=p.target,
                        hint="Use clip:<track>:<index> with an existing clip index.",
                    )
                clip = track.clips[index]
                track = track.model_copy(update={"clips": [clip]})
                start = (clip.at_bar - 1) * BEATS_PER_BAR + clip.offset_beats
                length = (
                    clip.length_beats
                    if clip.sample
                    else next(c.length_beats for c in song.chops if c.id == clip.chop)
                )
                end = start + length * clip.repeat
            scoped = song.model_copy(
                deep=True,
                update={
                    "tracks": [
                        track if t.id == track.id else t for t in song.tracks if t.id in selected
                    ],
                    "automation": [
                        a
                        for a in song.automation
                        if any(a.target.startswith(f"tracks.{s}.") for s in selected)
                    ],
                    "master": song.master.model_copy(
                        update={"fx": [], "gain_db": 0, "target_lufs": None}
                    ),
                },
            )
            _, tracks, _, _ = render_audio(project, scoped, (1, song.meta.length_bars + 1))
            audio = tracks[track.id][beat_frame(start, bpm, sr) : beat_frame(end, bpm, sr)]
    if audio is None:
        raise MuestraError.one(
            "ref_not_found",
            "Unknown preview target.",
            path="target",
            value=p.target,
            hint="Use mix, chop:<id>, track:<id>, or clip:<track>:<0-based index>.",
        )
    if p.bars is not None:
        audio = audio[: max(1, beat_frame(p.bars * BEATS_PER_BAR, bpm, sr))]
    audio = fade(audio, sr, start=False)
    key = hashlib.sha256(p.target.encode() + audio.tobytes()).hexdigest()[:24]
    relative = f"cache/previews/render-{key}.wav"
    project.path(relative).parent.mkdir(parents=True, exist_ok=True)
    sf.write(project.path(relative), audio, sr, subtype="PCM_24")
    return PreviewResult(target=p.target, preview_path=relative, files=[relative])


class RenderParams(ProjectScoped):
    range_bars: tuple[int, int] | None = Field(
        default=None,
        description="[start_bar, end_bar) to render, 1-indexed, end "
        "EXCLUSIVE; must satisfy start < end <= length_bars + 1. "
        "Default: the whole song.",
    )
    stems: bool = Field(default=True, description="Also render per-track stems.")
    request: str | None = Field(
        default=None,
        description="The natural-language request that produced this "
        "version (commit trailer Muestra-Request).",
    )
    summary: str | None = Field(
        default=None,
        description="Musical description of what changed (commit subject 'vNNN: <summary>').",
    )
    agent: str | None = Field(
        default=None,
        description="Agent/tool identity (commit trailer Muestra-Agent).",
    )
    version: VersionStr | None = Field(
        default=None,
        description="Re-render an existing version from its tagged "
        "song.yaml into renders/<version>/ — no new commit.",
    )


class RenderResult(ToolOutput):
    version: VersionStr
    mix_path: RelPath
    stem_paths: dict[IdStr, RelPath] = Field(default_factory=dict)
    meta_path: RelPath
    meta: RenderMeta
    metrics_path: RelPath | None = None
    metrics: Metrics | None = None
    hint: str | None = Field(
        default=None,
        description="Agent-facing note when metrics or history metadata are unavailable.",
    )


def render(p: RenderParams) -> RenderResult:
    """Render the song to renders/vNNN/: record the version (git commit
    + tag), render mix + stems, write song.yaml/meta.json, run ears for
    metrics.json (spec section 8)."""
    project = open_project(p.project)
    started = time.perf_counter()
    hints = []
    prior = None
    if p.version:
        song = versions.song_at(project, p.version)
        raw = versions.song_at_raw(project, p.version).encode("utf-8")
        snapshot = project.path(f"renders/{p.version}/song.yaml")
        if snapshot.is_file():
            raw = snapshot.read_bytes()
        old_meta = project.path(f"renders/{p.version}/meta.json")
        if old_meta.is_file():
            try:
                prior = RenderMeta.model_validate_json(old_meta.read_bytes())
            except ValidationError:
                hints.append("Previous render metadata was invalid; it was regenerated.")
        issues = song.check()
    else:
        issues = validate_song(project, check_files=True)
        song = load_song(project)
        raw = project.song_path.read_bytes()
    if issues:
        raise MuestraError(issues)
    bars = p.range_bars or (1, song.meta.length_bars + 1)
    if not 1 <= bars[0] < bars[1] <= song.meta.length_bars + 1:
        raise MuestraError.one(
            "invalid_range",
            "Invalid end-exclusive bar range.",
            path="range_bars",
            value=bars,
            hint=f"Use 1 <= start < end <= {song.meta.length_bars + 1}.",
        )
    version = p.version or versions.next_version_id(project)
    parent = prior.parent if prior else None
    commit = prior.git_commit if prior else None
    diff = prior.diff if prior else []
    existing = None
    try:
        history = versions_list(VersionsListParams(project=str(project.root))).versions
        if p.version:
            existing = next((v for v in history if v.version == version), None)
            if existing:
                parent, commit = existing.parent, existing.git_commit
        elif history:
            parent = history[0].version
    except NotImplementedError:
        hints.append("Version-history metadata unavailable until WP5 is installed.")
    if not p.version:
        commit = versions.record_version(project, version, p.request, p.summary, p.agent, parent)
    directory = render_song(project, song, version, bars, stems=p.stems)
    (directory / "song.yaml").write_bytes(raw)
    if parent:
        try:
            diff = versions_diff(
                VersionsDiffParams(project=str(project.root), a=parent, b=version)
            ).diff
        except NotImplementedError:
            hints.append("Musical diff unavailable until WP5 is installed.")
    relative = directory.relative_to(project.root).as_posix()
    track_meta = []
    if p.stems:
        for track in song.tracks:
            kind = (
                "clips" if track.clips is not None else "pattern" if track.pattern else "instrument"
            )
            track_meta.append(
                {
                    "id": track.id,
                    "kind": kind,
                    "stem": f"{relative}/stems/{track.id}.wav",
                    "lanes": [
                        {"id": lane, "stem": f"{relative}/stems/{track.id}.{lane}.wav"}
                        for lane in (track.pattern.lanes if track.pattern else [])
                    ],
                }
            )
    meta = RenderMeta(
        version=version,
        parent=parent,
        git_commit=commit,
        created_at=datetime.now(UTC),
        request=p.request if p.request is not None else (prior.request if prior else None),
        summary=p.summary or (existing.summary if existing else prior.summary if prior else None),
        agent=p.agent or (prior.agent if prior else "muestra"),
        diff=diff,
        render_time_s=time.perf_counter() - started,
        duration_s=sf.info(directory / "mix.wav").duration,
        sample_rate=project.config.sample_rate,
        bpm=song.meta.bpm,
        key=song.meta.key,
        length_bars=song.meta.length_bars,
        range_bars=bars,
        mix=f"{relative}/mix.wav",
        tracks=track_meta,
    )
    _write_json(directory / "meta.json", meta)
    metrics = None
    metrics_path = None
    # A re-render without ears must never return stale metrics from an old render.
    (directory / "metrics.json").unlink(missing_ok=True)
    try:
        metrics = ears.analyze(project, version)
    except NotImplementedError:
        hints.append(
            "Audio rendered; metrics unavailable until the WP6 ears analyzer is installed."
        )
    if metrics is not None:
        _write_json(directory / "metrics.json", metrics)
        metrics_path = f"{relative}/metrics.json"
    files = [f"{relative}/mix.wav", f"{relative}/song.yaml", f"{relative}/meta.json"]
    for track in meta.tracks:
        files.append(track.stem)
        files.extend(lane.stem for lane in track.lanes)
    if metrics_path:
        files.append(metrics_path)
    return RenderResult(
        version=version,
        mix_path=meta.mix,
        stem_paths={t.id: t.stem for t in meta.tracks},
        meta_path=f"{relative}/meta.json",
        meta=meta,
        metrics=metrics,
        metrics_path=metrics_path,
        files=files,
        hint=" ".join(hints) or None,
    )


def _write_json(path, model):
    path.write_text(
        json.dumps(model.model_dump(mode="json", by_alias=True), indent=2) + "\n", encoding="utf-8"
    )


#: tools exported by this subpackage (collected by muestra.registry).
#: 'render' and 'preview' are TOP-LEVEL CLI commands.
TOOLS = [
    (
        "render",
        render,
        RenderParams,
        RenderResult,
        {"long_running": True, "cli": ("render", None)},
    ),
    (
        "preview",
        preview,
        PreviewParams,
        PreviewResult,
        {"long_running": True, "cli": ("preview", None)},
    ),
]
