"""Contract tests for analysis.json, meta.json, metrics.json,
feedback.jsonl, muestra.toml and chop candidate sets: the committed
fixture files all parse, and round-trip."""

from __future__ import annotations

import json

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from muestra.project import load_analysis, load_config
from muestra.schemas import (
    Analysis,
    ChopCandidateSet,
    DiffEntry,
    Metrics,
    RenderMeta,
)
from muestra.schemas.feedback import FeedbackEvent

from ..conftest import EXAMPLE_PROJECT

_EVENT_ADAPTER = TypeAdapter(FeedbackEvent)


def test_analysis_fixture(example_project):
    a = load_analysis(example_project, "src1")
    assert a.bpm_global == 120.0
    assert a.downbeats_s == [0.25, 2.25, 4.25]
    assert a.stems and a.stems.other == "cache/stems/src1/other.wav"
    Analysis.model_validate(a.model_dump(mode="json"))  # round-trip


def test_analysis_rejects_unsorted_grid():
    raw = json.loads((EXAMPLE_PROJECT / "cache/analysis/src1.json").read_text())
    raw["beats_s"] = [0.75, 0.25]
    with pytest.raises(ValidationError):
        Analysis.model_validate(raw)


def test_render_meta_fixture():
    raw = json.loads((EXAMPLE_PROJECT / "renders/v001/meta.json").read_text(encoding="utf-8"))
    meta = RenderMeta.model_validate(raw)
    assert meta.version == "v001" and meta.parent is None
    assert meta.sample_rate == 22050 and meta.bpm == 120.0 and meta.key == "F#m"
    assert meta.range_bars == (1, 2)  # [start, end) exclusive
    assert meta.mix == "renders/v001/mix.wav"
    kinds = {t.id: t.kind for t in meta.tracks}
    assert kinds == {"sample": "clips", "drums": "pattern", "bass": "instrument"}
    drums = next(t for t in meta.tracks if t.id == "drums")
    assert {lane.id for lane in drums.lanes} == {"kick", "clap", "ohat"}
    # every declared audio file exists
    for t in meta.tracks:
        assert (EXAMPLE_PROJECT / t.stem).is_file(), t.stem
        for lane in t.lanes:
            assert (EXAMPLE_PROJECT / lane.stem).is_file(), lane.stem
    assert (EXAMPLE_PROJECT / "renders/v001/song.yaml").read_bytes() == (
        EXAMPLE_PROJECT / "song.yaml"
    ).read_bytes()
    # 'from' serialises under its alias
    d = DiffEntry(path="chops[0].warp.pitch_semitones", **{"from": 0, "to": 1})
    assert d.model_dump(mode="json", by_alias=True)["from"] == 0


def test_metrics_fixture():
    raw = json.loads((EXAMPLE_PROJECT / "renders/v001/metrics.json").read_text(encoding="utf-8"))
    m = Metrics.model_validate(raw)
    assert m.loudness.integrated_lufs == -12.4
    assert m.spectral.tracks["drums"].low_db == -19.0
    Metrics.model_validate(m.model_dump(mode="json"))


def test_chop_candidate_set_fixture():
    raw = json.loads((EXAMPLE_PROJECT / "cache/chops/src1/cs001.json").read_text(encoding="utf-8"))
    s = ChopCandidateSet.model_validate(raw)
    assert s.set_id == "cs001"
    assert s.pick and s.pick.candidate_id == "cs001/cand1"  # global candidate id
    assert s.chosen and s.chosen.mode == "auto"
    assert s.chosen.at.tzinfo is not None  # aware timestamp
    assert len(s.candidates) == 3


def test_library_index_fixture():
    from muestra.schemas.library import LibraryIndex

    raw = json.loads((EXAMPLE_PROJECT.parent / "library/.muestra-index.json").read_text())
    idx = LibraryIndex.model_validate(raw)
    assert len(idx.entries) == 3
    assert idx.entries[0].path == "kits/disco-01/kick.wav"


def test_feedback_fixture_events():
    for line in (EXAMPLE_PROJECT / "feedback.jsonl").read_text().splitlines():
        if line.strip():
            _EVENT_ADAPTER.validate_python(json.loads(line))


def test_feedback_ab_winner_must_be_a_or_b():
    with pytest.raises(ValidationError):
        _EVENT_ADAPTER.validate_python(
            {
                "ts": "2026-11-17T20:00:00Z",
                "type": "ab_choice",
                "a": "v001",
                "b": "v002",
                "winner": "v003",
            }
        )


def test_config_fixture(example_project):
    cfg = load_config(example_project.root)
    assert cfg.sample_rate == 22050
    assert cfg.library.roots == ["../library"]
    assert cfg.plugins.aliases["surge-xt"].windows
    assert cfg.separation.backend == "audio-separator"


def test_legacy_bare_library_list_still_parses():
    from muestra.schemas import ProjectConfig

    cfg = ProjectConfig.model_validate({"library": ["../library"]})
    assert cfg.library.roots == ["../library"]


def test_song_yaml_fixture_is_the_spec_example(example_project):
    raw = yaml.safe_load((EXAMPLE_PROJECT / "song.yaml").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 0
    assert raw["meta"]["key"] == "F#m"
    assert raw["tracks"][0]["clips"][0]["chop"] == "c1"
