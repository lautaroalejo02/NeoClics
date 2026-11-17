"""song.yaml loading and validation.

Two levels, deliberately separable:

* structural + semantic (always): pydantic parse -> :meth:`Song.check`
  (cross references, ids, automation targets, clip overrun, pattern
  lengths);
* filesystem-aware (``check_files=True``): referenced files exist
  (sources, samples, midi) and chops are checked against the source's
  ``cache/analysis/<source_id>.json`` grid (``start_downbeat`` bounds,
  downbeat-on-beat match, chop end inside the analyzed beats).

``load_analysis``/``save_analysis`` and ``load_song``/``dump_song`` are
also used by later WPs that write these files.
"""

from __future__ import annotations

import bisect
import json
from pathlib import Path

import yaml
from pydantic import TypeAdapter, ValidationError

from ..errors import Issue, MuestraError, issues_from_validation_error
from ..schemas._base import is_library_uri
from ..schemas.analysis import Analysis
from ..schemas.song import Song
from .core import SONG_FILE, Project

__all__ = [
    "BEAT_MATCH_TOLERANCE_S",
    "load_song",
    "dump_song",
    "save_song",
    "analysis_path",
    "load_analysis",
    "save_analysis",
    "validate_song",
    "chop_times",
]

#: max distance (seconds) between a downbeat and its matching beat on the
#: analyzed grid before it's an error.
BEAT_MATCH_TOLERANCE_S = 0.05

_ANALYSIS_ADAPTER = TypeAdapter(Analysis)


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
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise MuestraError.one(
            "parse_error",
            f"Invalid YAML in {SONG_FILE}: {e}",
            path=SONG_FILE,
            hint="Fix the YAML syntax.",
        ) from e
    try:
        return Song.model_validate(raw)
    except ValidationError as e:
        raise MuestraError(issues_from_validation_error(e)) from e


def dump_song(song: Song) -> str:
    """Serialize a Song back to YAML (round-trip stable)."""
    return yaml.safe_dump(
        song.model_dump(mode="json", by_alias=True, exclude_none=False),
        sort_keys=False,
        allow_unicode=True,
    )


def save_song(project: Project, song: Song) -> Path:
    project.song_path.write_text(dump_song(song), encoding="utf-8")
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


def chop_times(
    analysis: Analysis, start_downbeat: int, length_beats: float
) -> tuple[float, float] | None:
    """Resolve a chop's [start_s, end_s) inside the source audio.

    start_s = downbeats_s[start_downbeat - 1] (1-indexed). end_s walks
    ``length_beats`` forward on the beats_s grid from the downbeat's
    matching beat; a fractional tail interpolates linearly between beats.
    Returns None when the request goes out of bounds.
    """
    if not (1 <= start_downbeat <= len(analysis.downbeats_s)):
        return None
    start_s = analysis.downbeats_s[start_downbeat - 1]
    beat_i, dist = _beat_index_at(analysis.beats_s, start_s)
    if dist > BEAT_MATCH_TOLERANCE_S:
        return None
    end_pos = beat_i + length_beats
    if end_pos > len(analysis.beats_s) - 1:
        return None
    whole = int(end_pos)
    frac = end_pos - whole
    if frac < 1e-9:
        end_s = analysis.beats_s[whole]
    else:
        if whole + 1 >= len(analysis.beats_s):
            return None
        a, b = analysis.beats_s[whole], analysis.beats_s[whole + 1]
        end_s = a + frac * (b - a)
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
        start_s = None
        if 1 <= chop.start_downbeat <= len(analysis.downbeats_s):
            start_s = analysis.downbeats_s[chop.start_downbeat - 1]
        if start_s is None:
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
        beat_i, dist = _beat_index_at(analysis.beats_s, start_s)
        if dist > BEAT_MATCH_TOLERANCE_S:
            issues.append(
                Issue(
                    path=f"{base}.start_downbeat",
                    code="grid_mismatch",
                    message=f"Chop {chop.id!r}: downbeat at {start_s:.3f}s is "
                    f"{dist * 1000:.0f} ms off the nearest beat "
                    f"(tolerance {BEAT_MATCH_TOLERANCE_S * 1000:.0f} ms).",
                    hint="Check the analysis; downbeats should coincide with beats.",
                    value=chop.start_downbeat,
                )
            )
            continue
        end_pos = beat_i + chop.length_beats
        if end_pos > len(analysis.beats_s) - 1 + 1e-9:
            issues.append(
                Issue(
                    path=f"{base}.length_beats",
                    code="chop_overrun",
                    message=f"Chop {chop.id!r} needs {chop.length_beats:g} "
                    f"beats from beat index {beat_i} but the analyzed grid "
                    f"only has {len(analysis.beats_s)} beats.",
                    hint="Shorten 'length_beats' or pick an earlier 'start_downbeat'.",
                    value=chop.length_beats,
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
    return issues
