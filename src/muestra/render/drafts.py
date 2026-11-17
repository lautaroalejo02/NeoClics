"""Disposable renders/previews exposed to the listening UI, never git history."""

from __future__ import annotations

import json
from datetime import UTC, datetime


def register_preview(project, path: str, label: str, duration_s: float) -> None:
    from ..ingest.storage import write_json

    write_json(
        project.path(path).with_suffix(".preview.json"),
        {
            "id": path,
            "path": path,
            "kind": "preview",
            "label": label,
            "created_at": datetime.now(UTC).isoformat(),
            "duration_s": duration_s,
        },
    )


def draft_info(meta: dict) -> dict:
    return {
        "id": meta["version"],
        "path": meta["mix"],
        "kind": "draft",
        "created_at": meta["created_at"],
        "label": meta.get("summary") or "Borrador",
        "range_bars": meta["range_bars"],
        "duration_s": meta.get("duration_s"),
    }


def list_drafts(root):
    from ..api.summary import read_model
    from ..schemas.render_meta import RenderMeta

    drafts = []
    for path in sorted((root / "renders").glob("d-*/meta.json")):
        meta = read_model(path, RenderMeta)
        if meta and (root / meta["mix"]).is_file():
            drafts.append(draft_info(meta))
    return drafts


def list_previews(root):
    previews = []
    for path in sorted((root / "cache/previews").glob("*.preview.json")):
        try:
            entry = json.loads(path.read_text("utf-8"))
            if (root / entry["path"]).is_file():
                previews.append(entry)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return previews
