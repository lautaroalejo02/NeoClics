"""Reference-based, opt-in CPU separation evaluation in bounded subprocesses."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from .audio import decode, stereo
from .separation import LocalBackend
from .storage import write_json


def reference_audio(root, track):
    mix, sr = decode(root / track["audio"])
    references = {}
    for name, paths in track["reference_stems"].items():
        signals = []
        for path in paths:
            signal, ref_sr = decode(root / path)
            if ref_sr != sr or len(signal) != len(mix):
                raise ValueError("Reference/mix sample alignment differs")
            signals.append(stereo(signal))
        references[name] = sum(signals)
    return stereo(mix), sr, references


def reference_consistency(mix, references) -> dict:
    stride = max(1, len(mix) // 100_000)
    matrix = np.stack([signal[::stride].ravel() for signal in references.values()], axis=1)
    target = mix[::stride].ravel()
    gains = np.linalg.lstsq(matrix, target, rcond=None)[0]
    residual = float(np.sum((matrix @ gains - target) ** 2) / max(np.sum(target**2), 1e-15))
    return {
        "mix_residual_db": float(10 * np.log10(max(residual, 1e-15))),
        "mix_gains": dict(zip(references, gains.tolist(), strict=True)),
        "condition_number": float(np.linalg.cond(matrix)),
        "usable": bool(residual < 10 ** (-25 / 10) and np.all(gains > 0)),
    }


def stem_metrics(references, estimates) -> dict:
    names = list(references)
    matrix = np.stack([references[name].ravel() for name in names], axis=1).astype(np.float64)
    energies = np.mean(matrix**2, axis=0)
    result = {}
    for name, signal in estimates.items():
        estimated = signal.ravel().astype(np.float64)
        total_energy = max(float(np.mean(estimated**2)), 1e-15)
        gains = np.linalg.lstsq(matrix, estimated, rcond=None)[0]
        wrong = {
            source: float(gain**2 * energy / total_energy)
            for source, gain, energy in zip(names, gains, energies, strict=True)
            if source != name
        }
        item = {"wrong_source_energy_fraction": wrong}
        if name in references and np.mean(references[name] ** 2) > 1e-12:
            target = references[name].astype(np.float64)
            error = float(np.mean((target - signal) ** 2))
            energy = float(np.mean(target**2))
            item["sdr_db"] = float(10 * np.log10(energy / max(error, 1e-15)))
            scale = float(np.sum(target * signal) / max(np.sum(target**2), 1e-15))
            projection = scale * target
            item["si_sdr_db"] = float(
                10
                * np.log10(
                    max(np.sum(projection**2), 1e-15)
                    / max(np.sum((signal - projection) ** 2), 1e-15)
                )
            )
        else:
            item["absent_reference"] = True
            item["output_energy"] = total_energy
        result[name] = item
    return result


def worker(manifest, track_id, model, duration, destination):
    import psutil

    root = manifest.parent
    track = next(
        item
        for item in json.loads(manifest.read_text(encoding="utf-8"))["tracks"]
        if item["id"] == track_id
    )
    stop = threading.Event()
    peak = [0]

    def sample():
        process = psutil.Process()
        while not stop.is_set():
            total = 0
            for child in [process, *process.children(recursive=True)]:
                try:
                    total += child.memory_info().rss
                except psutil.NoSuchProcess:
                    pass
            peak[0] = max(peak[0], total)
            stop.wait(0.1)

    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    started = time.perf_counter()
    result = {"id": track_id, "model": model}
    try:
        mix, sr, references = reference_audio(root, track)
        consistency = reference_consistency(mix, references)
        result["reference_consistency"] = consistency
        if not consistency["usable"]:
            raise ValueError("Reference sum fails -25 dB residual gate; quality comparison refused")
        for name, gain in consistency["mix_gains"].items():
            references[name] *= gain
        if duration:
            size = round(duration * sr)
            repeats = int(np.ceil(size / len(mix)))
            mix = np.tile(mix, (repeats, 1))[:size]
            references = {
                name: np.tile(signal, (repeats, 1))[:size] for name, signal in references.items()
            }
            result["repeated_excerpt"] = repeats > 1
        result["audio_s"] = len(mix) / sr
        with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
            directory = Path(temporary)
            source = directory / "mix.wav"
            sf.write(source, mix, sr, subtype="FLOAT")
            separation_started = time.perf_counter()
            paths = LocalBackend().separate(source, directory / "stems", model)
            result["separation_s"] = time.perf_counter() - separation_started
            estimates = {}
            for name, path in paths.items():
                signal, est_sr = decode(path)
                if est_sr != sr or len(signal) != len(mix):
                    raise ValueError("Estimated stem alignment differs")
                estimates[name] = stereo(signal)
            result["stems"] = stem_metrics(references, estimates)
            result["ok"] = True
    except Exception as exc:
        result.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    finally:
        stop.set()
        monitor.join()
    result.update(total_s=time.perf_counter() - started, peak_rss_mib=peak[0] / 1024**2)
    write_json(destination, result)


def write_report(path, results):
    write_json(path.with_suffix(".json"), {"results": results})
    rows = [
        "# Reference stem separation evaluation",
        "",
        "SDR = 10 log10(reference energy / sample-aligned squared error), NOT filtered BSS Eval.",
        "SI-SDR is also reported. Wrong-source energy is simultaneous least-squares projection",
        "onto ALL reference sources, divided by estimate energy; correlated musical sources",
        "make this an attribution proxy, not an identifiable physical decomposition.",
        "Reference-to-mix gains are fitted ONCE from the input, never to an estimate.",
        "Reference sums worse than -25 dB residual are refused. No listening claims.",
        "Peak RSS samples the process tree every 100 ms, including model load.",
        "",
        "| Track | Model | Audio s | Separation s | Peak MiB | "
        "drums SDR | bass SDR | vocals SDR | other SDR |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        scores = [
            result.get("stems", {}).get(name, {}).get("sdr_db")
            for name in ("drums", "bass", "vocals", "other")
        ]
        formatted = " | ".join("n/a" if score is None else f"{score:.2f}" for score in scores)
        rows.append(
            f"| {result['id']} | {result['model']} | {result.get('audio_s', 0):.1f} | "
            f"{result.get('separation_s', 0):.2f} | "
            f"{result.get('peak_rss_mib', 0):.1f} | {formatted} |"
        )
    rows += ["", "```json", json.dumps(results, indent=2), "```", ""]
    path.write_text("\n".join(rows), encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--only", nargs="+", default=["disco", "funkjazz", "britpop"])
    parser.add_argument(
        "--models", nargs="+", default=["htdemucs", "htdemucs_ft", "mel-roformer+htdemucs"]
    )
    parser.add_argument("--duration", type=float)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--worker", type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(
            args.manifest.resolve(),
            args.only[0],
            args.models[0],
            args.duration,
            args.worker.resolve(),
        )
        return
    args.report.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(dir=args.report.parent) as temporary:
        destination = Path(temporary) / "worker.json"
        for track_id in args.only:
            for model in args.models:
                print(f"Evaluating {track_id} / {model}", flush=True)
                command = [
                    sys.executable,
                    "-m",
                    "muestra.ingest.separation_evaluation",
                    str(args.manifest.resolve()),
                    "--only",
                    track_id,
                    "--models",
                    model,
                    "--report",
                    str(args.report.resolve()),
                    "--worker",
                    str(destination),
                ]
                if args.duration:
                    command.extend(["--duration", str(args.duration)])
                try:
                    subprocess.run(command, check=True, timeout=args.timeout)
                    result = json.loads(destination.read_text(encoding="utf-8"))
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
                    result = {"id": track_id, "model": model, "ok": False, "error": str(exc)}
                results.append(result)
                write_report(args.report, results)
                print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
