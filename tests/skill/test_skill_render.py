"""Opt-in end-to-end check of the skill's examples (real models, library, plugins).

Run with ``MUESTRA_RUN_SLOW=1 uv run pytest tests/skill/test_skill_render.py -s``.
Needs the ``ingest`` extra with cached HTDemucs/Beat This weights, the owner's
indexed library (``~/Documents/MuestraLibrary`` or ``MUESTRA_LIBRARY``) with the
public-domain ``_test-audio/tiger-rag-first30s.wav``, and Surge XT + the SFZ
instruments. Each example must validate with ``check_files=True``, render, and
produce no ``error``-severity alarm (warnings are documented in the skill).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from muestra.registry import run_tool

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = sorted((REPO / "skills" / "muestra" / "examples").glob("*.yaml"))


def _library() -> Path | None:
    env = os.environ.get("MUESTRA_LIBRARY")
    roots = [Path(p) for p in env.split(os.pathsep)] if env else []
    roots.append(Path.home() / "Documents" / "MuestraLibrary")
    for root in roots:
        if (root / "_test-audio" / "tiger-rag-first30s.wav").is_file():
            return root
    return None


pytestmark = pytest.mark.skipif(
    os.environ.get("MUESTRA_RUN_SLOW") != "1" or _library() is None,
    reason="opt-in: set MUESTRA_RUN_SLOW=1 and provide the owner's library + test audio",
)


def _ok(result):
    assert result.ok, [e.model_dump() for e in result.errors]
    return result.data


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    parent = tmp_path_factory.mktemp("skill-render")
    data = _ok(run_tool("project.create", {"name": "tiger", "dir": str(parent)}))
    proj = data["project_path"]
    audio = _library() / "_test-audio" / "tiger-rag-first30s.wav"
    assert _ok(run_tool("source.import", {"project": proj, "path": str(audio)}))["source_id"] == (
        "src1"
    )
    _ok(run_tool("source.separate", {"project": proj, "source_id": "src1"}))
    _ok(run_tool("source.analyze", {"project": proj, "source_id": "src1"}))
    return Path(proj)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_renders(project, path):
    (project / "song.yaml").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    report = _ok(run_tool("song.validate", {"project": str(project)}))
    assert report["valid"], report["issues"]
    data = _ok(
        run_tool(
            "render",
            {"project": str(project), "request": f"test {path.stem}", "agent": "pytest"},
        )
    )
    metrics = data["metrics"]
    assert metrics is not None, data.get("hint")
    print(f"\n{path.name}: {data['meta']['render_time_s']:.1f} s\n{metrics['summary']}")
    errors = [a for a in metrics["alarms"] if a["severity"] == "error"]
    assert not errors, errors
