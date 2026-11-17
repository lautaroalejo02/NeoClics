"""Owner-run benchmark: uv run --extra separation python -m muestra.ingest.benchmark.

Runs each model in a fresh subprocess, samples process-tree RSS every 100ms,
and writes JSON + Markdown. No uploads, references or ground truth required.
Energy/correlation proxies do NOT estimate SDR or perceptual quality.
"""

from __future__ import annotations

import argparse
import json
import os
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


def proxies(source: Path, stems: dict[str, Path]) -> dict:
    signals = {}
    for name, path in stems.items():
        x, sr = decode(path)
        signals[name] = x.astype(np.float64)
    mix, mix_sr = decode(source)
    mix = stereo(mix)
    if mix_sr != sr:
        from math import gcd

        from scipy.signal import resample_poly

        divisor = gcd(sr, mix_sr)
        mix = resample_poly(mix, sr // divisor, mix_sr // divisor, axis=0)
    size = min(len(mix), *(len(x) for x in signals.values()))
    mix = mix[:size]
    energy = max(float(np.mean(mix**2)), 1e-15)
    residual = mix - sum(x[:size] for x in signals.values())
    correlations = {}
    names = list(signals)
    for i, name in enumerate(names):
        for other in names[i + 1 :]:
            a, b = signals[name][:size:8].ravel(), signals[other][:size:8].ravel()
            denominator = np.linalg.norm(a) * np.linalg.norm(b)
            correlations[f"{name}/{other}"] = float(abs(a @ b) / max(denominator, 1e-15))
    return {
        "stem_energy_ratio": {
            k: float(np.mean(v[:size] ** 2) / energy) for k, v in signals.items()
        },
        "pairwise_abs_correlation": correlations,
        "reconstruction_error_db": float(
            10 * np.log10(max(float(np.mean(residual**2)), 1e-15) / energy)
        ),
    }


def worker(audio: Path, model: str, result_path: Path):
    import psutil

    peak = [0]
    stop = threading.Event()

    def sample():
        process = psutil.Process()
        while not stop.is_set():
            processes = [process, *process.children(recursive=True)]
            total = 0
            for child in processes:
                try:
                    total += child.memory_info().rss
                except psutil.NoSuchProcess:
                    pass
            peak[0] = max(peak[0], total)
            stop.wait(0.1)

    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    started = time.perf_counter()
    result = {"file": str(audio), "model": model}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            outputs = LocalBackend().separate(audio, Path(tmp), model)
            elapsed = time.perf_counter() - started
            result.update({"ok": True, "separation_s": elapsed, "proxies": proxies(audio, outputs)})
    except Exception as exc:
        result.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        stop.set()
        monitor.join()
    result.update({"total_s": time.perf_counter() - started, "peak_rss_mib": peak[0] / 1024**2})
    write_json(result_path, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", nargs="+", type=Path)
    parser.add_argument("--models", nargs="+", default=["htdemucs", "mel-roformer+htdemucs"])
    parser.add_argument("--report", type=Path, default=Path("ingest-benchmark.md"))
    parser.add_argument("--seconds", type=float, help="Benchmark only the first N seconds.")
    parser.add_argument("--timeout", type=float, default=1800, help="Per model seconds.")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args.audio[0], args.models[0], args.worker)
        return
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for source in args.audio:
            data, sr = decode(source)
            if args.seconds:
                data = data[: round(sr * args.seconds)]
            input_path = Path(tmp) / "input.wav"
            sf.write(input_path, stereo(data), sr, subtype="FLOAT")
            for model in args.models:
                destination = Path(tmp) / "result.json"
                destination.unlink(missing_ok=True)
                try:
                    subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "muestra.ingest.benchmark",
                            str(input_path),
                            "--models",
                            model,
                            "--worker",
                            str(destination),
                        ],
                        check=True,
                        timeout=args.timeout,
                        env=os.environ.copy(),
                    )
                    result = json.loads(destination.read_text(encoding="utf-8"))
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
                    result = {"model": model, "ok": False, "error": str(exc)}
                result.update({"file": str(source.resolve()), "duration_s": len(data) / sr})
                results.append(result)
                # Persist partial results so a slow model never loses earlier measurements.
                write_report(args.report, results)


def write_report(path: Path, results: list[dict]):
    write_json(path.with_suffix(".json"), {"results": results})
    rows = [
        "# Muestra separation benchmark",
        "",
        "CPU only; 2 threads by default (MUESTRA_CPU_THREADS overrides). "
        "Shared laptop timings are pessimistic. RSS is sampled process-tree RAM, "
        "including model load; weights download time is included on first use.",
        "",
        "| File | Model | Audio s | Separation s | Peak RSS MiB | Result |",
        "|---|---|---:|---:|---:|---|",
    ]
    for r in results:
        rows.append(
            f"| {Path(r['file']).name} | {r['model']} | {r['duration_s']:.1f} | "
            f"{r.get('separation_s', r.get('total_s', 0)):.2f} | "
            f"{r.get('peak_rss_mib', 0):.1f} | {r.get('error', 'OK')} |"
        )
    rows += [
        "",
        "## Objective proxies",
        "",
        "These are not quality scores. High pairwise correlation can suggest bleed but "
        "also shared musical notes; reconstruction measures additive consistency, not "
        "source correctness. Listen to drums/transients, bass attacks and vocal tails.",
        "",
        "```json",
        json.dumps(results, indent=2, ensure_ascii=False),
        "```",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
