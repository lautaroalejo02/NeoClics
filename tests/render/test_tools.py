from __future__ import annotations

import json

import numpy as np
import pytest
import soundfile as sf

from muestra.ears import core as ears
from muestra.project import dump_song, save_song
from muestra.registry import run_tool
from muestra.render import core as render_core
from muestra.render import tools
from muestra.schemas.metrics import Metrics
from muestra.schemas.song import Song
from muestra.versions import core as versions
from tests.conftest import EXAMPLE_PROJECT


def small_song():
    return Song.model_validate(
        {
            "meta": {"title": "tool test", "bpm": 120, "key": "Am", "length_bars": 2},
            "tracks": [
                {
                    "id": "sample",
                    "clips": [
                        {"sample": "sources/fake-source.wav", "length_beats": 8, "at_bar": 1}
                    ],
                }
            ],
        }
    )


_COMMITS = {
    "a" * 40: versions.CommitInfo(
        "a" * 40,
        "2026-11-17T10:00:00+00:00",
        "v100: sample",
        {"Muestra-Version": "v100", "Muestra-Parent": "v001"},
    ),
    "b" * 40: versions.CommitInfo(
        "b" * 40, "2026-11-17T09:00:00+00:00", "v001: previous", {"Muestra-Version": "v001"}
    ),
}


@pytest.fixture
def version_fakes(monkeypatch):
    """Fake git history: v001 (b...) exists; a new render records v100 (a...)
    whose parent the versions package derived as v001."""
    monkeypatch.setattr(versions, "next_version_id", lambda project: "v100")
    monkeypatch.setattr(versions, "record_version", lambda *args: "a" * 40)
    monkeypatch.setattr(versions, "version_tags", lambda root: {"v001": "b" * 40})
    monkeypatch.setattr(versions, "commit_info", lambda root, ref: _COMMITS.get(ref))


def test_render_orchestration_snapshot_meta_metrics(project, version_fakes, monkeypatch):
    save_song(project, small_song())
    raw = project.song_path.read_bytes()
    order = []
    validate = tools.validate_song
    audio = render_core.render_song

    def validated(*args, **kwargs):
        order.append("validate")
        return validate(*args, **kwargs)

    def allocate(project):
        order.append("allocate")
        return "v100"

    def record(project, version, request, summary, agent, parent):
        order.append("record")
        assert not project.path("renders/v100/meta.json").exists()
        assert (version, request, summary, agent, parent) == (
            "v100",
            "more pump",
            "sample",
            "agent",
            None,  # the versions package derives the parent (honours restore)
        )
        return "a" * 40

    def rendered(*args, **kwargs):
        order.append("audio")
        return audio(*args, **kwargs)

    def analyze(project, version):
        order.append("ears")
        assert project.path("renders/v100/meta.json").is_file()
        assert project.path("renders/v100/song.yaml").read_bytes() == raw
        result = Metrics.model_validate_json(
            (EXAMPLE_PROJECT / "renders/v001/metrics.json").read_text()
        )
        return result.model_copy(update={"version": version})

    monkeypatch.setattr(tools, "validate_song", validated)
    monkeypatch.setattr(versions, "next_version_id", allocate)
    monkeypatch.setattr(versions, "record_version", record)
    monkeypatch.setattr(render_core, "render_song", rendered)  # tools imports it lazily
    monkeypatch.setattr(ears, "analyze", analyze)
    result = tools.render(
        tools.RenderParams(
            project=str(project.root), request="more pump", summary="sample", agent="agent"
        )
    )
    assert order == ["validate", "allocate", "record", "audio", "ears"]
    assert result.meta.git_commit == "a" * 40
    assert result.meta.parent == "v001"
    assert result.metrics_summary is not None and result.metrics_path
    assert result.meta.range_bars == (1, 3)
    assert result.meta.duration_s == 4
    assert result.stem_paths == {"sample": "renders/v100/stems/sample.wav"}
    assert all(project.path(path).is_file() for path in result.files)
    assert json.loads(project.path(result.meta_path).read_text())["tracks"][0]["id"] == "sample"


def test_ears_stub_returns_success_hint_and_removes_stale_metrics(
    project, version_fakes, monkeypatch
):
    # ears is real now; simulate it being unavailable to keep the fallback covered.
    def unavailable(*_args, **_kwargs):
        raise NotImplementedError

    monkeypatch.setattr(ears, "analyze", unavailable)
    save_song(project, small_song())
    result = run_tool("render", {"project": str(project.root)})
    assert result.ok
    assert result.data["metrics_summary"] is None
    assert result.data["hint"]
    assert result.data["metrics_path"] is None
    project.path("renders/v100/metrics.json").write_text("stale", encoding="utf-8")
    result = run_tool("render", {"project": str(project.root)})
    assert result.ok
    assert not project.path("renders/v100/metrics.json").exists()


def test_rerender_uses_tagged_song_bpm_and_raw_snapshot(project, version_fakes, monkeypatch):
    old = small_song()
    old.meta.bpm = 150
    raw = "# preserve my comments\r\n" + dump_song(old).replace("\n", "\r\n")
    project.path("renders/v001/song.yaml").write_bytes(raw.encode())
    project.song_path.write_text("invalid live yaml: [", encoding="utf-8")
    monkeypatch.setattr(versions, "song_at", lambda p, v: old)
    monkeypatch.setattr(versions, "song_at_raw", lambda p, v: raw)
    monkeypatch.setattr(versions, "next_version_id", lambda *a: pytest.fail("must not allocate"))
    monkeypatch.setattr(versions, "record_version", lambda *a: pytest.fail("must not commit"))
    result = tools.render(
        tools.RenderParams(
            project=str(project.root), version="v001", stems=False, range_bars=(2, 3)
        )
    )
    assert result.meta.bpm == 150
    assert result.meta.git_commit == "b" * 40
    assert result.meta.tracks == [] and result.stem_paths == {}
    assert project.path("renders/v001/song.yaml").read_bytes() == raw.encode()
    assert sf.info(project.path(result.mix_path)).frames == round(4 * 60 / 150 * 22050)


@pytest.mark.parametrize("target", ["mix", "chop:c1", "track:sample", "clip:sample:0"])
def test_previews_paths_lengths_and_no_versions(project, song, fake_instrument, target):
    song.meta.length_bars = 2
    song.tracks[0].clips[0].repeat = 1
    song.automation[0].points[-1] = (3, 18000)
    save_song(project, song)
    before = {p.name for p in project.renders_dir.iterdir()}
    result = tools.preview(tools.PreviewParams(project=str(project.root), target=target, bars=0.25))
    assert result.preview_path.startswith("cache/previews/")
    assert sf.info(project.path(result.preview_path)).frames == 11025
    assert {p.name for p in project.renders_dir.iterdir()} == before
    audio, _ = sf.read(project.path(result.preview_path), always_2d=True)
    assert np.max(np.abs(audio)) > 0.001
    assert np.max(np.abs(audio[-1])) < 1e-6
    assert result.files == [result.preview_path]


@pytest.mark.parametrize(
    "target", ["bogus", "chop:nope", "track:nope", "clip:sample:-1", "clip:sample:abc"]
)
def test_preview_structured_errors(project, target):
    result = run_tool("preview", {"project": str(project.root), "target": target})
    assert not result.ok
    assert result.errors[0].path == "target"
    assert result.errors[0].value and result.errors[0].hint


def test_validation_failure_does_not_record_version(project, monkeypatch):
    project.song_path.write_text("meta: {bpm: 0}\n", encoding="utf-8")
    monkeypatch.setattr(versions, "record_version", lambda *a: pytest.fail("must validate first"))
    result = run_tool("render", {"project": str(project.root)})
    assert not result.ok
    assert any(issue.path.startswith("meta") for issue in result.errors)


def test_render_names_tracks_whose_reproducibility_is_unverified():
    """WP11: an arbitrary Surge patch (noise LFOs) re-rendered differently in
    another process; curated aliases and SFZ are bit-exact."""
    song = Song.model_validate(
        {
            "meta": {"title": "t", "bpm": 120, "key": "Am", "length_bars": 1},
            "tracks": [
                {"id": "a", "instrument": {"plugin": "slap-bass"}, "notes": []},
                {"id": "b", "instrument": {"plugin": "synth-bass"}, "notes": []},
                {"id": "c", "instrument": {"plugin": "rhodes"}, "notes": []},
                {
                    "id": "d",
                    "instrument": {"plugin": "slap-bass", "preset": "Basses/Dynamo Square"},
                    "notes": [],
                },
                {"id": "e", "instrument": {"plugin": "surge-xt"}, "notes": []},
                {"id": "f", "instrument": {"plugin": "sfz", "preset": "x.sfz"}, "notes": []},
            ],
        }
    )
    assert tools.unverified_reproducibility(song) == ["d", "e"]
