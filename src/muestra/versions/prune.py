"""Disk use and cleanup: ``versions.prune`` and render-cache GC (WP13).

A 30 s render is ~95 MB (32-bit float stems plus per-lane drum stems), so
twenty iterations fill gigabytes. Pruning deletes a version's ``renders/vNNN``
folder only; its commit and tag stay, so it remains listed (``rerenderable``)
and ``render`` with ``version=vNNN`` regenerates it bit-exactly. Rules:

* kept: the newest ``keep_latest`` versions; liked ones (latest rating);
  versions with **unread feedback** (any rating/comment/chop choice naming
  them, or an A/B including them, timestamped after the newest version was
  created: the agent has not answered it with a new version yet); both
  sides of the last ``AB_RECENT`` A/B choices; the base (parent of the next
  render); any version that cannot be re-rendered bit-exactly (no git tag,
  or tracks using plugin presets whose determinism is unverified);
* everything else with a ``renders/`` folder is removable.

Cache GC removes ``cache/render`` entries not used by the newest render (a
cache hit touches its file), interrupted ``*.tmp`` files, and all but the
newest ``PREVIEWS_KEPT`` loose previews (``cache/previews/render-*.wav``,
``cache/drafts/``). Imported audio, stems and analyses are never touched.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import time
from pathlib import Path

from ..project.core import Project

AB_RECENT = 10
PREVIEWS_KEPT = 5
_VERSION_DIR = "v"


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.stat(os.path.join(root, name)).st_size
    return total


def disk_usage(project: Project) -> dict:
    renders = dir_size(project.renders_dir)
    cache_render = dir_size(project.cache_dir / "render")
    previews = dir_size(project.cache_dir / "previews") + dir_size(project.cache_dir / "drafts")
    stems = dir_size(project.cache_dir / "stems")
    total = renders + cache_render + previews + stems
    hint = None
    if renders + cache_render + previews > 1_000_000_000:
        hint = (
            f"Renders and caches use {(renders + cache_render + previews) / 1e9:.1f} GB. "
            "versions.prune (dry run by default) lists what can be removed safely."
        )
    return {
        "renders_bytes": renders,
        "cache_render_bytes": cache_render,
        "previews_bytes": previews,
        "stems_bytes": stems,
        "total_bytes": total,
        "hint": hint,
    }


def _rendered_versions(project: Project) -> list[str]:
    if not project.renders_dir.is_dir():
        return []
    return sorted(
        p.name
        for p in project.renders_dir.iterdir()
        if p.is_dir() and p.name.startswith(_VERSION_DIR) and p.name[1:].isdigit()
    )


def plan(project: Project, keep_latest: int = 5) -> tuple[dict[str, list[str]], list[str]]:
    """``(kept {version: reasons}, removable)`` among versions with a renders/ folder."""
    from ..feedback.store import read_events
    from ..render.tools import unverified_reproducibility
    from .core import song_at, suggest_parent, version_tags
    from .records import build_records

    rendered = _rendered_versions(project)
    tags = version_tags(project.root)
    records = {r.version: r for r in build_records(project)[0]}
    kept: dict[str, list[str]] = {}

    def keep(version, reason):
        if version in rendered:
            kept.setdefault(version, []).append(reason)

    for version in sorted(rendered, key=lambda v: int(v[1:]))[-keep_latest:] if keep_latest else []:
        keep(version, f"latest {keep_latest}")
    for version, record in records.items():
        if record.liked or record.feedback.rating == "like":
            keep(version, "liked")
    newest = max((r.created_at for r in records.values() if r.created_at), default=None)
    try:
        events = read_events(project).events
    except Exception:  # noqa: BLE001 - unreadable feedback: keep everything it might name
        events = []
        for version in rendered:
            keep(version, "feedback unreadable")
    ab = [e for e in events if e.type == "ab_choice"]
    for e in ab[-AB_RECENT:]:
        keep(e.a, "recent A/B")
        keep(e.b, "recent A/B")
    for e in events:
        if newest is not None and e.ts <= newest:
            continue
        for version in (getattr(e, "version", None), getattr(e, "a", None), getattr(e, "b", None)):
            if version:
                keep(version, "unread feedback")
    base = suggest_parent(project, tags)
    if base:
        keep(base, "base of the next render")
    for version in rendered:
        if version in kept:
            continue
        if version not in tags:
            keep(version, "not in git (cannot be re-rendered)")
            continue
        try:
            unverified = unverified_reproducibility(song_at(project, version))
        except Exception:  # noqa: BLE001 - unreadable song: never delete its audio
            unverified = ["?"]
        if unverified:
            keep(version, "re-render not verified bit-exact: " + ", ".join(unverified))
    return kept, [v for v in rendered if v not in kept]


def prune_versions(project: Project, removable: list[str]) -> int:
    freed = 0
    for version in removable:
        folder = project.renders_dir / version
        size = dir_size(folder)
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            freed += size
    return freed


def cache_plan(project: Project) -> list[Path]:
    """Files cache GC would delete."""
    out: list[Path] = []
    render = project.cache_dir / "render"
    newest = _newest_render_start(project)
    if render.is_dir():
        for path in render.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix == ".tmp" or newest is None or path.stat().st_mtime < newest:
                out.append(path)
    loose = sorted(
        [
            *(project.cache_dir / "previews").glob("render-*.wav"),
            *(project.cache_dir / "drafts").glob("*/*"),
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    drafts = {}
    for path in loose:
        group = path.parent if path.parent.parent.name == "drafts" else path
        drafts.setdefault(group, []).append(path)
    for group in list(drafts)[PREVIEWS_KEPT:]:
        out.extend(drafts[group])
    return out


def _newest_render_start(project: Project) -> float | None:
    """When the newest render began (cache entries it used were touched after)."""
    best = None
    for version in _rendered_versions(project):
        meta = project.renders_dir / version / "meta.json"
        try:
            import json

            data = json.loads(meta.read_text(encoding="utf-8"))
            start = meta.stat().st_mtime - float(data.get("render_time_s") or 0) - 60
        except (OSError, ValueError):
            continue
        best = start if best is None else max(best, start)
    return best


def gc_cache(project: Project, files: list[Path]) -> int:
    freed = 0
    for path in files:
        with contextlib.suppress(OSError):
            size = path.stat().st_size
            path.unlink()
            freed += size
    drafts = project.cache_dir / "drafts"
    if drafts.is_dir():
        for folder in drafts.iterdir():
            if folder.is_dir() and not any(folder.iterdir()):
                with contextlib.suppress(OSError):
                    folder.rmdir()
    return freed


def touch(path: Path) -> None:
    """Mark a cache entry as used (LRU for gc_cache)."""
    with contextlib.suppress(OSError):
        now = time.time()
        os.utime(path, (now, now))
