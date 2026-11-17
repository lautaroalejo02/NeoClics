# Muestra contracts (as implemented, WP0.1)

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
- **NaN/inf rejected** in every contract (`allow_inf_nan=False` on
  `ContractModel`).
- **Timestamps** (`AwareDatetime`): timezone-aware ISO-8601 required in
  every contract file (`created_at`, feedback `ts`, …); naive input is a
  validation error with a hint. UTC recommended.
- **ids** (`IdStr`): `^[a-z][a-z0-9_-]{0,63}$` — lowercase slug, no `.`
  (they appear inside automation target paths). Windows reserved names
  (`con`, `aux`, `nul`, `prn`, `com1`-`com9`, `lpt1`-`lpt9`) are rejected
  so ids are safe as path components on every OS.
- **Relative paths** (`RelPath`): POSIX separators, no absolute paths, no
  `..`, no empty segments, no `:` (Windows drive/stream separator), no
  trailing dots or spaces, no Windows reserved names. When
  `check_files=True` each component is also compared **case-sensitively**
  against the directory listing so a project validated on Windows also
  works on case-sensitive filesystems.
- **File refs** (`FileRef`): either a `RelPath` or a `library://` URI.
- **`library://<path>`** resolves against `[library] roots` in
  `muestra.toml` (ordered; relative entries resolve from the project dir,
  `..` allowed there). Well-formedness is always checked; existence only
  when `check_files=True`.
- **Versions** (`VersionStr`): `v001`, `v002`, … allocated max+1 over
  git tags, `renders/` dir names and versions mentioned in
  `feedback.jsonl`; gaps are never reused.
- **Chop candidate ids** (`CandidateIdStr`): one global format
  `<set_id>/<candN>`, e.g. `cs001/cand2`.
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
  `out_of_range`, `grid_mismatch`, `duplicate_key`, `not_implemented`,
  `unknown_tool`, `internal_error`).
- Validation **collects all issues**; never stops at the first.
- Duplicate YAML mapping keys in `song.yaml` are a `duplicate_key` issue
  (with the key's path and a hint) — never silently dropped.

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
- **Registration is decentralised** (merge-safety): each subpackage owns
  a `TOOLS` list of `ToolSpec`s in `<pkg>/tools.py`;
  `muestra/registry.py` only collects them from a fixed list of
  subpackages. The registry test asserts a *superset* of spec section 8,
  not an exact set.
- `ToolSpec`: `name`, `fn`, `params`, `result`, plus `mcp_name`
  (dots → `_`, matches `^[a-zA-Z0-9_-]{1,64}$`), `readonly: bool`,
  `long_running: bool`, `cli: (group, action)` — `render` and `preview`
  are top-level CLI commands. WP7 generates MCP/CLI/API from this.
- Params of project-scoped tools start with `project: <dir>`.
- Result/params JSON Schemas: `schemas/tools/<name>.{params,result}.schema.json`.
- **Tools never rewrite `song.yaml` wholesale.** Programmatic edits use
  `muestra.project.edit_song_document` (ruamel.yaml round-trip), so
  comments, ordering and omitted defaults survive.
  `versions.restore` copies the version's `song.yaml` snapshot
  byte-for-byte.

## Project = its own git repo (R1)

`project.create` runs `git init`. Commits made by Muestra use the local
user identity when configured and fall back to
`Muestra <muestra@localhost>` (via `-c user.name/-c user.email` for that
commit only) when none exists.

- `.gitignore`: `renders/`, `cache/`, `.venv/`, `__pycache__/`. Git
  versions `song.yaml`, `midi/`, `muestra.toml`, `feedback.jsonl`,
  `docs/`; everything under `renders/` and `cache/` is derived data.
- **One version = one commit + one annotated tag `vNNN`.** Commit
  message: subject `vNNN: <summary>`, then trailers `Muestra-Request:`,
  `Muestra-Agent:`, `Muestra-Parent:` so metadata survives deleting
  `renders/`. `meta.git_commit` records that commit.
- `renders/vNNN/song.yaml` is a byte-for-byte snapshot of the song at
  that version; it is the source of truth for ears/diff/restore when
  present, `git show vNNN:song.yaml` is the fallback.
- `meta.json`/`metrics.json` are written *after* the commit and can
  always be regenerated.

In-process seams (`muestra.versions.core`, `muestra.ears.core`,
`muestra.render.core`, `muestra.plugins.core`, `muestra.library.core`):
`record_version`, `song_at`/`song_at_raw`, `next_version_id`,
`analyze`, `render_song`, `render_chop`, `render_instrument`,
`build_plugin_fx`, `resolve_plugin`, `lookup`, `resolve` — typed stubs
with fixed signatures; WP owners implement them.

WP2 integration ruling (2026-11-17):
`render_instrument(project, instrument, notes, sample_rate, n_frames, *, bpm: float)`
returns finite float32 stereo `(n_frames, 2)`. Notes remain in project
beats (beat 1 = song start); the host uses the explicit version's `bpm`.
`RenderResult.hint: str | None` carries a nonfatal note when ears metrics
or optional version-history metadata are unavailable. A stub ears analyzer
does not fail a completed audio render.

**Render orchestration** (the `render` tool, WP2): validate → allocate
version id → `record_version` (WP5) → render audio → write `meta.json`
→ `ears.analyze` (WP6) → write `metrics.json`.

## Project layout

```
<project>/
  .git/  .gitignore
  muestra.toml  song.yaml  feedback.jsonl
  sources/  midi/
  renders/vNNN/{mix.wav, song.yaml, stems/<track>.wav,
                stems/<track>.<lane>.wav, meta.json, metrics.json}
  library/                 # default library dir (decision)
  cache/stems/<src>/  cache/analysis/<src>.json  cache/previews/<src>/<set>/
  cache/chops/<src>/<set_id>.json
  docs/decisions.md
```

`muestra.project` provides `create_project`, `open_project`,
`load_config`/`save_config`, `resolve`/`resolve_library`,
`load_song`/`save_song`/`dump_song`, `load_analysis`/`save_analysis`,
`validate_song`, `chop_times`, `next_source_id`/`next_version_id`/
`next_set_id`, `allocate_id`, `edit_song_document`. No global state —
everything takes a path.

## `muestra.toml` → `ProjectConfig`

| field | type | default | notes |
|---|---|---|---|
| `schema_version` | 0 | 0 | |
| `sample_rate` | int | 48000 | internal render rate |
| `export_bit_depth` | 16\|24\|32 | 24 | WAV export |
| `library` | list[str] | — | legacy bare list of dirs |
| `library.roots` | list[str] | `["library"]` on create | ordered roots searched by `library://`; relative → project dir |
| `plugins.search_dirs` | list[str] | `[]` | extra plugin search dirs |
| `plugins.aliases` | table | `{}` | alias → `{windows?, macos?, linux?}` per-OS plugin path |
| `separation.backend` | str | `"audio-separator"` | |
| `separation.model` | str? | null | model name for the backend |

## `song.yaml` → `Song`

```yaml
schema_version: 0
meta: {title, bpm, key, length_bars, seed, time_signature}
sources: [{id, file}]
chops: [{id, source, stem, start_downbeat, start_offset_beats,
         length_beats, warp, fade_ms}]
tracks: [{id, at_bar, until_bar, clips|pattern|instrument(+midi|+notes),
          fx, gain_db, pan, mute}]
automation: [{target, curve, points}]
master: {fx, gain_db, target_lufs}
```

### meta
`bpm` (0,400), `length_bars` ≥1, `seed` int, `time_signature`
**Literal `"4/4"`** — v0 is 4/4 only (`BEATS_PER_BAR = 4`).

**`key` canonical form: `F#` (major) / `F#m` (minor).** Accepted spellings
(`muestra.music.parse_key`): `F#`, `F#maj`, `F#major`, bare `a` means A
*minor* (classical bare-name-is-minor convention), `Am`, `Amin`,
`A minor`; an uppercase `M` suffix means MAJOR. Values are normalized on
load to canonical form; enharmonics resolve to the spelling nearest the
input (`F#`/`Gb` kept as given, `A#`→`Bb`, `Db`→`C#`, …).

### chops
- `source` → `sources[].id`; `stem` ∈ `drums|bass|vocals|other|mix`
  (`mix` = the original file).
- `start_downbeat`: **1-based index** into `analysis.downbeats_s`;
  `start_s` is the entry of `beats_s` *nearest* that downbeat (one grid,
  not two).
- `start_offset_beats` (default 0, −64..64): counted along `beats_s`
  from the downbeat; may be negative or fractional (linear
  interpolation; extrapolates up to one grid step before the first
  beat, but `start_s` must stay ≥ 0).
- `length_beats`: source beats consumed; occupies exactly this many
  **project** beats after warping. `chop_times()` resolves
  `[start_s, end_s)` on the `beats_s` grid;
  `local_bpm = 60 * length_beats / (end_s - start_s)`.
- `warp.mode`:
  - `resample`: playback rate = `project_bpm / local_bpm` **only** —
    the chop always fills its grid slot exactly; the tempo-induced
    pitch change is the vinyl effect. `pitch_semitones` is then an
    additional **length-preserving** pitch shift.
  - `stretch`: beat-wise warp — each source beat is mapped onto the
    project grid (pitch-preserving time-stretch), then the same
    length-preserving `pitch_semitones` shift.
  - `pitch_semitones` ∈ −24..24; 0 = no shift.
- `fade_ms` (default 5): edge fades to prevent clicks.

### tracks — exactly one of
| kind | fields |
|---|---|
| clips | `clips: [Clip]` |
| pattern | `pattern: Pattern` |
| instrument | `instrument: Instrument` **and** exactly one of `midi: FileRef` or inline `notes` |

Every track also has `at_bar` (default 1) and `until_bar` (exclusive,
default `length_bars + 1`): the window in which the track sounds —
"drums enter at bar 5" is one field. `at_bar < until_bar ≤
length_bars + 1`. Plus `fx: [Fx]`, `gain_db` (−60..24), `pan` (−1..1),
`mute`.

### notes / midi (R4)
Inline `notes`: `[{beat, pitch, length_beats, velocity}]` — `beat`
1-indexed float from song start, `pitch` MIDI number 0-127 or note name
(`F#2`, `C4`; `C4` = 60), `length_beats` > 0, `velocity` 1-127 default
100.

MIDI files: ticks/ppq are **project beats at `meta.bpm`**; tick 0 = bar 1
beat 1; tempo and time-signature events inside the file are ignored.

### clips
`{chop | sample, length_beats?, warp?, at_bar, offset_beats, repeat}`

- `chop` (id ref) **xor** `sample` (FileRef). `length_beats`+`warp`
  belong to sample clips only (chop clips inherit from the chop).
- Sample clips: `length_beats` required (grid occupancy; audio truncated
  at the boundary, **not** looped — `repeat` tiles instances). `warp`
  requires a BPM for the sample: library entries with `bpm` may be
  warped like a chop; a sample with no BPM (one-shots, plain project
  paths) plays at native speed and asking for a warp is a validation
  error with a hint.
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
- `swing` ∈ [0,1]: odd (off) steps are delayed by
  `delay = swing * step / 3` (1 = full triplet swing).

### fx — discriminated union on `type`
`id` optional but **required when automation targets it**; unique per
track (and per master).

| type | params |
|---|---|
| `highpass`, `lowpass` | `cutoff_hz` 10..22000, `order` ∈ 2/4/6/8 (=4) |
| `sidechain` | `key_track` → track id (not self), `key_lane?` → lane of the key track's pattern, `depth` 0..1, `attack_ms` (1), `release_ms` (120) |
| `compressor` | `threshold_db`, `ratio` ≥1, `attack_ms` (10), `release_ms` (100), `knee_db` (6), `makeup_gain_db` |
| `limiter` | `ceiling_db` (−1), `release_ms` (50) |
| `gain` | `gain_db` −60..24 |
| `reverb` | `room_size`, `damping`, `wet_level`, `dry_level`, `width` |
| `delay` | `time_beats` (project beats), `feedback` <1, `mix` 0..1 |
| `chorus` | `rate_hz`, `depth` 0..1, `centre_delay_ms`, `feedback`, `mix` |
| `phaser` | `rate_hz`, `depth` 0..1, `centre_frequency_hz`, `feedback`, `mix` |
| `saturation` | `drive_db` 0..60 |
| `plugin` | `plugin` (name/alias → `[plugins]` config), `preset?`, `params: {name: float}` native ranges |

**Sidechain semantics (R8):** the key signal is EVENT-triggered when the
key track has events (pattern hits or notes): `gain = 1 - depth * env`,
`env` jumps to 1 at each trigger, `attack_ms` ramps to it and
`release_ms` decays back. `key_lane` restricts triggers to one drum lane
(must exist on the key track's pattern). If the key track is a clip
track (no events) the renderer falls back to an audio envelope follower
on that track's stem. Sidechain cycles are a validation error.

### automation
`target` grammar:
`tracks.<id>.gain_db | .pan | .fx.<fxid>.<param>`,
`tracks.<id>.fx.<fxid>.params.<name>` (plugin fx native params), and
`master.gain_db | master.fx.<fx>.<param>`.
`parse_automation_target()` implements it.

- `curve`: `lin` | `exp` (values must be non-zero, same-sign) | `step`.
- `points`: ≥2 `[bar, value]` pairs, non-decreasing positions (equal
  positions = instant jump), bars fractional allowed, in
  `[1, length_bars+1]`.
- **Semantics**: automation REPLACES the static value; the first value
  holds before the first point, the last value holds after the last.
- Two automations with the same `target` are rejected; values are
  range-checked against the target field's own bounds.

### master
`fx` (same union), `gain_db`, `target_lufs` (optional). **Master chain
order**: master fx in listed order → loudness gain toward `target_lufs`
→ final true-peak safety limiter.

### Validation layers
1. **Parse** (always): types, ranges, enums, uri/path shape, step chars,
   track-kind XOR, clip-source XOR, instrument `midi`-xor-`notes`,
   point order — all collected.
2. **`Song.check()`** (always): unique ids; chop→source, clip→chop,
   sidechain→track/key_lane refs; sidechain cycles; fx-id uniqueness;
   automation target resolution (exists + numeric + bounds) and
   duplicate targets; clip positions/overrun; pattern step lengths;
   `at_bar`/`until_bar` windows; exp-curve sign.
3. **`check_files=True`** (`muestra.project.validate_song`): referenced
   files exist **case-sensitively**; `cache/analysis/<src>.json` present
   for each chopped source; `start_downbeat` in range; chop grid fits
   (`chop_overrun`); sample-clip warp requires a library `bpm`;
   referenced stem exists in `stems`.

## `analysis.json` → `Analysis` (per source)

`source_id`, `file`, `duration_s`, `sample_rate`, `bpm_global`,
`tempo_variable`, `beats_s` (sorted, ≥1, within duration),
`downbeats_s` (sorted, ≥1), `key{tonic,mode∈major|minor,confidence}`,
`sections[{label,start_s,end_s}]` (non-overlapping, sorted),
`loudness_lufs`, `stems{drums?,bass?,vocals?,other?}` (≥1 present),
`separation_model`, `schema_version`.

## `renders/vNNN/` output format (R7)

- `mix.wav`: stereo, project `sample_rate`, 24-bit PCM, post-master.
- `song.yaml`: byte-for-byte copy of the version's song.
- `stems/<track_id>.wav`: stereo, project `sample_rate`, 32-bit float,
  POST track fx/fader/pan, PRE master chain; exactly the same frame
  count as the mix, sample-aligned. A muted track still gets a silent
  stem. Pattern tracks additionally write per-lane stems
  `stems/<track_id>.<lane>.wav` (pre track fx) for the metrics.
- Length: exactly `range_bars`; tails are cut with a 5 ms fade-out.
- `range_bars = [start, end)`: 1-indexed, end EXCLUSIVE, requires
  `start < end ≤ length_bars + 1`.
- `clip:<track>:<index>` preview targets use a **0-based** clip index.

## `renders/vNNN/meta.json` → `RenderMeta`

`version` (`vNNN`), `parent?`, `git_commit?`, `created_at` (aware),
`request?`, `agent`, `summary?`, `diff[{path,from,to}]` — **diff paths
are ID-based**, same grammar as automation targets
(`chops.c1.warp.pitch_semitones`, `tracks.sample.fx.lp.cutoff_hz`,
`tracks.drums.pattern.lanes.kick.steps`, `meta.bpm`); clips use
`tracks.<id>.clips[<i>]` (0-based), an fx without an id is `fx[<i>]`
(`from` is a JSON alias of `from_`), `render_time_s?`, `duration_s?`,
`sample_rate`, `bpm`, `key`, `length_bars`, `range_bars` ([start, end)),
`mix` (project-relative path), `tracks[{id, kind, stem, lanes:[{id, stem}]}]`
(ordered; so the UI never lists directories), `schema_version`.

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
`ts` is `AwareDatetime` on every event.

| type | extra fields |
|---|---|
| `rating` | `version`, `value` ∈ `like|dislike` |
| `comment` | `version?`, `at_s?`, `track?`, `text` |
| `ab_choice` | `a`, `b`, `winner` (must equal a or b) |
| `chop_choice` | `version?`, `set_id`, `candidate_id` (global `csNNN/candN`), `adjust?` |

`muestra.feedback.append_event` / `read_events` return a `ReadPage`:
`events`, `next_cursor` (line count, for resumable paging),
`skipped: [{path, line, message, hint}]` — malformed JSON lines and
invalid events are skipped and reported, never crash a read. `since`
filters `ts >= since` and must itself be timezone-aware.

## Chop candidates (`cache/chops/<src>/<set_id>.json`) → `ChopCandidateSet`

`set_id` (`csNNN`, allocated max+1), `source_id`, `created_at` (aware),
`query{section?,stem?,length_beats?,n}`, `candidates[]`,
`pick{candidate_id,justification}?` (the agent's `auto` pick),
`chosen{candidate_id,mode,adjust,at}?`.

`ChopCandidate`: `id` (**global** `csNNN/candN`), `source_id`, `stem`,
`start_downbeat`, `start_s`, `length_beats`, `end_s`, `local_bpm`,
`preview` (project-relative path under `cache/previews/<src>/<set_id>/`),
`score{value,reasons}`. `ChopAdjust` = `{start_beats, length_beats}`
deltas in beats.

## Library index → `LibraryIndex` (R4)

Each `[library]` root holds `.muestra-index.json`:
`{schema_version, entries: [{path (root-relative POSIX), bpm?, key?,
tags[]}]}` — `bpm`/`key` optional (one-shots have neither).
`muestra.library.core.lookup(project, uri) -> LibraryHit | None`
searches roots in order and returns `{root, path, entry, abs_path}`;
`resolve(project, uri) -> Path` raises `file_not_found`.

## HTTP API model (R11, `schemas/api.py`)

- **One project per server process**: `muestra serve --project <path>`;
  routes never carry the project path.
- Tool routes: `POST /tools/<mcp_name>` (`GET` allowed for
  `readonly` tools). Static audio under `/files/<project-relative path>`
  with HTTP Range support, restricted to `renders/` and
  `cache/previews/`.
- Jobs: `long_running` tools return `{job_id}` immediately;
  `GET /jobs/<id>` returns `Job{id, tool, status ∈ queued|running|done|failed,
  progress 0..1|null, message?, result?}`. Over MCP and CLI the same
  tools simply block.
- Events on websocket `/events`: `WsEvent{type, ts, data}` with
  `type ∈ job.updated | version.created | candidates.created |
  feedback.appended | song.changed`. The API server produces them by
  **watching the project folder** (`renders/`, `cache/chops/`,
  `feedback.jsonl`, `song.yaml`), because renders are usually triggered
  from a separate MCP process.

## Tools (spec 8)

Implemented for real: `project.create`, `project.open`, `song.get`,
`song.validate`, `feedback.read`, `feedback.append`,
`library.index`-backing `lookup`/`resolve`. All others are typed stubs
(`NotImplementedError` → `not_implemented` via `run_tool`) with their
params/result models fixed — see `src/muestra/<pkg>/tools.py` and
`schemas/tools/`.

| tool | owning module | notes |
|---|---|---|
| `project.create/open` | `muestra.project.tools` | real |
| `song.get/validate` | `muestra.project.tools` | real; `check_files` param |
| `feedback.read/append` | `muestra.feedback.tools` | real; cursor + skipped reporting |
| `source.import/separate/analyze/find_chops` | `muestra.ingest.tools` | WP1 |
| `chops.choose`, `chops.list` | `muestra.ingest.tools` | WP1; choose gains `chop_id` (writes that chop into song.yaml via ruamel) |
| `preview`, `render` | `muestra.render.tools` | WP2; top-level CLI; `preview.target` = `mix\|chop:<id>\|track:<id>\|clip:<track>:<i>` (0-based); `render` gains `request/summary/agent/version` |
| plugins hosting | `muestra.plugins` | WP3, no tools in spec 8 |
| `library.index/search` | `muestra.library.tools` | WP4 |
| `versions.list/diff/restore/get` | `muestra.versions.tools` | WP5; restore = byte copy of snapshot |
| `ears.analyze` | `muestra.ears.tools` | WP6 |
| API/MCP/CLI | `muestra.api|mcp|cli` | WP7, generated from `REGISTRY` |
