"""Opt-in real source.analyze evaluation; see docs/ingest-evaluation.md.

uv run --with mir-eval python -m muestra.ingest.evaluation AUDIO_OR_MANIFEST --report report.md
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from .storage import write_json


def match_events(reference, estimated, tolerance=0.07) -> list[tuple[int, int]]:
    reference, estimated = np.asarray(reference), np.asarray(estimated)
    matches = []
    ref_index = est_index = 0
    while ref_index < len(reference) and est_index < len(estimated):
        delta = estimated[est_index] - reference[ref_index]
        if abs(delta) <= tolerance:
            matches.append((ref_index, est_index))
            ref_index += 1
            est_index += 1
        elif delta < 0:
            est_index += 1
        else:
            ref_index += 1
    return matches


def event_metrics(reference, estimated) -> dict:
    from mir_eval.beat import continuity

    reference = np.asarray(reference, dtype=float)
    estimated = np.asarray(estimated, dtype=float)
    matches = match_events(reference, estimated)
    cmlc, cmlt, amlc, amlt = continuity(reference, estimated)
    return {
        "f70": 2 * len(matches) / max(1, len(reference) + len(estimated)),
        "precision70": len(matches) / len(estimated) if len(estimated) else 0.0,
        "recall70": len(matches) / len(reference) if len(reference) else 0.0,
        "cmlc": float(cmlc),
        "cmlt": float(cmlt),
        "amlc": float(amlc),
        "amlt": float(amlt),
    }


def tempo_metrics(reference_bpm, estimated_bpm) -> dict:
    if estimated_bpm is None:
        return {"absolute_pct": None, "octave": "missing", "strict_correct": False}
    ratio = estimated_bpm / reference_bpm
    candidates = {"correct": 1.0, "half": 0.5, "double": 2.0, "3:2": 1.5, "2:3": 2 / 3}
    label, factor = min(candidates.items(), key=lambda item: abs(np.log2(ratio / item[1])))
    return {
        "reference_bpm": reference_bpm,
        "estimated_bpm": estimated_bpm,
        "absolute_pct": 100 * abs(ratio - 1),
        "octave": label if abs(ratio / factor - 1) <= 0.04 else "other",
        "strict_correct": bool(abs(ratio - 1) <= 0.04),
    }


def reliability_metrics(analysis, reference_beats, reference_downbeats) -> dict:
    duration = analysis.duration_s
    marked = correct = false = actual = 0.0
    beats = np.asarray(analysis.beats_s)
    ref_beats = np.asarray(reference_beats)
    ref_downs = np.asarray(reference_downbeats)
    downs = np.asarray(analysis.downbeats_s)
    bars = analysis.reliability.bars if analysis.reliability else []
    for bar in bars:
        length = bar.end_s - bar.start_s
        estimated = beats[(beats >= bar.start_s - 1e-6) & (beats <= bar.end_s + 1e-6)]
        truth = ref_beats[(ref_beats >= bar.start_s - 0.07) & (ref_beats <= bar.end_s + 0.07)]
        beat_ok = len(match_events(truth, estimated)) == len(truth) == len(estimated)
        phase_ok = all(np.any(abs(ref_downs - bound) <= 0.07) for bound in (bar.start_s, bar.end_s))
        accurate = beat_ok and phase_ok
        actual += length * accurate
        if bar.reliable:
            marked += length
            correct += length * accurate
            false += length * (not accurate)
    beat_ok = event_metrics(reference_beats, beats)["f70"] >= 0.9
    phase_f = event_metrics(reference_downbeats, downs)["f70"]
    return {
        "reliable_duration_s": marked,
        "false_reliable_duration_s": false,
        "reliable_fraction": marked / duration,
        "actually_correct_fraction": actual / duration,
        "false_reliable_track_fraction": false / duration,
        "false_reliable_given_reliable": false / marked if marked else None,
        "reliable_precision": correct / marked if marked else None,
        "beats_right_bar_phase_wrong": bool(beat_ok and phase_f < 0.5),
    }


def key_error(reference, estimate) -> str:
    if estimate is None:
        return "missing"
    names = {
        name: index
        for index, name in enumerate(
            ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
        )
    }
    names.update({"Db": 1, "D#": 3, "Gb": 6, "G#": 8, "A#": 10})
    offset = (names[estimate["tonic"]] - names[reference["tonic"]]) % 12
    same_mode = estimate["mode"] == reference["mode"]
    if offset == 0:
        return "correct" if same_mode else "parallel"
    if not same_mode and offset == (9 if reference["mode"] == "major" else 3):
        return "relative"
    return "fifth" if same_mode and offset in {5, 7} else "other"


def key_alternative(chroma) -> dict | None:
    from .analysis import estimate_key

    normalized = chroma / np.maximum(chroma.sum(axis=0), 1e-12)
    estimate = estimate_key(normalized)
    return estimate.model_dump() if estimate else None


def load_tracks(path: Path) -> tuple[Path, list[dict]]:
    if path.suffix.lower() == ".json":
        return path.parent, json.loads(path.read_text(encoding="utf-8"))["tracks"]
    files = (
        [path]
        if path.is_file()
        else sorted(
            candidate
            for candidate in path.iterdir()
            if candidate.suffix.lower() in {".wav", ".flac", ".mp3", ".m4a", ".aiff", ".ogg"}
        )
    )
    return path.parent if path.is_file() else path, [
        {"id": f"track-{index:03d}", "title": candidate.stem, "audio": str(candidate.resolve())}
        for index, candidate in enumerate(files, 1)
    ]


def aggregate(results) -> dict:
    annotated = [result for result in results if "beats" in result]
    means = {}
    for group in ("beats", "downbeats", "tempo", "reliability"):
        keys = {key for result in annotated for key in result[group]}
        means[group] = {
            key: float(np.mean(values))
            for key in sorted(keys)
            if (
                values := [
                    result[group][key]
                    for result in annotated
                    if isinstance(result[group].get(key), (int, float))
                    and result[group][key] is not None
                ]
            )
        }
    means["tempo_errors"] = {
        label: sum(result["tempo"]["octave"] == label for result in annotated)
        for label in ("correct", "half", "double", "3:2", "2:3", "other", "missing")
    }
    means["key_errors"] = {
        label: sum(result.get("key_error") == label for result in results)
        for label in ("correct", "relative", "parallel", "fifth", "other", "missing")
    }
    means["key_annotated"] = sum("key_error" in result for result in results)
    means["tracks"] = len(results)
    means["annotated_tracks"] = len(annotated)
    means["failed_tracks"] = sum("error" in result for result in results)
    means["analysis_s"] = sum(result.get("analysis_s", 0) for result in results)
    return means


def write_report(path, results):
    summary = aggregate(results)
    write_json(path.with_suffix(".json"), {"aggregate": summary, "results": results})
    rows = [
        "# Muestra ingest evaluation",
        "",
        "CPU measurements, not listening judgements. Missing annotations = no accuracy claim.",
        "F70 uses one-to-one event matching at 70 ms. CML/AML: mir_eval.beat.continuity",
        "(17.5% phase/period thresholds; no 5-second trim). Macro means; errors retained.",
        "Reliability correctness requires ALL beats and BOTH bar boundaries within 70 ms.",
        "False reliable is reported both per whole track and conditional on reliable time.",
        "",
        "## Aggregate",
        "",
        "```json",
        json.dumps(summary, indent=2),
        "```",
        "",
        "| Track | BPM (ref) | Beat F70 | Down F70 | Reliable % | False-reliable % track | s |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        rel = result.get("reliability", {})

        def number(value):
            return "n/a" if value is None else f"{value:.3f}"

        rows.append(
            f"| {result['title']} | {number(result.get('bpm'))} "
            f"({number(result.get('tempo', {}).get('reference_bpm'))}) | "
            f"{number(result.get('beats', {}).get('f70'))} | "
            f"{number(result.get('downbeats', {}).get('f70'))} | "
            f"{number(100 * rel.get('reliable_fraction', 0))} | "
            f"{number(100 * rel.get('false_reliable_track_fraction', 0))} | "
            f"{number(result.get('analysis_s'))} |"
        )
    for result in results:
        rows.extend(
            [
                "",
                f"## {result['title']}",
                "",
                "```json",
                json.dumps(result, indent=2, ensure_ascii=False),
                "```",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")


def evaluate_track(root: Path, track: dict, work: Path, previews=True, input_mode="mix") -> dict:
    from muestra.project import create_project, load_analysis, open_project

    from .analysis import spectral_features
    from .audio import decode
    from .grid_tools import SourcePreviewGridParams, source_preview_grid
    from .tools import SourceAnalyzeParams, SourceImportParams, source_analyze, source_import

    project_root = work / track["id"] / input_mode
    project = (
        open_project(project_root)
        if (project_root / "muestra.toml").exists()
        else create_project(project_root)
    )
    audio_path = root / track["audio"]
    if input_mode != "mix":
        import soundfile as sf

        drum_paths = track.get("reference_stems", {}).get("drums", [])
        if not drum_paths:
            raise ValueError("No reference drum stems available")
        drums, drum_sr = decode(root / drum_paths[0])
        if input_mode == "fusion":
            mix, mix_sr = decode(audio_path)
            if mix_sr != drum_sr or len(mix) != len(drums):
                raise ValueError("Reference drum/mix sample alignment differs")
            drums = (mix + drums) / 2
        audio_path = project_root / f"{input_mode}.wav"
        sf.write(audio_path, drums, drum_sr, subtype="PCM_16")
    imported = source_import(SourceImportParams(project=str(project_root), path=str(audio_path)))
    if input_mode != "mix":
        audio_path.unlink()
        audio_path = project.resolve(imported.file)
    started = time.perf_counter()
    output = source_analyze(
        SourceAnalyzeParams(project=str(project_root), source_id=imported.source_id)
    )
    elapsed = time.perf_counter() - started
    analysis = load_analysis(project, imported.source_id)
    result = {
        "id": track["id"],
        "title": track["title"],
        "input_mode": input_mode,
        "reference_strength": track.get("reference_strength", "none; audition only"),
        "audio": str(audio_path),
        "analysis_s": elapsed,
        "bpm": analysis.bpm_global,
        "tempo_variable": analysis.tempo_variable,
        "key": analysis.key.model_dump() if analysis.key else None,
        "grid": output.grid.model_dump(mode="json") if output.grid else None,
        "reliability_map": analysis.reliability.model_dump() if analysis.reliability else None,
        "analysis_path": str(project.resolve(output.analysis_path)),
    }
    if track.get("beats"):
        annotation = np.loadtxt(root / track["beats"], ndmin=2)
        reference_beats = annotation[:, 0]
        reference_downs = annotation[annotation[:, 1] == 1, 0]
        result["beats"] = event_metrics(reference_beats, analysis.beats_s)
        result["downbeats"] = event_metrics(reference_downs, analysis.downbeats_s)
        result["tempo"] = tempo_metrics(
            60 / np.median(np.diff(reference_beats)), analysis.bpm_global
        )
        result["reliability"] = reliability_metrics(analysis, reference_beats, reference_downs)
    audio, sr = decode(audio_path)
    _, _, chroma = spectral_features(audio, sr)
    result["key_frame_normalized"] = key_alternative(chroma)
    if track.get("key"):
        result["key_error"] = key_error(track["key"], result["key"])
        result["key_alternative_error"] = key_error(track["key"], result["key_frame_normalized"])
    if previews:
        preview = source_preview_grid(
            SourcePreviewGridParams(
                project=str(project_root),
                source_id=imported.source_id,
                window_s=(0, min(20.0, analysis.duration_s)),
                alternatives=True,
            )
        )
        result["previews"] = [str(project.resolve(item.path)) for item in preview.previews]
        result["listen"] = preview.listen
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--input-mode", choices=["mix", "drums", "fusion"], default="mix")
    parser.add_argument("--no-previews", action="store_true")
    args = parser.parse_args()
    root, tracks = load_tracks(args.input.resolve())
    work = (args.work or args.report.parent / "evaluation-projects").resolve()
    results = []
    for track in tracks:
        if args.only and track["id"] not in args.only:
            continue
        print(f"Analyzing {track['id']} ({args.input_mode})", flush=True)
        try:
            result = evaluate_track(root, track, work, not args.no_previews, args.input_mode)
        except Exception as exc:
            result = {
                "id": track["id"],
                "title": track["title"],
                "error": f"{type(exc).__name__}: {exc}",
            }
        results.append(result)
        write_report(args.report.resolve(), results)
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in ("id", "error", "bpm", "beats", "downbeats")
                    if key in result
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
