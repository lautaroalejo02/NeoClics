"""The four versions.* tools through the registry envelope."""

from __future__ import annotations

import json

import pytest

from muestra.feedback import append_event
from muestra.registry import REGISTRY, run_tool
from muestra.schemas.feedback import ABChoiceEvent, CommentEvent, RatingEvent
from muestra.versions.core import commit_info

from .helpers import (
    delete_renders,
    fake_render,
    git,
    make_metrics,
    rm_rf,
    song_text,
    track,
    write_song,
)


def call(project, name, **params):
    return run_tool(name, {"project": str(project.root), **params})


def ok(project, name, **params):
    res = call(project, name, **params)
    assert res.ok, (res.errors, res.hint)
    return res.data


def rating(version, value, ts):
    return RatingEvent(ts=ts, version=version, value=value)


@pytest.fixture
def three(project):
    """v001 -> v002 (bass -2 dB) -> v003 (bpm 128), with feedback."""
    write_song(project, song_text())
    fake_render(project, request="loop de 16", summary="Primer loop", agent="claude-code")
    write_song(project, song_text(lambda d: track(d, "bass").update(gain_db=-2)))
    fake_render(project, request="bajá el bajo", summary="Bajé el bajo", agent="claude-code")
    write_song(
        project,
        song_text(lambda d: (track(d, "bass").update(gain_db=-2), d["meta"].update(bpm=128))),
    )
    fake_render(project, request="más rápido", summary="128 BPM", agent="codex")
    append_event(project, rating("v001", "like", "2026-11-17T20:00:00Z"))
    append_event(project, rating("v002", "dislike", "2026-11-17T20:01:00Z"))
    append_event(project, rating("v002", "like", "2026-11-17T20:05:00Z"))
    append_event(project, CommentEvent(ts="2026-11-17T20:06:00Z", version="v002", text="ok"))
    append_event(project, CommentEvent(ts="2026-11-17T20:07:00Z", version="v002", text="mejor"))
    append_event(
        project, ABChoiceEvent(ts="2026-11-17T20:08:00Z", a="v001", b="v002", winner="v002")
    )
    append_event(
        project, ABChoiceEvent(ts="2026-11-17T20:09:00Z", a="v002", b="v003", winner="v002")
    )
    return project


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------


def test_tools_are_registered_with_their_flags():
    for name, readonly in (
        ("versions.list", True),
        ("versions.get", True),
        ("versions.diff", True),
        ("versions.restore", False),
    ):
        spec = REGISTRY[name]
        assert spec.readonly is readonly and spec.cli == ("versions", name.split(".")[1])


# --------------------------------------------------------------------------
# versions.list
# --------------------------------------------------------------------------


def test_list_merges_git_renders_and_feedback(three):
    data = ok(three, "versions.list")
    assert [v["version"] for v in data["versions"]] == ["v003", "v002", "v001"]
    by = {v["version"]: v for v in data["versions"]}
    v2 = by["v002"]
    assert (v2["request"], v2["summary"], v2["agent"]) == (
        "bajá el bajo",
        "Bajé el bajo",
        "claude-code",
    )
    assert (
        v2["parent"] == "v001" and by["v003"]["parent"] == "v002" and by["v001"]["parent"] is None
    )
    assert v2["git_commit"] == commit_info(three.root, "v002").sha
    assert v2["created_at"] is not None
    assert v2["rendered"] and v2["has_meta"] and v2["has_stems"] and not v2["has_metrics"]
    assert not v2["rerenderable"]
    # feedback: latest rating wins; counts
    assert v2["liked"] is True and v2["feedback"]["rating"] == "like"
    assert v2["feedback"]["comments"] == 2
    assert (v2["feedback"]["ab_wins"], v2["feedback"]["ab_losses"]) == (2, 0)
    assert by["v001"]["feedback"]["ab_losses"] == 1 and by["v001"]["liked"] is True
    assert by["v003"]["liked"] is None and by["v003"]["feedback"]["ab_losses"] == 1
    assert data["roots"] == ["v001"] and data["base"] == "v003"
    assert by["v003"]["is_base"] and not by["v001"]["is_base"]
    assert by["v001"]["children"] == ["v002"]


def test_list_marks_versions_whose_renders_were_deleted_as_rerenderable(three):
    delete_renders(three)
    data = ok(three, "versions.list")
    assert len(data["versions"]) == 3
    for v in data["versions"]:
        assert v["rerenderable"] is True and v["rendered"] is False
        assert not v["has_meta"] and not v["has_stems"]
        # request/summary/agent/parent still come from the git trailers
        assert v["request"] and v["summary"] and v["agent"]
    by = {v["version"]: v for v in data["versions"]}
    assert by["v003"]["parent"] == "v002" and by["v003"]["agent"] == "codex"


def test_list_of_a_project_without_versions(project):
    data = ok(project, "versions.list")
    assert data == {"files": [], "versions": [], "roots": [], "base": None}


def test_list_shows_a_render_folder_that_has_no_tag(project):
    d = project.renders_dir / "v007"
    d.mkdir()
    (d / "mix.wav").write_bytes(b"x")
    (d / "song.yaml").write_text(song_text(), encoding="utf-8")
    (v,) = ok(project, "versions.list")["versions"]
    assert v["version"] == "v007" and v["rendered"] and v["git_commit"] is None


def test_list_survives_a_corrupt_meta_json_and_reports_it(three):
    (three.renders_dir / "v002" / "meta.json").write_text("{not json", encoding="utf-8")
    data = ok(three, "versions.list")
    v2 = next(v for v in data["versions"] if v["version"] == "v002")
    assert v2["has_meta"] is False and v2["request"] == "bajá el bajo"  # from git
    assert any("meta.json" in w for w in v2["warnings"])


def test_list_warns_when_history_was_edited_by_hand(three):
    meta_path = three.renders_dir / "v002" / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["git_commit"] = "deadbeef" * 5
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    v2 = next(v for v in ok(three, "versions.list")["versions"] if v["version"] == "v002")
    assert any("history edited by hand" in w for w in v2["warnings"])


def test_list_with_a_dangling_parent_is_flagged_not_fatal(three):
    git(three, "tag", "-d", "v001")
    rm_rf(three.renders_dir / "v001")
    data = ok(three, "versions.list")
    by = {v["version"]: v for v in data["versions"]}
    assert "v001" not in by
    assert any("not a known version" in w for w in by["v002"]["warnings"])
    assert "v002" in data["roots"]


def test_list_errors_are_structured(project):
    rm_rf(project.root / ".git")
    res = call(project, "versions.list")
    assert not res.ok and res.errors[0].code == "not_git_repo" and "git init" in res.hint


# --------------------------------------------------------------------------
# versions.get
# --------------------------------------------------------------------------


def test_get_returns_meta_song_metrics_and_stems(three):
    (three.renders_dir / "v002" / "metrics.json").write_text(
        make_metrics().model_copy(update={"version": "v002"}).model_dump_json(), encoding="utf-8"
    )
    compact = ok(three, "versions.get", version="v002")
    assert compact["metrics"] is None and compact["song_yaml"] is None
    assert compact["metrics_path"] == "renders/v002/metrics.json"
    assert compact["metrics_summary"] == make_metrics().summary
    data = ok(three, "versions.get", version="v002", detail=True)
    assert data["version"] == "v002" and data["song_source"] == "renders"
    assert data["meta"]["request"] == "bajá el bajo"
    assert "gain_db: -2" in data["song_yaml"]
    assert data["metrics"]["loudness"]["integrated_lufs"] == -14.0
    assert data["mix"] == "renders/v002/mix.wav" and data["stems"] == [
        "renders/v002/stems/bass.wav"
    ]
    assert data["info"]["version"] == "v002" and data["info"]["feedback"]["comments"] == 2


def test_get_falls_back_to_git_when_renders_are_gone(three):
    delete_renders(three)
    data = ok(three, "versions.get", version="v001", detail=True)
    assert data["song_source"] == "git" and data["meta"] is None
    assert data["mix"] is None and data["stems"] == [] and data["info"]["rerenderable"]
    assert data["song_yaml"].replace("\r\n", "\n") == song_text()


def test_get_unknown_version(three):
    res = call(three, "versions.get", version="v099")
    assert not res.ok
    assert res.errors[0].code == "unknown_version" and "v003" in res.hint


def test_get_rejects_a_malformed_version_id(three):
    res = call(three, "versions.get", version="latest")
    assert not res.ok and res.errors[0].path == "version"


# --------------------------------------------------------------------------
# versions.diff
# --------------------------------------------------------------------------


def test_diff_between_two_versions(three):
    data = ok(three, "versions.diff", a="v001", b="v003")
    assert sorted(e["path"] for e in data["diff"]) == ["meta.bpm", "tracks.bass.gain_db"]
    assert data["changes"] == ["Subí el tempo de 120 a 128 BPM", "Bajé el bajo 2 dB"]
    assert data["musical_summary"] == "Subí el tempo de 120 a 128 BPM. Bajé el bajo 2 dB."
    assert data["identical"] is False and data["metrics_delta"] == []
    # the contract serialisation: 'from', not 'from_'
    assert {"path": "meta.bpm", "from": 120.0, "to": 128.0} in data["diff"]


def test_diff_is_symmetric_in_meaning_and_identical_for_same_song(three):
    back = ok(three, "versions.diff", a="v003", b="v001")
    assert back["changes"] == ["Bajé el tempo de 128 a 120 BPM", "Subí el bajo 2 dB"]
    same = ok(three, "versions.diff", a="v002", b="v002")
    assert (
        same["identical"]
        and same["diff"] == []
        and same["musical_summary"] == ("Sin cambios musicales.")
    )


def test_diff_works_from_git_when_renders_are_deleted(three):
    delete_renders(three)
    data = ok(three, "versions.diff", a="v001", b="v002")
    assert data["changes"] == ["Bajé el bajo 2 dB"]


def test_diff_with_metrics_appends_the_ears_delta(project):
    """WP11: versions.diff uses the ears comparison (structured + its notes)."""
    write_song(project, song_text())
    fake_render(project, metrics=make_metrics(lufs=-14, low=-20))
    write_song(project, song_text(lambda d: track(d, "bass").update(gain_db=3)))
    fake_render(project, metrics=make_metrics(lufs=-12.8, low=-17, high=-33))
    data = ok(project, "versions.diff", a="v001", b="v002")
    structured = data["metrics_compare"]
    assert structured["loudness"] == "louder" and structured["lufs_delta"] == 1.2
    assert data["metrics_delta"] == structured["notes"] and structured["notes"]
    assert data["metrics_delta"][0].startswith("más fuerte")
    assert data["musical_summary"].startswith("Subí el bajo 3 dB.")


def test_get_meta_diff_entries_use_the_contract_name_from(project):
    fake_render(project)
    meta_path = project.renders_dir / "v001" / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["diff"] = [{"path": "meta.bpm", "from": 120, "to": 128}]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    data = ok(project, "versions.get", version="v001")
    assert data["meta"]["diff"] == [{"path": "meta.bpm", "from": 120, "to": 128}]


def test_diff_falls_back_to_the_local_delta_when_ears_cannot_compare(project, monkeypatch):
    import muestra.ears.report as ears_report

    def broken(a, b):
        raise ValueError("incomparable")

    monkeypatch.setattr(ears_report, "compare_metrics", broken)
    fake_render(project, metrics=make_metrics(lufs=-14))
    fake_render(project, metrics=make_metrics(lufs=-12.8))
    data = ok(project, "versions.diff", a="v001", b="v002")
    assert data["metrics_delta"] == ["el mix quedó 1.2 LU más fuerte"]
    assert data["metrics_compare"] is None


def test_diff_ignores_metrics_when_only_one_side_has_them(project):
    fake_render(project, metrics=make_metrics())
    fake_render(project)
    assert ok(project, "versions.diff", a="v001", b="v002")["metrics_delta"] == []


def test_diff_unknown_version_and_broken_snapshot_are_structured(three):
    res = call(three, "versions.diff", a="v001", b="v050")
    assert not res.ok and res.errors[0].code == "unknown_version"
    (three.renders_dir / "v002" / "song.yaml").write_text("meta: [unclosed", encoding="utf-8")
    res = call(three, "versions.diff", a="v001", b="v002")
    assert not res.ok and res.errors[0].code == "parse_error"
    assert "v002:song.yaml" in res.errors[0].path
    (three.renders_dir / "v002" / "song.yaml").write_text("meta: {title: x}\n", encoding="utf-8")
    res = call(three, "versions.diff", a="v001", b="v002")
    assert not res.ok and {i.code for i in res.errors} >= {"missing_field"}


def test_diff_reports_unicode_and_odd_ids_safely(project):
    write_song(project, song_text(lambda d: d["meta"].update(title="canción ñandú 🎛️")))
    fake_render(project)
    write_song(project, song_text(lambda d: d["meta"].update(title="otra “canción”")))
    fake_render(project)
    data = ok(project, "versions.diff", a="v001", b="v002")
    assert data["changes"] == ["Renombré el tema a 'otra “canción”'"]
