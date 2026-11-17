"""Evaluate loop ranking on published reference grids; not a listening test."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

from .analysis import spectral_features
from .audio import decode
from .chops import score_loop
from .storage import write_json


def objective_proxy(audio, sr, start, end):
    first, last = round(start * sr), round(end * sr)
    mono = audio.mean(axis=1)
    if first < 1 or last >= len(mono):
        return None
    width = min(round(0.2 * sr), len(mono) - last, last - first)
    if width < 8:
        return None
    head, continuation = mono[first : first + width], mono[last : last + width]
    window = np.hanning(width)
    spectrum_head = abs(np.fft.rfft(head * window))
    spectrum_tail = abs(np.fft.rfft(continuation * window))
    spectral = float(
        spectrum_head
        @ spectrum_tail
        / max(np.linalg.norm(spectrum_head) * np.linalg.norm(spectrum_tail), 1e-12)
    )
    levels = [float(np.sqrt(np.mean(signal**2))) for signal in (head, continuation)]
    level = min(levels) / max(*levels, 1e-9)
    neighbourhood = np.r_[
        mono[max(0, first - width) : first + width], mono[last - width : last + width]
    ]
    natural = float(np.percentile(abs(np.diff(neighbourhood)), 95))
    step = float(abs(mono[last - 1] - mono[first]))
    excess_step = step / max(natural, 1e-6)
    click_safe = float(np.exp(-max(0, excess_step - 1) / 4))
    return {
        "value": click_safe * np.sqrt(spectral * level),
        "spectral": spectral,
        "level": level,
        "join_step_natural_p95": excess_step,
    }


def evaluate(root, track):
    annotation = np.loadtxt(root / track["beats"], ndmin=2)
    beats, downs = annotation[:, 0], annotation[annotation[:, 1] == 1, 0]
    audio, sr = decode(root / track["audio"])
    times, _, chroma = spectral_features(audio, sr)
    reference = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    rows = []
    for length in (8, 16):
        candidates = []
        for down in downs:
            first = int(np.argmin(abs(beats - down)))
            if first + length >= len(beats):
                continue
            start, end = float(beats[first]), float(beats[first + length])
            if not np.any(abs(downs - end) < 0.07):
                continue
            proxy = objective_proxy(audio, sr, start, end)
            if proxy is None:
                continue
            score = score_loop(audio, sr, start, end, beats, "mix", reference=reference).value
            period = float(np.median(np.diff(beats)))
            head = chroma[:, (times >= start) & (times < start + period)].mean(axis=1)
            tail = chroma[:, (times >= end) & (times < end + period)].mean(axis=1)
            harmonic = float(head @ tail / max(np.linalg.norm(head) * np.linalg.norm(tail), 1e-12))
            candidates.append(
                {
                    "start_s": start,
                    "end_s": end,
                    "score": score,
                    "proxy": proxy,
                    "harmonic_return_cosine": harmonic,
                }
            )
        correlation = None
        if len(candidates) >= 3:
            scores = [item["score"] for item in candidates]
            proxies = [item["proxy"]["value"] for item in candidates]
            value = float(spearmanr(scores, proxies).statistic)
            correlation = value if np.isfinite(value) else None
        ranked = sorted(candidates, key=lambda item: item["score"], reverse=True)
        rows.append(
            {
                "length_beats": length,
                "n": len(candidates),
                "spearman": correlation,
                "top_proxy": float(np.mean([item["proxy"]["value"] for item in ranked[:3]]))
                if ranked
                else None,
                "all_proxy": float(np.mean([item["proxy"]["value"] for item in candidates]))
                if candidates
                else None,
                "candidates": candidates,
            }
        )
    return {"id": track["id"], "title": track["title"], "lengths": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = args.manifest.resolve().parent
    tracks = json.loads(args.manifest.read_text(encoding="utf-8"))["tracks"]
    results = []
    for track in tracks:
        print(track["id"], flush=True)
        results.append(evaluate(root, track))
        values = [row for result in results for row in result["lengths"]]
        aggregate = {
            "tracks": len(results),
            "candidates": sum(row["n"] for row in values),
            "spearman_macro": float(
                np.mean([row["spearman"] for row in values if row["spearman"] is not None])
            ),
            "top_proxy_macro": float(
                np.mean([row["top_proxy"] for row in values if row["top_proxy"] is not None])
            ),
            "all_proxy_macro": float(
                np.mean([row["all_proxy"] for row in values if row["all_proxy"] is not None])
            ),
        }
        write_json(args.report.with_suffix(".json"), {"aggregate": aggregate, "results": results})
        rows = [
            "# Chop evaluation on reference grids",
            "",
            "Objective proxies, not annotated phrase quality or listening. All starts and ends",
            "are published downbeats; 8 and 16 annotated beats, without re-tracking excerpts.",
            "Proxy: 200-ms continuation spectral/level match times natural-derivative-normalized",
            "sample-join safety. Scorer uses 80-ms context; this tests another horizon but is",
            "NOT independent perceptual validation. Harmonic return cosine cannot tell a",
            "true phrase start from a cyclic rotation. No phrase-start accuracy is claimed.",
            "",
            "```json",
            json.dumps(aggregate, indent=2),
            "```",
            "",
            "| Track | Beats | Loops | Spearman | Top-3 proxy | All proxy |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for result in results:
            for row in result["lengths"]:
                rows.append(
                    f"| {result['id']} | {row['length_beats']} | {row['n']} | "
                    f"{row['spearman']} | {row['top_proxy']} | {row['all_proxy']} |"
                )
        args.report.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
