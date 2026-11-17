from __future__ import annotations

import shutil

import pytest

from muestra.project import create_project, open_project

_IDENTITY_VARS = (
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "EMAIL",
)


@pytest.fixture(autouse=True)
def hermetic_git(tmp_path_factory, monkeypatch):
    """No system/global git config, no identity: tests control all of it.

    Returns the path of the (empty) *global* gitconfig so tests can put
    settings there (e.g. ``core.autocrlf=true``, the Git-for-Windows default).
    """
    home = tmp_path_factory.mktemp("home")
    cfg = home / "gitconfig"
    cfg.write_text("", encoding="utf-8")
    for var in _IDENTITY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return cfg


@pytest.fixture
def global_gitconfig(hermetic_git):
    return hermetic_git


@pytest.fixture(scope="session")
def _project_template(tmp_path_factory):
    """One real project.create per session; tests copy it (git spawns are slow)."""
    home = tmp_path_factory.mktemp("tplhome")
    cfg = home / "gitconfig"
    cfg.write_text("", encoding="utf-8")
    mp = pytest.MonkeyPatch()
    mp.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    mp.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in _IDENTITY_VARS:
        mp.delenv(var, raising=False)
    try:
        yield create_project(tmp_path_factory.mktemp("tpl") / "proj").root
    finally:
        mp.undo()


@pytest.fixture
def project(tmp_path, _project_template):
    dst = tmp_path / "proj"
    shutil.copytree(_project_template, dst)
    return open_project(dst)
