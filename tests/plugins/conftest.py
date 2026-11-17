from pathlib import Path

import pytest

from muestra.plugins.discovery import resolve_plugin
from muestra.project.core import Project
from muestra.schemas.config import ProjectConfig


def pytest_configure(config):
    config.addinivalue_line("markers", "real_plugin: needs an installed native VST3/AU plugin")
    config.addinivalue_line(
        "markers", "real_samples: needs the optional installed free sample data"
    )


@pytest.fixture
def project(tmp_path):
    (tmp_path / "muestra.toml").write_text("schema_version = 0\n", encoding="utf-8")
    (tmp_path / "song.yaml").write_text(
        "schema_version: 0\n"
        "meta: {title: test, key: Am, bpm: 120, length_bars: 16, seed: 42}\ntracks: []\n",
        encoding="utf-8",
    )
    return Project(tmp_path, ProjectConfig())


@pytest.fixture(scope="module")
def surge():
    from muestra.errors import MuestraError

    project = Project(Path.cwd(), ProjectConfig())
    try:
        return resolve_plugin(project, "surge-xt")
    except MuestraError:
        pytest.skip("Surge XT is not installed; install the free VST3 to run real_plugin tests.")
