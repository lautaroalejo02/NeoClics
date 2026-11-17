"""Starter kits: the bundled template, manifests, song.yaml validity, the tool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from muestra.errors import MuestraError
from muestra.library.kits import BUNDLED_KITS_FILE, DEFAULT_STEPS, kit_pattern, kit_reports
from muestra.registry import run_tool
from muestra.schemas import Song
from muestra.schemas.library import STARTER_KITS_FILE, StarterKits

REAL_LIBRARY = Path.home() / "Documents" / "MuestraLibrary"


def bundled() -> StarterKits:
    return StarterKits.model_validate(json.loads(BUNDLED_KITS_FILE.read_text(encoding="utf-8")))


def song_with(kit_name: str, kits: StarterKits, name: str = "basic") -> Song:
    pattern = kit_pattern(kits.kits[kit_name], name=name)
    return Song.model_validate(
        {
            "schema_version": 0,
            "meta": {"title": "t", "bpm": 120, "key": "Am", "length_bars": 4, "seed": 0},
            "sources": [],
            "chops": [],
            "tracks": [{"id": "drums", "pattern": pattern}],
            "automation": [],
            "master": {"fx": []},
        }
    )


def test_bundled_template_is_valid_and_has_the_required_kits():
    kits = bundled()
    assert {"disco-01", "disco-808", "city-pop-80s", "acoustic-disco"} <= set(kits.kits)
    assert "nobody has listened" in kits.note.lower()  # stated plainly
    for name, kit in kits.kits.items():
        assert kit.description, name
        for path in kit.lanes.values():
            assert path.startswith("kits/") and path.endswith(".wav"), (name, path)


def test_disco_01_follows_the_report_section_2_picks():
    lanes = bundled().kits["disco-01"].lanes
    assert lanes["kick"] == "kits/tr8-hardware-machines/roland-tr-8/kick-tr-8-kick-02.wav"
    assert lanes["clap"] == "kits/essential-kits-musicradar/tr-909-kit/clap-tr-909-clap.wav"
    assert lanes["chat"].endswith("roland-tr-8/chat-tr-8-closed-hat-01.wav")
    assert lanes["ohat"].endswith("roland-tr-8/ohat-tr-8-open-hat-01.wav")
    assert lanes["crash"].endswith("roland-tr-8/crash-tr-8-crash-02.wav")


KIT_NAMES = ["disco-01", "disco-808", "city-pop-80s", "acoustic-disco"]


@pytest.mark.parametrize("name", KIT_NAMES)
def test_every_named_pattern_of_every_kit_validates_as_a_song_yaml_drum_track(name):
    kits = bundled()
    kit = kits.kits[name]
    assert {"basic", "busy", "fill"} <= set(kit.patterns), name
    for pattern in kit.patterns:
        song = song_with(name, kits, pattern)
        assert song.check() == [], (name, pattern)
        track = song.tracks[0]
        assert set(track.pattern.lanes) <= set(kit.lanes)
        for lane in track.pattern.lanes.values():
            assert lane.sample.startswith("library://kits/")


@pytest.mark.parametrize("name", KIT_NAMES)
def test_basic_is_a_sane_future_funk_default(name):
    """WP12 (skill finding 8): disco-01's pattern put a crash on every bar."""
    lanes = kit_pattern(bundled().kits[name])["lanes"]
    assert lanes["kick"]["steps"] == "x...x...x...x..."
    back = [lanes[ln]["steps"] for ln in ("clap", "snare") if ln in lanes]
    assert back and all(s == "....x.......x..." for s in back)
    assert lanes["ohat"]["steps"] == "..x...x...x...x."
    assert not {"crash", "perc", "cowbell", "conga", "tom", "shaker"} & set(lanes)


@pytest.mark.parametrize("name", KIT_NAMES)
def test_crash_only_at_the_start_of_a_phrase(name):
    for pname, spec in bundled().kits[name].patterns.items():
        crash = spec.steps.get("crash")
        if crash is None:
            continue
        hits = [i for i, c in enumerate(crash.replace(" ", "")) if c not in ".-_"]
        assert hits == [0] and spec.bars >= 4, (name, pname, hits)


def test_fallback_steps_and_steps_per_bar_keep_musical_time():
    pat = kit_pattern(bundled().kits["disco-01"])
    assert all(len(s) == 16 for s in DEFAULT_STEPS.values())
    assert "crash" not in DEFAULT_STEPS and "perc" not in DEFAULT_STEPS
    k32 = kit_pattern(bundled().kits["disco-01"], steps_per_bar=32)["lanes"]["kick"]["steps"]
    assert k32 == "x.......x.......x.......x......."  # still 4 kicks per bar
    assert pat["lanes"]["kick"]["steps"] == "x...x...x...x..."
    phrase = kit_pattern(bundled().kits["disco-01"], name="phrase")
    assert phrase["bars"] == 4 and len(phrase["lanes"]["crash"]["steps"]) == 64
    with pytest.raises(MuestraError, match="no pattern"):
        kit_pattern(bundled().kits["disco-01"], name="nope")


def test_the_committed_template_matches_its_json_schema_file():
    schema = json.loads(
        (Path(__file__).parents[2] / "schemas" / "starter-kits.schema.json").read_text("utf-8")
    )
    assert schema["properties"]["kits"] and "StarterKit" in json.dumps(schema)


def test_bundled_kits_are_reported_unavailable_in_a_foreign_library(project, indexed):
    reports = {r.name: r for r in kit_reports(project)}
    assert set(reports) >= {"disco-01"}
    r = reports["disco-01"]
    assert r.source == "bundled template" and not r.available
    assert any("missing from" in w for w in r.warnings)


def test_a_manifest_inside_the_root_wins_and_reports_the_sound(project, indexed):
    manifest = {
        "schema_version": 0,
        "kits": {
            "mini": {
                "description": "tiny",
                "lanes": {
                    "kick": "kits/machine-a/kick-m-kick-01.wav",
                    "chat": "kits/machine-a/chat-m-closed-hat-01.wav",
                    "ohat": "kits/machine-a/ohat-m-open-hat-01.wav",
                },
            }
        },
    }
    (indexed / STARTER_KITS_FILE).write_text(json.dumps(manifest), encoding="utf-8")
    r = run_tool("library.kits", {"project": str(project.root), "name": "mini"})
    assert r.ok, r.errors
    kit = r.data["kits"][0]
    assert kit["available"] and kit["source"].endswith(STARTER_KITS_FILE) and not kit["warnings"]
    assert [lane["lane"] for lane in kit["lanes"]] == ["kick", "chat", "ohat"]
    assert "pitch" in kit["lanes"][0]["sound"]  # the agent gets the measured description
    assert (
        kit["pattern"]["lanes"]["kick"]["sample"] == "library://kits/machine-a/kick-m-kick-01.wav"
    )
    assert "nobody has listened" in r.data["caveat"]
    # the pattern goes straight into a song
    Song.model_validate(
        {
            "schema_version": 0,
            "meta": {"title": "t", "bpm": 120, "key": "Am", "length_bars": 4, "seed": 0},
            "tracks": [{"id": "drums", "pattern": kit["pattern"]}],
        }
    ).check()


def test_wrong_type_missing_and_unindexed_members_produce_warnings(project, indexed):
    manifest = {
        "kits": {
            "bad": {
                "lanes": {
                    "kick": "kits/machine-a/snare-m-snare-01.wav",  # a snare in the kick lane
                    "clap": "kits/machine-a/nope.wav",  # does not exist
                    "chat": "kits/acoustic-b/kick-ac-clipped-01.wav",  # wrong type AND clipped
                }
            }
        }
    }
    (indexed / STARTER_KITS_FILE).write_text(json.dumps(manifest), encoding="utf-8")
    r = run_tool("library.kits", {"project": str(project.root), "name": "bad"})
    kit = r.data["kits"][0]
    assert not kit["available"]
    text = " | ".join(kit["warnings"])
    assert "lane kick: sample is typed 'snare'" in text
    assert "lane clap" in text and "missing" in text
    assert "lane chat: clipping" in text
    assert "no-ai-training" in kit["restrictions"]  # acoustic-b's licence travels with the kit


def test_listing_is_compact_and_unknown_names_are_clear_errors(project, indexed):
    r = run_tool("library.kits", {"project": str(project.root)})
    assert r.ok and r.data["kits"] and all(k["pattern"] is None for k in r.data["kits"])
    assert "name=" in r.data["hint"]
    bad = run_tool("library.kits", {"project": str(project.root), "name": "nope"})
    assert not bad.ok and bad.errors[0].code == "ref_not_found"
    with pytest.raises(MuestraError):
        kit_reports(project, "nope")


@pytest.mark.skipif(
    not (REAL_LIBRARY / ".muestra-index.json").is_file(), reason="owner's real library not indexed"
)
def test_bundled_kits_match_the_owners_real_library():
    """Only runs on the owner's machine: every curated member exists, is typed
    for its lane and is neither clipped nor mislabelled."""
    from muestra.library.core import loaded_index

    loaded = loaded_index(REAL_LIBRARY)
    expect = {
        "kick": {"kick"},
        "clap": {"clap"},
        "snare": {"snare"},
        "chat": {"chat"},
        "ohat": {"ohat"},
        "crash": {"cym"},
        "tom": {"tom"},
    }
    for name, kit in bundled().kits.items():
        for lane, path in kit.lanes.items():
            e = loaded.by_path.get(path)
            assert e is not None, (name, lane, path)
            assert not e.clipping and not any(f.startswith("type-mismatch") for f in e.flags)
            if lane in expect:
                assert e.type in expect[lane], (name, lane, e.type)


def test_an_empty_first_root_does_not_hide_the_real_library_kits(tmp_path):
    """Regression: the project's own empty library/ sits before the real library."""
    from muestra.library.index import index_root
    from muestra.project import create_project
    from muestra.schemas import LibraryConfig, ProjectConfig

    from .conftest import kick, write

    real = tmp_path / "real"
    for path in bundled().kits["disco-01"].lanes.values():
        write(real / path, kick(60.0, 0.05, dur=0.2))
    index_root(real, workers=1)
    (tmp_path / "empty").mkdir()
    project = create_project(
        tmp_path / "p",
        ProjectConfig(
            library=LibraryConfig(roots=[(tmp_path / "empty").as_posix(), real.as_posix()])
        ),
    )
    r = run_tool("library.kits", {"project": str(project.root), "name": "disco-01"})
    assert r.ok, r.errors
    kit = r.data["kits"][0]
    assert kit["available"] and kit["source"] == "bundled template"
    assert all(lane["present"] for lane in kit["lanes"])
    assert kit["pattern"]["lanes"]["kick"]["sample"].startswith("library://kits/")
    # the other bundled kits are still listed, as unavailable
    listing = run_tool("library.kits", {"project": str(project.root)})
    avail = {k["name"]: k["available"] for k in listing.data["kits"]}
    assert avail["disco-01"] and not avail["disco-808"]


@pytest.mark.parametrize("name", KIT_NAMES)
def test_closed_and_open_hats_never_hit_the_same_step(name):
    for pname, spec in bundled().kits[name].patterns.items():
        ch, oh = spec.steps.get("chat"), spec.steps.get("ohat")
        if not ch or not oh:
            continue
        both = [i for i, (a, b) in enumerate(zip(ch, oh, strict=True)) if a != "." and b != "."]
        assert not both, (name, pname, both)
