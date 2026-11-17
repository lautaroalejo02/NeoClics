"""Audio pieces for the browser editor (WP16): the arrangement, taken apart.

The editor plays the working song itself, in the browser, from pieces:

* **materials**: a chop rendered post-warp/pitch exactly as the renderer does
  (``chop_audio``: grid slot + the seam-crossfade tail). One base material per
  chop serves every clip pass whose window lies inside it (trims are instant);
  passes with ``stretch``/per-pass pitch or a window outside the chop get their
  own material (``pass_audio`` with gain/reverse/fades left to the browser).
* **oneshots**: each drum sample decoded at the project rate with the
  renderer's 1 ms end fade (lane gain/pan/velocity are applied by the browser
  with the renderer's pan law).
* **instrument dry stems**: an instrument track's raw audio (notes rendered by
  its plugin, window-gated) for the whole song, pre fx and fader.
* **wet stems** (``wet=true``): a track rendered with its fx and automation
  (sidechain keys included) like ``render``'s stem, but WITHOUT its static
  ``gain_db``/``pan``/``mute`` (the browser fader applies those, so mixer
  moves stay instant and exact). Automated gain/pan stay in the wet stem.

Every file lives under ``cache/previews/pieces/`` (served by ``/files``),
named by content; unchanged pieces are never re-rendered. What the browser
mix does NOT reproduce is listed in ``approximations``.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid

import numpy as np
import soundfile as sf
from pydantic import Field

from ..errors import MuestraError
from ..project import load_song, open_project
from ..schemas._base import ContractModel, IdStr
from ..schemas.song import (
    BEATS_PER_BAR,
    Master,
    Song,
    Track,
    clip_passes,
)
from ..toolbase import ProjectScoped, ToolOutput

__all__ = ["SongPiecesParams", "SongPiecesResult", "song_pieces", "APPROXIMATIONS"]

PIECES_DIR = "cache/previews/pieces"
#: pieces kept on disk (newest by use); a 16-bar wet stem is ~12 MB.
KEEP_FILES = 400
KEEP_BYTES = 2_500_000_000

APPROXIMATIONS = [
    "La vista previa del editor no pasa por la cadena del master (compresor, limitador, "
    "loudness): para eso está el render final.",
    "Un track recién editado suena «en seco» unos segundos (sin sus efectos salvo filtros, "
    "ganancia y sidechain aproximados) hasta que llega su versión con efectos.",
    "Los cambios de altura, «estirar» y recortes fuera del chop esperan su pieza renderizada.",
]


class SongPiecesParams(ProjectScoped):
    revision: str | None = Field(
        default=None,
        description="Expected song.state revision; a different current file is 'stale_revision'.",
    )
    tracks: list[IdStr] | None = Field(
        default=None, description="Only these tracks (default: all)."
    )
    dry: bool = Field(default=True, description="Materials, one-shots and instrument dry stems.")
    instruments: bool = Field(
        default=True,
        description="With dry: also render instrument dry stems (plugin renders, slower).",
    )
    wet: bool = Field(default=False, description="Per-track renders with fx (seconds).")
    samples: list[str] = Field(
        default_factory=list,
        description="Extra one-shots to prepare (library:// or project paths), e.g. to "
        "audition a sound before using it.",
    )


class Piece(ContractModel):
    id: str
    path: str
    kind: str
    frames: int
    duration_s: float


class Material(ContractModel):
    """Audio for clip passes: material beats [start, start + length/stretch) of the
    chop (or sample) rendered over ``length`` grid beats + ``tail_frames``."""

    chop: IdStr | None = None
    sample: str | None = None
    clip_warp: dict | None = None
    start: float
    length: float
    stretch: float = 1.0
    pitch: float = 0.0
    tail_frames: int
    base: bool = Field(default=False, description="The chop's own slot: any window inside serves.")
    piece: str


class TrackPieces(ContractModel):
    id: IdStr
    kind: str
    dry_stem: str | None = None
    wet: str | None = None
    wet_render_s: float | None = None
    error: str | None = Field(
        default=None, description="Why this track's audio could not be prepared (Spanish)."
    )


class SongPiecesResult(ToolOutput):
    revision: str
    sample_rate: int
    bpm: float
    length_bars: int
    frames: int
    duration_s: float
    pieces: list[Piece] = Field(default_factory=list)
    materials: list[Material] = Field(default_factory=list)
    oneshots: dict[str, str] = Field(default_factory=dict)
    tracks: list[TrackPieces] = Field(default_factory=list)
    render_s: float = 0.0
    approximations: list[str] = Field(default_factory=lambda: list(APPROXIMATIONS))


def _write_piece(project, kind: str, audio: np.ndarray, sr: int, key: str | None = None) -> Piece:
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    digest = key or hashlib.sha256(audio.tobytes()).hexdigest()[:20]
    rel = f"{PIECES_DIR}/{kind}-{digest}.wav"
    path = project.path(rel)
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        from ..render.audio import replace_content_addressed

        tmp = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.wav")
        try:
            sf.write(tmp, audio, sr, subtype="FLOAT")
            replace_content_addressed(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    else:
        _touch(path)
    return Piece(
        id=f"{kind}-{digest}", path=rel, kind=kind, frames=len(audio), duration_s=len(audio) / sr
    )


def _existing(project, kind: str, key: str, sr: int) -> Piece | None:
    rel = f"{PIECES_DIR}/{kind}-{key}.wav"
    path = project.path(rel)
    if not path.is_file():
        return None
    try:
        frames = sf.info(path).frames
    except RuntimeError:
        return None
    _touch(path)
    return Piece(id=f"{kind}-{key}", path=rel, kind=kind, frames=frames, duration_s=frames / sr)


def _touch(path) -> None:
    try:
        path.touch()  # pruning keeps recently used pieces
    except OSError:
        pass


def _prune(project) -> None:
    d = project.path(PIECES_DIR)
    if not d.is_dir():
        return
    files = sorted(d.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    total = 0
    for i, p in enumerate(files):
        try:
            total += p.stat().st_size
            if i >= KEEP_FILES or total > KEEP_BYTES:
                p.unlink(missing_ok=True)
        except OSError:
            continue  # in use (being served / written): next prune


def _kind(track: Track) -> str:
    return "clips" if track.clips is not None else "pattern" if track.pattern else "instrument"


def _with_keys(song: Song, track: Track) -> set[str]:
    selected = {track.id}
    stack = [track]
    by_id = {t.id: t for t in song.tracks}
    while stack:
        t = stack.pop()
        for fx in t.fx:
            if fx.type == "sidechain" and fx.key_track not in selected and fx.key_track in by_id:
                selected.add(fx.key_track)
                stack.append(by_id[fx.key_track])
    return selected


def wet_song(song: Song, track: Track) -> Song:
    """The song reduced to ``track`` (+ its sidechain keys) with no master and the
    track's static gain/pan/mute neutral (automation kept)."""
    selected = _with_keys(song, track)
    targets = {a.target for a in song.automation}
    neutral = {"mute": False}
    if f"tracks.{track.id}.gain_db" not in targets:
        neutral["gain_db"] = 0.0
    if f"tracks.{track.id}.pan" not in targets:
        neutral["pan"] = 0.0
    return song.model_copy(
        deep=True,
        update={
            "tracks": [
                t.model_copy(update=neutral) if t.id == track.id else t
                for t in song.tracks
                if t.id in selected
            ],
            "automation": [
                a
                for a in song.automation
                if any(a.target.startswith(f"tracks.{s}.") for s in selected)
            ],
            "master": Master(fx=[], gain_db=0.0, target_lufs=None),
        },
    )


def song_pieces(p: SongPiecesParams) -> SongPiecesResult:
    """Audio pieces of the working song for the browser editor: chop materials,
    drum one-shots, instrument dry stems and (wet=true) per-track renders with fx."""
    from ..library import core as library
    from ..render.audio import CACHE_ABI, decode, fade
    from ..render.core import (
        arrange,
        beat_frame,
        chop_audio,
        clip_audio,
        pass_audio,
        plain_pass,
        render_audio,
        xfade_frames,
    )
    from ..render.reproducibility import render_signature
    from .core import current_revision

    started = time.perf_counter()
    project = open_project(p.project)
    revision = current_revision(project)
    if p.revision and p.revision != revision:
        raise MuestraError.one(
            "stale_revision",
            "La canción cambió desde que el editor la leyó.",
            value={"expected": p.revision, "current": revision},
            hint="Reload song.state and ask again with its revision.",
        )
    song = load_song(project)
    issues = song.check()
    if issues:
        raise MuestraError(issues, hint="Fix song.yaml (song.validate) before asking for pieces.")
    sr = project.config.sample_rate
    bpm = song.meta.bpm
    n = beat_frame(song.meta.length_bars * BEATS_PER_BAR, bpm, sr)
    wanted = [t for t in song.tracks if p.tracks is None or t.id in p.tracks]
    pieces: dict[str, Piece] = {}
    materials: list[Material] = []
    oneshots: dict[str, str] = {}
    out_tracks: list[TrackPieces] = []
    decoded: dict = {}

    def add(piece: Piece) -> str:
        pieces[piece.id] = piece
        return piece.id

    if p.dry:
        bases: set[str] = set()
        seen: set[tuple] = set()
        for track in wanted:
            for ps in clip_passes(song, track):
                clip = track.clips[ps.clip_index]
                if clip.chop:
                    chop = next(c for c in song.chops if c.id == clip.chop)
                    if clip.chop not in bases:
                        bases.add(clip.chop)
                        xf = xfade_frames(sr, chop.fade_ms)
                        audio = chop_audio(project, chop, sr, bpm, xf)
                        materials.append(
                            Material(
                                chop=chop.id,
                                start=0.0,
                                length=chop.length_beats,
                                tail_frames=xf,
                                base=True,
                                piece=add(_write_piece(project, "chop", audio, sr)),
                            )
                        )
                    inside = (
                        ps.stretch == 1
                        and ps.pitch_semitones == 0
                        and ps.material_start >= 0
                        and ps.material_start + ps.length <= chop.length_beats + 1e-9
                    )
                    if inside:
                        continue
                key = (
                    ps.clip_index,
                    track.id,
                    ps.material_start,
                    ps.length,
                    ps.stretch,
                    ps.pitch_semitones,
                )
                if key in seen:
                    continue
                seen.add(key)
                bare = ps.__class__(
                    **{
                        **ps.__dict__,
                        "gain_db": 0.0,
                        "reverse": False,
                        "fade_in_ms": None,
                        "fade_out_ms": None,
                    }
                )
                if clip.sample and plain_pass(clip, ps):
                    audio, _, xf = clip_audio(project, song, clip, sr, decoded)
                else:
                    audio, xf, _, _ = pass_audio(project, song, clip, bare, sr, decoded)
                materials.append(
                    Material(
                        chop=clip.chop,
                        sample=clip.sample,
                        clip_warp=clip.warp.model_dump(mode="json") if clip.warp else None,
                        start=ps.material_start,
                        length=ps.length,
                        stretch=ps.stretch,
                        pitch=ps.pitch_semitones,
                        tail_frames=xf,
                        piece=add(_write_piece(project, "pass", audio, sr)),
                    )
                )
            if track.pattern is not None:
                refs = {lane.sample for lane in track.pattern.lanes.values()}
                refs |= {h.sample for h in track.pattern.hits if h.sample}
                for ref in sorted(refs):
                    if ref in oneshots:
                        continue
                    path = (
                        library.resolve(project, ref)
                        if ref.startswith("library://")
                        else project.resolve(ref)
                    )
                    audio = fade(decode(project, path, sr)[0], sr, 1, start=False)
                    oneshots[ref] = add(_write_piece(project, "shot", audio, sr))

    for ref in p.samples:
        if ref in oneshots:
            continue
        try:
            path = (
                library.resolve(project, ref)
                if ref.startswith("library://")
                else project.resolve(ref)
            )
        except MuestraError:
            continue
        audio = fade(decode(project, path, sr)[0], sr, 1, start=False)
        oneshots[ref] = add(_write_piece(project, "shot", audio, sr))

    for track in wanted:
        entry = TrackPieces(id=track.id, kind=_kind(track))
        try:
            if p.dry and p.instruments and track.instrument is not None:
                scoped = song.model_copy(
                    deep=True,
                    update={"tracks": [track.model_copy(update={"mute": False})], "automation": []},
                )
                key = hashlib.sha256(
                    json.dumps(
                        [
                            CACHE_ABI,
                            sr,
                            scoped.model_dump(mode="json"),
                            render_signature(project, scoped),
                        ],
                        sort_keys=True,
                    ).encode()
                ).hexdigest()[:20]
                piece = _existing(project, "inst", key, sr)
                if piece is None:
                    raw, _, _ = arrange(project, scoped, sr, n)
                    piece = _write_piece(project, "inst", raw[track.id], sr, key)
                entry.dry_stem = add(piece)
            if p.wet:
                t0 = time.perf_counter()
                scoped = wet_song(song, track)
                key = hashlib.sha256(
                    json.dumps(
                        [CACHE_ABI, sr, track.id, render_signature(project, scoped)], sort_keys=True
                    ).encode()
                ).hexdigest()[:20]
                piece = _existing(project, "wet", key, sr)
                if piece is None:
                    _, stems, _, _ = render_audio(project, scoped, (1, song.meta.length_bars + 1))
                    piece = _write_piece(project, "wet", stems[track.id], sr, key)
                entry.wet = add(piece)
                entry.wet_render_s = round(time.perf_counter() - t0, 3)

        except MuestraError as exc:
            entry.error = "No pude preparar el audio de este track: " + "; ".join(
                i.message for i in exc.issues[:2]
            )
        out_tracks.append(entry)
    _prune(project)
    return SongPiecesResult(
        revision=revision,
        sample_rate=sr,
        bpm=bpm,
        length_bars=song.meta.length_bars,
        frames=n,
        duration_s=n / sr,
        pieces=list(pieces.values()),
        materials=materials,
        oneshots=oneshots,
        tracks=out_tracks,
        render_s=round(time.perf_counter() - started, 3),
        files=[pc.path for pc in pieces.values()],
    )
