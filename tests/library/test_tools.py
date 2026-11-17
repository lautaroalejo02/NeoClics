"""The library.* tools through the registry envelope."""

from __future__ import annotations

from muestra.project import open_project
from muestra.registry import REGISTRY, run_tool
from muestra.schemas import LibraryConfig, ProjectConfig

from .conftest import kick, write


def test_tools_are_registered_with_the_right_flags():
    for name in ("library.index", "library.search", "library.kits", "library.info"):
        assert name in REGISTRY
    assert REGISTRY["library.index"].long_running and not REGISTRY["library.index"].readonly
    assert REGISTRY["library.search"].readonly and REGISTRY["library.kits"].readonly
    assert REGISTRY["library.search"].cli == ("library", "search")
    assert REGISTRY["library.search"].mcp_name == "library_search"


def test_index_tool_indexes_registers_and_reports(tmp_path, lib):
    from muestra.project import create_project

    p = create_project(tmp_path / "p", ProjectConfig(library=LibraryConfig(roots=[])))
    r = run_tool("library.index", {"project": str(p.root), "path": str(lib), "workers": 1})
    assert r.ok, r.errors
    d = r.data
    assert d["indexed"] == d["roots"][0]["entries"] > 10
    assert d["index_path"].endswith(".muestra-index.json")
    root = d["roots"][0]
    assert root["analyzed"] == root["scanned"] and root["reused"] == 0
    assert root["by_type"]["kick"] == 5 and root["by_kind"]["loop"] >= 4
    assert root["clipping"] == 1 and root["type_mismatches"] == 1  # the planted ones
    assert root["seconds"] >= 0 and root["problems_total"] == 0
    assert d["registered"] == [lib.as_posix()]
    assert open_project(p.root).config.library.roots == [lib.as_posix()]  # muestra.toml updated
    # a second call is incremental and does not register twice
    r2 = run_tool("library.index", {"project": str(p.root), "path": str(lib), "workers": 1})
    assert r2.data["roots"][0]["analyzed"] == 0 and r2.data["registered"] == []
    assert open_project(p.root).config.library.roots == [lib.as_posix()]


def test_index_tool_without_path_reindexes_configured_roots(project, lib):
    r = run_tool("library.index", {"project": str(project.root), "workers": 1})
    assert r.ok and r.data["roots"][0]["root"].lower() == str(lib.resolve()).lower()
    write(lib / "kits" / "machine-a" / "kick-m-added-07.wav", kick(66.0))
    r = run_tool("library.index", {"project": str(project.root), "workers": 1})
    assert r.data["roots"][0]["new"] == 1 and r.data["roots"][0]["reused"] > 10


def test_index_tool_reports_corrupt_files_in_the_result(project, lib):
    (lib / "kits" / "machine-a" / "kick-m-broken-01.wav").write_bytes(b"nope")
    r = run_tool("library.index", {"project": str(project.root), "workers": 1})
    assert r.ok
    assert r.data["roots"][0]["problems_total"] == 1
    assert r.data["roots"][0]["problems"][0]["path"].endswith("kick-m-broken-01.wav")
    assert "could not be read" in r.data["hint"]


def test_index_tool_errors_are_actionable(tmp_path):
    from muestra.project import create_project

    p = create_project(tmp_path / "p", ProjectConfig(library=LibraryConfig(roots=[])))
    r = run_tool("library.index", {"project": str(p.root), "path": str(tmp_path / "nope")})
    assert not r.ok and r.errors[0].code == "file_not_found"
    r = run_tool("library.index", {"project": str(p.root)})
    assert not r.ok and r.errors[0].code == "no_library" and "MUESTRA_LIBRARY" in r.hint


def test_index_tool_progress_callback(project):
    from muestra.library.tools import LibraryIndexParams, library_index

    calls = []
    library_index(
        LibraryIndexParams(project=str(project.root), workers=1),
        progress=lambda fraction, message=None: calls.append((fraction, message)),
    )
    assert calls and calls[-1][0] == 1.0
    assert all(0.0 <= f <= 1.0 and isinstance(m, str) for f, m in calls)


def test_index_tool_progress_through_transport_adapter(project):
    """Seam: the HTTP/MCP adapter passes progress(fraction, message); the tool
    used to call it with (done, total, rel) and crashed with internal_error."""
    from muestra.api.core import invoke_spec
    from muestra.registry import REGISTRY

    calls = []
    result = invoke_spec(
        REGISTRY["library.index"],
        {"project": str(project.root), "workers": 1},
        progress=lambda fraction, message=None: calls.append(fraction),
    )
    assert result.ok, result.errors
    assert calls and calls[-1] == 1.0


def test_search_tool_result_shape(project, indexed):
    r = run_tool(
        "library.search", {"project": str(project.root), "query": "bombo corto", "limit": 3}
    )
    assert r.ok and r.files == []
    d = r.data
    assert d["total"] == 5 and len(d["results"]) == 3
    assert d["searched_roots"] and d["interpreted"]
    top = d["results"][0]
    assert {"uri", "path", "type", "kit", "tags", "score", "reasons", "summary"} <= set(top)
    assert (
        "features" not in top and "clipping" not in top
    )  # compact: empty defaults omitted? see below
    assert "hint" in d


def test_search_tool_params_are_validated_for_the_agent(project):
    r = run_tool("library.search", {"project": str(project.root), "limit": 0})
    assert not r.ok and r.errors
    r = run_tool("library.search", {"project": str(project.root), "kind": "weird"})
    assert not r.ok


def test_info_tool_summarises_the_library(project, indexed):
    r = run_tool("library.info", {"project": str(project.root)})
    assert r.ok
    root = r.data["roots"][0]
    assert root["indexed"] and root["entries"] > 10 and root["by_type"]["kick"] == 5
    assert "no-ai-training" in root["kits"]["acoustic-b"]["restrictions"]
    assert root["kits"]["machine-a"]["files"] > 5


def test_info_tool_flags_unindexed_roots(project):
    r = run_tool("library.info", {"project": str(project.root)})
    assert r.ok and not r.data["roots"][0]["indexed"]
    assert "library.index" in r.data["hint"]
