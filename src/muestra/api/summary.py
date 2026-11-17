"""One-read project view for the listening UI; incomplete writes are reported/skipped."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ValidationError

from ..project.core import open_project
from ..registry import REGISTRY
from ..schemas.chops import ChopCandidateSet
from ..schemas.metrics import Metrics
from ..schemas.render_meta import RenderMeta
from .core import Runtime


def read_model(path: Path, model: type[BaseModel]) -> dict | None:
    try:
        return model.model_validate_json(path.read_text("utf-8")).model_dump(
            mode="json", by_alias=True
        )
    except (OSError, ValueError, ValidationError):
        return None


def feedback_aggregate(events: list[dict]) -> dict:
    return {
        "likes": sum(e.get("type") == "rating" and e.get("value") == "like" for e in events),
        "dislikes": sum(e.get("type") == "rating" and e.get("value") == "dislike" for e in events),
        "comments": [e for e in events if e.get("type") == "comment"],
        "events": events,
    }


def project_summary(runtime: Runtime) -> dict:
    root = runtime.project
    project = open_project(root)
    song = runtime.call(REGISTRY["song.get"])
    feedback = runtime.call(REGISTRY["feedback.read"])
    events = (feedback.data or {}).get("events", [])
    versions, candidates, issues = [], [], []
    for path in sorted((root / "renders").glob("v*/meta.json")):
        meta = read_model(path, RenderMeta)
        if meta is None:
            issues.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "message": "Incomplete or invalid render metadata; retry after writing.",
                }
            )
            continue
        version = meta["version"]
        versions.append(
            {
                "version": version,
                "meta": meta,
                "metrics": read_model(path.with_name("metrics.json"), Metrics),
                "feedback": feedback_aggregate(
                    [
                        e
                        for e in events
                        if e.get("version") == version
                        or e.get("a") == version
                        or e.get("b") == version
                    ]
                ),
            }
        )
    versions.sort(key=lambda item: int(item["version"][1:]), reverse=True)
    for path in sorted((root / "cache/chops").glob("*/*.json")):
        candidate = read_model(path, ChopCandidateSet)
        if candidate:
            candidates.append(candidate)
        else:
            issues.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "message": "Incomplete or invalid candidate set.",
                }
            )
    return {
        "project_path": str(root),
        "config": project.config.model_dump(mode="json"),
        "song": (song.data or {}).get("song"),
        "versions": versions,
        "candidate_sets": candidates,
        "feedback": feedback_aggregate(events),
        "feedback_cursor": (feedback.data or {}).get("next_cursor", 0),
        "issues": [
            *issues,
            *[i.model_dump() for i in song.errors],
            *[i.model_dump() for i in feedback.errors],
        ],
        "feedback_skipped": (feedback.data or {}).get("skipped", []),
    }
