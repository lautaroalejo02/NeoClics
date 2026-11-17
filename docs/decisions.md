# Decisions

Dated log of decisions made where `docs/spec.md` was silent or ambiguous,
and of deviations from it. Each entry says what was decided and why.
(Newest last; WPs append their own entries.)

## 2026-11-17 — WP0: contracts + scaffold

### song.yaml semantics

1. **Time signature.** `meta.time_signature` exists and is
   `Literal["4/4"]` (default `"4/4"`); `BEATS_PER_BAR = 4` is the single
   code constant. The spec never defined meter; v0 assumes 4/4 and any
   other meter requires a `schema_version` bump.
2. **`start_downbeat` is 1-indexed** into `analysis.downbeats_s`
   (start_s = `downbeats_s[n-1]`). Bars/beats elsewhere are already
   1-indexed, so this stays consistent; `0` is a `too_small` error.
3. **Chop source tempo & end.** `chop_times()` walks the `beats_s` grid
   from the downbeat's matching beat (tolerance 50 ms); a fractional
   `length_beats` interpolates linearly between beats.
   `local_bpm = 60 * length_beats / (end_s - start_s)` — this is what
   handles tempo-variable sources: warp factors are per-chop, never
   global `bpm_global`.
4. **`warp.mode` composition.** `resample`: total rate =
   `project_bpm / local_bpm * 2^(pitch_semitones/12)` — pitch rides with
   tempo and `pitch_semitones` also changes sounding duration (the clip's
   grid span is fixed; audio is truncated/padded to it). `stretch`:
   Rubber Band time-stretch preserving pitch, then pitch-shift
   `pitch_semitones` (one pass). Chosen so `resample` stays honest vinyl
   behavior.
5. **Clip placement.** `at_bar` (1-indexed int) + `offset_beats`
   (fractional, may be negative; start must stay ≥ 0). `repeat` =
   consecutive instances of `length_beats`. **Overrunning
   `meta.length_bars` is a `clip_overrun` error** — silent truncation
   would hide agent mistakes; overlaps between clips on a track are
   allowed (they sum).
6. **fx set.** Discriminated union on `type`: highpass, lowpass,
   sidechain, compressor, limiter, gain, reverb, delay, chorus, phaser,
   saturation, plus generic `plugin` (VST3/AU name + optional preset +
   `params` dict in native ranges). Param names track pedalboard where a
   matching native effect exists.
7. **fx `id`.** Optional, unique per container (track or master);
   **required when automation targets the fx** — otherwise the target
   can't resolve.
8. **Automation grammar.** `tracks.<id>.gain_db|.pan`,
   `tracks.<id>.fx.<fx>.<param>`, `master.gain_db`,
   `master.fx.<fx>.<param>`; `<param>` must be a numeric field of that fx
   model. `curve`: `lin|exp|step` (`exp` requires non-zero same-sign
   values). `points` are `[bar, value]` pairs — fractional bars allowed,
   ≥2 points, non-decreasing (equal positions = instant jump), range
   `[1, length_bars+1]` where `length_bars+1` means "end of song"; value
   units are the parameter's own units.
9. **Step-string grammar.** `x`/`X` = 1.0, `o`/`O` = ghost 0.6,
   `1`-`9` = n/9, `.`/`-`/`_` = rest, spaces ignored. Length must equal
   `steps_per_bar * bars`; `bars` defaults to 1 (matches the spec's
   16-char example). `swing` ∈ [0,1] delays odd-indexed steps by
   `swing * half` a step (1 = triplet). The pattern tiles every `bars`
   bars; the last tile truncates silently.
10. **Track kinds.** Exactly one of `clips` / `pattern` /
    `instrument`+`midi` (the last two must appear together). `clips: []`
    counts as set — an empty clips track is legal.
11. **`meta.key` grammar.** `X[#|b]` + optional
    `m|min|maj|minor|major`, case-insensitive, normalized
    (`f#M` → `F#m`). `analysis.key` is separate:
    `{tonic, mode ∈ major|minor, confidence}`.
12. **`master`** keeps spec's `fx` + `target_lufs`; added `gain_db`
    (needed so master has an automatable param).

### Other contracts

13. **`schema_version` added to all contract files** (analysis,
    meta.json, metrics.json, candidate sets, muestra.toml), not just
    song.yaml — cheap future-proofing, default 0.
14. **metrics.json shape.** Designed concrete models for every spec-10
    bullet: fixed spectral bands 20–250/250–4k/4k–20k Hz (dB values),
    low-end masking band 40–150 Hz with per-track energies +
    `dominant_track` (kick/bass/sample identified by track id), per-track
    transient alignment, tonal estimate vs `meta.key` (`in_key` = tonic
    and mode match), click events with severity, optional CLAP/MERT
    `style_similarity`, and an `alarms` list (code/severity/message) —
    spec says metrics are alarms, so WP6 gets a place to put them.
15. **feedback event union** = the spec's three types **plus
    `chop_choice`** — section 3 step 4 requires logging manual chop
    overrides and no event type existed for it. Unknown `type` values are
    errors (strict union), not skipped.
16. **Chop candidates.** New contract `ChopCandidateSet` persisted to
    `cache/chops/<source_id>/<set_id>.json` (`set_id` = `csNNN`,
    allocated); previews under `cache/previews/<source_id>/<set_id>/`.
    The file carries the agent's `pick` (+justification) and a `chosen`
    record written by `chops.choose` — this is where the UI reads
    candidates (spec 3 step 4). Global candidate id =
    `"<set_id>/<candN>"`. `cache/chops/` is added to the 7.1 tree.
17. **`muestra.toml`** minimal fields only (spec 7.1): `sample_rate`
    (48000), `export_bit_depth` (24), `library` dirs, `separation`
    `{backend, model}`. New projects default `library = ["library"]` and
    get a `library/` dir. Written with `exclude_none` (TOML has no null).
18. **Structured error shape** = `Issue{path, code, message, hint,
    value}` used for every failure class; `MuestraError` carries a list;
    tool envelope `ToolResult{tool, ok, data, files, errors, hint}` —
    `files` are project-relative paths mirrored from each result model's
    own `files` field. Validation collects all issues.
19. **`song.validate(check_files)` flag** separates structural+semantic
    checks from filesystem checks so a song can be validated without
    audio present. Stems/analysis are only consulted in the fs layer;
    `instrument.preset`/`plugin.preset` are never file-checked (they may
    name plugin-internal presets).
20. **Tool conventions.** Every section-8 tool is `fn(params) ->
    result`; project-scoped params inherit `ProjectScoped` (`project`
    path field); results inherit `ToolOutput` (`files` list).
    `song.get`/`song.validate` live in `muestra.project` (the project
    module owns on-disk song.yaml; no `song` subpackage existed in the
    layout). `chops.choose` lives in `muestra.ingest` beside
    `source.find_chops` (it consumes the candidate sets ingest produces).
    `preview` lives in `muestra.render`. `chops.choose` gains a `mode`
    param (`auto|manual`, default `manual`) so manual picks can be
    logged to feedback.jsonl.
21. **`preview.target` grammar** (invented, spec only says "target"):
    `mix`, `chop:<id>`, `track:<id>`, `clip:<track_id>:<clip_index>`.
22. **Empty extras groups** `ingest/render/plugins/library/ears/server`
    pre-declared in pyproject + `[project.scripts] muestra` entry point
    pre-declared, so later WPs touch their own group and nobody fights
    over pyproject.
23. **Fixture audio** is synthesized (numpy+soundfile, seeded), ~0.6 MB:
    5.7 s source at 120 BPM, 11 beats from 0.25 s, downbeats at
    0.25/2.25/4.25 s — matching the fixture `analysis.json` exactly. The
    generator is committed (`fixtures/gen_audio.py`); numpy+soundfile
    live in the dev group only.

### Deviations / risks flagged for the orchestrator

- Spec 7.3 draft shows `repeat: 8` — with `length_beats: 8` and 16 bars
  that is exactly 64 beats (fits); our rule "no overrun" is compatible
  with the example.
- Spec 8 `chops.choose(candidate_id, adjust?)` — we added `mode` (see 20)
  and made `candidate_id` a `"<set>/<cand>"` pair (see 16); harmless
  superset.
- `project.open` reports `sources`/`versions`/`has_song`; spec doesn't
  specify a return shape — chosen as the natural "what's here" summary.
