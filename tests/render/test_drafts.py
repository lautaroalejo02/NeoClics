from __future__ import annotations

import subprocess

from muestra.api.core import Runtime
from muestra.api.events import EventBus, ProjectWatcher
from muestra.api.summary import project_summary
from muestra.project import save_song
from muestra.registry import run_tool
from muestra.render.tools import RenderParams, render
from muestra.versions.core import init_repo

from .test_tools import small_song


def test_draft_is_playable_and_analyzable_without_history(project):
    save_song(project, small_song())
    init_repo(project.root)

    def git(*args):
        return subprocess.check_output(["git", "-C", str(project.root), *args])

    before = git("rev-parse", "HEAD"), git("tag")
    watcher = ProjectWatcher(project.root, EventBus(), debounce=0)
    watcher.initialize()
    result = render(RenderParams(project=str(project.root), draft=True, range_bars=(1, 2)))
    assert result.version is None and result.draft_id.startswith("d-")
    assert (git("rev-parse", "HEAD"), git("tag")) == before
    assert result.metrics_path is None
    assert not list(project.renders_dir.glob("v002"))
    assert list(result.model_dump())[0] == "metrics_summary"
    events = watcher.scan()
    assert any(kind == "draft.created" and data["id"] == result.draft_id for kind, data in events)
    assert not any(kind == "version.created" for kind, _ in events)
    view = project_summary(Runtime(project.root))
    draft = next(d for d in view["drafts"] if d["id"] == result.draft_id)
    assert draft["path"] == result.mix_path and draft["range_bars"] == [1, 2]
    ears = run_tool("ears.analyze", {"project": str(project.root), "version": result.draft_id})
    assert ears.ok, ears.errors
    assert project.path(ears.data["metrics_path"]).is_file()
    assert (git("rev-parse", "HEAD"), git("tag")) == before


def test_preview_is_listed_and_emits_refresh(project):
    save_song(project, small_song())
    watcher = ProjectWatcher(project.root, EventBus(), debounce=0)
    watcher.initialize()
    result = run_tool("preview", {"project": str(project.root), "target": "mix", "bars": 1})
    assert result.ok, result.errors
    assert ("drafts.changed", {}) in watcher.scan()
    view = project_summary(Runtime(project.root))
    assert view["previews"][0]["path"] == result.data["preview_path"]


def test_failed_draft_publishes_nothing(project, monkeypatch):
    from muestra.render import tools

    save_song(project, small_song())

    def fail(*args, **kwargs):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(tools, "render_song", fail)
    result = run_tool("render", {"project": str(project.root), "draft": True})
    assert not result.ok
    assert not list(project.renders_dir.glob("d-*"))
