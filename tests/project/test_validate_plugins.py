"""WP13 finding 1: song.validate checks instruments before render can fail."""

from __future__ import annotations

from muestra.project import save_song
from muestra.project.validate import validate_song
from muestra.registry import run_tool
from muestra.schemas.song import Song
from muestra.versions import core as versions
from tests.render.conftest import prepare_fixture


def _song(plugin: str, preset: str | None = None) -> Song:
    instrument = {"plugin": plugin, **({"preset": preset} if preset else {})}
    return Song.model_validate(
        {
            "meta": {"title": "t", "bpm": 120, "key": "Am", "length_bars": 1},
            "tracks": [
                {
                    "id": "bass",
                    "instrument": instrument,
                    "notes": [{"beat": 1, "pitch": "A1", "length_beats": 1}],
                }
            ],
        }
    )


def test_unknown_instrument_alias_is_a_validation_issue(tmp_path):
    project = prepare_fixture(tmp_path)
    save_song(project, _song("funky-bass"))
    issues = validate_song(project, check_files=False, check_plugins=True)
    assert [i.path for i in issues] == ["tracks.bass.instrument.plugin"]
    assert issues[0].code == "plugin_missing"
    assert issues[0].hint.startswith("Use one of the instruments available here:")
    assert "plugins.list" not in issues[0].hint
    # separable, like file existence
    assert validate_song(project, check_files=False, check_plugins=False) == []
    result = run_tool("song.validate", {"project": str(project.root), "check_files": False})
    assert result.data["valid"]  # default follows check_files
    result = run_tool("song.validate", {"project": str(project.root)})
    assert "tracks.bass.instrument.plugin" in [i["path"] for i in result.data["issues"]]


def test_missing_sfz_preset_points_at_the_preset_field(tmp_path):
    project = prepare_fixture(tmp_path)
    save_song(project, _song("sfz", "instruments/nope.sfz"))
    issues = validate_song(project, check_files=False, check_plugins=True)
    assert [(i.path, i.code) for i in issues] == [
        ("tracks.bass.instrument.preset", "preset_missing")
    ]


def test_render_of_unknown_instrument_creates_no_version(tmp_path):
    project = prepare_fixture(tmp_path)
    versions.init_repo(project.root)
    save_song(project, _song("funky-bass"))
    tags = versions.version_tags(project.root)
    head = versions._head_sha(project.root)
    result = run_tool("render", {"project": str(project.root)})
    assert not result.ok
    assert "tracks.bass.instrument.plugin" in [i.path for i in result.errors]
    assert "No version was created" in result.hint
    assert versions.version_tags(project.root) == tags
    assert versions._head_sha(project.root) == head
