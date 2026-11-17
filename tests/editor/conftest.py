from __future__ import annotations

import subprocess

import pytest

from tests.render.conftest import fake_instrument, prepare_fixture  # noqa: F401


@pytest.fixture
def editor_project(tmp_path):
    return prepare_fixture(tmp_path)


@pytest.fixture
def versioned_project(tmp_path):
    """The fixture as its own git repo with v001 recorded (no audio needed)."""
    from muestra.versions.core import init_repo, record_version

    project = prepare_fixture(tmp_path)
    subprocess.run(["git", "init", "-q", str(project.root)], check=True)
    init_repo(project.root)
    record_version(project, "v001", "inicio", "primera", "test", None)
    return project, project.song_path.read_bytes()
