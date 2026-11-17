# Muestra contracts (as implemented, WP0)

This is the authoritative reference for the data contracts in spec
section 7 and the tool interface in section 8, **as actually
implemented**. The pydantic models under `src/muestra/schemas/` are the
source of truth; JSON Schemas are generated to `schemas/` (repo root) and
kept fresh by a test. Where the spec was ambiguous, the decision is here
and dated in `docs/decisions.md`.

## Conventions

- **Unknown fields are errors** everywhere (`extra="forbid"`, spec 7.3).
- **Positions are musical**: bars/beats are 1-indexed, never seconds
  (seconds only appear inside `analysis.json`, metrics, previews).
- **ids** (`IdStr`): `^[a-z][a-z0-9_-]{0,63}$` — lowercase slug, no `.`
  (they appear inside automation target paths).
- **File refs** (`FileRef`): either a project-relative POSIX path (`/`
  separators, no `..`, never absolute) or a `library://` URI.
- **`library://<path>`** resolves against `muestra.toml`'s `library`
  dirs in order (relative entries resolve from the project dir, `..`
  allowed there). Well-formedness is always checked; existence only when
  `check_files=True`.
- **Versions** (`VersionStr`): `v001`, `v002`, … allocated from
  `renders/` dir names.
- **`schema_version: 0`** on every contract file; bumping it is a
  contract change (note it in `docs/decisions.md`).

## Errors (shared shape, `muestra.errors`)

Every problem is an `Issue`:

```json
{"path": "tracks[1].fx[2].cutoff_hz", "code": "invalid_value",
 "message": "Input should be ...", "hint": "Use one of: ...",
 "value": "'bad'"}
```

- `path`: dotted/bracketed location; `""` = whole file.
- `code`: stable machine code (`unknown_field`, `missing_field`,
  `invalid_value`, `invalid_type`, `invalid_uri`, `ref_not_found`,
  `duplicate_id`, `automation_target`, `clip_overrun`, `chop_overrun`,
  `pattern_length`, `track_kind`, `clip_position`, `points_order`,
  `file_not_found`, `parse_error`, `too_small`/`too_large`,
  `out_of_range`, `grid_mismatch`, `not_implemented`, `unknown_tool`,
  `internal_error`).
- Validation **collects all issues**; never stops at the first.

## Tool envelope (spec 8)

`muestra.registry.run_tool(name, params_dict) -> ToolResult`:

```json
{"tool": "source.find_chops", "ok": true,
 "data": {...}, "files": ["cache/chops/src1/cs001.json"],
 "errors": [], "hint": null}
```

- `data`: the tool's result model serialized; `files`: project-relative
  POSIX paths of generated/updated files (mirrored from the result's own
  `files` field); `hint`: short agent-facing note on failure.
- Failures come back `ok: false` with all `Issue`s — tools raise
  `MuestraError`, never return partial data.
- `REGISTRY` (`muestra/registry.py`) maps every tool name to callable +
  params model + result model; WP7 generates MCP/CLI/API from it.
  Params of project-scoped tools start with `project: <dir>`.
- Result/params JSON Schemas: `schemas/tools/<name>.{params,result}.schema.json`.

## Project layout (spec 7.1 + WP0 additions)

```
<project>/
  muestra.toml  song.yaml  feedback.jsonl
  sources/  midi/  renders/vNNN/{mix.wav, stems/<track>.wav, meta.json, metrics.json}
  library/                 # default library dir (decision)
  cache/stems/<src>/  cache/analysis/<src>.json  cache/previews/<src>/<set>/
  cache/chops/<src>/<set_id>.json   # WP0 addition
  docs/decisions.md
```

`muestra.project` provides `create_project`, `open_project`,
`load_config`/`save_config`, `resolve`/`resolve_library`,
`load_song`/`save_song`/`dump_song`, `load_analysis`/`save_analysis`,
`validate_song`, `chop_times`, `next_source_id`/`next_version_id`/
`next_set_id`, `allocate_id`. No global state — everything takes a path.

## `muestra.toml` → `ProjectConfig`

| field | type | default | notes |
|---|---|---|---|
| `schema_version` | 0 | 0 | |
| `sample_rate` | int | 48000 | internal render rate |
| `export_bit_depth` | 16\|24\|32 | 24 | WAV export |
| `library` | list[str] | `["library"]` on create | dirs searched by `library://`; relative → from project dir, `..` ok |
| `separation.backend` | str | `"audio-separator"` | |
| `separation.model` | str? | null | model name for the backend |

## `song.yaml` → `Song`

```yaml
schema_version: 0
meta: {title, bpm, key, length_bars, seed, time_signature}
sources: [{id, file}]
chops: [{id, source, stem, start_downbeat, length_beats, warp, fade_ms}]
tracks: [{id, clips|pattern|instrument+midi, fx, gain_db, pan, mute}]
automation: [{target, curve, points}]
master: {fx, gain_db, target_lufs}
```

### meta
`bpm` (0,400), `key` accepts `X`, `Xm`, `Xmaj`, `Xmin`, `Xmajor`,
`Xminor` with `#`/`b` (normalized, e.g. `f#M`→`F#m`),
`length_bars` ≥1, `seed` int, `time_signature` **Literal `"4/4"`** — v0
is 4/4 only (`BEATS_PER_BAR = 4`); other meters need a schema bump.

### chops
- `source` → `sources[].id`; `stem` ∈ `drums|bass|vocals|other|mix`
  (`mix` = the original file).
- `start_downbeat`: **1-based index** into the source's
  `analysis.downbeats_s` (start time = `downbeats_s[n-1]`).
- `length_beats`: source beats consumed; the chop occupies exactly this
  many **project** beats on the grid after warping. `chop_times()`
  resolves `[start_s, end_s)` walking the `beats_s` grid (fractional
  tails interpolate linearly), giving
  `local_bpm = 60 * length_beats / (end_s - start_s)`.
- `warp.mode`: `resample` — rate = `project_bpm / local_bpm *
  2^(pitch/12)`; pitch rides with tempo and `pitch_semitones` also
  changes sounding length (audio is truncated/padded to the grid span).
  `stretch` — time-stretch to `length_beats` at project tempo preserving
  pitch (Rubber Band), then pitch-shift `pitch_semitones` (−24..24).
- `fade_ms` (default 5): edge fades to prevent clicks.

### tracks — exactly one of
| kind | fields |
|---|---|
| clips | `clips: [Clip]` |
| pattern | `pattern: Pattern` |
| instrument | `instrument: Instrument` **and** `midi: FileRef` (both or neither) |

plus `fx: [Fx]`, `gain_db` (−60..24), `pan` (−1..1), `mute`.

### clips
`{chop | sample, length_beats?, warp?, at_bar, offset_beats, repeat}`

- `chop` (id ref) **xor** `sample` (FileRef). `length_beats`+`warp`
  belong to sample clips only (chop clips inherit from the chop).
- Sample clips: `length_beats` required (grid occupancy; audio truncated
  at the boundary, **not** looped — `repeat` tiles instances); `warp`
  resolves source BPM via the library index at render time.
- Position = `(at_bar-1)*4 + offset_beats` project beats; `offset_beats`
  may be fractional/negative (start must stay ≥ 0). Each of `repeat`
  instances is `length_beats` long; **overrunning `length_bars` is a
  validation error** (`clip_overrun`). Clips on one track may overlap —
  they are summed.

### pattern
`{steps_per_bar: 16, bars: 1, lanes: {name: {sample, steps, gain_db, pan}}, swing}`

- `steps` length = `steps_per_bar * bars` (spaces ignored). Chars:
  `x`/`X` = hit vel 1.0, `o`/`O` = ghost 0.6, `1`-`9` = vel n/9,
  `.`/`-`/`_` = rest. `parse_steps()` returns the velocity list.
- The pattern **tiles every `bars` bars** across the song; the last tile
  truncates silently.
- `swing` ∈ [0,1]: odd-indexed steps delayed by `swing * half` a step
  (0 = straight, 1 = triplet swing).

### fx — discriminated union on `type`
`id` optional but **required when automation targets it**; unique per
track (and per master).

| type | params |
|---|---|
| `highpass`, `lowpass` | `cutoff_hz`, `order` ∈ 2/4/6/8 (=4) |
| `sidechain` | `key_track` → track id (not self), `depth` 0..1 (gain multiplier `1-depth`), `attack_ms` (5), `release_ms` (120) |
| `compressor` | `threshold_db`, `ratio`, `attack_ms` (10), `release_ms` (100), `knee_db` (6), `makeup_gain_db` |
| `limiter` | `ceiling_db` (−1), `release_ms` (50) |
| `gain` | `gain_db` |
| `reverb` | `room_size`, `damping`, `wet_level`, `dry_level`, `width` (pedalboard names; serial insert) |
| `delay` | `time_beats` (project beats), `feedback` <1, `mix` |
| `chorus` | `rate_hz`, `depth`, `centre_delay_ms`, `feedback`, `mix` |
| `phaser` | `rate_hz`, `depth`, `centre_frequency_hz`, `feedback`, `mix` |
| `saturation` | `drive_db` |
| `plugin` | `plugin` (VST3/AU name/path), `preset?`, `params: {name: float}` native ranges |

### automation
`target` grammar:
`tracks.<track>.gain_db | .pan | .fx.<fx>.<param>` and
`master.gain_db | master.fx.<fx>.<param>` (`param` = a *numeric* field
of that fx model). `parse_automation_target()` implements it.

`curve`: `lin` | `exp` (values must be non-zero, same-sign) | `step`.
`points`: ≥2 `[bar, value]` pairs, non-decreasing positions (equal
positions = instant jump), bars fractional allowed, in
`[1, length_bars+1]` (`length_bars+1` = end of song); value units are
the parameter's own units.

### master
`fx` (same union), `gain_db`, `target_lufs` (optional; renderer may
normalize to it).

### Validation layers
1. **Parse** (always): types, ranges, enums, uri/path shape, step chars,
   track-kind XOR, clip-source XOR, point order — all collected.
2. **`Song.check()`** (always): unique ids; chop→source, clip→chop,
   sidechain→track refs; fx-id uniqueness; automation target resolution
   (exists + numeric); clip positions/overrun; pattern step lengths;
   exp-curve sign.
3. **`check_files=True`** (`muestra.project.validate_song`): referenced
   files exist (sources, samples, midi, stems); `cache/analysis/<src>.json`
   present for each chopped source; `start_downbeat` in range; downbeat
   matches a beat within 50 ms (`BEAT_MATCH_TOLERANCE_S`); chop end fits
   the beat grid (`chop_overrun`); referenced stem exists in `stems`.

## `analysis.json` → `Analysis` (per source)

`source_id`, `file`, `duration_s`, `sample_rate`, `bpm_global`,
`tempo_variable`, `beats_s` (sorted, ≥1, within duration),
`downbeats_s` (sorted, ≥1), `key{tonic,mode∈major|minor,confidence}`,
`sections[{label,start_s,end_s}]` (non-overlapping, sorted),
`loudness_lufs`, `stems{drums?,bass?,vocals?,other?}` (≥1 present),
`separation_model`, `schema_version`.

## `renders/vNNN/meta.json` → `RenderMeta`

`version` (`vNNN`), `parent?`, `git_commit?`, `created_at`, `request?`,
`agent`, `summary?` (musical language), `diff[{path,from,to}]`
(song.yaml addressing; `from` is a JSON alias of `from_`),
`render_time_s?`, `duration_s?`, `schema_version`.

## `renders/vNNN/metrics.json` → `Metrics` (spec 10)

| field | shape | covers |
|---|---|---|
| `loudness` | `integrated_lufs, true_peak_dbtp, clipping, clipped_samples` | loudness/peak/clipping |
| `spectral` | `bands_hz` + `mix` + `tracks{}` of `{low_db,mid_db,high_db}`; fixed bands 20–250 / 250–4k / 4k–20k Hz | band balance mix+track |
| `low_end_masking` | `band_hz` (40–150), `energy_db{track}`, `dominant_track` | kick/bass/sample masking |
| `alignment` | `tracks{track: {mean_offset_ms,max_abs_offset_ms,n_transients}}`, `max_abs_offset_ms` | grid alignment |
| `tonal` | `estimated KeyEstimate?`, `target_key`, `tonic_match`, `mode_match`, `in_key` | key coherence |
| `clicks` | `count`, `events[{track,at_s,severity}]` | chop-edge clicks |
| `style_similarity?` | `model`, `references[{reference,score}]`, `mean_score` | optional CLAP/MERT |
| `alarms` | `[{code,severity∈info|warn|error,message,track?,at_s?}]` | computed heads-ups |

## `feedback.jsonl` → `FeedbackEvent` (spec 7.5)

One JSON object/line, discriminated on `type`. Unknown types are errors.

| type | extra fields |
|---|---|
| `rating` | `version`, `value` ∈ `like|dislike` |
| `comment` | `version?`, `at_s?`, `track?`, `text` |
| `ab_choice` | `a`, `b`, `winner` (must equal a or b) |
| `chop_choice` | `version?`, `set_id`, `candidate_id`, `adjust?` — manual override (mode dual) |

`muestra.feedback.append_event` / `read_events(since=ISO)`.

## Chop candidates (`cache/chops/<src>/<set_id>.json`) → `ChopCandidateSet`

`set_id` (`csNNN`), `source_id`, `created_at`, `query{section?,stem?,
length_beats?,n}`, `candidates[]`, `pick{candidate_id,justification}?`
(the agent's `auto` pick), `chosen{candidate_id,mode,adjust,at}?`.

`ChopCandidate`: `id` (`candN`), `source_id`, `stem`, `start_downbeat`,
`start_s`, `length_beats`, `end_s`, `local_bpm`,
`preview` (project-relative path under `cache/previews/<src>/<set_id>/`),
`score{value,reasons}`.

Global candidate id for `chops.choose`: `"<set_id>/<candN>"`
(`full_candidate_id`/`parse_candidate_id`). `ChopAdjust` =
`{start_beats, length_beats}` deltas in beats.

## Tools (spec 8)

Implemented for real in WP0: `project.create`, `project.open`,
`song.get`, `song.validate`, `feedback.read`. All others are typed stubs
(`NotImplementedError`) with their params/result models fixed — see
`src/muestra/<pkg>/tools.py` and `schemas/tools/`.

| tool | owning module | notes |
|---|---|---|
| `project.create/open` | `muestra.project.tools` | real |
| `song.get/validate` | `muestra.project.tools` | real; `check_files` param |
| `feedback.read` | `muestra.feedback.tools` | real; `since` ISO-8601 |
| `source.import/separate/analyze/find_chops`, `chops.choose` | `muestra.ingest.tools` | WP1 |
| `preview`, `render` | `muestra.render.tools` | WP2; `preview.target` = `mix\|chop:<id>\|track:<id>\|clip:<track>:<i>` |
| `plugins` hosting | `muestra.plugins` | WP3, no tools in spec 8 |
| `library.index/search` | `muestra.library.tools` | WP4 |
| `versions.list/diff/restore` | `muestra.versions.tools` | WP5 |
| `ears.analyze` | `muestra.ears.tools` | WP6 |
| API/MCP/CLI | `muestra.api|mcp|cli` | WP7, generated from `REGISTRY` |
