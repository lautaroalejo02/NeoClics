"""WP13 finding 1: a failed or killed render never leaves a ghost version."""

from __future__ import annotations

import json
import shutil

import pytest

from muestra.project import save_song
from muestra.registry import run_tool
from muestra.render import staging, tools
from muestra.versions import core as versions
from tests.render.test_tools import small_song


@pytest.fixture
def repo(project):
    shutil.rmtree(project.renders_dir / "v001")  # untagged fixture render
    project.feedback_path.unlink()  # names v001/v002: ids would start at v003
    versions.init_repo(project.root)
    save_song(project, small_song())
    return project


def _state(project):
    tags = sorted(versions.version_tags(project.root))
    head = versions._head_sha(project.root)
    renders = sorted(p.name for p in project.renders_dir.iterdir() if p.name != staging.STAGING)
    leftovers = (
        sorted(p.name for p in staging.staging_root(project).iterdir())
        if staging.staging_root(project).is_dir()
        else []
    )
    return tags, head, renders, [x for x in leftovers if x != "version.lock"]


def test_failed_render_leaves_no_commit_tag_or_folder(repo, monkeypatch):
    ok = run_tool("render", {"project": str(repo.root), "summary": "first"})
    assert ok.ok, ok.errors
    before = _state(repo)

    def broken(project, song, version, bars, *, stems=True, directory=None):
        (directory / "mix.wav").write_bytes(b"partial")
        raise RuntimeError("plugin crashed")

    monkeypatch.setattr(tools, "render_song", broken)
    failed = run_tool("render", {"project": str(repo.root), "summary": "second"})
    assert not failed.ok
    assert failed.errors[0].code == "render_failed"
    assert "No version was created" in failed.hint
    assert _state(repo) == before

    monkeypatch.undo()
    again = run_tool("render", {"project": str(repo.root), "summary": "third"})
    assert again.ok
    assert again.data["version"] == "v002"
    assert again.data["meta"]["parent"] == "v001"


def test_muestra_error_during_render_says_no_version(repo, monkeypatch):
    before = _state(repo)

    def missing(*_a, **_k):
        from muestra.errors import MuestraError

        raise MuestraError.one("plugin_missing", "gone", hint="Install it.")

    monkeypatch.setattr(tools, "render_song", missing)
    failed = run_tool("render", {"project": str(repo.root)})
    assert failed.errors[0].code == "plugin_missing"
    assert failed.hint.startswith("Install it.") and staging.NOT_CREATED in failed.hint
    assert _state(repo) == before


def test_failure_after_commit_rolls_back_tag_and_commit(repo, monkeypatch):
    assert run_tool("render", {"project": str(repo.root)}).ok
    before = _state(repo)

    def boom(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(staging.Stage, "publish_new", boom)
    failed = run_tool("render", {"project": str(repo.root)})
    assert not failed.ok and staging.NOT_CREATED in failed.hint
    assert _state(repo) == before


def test_hard_kill_leftovers_are_rolled_back_on_next_render(repo):
    assert run_tool("render", {"project": str(repo.root)}).ok
    tags, head, renders, _ = _state(repo)
    # Simulate a process killed between tag and publish: its staging folder
    # and an unheld lock file remain, and v002 is tagged with no audio.
    sha = versions.record_version(repo, "v002", None, "killed", None, None)
    leftover = staging.staging_root(repo) / "deadbeef"
    leftover.mkdir(parents=True)
    (staging.staging_root(repo) / "deadbeef.lock").write_text("")
    (leftover / "mix.wav").write_bytes(b"x")
    (leftover / "pending.json").write_text(json.dumps({"version": "v002", "commit": sha}))
    # Another dead render that had not committed anything yet.
    (staging.staging_root(repo) / "cafe").mkdir()
    assert "v002" in versions.version_tags(repo.root)

    assert staging.recover(repo) == ["v002"]
    assert _state(repo) == (tags, head, renders, [])

    result = run_tool("render", {"project": str(repo.root), "summary": "after crash"})
    assert result.ok
    assert result.data["meta"]["parent"] == "v001"


def test_actual_killed_process_rolls_back_its_tag_and_audio(repo):
    from tests.integration.wp15_audit import kill_after_commit

    assert run_tool("render", {"project": str(repo.root)}).ok
    proof = kill_after_commit(repo)
    assert proof["state_unchanged"] and proof["recovered"] == ["v002"]


def test_recover_ignores_live_render_and_real_versions(repo):
    assert run_tool("render", {"project": str(repo.root)}).ok
    live = staging.Stage(repo)
    try:
        live.mark("v001")  # names a version that HAS audio: never touched
        assert staging.recover(repo) == []
        assert live.dir.is_dir()
    finally:
        live.close()
    assert "v001" in versions.version_tags(repo.root)


def test_rerender_failure_keeps_the_old_render(repo, monkeypatch):
    assert run_tool("render", {"project": str(repo.root)}).ok
    mix = repo.path("renders/v001/mix.wav").read_bytes()

    def broken(project, song, version, bars, *, stems=True, directory=None):
        raise RuntimeError("crash")

    monkeypatch.setattr(tools, "render_song", broken)
    assert not run_tool("render", {"project": str(repo.root), "version": "v001"}).ok
    assert repo.path("renders/v001/mix.wav").read_bytes() == mix
    assert repo.path("renders/v001/meta.json").is_file()
