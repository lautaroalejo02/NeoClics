"""Project module: create/open, muestra.toml, path + library:// resolution,
id allocation, song loading and validation."""

from __future__ import annotations

import pytest

from muestra.errors import MuestraError
from muestra.project import (
    allocate_id,
    chop_times,
    create_project,
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
    assert reopened.config.library == ["library"]
    assert validate_song(reopened) == []


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


def test_library_uri_resolution(example_project):
    p = example_project.resolve_library("library://kits/disco-01/kick.wav")
    assert p.name == "kick.wav" and p.is_file()
    with pytest.raises(MuestraError) as exc:
        example_project.resolve_library("library://kits/disco-01/missing.wav")
    assert exc.value.issues[0].code == "file_not_found"
    # must_exist=False returns the first library dir's path
    p2 = example_project.resolve_library("library://kits/disco-01/missing.wav", must_exist=False)
    assert p2.name == "missing.wav"


def test_no_library_configured(tmp_path):
    dst = tmp_path / "proj"
    project = create_project(dst)
    save_config(project, ProjectConfig(library=[]))
    with pytest.raises(MuestraError) as exc:
        open_project(dst).resolve_library("library://a/b.wav")
    assert exc.value.issues[0].code == "invalid_uri"


def test_id_allocation(tmp_path, tmp_project, song_of):
    assert allocate_id("src", set()) == "src1"
    assert allocate_id("src", {"src1", "src2"}) == "src3"
    assert allocate_id("v", {"v001"}, width=3) == "v002"
    assert next_source_id(song_of) == "src2"
    assert next_version_id(tmp_project) == "v002"  # renders/v001 exists
    assert next_set_id(tmp_project, "src1") == "cs002"  # cs001.json exists
    assert next_set_id(tmp_project, "src9") == "cs001"


def test_chop_times(example_project):
    a = load_analysis(example_project, "src1")
    assert chop_times(a, 1, 8) == (0.25, 4.25)
    assert chop_times(a, 2, 4) == (2.25, 4.25)
    assert chop_times(a, 4, 8) is None  # start_downbeat out of range
    assert chop_times(a, 1, 99) is None  # overruns the beat grid
    # fractional tail interpolates between beats
    start, end = chop_times(a, 1, 8.5)
    assert (start, end) == (0.25, 4.5)


def test_validate_song_check_files_flag(tmp_path):
    dst = tmp_path / "proj"
    project = create_project(dst)
    (dst / "song.yaml").write_text(
        "schema_version: 0\n"
        "meta: {title: t, bpm: 120, key: Am, length_bars: 4}\n"
        "sources: [{id: src1, file: sources/missing.wav}]\n",
        encoding="utf-8",
    )
    with_files = validate_song(project, check_files=True)
    without = validate_song(project, check_files=False)
    assert any(i.code == "file_not_found" for i in with_files)
    assert without == []


def test_validate_catches_missing_analysis(tmp_path):
    dst = tmp_path / "proj"
    project = create_project(dst)
    (dst / "sources/x.wav").write_bytes(b"RIFF")
    (dst / "song.yaml").write_text(
        "schema_version: 0\n"
        "meta: {title: t, bpm: 120, key: Am, length_bars: 4}\n"
        "sources: [{id: src1, file: sources/x.wav}]\n"
        "chops: [{id: c1, source: src1, stem: mix, start_downbeat: 1, length_beats: 4}]\n",
        encoding="utf-8",
    )
    issues = validate_song(project, check_files=True)
    assert any(i.path == "chops[0]" and i.code == "file_not_found" for i in issues)
    assert validate_song(project, check_files=False) == []
