"""Committed JSON Schemas under schemas/ must match the pydantic models."""

from __future__ import annotations

import json

from muestra.schemas.export import generated_schemas

from ..conftest import SCHEMAS_DIR


def test_committed_schemas_are_fresh():
    generated = generated_schemas()
    stale = []
    for rel, schema in sorted(generated.items()):
        path = SCHEMAS_DIR / rel
        if not path.is_file():
            stale.append(f"{rel} (missing)")
            continue
        committed = json.loads(path.read_text(encoding="utf-8"))
        if committed != schema:
            stale.append(f"{rel} (stale)")
    extra = {
        p.relative_to(SCHEMAS_DIR).as_posix() for p in SCHEMAS_DIR.rglob("*.schema.json")
    } - set(generated)
    assert not stale and not extra, (
        f"Schema files out of date: {stale}; unexpected files: {sorted(extra)}. "
        "Run: uv run python -m muestra.schemas.export schemas/"
    )


def test_export_includes_tool_schemas():
    generated = generated_schemas()
    assert "tools/source.find_chops.params.schema.json" in generated
    assert "tools/source.find_chops.result.schema.json" in generated
    assert "tool-result.schema.json" in generated
