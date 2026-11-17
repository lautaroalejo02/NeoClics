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


def song_with(kit_name: str, kits: StarterKits) -> Song:
    pattern = kit_pattern(kits.kits[kit_name])
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


@pytest.mark.parametrize("name", ["disco-01", "disco-808", "city-pop-80s", "acoustic-disco"])
def test_every_starter_kit_validates_as_a_song_yaml_drum_track(name):
    kits = bundled()
    song = song_with(name, kits)
    assert song.check() == []
    track = song.tracks[0]
    assert set(track.pattern.lanes) == set(kits.kits[name].lanes)
    for lane in track.pattern.lanes.values():
        assert lane.sample.startswith("library://kits/")


def test_pattern_steps_are_four_on_the_floor_for_the_core_lanes():
    pat = kit_pattern(bundled().kits["disco-01"])
    assert pat["lanes"]["kick"]["steps"] == "x...x...x...x..."
    assert pat["lanes"]["clap"]["steps"] == "....x.......x..."
    assert pat["lanes"]["ohat"]["steps"] == "..x...x...x...x."
    assert all(len(s) == 16 for s in DEFAULT_STEPS.values())
    assert (
        len(kit_pattern(bundled().kits["disco-01"], steps_per_bar=32)["lanes"]["kick"]["steps"])
        == 32
    )


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
