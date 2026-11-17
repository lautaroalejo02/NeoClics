"""Render and preview tools, with git versioning and ears orchestration.

The ``render`` tool orchestrates: validate -> render audio into a staging
folder -> (under the version lock) allocate version id (max+1) ->
``versions.core.record_version`` (commit + tag) -> write song.yaml (byte
copy) + meta.json -> publish the folder as ``renders/vNNN`` ->
``ears.core.analyze`` -> write metrics.json (see render/staging.py).
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
from ..schemas.render_meta import RenderMeta
from ..schemas.song import BEATS_PER_BAR
from ..toolbase import ProjectScoped, ToolOutput
from ..versions import core as versions
from ..versions.diff import song_diff
from . import staging
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
    metrics_path: RelPath | None = Field(
        default=None, description="renders/vNNN/metrics.json: the full ears report."
    )
    metrics_summary: str | None = Field(
        default=None,
        description="Compact Spanish ears summary (headline + worst alarms with their "
        "song.yaml fixes); the full detail is in metrics_path.",
    )
    hint: str | None = Field(
        default=None,
        description="Agent-facing note when metrics or history metadata are unavailable.",
    )


def render(p: RenderParams) -> RenderResult:
    """Render the song to renders/vNNN/: render mix + stems into a staging
    folder, then record the version (git commit + tag), write
    song.yaml/meta.json and publish the folder; run ears for metrics.json
    (spec section 8). A failed render leaves no version behind."""
    project = open_project(p.project)
    staging.recover(project)
    if p.version:
        return _render(project, p)
    try:
        return _render(project, p)
    except MuestraError as exc:
        hint = f"{exc.hint or ''} {staging.NOT_CREATED}".strip()
        raise MuestraError(exc.issues, hint=hint) from exc
    except Exception as exc:  # noqa: BLE001 - say what (not) happened, never a bare 500
        raise MuestraError.one(
            "render_failed",
            f"Render failed ({type(exc).__name__}: {exc}).",
            hint=staging.NOT_CREATED,
        ) from exc


def _render(project, p: RenderParams) -> RenderResult:
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
    with staging.stage(project) as stage:
        # Audio first: nothing is versioned until it exists (WP13 finding 1).
        loudness: dict = {}
        render_song(
            project,
            song,
            p.version or "v001",
            bars,
            stems=p.stems,
            directory=stage.dir,
            report=loudness,
        )
        if note := loudness_hint(loudness):
            hints.append(note)
        (stage.dir / "song.yaml").write_bytes(raw)
        with staging.version_lock(project):
            version = p.version or versions.next_version_id(project)
            stage.mark(version, rerender=bool(p.version))
            parent = prior.parent if prior else None
            commit = prior.git_commit if prior else None
            diff = prior.diff if prior else []
            info = None
            if p.version:
                # Re-render: never a new commit. History (tag + trailers) wins over an
                # old meta.json, and still answers after renders/ was deleted.
                tag = versions.version_tags(project.root).get(version)
                info = versions.commit_info(project.root, tag) if tag else None
                if info is not None:
                    parent, commit = info.parent, info.sha
            else:
                # parent=None lets versions derive it from history, which honours
                # versions.restore (restore v001 -> render -> parent v001).
                commit = versions.record_version(
                    project, version, p.request, p.summary, p.agent, None
                )
                stage.mark(version, commit)
                info = versions.commit_info(project.root, commit)
                parent = info.parent if info else None
            if parent and (not p.version or not diff):
                try:
                    diff = song_diff(versions.song_at(project, parent), song)
                except MuestraError:
                    hints.append(f"Musical diff against parent {parent} unavailable.")
            directory = project.renders_dir / version
            relative = directory.relative_to(project.root).as_posix()
            track_meta = []
            if p.stems:
                for track in song.tracks:
                    kind = (
                        "clips"
                        if track.clips is not None
                        else "pattern"
                        if track.pattern
                        else "instrument"
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
                created_at=_created_at(prior, info if p.version else None),
                request=_first(p.request, info.request if info else None, prior and prior.request),
                summary=_first(p.summary, info.summary if info else None, prior and prior.summary),
                agent=_first(p.agent, info.agent if info else None, prior and prior.agent)
                or "muestra",
                diff=diff,
                render_time_s=time.perf_counter() - started,
                duration_s=sf.info(stage.dir / "mix.wav").duration,
                sample_rate=project.config.sample_rate,
                bpm=song.meta.bpm,
                key=song.meta.key,
                length_bars=song.meta.length_bars,
                range_bars=bars,
                mix=f"{relative}/mix.wav",
                tracks=track_meta,
            )
            _write_json(stage.dir / "meta.json", meta)
            if p.version:
                # A re-render without ears must never keep stale metrics.
                (directory / "metrics.json").unlink(missing_ok=True)
                stage.publish_over(directory)
            else:
                stage.publish_new(directory)
    metrics = None
    metrics_path = None
    try:
        metrics = ears.analyze(project, version)
    except NotImplementedError:
        hints.append(
            "Audio rendered; metrics unavailable until the WP6 ears analyzer is installed."
        )
    except Exception as exc:  # noqa: BLE001 - a metrics failure never loses a render
        hints.append(
            f"Audio rendered; ears could not analyze it ({type(exc).__name__}: {exc}). "
            f"Retry with ears.analyze version={version}."
        )
    if metrics is not None:
        _write_json(directory / "metrics.json", metrics)
        metrics_path = f"{relative}/metrics.json"
    unverified = unverified_reproducibility(song)
    if unverified:
        hints.append(
            "Re-rendering this version is only verified bit-exact for the curated "
            "instrument aliases and SFZ; these tracks use other plugin presets and may "
            "come out slightly different next time: " + ", ".join(unverified) + "."
        )
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
        metrics_summary=metrics.summary if metrics is not None else None,
        metrics_path=metrics_path,
        files=files,
        hint=" ".join(hints) or None,
    )


def loudness_hint(report: dict) -> str | None:
    """Agent-facing note when master.target_lufs was not reached (WP13)."""
    if not report or report.get("target_lufs") is None:
        return None
    target, got = report["target_lufs"], report["integrated_lufs"]
    if abs(target - got) <= 0.5:
        return None
    lost, peak = report["limiter_lu"], report["limiter_peak_db"]
    if report.get("too_hot"):
        return (
            f"Loudness: reached {got:g} LUFS for target_lufs {target:g}: the safety limiter "
            f"already takes {lost:.1f} LU off (peaks down to -{peak:.1f} dB), so this target is "
            f"too hot for this material without crushing it. Set master.target_lufs to about "
            f"{round(got):g}, or tame the peaks first (compressor/saturation, quieter drums)."
        )
    passes = report["passes"]
    return f"Loudness: reached {got:g} LUFS for target_lufs {target:g} after {passes} passes."


def unverified_reproducibility(song) -> list[str]:
    """Tracks whose audio is not known to re-render identically (WP11).

    Curated aliases (plugins.list) use deterministic patch variants and SFZ
    round robins are seeded; arbitrary Surge patches can use noise/S&H LFOs
    (measured: Surge 'Basses/Dynamo Square' differs at -40 dBFS RMS between
    processes) and other VST3s are untested.
    """
    from ..plugins.presets import CURATED

    out = []
    for track in song.tracks:
        inst = track.instrument
        if inst is not None:
            curated = CURATED.get(inst.plugin)
            if inst.plugin == "sfz" or (curated and inst.preset in (None, curated[1])):
                pass
            elif curated and curated[0] == "sfz":
                pass
            else:
                out.append(track.id)
                continue
        if any(fx.type == "plugin" for fx in track.fx):
            out.append(track.id)
    if any(fx.type == "plugin" for fx in song.master.fx):
        out.append("master")
    return out


def _created_at(prior, info):
    """A re-render keeps the version's creation time (UI ordering, feedback
    'pending' badges); a new version is created now."""
    if prior is not None:
        return prior.created_at
    if info is not None:
        try:
            return datetime.fromisoformat(info.committed_at).astimezone(UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


def _first(*values):
    return next((v for v in values if v), None)


def _write_json(path, model):
    path.write_text(
        json.dumps(model.model_dump(mode="json", by_alias=True), indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
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
