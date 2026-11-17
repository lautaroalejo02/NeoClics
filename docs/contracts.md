# Muestra contracts (as implemented, WP0.1)

WP15 drafts: `render(draft=true, range_bars?, analyze?)` publishes under
`renders/d-<12 hex>/` without a commit, tag or version allocation. Result
`version` is null and `draft_id` is the identifier. `analyze` defaults to false
for drafts, true for versions; `ears.analyze(version=<draft_id>)` computes
metrics later. `RenderMeta.version` and `Metrics.version` accept either id;
history and version tools still accept only `vNNN`. `draft` with `version`
is an error. Render result order is summary/hint/ids/paths then large metadata.
`GET /project` always has `drafts[]` and `previews[]`: entries carry
`id,path,kind,label,created_at,duration_s,range_bars?`. `preview` registers its
output too. New events: `draft.created` (one entry, upsert), `drafts.changed`
(`{}`, reload project, also emitted after audio/history files disappear).
LAN accepts Bearer, X-Muestra-Token, query and cookie tokens; `/` and
`/assets/*` are public so the UI can read the link and render its auth screen.

WP15 safe pruning: `RenderMeta.render_signature?` fingerprints the renderer
ABI/dependency versions, normalized song, effective source grids and referenced
audio/MIDI/explicit preset file contents. `versions.prune` keeps missing/mismatched
signatures, in addition to WP13 protections. Legacy audio is never silently
claimed bit-exact after engine/grid changes. Instrument hosting accepts optional
`seed`; renderer passes the snapshot's seed, including on re-render.

WP15 additions: `Analysis.reliability` contains
`confidence` (fraction of complete bars with consistent meter, not a probability),
`bars[{start_downbeat,start_s,end_s,beat_count,reliable,reason}]`, contiguous
`regions[{start_s,end_s,reliable,reason}]` including unverified intro/outro,
and `hint`. Missing maps in old caches are derived when needed.
`AnalysisSummary.reliability` omits `bars` (the detailed map stays on disk).
For valid audio without a detected pulse, `bpm_global` and the tool result's
`grid` are null, beat/downbeat arrays are empty and confidence is zero. This
is a reader-visible relaxation: consumers must handle no detected grid;
there is no fabricated fallback. `source.preview_grid` returns an explicitly
labelled audio-only audition in this case (`bpm=null`, `bars=0`).
`source.find_chops(allow_unreliable=false)` requires the entire candidate to
lie in reliable regions, including with `at_s`; override is explicit. The
default stem is `other` when available, else `mix`. Result adds `hint`.
`source.preview_grid(window_s=[start,end])` uses the full-file grid for that
exact window, without retracking. `chops.choose` and `song.validate` gain
nonblocking `warnings[]`; choice also adds `requested_chop_id`, `recorded`,
`rescaled` (boolean) and `notes[]` (agent follow-up). Analysis on the mix
requires no separation; separating later attaches stems without moving beats.

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
  `unknown_tool`, `internal_error`; versioning (WP5): `unknown_version`,
  `version_exists`, `not_git_repo`, `git_not_installed`, `repo_locked`,
  `repo_busy`, `detached_head`, `git_unsafe_repo`, `git_error`, see
  `docs/versions.md`); WP13: `render_failed`, `local_only`, `unauthorized`,
  `plugin_missing`/`preset_missing` (also from `song.validate`),
  `ffmpeg_missing` (only when a format really needs ffmpeg), `ffmpeg_unusable`,
  `decode_error` ("the file is damaged or is not audio"; replaces render's
  `audio_decode`)).
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
  `files` field); `hint`: short agent-facing note on failure, and (WP12) the
  result's own nonfatal `hint` on success (render, source.analyze,
  source.regrid, plugins.list...).
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

- `.gitignore`: `renders/`, `cache/`, `sources/` (WP11), `.venv/`, `__pycache__/`. Git
  versions `song.yaml`, `midi/`, `muestra.toml`, `ears.toml` (optional),
  `feedback.jsonl`, `docs/` (+ `.gitignore`, `.gitattributes`); restore
  rewinds only `song.yaml` and `midi/` (the two config files are
  configuration, not the arrangement); everything under `renders/`
  and `cache/` is derived data, and Muestra never commits `sources/` or
  `library/`. `.gitattributes` is `* -text` and every Muestra git call
  pins `core.autocrlf=false`: files are stored byte-for-byte whatever the
  user's git config. **Every text file Muestra writes uses LF on every OS**
  (WP11; it used to write CRLF on Windows).
- **One version = one commit + one annotated tag `vNNN`.** Commit
  message: subject `vNNN: <summary>` (bare `vNNN` without a summary), then
  trailers `Muestra-Version:`, `Muestra-Request:`, `Muestra-Agent:`,
  `Muestra-Parent:` (+ `Muestra-Summary:` when the one-line subject is not
  the verbatim summary) so metadata survives deleting `renders/`. Trailer
  text is one line with `\`, `
`, `
` escapes. A version commit is always
  created (empty if the song didn't change). `meta.git_commit` records
  that commit. Restore commits carry `Muestra-Restored-From:`; the parent of
  the next version is the newest version-or-restore commit in HEAD's
  history (`versions.core.suggest_parent`), so history is a tree.
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

**Render orchestration** (the `render` tool, WP2; order changed by WP13):
validate (incl. instruments) → render audio + `song.yaml` into
`renders/.staging/<token>/` → under `renders/.staging/version.lock`:
allocate version id → `record_version` (WP5) → write `meta.json` → move
the folder to `renders/vNNN` (one rename) → `ears.analyze` (WP6) → write
`metrics.json`. **A version never exists without its audio:** any failure
before the move undoes the commit and tag, deletes the staging folder and
returns `ok: false` with a hint ending "No version was created (no
commit, no tag, no renders/ folder)."; unexpected exceptions become code
`render_failed`. Leftovers of a killed process are rolled back at the
start of the next render and when `muestra serve` starts (rule:
`docs/versions.md`, "Atomic versions").

## Project layout

```
<project>/
  .git/  .gitignore
  muestra.toml  song.yaml  feedback.jsonl
  ears.toml                # optional (WP6): spectral reference + thresholds
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
| `library.roots` | list[str] | `["library"]` on create | ordered roots searched by `library://`; relative → project dir. `MUESTRA_LIBRARY` and `~/Documents/MuestraLibrary` are appended after them unless `roots = []` is written explicitly (see *Library discovery*) |
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
- `start_offset_beats` (default 0, −4096..4096): counted along `beats_s`
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
  - `formants` (WP13, optional bool): formant preservation for the pitch
    shift; default (null) = on for `stem: vocals`, off otherwise (sample
    clips: off). Measurements: `docs/render.md`.
- `fade_ms` (default 5): **seam crossfade length** (WP13). Each placement
  of the chop is rendered with `fade_ms` of extra source past its end (the
  record continuing, warped at the same rate); when the next placement on
  the same track starts on the exact frame where this one ends (the next
  `repeat`, or another clip), that tail fades out with an equal-power
  (sin/cos) crossfade while the next one fades in over the same length,
  starting at the boundary — no level dip. Only the first start and the
  last end of a contiguous run fade to/from silence (raised cosine,
  `fade_ms`). `fade_ms: 0` = hard cuts. Sample clips use 5 ms the same way.
  Placement stays sample-accurate; previews (`render_chop`, `preview
  chop:<id>`) are one slot faded at both ends.

### tracks — exactly one of
| kind | fields |
|---|---|
| clips | `clips: [Clip]` |
| pattern | `pattern: Pattern` |
| instrument | `instrument: Instrument` **and** exactly one of `midi: FileRef` or inline `notes` |

Instrument tracks (WP12) may also take `phrases: [PhrasePlacement]`
(with or without `notes`, never with `midi`); see *phrases* below.

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

### phrases (WP12)
Top level `phrases: [{id, length_bars (1..64), notes: [{beat, pitch,
length_beats, velocity}]}]` — `beat` is 1-indexed INSIDE the phrase and must be
< `length_bars`·4 + 1. On an instrument track, `phrases: [{phrase, at_bar?,
repeat?, transpose_semitones (−24..24, 0)}]`: `at_bar` defaults to the track's
`at_bar` and must lie in the track window; `repeat` = passes (an overrun of
`until_bar`/song end is `clip_overrun`); **omitted `repeat` loops the phrase
until the track window ends**, the last pass cut. Notes past the window end are
dropped; transposed pitches must stay 0-127 (`out_of_range`); unknown phrase =
`ref_not_found`. `Song.track_notes(track)` returns the expanded song-beat notes
(inline `notes` + phrases, sorted); the renderer and ears use it. Phrase ids are
unique (`duplicate_id`).

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
→ final true-peak safety limiter. WP13: gain → limiter → measure is
iterated to within 0.3 LU (≤ 4 passes) unless the limiter would remove
more than 3 LU; then the render `hint` says the target is too hot for the
material and how much the limiter takes off.

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
4. **`check_plugins`** (WP13; `song.validate` param, default = `check_files`):
   every instrument alias/plugin and preset, and every plugin fx, resolves on
   this machine. Codes `plugin_missing`, `plugin_ambiguous`, `preset_missing`,
   `preset_ambiguous`; paths are id-based
   (`tracks.bass.instrument.plugin`, `tracks.bass.instrument.preset`,
   `tracks.sample.fx.<fxid|[i]>.plugin`); a plugin hint lists the aliases
   available here inline ("Use one of the instruments available here:
   finger-bass, slap-bass, …").

## `analysis.json` → `Analysis` (per source)

`source_id`, `file`, `duration_s`, `sample_rate`, `bpm_global`,
`tempo_variable`, `beats_s` (sorted, ≥1, within duration),
`downbeats_s` (sorted, ≥1), `key{tonic,mode∈major|minor,confidence}`,
`sections[{label,start_s,end_s}]` (non-overlapping, sorted),
`loudness_lufs`, `stems{drums?,bass?,vocals?,other?}` (≥1 present),
`separation_model`, `schema_version`, and (WP12) `grid?`:

`GridCorrection{origin∈tracker|auto|manual, tempo_factor∈0.5|1|2,
beat_phase∈0|1, downbeat_shift_beats (−16..16), beats_per_bar (2..12, 4),
tempo_range_bpm, raw{bpm_global, tempo_variable, beats_s, downbeats_s},
tempo_confidence, bar_phase_confidence, ask_human, reason (Spanish),
alternatives[{tempo_factor, beat_phase, downbeat_shift_beats, bpm, label}],
history[{at, origin∈auto|manual|reset, tempo_factor, beat_phase,
downbeat_shift_beats, beats_per_bar}]}`. `beats_s`/`downbeats_s`/`bpm_global`
are always the EFFECTIVE grid (`raw` corrected by the parameters, absolute
w.r.t. the tracker); absent `grid` = the tracker grid as-is. Method and
numbers: `docs/ingest.md` ("Grid correction").

## `renders/vNNN/` output format (R7)

- `mix.wav`: stereo, project `sample_rate`, 24-bit PCM, post-master.
- `song.yaml`: byte-for-byte copy of the version's song.
- `stems/<track_id>.wav`: stereo, project `sample_rate`, 32-bit float,
  POST track fx/fader/pan, PRE master chain; exactly the same frame
  count as the mix, sample-aligned. A muted track still gets a silent
  stem. Pattern tracks additionally write per-lane stems
  `stems/<track_id>.<lane>.wav` (pre track fx) for the metrics.
- Length: exactly `range_bars`. A partial range ends with a 5 ms fade-out.
  **A full render is a loop** (WP13; the UI plays it seamlessly): the mix
  and stems are not faded at the song end; a clip track whose run reaches
  the song end and starts at bar 1 wraps (its last tail crossfades into
  bar 1; its effects are processed 100 ms past the end with automation
  holding its last value, and that continuation is crossfaded into the
  cold start, so an automated intro filter does not cut at the wrap);
  every other track is faded (5 ms) at the song end as before.
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
(`from` is a JSON alias of `from_`), added/removed elements are one entry
with `from`/`to` null; notes `tracks.<id>.notes[<i>]`, automations
`automation.<target>[.curve|.points]`, instrument `tracks.<id>.instrument.<f>`,
reorders `tracks` / `tracks.<id>.fx` (full grammar: `docs/versions.md`),
`render_time_s?`, `duration_s?`,
`sample_rate`, `bpm`, `key`, `length_bars`, `range_bars` ([start, end)),
`mix` (project-relative path), `tracks[{id, kind, stem, lanes:[{id, stem}]}]`
(ordered; so the UI never lists directories), `schema_version`.

## `renders/vNNN/metrics.json` → `Metrics` (spec 10)

| field | shape | covers |
|---|---|---|
| `loudness` | `integrated_lufs, true_peak_dbtp, clipping, clipped_samples` + (WP6) `sample_peak_dbfs, short_term_max_lufs, lra_lu, target_lufs, target_delta_lu, plr_db, crest_factor_db` | loudness/peak/clipping/dynamics |
| `spectral` | `bands_hz` + `mix` + `tracks{}` of `{low_db,mid_db,high_db}`; fixed bands 20–250 / 250–4k / 4k–20k Hz + (WP6) `detail_bands_hz` (7 nested bands), `mix_detail_db` (shares), `tracks_detail_db`, `reference`, `deviation_db` | band balance mix+track vs a reference tilt |
| `low_end_masking` | `band_hz` (40–150), `energy_db{track}`, `dominant_track` + (WP6) `kick`, `pairs[]`, `collisions[{at_s,bar,beat,track,kick_db,track_db,margin_db}]`, `sidechain[{track,depth,expected_dip_db,measured_dip_db,recovery_ms,ducking,…}]` | kick/bass/sample masking + sidechain audible |
| `alignment` | `tracks{track: {mean_offset_ms,max_abs_offset_ms,n_transients}}`, `max_abs_offset_ms` + (WP6) per track `basis, median_offset_ms, mad_ms, p90_abs_offset_ms, drift_ms, pulse_share, offbeat_offset_ms, histogram_ms, flagged[], clips[]` and `lanes{"<track>.<lane>"}` + (WP12, clip tracks) `verified, confidence, onsets_per_beat, window_spread_ms` | grid alignment |
| `tonal` | `estimated KeyEstimate?`, `target_key`, `tonic_match`, `mode_match`, `in_key` + (WP6) `compatible`, `source`, `tracks{}`, `detune[]`, `bass_vs_sample[]` | key coherence + detune |
| `clicks` | `count`, `events[{track,at_s,severity}]` + (WP6) `bar, beat, edge, clip, chop, ratio, level_db` per event | chop-edge / seam clicks |
| `style_similarity?` | `model`, `references[{reference,score}]`, `mean_score` | optional CLAP/MERT (hook only in v1) |
| `alarms` | `[{code,severity∈info|warn|error,message,track?,at_s?}]` + (WP6) `bar, beat, lane, clip, chop, value, threshold, unit, count, where[], fix` | computed heads-ups |
| `technical?` (WP6) | `dc_offset_db, stereo_correlation, low_band_correlation, low_band_mono_loss_db, tracks{peak/rms/dc/audible_share/muted}, silent_tracks, inaudible_tracks` | extras |
| `summary` (WP6) | compact Spanish text: 1 headline + ≤5 alarm lines | inline in tool results |
| `skipped`, `analysis_time_s`, `ears_version` (WP6) | checks that could not run (and why), timing, analyzer version | honesty/debug |

WP6 additions are all optional/defaulted (see decisions 2026-11-17 WP6).
`severity: error` is the "problem" level. `message` is one line of
Spanish in musical terms; `fix` is a concrete `song.yaml` change written
with the automation-target path grammar (`tracks.sample.fx.hp.cutoff_hz`).
Methods, thresholds and limits: `docs/ears.md`. Optional per-project
config: `ears.toml` (`[spectral]` reference profile, `[thresholds]`).
`MetricsDelta` (`ears.compare`): `lufs_delta`, `true_peak_delta_db`,
`plr_delta_db`, `loudness∈louder|quieter|same`, `low_delta_db`,
`high_delta_db`, `brightness∈brighter|darker|same`,
`low_end∈heavier|lighter|same`, `track_level_delta_db{}`,
`alarms_fixed|alarms_introduced|alarms_kept[{code,severity,track,message}]`,
`notes[]` — b relative to a; alarms match on `(code, track)`, info
excluded.

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
invalid events are skipped and reported, never crash a read. WP13: appends
are atomic across processes (OS lock + one write); a final line without
its `
` is not read (nor counted in `next_cursor`) until it is complete,
and an append after such a hand-edited line first adds the newline. `since`
filters `ts >= since` and must itself be timezone-aware.

## Chop candidates (`cache/chops/<src>/<set_id>.json`) → `ChopCandidateSet`

`set_id` (`csNNN`, allocated max+1), `source_id`, `created_at` (aware),
`query{section?,stem?,length_beats?,n,window_s?,at_s?}`, `candidates[]`,
`pick{candidate_id,justification}?` (the agent's `auto` pick),
`chosen{candidate_id,mode,adjust,at,chop_id?}?` (`chop_id`, WP13: the
song.yaml chop actually written).

`chops.choose` (WP13): a `manual` choice appends its `chop_choice` event
before anything else can fail. Result adds `chop_id` (written id),
`repeat_changes[{track, clip, old_repeat, new_repeat}]` (clips rescaled
to keep their span in beats when the length changed), `message` (Spanish,
for the human) and `agent_todo` (set when the choice had to be written
under a NEW id `<chop_id>-N` because the new length does not divide a
clip's span; the arrangement is then untouched).

`ChopCandidate`: `id` (**global** `csNNN/candN`), `source_id`, `stem`,
`start_downbeat`, `start_s`, `length_beats`, `end_s`, `local_bpm`,
`preview` (project-relative path under `cache/previews/<src>/<set_id>/`),
`score{value,reasons}`, and (WP13, optional) `bar` (= `start_downbeat`),
`start_mmss` (`"m:ss.s"`), `length_s`. The preview is two repeats
crossfaded at the seam and the wrap (plays as a seamless loop).

`source.find_chops` params (WP13 additions): `window_s: [start_s, end_s]`
(only candidates inside it), `at_s: float | "m:ss"` (candidate 1 = the
downbeat nearest it). Result adds `downbeats_s` (bar i of the record
starts at `downbeats_s[i-1]`). `ChopAdjust` = `{start_beats, length_beats}`
deltas in beats.

## Library index → `LibraryIndex` (R4, extended by WP4)

Each library root holds `.muestra-index.json` (written by `library.index`,
one entry per line; never inside the project):

```
{schema_version: 0, analyzer_version?, indexed_at?, generator?,
 kits: {<kit>: KitLicense{path, summary, restrictions[], source?}},
 problems: [{path, message}],          # unreadable files, never fatal
 entries: [LibraryIndexEntry]}
```

`LibraryIndexEntry` — only `path` (root-relative POSIX) is required, so a
hand-written index of `{path, type, tags}` still validates:

| field | meaning |
|---|---|
| `type` | `kick snare clap chat ohat hat tom cym rim perc drums fill bass keys synth guitar pad vox fx other` (`hat` = openness unknown, `drums` = multi-hit drum loop) |
| `subtype` | `conga cowbell crash ride sidestick snares-off roll …` |
| `kind` | `one-shot` \| `loop` (repeats on a grid) \| `phrase` (tempo-bound one-off: fills, rolls) |
| `kit` | folder under `kits/` (else the top-level folder) |
| `bpm`, `bpm_source` (`name`\|`detected`\|`name+detected`), `bpm_confidence` | loops/phrases only; one-shots carry no BPM (so they cannot be warped, R4) |
| `key`, `key_confidence` | canonical `F#m`/`F#` (`muestra.music`), tonal loops only |
| `duration_s sample_rate bit_depth channels peak_dbfs rms_dbfs loudness_lufs` | container + level facts (BS.1770 K-weighted LUFS) |
| `clipping` | flat runs of ≥4 full-scale samples |
| `features` | `AudioFeatures`: `fundamental_hz note attack_ms decay_ms spectral_centroid_hz sub low mid high air tonality` (band fields are energy shares 0..1) |
| `tags`, `flags` | descriptive tags; heads-ups (`clipping low-level type-mismatch:… bpm-disagreement:… …`) |
| `size mtime_ns sig` | incremental-index bookkeeping |

Thresholds, vocabularies and ranking: `docs/library.md`.
`muestra.library.core.lookup(project, uri) -> LibraryHit | None` searches
the roots in order and returns the `LibraryHit` (`uri, root, path, type,
subtype, kind, kit, bpm, key, duration_s, tags, flags, …`; an
un-indexed file that exists still answers, with no bpm);
`resolve(project, uri) -> Path` raises `file_not_found`.
`LibraryHit` serialises without null/empty/false fields (compact LLM
payloads): an absent key means the default.

**Starter kits** — `starter-kits.json` inside a root
(`schemas/starter-kits.schema.json`):
`{schema_version, note?, kits: {<name>: {description, lanes: {<lane>:
<root-relative path>}, notes?, patterns?: {<name>: {description, bars,
steps: {<lane>: <16·bars step chars>}}}}}}`; a template for the owner's library
ships in `muestra/library/data/`. `patterns` (WP12) must use the kit's lanes;
the curated kits have `basic`, `busy`, `fill` (+ `phrase` with a crash).

## Library discovery

`Project.library_dirs()` = `[library] roots` (project dir relative) →
`MUESTRA_LIBRARY` (OS path-separated list) → `~/Documents/MuestraLibrary`
if it exists and the env var is unset. An explicit `roots = []` in
`muestra.toml`, or `MUESTRA_LIBRARY=none` / empty, disables the
fallbacks. Implemented in `muestra.library.discovery`.

## HTTP API model (R11, `schemas/api.py`)

- **One project per server process**: `muestra serve --project <path>`;
  routes never carry the project path.
- Tool routes: `POST /tools/<mcp_name>` (`GET` allowed for
  `readonly` tools). Static audio under `/files/<project-relative path>`
  with HTTP Range support, restricted to audio under `renders/` and
  `cache/previews/` (`Cache-Control: no-cache` + ETag). JSON (meta,
  metrics, analyses) comes from `GET /project`, never from `/files`.
- `GET /project` (alias `/project/summary`): `{project_path, config, song,
  versions: [{version, meta, metrics, feedback}], candidate_sets,
  analyses: {<src>: Analysis}, grid_previews: {<src>: index.json of
  source.preview_grid} (WP12), feedback, feedback_cursor, feedback_skipped,
  issues}` — the one shape the UI boots from (WP11). Candidate-set fingerprint
  sidecars (`csNNN.meta.json`) are no longer reported as invalid sets.
- `GET /waveform?path=<audio>&points=N` (alias `/waveform/peaks`):
  `{path, sample_rate, frames, duration_s, points, channels: [{min, max}]}`.
- Tool status codes: success 200; expected tool failure 422 (500
  internal, 501 not implemented, 409 other project); the body is always
  the `ToolResult` envelope, so clients read `ok`, not the status.
- Jobs: `long_running` tools return `{job_id}` immediately;
  `GET /jobs/<id>` returns `Job{id, tool, status ∈ queued|running|done|failed,
  progress 0..1|null, message?, result?}`. Over MCP and CLI the same
  tools simply block.
- Events on websocket `/events`: `WsEvent{type, ts, data}` with
  `type ∈ job.updated | version.created | candidates.created |
  feedback.appended | song.changed | grid.changed` (WP12: `{source_id}` when an
  analysis or its grid click previews change). `version.created` data is
  `{version, meta, metrics}` and is re-sent (upsert) when `metrics.json`
  lands after `meta.json`; a websocket that connects receives
  `job.updated` for every queued/running job first. The API server produces them by
  **watching the project folder** (`renders/`, `cache/chops/`,
  `feedback.jsonl`, `song.yaml`), because renders are usually triggered
  from a separate MCP process.

## Tools (spec 8)

Implemented for real: `project.create`, `project.open`, `song.get`,
`song.validate`, `feedback.read`, `feedback.append`, `versions.list/get/diff/restore`
(WP5), `library.index`-backing `lookup`/`resolve`. All others are typed stubs
(`NotImplementedError` → `not_implemented` via `run_tool`) with their
params/result models fixed — see `src/muestra/<pkg>/tools.py` and
`schemas/tools/`.

| tool | owning module | notes |
|---|---|---|
| `project.create/open` | `muestra.project.tools` | real |
| `song.get/validate` | `muestra.project.tools` | real; `check_files` param |
| `feedback.read/append` | `muestra.feedback.tools` | real; cursor + skipped reporting |
| `source.import/separate/analyze/find_chops` | `muestra.ingest.tools` | WP1; WP12: `source.analyze` takes `min_bpm`/`max_bpm`, returns `analysis` as an `AnalysisSummary` (no beat lists; read the file) + `grid: GridReport` + `hint` |
| `source.regrid`, `source.preview_grid` | `muestra.ingest.grid_tools` (registered via `ingest.tools.TOOLS`) | WP12; see `docs/ingest.md` "Grid correction" |
| `chops.choose`, `chops.list` | `muestra.ingest.tools` | WP1; choose gains `chop_id` (writes that chop into song.yaml via ruamel) |
| `preview`, `render` | `muestra.render.tools` | WP2; top-level CLI; `preview.target` = `mix\|chop:<id>\|track:<id>\|clip:<track>:<i>` (0-based); `render` gains `request/summary/agent/version`; its result carries `metrics_summary` (compact ears text) + `metrics_path`, never the full metrics (WP11) |
| plugins hosting; `plugins.list/scan` | `muestra.plugins` | WP3; inventory tools requested as an additive extension, models and TOOLS in plugins/tools.py. WP12: `plugins.list` default = aliases + plugins + `preset_counts` + `categories`; presets only with `query`/`plugin`/`category` (`limit` 30, cap 100, `offset`, `matched`, `next_offset`); `detail` adds paths/parameter names |
| `library.index/search/kits/info` | `muestra.library.tools` | WP4, real. WP12: `library.kits` takes `pattern=<name>` and returns the kit's `patterns{name: description}`. `library.index` is `long_running`; `search`/`kits`/`info` are `readonly`. New error codes: `no_library`, `library_not_indexed` (hint says to run `library.index`) |
| `versions.prune` | `muestra.versions.tools` | WP13: dry run by default; `{dry_run, remove[{version,bytes}], keep[{version,bytes,reasons}], freed_bytes, cache_files, cache_freed_bytes, disk, hint}`; rules in `docs/versions.md` "Disk use". `versions.list` adds `disk` and per-record `size_bytes`; `GET /project` adds `disk` |
| `versions.list/diff/restore/get` | `muestra.versions.tools` | WP5, real (`docs/versions.md`); WP12: `versions.get` returns `metrics_summary` + `metrics_path` by default, the full `metrics` and `song_yaml` only with `detail=true`; restore = byte copy of snapshot, saves uncommitted work first; `versions.diff` adds `changes`, `metrics_delta` (ears notes), `metrics_compare` (ears `MetricsDelta`), `identical`; `versions.list` returns merged git+renders+feedback records and the parent tree |
| `ears.analyze`, `ears.compare` | `muestra.ears.tools` | WP6; real. analyze writes metrics.json and returns `summary` (WP12: `metrics` only with `detail=true`); compare returns a `MetricsDelta`. Seams: `muestra.ears.core.analyze/write_metrics/compare` |
| API/MCP/CLI | `muestra.api|mcp|cli` | WP7, generated from `REGISTRY` |
