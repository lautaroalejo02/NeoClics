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
    # Reporting/history updates do not move the audio or the effective grid.
    # In particular, an idempotent regrid must leave its retained sets usable.
    content = analysis.model_dump_json(
        include={
            "file",
            "duration_s",
            "sample_rate",
            "beats_s",
            "downbeats_s",
            "stems",
            "separation_model",
        }
    )
    return hashlib.sha256((digest(source) + content).encode()).hexdigest()


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


def loop_continuity(audio, sr, start, end) -> float:
    first, last = round(start * sr), round(end * sr)
    if first < 0 or last <= first or last >= len(audio):
        return 0.0
    similarities = []
    for seconds in (0.08, 0.32):
        width = min(round(sr * seconds), (last - first) // 4)
        head = audio[first : first + width].mean(axis=1)
        tail = audio[last : last + width].mean(axis=1)
        if len(tail) != width or not width:
            return 0.0
        a, b = (
            abs(np.fft.rfft(head * np.hanning(width))),
            abs(np.fft.rfft(tail * np.hanning(width))),
        )
        similarity = float(a @ b / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-12))
        rms_a, rms_b = np.sqrt(np.mean(head**2)), np.sqrt(np.mean(tail**2))
        level = float(min(rms_a, rms_b) / max(rms_a, rms_b, 1e-8))
        similarities.append(similarity * level)
    width = round(sr * 0.08)
    context = np.r_[
        audio[max(0, first - width) : first + width].mean(axis=1),
        audio[max(0, last - width) : min(len(audio), last + width)].mean(axis=1),
    ]
    natural = float(np.percentile(abs(np.diff(context)), 95))
    step = float(abs(audio[last - 1].mean() - audio[first].mean()))
    ratio = step / max(natural, 1e-6)
    safe = float(np.exp(-max(0, ratio - 1) / 4))
    return float(np.sqrt(np.prod(similarities)) * safe)


def score_loop(audio, sr, start, end, beats, stem, vocal=None, reference=None) -> CandidateScore:
    excerpt = audio[round(start * sr) : round(end * sr)].mean(axis=1)
    if len(excerpt) < 4:
        return CandidateScore(value=0, reasons=["El corte es demasiado corto para evaluar."])
    seam = loop_continuity(audio, sr, start, end)
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
            f"Continuidad del loop: {seam:.3f} (espectro a 80/320 ms, nivel y salto de la junta).",
            f"Pulso estable: {stability:.3f}; irregularidad residual {jitter * 100:.2f}% "
            "descontando la deriva gradual del tempo.",
            f"Energía relativa: {energy:.3f}; ataques por segundo: {onset_rate:.2f}.",
            f"Contenido tonal: {harmonic:.3f}; coincidencia con voz "
            f"(posible filtración): {bleed:.3f}.",
        ],
    )


def mmss(seconds: float) -> str:
    """'m:ss.s' (e.g. '0:15.2') for agents and humans."""
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def find(project: Project, source_id: str, query: ChopQuery) -> tuple[ChopCandidateSet, Path]:
    from .reliability import reliable_window

    analysis = load_analysis(project, source_id)
    if len(analysis.beats_s) < 2 or not analysis.downbeats_s:
        raise MuestraError.one(
            "no_candidates",
            "No beat grid was detected in this source.",
            hint="Listen with source.preview_grid(window_s=[start, end]); "
            "import a rhythmic source with source.import then source.analyze.",
        )
    original_fingerprint = fingerprint(project, source_id, analysis)
    stem = query.stem or ("other" if analysis.stems and analysis.stems.other else "mix")
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
            hint="Use a section label returned by source.analyze.",
        )
    lengths = [query.length_beats] if query.length_beats else [4.0, 8.0, 16.0, 32.0]
    if any(length > 128 for length in lengths):
        raise MuestraError.one("too_large", "Chop length must be <=128 beats.")
    proposals = []
    reference = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
    grid = np.asarray(analysis.beats_s)
    downbeat_indices = np.array([np.argmin(abs(grid - beat)) for beat in analysis.downbeats_s])
    window = query.window_s
    slack = 0.25  # seconds: a loop that ends a hair past the window still counts

    def regular(index, length):
        start_index = downbeat_indices[index - 1]
        required = start_index + np.arange(0, int(length // 4) + 1) * 4
        if not np.all(np.isin(required, downbeat_indices)):
            return False
        inside = downbeat_indices[
            (downbeat_indices >= start_index) & (downbeat_indices <= start_index + length)
        ]
        return not np.any(np.diff(inside) != 4)

    def propose(index, length, *, strict=True):
        timing = chop_times(analysis, index, length)
        if timing is None:
            return None
        start, end = timing
        if not query.allow_unreliable and not reliable_window(analysis, start, end):
            return None
        if strict and not query.allow_unreliable and not regular(index, length):
            return None
        if window and not (start >= window[0] - slack and end <= window[1] + slack):
            return None
        if (strict or query.section) and not any(
            start >= s.start_s and end <= s.end_s for s in sections
        ):
            return None
        score = score_loop(audio, sr, start, end, analysis.beats_s, stem, vocal, reference)
        return score.value, start, end, index, length, score

    if query.at_s is not None:
        # "el riff del 0:15": the downbeat nearest that time first (best length
        # there), then the other lengths at that start, then neighbouring bars.
        times = np.asarray(analysis.downbeats_s)
        order = list(np.argsort(np.abs(times - query.at_s), kind="stable")[: 2 * query.n + 2] + 1)
        here = [p for p in (propose(int(order[0]), n, strict=False) for n in lengths) if p]
        here.sort(key=lambda p: -p[0])
        selected = here[: query.n]
        best_length = selected[0][4] if selected else lengths[0]
        for index in order[1:]:
            if len(selected) >= query.n:
                break
            proposal = propose(int(index), best_length, strict=False)
            if proposal:
                selected.append(proposal)
    else:
        for index in range(1, len(analysis.downbeats_s) + 1):
            for length in lengths:
                proposal = propose(index, length)
                if proposal and proposal[0] > 0:
                    proposals.append(proposal)
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
        where = f" between {mmss(window[0])} and {mmss(window[1])}" if window else ""
        raise MuestraError.one(
            "no_candidates",
            f"No audible loops fit the section, time window and beat grid{where}.",
            hint="Try a shorter length_beats, a wider window_s, another section, "
            "at_s for an exact spot, stem='mix', or allow_unreliable=true after "
            "checking source.preview_grid(window_s=[start, end]).",
        )
    with project_lock(project):
        if (
            fingerprint(project, source_id, load_analysis(project, source_id))
            != original_fingerprint
        ):
            raise MuestraError.one(
                "stale_candidates",
                "Source or analysis changed while candidates were scored.",
                hint="Run source.find_chops again.",
            )
        # Shared next_set_id is source-local; the public id has no source component,
        # so take max+1 over ALL sources to guarantee global uniqueness.
        directories = {source_id, *(p.parent.name for p in set_paths(project))}
        next_number = max(int(next_set_id(project, s)[2:]) for s in directories)
        # Interrupted preview rendering also reserves its id; never collide on retry.
        for preview_path in project.path("cache/previews").glob("*/cs*"):
            if re.fullmatch(r"cs\d{3,}", preview_path.name):
                next_number = max(next_number, int(preview_path.name[2:]) + 1)
        set_id = f"cs{next_number:03d}"
        candidates = []
        preview_dir = project.path(f"cache/previews/{source_id}/{set_id}")
        preview_dir.mkdir(parents=True, exist_ok=False)
        for number, (_, start, end, index, length, score) in enumerate(selected, 1):
            chop = Chop(
                id="preview", source=source_id, stem=stem, start_downbeat=index, length_beats=length
            )
            local_bpm = 60 * length / (end - start)
            bpm = load_song(project).meta.bpm if project.song_path.exists() else local_bpm
            from ..render.core import render_chop_loop

            try:
                # Two repeats crossfaded at the seam and the wrap (WP13).
                looped = render_chop_loop(project, chop, project.config.sample_rate, bpm)
                preview_sr = project.config.sample_rate
            except NotImplementedError:
                rendered = fade(stereo(audio[round(start * sr) : round(end * sr)]), sr)
                looped = np.tile(rendered, (2, 1))
                preview_sr = sr
                score.reasons.append(
                    "Preview sin warp: render_chop aún no está implementado; "
                    "extracto original con fundidos de 5 ms."
                )
            preview = preview_dir / f"cand{number}.wav"
            sf.write(preview, looped, preview_sr, subtype="PCM_24")
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
                    bar=index,
                    start_mmss=mmss(start),
                    length_s=round(end - start, 3),
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


def _free_chop_id(song, base: str) -> str:
    taken = {c.id for c in song.chops}
    stem = re.sub(r"-\d+$", "", base)[:60]
    n = 2
    while f"{stem}-{n}" in taken:
        n += 1
    return f"{stem}-{n}"


def _repeat_plan(song, chop_id: str, new_length: float):
    """Keep every clip's span when the chop's length changes (WP13 finding 2).

    Returns ``(changes, clean)``: ``changes`` = ``[(track_id, clip_index,
    old_repeat, new_repeat)]``; ``clean`` is False when some clip's span
    (old length x repeat) is not a whole number of new lengths."""
    old = next((c for c in song.chops if c.id == chop_id), None)
    if old is None or abs(old.length_beats - new_length) < 1e-9:
        return [], True
    changes, clean = [], True
    for track in song.tracks:
        for index, clip in enumerate(track.clips or []):
            if clip.chop != chop_id:
                continue
            ratio = old.length_beats * clip.repeat / new_length
            if ratio >= 1 - 1e-9 and abs(ratio - round(ratio)) < 1e-9:
                if round(ratio) != clip.repeat:
                    changes.append((track.id, index, clip.repeat, round(ratio)))
            else:
                clean = False
    return changes, clean


def choose(project: Project, candidate_id: str, chop_id: str, mode: str, adjust: ChopAdjust | None):
    """Write a candidate into song.yaml as ``chop_id``.

    Returns ``(set, chop, path, outcome)``; ``outcome`` has ``chop_id`` (the
    id actually written), ``repeat_changes``, ``message`` (Spanish, for the
    human) and ``agent_todo`` (what the agent must still wire in, or None).
    A manual choice is recorded in feedback.jsonl BEFORE anything can fail,
    so the agent always learns what the human picked.
    """
    set_id = candidate_id.split("/")[0]
    with project_lock(project):
        matches = [p for p in set_paths(project) if p.stem == set_id]
        if len(matches) != 1:
            raise MuestraError.one(
                "ref_not_found",
                f"No encuentro la tanda de candidatos {set_id}.",
                hint="Pedile al agente que busque candidatos de nuevo (source.find_chops).",
            )
        path = matches[0]
        candidate_set = read_set(path)
        candidate = next((c for c in candidate_set.candidates if c.id == candidate_id), None)
        if candidate is None:
            raise MuestraError.one(
                "ref_not_found",
                f"No existe el candidato {candidate_id}.",
                hint=f"Elegí uno de: {', '.join(c.id for c in candidate_set.candidates)}.",
            )
        adjust = adjust or ChopAdjust()
        now = datetime.now(UTC)
        if mode == "manual":
            append_event(
                project,
                ChopChoiceEvent(ts=now, candidate_id=candidate_id, adjust=adjust),
            )
        analysis = load_analysis(project, candidate.source_id)
        meta = path.with_suffix(".meta.json")
        if meta.exists() and read_json(meta)["fingerprint"] != fingerprint(
            project, candidate.source_id, analysis
        ):
            raise MuestraError.one(
                "stale_candidates",
                "El audio o la grilla de la fuente cambiaron desde que se propusieron "
                "estos candidatos.",
                hint="Run source.find_chops again before choosing.",
            )
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
                "chop_overrun",
                "Con ese ajuste el corte se sale de los tiempos detectados del tema.",
                hint="Probá un ajuste más chico de inicio o de largo.",
            )
        stem_path(project, analysis, chop.stem)
        song = load_song(project)
        if chop.source not in {s.id for s in song.sources}:
            raise MuestraError.one(
                "ref_not_found",
                "La fuente de este candidato no está en song.yaml.",
                hint=f"Add source {chop.source} to song.yaml sources first.",
            )
        changes, clean = _repeat_plan(song, chop_id, chop.length_beats)
        written = chop_id if clean else _free_chop_id(song, chop_id)
        if written != chop_id:
            chop = chop.model_copy(update={"id": written})
            changes = []

        def mutate(doc):
            chops = doc.setdefault("chops", [])
            existing = next((c for c in chops if c["id"] == written), None)
            values = chop.model_dump(mode="json", exclude_defaults=True)
            # Keep a user's warp and fade when replacing an existing chop.
            if existing is None:
                previous = next((c for c in chops if c["id"] == chop_id), None)
                for field in ("warp", "fade_ms"):
                    if previous is not None and field in previous:
                        values[field] = previous[field]
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
            tracks = {t["id"]: t for t in doc.get("tracks", [])}
            for track_id, index, _, repeat in changes:
                tracks[track_id]["clips"][index]["repeat"] = repeat
            # Validate the whole edit before the round-trip helper writes anything.
            from ..schemas.song import Song

            edited = Song.model_validate(doc)
            if issues := edited.check():
                raise MuestraError(issues)

        edit_song_document(project, mutate)
        # Return the actual chop, including any preserved warp/fade settings.
        chop = next(c for c in load_song(project).chops if c.id == written)
        candidate_set.chosen = ChosenRecord(
            candidate_id=candidate_id, mode=mode, adjust=adjust, at=now, chop_id=written
        )
        write_json(path, candidate_set.model_dump(mode="json"))
        beats = f"{chop.length_beats:g} tiempos"
        if written != chop_id:
            message = (
                f"Listo: guardé tu elección como un chop nuevo, {written} ({beats}), porque "
                f"no entra justo en el lugar de {chop_id}. El agente lo va a acomodar en el "
                "arreglo."
            )
            agent_todo = (
                f"The human chose {candidate_id} ({chop.length_beats:g} beats). It was written "
                f"as NEW chop '{written}' because clips using '{chop_id}' span a number of "
                f"beats that {chop.length_beats:g} does not divide. Wire it in: point those "
                f"clips at chop: {written} and set repeat so they keep their span (or change "
                "the arrangement), validate, render."
            )
        elif changes:
            detail = ", ".join(f"{t} {a}→{b}" for t, _, a, b in changes)
            message = (
                f"Listo: el chop {chop_id} ahora dura {beats}; para que el loop siga ocupando "
                f"lo mismo ajusté las repeticiones ({detail})."
            )
            agent_todo = None
        else:
            message = f"Listo: el chop {chop_id} quedó como elegiste ({beats})."
            agent_todo = None
        outcome = {
            "chop_id": written,
            "repeat_changes": [
                {"track": t, "clip": i, "old_repeat": a, "new_repeat": b} for t, i, a, b in changes
            ],
            "message": message,
            "agent_todo": agent_todo,
        }
        from .reliability import chop_warning

        timing = chop_times(
            analysis, chop.start_downbeat, chop.length_beats, chop.start_offset_beats
        )
        warning = chop_warning(analysis, *timing)
        outcome["warnings"] = [warning] if warning else []
        return candidate_set, chop, path, outcome
