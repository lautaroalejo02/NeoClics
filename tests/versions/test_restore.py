"""versions.restore: byte-for-byte, never loses work, undoable, parent tree."""

from __future__ import annotations

import pytest

from muestra.errors import MuestraError
from muestra.registry import run_tool
from muestra.versions.core import commit_info, restore_files, suggest_parent, version_tags
from muestra.versions.records import build_records

from .helpers import (
    delete_renders,
    fake_render,
    git,
    git_bytes,
    song_text,
    write_song,
)

ORIGINAL = "# mi canción\n" + song_text() + "\n# fin\n"


def restore(project, version):
    res = run_tool("versions.restore", {"project": str(project.root), "version": version})
    assert res.ok, res.errors
    return res.data


def test_restore_is_byte_for_byte_with_comments_and_crlf(project):
    original = ORIGINAL.replace("\n", "\r\n").encode("utf-8")  # what Windows editors write
    write_song(project, original)
    v1 = fake_render(project)
    write_song(project, song_text(lambda d: d["meta"].update(bpm=99)))
    fake_render(project)
    write_song(project, "meta: {title: roto}\n")
    restore(project, v1)
    assert project.song_path.read_bytes() == original


@pytest.mark.parametrize("renders_present", [True, False], ids=["snapshot", "git-fallback"])
def test_byte_for_byte_with_autocrlf_true(project, global_gitconfig, renders_present):
    """Git for Windows defaults to core.autocrlf=true; it must not alter song.yaml."""
    global_gitconfig.write_text("[core]\n\tautocrlf = true\n\tsafecrlf = warn\n", encoding="utf-8")
    original = ORIGINAL.replace("\n", "\r\n").encode("utf-8")
    write_song(project, original)
    v1 = fake_render(project)
    # the blob in git is the CRLF bytes, untouched
    assert git_bytes(project, "cat-file", "blob", f"{v1}:song.yaml") == original
    write_song(project, b"changed: true\n")
    fake_render(project)
    if not renders_present:
        delete_renders(project)
    data = restore(project, v1)
    assert project.song_path.read_bytes() == original
    assert data["snapshot_source"] == ("renders" if renders_present else "git")
    # and the working tree is clean afterwards (no phantom CRLF "modifications")
    assert git(project, "status", "--porcelain") == ""


def test_lf_files_stay_lf_even_with_autocrlf_true(project, global_gitconfig):
    global_gitconfig.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")
    write_song(project, ORIGINAL.encode("utf-8"))
    v1 = fake_render(project)
    delete_renders(project)
    write_song(project, b"x: 1\n")
    restore(project, v1)
    assert project.song_path.read_bytes() == ORIGINAL.encode("utf-8")
    assert b"\r" not in project.song_path.read_bytes()


def test_uncommitted_work_is_saved_first_and_undoable(project):
    write_song(project, ORIGINAL)
    v1 = fake_render(project)
    write_song(project, song_text(lambda d: d["meta"].update(bpm=111)))
    v2 = fake_render(project)
    work_in_progress = "# mi laburo sin renderizar\n" + song_text(
        lambda d: d["meta"].update(bpm=77)
    )
    write_song(project, work_in_progress)
    head_before = git(project, "rev-parse", "HEAD")

    data = restore(project, v1)
    assert project.song_path.read_text(encoding="utf-8") == ORIGINAL
    assert data["changed"] is True
    snap = data["snapshot_commit"]
    assert snap and data["previous_commit"] == head_before
    # the edits live in a commit, not nowhere
    assert (
        git(project, "show", f"{snap}:song.yaml").replace("\r\n", "\n") == work_in_progress.strip()
    )
    # no tag was created by the restore
    assert sorted(version_tags(project.root)) == [v1, v2]
    # the hint says how to undo, and it works
    assert "restore --source=" + snap in data["undo"] and "To undo" in data["hint"]
    assert data["undo"].endswith("-- song.yaml")  # no midi files in this project
    git(project, "restore", f"--source={snap}", "--", "song.yaml")
    assert project.song_path.read_text(encoding="utf-8") == work_in_progress


def test_clean_restore_has_no_snapshot_commit_and_undo_points_at_previous_head(project):
    v1 = fake_render(project)
    write_song(project, song_text(lambda d: d["meta"].update(bpm=90)))
    fake_render(project)
    head = git(project, "rev-parse", "HEAD")
    data = restore(project, v1)
    assert data["snapshot_commit"] is None
    assert data["previous_commit"] == head
    assert head in data["undo"]


def test_restore_does_not_render_and_leaves_renders_and_feedback_alone(project):
    v1 = fake_render(project)
    write_song(project, song_text(lambda d: d["meta"].update(bpm=90)))
    v2 = fake_render(project)
    project.feedback_path.write_text('{"keep": "me"}\n', encoding="utf-8")
    snapshot = (project.renders_dir / v2 / "song.yaml").read_bytes()
    restore(project, v1)
    assert sorted(p.name for p in project.renders_dir.iterdir()) == [v1, v2]
    assert (project.renders_dir / v2 / "song.yaml").read_bytes() == snapshot
    assert project.feedback_path.read_text(encoding="utf-8") == '{"keep": "me"}\n'
    assert git(project, "status", "--porcelain") == ""


def test_restore_then_render_makes_a_tree(project):
    v1 = fake_render(project)
    write_song(project, song_text(lambda d: d["meta"].update(bpm=90)))
    v2 = fake_render(project)
    write_song(project, song_text(lambda d: d["meta"].update(bpm=95)))
    v3 = fake_render(project)
    assert (v1, v2, v3) == ("v001", "v002", "v003")
    assert suggest_parent(project) == v3

    restore(project, v1)
    assert suggest_parent(project) == v1
    v4 = fake_render(project)
    assert v4 == "v004"
    assert commit_info(project.root, v4).parent == v1

    records, roots, base = build_records(project)
    by = {r.version: r for r in records}
    assert [r.version for r in records] == ["v004", "v003", "v002", "v001"]  # newest first
    assert by["v001"].children == ["v002", "v004"]
    assert by["v002"].children == ["v003"]
    assert by["v004"].parent == "v001" and by["v003"].parent == "v002"
    assert roots == ["v001"]
    assert base == "v004" and by["v004"].is_base and not by["v001"].is_base


def test_restore_twice_and_restore_current(project):
    write_song(project, ORIGINAL)
    v1 = fake_render(project)
    data = restore(project, v1)  # already identical
    assert data["changed"] is False
    assert project.song_path.read_text(encoding="utf-8") == ORIGINAL
    write_song(project, "other: 1\n")
    restore(project, v1)
    restore(project, v1)
    assert project.song_path.read_text(encoding="utf-8") == ORIGINAL


def test_midi_files_are_restored_binary_exact(project):
    midi = project.root / "midi"
    blob1 = b"MThd\x00\x00\x00\x06\r\n\x00\x01\x00\x01\x01\xe0"
    (midi / "bass.mid").write_bytes(blob1)
    v1 = fake_render(project)
    (midi / "bass.mid").write_bytes(b"MThd-changed")
    (midi / "extra.mid").write_bytes(b"MThd-extra")
    fake_render(project)
    (midi / "extra.mid").write_bytes(b"MThd-extra-edited-after")
    data = restore(project, v1)
    assert (midi / "bass.mid").read_bytes() == blob1
    assert not (midi / "extra.mid").exists()  # wasn't part of v001...
    # ...but is not lost: it sits in the commit before the restore
    pre = data["previous_commit"] if not data["snapshot_commit"] else data["snapshot_commit"]
    assert git_bytes(project, "cat-file", "blob", f"{pre}:midi/extra.mid") == (
        b"MThd-extra-edited-after"
    )
    assert "midi/bass.mid" in data["files"]


def test_ignored_midi_files_are_never_deleted(project):
    (project.root / ".gitignore").write_text(
        "renders/\ncache/\nmidi/scratch.mid\n", encoding="utf-8"
    )
    v1 = fake_render(project)
    (project.root / "midi" / "scratch.mid").write_bytes(b"mine")
    restore(project, v1)
    assert (project.root / "midi" / "scratch.mid").read_bytes() == b"mine"


def test_restore_unknown_version_is_a_structured_error(project):
    fake_render(project)
    res = run_tool("versions.restore", {"project": str(project.root), "version": "v042"})
    assert not res.ok
    assert res.errors[0].code == "unknown_version"
    assert "v001" in res.hint and "versions.list" in res.hint
    # nothing happened: still one scaffold commit + v001
    assert git(project, "status", "--porcelain") == ""


def test_restore_when_the_tag_was_deleted_by_hand_uses_renders(project):
    write_song(project, ORIGINAL)
    v1 = fake_render(project)
    write_song(project, "x: 1\n")
    git(project, "tag", "-d", v1)
    data = restore(project, v1)
    assert project.song_path.read_text(encoding="utf-8") == ORIGINAL
    assert data["snapshot_source"] == "renders"
    assert any("No git tag" in w for w in data["warnings"])


def test_restore_refuses_a_locked_or_foreign_repo(project):
    v1 = fake_render(project)
    (project.root / ".git" / "index.lock").write_text("", encoding="utf-8")
    with pytest.raises(MuestraError) as e:
        restore_files(project, v1)
    assert e.value.issues[0].code == "repo_locked"


def test_restore_works_after_a_hand_edited_song_with_staged_changes(project):
    write_song(project, ORIGINAL)
    v1 = fake_render(project)
    write_song(project, "staged: true\n")
    git(project, "add", "song.yaml")  # the user staged an edit by hand
    restore(project, v1)
    assert project.song_path.read_text(encoding="utf-8") == ORIGINAL
    assert git(project, "status", "--porcelain") == ""
