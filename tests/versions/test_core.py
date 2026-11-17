"""record_version / next_version_id / git safety, on real temp-dir git repos."""

from __future__ import annotations

import json
import subprocess

import pytest

from muestra.errors import MuestraError
from muestra.project import create_project, next_version_id
from muestra.versions import core
from muestra.versions.core import commit_info, record_version, suggest_parent, version_tags

from .helpers import fake_render, git, rm_rf, song_text, write_song


def code(exc: pytest.ExceptionInfo[MuestraError]) -> str:
    return exc.value.issues[0].code


# --------------------------------------------------------------------------
# the happy path
# --------------------------------------------------------------------------


def test_first_version_in_a_brand_new_project(project):
    assert next_version_id(project) == "v001"
    sha = record_version(project, "v001", "hacé un loop", "Primer loop", "claude-code", None)
    assert git(project, "rev-parse", "HEAD") == sha
    # one annotated tag, pointing at that commit
    assert git(project, "cat-file", "-t", "refs/tags/v001") == "tag"
    assert git(project, "rev-parse", "refs/tags/v001^{commit}") == sha
    info = commit_info(project.root, "v001")
    assert info.subject == "v001: Primer loop"
    assert info.version == "v001"
    assert (info.request, info.summary, info.agent, info.parent) == (
        "hacé un loop",
        "Primer loop",
        "claude-code",
        None,
    )
    # nothing is left staged or dirty: the user's index is in sync with HEAD
    assert git(project, "status", "--porcelain") == ""


def test_rerender_of_unchanged_song_is_a_distinct_version(project):
    v1 = fake_render(project, summary="uno")
    v2 = fake_render(project, summary="dos")
    assert (v1, v2) == ("v001", "v002")
    tags = version_tags(project.root)
    assert tags["v001"] != tags["v002"]
    assert git(project, "rev-parse", "v001^{tree}") == git(project, "rev-parse", "v002^{tree}")
    assert commit_info(project.root, "v002").parent == "v001"


def test_metadata_only_change_still_commits(project):
    fake_render(project, request="a")
    fake_render(project, request="b")
    assert commit_info(project.root, "v002").request == "b"


def test_only_versioned_files_are_committed(project):
    (project.root / "sources").mkdir(exist_ok=True)
    (project.root / "sources" / "big.wav").write_bytes(b"RIFF" * 100)
    (project.root / "midi" / "bass.mid").write_bytes(b"MThd\x00\x00")
    (project.root / "renders" / "junk.txt").write_text("derived")
    record_version(project, "v001", None, None, None, None)
    files = git(project, "ls-tree", "-r", "--name-only", "v001").splitlines()
    assert "song.yaml" in files and "midi/bass.mid" in files and "muestra.toml" in files
    assert not any(f.startswith(("sources/", "renders/", "cache/")) for f in files)


def test_users_own_staging_area_is_never_touched(project):
    (project.root / "notes.txt").write_text("mine")
    git(project, "add", "notes.txt")
    write_song(project, song_text())
    record_version(project, "v001", None, "s", None, None)
    assert "notes.txt" not in git(project, "ls-tree", "-r", "--name-only", "v001")
    assert git(project, "diff", "--cached", "--name-only") == "notes.txt"


def test_summary_none_gives_bare_subject(project):
    record_version(project, "v001", None, None, None, None)
    info = commit_info(project.root, "v001")
    assert info.subject == "v001"
    assert info.summary is None and info.request is None and info.agent is None


def test_works_on_a_repo_with_no_commits_yet(tmp_path):
    root = tmp_path / "bare"
    (root / "renders").mkdir(parents=True)
    (root / "muestra.toml").write_text("schema_version = 0\n", encoding="utf-8")
    (root / "song.yaml").write_text(song_text(), encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    from muestra.project import Project, load_config

    proj = Project(root=root.resolve(), config=load_config(root))
    sha = record_version(proj, "v001", None, "x", None, None)
    assert git(proj, "rev-parse", "HEAD") == sha
    assert git(proj, "status", "--porcelain") == ""


# --------------------------------------------------------------------------
# unusual text: no shell injection, no mangled trailers
# --------------------------------------------------------------------------

NASTY_REQUESTS = [
    "dijo \"hacelo más fuerte\" y 'sí'",
    "línea uno\nlínea dos\r\nlínea tres",
    "emoji 🎛️🔥 y acentos: canción, añejo, ¿qué tal?",
    "--force --amend -m evil",
    "$(touch PWNED) `touch PWNED2` ; touch PWNED3 && echo hi",
    "C:\\Users\\yo\\barra\\invertida \\n literal",
    "Muestra-Parent: v999\nMuestra-Agent: fake",
    "# comentario que git podría tragarse",
    "tab\tseparado y  dobles  espacios",
]


@pytest.mark.parametrize("text", NASTY_REQUESTS)
def test_unusual_text_survives_verbatim_and_safely(project, text):
    record_version(project, "v001", text, text, text, None)
    info = commit_info(project.root, "v001")
    assert info.request == text
    assert info.summary == text
    assert info.agent == text
    assert info.parent is None  # the injected 'Muestra-Parent' stayed inside the request
    assert info.subject.startswith("v001: ") and "\n" not in info.subject
    assert not [p for p in project.root.iterdir() if p.name.startswith("PWNED")]
    assert git(project, "status", "--porcelain") == ""


def test_leading_dash_summary_is_not_an_option(project):
    record_version(project, "v001", "--no-verify", "--amend", "-x", None)
    info = commit_info(project.root, "v001")
    assert info.subject == "v001: --amend"
    assert info.request == "--no-verify"


def test_control_characters_are_dropped_not_fatal(project):
    record_version(project, "v001", "a\x00b\x07c", "s\x00", None, None)
    assert commit_info(project.root, "v001").request == "abc"


def test_very_long_summary_is_kept_in_full(project):
    long = "ritmo " * 300
    record_version(project, "v001", None, long, None, None)
    info = commit_info(project.root, "v001")
    assert len(info.subject) < 260
    assert info.summary == long.strip()


# --------------------------------------------------------------------------
# identity, hooks, signing, environment
# --------------------------------------------------------------------------


def test_fallback_identity_only_when_none_configured(project):
    record_version(project, "v001", None, None, None, None)
    assert git(project, "log", "-1", "--format=%an <%ae>", "v001") == "Muestra <muestra@localhost>"
    assert git(
        project, "for-each-ref", "--format=%(taggername) %(taggeremail)", "refs/tags/v001"
    ) == ("Muestra <muestra@localhost>")
    # config was never mutated
    assert git(project, "config", "--local", "--get", "user.name", check=False) == ""


def test_configured_identity_is_respected(project):
    git(project, "config", "user.name", "Lauti Test")
    git(project, "config", "user.email", "lauti@example.com")
    record_version(project, "v001", None, None, None, None)
    assert (
        git(project, "log", "-1", "--format=%an <%ae>", "v001") == "Lauti Test <lauti@example.com>"
    )


def test_failing_hooks_and_forced_signing_do_not_block_versions(project):
    hook = project.root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    git(project, "config", "commit.gpgsign", "true")
    git(project, "config", "tag.gpgSign", "true")
    git(project, "config", "gpg.program", "definitely-not-gpg")
    record_version(project, "v001", None, "ok", None, None)
    assert "v001" in version_tags(project.root)


def test_repo_redirecting_env_is_ignored(project, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "-C", str(other), "init", "-q"], check=True)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / "idx"))
    record_version(project, "v001", None, "ok", None, None)
    assert "v001" in version_tags(project.root)
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_INDEX_FILE")
    assert git(other, "rev-parse", "--verify", "-q", "HEAD", check=False) == ""


# --------------------------------------------------------------------------
# version ids are never reused
# --------------------------------------------------------------------------


def test_ids_never_reused_after_deleting_tag_and_renders(project):
    fake_render(project)
    fake_render(project)
    git(project, "tag", "-d", "v002")
    rm_rf(project.renders_dir / "v002")
    assert next_version_id(project) == "v003"  # the v002 commit is still in history


def test_ids_account_for_renders_dirs_and_feedback(project):
    (project.renders_dir / "v004").mkdir()
    assert next_version_id(project) == "v005"
    project.feedback_path.write_text(
        json.dumps(
            {"ts": "2026-11-17T20:00:00Z", "type": "rating", "version": "v009", "value": "like"}
        )
        + "\n",
        encoding="utf-8",
    )
    assert next_version_id(project) == "v010"


def test_hand_written_commit_titled_like_a_version_does_not_bump_ids(project):
    (project.root / "notes.txt").write_text("x")
    write_song(project, song_text())
    git(project, "add", "-A")
    git(
        project,
        "-c",
        "user.name=a",
        "-c",
        "user.email=b@c",
        "commit",
        "-q",
        "-m",
        "v100: mi release a mano",
    )
    assert next_version_id(project) == "v001"


def test_free_text_in_feedback_does_not_bump_ids(project):
    project.feedback_path.write_text(
        json.dumps(
            {"ts": "2026-11-17T20:00:00Z", "type": "comment", "text": "como el v2026 tour, v123"}
        )
        + "\nnot json v777\n",
        encoding="utf-8",
    )
    assert next_version_id(project) == "v001"


# --------------------------------------------------------------------------
# structured errors with fix hints
# --------------------------------------------------------------------------


def test_tag_exists(project):
    record_version(project, "v001", None, None, None, None)
    with pytest.raises(MuestraError) as e:
        record_version(project, "v001", None, None, None, None)
    assert code(e) == "version_exists"
    assert "next_version_id" in e.value.hint


def test_invalid_version_and_parent(project):
    with pytest.raises(MuestraError) as e:
        record_version(project, "1", None, None, None, None)
    assert code(e) == "invalid_value"
    with pytest.raises(MuestraError) as e:
        record_version(project, "v001", None, None, None, "bad")
    assert code(e) == "invalid_value"


def test_not_a_git_repo(project):
    rm_rf(project.root / ".git")
    with pytest.raises(MuestraError) as e:
        record_version(project, "v001", None, None, None, None)
    assert code(e) == "not_git_repo" and "git init" in e.value.hint


def test_never_commits_into_a_parent_repo(tmp_path):
    parent = tmp_path / "outer"
    parent.mkdir()
    subprocess.run(["git", "-C", str(parent), "init", "-q"], check=True)
    proj = create_project(parent / "proj")
    rm_rf(proj.root / ".git")  # project lost its own repo: it is now inside 'outer'
    before = git(parent, "rev-list", "--all", "--count", check=False)
    with pytest.raises(MuestraError) as e:
        record_version(proj, "v001", None, None, None, None)
    assert code(e) == "not_git_repo" and "top level" in e.value.issues[0].message
    assert git(parent, "rev-list", "--all", "--count", check=False) == before
    assert git(parent, "tag", "--list") == ""


def test_git_not_installed(project, monkeypatch):
    monkeypatch.setattr(core.shutil, "which", lambda *_a, **_k: None)
    with pytest.raises(MuestraError) as e:
        record_version(project, "v001", None, None, None, None)
    assert code(e) == "git_not_installed" and "Install git" in e.value.hint


def test_locked_index(project):
    (project.root / ".git" / "index.lock").write_text("", encoding="utf-8")
    with pytest.raises(MuestraError) as e:
        record_version(project, "v001", None, None, None, None)
    assert code(e) == "repo_locked" and "index.lock" in e.value.hint


def test_detached_head(project):
    git(project, "checkout", "-q", "--detach")
    with pytest.raises(MuestraError) as e:
        record_version(project, "v001", None, None, None, None)
    assert code(e) == "detached_head" and "switch" in e.value.hint


def test_rebase_or_merge_in_progress(project):
    (project.root / ".git" / "MERGE_HEAD").write_text(git(project, "rev-parse", "HEAD") + "\n")
    with pytest.raises(MuestraError) as e:
        record_version(project, "v001", None, None, None, None)
    assert code(e) == "repo_busy"


def test_missing_song_yaml(project):
    project.song_path.unlink()
    with pytest.raises(MuestraError) as e:
        record_version(project, "v001", None, None, None, None)
    assert code(e) == "file_not_found"


def test_failed_tag_rolls_the_commit_back(project, monkeypatch):
    head = git(project, "rev-parse", "HEAD")

    def boom(*_a, **_k):
        raise MuestraError.one("git_error", "tag failed")

    monkeypatch.setattr(core, "tag_version", boom)
    with pytest.raises(MuestraError):
        record_version(project, "v001", None, None, None, None)
    assert git(project, "rev-parse", "HEAD") == head
    assert git(project, "status", "--porcelain") == ""


# --------------------------------------------------------------------------
# history edited by hand
# --------------------------------------------------------------------------


def test_versions_survive_hand_made_commits_and_branch_names(project):
    fake_render(project)
    git(project, "branch", "-m", "trabajo")
    write_song(project, song_text(lambda d: d["meta"].update(bpm=130)))
    git(project, "add", "-A")
    git(project, "-c", "user.name=a", "-c", "user.email=b@c", "commit", "-q", "-m", "a mano")
    v2 = fake_render(project)
    assert v2 == "v002"
    assert suggest_parent(project) == "v002"
    assert commit_info(project.root, "v002").parent == "v001"


def test_parent_follows_manual_reset_to_an_older_version(project):
    fake_render(project)
    write_song(project, song_text(lambda d: d["meta"].update(bpm=140)))
    fake_render(project)
    git(project, "reset", "-q", "--hard", "v001")
    assert suggest_parent(project) == "v001"
    assert fake_render(project) == "v003"
    assert commit_info(project.root, "v003").parent == "v001"


def test_project_folder_with_spaces_and_accents(tmp_path):
    proj = create_project(tmp_path / "mi canción ñandú (v2)")
    v = fake_render(proj, request="ñ", summary="canción")
    assert v == "v001"
    assert commit_info(proj.root, v).summary == "canción"


def test_use_config_only_without_identity_still_versions(project, global_gitconfig):
    global_gitconfig.write_text("[user]\n\tuseConfigOnly = true\n", encoding="utf-8")
    record_version(project, "v001", None, "x", None, None)
    assert git(project, "log", "-1", "--format=%an", "v001") == "Muestra"


def test_ears_toml_is_versioned_like_muestra_toml(tmp_path):
    """WP11: ears.toml (ears thresholds/reference) is committed with every
    version; like muestra.toml it is configuration, so restore leaves it."""
    import subprocess

    from muestra.project import create_project
    from muestra.versions.core import record_version, restore_files

    project = create_project(tmp_path / "song")
    ears = project.root / "ears.toml"
    ears.write_bytes(b"[thresholds]\ntiming_warn_ms = 20.0\n")
    record_version(project, "v001", None, None, None, None)
    shown = subprocess.run(
        ["git", "-C", str(project.root), "show", "v001:ears.toml"],
        capture_output=True,
        check=True,
    ).stdout
    assert shown == b"[thresholds]\ntiming_warn_ms = 20.0\n"
    ears.write_bytes(b"[thresholds]\ntiming_warn_ms = 30.0\n")
    record_version(project, "v002", None, None, None, None)
    restore_files(project, "v001")
    assert ears.read_bytes().endswith(b"30.0\n")
