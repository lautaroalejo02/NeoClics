"""Incremental, parallel library indexer (WP4).

Scans a library root for audio files, measures each one
(:mod:`.audio`), classifies it (:mod:`.classify`) and writes
``.muestra-index.json`` inside the root.

Incremental: an entry is reused untouched when the file's size and mtime
are unchanged (and the analyzer version matches); a file whose mtime
changed but whose size and cheap content hash (size + first/last 64 KiB)
did not is also reused. Everything else is re-probed in a process pool.
Unreadable files are reported in ``problems`` and never abort the run.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..errors import MuestraError
from ..schemas.library import (
    ANALYZER_VERSION,
    IndexProblem,
    KitLicense,
    LibraryIndex,
    LibraryIndexEntry,
)
from .audio import AUDIO_EXTENSIONS, probe
from .classify import classify, kit_dir, parse_name, probe_needs
from .core import index_path, load_index
from .licence import parse_license

__all__ = ["IndexReport", "IndexSummary", "index_root", "scan_audio_files", "summarize"]

_SIG_BYTES = 64 * 1024

#: top-level directories never indexed (``_*`` and ``.*`` are skipped too):
#: instrument banks (SFZ/VST sample sets) belong to the plugin host, not to
#: the drum/loop library.
IGNORED_TOP_DIRS = frozenset({"instruments"})

#: below this many files to analyse the pool is not worth its start-up.
_POOL_MIN_FILES = 24

ProgressFn = Callable[[int, int, str], None]


@dataclass
class IndexSummary:
    """Headline numbers for an index (also used by ``library.info``)."""

    entries: int
    by_type: dict[str, int]
    by_kind: dict[str, int]
    by_kit: dict[str, int]
    flagged: int
    flag_counts: dict[str, int]
    clipping: int
    type_mismatches: int


@dataclass
class IndexReport:
    """What an index run did."""

    root: Path
    index_path: Path
    scanned: int
    analyzed: int  # freshly measured (new + changed)
    new: int
    changed: int
    reused: int
    removed: int
    problems: list[IndexProblem]
    seconds: float
    workers: int
    index: LibraryIndex = field(repr=False, default_factory=LibraryIndex)

    def summary(self) -> IndexSummary:
        return summarize(self.index)


def summarize(index: LibraryIndex) -> IndexSummary:
    flag_counts: Counter[str] = Counter()
    flagged = 0
    for e in index.entries:
        if e.flags:
            flagged += 1
        for f in e.flags:
            flag_counts[f.split(":", 1)[0]] += 1
    return IndexSummary(
        entries=len(index.entries),
        by_type=dict(Counter(e.type or "unknown" for e in index.entries).most_common()),
        by_kind=dict(Counter(e.kind or "unknown" for e in index.entries).most_common()),
        by_kit=dict(Counter(e.kit or "." for e in index.entries).most_common()),
        flagged=flagged,
        flag_counts=dict(flag_counts.most_common()),
        clipping=sum(1 for e in index.entries if e.clipping),
        type_mismatches=flag_counts.get("type-mismatch", 0),
    )


# ---------------------------------------------------------------------------
# scanning
# ---------------------------------------------------------------------------


def scan_audio_files(root: Path) -> list[str]:
    """Root-relative POSIX paths of audio files under ``root``.

    Directories starting with ``_`` or ``.`` (tooling, archives, the
    index itself) are skipped; results are sorted for determinism.
    """
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        at_root = Path(dirpath) == root
        dirnames[:] = sorted(
            d
            for d in dirnames
            if not d.startswith(("_", ".")) and not (at_root and d in IGNORED_TOP_DIRS)
        )
        for name in filenames:
            if Path(name).suffix.lower() in AUDIO_EXTENSIONS and not name.startswith("."):
                full = Path(dirpath) / name
                out.append(full.relative_to(root).as_posix())
    out.sort()
    return out


def _sig(path: Path, size: int) -> str:
    """Cheap content hash: size + first and last 64 KiB."""
    h = hashlib.sha1(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(_SIG_BYTES))
        if size > _SIG_BYTES:
            f.seek(size - _SIG_BYTES)
            h.update(f.read(_SIG_BYTES))
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# per-file analysis (runs in worker processes: module-level, picklable)
# ---------------------------------------------------------------------------

_Job = tuple[str, str, int, int, str]  # root, rel, size, mtime_ns, sig
_Result = tuple[str, LibraryIndexEntry | None, str | None]


def analyze_file(job: _Job) -> _Result:
    """Probe + classify one file -> (rel, entry | None, problem message | None)."""
    root, rel, size, mtime_ns, sig = job
    path = Path(root) / rel
    try:
        info = parse_name(rel)
        want_bpm, want_key = probe_needs(info)
        pr = probe(path, want_bpm=want_bpm, want_key=want_key)
        c = classify(rel, pr, info)
        lufs = pr.loudness_lufs
        entry = LibraryIndexEntry(
            path=rel,
            type=c.type,
            subtype=c.subtype,
            kind=c.kind,  # type: ignore[arg-type]
            kit=c.kit,
            bpm=c.bpm,
            bpm_source=c.bpm_source,  # type: ignore[arg-type]
            bpm_confidence=round(c.bpm_confidence, 3) if c.bpm_confidence is not None else None,
            key=c.key,
            key_confidence=c.key_confidence,
            duration_s=round(pr.duration_s, 4),
            sample_rate=pr.sample_rate,
            bit_depth=pr.bit_depth,
            channels=pr.channels,
            peak_dbfs=round(pr.peak_dbfs, 2),
            rms_dbfs=round(pr.rms_dbfs, 2),
            loudness_lufs=round(lufs, 1) if lufs is not None else None,
            clipping=pr.clipping,
            features=pr.features,
            tags=c.tags,
            flags=c.flags,
            size=size,
            mtime_ns=mtime_ns,
            sig=sig,
        )
    except Exception as e:  # noqa: BLE001 - a corrupt file is a reported problem, never fatal
        msg = str(e).strip().splitlines()[0] if str(e).strip() else ""
        return rel, None, f"{type(e).__name__}: {msg}" if msg else type(e).__name__
    return rel, entry, None


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


def _write_index(path: Path, index: LibraryIndex) -> None:
    """Atomic write; one entry per line so the file diffs and greps well."""
    data = index.model_dump(mode="json", exclude_none=True)
    entries = data.pop("entries")
    head = json.dumps(data, ensure_ascii=False, indent=1)
    head = head.rsplit("\n}", 1)[0]  # drop the closing brace; entries follow
    rows = ",\n  ".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in entries)
    body = (
        f'{head},\n "entries": [\n  {rows}\n ]\n}}\n' if rows else f'{head},\n "entries": []\n}}\n'
    )
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(body, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError as e:
        raise MuestraError.one(
            "internal_error",
            f"Cannot write the library index {path}: {e}",
            hint="The library root must be writable (the index lives inside it).",
        ) from e


def index_root(
    root: Path,
    *,
    progress: ProgressFn | None = None,
    workers: int | None = None,
    force: bool = False,
) -> IndexReport:
    """Index ``root`` incrementally and write ``<root>/.muestra-index.json``.

    ``progress(done, total, rel_path)`` fires once up-front (with the
    reused files counted as done) and after each analysed file.
    ``workers`` defaults to ``min(cpu_count, 8)``; ``force`` re-measures
    every file.
    """
    t0 = time.monotonic()
    root = root.resolve()
    if not root.is_dir():
        raise MuestraError.one(
            "file_not_found",
            f"Library root {root} does not exist.",
            hint="Point library.index at a directory containing samples.",
        )

    prev = load_index(root)
    if prev is None or force or prev.analyzer_version != ANALYZER_VERSION:
        prev_by_path: dict[str, LibraryIndexEntry] = {}
    else:
        prev_by_path = {e.path: e for e in prev.entries}

    files = scan_audio_files(root)
    total = len(files)
    reused: list[LibraryIndexEntry] = []
    todo: list[_Job] = []
    problems: list[IndexProblem] = []
    new_paths: set[str] = set()

    for rel in files:
        path = root / rel
        try:
            st = path.stat()
        except OSError as e:
            problems.append(IndexProblem(path=rel, message=f"stat failed: {e}"))
            continue
        old = prev_by_path.get(rel)
        if old is not None and old.size == st.st_size and old.mtime_ns == st.st_mtime_ns:
            reused.append(old)
            continue
        try:
            sig = _sig(path, st.st_size)
        except OSError as e:
            problems.append(IndexProblem(path=rel, message=f"read failed: {e}"))
            continue
        if old is not None and old.size == st.st_size and old.sig == sig:
            reused.append(old.model_copy(update={"mtime_ns": st.st_mtime_ns}))
            continue
        if old is None:
            new_paths.add(rel)
        todo.append((str(root), rel, st.st_size, st.st_mtime_ns, sig))

    done = len(reused) + len(problems)
    if progress:
        progress(done, total, "")

    analyzed: list[LibraryIndexEntry] = []
    n_workers = 1
    if todo:
        n_workers = max(1, workers if workers else min(os.cpu_count() or 2, 8))
        if len(todo) < _POOL_MIN_FILES:
            n_workers = 1

        def consume(results) -> None:
            nonlocal done
            for rel, entry, problem in results:
                if entry is not None:
                    analyzed.append(entry)
                else:
                    problems.append(IndexProblem(path=rel, message=problem or "unknown error"))
                done += 1
                if progress:
                    progress(done, total, rel)

        if n_workers == 1:
            consume(analyze_file(j) for j in todo)
        else:
            try:
                with ProcessPoolExecutor(max_workers=n_workers) as pool:
                    consume(pool.map(analyze_file, todo, chunksize=8))
            except (OSError, RuntimeError, ImportError):
                # no process pool available (restricted env): do it inline
                already = {e.path for e in analyzed} | {p.path for p in problems}
                n_workers = 1
                consume(analyze_file(j) for j in todo if j[1] not in already)

    # licences: re-read each run (cheap) for every kit dir that has entries
    entries = sorted(reused + analyzed, key=lambda e: e.path)
    kits: dict[str, KitLicense] = {}
    kit_dirs = {kit_dir(e.path) for e in entries} - {None}
    for kd in sorted(kit_dirs):
        lic = parse_license(root / kd, kd)
        if lic is not None:
            kits[kd.rsplit("/", 1)[-1]] = lic

    present = {e.path for e in entries}
    removed = len(
        [p for p in prev_by_path if p not in present and p not in {x.path for x in problems}]
    )
    index = LibraryIndex(
        schema_version=0,
        analyzer_version=ANALYZER_VERSION,
        indexed_at=datetime.now(UTC),
        generator="muestra",
        kits=kits,
        problems=sorted(problems, key=lambda p: p.path),
        entries=entries,
    )
    out = index_path(root)
    _write_index(out, index)
    return IndexReport(
        root=root,
        index_path=out,
        scanned=total,
        analyzed=len(analyzed),
        new=len([e for e in analyzed if e.path in new_paths]),
        changed=len([e for e in analyzed if e.path not in new_paths]),
        reused=len(reused),
        removed=removed,
        problems=index.problems,
        seconds=time.monotonic() - t0,
        workers=n_workers,
        index=index,
    )
