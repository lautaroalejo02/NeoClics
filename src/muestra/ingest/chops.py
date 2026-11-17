"""Downbeat-aligned, section-contained loop proposals and durable choices."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import soundfile as sf
from pydantic import ValidationError

from ..errors import MuestraError, issues_from_validation_error
from ..feedback import append_event
from ..project import (
    Project,
    chop_times,
    edit_song_document,
    load_analysis,
    load_song,
    next_set_id,
)
from ..schemas.chops import (
    AgentPick,
    CandidateScore,
    ChopAdjust,
    ChopCandidate,
    ChopCandidateSet,
    ChopQuery,
    ChosenRecord,
)
from ..schemas.feedback import ChopChoiceEvent
from ..schemas.song import Chop
from .audio import decode, fade, stereo
from .storage import digest, project_lock, read_json, source_file, write_json


def set_paths(project: Project, source_id: str | None = None) -> list[Path]:
    directory = project.path("cache/chops")
    paths = (directory / source_id).glob("*.json") if source_id else directory.glob("*/*.json")
    return sorted(p for p in paths if re.fullmatch(r"cs\d{3,}\.json", p.name))


def read_set(path: Path) -> ChopCandidateSet:
    try:
        return ChopCandidateSet.model_validate(read_json(path))
    except ValidationError as exc:
        raise MuestraError(issues_from_validation_error(exc, path_prefix=str(path))) from exc


def fingerprint(project: Project, source_id: str, analysis) -> str:
    _, source = source_file(project, source_id)
    return hashlib.sha256((digest(source) + analysis.model_dump_json()).encode()).hexdigest()


def stem_path(project: Project, analysis, stem: str) -> Path:
    if stem == "mix":
        return project.resolve(analysis.file)
    path = getattr(analysis.stems, stem, None) if analysis.stems else None
    if not path:
        raise MuestraError.one(
            "ref_not_found",
            f"No {stem} stem is available.",
            path="stem",
            hint="Run source.separate first, or explicitly request stem='mix'.",
        )
    return project.resolve(path)


def score_loop(audio, sr, start, end, beats, stem, vocal=None, reference=None) -> CandidateScore:
    excerpt = audio[round(start * sr) : round(end * sr)].mean(axis=1)
    if len(excerpt) < 4:
        return CandidateScore(value=0, reasons=["El corte es demasiado corto para evaluar."])
    width = min(round(sr * 0.08), len(excerpt) // 4)
    # Compare context AFTER the end to context AFTER the start: tail-vs-head waveforms
    # alone confuse ordinary periodic motion with a discontinuity.
    head = audio[round(start * sr) : round(start * sr) + width].mean(axis=1)
    tail = audio[round(end * sr) : round(end * sr) + width].mean(axis=1)
    if len(tail) != width or not width:
        seam = 0.0
    else:
        a, b = (
            abs(np.fft.rfft(head * np.hanning(width))),
            abs(np.fft.rfft(tail * np.hanning(width))),
        )
        similarity = float(a @ b / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-12))
        rms_a, rms_b = np.sqrt(np.mean(head**2)), np.sqrt(np.mean(tail**2))
        level = float(min(rms_a, rms_b) / max(rms_a, rms_b, 1e-8))
        seam = similarity * level
    grid = np.array(beats)
    intervals = np.diff(grid[(grid >= start - 1e-6) & (grid <= end + 1e-6)])
    if len(intervals) > 2:
        trend = np.polyval(
            np.polyfit(np.arange(len(intervals)), intervals, 1), np.arange(len(intervals))
        )
        jitter = float(np.std(intervals - trend) / max(np.mean(intervals), 1e-8))
    else:
        jitter = 0.0
    stability = float(np.exp(-10 * jitter))
    if reference is None:
        reference = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
    rms = float(np.sqrt(np.mean(excerpt.astype(np.float64) ** 2)))
    energy = min(1.0, rms / max(reference, 1e-8))
    hop = max(1, round(sr * 0.02))
    frames = len(excerpt) // hop
    envelope = np.sqrt(np.mean(excerpt[: frames * hop].reshape(frames, hop) ** 2, axis=1))
    onsets = np.maximum(np.diff(envelope), 0)
    onset_rate = float(np.count_nonzero(onsets > max(rms * 0.2, 1e-6)) / max(end - start, 1e-6))
    density = min(1, onset_rate / 4)
    spectrum = abs(np.fft.rfft(excerpt[:: max(1, sr // 8000)])) + 1e-12
    harmonic = float(np.clip(1 - np.exp(np.mean(np.log(spectrum))) / np.mean(spectrum), 0, 1))
    bleed = 0.0
    if stem == "other" and vocal is not None:
        other = audio[round(start * sr) : round(end * sr) : 8].ravel()
        voice = vocal[round(start * sr) : round(end * sr) : 8].ravel()
        if len(voice) == len(other):
            bleed = float(
                abs(other @ voice) / max(np.linalg.norm(other) * np.linalg.norm(voice), 1e-8)
            )
    tonal_weight = harmonic if stem in {"bass", "other", "mix"} else density
    score = float(
        np.clip(
            0.35 * seam
            + 0.25 * stability
            + 0.15 * energy
            + 0.15 * density
            + 0.1 * tonal_weight
            - 0.15 * bleed,
            0,
            1,
        )
    )
    if rms < 1e-6:
        score = 0.0
    return CandidateScore(
        value=score,
        reasons=[
            f"Continuidad del loop: {seam:.3f} (espectro y nivel en el punto de vuelta).",
            f"Pulso estable: {stability:.3f}; irregularidad residual {jitter * 100:.2f}% "
            "descontando la deriva gradual del tempo.",
            f"Energía relativa: {energy:.3f}; ataques por segundo: {onset_rate:.2f}.",
            f"Contenido tonal: {harmonic:.3f}; coincidencia con voz "
            f"(posible filtración): {bleed:.3f}.",
        ],
    )


def find(project: Project, source_id: str, query: ChopQuery) -> tuple[ChopCandidateSet, Path]:
    analysis = load_analysis(project, source_id)
    stem = query.stem or "other"
    audio, sr = decode(stem_path(project, analysis, stem))
    vocal = None
    if stem == "other" and analysis.stems and analysis.stems.vocals:
        voice, voice_sr = decode(project.resolve(analysis.stems.vocals))
        if sr == voice_sr:
            vocal = voice
    sections = [s for s in analysis.sections if query.section is None or s.label == query.section]
    if not analysis.sections and query.section is None:
        from ..schemas.analysis import Section

        sections = [Section(label="all", start_s=0, end_s=analysis.duration_s)]
    if not sections:
        raise MuestraError.one(
            "ref_not_found",
            f"Unknown section {query.section}.",
            hint="Use a label from source.analyze.sections.",
        )
    lengths = [query.length_beats] if query.length_beats else [4.0, 8.0, 16.0, 32.0]
    proposals = []
    reference = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
    for index in range(1, len(analysis.downbeats_s) + 1):
        for length in lengths:
            if length > 128:
                raise MuestraError.one("too_large", "Chop length must be <=128 beats.")
            timing = chop_times(analysis, index, length)
            if timing is None:
                continue
            start, end = timing
            if not any(start >= s.start_s and end <= s.end_s for s in sections):
                continue
            score = score_loop(audio, sr, start, end, analysis.beats_s, stem, vocal, reference)
            if score.value > 0:
                proposals.append((score.value, start, end, index, length, score))
    proposals.sort(key=lambda p: (-p[0], p[1], p[4]))
    selected = []
    for proposal in proposals:
        _, start, end, *_ = proposal
        if any(
            max(0, min(end, p[2]) - max(start, p[1])) / min(end - start, p[2] - p[1]) > 0.5
            for p in selected
        ):
            continue
        selected.append(proposal)
        if len(selected) == query.n:
            break
    if not selected:
        raise MuestraError.one(
            "no_candidates",
            "No audible loops fit the section and beat grid.",
            hint="Try a shorter length, another section or stem='mix'.",
        )
    with project_lock(project):
        # Shared next_set_id is source-local; the public id has no source component,
        # so take max+1 over ALL sources to guarantee global uniqueness.
        directories = {source_id, *(p.parent.name for p in set_paths(project))}
        set_id = f"cs{max(int(next_set_id(project, s)[2:]) for s in directories):03d}"
        candidates = []
        preview_dir = project.path(f"cache/previews/{source_id}/{set_id}")
        preview_dir.mkdir(parents=True, exist_ok=False)
        for number, (_, start, end, index, length, score) in enumerate(selected, 1):
            chop = Chop(
                id="preview", source=source_id, stem=stem, start_downbeat=index, length_beats=length
            )
            local_bpm = 60 * length / (end - start)
            bpm = load_song(project).meta.bpm if project.song_path.exists() else local_bpm
            from ..render.core import render_chop

            try:
                rendered = render_chop(project, chop, project.config.sample_rate, bpm)
                preview_sr = project.config.sample_rate
            except NotImplementedError:
                rendered = fade(stereo(audio[round(start * sr) : round(end * sr)]), sr)
                preview_sr = sr
                score.reasons.append(
                    "Preview sin warp: render_chop aún no está implementado; "
                    "extracto original con fundidos de 5 ms."
                )
            preview = preview_dir / f"cand{number}.wav"
            sf.write(preview, np.tile(rendered, (2, 1)), preview_sr, subtype="PCM_24")
            candidates.append(
                ChopCandidate(
                    id=f"{set_id}/cand{number}",
                    source_id=source_id,
                    stem=stem,
                    start_downbeat=index,
                    start_s=start,
                    end_s=end,
                    length_beats=length,
                    local_bpm=local_bpm,
                    preview=preview.relative_to(project.root).as_posix(),
                    score=score,
                )
            )
        best = candidates[0]
        justification = (
            f"Elijo este loop de {best.length_beats / 4:g} compases porque combina una vuelta "
            "fluida con un pulso regular y suficiente movimiento para sostener el groove. "
            f"Empieza en el primer tiempo a {best.start_s:.2f} s; "
            f"tempo local {best.local_bpm:.1f} BPM. " + " ".join(best.score.reasons)
        )
        candidate_set = ChopCandidateSet(
            set_id=set_id,
            source_id=source_id,
            created_at=datetime.now(UTC),
            query=query,
            candidates=candidates,
            pick=AgentPick(candidate_id=best.id, justification=justification),
        )
        path = project.path(f"cache/chops/{source_id}/{set_id}.json")
        write_json(path, candidate_set.model_dump(mode="json"))
        write_json(
            path.with_suffix(".meta.json"),
            {"fingerprint": fingerprint(project, source_id, analysis)},
        )
        return candidate_set, path


def choose(project: Project, candidate_id: str, chop_id: str, mode: str, adjust: ChopAdjust | None):
    set_id = candidate_id.split("/")[0]
    with project_lock(project):
        matches = [p for p in set_paths(project) if p.stem == set_id]
        if len(matches) != 1:
            raise MuestraError.one("ref_not_found", f"Candidate set {set_id} missing or ambiguous.")
        path = matches[0]
        candidate_set = read_set(path)
        candidate = next((c for c in candidate_set.candidates if c.id == candidate_id), None)
        if candidate is None:
            raise MuestraError.one("ref_not_found", f"Unknown candidate {candidate_id}.")
        analysis = load_analysis(project, candidate.source_id)
        meta = path.with_suffix(".meta.json")
        if meta.exists() and read_json(meta)["fingerprint"] != fingerprint(
            project, candidate.source_id, analysis
        ):
            raise MuestraError.one(
                "stale_candidates",
                "Source, stems or grid changed since this proposal.",
                hint="Run source.find_chops again before choosing.",
            )
        adjust = adjust or ChopAdjust()
        try:
            chop = Chop(
                id=chop_id,
                source=candidate.source_id,
                stem=candidate.stem,
                start_downbeat=candidate.start_downbeat,
                start_offset_beats=adjust.start_beats,
                length_beats=candidate.length_beats + adjust.length_beats,
            )
        except ValidationError as exc:
            raise MuestraError(issues_from_validation_error(exc, path_prefix="adjust")) from exc
        if (
            chop_times(analysis, chop.start_downbeat, chop.length_beats, chop.start_offset_beats)
            is None
        ):
            raise MuestraError.one(
                "chop_overrun", "Adjusted chop does not fit the tracked beat grid."
            )
        stem_path(project, analysis, chop.stem)
        song = load_song(project)
        if chop.source not in {s.id for s in song.sources}:
            raise MuestraError.one("ref_not_found", "Source is not registered in song.yaml.")

        def mutate(doc):
            chops = doc.setdefault("chops", [])
            existing = next((c for c in chops if c["id"] == chop_id), None)
            values = chop.model_dump(mode="json", exclude_defaults=True)
            # Keep a user's warp and fade when replacing an existing chop.
            if existing is None:
                chops.append(values)
            else:
                for field in (
                    "source",
                    "stem",
                    "start_downbeat",
                    "start_offset_beats",
                    "length_beats",
                ):
                    existing[field] = getattr(chop, field)
            # Validate the whole edit before the round-trip helper writes anything.
            from ..schemas.song import Song

            edited = Song.model_validate(doc)
            if issues := edited.check():
                raise MuestraError(issues)

        edit_song_document(project, mutate)
        # Return the actual chop, including any preserved warp/fade settings.
        chop = next(c for c in load_song(project).chops if c.id == chop_id)
        now = datetime.now(UTC)
        candidate_set.chosen = ChosenRecord(
            candidate_id=candidate_id, mode=mode, adjust=adjust, at=now
        )
        if mode == "manual":
            append_event(
                project,
                ChopChoiceEvent(ts=now, candidate_id=candidate_id, adjust=adjust),
            )
        write_json(path, candidate_set.model_dump(mode="json"))
        return candidate_set, chop, path
