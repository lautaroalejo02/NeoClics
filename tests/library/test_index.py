"""Indexing: entries, incremental re-index, corrupt files, licences."""

from __future__ import annotations

import os
import time

import pytest

from muestra.library.core import load_index
from muestra.library.index import index_root, scan_audio_files
from muestra.library.licence import parse_license
from muestra.schemas.library import ANALYZER_VERSION, LibraryIndex

from .conftest import hat, kick, write


def _by_path(report):
    return {e.path: e for e in report.index.entries}


def test_index_describes_every_file(lib):
    rep = index_root(lib, workers=1)
    assert rep.scanned == rep.analyzed == len(rep.index.entries) > 10
    assert not rep.problems
    e = _by_path(rep)
    k = e["kits/machine-a/kick-m-kick-01.wav"]
    assert (k.type, k.kind, k.kit) == ("kick", "one-shot", "machine-a")
    assert k.features.fundamental_hz == pytest.approx(60.0, abs=1.5)
    assert k.bit_depth == 24 and k.sample_rate == 44100 and k.channels == 1
    assert not k.clipping and "clean" in k.tags
    assert k.size and k.mtime_ns and k.sig  # incremental bookkeeping
    assert e["kits/machine-a/chat-m-closed-hat-01.wav"].type == "chat"
    assert e["kits/machine-a/ohat-m-open-hat-01.wav"].type == "ohat"


def test_index_file_is_valid_and_lives_inside_the_root(lib):
    rep = index_root(lib, workers=1)
    assert rep.index_path == lib.resolve() / ".muestra-index.json"
    loaded = LibraryIndex.model_validate_json(rep.index_path.read_text(encoding="utf-8"))
    assert loaded.analyzer_version and loaded.indexed_at is not None
    assert load_index(lib).entries[0].path == loaded.entries[0].path


def test_clipping_and_type_mismatch_are_flagged(lib):
    e = _by_path(index_root(lib, workers=1))
    clipped = e["kits/acoustic-b/kick-ac-clipped-01.wav"]
    assert clipped.clipping and "clipping" in clipped.flags and "clipped" in clipped.tags
    liar = e["kits/acoustic-b/kick-ac-liar-01.wav"]
    assert liar.type == "kick"  # the name decides ...
    assert any(f.startswith("type-mismatch:name=kick") for f in liar.flags)  # ... audio disagrees


def test_loops_get_bpm_from_name_cross_checked_by_audio(lib):
    e = _by_path(index_root(lib, workers=1))
    ok = e["kits/machine-a/loops/misc-m-beat-120-01.wav"]
    assert ok.kind == "loop" and ok.type == "drums"
    assert ok.bpm == 120.0 and ok.bpm_source == "name+detected" and ok.bpm_confidence > 0.7
    liar = e["kits/machine-a/loops/misc-m-beat-100-02.wav"]
    assert liar.bpm == 100.0 and liar.bpm_source == "name"
    assert any(f.startswith("bpm-disagreement:name=100,detected=120") for f in liar.flags)
    # one-shots never carry a bpm
    assert e["kits/machine-a/kick-m-kick-01.wav"].bpm is None


def test_tonal_loop_gets_a_canonical_key(lib):
    pad = _by_path(index_root(lib, workers=1))["kits/machine-a/instruments/misc-m-pad-a-120-a.wav"]
    assert (pad.type, pad.kind) == ("pad", "loop")
    assert pad.key == "Am" and pad.key_confidence > 0.3


def test_incremental_reindex_reuses_unchanged_files(lib):
    first = index_root(lib, workers=1)
    again = index_root(lib, workers=1)
    assert again.analyzed == 0 and again.reused == first.scanned
    assert [e.model_dump() for e in again.index.entries] == [
        e.model_dump() for e in first.index.entries
    ]


def test_changed_new_and_removed_files_are_picked_up(lib):
    index_root(lib, workers=1)
    changed = lib / "kits" / "machine-a" / "kick-m-kick-01.wav"
    write(changed, kick(80.0, 0.05))  # different content and size
    write(lib / "kits" / "machine-a" / "snare-m-snare-99.wav", hat(0.05, 0.3))
    (lib / "kits" / "acoustic-b" / "kick-ac-liar-01.wav").unlink()
    rep = index_root(lib, workers=1)
    assert (rep.new, rep.changed, rep.removed) == (1, 1, 1)
    e = _by_path(rep)
    assert e["kits/machine-a/kick-m-kick-01.wav"].features.fundamental_hz == pytest.approx(
        80.0, abs=2.0
    )
    assert "kits/acoustic-b/kick-ac-liar-01.wav" not in e


def test_touched_but_identical_file_is_not_remeasured(lib):
    index_root(lib, workers=1)
    f = lib / "kits" / "machine-a" / "kick-m-kick-01.wav"
    future = time.time() + 3600
    os.utime(f, (future, future))  # new mtime, same bytes
    rep = index_root(lib, workers=1)
    assert rep.analyzed == 0
    assert _by_path(rep)["kits/machine-a/kick-m-kick-01.wav"].mtime_ns == f.stat().st_mtime_ns


def test_force_remeasures_everything(lib):
    first = index_root(lib, workers=1)
    forced = index_root(lib, workers=1, force=True)
    assert forced.analyzed == first.scanned and forced.reused == 0


def test_stale_analyzer_version_triggers_full_reindex(lib):
    rep = index_root(lib, workers=1)
    text = rep.index_path.read_text(encoding="utf-8")
    rep.index_path.write_text(
        text.replace(f'"analyzer_version": {ANALYZER_VERSION}', '"analyzer_version": 0'),
        encoding="utf-8",
    )
    assert index_root(lib, workers=1).reused == 0


def test_corrupt_and_empty_files_are_reported_not_fatal(lib):
    (lib / "kits" / "machine-a" / "kick-m-broken-01.wav").write_bytes(b"RIFF garbage")
    (lib / "kits" / "machine-a" / "kick-m-empty-02.wav").write_bytes(b"")
    rep = index_root(lib, workers=1)
    bad = {p.path: p.message for p in rep.problems}
    assert set(bad) == {
        "kits/machine-a/kick-m-broken-01.wav",
        "kits/machine-a/kick-m-empty-02.wav",
    }
    assert all(bad.values())
    assert len(rep.index.entries) == rep.scanned - 2  # everything else is indexed
    assert [p.path for p in load_index(lib).problems] == sorted(bad)


def test_problems_are_retried_on_the_next_run(lib):
    broken = lib / "kits" / "machine-a" / "kick-m-broken-01.wav"
    broken.write_bytes(b"RIFF garbage")
    assert len(index_root(lib, workers=1).problems) == 1
    write(broken, kick(70.0))  # fixed
    rep = index_root(lib, workers=1)
    assert not rep.problems and rep.new == 1


def test_progress_callback_reports_all_files(lib):
    calls = []
    index_root(lib, workers=1, progress=lambda done, total, rel: calls.append((done, total, rel)))
    assert calls[0][:2] == (0, calls[0][1])
    total = calls[0][1]
    assert calls[-1][:2] == (total, total)
    assert [c[0] for c in calls] == sorted(c[0] for c in calls)  # monotonic


def test_pool_and_inline_give_identical_entries(tmp_path):
    root = tmp_path / "lib" / "kits" / "pack"
    for i in range(30):
        write(root / f"kick-p-kick-{i:02d}.wav", kick(50.0 + i, 0.05 + i * 0.002, dur=0.3))
    a = index_root(tmp_path / "lib", workers=1, force=True)
    b = index_root(tmp_path / "lib", workers=2, force=True)
    assert b.workers == 2 and a.workers == 1
    strip = lambda r: [  # noqa: E731
        e.model_dump(exclude={"mtime_ns", "sig"}) for e in r.index.entries
    ]
    assert strip(a) == strip(b)


def test_underscore_dot_and_instruments_dirs_are_skipped(lib):
    write(lib / "_downloads" / "x.wav", kick())
    write(lib / ".hidden" / "y.wav", kick())
    write(lib / "instruments" / "bank" / "z.wav", kick())
    files = scan_audio_files(lib)
    assert not any(f.startswith(("_downloads", ".hidden", "instruments")) for f in files)
    assert "kits/machine-a/kick-m-kick-01.wav" in files


def test_missing_root_is_a_clear_error(tmp_path):
    from muestra.errors import MuestraError

    with pytest.raises(MuestraError) as exc:
        index_root(tmp_path / "nope")
    assert exc.value.issues[0].code == "file_not_found"


def test_licences_are_read_from_license_txt(lib):
    rep = index_root(lib, workers=1)
    a, b = rep.index.kits["machine-a"], rep.index.kits["acoustic-b"]
    assert "no-redistribution" in a.restrictions and "no-ai-training" not in a.restrictions
    assert "no-ai-training" in b.restrictions  # the goldbaby-style clause
    assert "Commercial use in music: YES" in a.summary
    assert a.source == "https://example.test/a" and a.path == "kits/machine-a"


def test_real_world_licence_texts(tmp_path):
    kit = tmp_path / "gb"
    kit.mkdir()
    (kit / "LICENSE.txt").write_text(
        "SOURCE PACKS: Goldbaby free packs\nSOURCE PAGE:  https://goldbaby.co.nz/freestuff.html\n\n"
        'LICENCE:\n"You may not re-distribute these free sounds."\n\n'
        'ADDITIONAL RESTRICTION: "I do not permit the use of any of my audio samples for machine '
        'learning or any AI-related process."\n\nINTERPRETATION:\n- Commercial use: YES.\n',
        encoding="utf-8",
    )
    lic = parse_license(kit, "gb")
    assert lic is not None
    assert {"no-redistribution", "no-ai-training"} <= set(lic.restrictions)
    assert lic.source == "https://goldbaby.co.nz/freestuff.html"

    lw = tmp_path / "lw"
    lw.mkdir()
    (lw / "LICENSE.txt").write_text(
        'LICENCE:\n"The samples are free to download and use in your productions"\n'
        "What I don't want are people SELLING the samples themselves\n",
        encoding="utf-8",
    )
    assert "no-resale" in parse_license(lw, "lw").restrictions
    assert parse_license(tmp_path, "none") is None


def test_unreferenceable_file_names_are_flagged(lib):
    """library:// URIs only allow A-Za-z0-9._~/-: a name with spaces can't be used in a song."""
    write(lib / "kits" / "machine-a" / "kick copy (3).wav", kick(61.0))
    rep = index_root(lib, workers=1)
    e = _by_path(rep)["kits/machine-a/kick copy (3).wav"]
    assert "unusable-uri" in e.flags and e.type == "kick"
    ok = _by_path(rep)["kits/machine-a/kick-m-kick-01.wav"]
    assert "unusable-uri" not in ok.flags


def test_a_file_that_cannot_become_an_entry_is_a_problem_not_a_crash(lib):
    from muestra.library.index import analyze_file

    rel, entry, problem = analyze_file((str(lib), "kits/../kick.wav", 1, 1, "x"))
    assert entry is None and problem


def test_no_process_pool_falls_back_to_inline_measuring(tmp_path, monkeypatch):
    import muestra.library.index as index_mod

    class Broken:
        def __init__(self, *a, **k):
            raise OSError("no processes here")

    monkeypatch.setattr(index_mod, "ProcessPoolExecutor", Broken)
    root = tmp_path / "lib" / "kits" / "pack"
    for i in range(30):
        write(root / f"kick-p-kick-{i:02d}.wav", kick(50.0 + i, 0.05, dur=0.2))
    rep = index_root(tmp_path / "lib", workers=4)
    assert rep.workers == 1 and rep.analyzed == 30 and not rep.problems
