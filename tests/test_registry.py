"""The tool registry + ToolResult envelope."""

from __future__ import annotations

import json
import re

from muestra.errors import ToolResult
from muestra.registry import REGISTRY, run_tool, tool_spec

#: the tools named in spec section 8 — the registry is a SUPERSET
#: (WPs may add tools via their own subpackage TOOLS lists).
SPEC_SECTION_8 = {
    "project.create",
    "project.open",
    "source.import",
    "source.separate",
    "source.analyze",
    "source.find_chops",
    "chops.choose",
    "preview",
    "library.index",
    "library.search",
    "song.get",
    "song.validate",
    "render",
    "versions.list",
    "versions.diff",
    "versions.restore",
    "feedback.read",
    "ears.analyze",
}


def test_all_spec_tools_registered():
    assert SPEC_SECTION_8 <= set(REGISTRY)
    for spec in REGISTRY.values():
        assert spec.description, spec.name


def test_wp01_tools_present():
    assert {"feedback.append", "chops.list", "versions.get"} <= set(REGISTRY)


def test_tool_spec_metadata():
    for spec in REGISTRY.values():
        assert re.match(r"^[a-zA-Z0-9_-]{1,64}$", spec.mcp_name), spec.name
        assert spec.mcp_name == spec.name.replace(".", "_")
        assert spec.group == spec.name.split(".", 1)[0]
    # render and preview are top-level CLI commands
    assert REGISTRY["render"].cli == ("render", None)
    assert REGISTRY["preview"].cli == ("preview", None)
    # readonly tools serve GET routes; long-running ones go through jobs
    assert REGISTRY["feedback.read"].readonly is True
    assert REGISTRY["render"].long_running is True


def test_run_tool_success(tmp_path):
    r = run_tool("project.create", {"name": "song1", "dir": str(tmp_path)})
    assert r.ok and r.data and r.data["project_path"].endswith("song1")
    assert "muestra.toml" in r.files
    ToolResult.model_validate(r.model_dump(mode="json"))
    json.dumps(r.model_dump(mode="json"))  # must be JSON-serialisable

    r = run_tool("song.validate", {"project": str(tmp_path / "song1")})
    assert r.ok and r.data["valid"] is True


def test_run_tool_envelope_on_error(tmp_path):
    r = run_tool("song.get", {"project": str(tmp_path / "nope")})
    assert not r.ok
    assert r.errors[0].code == "file_not_found"
    assert r.hint


def test_run_tool_unknown_and_bad_params():
    r = run_tool("nope.tool", {})
    assert not r.ok and r.errors[0].code == "unknown_tool"
    r = run_tool("source.separate", {"project": "x"})  # missing source_id
    assert not r.ok and r.errors[0].code == "missing_field"
    assert r.errors[0].path == "source_id"


def test_run_tool_not_implemented(tmp_path):
    r = run_tool("ears.analyze", {"project": str(tmp_path), "version": "v001"})
    assert not r.ok and r.errors[0].code == "not_implemented"


def test_tool_spec_unknown():
    import pytest

    from muestra.errors import MuestraError

    with pytest.raises(MuestraError):
        tool_spec("bogus")
