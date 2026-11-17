"""WP13 finding 9: versions.prune frees disk without losing anything."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta

from muestra.feedback import append_event
from muestra.registry import run_tool
from muestra.schemas.feedback import ABChoiceEvent, CommentEvent, RatingEvent
from tests.render.test_atomic_versions import repo  # noqa: F401 - fixture


def _render(project, n):
    out = []
    for i in range(n):
        result = run_tool("render", {"project": str(project.root), "summary": f"take {i}"})
        assert result.ok, result.errors
        out.append(result.data["version"])
    return out


def test_prune_keeps_what_matters_and_rerenders_bit_exact(repo):  # noqa: F811
    project = repo
    v = _render(project, 7)  # v001..v007
    past = datetime.now(UTC) - timedelta(days=1)
    append_event(project, RatingEvent(ts=past, version=v[0], value="like"))
    append_event(project, ABChoiceEvent(ts=past, a=v[1], b=v[2], winner=v[2]))
    mix = project.path(f"renders/{v[3]}/mix.wav").read_bytes()

    dry = run_tool("versions.prune", {"project": str(project.root), "keep_latest": 2})
    assert dry.ok, dry.errors
    data = dry.data
    assert data["dry_run"] and [e["version"] for e in data["remove"]] == [v[3], v[4]]
    reasons = {e["version"]: e["reasons"] for e in data["keep"]}
    assert reasons[v[0]] == ["liked"]
    assert reasons[v[1]] == reasons[v[2]] == ["recent A/B"]
    assert "latest 2" in reasons[v[6]] and "base of the next render" in reasons[v[6]]
    assert data["freed_bytes"] > 0 and project.path(f"renders/{v[3]}/mix.wav").is_file()

    # unread feedback (newer than the newest version) protects its version too
    append_event(project, CommentEvent(ts=datetime.now(UTC), version=v[4], text="ojo acá"))
    real = run_tool(
        "versions.prune", {"project": str(project.root), "keep_latest": 2, "dry_run": False}
    )
    assert [e["version"] for e in real.data["remove"]] == [v[3]]
    assert not project.path(f"renders/{v[3]}").exists()

    listed = run_tool("versions.list", {"project": str(project.root)}).data
    record = next(r for r in listed["versions"] if r["version"] == v[3])
    assert record["rerenderable"] and not record["rendered"] and record["size_bytes"] == 0
    assert listed["disk"]["renders_bytes"] > 0

    again = run_tool("render", {"project": str(project.root), "version": v[3]})
    assert again.ok, again.errors
    assert project.path(f"renders/{v[3]}/mix.wav").read_bytes() == mix  # bit-exact


def test_cache_gc_drops_entries_the_newest_render_did_not_use(repo):  # noqa: F811
    project = repo
    _render(project, 1)
    stale = project.cache_dir / "render/chops/old.npy"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"x" * 1000)
    old = time.time() - 3 * 3600
    os.utime(stale, (old, old))
    (project.cache_dir / "render/chops/half.npy.tmp").write_bytes(b"y")
    used = [p for p in (project.cache_dir / "render").rglob("*.npy") if p != stale]
    result = run_tool("versions.prune", {"project": str(project.root), "dry_run": False})
    assert result.data["cache_files"] == 2 and not stale.exists()
    assert all(p.exists() for p in used)


def test_project_summary_reports_disk(repo):  # noqa: F811
    from muestra.api.core import Runtime
    from muestra.api.summary import project_summary

    _render(repo, 1)
    disk = project_summary(Runtime(repo.root, fixed=True))["disk"]
    assert disk["renders_bytes"] > 0 and disk["total_bytes"] >= disk["renders_bytes"]
