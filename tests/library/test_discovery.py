"""Library discovery precedence and the core lookup/resolve seams."""

from __future__ import annotations

import pytest

from muestra.errors import MuestraError
from muestra.library import discovery
from muestra.library.core import loaded_index, lookup, resolve
from muestra.library.index import index_root
from muestra.project import create_project, open_project, save_config
from muestra.schemas import LibraryConfig, ProjectConfig

from .conftest import kick, write


@pytest.fixture
def default_lib(tmp_path, monkeypatch):
    d = tmp_path / "home" / "Documents" / "MuestraLibrary"
    d.mkdir(parents=True)
    monkeypatch.setattr(discovery, "default_library_path", lambda: d)
    return d


def _project(tmp_path, roots=None, *, explicit=True):
    cfg = ProjectConfig(library=LibraryConfig(roots=roots)) if explicit else ProjectConfig()
    return create_project(tmp_path / "proj", cfg)


def test_precedence_toml_then_env_then_default(tmp_path, monkeypatch, default_lib):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    p = _project(tmp_path, [a.as_posix()])
    monkeypatch.setenv("MUESTRA_LIBRARY", str(b))
    assert [r.name for r in p.library_dirs()] == ["a", "b"]  # toml first, then env
    monkeypatch.delenv("MUESTRA_LIBRARY")
    assert [r.name for r in p.library_dirs()] == ["a", "MuestraLibrary"]  # then the default


def test_env_var_accepts_several_roots_and_expands_home(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    import os

    monkeypatch.setenv("MUESTRA_LIBRARY", os.pathsep.join([str(a), str(b)]))
    assert [r.name for r in discovery.env_roots()] == ["a", "b"]


def test_default_library_is_used_when_nothing_else_is_configured(
    tmp_path, default_lib, monkeypatch
):
    monkeypatch.delenv("MUESTRA_LIBRARY")
    p = _project(tmp_path, ["library"])  # the project's own (empty) library dir
    dirs = p.library_dirs()
    assert dirs[0] == (p.root / "library").resolve() and dirs[-1] == default_lib.resolve()


def test_default_is_skipped_when_it_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.delenv("MUESTRA_LIBRARY")
    monkeypatch.setattr(discovery, "default_library_path", lambda: tmp_path / "missing")
    p = _project(tmp_path, ["library"])
    assert [d.name for d in p.library_dirs()] == ["library"]


def test_opt_outs(tmp_path, monkeypatch, default_lib):
    # 1. explicitly empty roots: the project is authoritative
    monkeypatch.delenv("MUESTRA_LIBRARY")
    p = _project(tmp_path, [])
    assert p.library_dirs() == []
    # 2. MUESTRA_LIBRARY=none / empty disables env + default for everyone
    q = create_project(tmp_path / "proj2", ProjectConfig(library=LibraryConfig(roots=["library"])))
    assert len(q.library_dirs()) == 2
    for off in ("none", "", "NONE"):
        monkeypatch.setenv("MUESTRA_LIBRARY", off)
        assert [d.name for d in q.library_dirs()] == ["library"]


def test_env_var_wins_over_the_default(tmp_path, monkeypatch, default_lib):
    mine = tmp_path / "mine"
    mine.mkdir()
    monkeypatch.setenv("MUESTRA_LIBRARY", str(mine))
    p = _project(tmp_path, ["library"])
    assert [d.name for d in p.library_dirs()] == ["library", "mine"]  # default not appended


def test_duplicate_roots_are_listed_once(tmp_path, monkeypatch):
    a = tmp_path / "a"
    a.mkdir()
    monkeypatch.setenv("MUESTRA_LIBRARY", str(a))
    p = _project(tmp_path, [a.as_posix()])
    assert len(p.library_dirs()) == 1


def test_library_uris_resolve_through_the_env_fallback(tmp_path, monkeypatch, lib):
    index_root(lib, workers=1)
    monkeypatch.setenv("MUESTRA_LIBRARY", str(lib))
    p = _project(tmp_path, ["library"])  # no explicit root for it
    uri = "library://kits/machine-a/kick-m-kick-01.wav"
    assert resolve(p, uri).is_file()
    assert p.resolve_library(uri).is_file()
    hit = lookup(p, uri)
    assert hit is not None and hit.type == "kick" and hit.bpm is None


def test_lookup_resolve_contracts(project, indexed):
    uri = "library://kits/machine-a/loops/misc-m-beat-120-01.wav"
    hit = lookup(project, uri)
    assert hit.uri == uri and hit.bpm == 120.0 and hit.kind == "loop" and hit.kit == "machine-a"
    assert lookup(project, "library://kits/machine-a/missing.wav") is None
    assert lookup(project, "kits/machine-a/kick-m-kick-01.wav") is None  # not a library URI
    assert resolve(project, uri).is_file()
    with pytest.raises(MuestraError) as exc:
        resolve(project, "library://kits/machine-a/missing.wav")
    assert exc.value.issues[0].code == "file_not_found"


def test_lookup_of_an_unindexed_file_still_answers(project, lib):
    hit = lookup(project, "library://kits/machine-a/kick-m-kick-01.wav")
    assert hit is not None and hit.bpm is None and hit.type is None  # not indexed yet


def test_index_cache_follows_the_file(lib):
    index_root(lib, workers=1)
    first = loaded_index(lib)
    assert loaded_index(lib) is first  # cached
    write(lib / "kits" / "machine-a" / "kick-m-new-09.wav", kick(70.0))
    index_root(lib, workers=1)
    second = loaded_index(lib)
    assert second is not first and "kits/machine-a/kick-m-new-09.wav" in second.by_path


def test_sample_clip_warp_check_uses_the_library_bpm(project, indexed):
    """song.validate: a loop with a BPM can be warped, a one-shot cannot."""
    from muestra.project import edit_song_document, validate_song

    def add_clip(sample, warp):
        def edit(doc):
            doc["tracks"] = [
                {
                    "id": "loops",
                    "clips": [
                        {
                            "sample": sample,
                            "length_beats": 4,
                            "at_bar": 1,
                            "warp": {"mode": "resample"} if warp else None,
                        }
                    ],
                }
            ]
            if not warp:
                doc["tracks"][0]["clips"][0].pop("warp")

        edit_song_document(project, edit)

    add_clip("library://kits/machine-a/loops/misc-m-beat-120-01.wav", True)
    assert not [i for i in validate_song(project, check_files=True) if i.code == "invalid_value"]
    add_clip("library://kits/machine-a/kick-m-kick-01.wav", True)
    issues = validate_song(project, check_files=True)
    assert any("cannot be warped" in i.message for i in issues)


def test_opening_a_saved_project_keeps_explicit_empty_roots(tmp_path, default_lib, monkeypatch):
    monkeypatch.delenv("MUESTRA_LIBRARY")
    p = _project(tmp_path, ["library"])
    save_config(p, ProjectConfig(library=LibraryConfig(roots=[])))
    assert open_project(p.root).library_dirs() == []
