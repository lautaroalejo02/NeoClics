import json
from pathlib import Path

import numpy as np
import pytest

from muestra.ingest.evaluation import aggregate, key_error, load_tracks, match_events, tempo_metrics
from muestra.ingest.separation_evaluation import reference_consistency, stem_metrics


def test_matching_is_one_to_one_and_includes_boundaries():
    assert match_events([1, 2, 3], [0.95, 1.01, 2.07, 5], 0.071) == [(0, 0), (1, 2)]
    assert match_events([], [1]) == []
    assert match_events([1], []) == []


@pytest.mark.parametrize(
    "factor,label", [(1, "correct"), (0.5, "half"), (2, "double"), (1.5, "3:2"), (2 / 3, "2:3")]
)
def test_tempo_errors_are_explicit_and_json_serializable(factor, label):
    result = tempo_metrics(np.float64(100), np.float64(100 * factor))
    assert result["octave"] == label
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "tonic,mode,label",
    [
        ("C", "major", "correct"),
        ("A", "minor", "relative"),
        ("C", "minor", "parallel"),
        ("G", "major", "fifth"),
        ("F#", "major", "other"),
    ],
)
def test_key_error_classes(tonic, mode, label):
    assert key_error({"tonic": "C", "mode": "major"}, {"tonic": tonic, "mode": mode}) == label


def test_unannotated_directory_does_not_invent_references(tmp_path):
    (tmp_path / "own-song.wav").touch()
    (tmp_path / "SOURCES.md").touch()
    root, tracks = load_tracks(tmp_path)
    assert root == tmp_path
    assert len(tracks) == 1
    assert "beats" not in tracks[0]
    result = aggregate([{"title": "unknown", "analysis_s": 2}])
    assert result["annotated_tracks"] == result["key_annotated"] == 0
    assert result["analysis_s"] == 2


def test_projection_quantifies_vocal_and_bass_bleed_into_other():
    generator = np.random.default_rng(19)
    basis, _ = np.linalg.qr(generator.normal(size=(400, 3)))
    references = dict(
        zip(["other", "vocals", "bass"], (basis[:, index, None] for index in range(3)), strict=True)
    )
    estimated = references["other"] + 0.5 * references["vocals"] + 0.25 * references["bass"]
    result = stem_metrics(references, {"other": estimated})["other"]
    wrong = result["wrong_source_energy_fraction"]
    assert wrong["vocals"] == pytest.approx(0.25 / 1.3125)
    assert wrong["bass"] == pytest.approx(0.0625 / 1.3125)
    assert result["sdr_db"] == pytest.approx(10 * np.log10(1 / 0.3125))
    assert stem_metrics(references, {"other": references["other"]})["other"]["sdr_db"] > 100


def test_inconsistent_reference_mixes_are_refused():
    generator = np.random.default_rng(21)
    sources = {name: generator.normal(size=(500, 2)) for name in ("drums", "bass", "other")}
    assert reference_consistency(sum(sources.values()), sources)["usable"]
    assert not reference_consistency(generator.normal(size=(500, 2)), sources)["usable"]


def test_owner_command_without_annotations_writes_clicks_and_no_accuracy(
    rhythmic_project,
    tmp_path,
    monkeypatch,
):
    from muestra.ingest.evaluation import evaluate_track, write_report

    project, _, analysis = rhythmic_project
    monkeypatch.setattr(
        "muestra.ingest.analysis.neural_beats",
        lambda *args: (
            np.asarray(analysis.beats_s),
            np.asarray(analysis.downbeats_s),
        ),
    )
    track = {"id": "own-song", "title": "Unlabelled", "audio": str(project.resolve(analysis.file))}
    result = evaluate_track(tmp_path, track, tmp_path / "audit")
    assert "beats" not in result and "key_error" not in result
    assert result["previews"] and all(Path(path).is_file() for path in result["previews"])
    assert result["grid"]["ask_human"]
    assert result["reliability_map"]["regions"]
    write_report(tmp_path / "owner-report.md", [result])
    assert (tmp_path / "owner-report.json").is_file()
