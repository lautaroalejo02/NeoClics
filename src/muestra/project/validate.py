"""song.yaml loading, round-trip editing and validation.

Loading goes through ruamel.yaml in round-trip mode so that:

* duplicate keys are a ``duplicate_key`` issue with path + hint —
  never silently dropped;
* :func:`load_song_document`/:func:`save_song_document`/
  :func:`edit_song_document` let tools make surgical edits that keep
  comments, key ordering and omitted defaults intact — tools never
  rewrite ``song.yaml`` wholesale.

Two validation levels, deliberately separable:

* structural + semantic (always): pydantic parse -> :meth:`Song.check`
  (cross references, ids, automation targets, clip overrun, pattern
  lengths, sidechain cycles);
* filesystem-aware (``check_files=True``): referenced files exist
  (sources, samples, midi) and chops are checked against the source's
  ``cache/analysis/<source_id>.json`` grid (``start_downbeat`` bounds,
  downbeat-on-beat match, ``start_offset_beats`` and chop end inside
  the analyzed beats), and sample clips that ask for a ``warp`` must
  have a known BPM in a library index.

``load_analysis``/``save_analysis`` and ``load_song``/``dump_song`` are
also used by later WPs that write these files.
"""

from __future__ import annotations

import bisect
import json
import re
from collections.abc import Callable
from pathlib import Path

import yaml
from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.error import YAMLError as RuamelError

from ..errors import Issue, MuestraError, issues_from_validation_error
from ..schemas._base import LazyAdapter, is_library_uri
from ..schemas.analysis import Analysis
from ..schemas.song import Song
from .core import SONG_FILE, Project

__all__ = [
    "BEAT_MATCH_TOLERANCE_S",
    "load_song",
    "dump_song",
    "save_song",
    "load_song_document",
    "save_song_document",
    "edit_song_document",
    "analysis_path",
    "load_analysis",
    "save_analysis",
    "validate_song",
    "chop_times",
]

#: max distance (seconds) between a downbeat and its matching beat on the
#: analyzed grid before it's an error.
BEAT_MATCH_TOLERANCE_S = 0.05

_ANALYSIS_ADAPTER = LazyAdapter(Analysis)

#: ruamel round-trip parser: preserves comments/ordering, rejects
#: duplicate keys (DuplicateKeyError).
_YAML = YAML(typ="rt")
_YAML.preserve_quotes = True
# Never re-wrap long flow-style lines (lanes with library:// URIs) on round-trip edits.
_YAML.width = 4096

_DUP_KEY_RE = re.compile(r'duplicate key "([^"]+)"')


def _fast_loader():
    """libyaml (C) safe loader with YAML 1.2 core-schema scalars, like ruamel.

    Reading song.yaml for validation does not need ruamel's round trip, which is
    ~10x slower in pure Python (0.2-0.3 s on a long song: most of a CLI call).
    Only true/false are booleans (YAML 1.1's yes/no/on/off stay strings), ints are
    decimal/0o/0x, floats include 1e3. Duplicate keys raise; any error falls back
    to the ruamel path, which produces the user-facing messages. None when PyYAML
    has no libyaml.
    """
    try:
        import yaml
    except ImportError:  # pragma: no cover - PyYAML is a dependency
        return None
    base = getattr(yaml, "CSafeLoader", None)
    if base is None:
        return None

    class _Dup(Exception):
        pass

    class Loader(base):  # type: ignore[misc, valid-type]
        pass

    Loader.yaml_implicit_resolvers = {}
    for tag, regexp, first in (
        ("tag:yaml.org,2002:bool", r"^(?:true|True|TRUE|false|False|FALSE)$", "tTfF"),
        ("tag:yaml.org,2002:int", r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$", "-+0123456789"),
        (
            "tag:yaml.org,2002:float",
            r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
            r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$",
            "-+0123456789.",
        ),
        ("tag:yaml.org,2002:null", r"^(?:~|null|Null|NULL|)$", ["~", "n", "N", ""]),
    ):
        Loader.add_implicit_resolver(tag, re.compile(regexp), list(first))

    def int_12(loader, node):
        value = loader.construct_scalar(node)
        return int(value, 0) if value[:2] in ("0o", "0x") else int(value)

    def mapping(loader, node, deep=False):
        loader.flatten_mapping(node)
        seen = set()
        for key_node, _ in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in seen:
                raise _Dup(key)
            seen.add(key)
        return loader.construct_mapping(node, deep=deep)

    Loader.add_constructor("tag:yaml.org,2002:int", int_12)
    Loader.add_constructor("tag:yaml.org,2002:map", mapping)
    return yaml, Loader


_FAST = _fast_loader()


def _plain_doc(path: Path, label: str):
    """song.yaml as plain Python data, for validation only (never written back)."""
    if _FAST is not None:
        yaml, loader = _FAST
        try:
            doc = yaml.load(path.read_text(encoding="utf-8"), Loader=loader)  # noqa: S506
        except Exception:  # noqa: BLE001 - the ruamel path reports it properly
            doc = Ellipsis
        if doc is None:
            return {}
        if isinstance(doc, dict):
            return doc
    return _yaml_doc(path, label)


def _yaml_doc(path: Path, label: str) -> CommentedMap:
    """Round-trip parse of a YAML file; duplicate keys are errors."""
    try:
        doc = _YAML.load(path.read_text(encoding="utf-8"))
    except DuplicateKeyError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        m = _DUP_KEY_RE.search(getattr(e, "problem", "") or "")
        raise MuestraError.one(
            "duplicate_key",
            f"Duplicate YAML key {m.group(1) if m else '?'!r} in {label}{where}.",
            path=label,
            hint="Remove the duplicated key; later values would silently override earlier ones.",
        ) from e
    except RuamelError as e:
        raise MuestraError.one(
            "parse_error",
            f"Invalid YAML in {label}: {e}",
            path=label,
            hint="Fix the YAML syntax.",
        ) from e
    if doc is None:
        doc = CommentedMap()
    if not isinstance(doc, CommentedMap):
        raise MuestraError.one(
            "invalid_type",
            f"{label} must contain a YAML mapping at the top level.",
            path=label,
            hint="song.yaml is a mapping of meta/sources/chops/tracks/...",
        )
    return doc


def load_song(project: Project) -> Song:
    """Read and parse song.yaml. Raises MuestraError listing ALL parse issues."""
    path = project.song_path
    if not path.is_file():
        raise MuestraError.one(
            "file_not_found",
            f"{SONG_FILE} does not exist in {project.root}.",
            path=SONG_FILE,
            hint="Create a song.yaml, or create the project with project.create.",
        )
    doc = _plain_doc(path, SONG_FILE)
    try:
        return Song.model_validate(doc)
    except ValidationError as e:
        raise MuestraError(issues_from_validation_error(e)) from e


def load_song_document(project: Project) -> CommentedMap:
    """Round-trip representation of song.yaml for surgical edits.

    Tools that edit the song programmatically (``source.import``
    registering a source, ``chops.choose`` writing a chop) MUST use this
    + :func:`save_song_document` so comments, ordering and omitted
    defaults survive — never a full ``dump_song`` rewrite.
    """
    path = project.song_path
    if not path.is_file():
        raise MuestraError.one(
            "file_not_found",
            f"{SONG_FILE} does not exist in {project.root}.",
            path=SONG_FILE,
            hint="Create a song.yaml, or create the project with project.create.",
        )
    return _yaml_doc(path, SONG_FILE)


def save_song_document(project: Project, doc: CommentedMap) -> Path:
    """Write a round-trip document back to song.yaml."""
    path = project.song_path
    with path.open("w", encoding="utf-8", newline="\n") as f:
        _YAML.dump(doc, f)
    return path


def edit_song_document(project: Project, mutator: Callable[[CommentedMap], None]) -> Path:
    """Load song.yaml round-trip, apply ``mutator`` to the mapping, save."""
    doc = load_song_document(project)
    mutator(doc)
    return save_song_document(project, doc)


def dump_song(song: Song) -> str:
    """Serialize a Song back to YAML (round-trip stable)."""
    return yaml.safe_dump(
        song.model_dump(mode="json", by_alias=True, exclude_none=False),
        sort_keys=False,
        allow_unicode=True,
    )


def save_song(project: Project, song: Song) -> Path:
    project.song_path.write_text(dump_song(song), encoding="utf-8", newline="\n")
    return project.song_path


def analysis_path(project: Project, source_id: str) -> Path:
    return project.cache_dir / "analysis" / f"{source_id}.json"


def load_analysis(project: Project, source_id: str) -> Analysis:
    """Load cache/analysis/<source_id>.json. MuestraError if missing/invalid."""
    path = analysis_path(project, source_id)
    if not path.is_file():
        raise MuestraError.one(
            "file_not_found",
            f"No analysis for source {source_id!r} at cache/analysis/{source_id}.json.",
            value=source_id,
            hint="Run source.analyze first.",
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise MuestraError.one(
            "parse_error",
            f"Invalid JSON in {path.name}: {e}",
            path=f"cache/analysis/{source_id}.json",
            hint="Regenerate it with source.analyze.",
        ) from e
    try:
        return _ANALYSIS_ADAPTER.validate_python(raw)
    except ValidationError as e:
        raise MuestraError(
            issues_from_validation_error(e, path_prefix=f"cache/analysis/{source_id}.json")
        ) from e


def save_analysis(project: Project, analysis: Analysis) -> Path:
    path = analysis_path(project, analysis.source_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(analysis.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _check_files_exist(project: Project, song: Song) -> list[Issue]:
    issues: list[Issue] = []

    def check_ref(ref: str, path: str) -> None:
        try:
            project.resolve(ref, must_exist=True)
        except MuestraError as e:
            for issue in e.issues:
                issues.append(issue.model_copy(update={"path": path}))

    for i, source in enumerate(song.sources):
        check_ref(source.file, f"sources[{i}].file")
    for ti, track in enumerate(song.tracks):
        if track.clips:
            for ci, clip in enumerate(track.clips):
                if clip.sample is not None:
                    check_ref(clip.sample, f"tracks[{ti}].clips[{ci}].sample")
        if track.pattern:
            for lane, spec in track.pattern.lanes.items():
                check_ref(spec.sample, f"tracks[{ti}].pattern.lanes.{lane}.sample")
        if track.midi is not None:
            check_ref(track.midi, f"tracks[{ti}].midi")
    return issues


def _beat_index_at(beats_s: list[float], t: float) -> tuple[int, float]:
    """Nearest beats_s index to time t and its absolute distance."""
    i = bisect.bisect_left(beats_s, t)
    candidates = [j for j in (i - 1, i) if 0 <= j < len(beats_s)]
    best = min(candidates, key=lambda j: abs(beats_s[j] - t))
    return best, abs(beats_s[best] - t)


def _interpolate_beats(beats_s: list[float], pos: float) -> float | None:
    """beats_s at fractional index ``pos``; None when outside the grid.

    Positions just before the first beat (down to -1) extrapolate
    linearly from the first interval — ``start_offset_beats`` may push a
    chop start before the first analyzed beat — but never before the
    audio start. The high end is strict: a chop may not end past the
    analyzed grid.
    """
    n = len(beats_s)
    if pos > n - 1 + 1e-9 or pos < -1.0 - 1e-9:
        return None
    if pos < 0.0:
        if n < 2:
            return None
        step = beats_s[1] - beats_s[0]
        result = beats_s[0] + pos * step
        return result if result >= -1e-9 else None
    pos = min(pos, float(n - 1))
    whole = int(pos)
    frac = pos - whole
    if frac < 1e-9 or whole + 1 >= n:
        return beats_s[whole]
    a, b = beats_s[whole], beats_s[whole + 1]
    return a + frac * (b - a)


def chop_times(
    analysis: Analysis,
    start_downbeat: int,
    length_beats: float,
    start_offset_beats: float = 0.0,
) -> tuple[float, float] | None:
    """Resolve a chop's [start_s, end_s) inside the source audio.

    ``start_downbeat`` is a 1-based index into ``downbeats_s``; the
    start is taken from ``beats_s`` at the beat NEAREST that downbeat
    (one grid, not two). ``start_offset_beats`` shifts the start along
    ``beats_s`` from there (may be negative, fractional interpolates).
    ``end_s`` walks ``length_beats`` forward on the same grid.
    Returns None when the request goes out of bounds.
    """
    if not (1 <= start_downbeat <= len(analysis.downbeats_s)):
        return None
    downbeat_s = analysis.downbeats_s[start_downbeat - 1]
    beat_i, dist = _beat_index_at(analysis.beats_s, downbeat_s)
    if dist > BEAT_MATCH_TOLERANCE_S:
        return None
    start_pos = beat_i + start_offset_beats
    start_s = _interpolate_beats(analysis.beats_s, start_pos)
    if start_s is None:
        return None
    end_s = _interpolate_beats(analysis.beats_s, start_pos + length_beats)
    if end_s is None:
        return None
    return start_s, end_s


def _check_chops_vs_analysis(project: Project, song: Song) -> list[Issue]:
    issues: list[Issue] = []
    analyses: dict[str, Analysis | None] = {}

    def get_analysis(source_id: str) -> Analysis | None:
        if source_id not in analyses:
            try:
                analyses[source_id] = load_analysis(project, source_id)
            except MuestraError:
                analyses[source_id] = None
        return analyses[source_id]

    source_ids = {s.id for s in song.sources}
    for ci, chop in enumerate(song.chops):
        if chop.source not in source_ids:
            continue  # already reported by Song.check
        analysis = get_analysis(chop.source)
        base = f"chops[{ci}]"
        if analysis is None:
            issues.append(
                Issue(
                    path=base,
                    code="file_not_found",
                    message=f"Chop {chop.id!r} needs cache/analysis/"
                    f"{chop.source}.json which does not exist.",
                    hint="Run source.analyze on the source, or validate with check_files=False.",
                    value=chop.id,
                )
            )
            continue
        if chop.stem != "mix":
            stems = analysis.stems
            if stems is None or getattr(stems, chop.stem, None) is None:
                issues.append(
                    Issue(
                        path=f"{base}.stem",
                        code="ref_not_found",
                        message=f"Chop {chop.id!r} uses stem {chop.stem!r} but "
                        f"the analysis has no such stem.",
                        hint="Run source.separate, pick another stem, or use 'mix'.",
                        value=chop.stem,
                    )
                )
            else:
                stem_path = getattr(stems, chop.stem)
                if stem_path and not is_library_uri(stem_path):
                    if not (project.root / stem_path).is_file():
                        issues.append(
                            Issue(
                                path=f"{base}.stem",
                                code="file_not_found",
                                message=f"Stem file {stem_path!r} for chop "
                                f"{chop.id!r} does not exist.",
                                hint="Run source.separate to regenerate the stems.",
                                value=stem_path,
                            )
                        )
        downbeat_s = None
        if 1 <= chop.start_downbeat <= len(analysis.downbeats_s):
            downbeat_s = analysis.downbeats_s[chop.start_downbeat - 1]
        if downbeat_s is None:
            issues.append(
                Issue(
                    path=f"{base}.start_downbeat",
                    code="out_of_range",
                    message=f"Chop {chop.id!r}: start_downbeat "
                    f"{chop.start_downbeat} but the source only has "
                    f"{len(analysis.downbeats_s)} downbeats.",
                    hint=f"Pick a value in [1, {len(analysis.downbeats_s)}].",
                    value=chop.start_downbeat,
                )
            )
            continue
        beat_i, dist = _beat_index_at(analysis.beats_s, downbeat_s)
        if dist > BEAT_MATCH_TOLERANCE_S:
            issues.append(
                Issue(
                    path=f"{base}.start_downbeat",
                    code="grid_mismatch",
                    message=f"Chop {chop.id!r}: downbeat at {downbeat_s:.3f}s is "
                    f"{dist * 1000:.0f} ms off the nearest beat "
                    f"(tolerance {BEAT_MATCH_TOLERANCE_S * 1000:.0f} ms).",
                    hint="Check the analysis; downbeats should coincide with beats.",
                    value=chop.start_downbeat,
                )
            )
            continue
        start_pos = beat_i + chop.start_offset_beats
        if _interpolate_beats(analysis.beats_s, start_pos) is None:
            issues.append(
                Issue(
                    path=f"{base}.start_offset_beats",
                    code="out_of_range",
                    message=f"Chop {chop.id!r}: start_offset_beats "
                    f"{chop.start_offset_beats:g} lands at beat {start_pos:g} "
                    f"but the analyzed grid has {len(analysis.beats_s)} beats.",
                    hint="Use a smaller offset; negative values can't go before the audio start.",
                    value=chop.start_offset_beats,
                )
            )
            continue
        end_pos = start_pos + chop.length_beats
        if end_pos > len(analysis.beats_s) - 1 + 1e-9:
            issues.append(
                Issue(
                    path=f"{base}.length_beats",
                    code="chop_overrun",
                    message=f"Chop {chop.id!r} needs {chop.length_beats:g} "
                    f"beats from beat {start_pos:g} but the analyzed grid "
                    f"only has {len(analysis.beats_s)} beats.",
                    hint="Shorten 'length_beats', reduce 'start_offset_beats', "
                    "or pick an earlier 'start_downbeat'.",
                    value=chop.length_beats,
                )
            )
    return issues


def _check_sample_warps(project: Project, song: Song) -> list[Issue]:
    """A sample clip's ``warp`` needs a known BPM.

    ``library://`` samples get their BPM from the library index
    (``.muestra-index.json`` inside the root); project paths and
    unindexed samples have no BPM and play at native speed — asking for
    a warp on them is a validation error.
    """
    from ..library.core import lookup

    issues: list[Issue] = []
    for ti, track in enumerate(song.tracks):
        if not track.clips:
            continue
        for ci, clip in enumerate(track.clips):
            if clip.sample is None or clip.warp is None:
                continue
            base = f"tracks[{ti}].clips[{ci}]"
            bpm: float | None = None
            if is_library_uri(clip.sample):
                hit = lookup(project, clip.sample)
                bpm = hit.bpm if hit is not None else None
            if bpm is None:
                issues.append(
                    Issue(
                        path=f"{base}.warp",
                        code="invalid_value",
                        message=f"Clip sample {clip.sample!r} has no known "
                        "BPM, so it cannot be warped.",
                        hint="Remove 'warp' (the sample plays at native "
                        "speed), or index it with library.index first.",
                        value=clip.sample,
                    )
                )
    return issues


def validate_song(project: Project, *, check_files: bool = True) -> list[Issue]:
    """All issues for the project's song.yaml. Never raises for invalid songs.

    Returns the parse issues alone when the file can't be parsed at all.
    """
    try:
        song = load_song(project)
    except MuestraError as e:
        return e.issues
    issues = song.check()
    if check_files:
        issues += _check_files_exist(project, song)
        issues += _check_chops_vs_analysis(project, song)
        issues += _check_sample_warps(project, song)
    return issues
