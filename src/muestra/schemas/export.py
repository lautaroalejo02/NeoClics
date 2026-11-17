"""Export JSON Schemas for every contract to ``schemas/`` (repo root).

Run: ``uv run python -m muestra.schemas.export schemas/``

Also exports each registered tool's params/result models under
``schemas/tools/`` so WP7 can generate MCP tools, CLI commands and API
routes mechanically. ``tests/schemas/test_schema_export.py`` fails when
the committed files are stale.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter

from ..errors import ToolResult
from .analysis import Analysis
from .api import Job, WsEvent
from .chops import ChopCandidateSet
from .config import ProjectConfig
from .feedback import FeedbackEvent
from .library import LibraryIndex, StarterKits
from .metrics import Metrics
from .render_meta import RenderMeta
from .song import Song

_SCHEMA_SUFFIX = ".schema.json"


def _schema_of(model: Any) -> dict:
    if isinstance(model, type) and issubclass(model, BaseModel):
        return model.model_json_schema()
    return TypeAdapter(model).json_schema()


def generated_schemas() -> dict[str, dict]:
    """Map of output filename (relative to the schemas dir) -> schema dict."""
    out: dict[str, dict] = {
        f"song{_SCHEMA_SUFFIX}": _schema_of(Song),
        f"analysis{_SCHEMA_SUFFIX}": _schema_of(Analysis),
        f"render-meta{_SCHEMA_SUFFIX}": _schema_of(RenderMeta),
        f"feedback{_SCHEMA_SUFFIX}": _schema_of(FeedbackEvent),
        f"metrics{_SCHEMA_SUFFIX}": _schema_of(Metrics),
        f"config{_SCHEMA_SUFFIX}": _schema_of(ProjectConfig),
        f"chop-candidates{_SCHEMA_SUFFIX}": _schema_of(ChopCandidateSet),
        f"library-index{_SCHEMA_SUFFIX}": _schema_of(LibraryIndex),
        f"starter-kits{_SCHEMA_SUFFIX}": _schema_of(StarterKits),
        f"job{_SCHEMA_SUFFIX}": _schema_of(Job),
        f"ws-event{_SCHEMA_SUFFIX}": _schema_of(WsEvent),
        f"tool-result{_SCHEMA_SUFFIX}": _schema_of(ToolResult),
    }
    # Tool params/result models (imported lazily: registry -> tools -> schemas).
    from ..registry import REGISTRY

    for name, spec in REGISTRY.items():
        out[f"tools/{name}.params{_SCHEMA_SUFFIX}"] = _schema_of(spec.params)
        if spec.result is not None:
            out[f"tools/{name}.result{_SCHEMA_SUFFIX}"] = _schema_of(spec.result)
    return out


def export_all(out_dir: Path) -> list[Path]:
    """Write every schema file under ``out_dir``; returns written paths."""
    written: list[Path] = []
    for rel, schema in generated_schemas().items():
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export muestra JSON Schemas.")
    parser.add_argument(
        "out_dir",
        nargs="?",
        default="schemas",
        help="Output directory (default: ./schemas)",
    )
    args = parser.parse_args(argv)
    written = export_all(Path(args.out_dir))
    for p in written:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
