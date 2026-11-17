"""Small durable source catalogue and atomic ingest cache writes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..errors import MuestraError
from ..project import Project, allocate_id, edit_song_document, load_song
from .audio import decode


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise MuestraError.one(
            "parse_error",
            f"Cannot read {path}: {exc}",
            path=str(path),
            hint="Restore the file from backup, or remove the derived cache and retry.",
        ) from exc


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def project_lock(project: Project) -> Iterator[None]:
    """Cross-process exclusion for WP1 mutations; never hold an open file on replace."""
    path = project.root / ".muestra-ingest.lock"
    try:
        stream = path.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise MuestraError.one(
            "busy",
            "Another ingest operation holds this project.",
            hint="Retry when it finishes. After a crashed process, remove .muestra-ingest.lock.",
        ) from exc
    try:
        with stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        path.unlink(missing_ok=True)


def sources(project: Project) -> dict[str, dict]:
    path = project.path("sources/.muestra-sources.json")
    catalogue = read_json(path).get("sources", {}) if path.exists() else {}
    if project.song_path.exists():
        for source in load_song(project).sources:
            catalogue[source.id] = {**catalogue.get(source.id, {}), "file": source.file}
    return catalogue


def source_file(project: Project, source_id: str) -> tuple[str, Path]:
    entry = sources(project).get(source_id)
    if entry is None:
        raise MuestraError.one(
            "ref_not_found",
            f"Unknown source {source_id}.",
            path="source_id",
            hint="Import the file with source.import first.",
        )
    return entry["file"], project.resolve(entry["file"])


def import_source(project: Project, path: Path, source_id: str | None) -> tuple[str, str]:
    if not path.is_file():
        raise MuestraError.one("file_not_found", f"Audio file does not exist: {path}")
    if path.suffix.lower() not in {".wav", ".flac", ".mp3", ".m4a"}:
        raise MuestraError.one("invalid_audio", "Supported formats: wav, flac, mp3, m4a.")
    with project_lock(project):
        catalogue = sources(project)
        content_hash = digest(path)
        for existing_id, entry in catalogue.items():
            existing_path = project.path(entry["file"])
            if existing_path.is_file() and digest(existing_path) == content_hash:
                _register(project, existing_id, entry["file"])
                return existing_id, entry["file"]
        source_id = source_id or allocate_id("src", set(catalogue))
        if source_id in catalogue:
            raise MuestraError.one("duplicate_id", f"Source id {source_id} is already in use.")
        # Validate before copying; store safe generated filenames, not arbitrary user basenames.
        decode(path)
        rel = f"sources/{source_id}{path.suffix.lower()}"
        dest = project.path(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            raise MuestraError.one("already_exists", f"Refusing to overwrite {rel}.")
        with dest.open("xb") as out, path.open("rb") as src:
            shutil.copyfileobj(src, out)
        catalogue[source_id] = {"file": rel, "sha256": content_hash}
        write_json(project.path("sources/.muestra-sources.json"), {"sources": catalogue})
        _register(project, source_id, rel)
        return source_id, rel


def _register(project: Project, source_id: str, rel: str) -> None:
    if project.song_path.exists():

        def mutate(doc):
            entries = doc.setdefault("sources", [])
            if not any(s["id"] == source_id for s in entries):
                entries.append({"id": source_id, "file": rel})

        edit_song_document(project, mutate)
