from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "fixtures"
EXAMPLE_PROJECT = FIXTURES / "example-project"
INVALID_DIR = FIXTURES / "invalid"
SCHEMAS_DIR = REPO_ROOT / "schemas"


@pytest.fixture
def example_project():
    """The committed example project (read-only usage)."""
    from muestra.project import open_project

    return open_project(EXAMPLE_PROJECT)


@pytest.fixture
def tmp_project(tmp_path):
    """A writable copy of the example project."""
    dst = tmp_path / "proj"
    shutil.copytree(EXAMPLE_PROJECT, dst)
    from muestra.project import open_project

    return open_project(dst)


@pytest.fixture
def song_of(tmp_project):
    from muestra.project import load_song

    return load_song(tmp_project)


def write_song(project_root: Path, text: str) -> Path:
    p = project_root / "song.yaml"
    p.write_text(text, encoding="utf-8")
    return p
