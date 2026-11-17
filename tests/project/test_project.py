"""Project module: create/open (git init), muestra.toml, path + library://
resolution, id allocation, song loading, round-trip editing, validation."""

from __future__ import annotations

import subprocess

import pytest

from muestra.errors import MuestraError
from muestra.project import (
    allocate_id,
    chop_times,
    create_project,
    edit_song_document,
    load_analysis,
    load_config,
    next_set_id,
    next_source_id,
    next_version_id,
    open_project,
    save_config,
    validate_song,
)
from muestra.schemas import ProjectConfig
from muestra.schemas.config import LibraryConfig

from ..conftest import write_song


def test_create_and_open(tmp_path):
    dst = tmp_path / "my-song"
    create_project(dst)
    for d in (
        "sources",
        "cache/stems",
        "cache/analysis",
        "cache/previews",
        "cache/chops",
        "midi",
        "renders",
        "library",
        "docs",
    ):
        assert (dst / d).is_dir(), d
    assert (dst / "muestra.toml").is_file()
    assert (dst / "song.yaml").is_file()
    assert (dst / "feedback.jsonl").is_file()

    reopened = open_project(dst)
    assert reopened.config.library.roots == ["library"]
    assert validate_song(reopened) == []


def test_create_git_inits_and_ignores_derived(tmp_path):
    dst = tmp_path / "my-song"
    create_project(dst)
    # a Muestra project is its own git repo with one scaffold commit
    assert (dst / ".git").is_dir()
    log = subprocess.run(
        ["git", "-C", str(dst), "log", "--oneline"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "Initial muestra project" in log
    # renders/ and cache/ are entirely git-ignored (derived data)
    assert "renders/" in (dst / ".gitignore").read_text(encoding="utf-8")
    assert "cache/" in (dst / ".gitignore").read_text(encoding="utf-8")
    check = subprocess.run(
        ["git", "-C", str(dst), "check-ignore", "renders/x", "cache/x"],
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0


def test_create_refuses_nonempty_dir(tmp_path):
    dst = tmp_path / "proj"
    create_project(dst)
    with pytest.raises(MuestraError) as exc:
        create_project(dst)
    assert exc.value.issues[0].code == "already_exists"


def test_open_missing_project(tmp_path):
    with pytest.raises(MuestraError) as exc:
        open_project(tmp_path / "nope")
    assert exc.value.issues[0].code == "file_not_found"


def test_config_round_trip(tmp_project):
    cfg = load_config(tmp_project.root)
    cfg.sample_rate = 44100
    save_config(tmp_project, cfg)
    assert load_config(tmp_project.root).sample_rate == 44100


def test_config_fixture_sections(example_project):
    cfg = example_project.config
    assert cfg.library.roots == ["../library"]
    assert "surge-xt" in cfg.plugins.aliases
    assert cfg.plugins.aliases["surge-xt"].macos


def test_library_uri_resolution(example_project):
    p = example_project.resolve_library("library://kits/disco-01/kick.wav")
    assert p.name == "kick.wav" and p.is_file()
    with pytest.raises(MuestraError) as exc:
        example_project.resolve_library("library://kits/disco-01/missing.wav")
    assert exc.value.issues[0].code == "file_not_found"
    # must_exist=False returns the first library dir's path
    p2 = example_project.resolve_library("library://kits/disco-01/missing.wav", must_exist=False)
    assert p2.name == "missing.wav"


def test_library_index_lookup(example_project):
    from muestra.library.core import lookup

    hit = lookup(example_project, "library://kits/disco-01/kick.wav")
    assert hit is not None and hit.type == "kick" and hit.bpm is None
    assert lookup(example_project, "library://kits/disco-01/missing.wav") is None


def test_no_library_configured(tmp_path):
    dst = tmp_path / "proj"
    project = create_project(dst)
    save_config(project, ProjectConfig(library=LibraryConfig(roots=[])))
    with pytest.raises(MuestraError) as exc:
        open_project(dst).resolve_library("library://a/b.wav")
    assert exc.value.issues[0].code == "invalid_uri"


def test_id_allocation(tmp_path, tmp_project, song_of):
    assert allocate_id("src", set()) == "src1"
    assert allocate_id("src", {"src1", "src2"}) == "src3"
    assert allocate_id("v", {"v001"}, width=3) == "v002"
    assert next_source_id(song_of) == "src2"
    # max+1 over tags/renders/feedback: feedback.jsonl mentions v002
    assert next_version_id(tmp_project) == "v003"
    assert next_set_id(tmp_project, "src1") == "cs002"  # cs001.json exists
    assert next_set_id(tmp_project, "src9") == "cs001"


def test_version_id_never_fills_gaps(tmp_path):
    dst = tmp_path / "proj"
    project = create_project(dst)
    (dst / "renders" / "v001").mkdir(parents=True)
    (dst / "renders" / "v007").mkdir()
    assert next_version_id(project) == "v008"


def test_chop_times(example_project):
    a = load_analysis(example_project, "src1")
    assert chop_times(a, 1, 8) == (0.25, 4.25)
    assert chop_times(a, 2, 4) == (2.25, 4.25)
    assert chop_times(a, 4, 8) is None  # start_downbeat out of range
    assert chop_times(a, 1, 99) is None  # overruns the beat grid
    # start comes from beats_s at the beat NEAREST the downbeat
    assert chop_times(a, 1, 4, start_offset_beats=1) == (0.75, 2.75)
    assert chop_times(a, 1, 4, start_offset_beats=-0.5) == (0.0, 2.0)
    # fractional tail interpolates between beats
    start, end = chop_times(a, 1, 8.5)
    assert (start, end) == (0.25, 4.5)


def test_validate_song_check_files_flag(tmp_path):
    dst = tmp_path / "proj"
    project = create_project(dst)
    write_song(
        dst,
        "schema_version: 0\n"
        "meta: {title: t, bpm: 120, key: Am, length_bars: 4}\n"
        "sources: [{id: src1, file: sources/missing.wav}]\n",
    )
    with_files = validate_song(project, check_files=True)
    without = validate_song(project, check_files=False)
    assert any(i.code == "file_not_found" for i in with_files)
    assert without == []


def test_validate_catches_missing_analysis(tmp_path):
    dst = tmp_path / "proj"
    project = create_project(dst)
    (dst / "sources/x.wav").write_bytes(b"RIFF")
    write_song(
        dst,
        "schema_version: 0\n"
        "meta: {title: t, bpm: 120, key: Am, length_bars: 4}\n"
        "sources: [{id: src1, file: sources/x.wav}]\n"
        "chops: [{id: c1, source: src1, stem: mix, start_downbeat: 1, length_beats: 4}]\n",
    )
    issues = validate_song(project, check_files=True)
    assert any(i.path == "chops[0]" and i.code == "file_not_found" for i in issues)
    assert validate_song(project, check_files=False) == []


def test_sample_warp_needs_indexed_bpm(tmp_path, tmp_project):
    # a clip 'sample' with 'warp' but no indexed BPM is a validation error
    write_song(
        tmp_project.root,
        "schema_version: 0\n"
        "meta: {title: t, bpm: 120, key: Am, length_bars: 4}\n"
        "tracks:\n"
        "  - id: s\n"
        "    clips:\n"
        "      - {sample: 'library://kits/disco-01/kick.wav', length_beats: 4, "
        "at_bar: 1, warp: {mode: resample}}\n",
    )
    issues = validate_song(tmp_project, check_files=True)
    assert any(i.code == "invalid_value" and "BPM" in i.message for i in issues)


def test_duplicate_yaml_keys_are_an_issue(tmp_path):
    dst = tmp_path / "proj"
    project = create_project(dst)
    write_song(
        dst,
        "schema_version: 0\n"
        "meta: {title: t, bpm: 120, key: Am, length_bars: 4}\n"
        "meta: {title: t, bpm: 130, key: Am, length_bars: 4}\n",
    )
    issues = validate_song(project, check_files=False)
    assert any(i.code == "duplicate_key" for i in issues)


def test_case_mismatch_detected(tmp_path, tmp_project):
    # exact-case checking works on every OS (incl. case-insensitive ones)
    issues = []
    try:
        tmp_project.resolve("SOURCES/fake-source.wav", must_exist=True)
    except MuestraError as e:
        issues = e.issues
    assert issues and issues[0].code == "case_mismatch"


def test_edit_song_document_preserves_comments(tmp_path, tmp_project):
    """A programmatic edit keeps comments, ordering and omitted defaults."""
    before = (tmp_project.root / "song.yaml").read_text(encoding="utf-8")
    assert "# vinyl style" in before  # fixture carries comments

    def add_source(doc):
        doc["sources"].append({"id": "src2", "file": "sources/b.wav"})

    edit_song_document(tmp_project, add_source)
    after = (tmp_project.root / "song.yaml").read_text(encoding="utf-8")
    assert "# vinyl style" in after  # comments survive
    assert "id: src2" in after
    assert "fade_ms" in after  # untouched entries keep explicit fields
    # the file still validates
    from muestra.project import load_song

    song = load_song(tmp_project)
    assert {s.id for s in song.sources} == {"src1", "src2"}
