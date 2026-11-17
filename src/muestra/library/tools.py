"""Tools owned by WP4 (sample library): ``library.index``,
``library.search``, ``library.kits``, ``library.info``.

Resolution seam: ``muestra.library.core.lookup``/``resolve`` read the
``.muestra-index.json`` inside each library root (roots come from
``muestra.library.discovery``: ``muestra.toml``, then ``MUESTRA_LIBRARY``,
then ``~/Documents/MuestraLibrary``).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import Field

from ..errors import MuestraError
from ..project import open_project, save_config
from ..schemas.library import LibraryHit
from ..toolbase import ProjectScoped, ToolOutput
from .core import loaded_index, root_label
from .discovery import ENV_VAR, default_library_path
from .index import IndexReport, index_root, summarize
from .kits import kit_pattern, kit_reports
from .search import search

__all__ = [
    "TOOLS",
    "KitInfo",
    "KitLane",
    "LibraryIndexParams",
    "LibraryIndexResult",
    "LibraryInfoParams",
    "LibraryInfoResult",
    "LibraryKitsParams",
    "LibraryKitsResult",
    "LibrarySearchParams",
    "LibrarySearchResult",
    "RootIndexReport",
    "library_index",
    "library_info",
    "library_kits",
    "library_search",
]

_NO_LIBRARY_HINT = (
    "No library found. Point one out with library.index(path=<dir of samples>), "
    f"set the {ENV_VAR} environment variable, add a root under [library] in "
    f"muestra.toml, or create {default_library_path()}."
)


# ---------------------------------------------------------------------------
# library.index
# ---------------------------------------------------------------------------


class LibraryIndexParams(ProjectScoped):
    path: str | None = Field(
        default=None,
        description="Library root to index. Added to muestra.toml [library] roots when it is "
        "not already one (see add_root). Omit to (re)index every configured root.",
    )
    force: bool = Field(
        default=False, description="Re-measure every file instead of only new/changed ones."
    )
    add_root: bool = Field(
        default=True, description="Add `path` to muestra.toml [library] roots if it is new."
    )
    workers: int | None = Field(
        default=None, ge=1, le=64, description="Worker processes (default min(cpu, 8))."
    )


class RootIndexReport(ToolOutput):
    root: str
    index_path: str
    scanned: int = Field(ge=0, description="Audio files found.")
    entries: int = Field(ge=0, description="Entries in the index after the run.")
    analyzed: int = Field(ge=0, description="Files measured this run (new + changed).")
    new: int = Field(ge=0)
    changed: int = Field(ge=0)
    reused: int = Field(ge=0, description="Unchanged files kept from the previous index.")
    removed: int = Field(ge=0, description="Entries dropped because the file is gone.")
    seconds: float
    workers: int
    by_type: dict[str, int] = Field(default_factory=dict)
    by_kind: dict[str, int] = Field(default_factory=dict)
    by_kit: dict[str, int] = Field(default_factory=dict)
    flagged: int = Field(default=0, description="Entries carrying at least one flag.")
    flag_counts: dict[str, int] = Field(default_factory=dict)
    clipping: int = 0
    type_mismatches: int = Field(
        default=0, description="Files whose name says one type but whose audio says another."
    )
    problems_total: int = 0
    problems: list[dict[str, str]] = Field(
        default_factory=list, description="Unreadable files (first 20): {path, message}."
    )


class LibraryIndexResult(ToolOutput):
    indexed: int = Field(ge=0, description="Total entries across the indexed roots.")
    index_path: str = Field(
        description="Path of the (first) index file ('.muestra-index.json' inside the root)."
    )
    roots: list[RootIndexReport] = Field(default_factory=list)
    registered: list[str] = Field(
        default_factory=list, description="Roots added to muestra.toml by this call."
    )
    hint: str | None = None


def _root_report(rep: IndexReport) -> RootIndexReport:
    s = summarize(rep.index)
    return RootIndexReport(
        root=str(rep.root),
        index_path=str(rep.index_path),
        scanned=rep.scanned,
        entries=s.entries,
        analyzed=rep.analyzed,
        new=rep.new,
        changed=rep.changed,
        reused=rep.reused,
        removed=rep.removed,
        seconds=round(rep.seconds, 2),
        workers=rep.workers,
        by_type=s.by_type,
        by_kind=s.by_kind,
        by_kit=s.by_kit,
        flagged=s.flagged,
        flag_counts=s.flag_counts,
        clipping=s.clipping,
        type_mismatches=s.type_mismatches,
        problems_total=len(rep.problems),
        problems=[{"path": p.path, "message": p.message} for p in rep.problems[:20]],
    )


def library_index(
    p: LibraryIndexParams, progress: Callable[[float, str | None], None] | None = None
) -> LibraryIndexResult:
    """Index a sample library into <root>/.muestra-index.json (incremental, parallel).

    Per file: type, one-shot/loop, BPM, key, loudness, clipping, descriptive audio features
    (pitch, decay, brightness, ...) and derived tags; per kit: the licence summary.
    """
    project = open_project(p.project)
    registered: list[str] = []
    if p.path is not None:
        target = Path(p.path).expanduser()
        if not target.is_absolute():
            target = (project.root / target).resolve()
        if not target.is_dir():
            raise MuestraError.one(
                "file_not_found",
                f"Library path {p.path!r} is not a directory.",
                value=p.path,
                hint="Pass the folder that holds the samples (it may contain subfolders).",
            )
        roots = [target.resolve()]
        known = {os.path.normcase(str(r.resolve())) for r in project.library_dirs()}
        if p.add_root and os.path.normcase(str(target.resolve())) not in known:
            # muestra.toml wants '/' separators; keep relative paths relative
            entry = (
                target.as_posix()
                if Path(p.path).expanduser().is_absolute()
                else (Path(p.path).as_posix())
            )
            cfg = project.config.model_copy(deep=True)
            cfg.library.roots = [*cfg.library.roots, entry]
            save_config(project, cfg)
            registered.append(entry)
    else:
        roots = [r for r in project.library_dirs() if r.is_dir()]
        if not roots:
            raise MuestraError.one(
                "no_library", "No library roots to index.", hint=_NO_LIBRARY_HINT
            )

    reports = []
    for number, r in enumerate(roots):
        per_file = None
        if progress is not None:
            # The transports' convention is progress(fraction 0..1, message);
            # index_root reports (done, total, rel_path) per root.
            def per_file(done, total, rel, number=number, root=r):
                fraction = (number + (done / total if total else 1.0)) / len(roots)
                progress(fraction, f"{root.name}: {done}/{total} {rel}".rstrip())

        reports.append(
            _root_report(index_root(r, progress=per_file, workers=p.workers, force=p.force))
        )
    problems = sum(r.problems_total for r in reports)
    hint = f"{problems} file(s) could not be read; see roots[].problems." if problems else None
    return LibraryIndexResult(
        indexed=sum(r.entries for r in reports),
        index_path=reports[0].index_path,
        roots=reports,
        registered=registered,
        hint=hint,
    )


# ---------------------------------------------------------------------------
# library.search
# ---------------------------------------------------------------------------


class LibrarySearchParams(ProjectScoped):
    query: str = Field(
        default="",
        description="Free text, English or Spanish: 'punchy 909 kick', 'bombo seco', "
        "'open hat', 'palmas', 'conga loop 120'. Empty lists everything.",
    )
    bpm: float | None = Field(
        default=None, gt=0, description="Only loops/phrases near this BPM (half/double time too)."
    )
    key: str | None = Field(
        default=None, description="Only tonal samples in a compatible key, e.g. 'F#m'."
    )
    type: str | None = Field(
        default=None,
        description="Instrument: kick|snare|clap|chat|ohat|hat|tom|cym|rim|perc|drums|fill|"
        "bass|keys|synth|guitar|pad|vox|fx. Aliases like 'bombo' work; comma-separate several.",
    )
    subtype: str | None = Field(
        default=None, description="e.g. conga|cowbell|crash|ride|roll|sidestick."
    )
    kind: Literal["one-shot", "loop", "phrase"] | None = None
    kit: str | None = Field(
        default=None, description="Substring of a folder name, e.g. 'tr8', '80s-pop', 'tr-909-kit'."
    )
    tags: list[str] = Field(
        default_factory=list, description="Tags that must ALL be present (e.g. ['909', 'punchy'])."
    )
    duration_min: float | None = Field(default=None, ge=0, description="Seconds.")
    duration_max: float | None = Field(default=None, ge=0, description="Seconds.")
    similar_to: str | None = Field(
        default=None,
        description="A library:// URI: rank by closeness of measured sound features to it.",
    )
    bpm_tolerance: float = Field(default=0.04, gt=0, le=0.5, description="Fraction, 0.04 = 4%.")
    half_double: bool = Field(default=True, description="Also match half/double-time BPMs.")
    key_exact: bool = Field(default=False, description="Exact key only (no relative/fifth keys).")
    clean_only: bool = Field(
        default=False, description="Drop clipped / very quiet / mislabelled / BPM-disputed files."
    )
    detail: Literal["compact", "full"] = Field(
        default="compact",
        description="'full' adds measured features and licence terms to every hit.",
    )
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class LibrarySearchResult(ToolOutput):
    results: list[LibraryHit] = Field(default_factory=list)
    total: int = Field(default=0, ge=0, description="Matches before limit/offset.")
    interpreted: list[str] = Field(
        default_factory=list, description="How the query was understood (filters / preferences)."
    )
    searched_roots: list[str] = Field(default_factory=list)
    unindexed_roots: list[str] = Field(default_factory=list)
    hint: str | None = None


def library_search(p: LibrarySearchParams) -> LibrarySearchResult:
    """Search the library, ranked with reasons.

    Free text in English or Spanish, filters (type, kit, tags, BPM incl. half/double time,
    compatible keys, duration) and "similar to <uri>" by measured sound features.
    """
    project = open_project(p.project)
    outcome = search(
        project,
        query=p.query,
        bpm=p.bpm,
        bpm_tolerance=p.bpm_tolerance,
        half_double=p.half_double,
        key=p.key,
        key_exact=p.key_exact,
        type=p.type,
        subtype=p.subtype,
        kind=p.kind,
        kit=p.kit,
        tags=p.tags,
        duration_min=p.duration_min,
        duration_max=p.duration_max,
        similar=p.similar_to,
        clean_only=p.clean_only,
        detail=p.detail,
        limit=p.limit,
        offset=p.offset,
    )
    if not outcome.searched_roots:
        if outcome.unindexed_roots:
            raise MuestraError.one(
                "library_not_indexed",
                "Library root(s) not indexed yet: " + ", ".join(outcome.unindexed_roots),
                hint="Run library.index first (it takes a few minutes for thousands of files).",
            )
        raise MuestraError.one(
            "no_library", "No library roots are configured.", hint=_NO_LIBRARY_HINT
        )

    hints: list[str] = list(outcome.notes)
    if outcome.unindexed_roots:
        hints.append("Not indexed (run library.index): " + ", ".join(outcome.unindexed_roots))
    if outcome.total == 0:
        if p.bpm is not None or p.key is not None:
            hints.append(
                "bpm/key filters only match loops and phrases (one-shots carry neither); "
                "drop them to search one-shots."
            )
        else:
            hints.append("No matches. Try fewer words, or a type like 'kick' / 'bombo'.")
    elif outcome.total > p.offset + len(outcome.results):
        hints.append(f"{outcome.total} matches; raise offset/limit for more, or add filters.")
    return LibrarySearchResult(
        results=outcome.results,
        total=outcome.total,
        interpreted=outcome.interpreted,
        searched_roots=outcome.searched_roots,
        unindexed_roots=outcome.unindexed_roots,
        hint=" ".join(hints) or None,
    )


# ---------------------------------------------------------------------------
# library.kits
# ---------------------------------------------------------------------------


class LibraryKitsParams(ProjectScoped):
    name: str | None = Field(
        default=None, description="Kit to show in full; omit to list all starter kits."
    )
    steps_per_bar: int = Field(default=16, ge=4, le=64)


class KitLane(ToolOutput):
    lane: str
    uri: str
    present: bool = Field(description="The file exists in the library.")
    type: str | None = None
    sound: str | None = Field(default=None, description="Measured one-line description.")


class KitInfo(ToolOutput):
    name: str
    description: str = ""
    notes: str | None = None
    available: bool = Field(description="Every lane's sample exists in the library.")
    source: str = Field(description="Manifest the kit came from.")
    lanes: list[KitLane] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    restrictions: list[str] = Field(
        default_factory=list,
        description="Licence restrictions of the kits the samples come from.",
    )
    pattern: dict | None = Field(
        default=None,
        description="Ready-to-paste `pattern:` for a drum track (four-on-the-floor steps).",
    )


class LibraryKitsResult(ToolOutput):
    kits: list[KitInfo] = Field(default_factory=list)
    caveat: str = (
        "Starter kits were chosen from measured features and source reputation; "
        "nobody has listened to them."
    )
    hint: str | None = None


def library_kits(p: LibraryKitsParams) -> LibraryKitsResult:
    """List starter drum kits (lane -> sample); with a name, add a ready-to-paste drum pattern."""
    project = open_project(p.project)
    reports = kit_reports(project, p.name)
    if not reports:
        raise MuestraError.one(
            "no_library",
            "No starter kits found in any library root.",
            hint="Add a starter-kits.json to a library root (see docs/library.md), or "
            + _NO_LIBRARY_HINT,
        )
    kits: list[KitInfo] = []
    for r in reports:
        kits.append(
            KitInfo(
                name=r.name,
                description=r.kit.description,
                notes=r.kit.notes,
                available=r.available,
                source=r.source,
                lanes=[
                    KitLane(
                        lane=lane.lane,
                        uri=lane.uri,
                        present=lane.present,
                        type=lane.type,
                        sound=lane.summary if p.name else None,
                    )
                    for lane in r.lanes
                ],
                warnings=r.warnings,
                restrictions=r.restrictions,
                pattern=kit_pattern(r.kit, steps_per_bar=p.steps_per_bar) if p.name else None,
            )
        )
    hint = None
    if not p.name:
        hint = "Pass name=<kit> to get the full lane list and a ready-to-paste pattern."
    return LibraryKitsResult(kits=kits, hint=hint)


# ---------------------------------------------------------------------------
# library.info
# ---------------------------------------------------------------------------


class LibraryInfoParams(ProjectScoped):
    pass


class LibraryRootInfo(ToolOutput):
    root: str
    indexed: bool
    entries: int = 0
    indexed_at: str | None = None
    by_type: dict[str, int] = Field(default_factory=dict)
    by_kind: dict[str, int] = Field(default_factory=dict)
    kits: dict[str, dict] = Field(
        default_factory=dict, description="kit -> {files, restrictions, summary}"
    )
    problems: int = 0


class LibraryInfoResult(ToolOutput):
    roots: list[LibraryRootInfo] = Field(default_factory=list)
    hint: str | None = None


def library_info(p: LibraryInfoParams) -> LibraryInfoResult:
    """What is in the library: roots, index status, counts per type/kind/kit, licence limits."""
    project = open_project(p.project)
    out: list[LibraryRootInfo] = []
    missing = False
    for root in project.library_dirs():
        label = root_label(project, root)
        loaded = loaded_index(root)
        if loaded is None:
            missing = True
            out.append(LibraryRootInfo(root=label, indexed=False))
            continue
        s = summarize(loaded.index)
        kits = {
            k: {
                "files": s.by_kit.get(k, 0),
                "restrictions": lic.restrictions,
                "summary": lic.summary,
            }
            for k, lic in loaded.index.kits.items()
        }
        out.append(
            LibraryRootInfo(
                root=label,
                indexed=True,
                entries=s.entries,
                indexed_at=loaded.index.indexed_at.isoformat() if loaded.index.indexed_at else None,
                by_type=s.by_type,
                by_kind=s.by_kind,
                kits=kits,
                problems=len(loaded.index.problems),
            )
        )
    hint = None
    if not out:
        hint = _NO_LIBRARY_HINT
    elif missing:
        hint = "Some roots are not indexed: run library.index."
    return LibraryInfoResult(roots=out, hint=hint)


#: tools exported by this subpackage (collected by muestra.registry).
TOOLS = [
    (
        "library.index",
        library_index,
        LibraryIndexParams,
        LibraryIndexResult,
        {"long_running": True, "cli": ("library", "index")},
    ),
    (
        "library.search",
        library_search,
        LibrarySearchParams,
        LibrarySearchResult,
        {"readonly": True, "cli": ("library", "search")},
    ),
    (
        "library.kits",
        library_kits,
        LibraryKitsParams,
        LibraryKitsResult,
        {"readonly": True, "cli": ("library", "kits")},
    ),
    (
        "library.info",
        library_info,
        LibraryInfoParams,
        LibraryInfoResult,
        {"readonly": True, "cli": ("library", "info")},
    ),
]
