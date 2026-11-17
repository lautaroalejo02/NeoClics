"""Integration fixtures: a hermetic studio (record, fixture library, fakes).

Fast tests run by default. ``@pytest.mark.slow`` tests use real separation,
the real library and real instruments; they run with ``MUESTRA_RUN_SLOW=1``
and skip (with the reason) when the owner's library or plugins are absent.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from tests.conftest import FIXTURES

from . import fakes


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: real models / library / plugins (MUESTRA_RUN_SLOW=1)")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("MUESTRA_RUN_SLOW") == "1":
        return
    for item in items:
        if "slow" in item.keywords and "integration" in str(item.path):
            item.add_marker(
                pytest.mark.skip(reason="Set MUESTRA_RUN_SLOW=1 to run real-stack tests")
            )


@pytest.fixture
def studio(tmp_path, monkeypatch):
    """A record, a small indexed library with a disco-01 starter kit, fakes on."""
    library = tmp_path / "library"
    shutil.copytree(FIXTURES / "library", library)
    kit = {lane: f"kits/disco-01/{lane}.wav" for lane in ("kick", "clap", "ohat")}
    (library / "starter-kits.json").write_text(
        json.dumps(
            {
                "schema_version": 0,
                "kits": {"disco-01": {"description": "fixture kit", "lanes": kit}},
            }
        ),
        encoding="utf-8",
    )
    record = fakes.synth_record(tmp_path / "inbox" / "record.wav")
    monkeypatch.setenv("MUESTRA_LIBRARY", "none")
    monkeypatch.setenv("MUESTRA_PROJECT", "")
    fakes.install_into(monkeypatch)
    env = {**os.environ, "PYTHONUTF8": "1", "MUESTRA_LIBRARY": "none", "MUESTRA_PROJECT": ""}
    work = tmp_path / "projects"
    work.mkdir()
    return {"library": library, "record": record, "work": work, "env": env, "tmp": tmp_path}


def kit_uris(library_kits_result: dict) -> dict[str, str]:
    kit = library_kits_result["kits"][0]
    return {lane["lane"]: lane["uri"] for lane in kit["lanes"] if lane.get("present", True)}


REAL_LIBRARY = Path.home() / "Documents/MuestraLibrary"
