# Decisions

## 2026-11-17 — WP3: explicit instrument tempo (orchestrator ruling)

`plugins.core.render_instrument` gains required keyword-only `bpm: float`:
`render_instrument(project, instrument, notes, sample_rate, n_frames, *, bpm)`.
The renderer passes `meta.bpm`; beat 1 maps to frame zero. This removes
the stub's ambiguity about who converts beats to frames while preserving
the float32 `(n_frames, 2)` output and project-beat MIDI rule.

WP3 also implements the explicitly requested additive `plugins.list` and
`plugins.scan` tools, with their params/results and `TOOLS` list owned by
`muestra.plugins.tools`. Existing song/config/error model shapes are unchanged.
The integrator must collect the plugins package in the registry and regenerate
shared tool schemas (those files are outside WP3 ownership). SFZ seed selection
reads project/song.yaml because the approved seam exposes only explicit BPM,
not seed; historical renders must keep that project seed consistent.

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

## 2026-11-17 — WP0.1: orchestrator contract-review rulings (R1–R20)

Each entry is one ruling; where it supersedes an earlier WP0 decision it
says so.

1. **R1 — git-backed versions (supersedes WP0-16's implicit "renders/ is
   truth" assumption).** A project is its own git repo; `project.create`
   runs `git init`. `.gitignore` covers `renders/`, `cache/`, `.venv/`,
   `__pycache__` — git versions `song.yaml`, `midi/`, `muestra.toml`,
   `feedback.jsonl`, `docs/` only. One version = one commit + annotated
   tag `vNNN`, commit subject `vNNN: <summary>` with trailers
   `Muestra-Request:`/`Muestra-Agent:`/`Muestra-Parent:`; commits made by
   Muestra fall back to `Muestra <muestra@localhost>` only when no user
   identity is configured (per-commit `-c`, never mutating config).
   `renders/vNNN/song.yaml` is a byte-for-byte snapshot and the source of
   truth for ears/diff/restore (`git show vNNN:song.yaml` fallback).
   `RenderParams` gains `request|summary|agent|version` (re-render an
   existing version). Typed stubs: `versions.core.record_version`,
   `song_at`/`song_at_raw`, `next_version_id`; `ears.core.analyze`;
   `render.core.render_song`. The `render` tool orchestrates:
   validate → allocate id → record_version → render audio → meta.json
   → ears.analyze → metrics.json.
2. **R2 — ids never reused.** `next_version_id` = max over git tags,
   `renders/` dirs and versions mentioned in `feedback.jsonl`, +1.
   `next_set_id` follows the same max+1 rule over `cache/chops/`. Gaps
   are never filled.
3. **R3 — warp semantics (supersedes WP0-4).** `resample` rate =
   `project_bpm / local_bpm` only — the chop always fills its grid slot;
   the tempo pitch shift is the vinyl effect. `pitch_semitones` is an
   additional **length-preserving** shift in both modes (after
   beat-wise stretch in `stretch`). The fixture chop with
   `pitch_semitones: 1` now lands exactly on the grid.
4. **R4 — seams + MIDI.** MIDI ticks/ppq are project beats at
   `meta.bpm`; tick 0 = bar 1 beat 1; file tempo/meter events ignored.
   Instrument tracks take exactly one of `midi: <path>` or inline
   `notes`. Stubs: `plugins.core.render_instrument` (float32
   (n_frames,2)), `build_plugin_fx` (pedalboard-compatible),
   `resolve_plugin`; `render.core.render_chop` (shared by previews:
   project tempo when a song exists, else source tempo);
   `library.core.lookup`/`resolve` (implemented for real).
   `muestra.toml` gains `[plugins]` (`search_dirs`, per-OS `aliases`)
   and `[library].roots`; the index `.muestra-index.json` lives inside
   each root. Samples with a known `bpm` may warp like chops; warping a
   BPM-less sample is a validation error.
5. **R5 — chop starts + ids.** `Chop.start_offset_beats` (default 0)
   counts along `beats_s` from the downbeat; negative/fractional
   interpolate. `ChopsChooseParams.chop_id` names the chop written into
   `song.yaml`. Candidate ids are the global `csNNN/candN` everywhere —
   `ChopCandidate.id` itself carries the global form (supersedes
   WP0-16's local `candN`).
6. **R6 — timestamps + robust reads.** Every contract timestamp is
   `AwareDatetime`; naive input is a validation error with a hint.
   `feedback.read` never crashes on a bad line: malformed JSON/invalid
   events are skipped and reported (`{path, line, message, hint}`).
   Cursor paging: `read(since?, cursor?)` returns `next_cursor` (line
   count); `since` is `ts >= since`.
7. **R7 — render output format.** `mix.wav` stereo 24-bit PCM at
   `sample_rate`, post-master. Stems stereo float32, post track
   fx/fader/pan, pre master, frame-aligned with the mix; muted tracks
   get silent stems; pattern tracks also write per-lane
   `stems/<track>.<lane>.wav` (pre track fx). Master order: master fx in
   order → loudness gain toward `target_lufs` → true-peak safety
   limiter. Render is exactly `range_bars` = `[start, end)` (end
   exclusive, `start < end ≤ length_bars+1`), tails cut with 5 ms
   fade-out. `clip:` preview index is 0-based. `RenderMeta` gains
   `sample_rate`, `bpm`, `key`, `length_bars`, `range_bars`, `mix`,
   `tracks[{id,kind,stem,lanes[{id,stem}]}]`. Fixture `renders/v001/`
   regenerated to match (2-bar render, ~2.4 MB).
8. **R8 — sidechain.** Event-triggered when the key track has events:
   `gain = 1 - depth * env`, env jumps to 1 per trigger with
   `attack_ms` (default 1) ramp and `release_ms` decay; `key_lane`
   restricts triggers to one drum lane (validated). Clip-track keys fall
   back to an audio envelope follower. Sidechain cycles are validation
   errors.
9. **R9 — merge-safety.** pyproject groups are multi-line arrays
   separated by a comment naming the owning WP; shared audio deps moved
   to base (numpy 2.5.5, scipy 1.18.1, soundfile 0.14.0, pedalboard
   0.9.26, ruamel.yaml 0.19.1 — verified `uv sync` + import on Windows).
   Tool registration is decentralised (`TOOLS` per subpackage; registry
   collects from a fixed subpackage list; test asserts superset).
   `CONTRIBUTING-AGENTS.md` states the ownership rules.
10. **R10 — diff paths are ID-based**, the automation grammar:
    `chops.c1.warp.pitch_semitones`, `tracks.<id>.fx.<fxid>.<param>`,
    `tracks.drums.pattern.lanes.kick.steps`, `meta.bpm`; clips use
    `tracks.<id>.clips[<i>]`, id-less fx use `fx[<i>]`. Documented on
    `RenderMeta.diff`.
11. **R11 — registry + API model.** `ToolSpec` gains `mcp_name`,
    `readonly`, `long_running`, `cli` (render/preview are top-level CLI).
    New tools: `feedback.append` (implemented), `chops.list`,
    `versions.get` (stubs). API: one project per server process
    (`muestra serve --project <path>`); `POST /tools/<mcp_name>` (+GET
    for readonly); `/files/<path>` Range-served under `renders/` and
    `cache/previews/`; `long_running` tools return `{job_id}` and
    `GET /jobs/<id>` returns the `Job` model; `/events` websocket emits
    `WsEvent{type, ts, data}` (`job.updated`, `version.created`,
    `candidates.created`, `feedback.appended`, `song.changed`) produced
    by watching the project folder. Models in `schemas/api.py`; the
    server itself is WP7.
12. **R12 — automation.** `ContractModel` sets `allow_inf_nan=False`
    globally. Automated values are range-checked against the target
    field's own bounds; duplicate targets rejected; plugin params
    addressable via `.params.<name>`; automation replaces the static
    value (first/last value held outside the point range). ge/le bounds
    added to fx params that lacked them (cutoff 10..22000, depth 0..1,
    ratio ≥1, etc.).
13. **R13 — swing (supersedes WP0-9).** `delay = swing * step / 3`
    applied to odd (off) steps; `swing` in [0,1], 1 = full triplet
    swing. WP0-9's `swing * half` description was equivalent at swing=⅔
    but the ruling's formula is now normative.
14. **R14 — canonical keys (supersedes WP0-11).** Canonical form `F#`
    (major) / `F#m` (minor); `a` means A minor; uppercase `M` suffix =
    MAJOR. Shared `muestra.music.parse_key(str) -> (pitch_class, mode)`
    for WP4/WP6, plus `parse_note` for MIDI note names.
15. **R15 — round-trip edits.** All programmatic song.yaml edits go
    through `project.edit_song_document` (ruamel round-trip): comments,
    ordering and omitted defaults survive. `versions.restore` copies the
    snapshot byte-for-byte.
16. **R16 — duplicate YAML keys** are a `duplicate_key` issue with path
    and hint, never silently dropped (ruamel `DuplicateKeyError` mapped
    into an Issue).
17. **R17 — placeholder messages.** `PydanticCustomError` templates
    can't use `!r`; all custom error messages rewritten. A test scans
    every issue message from invalid fixtures for `{`/`!r`.
18. **R18 — portable paths.** `RelPath` rejects `:`, `..`, empty
    segments, absolute paths, trailing dots/spaces and Windows reserved
    names; ids reject the same reserved names. `check_files` compares
    each path component's exact case against directory listings.
19. **R19 — chop start.** `start_s` is the `beats_s` entry nearest the
    downbeat (one grid). `stretch` maps each source beat onto the
    project grid (beat-wise); `resample` uses one rate for the whole
        chop. Documented in contracts.md.
20. **R20 — track windows.** Pattern and instrument tracks (and clips
    tracks uniformly) gain `at_bar` (default 1) / `until_bar`
    (exclusive, default `length_bars+1`): "drums enter at bar 5" is one
    field.

### Flags for the orchestrator

- **R13 vs WP0-9**: WP0 documented `swing * half` a step as "triplet
  swing"; the ruling's `swing * step / 3` is now implemented — at
  swing=1 a step lands at the second triplet position, which matches the
  ruling's intent.
- **R20 scope**: the ruling names pattern and instrument tracks; the
  fields were put on all track kinds uniformly (clips included) for a
  single code path — superset, harmless.
- **R7 vs file size**: the v001 render fixture was kept at a 2-bar
  length with synthesized stems (~2.4 MB committed audio, under the
  ~4 MB budget); no shortening of the song itself was needed.
- **R1 scaffold commit**: `project.create` makes an initial "scaffold"
  commit so `record_version` always has a HEAD; versions start at v001.
- **R11 events**: `schemas/api.py` defines the models only; the
  file-watching producer is WP7's API server (no WP-owned module exists
  for it yet).

## 2026-11-17 — WP1: candidate IDs and implementation ambiguities

User-authorized correction: `ChopCandidate.id` now uses `CandidateIdStr`,
matching existing R5 and contracts.md (global `csNNN/candN`). The old local
`candN` validator and fixture contradicted that ruling. Regenerated affected
JSON schemas and migrated the example candidate IDs. No new wire format or
contract change; existing binding R5 is implemented.

WP1 allocates candidate set IDs over all sources (including interrupted
preview directories): the shared `next_set_id` is source-local, while the
public candidate ID has no source component. Without that extra scan,
`chops.choose(cs001/cand1)` would be ambiguous across two sources.

Other differences between prose and shared models remain unchanged:
`Analysis.stems` is optional in the Python schema, so analysis can precede
separation; `ChopChoiceEvent` has no `set_id` field, so feedback records the
global candidate ID, from which the set is recoverable. `Chop.start_offset_beats`
allows ±4096 in the model (prose says ±64); WP1 follows the shared model and
checks actual grid bounds. These are flagged for integration, not silently
patched in another WP's schema.

The explicit WP1 request to compare 1/2/4/8-bar candidates supersedes the stub's
description of a fixed default of eight beats; `length_beats=None` searches
4/8/16/32 beats. The parameter/result shapes are unchanged. Algorithm choices,
licences, CPU measurements and remaining limitations are in `docs/ingest.md`.

## 2026-11-17 — WP2: approved integration seam corrections

The orchestrator explicitly approved these two corrections during WP2:

- `RenderResult` gains optional `hint`, so an unavailable ears analyzer
  can return successful rendered audio with an actionable note. The tool
  envelope contains this note in `data.hint` (the shared registry is unchanged).
- `plugins.core.render_instrument` gains required keyword-only `bpm: float`.
  Notes stay in project beats; the plugin host converts them using the
  rendered version's tempo. Reading live song.yaml would use the wrong
  tempo when re-rendering a tagged version.

The renderer uses the approved signature; WP3 owns its implementation.
Only its seam stub is updated here. The render result JSON Schema is
regenerated. No other schema or song semantics change.

Implementation choices where the contract does not specify a value:

- Equal-power -3 dB centre pan, no channel crossfeed, at both lane and track stages.
- Master gain follows master inserts, before loudness normalization. Final
  safety uses the minimum static declared master limiter ceiling, default -1 dBFS.
- Full-song effect history and loudness measurement precede range cropping;
  ranges use the difference between independently rounded absolute boundaries.
- With `stems=False`, metadata has no track/lane references to nonexistent WAVs.
- Track windows gate the absolute note/clip timeline rather than moving notes.
  Pattern tiling begins at the track window's start.
- Standalone chop candidate previews without song.yaml use the existing
  render_chop seam; ID-based preview targets require the song's definitions.
- If prior meta.json is gone, the version-history API exposes no historical
  request/agent trailers; re-render parameters can supply them, otherwise
  agent defaults to muestra. Existing metadata retains request/agent.

Fixture inconsistency flagged for integration: analysis spans 5.7 seconds,
but shared stem placeholders are 1.2 seconds. Renderer reports chop_overrun;
tests/benchmarks extend only disposable copies using existing fixture audio.
Details, quality settings, measurements and limitations are in docs/render.md.

## 2026-11-17 — WP5: versioning + musical diff

Details and behaviour: `docs/versions.md`. Contract-visible decisions:

1. **Version commits are plumbing commits on a private index** and contain
   only `song.yaml`, `midi/`, `muestra.toml`, `feedback.jsonl`, `docs/`,
   `.gitignore`, `.gitattributes`. `sources/` audio, `library/`, `renders/`
   and `cache/` are never committed by Muestra (`project/core.py`'s docstring
   said `sources/`/`library/` "stay tracked"; they don't — big binaries
   never belong in version commits). The user's staging area, hooks and
   `commit.gpgsign` can't interfere.
2. **Re-render of an unchanged song = an empty commit** (`allow_empty`
   semantics): the version is the (metadata, tag), not the diff.
3. **Extra trailers** beyond R1's three: `Muestra-Version: vNNN` (marker that
   makes a commit a version commit, so a hand-written commit titled "v100 …"
   is never mistaken for one), `Muestra-Summary:` (only when the one-line
   subject isn't verbatim), and on restore commits `Muestra-Restored-From:`.
   Free text uses a reversible one-line escape (`\`, `
`, `
`); the
   subject is `vNNN: <summary>` or bare `vNNN`.
4. **Parent = history, not a file.** `parent=None` in `record_version` means
   "derive": the newest version-or-restore commit in HEAD's first-parent
   history. `versions.restore v001` then `render` yields `Muestra-Parent:
   v001` (a tree), and a manual `git reset --hard v001` behaves the same.
   (An earlier draft kept a `.git/muestra-base` marker file; rejected — local,
   lost on clone, can disagree with history.) The `render` tool should pass
   `parent=None` and may read the result with `commit_info(...).parent`.
5. **Line endings.** `project.create` writes via text mode → CRLF on Windows;
   Git for Windows defaults to `core.autocrlf=true` (LF in the blob). To keep
   `git show vNNN:song.yaml` byte-identical to the file, every Muestra git call
   pins `core.autocrlf=false` and new projects get `.gitattributes`
   (`* -text`). This adds a file to the project layout.
6. **Restore never loses work**: uncommitted versioned files are committed as
   an untagged snapshot first; the restore itself is a commit
   (`Muestra-Restored-From`); the result gives a ready `git restore
   --source=<sha> -- song.yaml [midi]` undo. `versions.restore` result
   `version` and `restored_from` are both the restored version (the stub
   didn't distinguish them); new optional fields `changed`,
   `snapshot_commit`, `previous_commit`, `restore_commit`, `undo`, `hint`,
   `warnings`, `snapshot_source`.
7. **Diff path grammar additions** (R10 left these open): automations are
   keyed by target (`automation.<target>[.curve|.points]`), notes
   `tracks.<id>.notes[<i>]`, instrument fields `tracks.<id>.instrument.<f>`;
   added/removed elements are one entry with `from`/`to` null; indices are
   0-based in the new version (old version for removed elements); only
   audible/visible reorders are reported (`tracks`, `tracks.<id>.fx`,
   `master.fx`). Clips/notes/id-less fx are aligned by similarity, not
   position.
8. **Tool result extensions** (additive): `VersionInfo` gains `request`,
   `agent`, `children`, `feedback{…}`, `rendered`, `rerenderable`, `has_meta`,
   `has_metrics`, `has_stems`, `is_base`, `warnings`; `versions.list` gains
   `roots`, `base`; `versions.get` gains `song_source`, `info`, `metrics`,
   `mix`, `stems`; `versions.diff` gains `changes`, `metrics_delta`,
   `identical`. `DiffEntry`s inside tool results are serialised as `from`
   (via `DiffItem`/`RenderMetaOut` in `versions.models`), because the tool
   envelope dumps without `by_alias`. *Flag:* `DiffEntry` itself should get
   `serialize_by_alias` upstream; until then other tools that return
   `RenderMeta` through the envelope will emit `from_`.
9. **New error codes** (see `docs/contracts.md`): `unknown_version`,
   `version_exists`, `not_git_repo`, `git_not_installed` (the seam previously
   used `git_error` for a missing git), `repo_locked`, `repo_busy`,
   `detached_head`, `git_unsafe_repo`.
10. **`next_version_id` reads `feedback.jsonl` structurally** (only
    `version`/`a`/`b`/`winner`) and also counts version commits in history,
    so a comment containing "v2026" can't bump ids and a deleted tag can't
    cause reuse.
11. **Metrics delta**: uses `muestra.ears.core.compare(a, b)` if it exists and
    returns phrases; otherwise computed locally from the two `metrics.json`.

## 2026-11-17 — WP4: sample library (indexing, search, starter kits)

Contract changes (every one is a superset of the WP0/R4 shape; a minimal
hand-written index `{path, type, tags}` still validates — the fixture
index is unchanged):

1. **`LibraryIndexEntry` grows** `subtype`, `kind`, `kit`, `bpm_source`,
   `bpm_confidence`, `key_confidence`, `duration_s`, `sample_rate`,
   `bit_depth`, `channels`, `peak_dbfs`, `rms_dbfs`, `loudness_lufs`,
   `clipping`, `features` (`AudioFeatures`), `flags`, and the incremental
   bookkeeping `size`/`mtime_ns`/`sig`. `LibraryIndex` grows
   `analyzer_version`, `indexed_at`, `generator`, `kits` (per-kit
   `KitLicense`) and `problems`. Reason: an agent that cannot hear picks a
   kick from these numbers; documented in `docs/library.md`.
2. **Type vocabulary.** `type` is the instrument family:
   `kick snare clap chat ohat hat tom cym rim perc drums fill bass keys
   synth guitar pad vox fx other` (`ohat`/`chat` = open/closed hat, `hat` =
   openness unknown, `cym` = crash/ride/china/splash/gong with the kind in
   `subtype`). The spec's "loop" is a **`kind`**, not a type: a loop is
   `kind: loop` with `type` saying what it is (`drums` for multi-hit drum
   loops, `perc`, `pad`, `bass`, …). `kind` has THREE values —
   `one-shot | loop | phrase`; `phrase` (drum fills, rolls) is tempo-bound
   but must not be tiled, so it carries a `bpm` and may be warped like a
   loop. One-shots never carry a `bpm`.
3. **`LibraryHit` grows** the same descriptive fields plus `score`,
   `reasons`, `summary`, `features`, `license`, and serialises **without
   null/empty/false fields** (search results go into an LLM context; an
   absent key means the default). `lookup`/`resolve` keep their R4
   signatures; `lookup` returns the `LibraryHit` (contracts.md used to say
   `{root, path, entry, abs_path}`, which never matched the code — the code
   wins and contracts.md now says so).
4. **New contract `StarterKits`** (`starter-kits.json` inside a library
   root; `schemas/starter-kits.schema.json`, added to
   `muestra/schemas/export.py`): `{kits: {name: {description, lanes:
   {lane: root-relative path}, notes}}}`. A template for the owner's
   library ships as package data (`muestra/library/data/starter-kits.json`)
   and is the fallback when a root has no manifest of its own.
5. **Tools.** `library.index` params: `path` is now optional (omit = every
   configured root) and gains `force`, `add_root` (add a new `path` to
   `muestra.toml`), `workers`; result: `index_path` is a plain string (the
   index lives outside the project, so it can never be a project-relative
   `RelPath`), plus `indexed` (total entries), `roots[]` (per-root counts,
   timing, per-type/kit stats, flagged/clipping/type-mismatch counts,
   problems), `registered`, `hint`. `library.search` params gain `subtype`,
   `kind`, `kit`, `tags`, `duration_min/max`, `similar_to`,
   `bpm_tolerance`, `half_double`, `key_exact`, `clean_only`, `detail`
   (`compact` default | `full`), `offset`; default `limit` is now 10; result
   gains `total`, `interpreted`, `searched_roots`, `unindexed_roots`,
   `hint`. **New tools** `library.kits` (starter kits + ready-to-paste
   pattern) and `library.info` (roots, index status, counts, licences).
   New error codes `no_library` and `library_not_indexed`; their hint says
   to run `library.index`. The tool functions accept an optional
   `progress(done, total, rel)` callback for WP7's job progress.
6. **Library discovery** (`muestra.library.discovery`) and the one allowed
   edit outside the package: `Project.library_dirs()` (`project/core.py`,
   four lines) now returns `[library] roots`, then `MUESTRA_LIBRARY`, then
   `~/Documents/MuestraLibrary` (only if it exists and the env var is
   unset). An explicit `roots = []`, or `MUESTRA_LIBRARY=none`/empty, is the
   opt-out — that is what keeps `test_no_library_configured` hermetic on a
   machine that has the default library. New projects still get
   `roots = ["library"]` (their own empty dir); the fallbacks are appended
   at run time rather than written into `muestra.toml`, so a project stays
   portable between machines.
7. **What is indexed.** Audio files (`.wav .flac .aif .aiff .ogg .mp3`)
   under the root, skipping directories that start with `_` or `.` and a
   top-level `instruments/` (instrument banks belong to the plugin host,
   WP3, not to the drum/loop library). Kit = the folder under `kits/` (else
   the top-level folder); licence = that kit's `LICENSE.txt`.
8. **Incremental rule.** An entry is reused when size and mtime are
   unchanged, or when only the mtime changed and the cheap content hash
   (size + first/last 64 KiB) matches. `analyzer_version` (currently 2) is
   bumped whenever measuring/classifying changes, which forces a full
   re-measure. Unreadable files go into `problems` and are retried on the
   next run.
9. **Clipping** = ≥4 consecutive full-scale samples that are flat (within
   2 LSB). A plain "peak ≥ -0.001 FS" test (REPORT.md's) flags every
   normalised kick, because a sine peak is full-scale for a few samples.
10. **Names win, audio verifies.** `type` comes from file/folder names;
    when the measured spectrum clearly contradicts it the file gets a
    `type-mismatch:name=…,audio=…` flag (never silently retyped). Files
    whose names say nothing get a conservative audio-based type flagged
    `type-inferred:…`. `bpm` for loops comes from the name when present,
    cross-checked by onset-autocorrelation detection (half/double time
    accepted); disagreement is flagged `bpm-disagreement:…` and the name
    wins with low confidence.

### Flags for the orchestrator

- **Key detection is a heuristic** (Krumhansl-Schmuckler on a chromagram)
  and was validated only on synthetic triads; on the real pad/arp/bass loops
  many keys come back with low confidence (`low-confidence-key`). The
  trailing `-a/-c/-e` letters in `french-house-loops/instruments` file
  names are NOT used as keys: their meaning is unknown and the audio does
  not consistently agree with them.
- **The curated starter kits were never listened to** (stated in the
  manifest, in `library.kits` output and in `docs/library.md`).
- `library.search` treats `bpm`/`key` as hard filters, so they only match
  loops/phrases; the result hint says so when a filtered search is empty.

## 2026-11-17 — WP6: ears (objective audio metrics)

Contract changes are **additive only**: every pre-existing field of
`metrics.json` keeps its name, type and meaning, and every new field has
a default, so older files still validate. Methods, thresholds and
detection limits are in `docs/ears.md`.

1. **metrics.json extended** (`src/muestra/schemas/metrics.py`):
   - `Loudness` + `sample_peak_dbfs`, `short_term_max_lufs`, `lra_lu`,
     `target_lufs`, `target_delta_lu`, `plr_db`, `crest_factor_db`.
     `clipping` now means "flat-topped full-scale runs", not "any sample
     at full scale"; `clipped_samples` still counts samples >= 0.9999.
   - `SpectralReport` + seven nested detail bands (`DETAIL_BAND_EDGES_HZ`:
     sub/bass/low_mid/mid/high_mid/presence/air, nesting exactly inside
     the three fixed bands), `mix_detail_db` (share of total power),
     `tracks_detail_db`, the `reference` used and `deviation_db`.
     The fixed three bands are unchanged.
   - `LowEndMasking` + `kick`, per-track `pairs`, per-hit `collisions`
     (bar/beat) and `sidechain` (measured dip depth + recovery per
     sidechain fx).
   - `TrackAlignment` + `basis`, `median_offset_ms`, `mad_ms`,
     `p90_abs_offset_ms`, `drift_ms`, `pulse_share`,
     `offbeat_offset_ms`, `histogram_ms`, `flagged`, per-instance
     `clips`; `AlignmentReport` + `lanes` (keyed `<track>.<lane>`).
   - `TonalReport` + `compatible`, `source`, per-track `tracks`,
     `detune` (expected shift from snapshot + analysis vs measured
     tuning), `bass_vs_sample`.
   - `ClickEvent` + `bar`, `beat`, `edge`, `clip`, `chop`, `ratio`,
     `level_db`.
   - New `technical` (DC, stereo/low-band correlation, mono loss,
     per-track levels, silent/inaudible tracks), `summary` (compact
     Spanish text for inline use), `skipped`, `analysis_time_s`,
     `ears_version`.
   - `Alarm` + `bar`, `beat`, `lane`, `clip`, `chop`, `value`,
     `threshold`, `unit`, `count`, `where[]`, `fix`. `message` is a
     one-line Spanish explanation; `fix` a concrete song.yaml change
     using the automation-target path grammar.
   - New `MetricsDelta` / `AlarmRef` for `ears.compare`.
2. **Severity vocabulary kept** as `info | warn | error` (no rename);
   `error` is the "problem" level of the WP6 brief.
3. **New optional project file `ears.toml`** (project root,
   git-versioned): `[spectral]` picks or defines the reference tilt,
   `[thresholds]` overrides any `muestra.ears.config.Thresholds` field.
   Unknown keys are errors. `muestra.toml` was not touched (its model
   forbids unknown tables).
4. **Tools:** `ears.analyze` result gains `summary`; new readonly-ish
   `ears.compare(version_a, version_b, reanalyze=False)` returning a
   `MetricsDelta` (it writes a missing `metrics.json`, which is derived
   data). Seam for WP5/UI: `muestra.ears.core.compare(project, a, b)`.
   **Suggestion for WP2:** the `render` tool should return
   `metrics.summary` inline (a handful of lines) rather than the full
   `Metrics` model, which is large.
5. **No new dependency.** BS.1770-4/EBU 3342 loudness is implemented
   in `muestra.ears.dsp` and verified against the EBU Tech 3341/3342
   reference signals; the `ears` dependency group stays empty (the
   earlier partial work had added pyloudnorm; removed).
6. **Sidechain verification needs the chop's source stem** in
   `cache/stems/` (+ `analysis.json`): the stem is compared with the
   un-processed chop mapped through its warp. Without it the check is
   skipped and reported in `metrics.skipped` (no guess, no alarm).
7. **Timeline assumptions** where the contract is silent (to confirm
   with WP2): a pattern's `bars`-long tiles are anchored at the track's
   `at_bar`; clip instances outside the track's `at_bar`/`until_bar`
   window are silent; a render always starts on a bar line
   (`range_bars` are whole bars).
8. **Shared test touched:** `tests/test_registry.py::test_run_tool_not_implemented`
   used `ears.analyze` as its example stub; it now swaps a stub into the
   registry with `monkeypatch`, so it no longer depends on which WP has
   shipped.
9. **Style similarity** (CLAP/MERT) is a documented hook only
   (`muestra.ears.style.register`); `style_similarity` stays `null`.

## 2026-11-17 — WP11: integration pass (seams found running the real product)

Contract-visible changes (all additive or bug fixes; schemas regenerated):

1. **LF everywhere.** Every text file Muestra writes (`song.yaml`,
   `muestra.toml`, `.gitignore`, `feedback.jsonl`, `meta.json`,
   `metrics.json`, analysis/candidate JSON, schema exports, agent
   configs) is written with `newline="\n"`. `project.create` used to write
   CRLF on Windows (contracts.md said so); the exporter produced CRLF
   schema files.
2. **`DiffEntry` serialises `from` in every dump** (`serialize_by_alias`),
   so `meta.diff` reads the same through `run_tool`, MCP, HTTP and disk.
3. **`render` parentage.** The tool passes `parent=None` to
   `record_version` (versions derives it, honouring `versions.restore`)
   and reads parent/commit back with `commit_info`; it used to force the
   newest version as parent, so restore v001 → render gave parent v002.
   Re-rendering an existing version (`version=`) never commits; parent,
   commit, request, summary and agent come from the version tag's
   trailers (they survive deleting `renders/`), `created_at` is kept
   (prior meta, else the commit time in UTC). The meta diff is
   `song_diff(parent, song)` instead of a full `versions.list` +
   `versions.diff` per render.
4. **`RenderResult.metrics` → `metrics_summary`** (the compact Spanish
   ears text) + `metrics_path`. A render lands in the agent's context on
   every iteration; the full report stays in `metrics.json`. The MCP text
   content of a render is a short report (version, parent, changes,
   ears summary); structured content keeps the envelope. An ears crash no
   longer fails a completed render (hint instead).
5. **`versions.diff` uses ears** (`ears.report.compare_metrics`):
   `metrics_delta` = its Spanish notes, new `metrics_compare` = the
   structured `MetricsDelta`; WP5's local description is only a fallback.
6. **HTTP API**: `/project` gains `analyses{src: Analysis}` (the UI's
   chop panel), `/files` stays audio-only and sends `Cache-Control:
   no-cache`; `version.created` carries `metrics` and is re-sent when
   `metrics.json` lands; a websocket that connects is first sent
   `job.updated` for every queued/running job. The UI was adapted to the
   real `/project`, `/waveform` and `versions.get` shapes (ui/API-NEEDS.md).
7. **Progress convention.** `library.index`, `source.separate` and
   `source.analyze` take the transports' `progress(fraction, message)`
   (library.index used to call it with `(done, total, rel)` and crashed
   with `internal_error` over HTTP/MCP). The CLI prints long-tool progress
   on stderr (stdout stays one JSON document).
8. **Dependency groups.** A plain `uv sync` installs everything except
   stem separation: server (FastAPI, uvicorn, MCP, portalocker) and beat
   tracking (beat-this) moved to base dependencies. New extra
   `separation` (demucs-infer, audio-separator); `ingest` is kept as its
   alias and the other WP extras stay (empty) so old commands work.
   `httpx` moved to the dev group. `muestra doctor` reports each missing
   piece with the one command that fixes it.
9. **`ears.toml` is versioned** (committed with every version, like
   `muestra.toml`; restore leaves both alone).
10. **ears on real renders** (details and numbers: docs/ears.md,
    "Validation on real renderer output"): the timeline assumptions match
    the renderer; instrument note onsets out of silence are no longer
    clicks (`click_onset_rise_db = 12`); the `loudness_off_target` fix
    names the safety-limiter cause; sample `grid_offset` on dense live
    records is documented as unreliable (thresholds unchanged).
11. **Skill findings 1-3 (ears, after merging WP9)**: `loudness_off_target` no longer
    suggests `master.gain_db` (normalised away); a weak tuning measurement overrides the
    A440 prediction of `sample_detuned` (never creates one); `track_transposed` at ±5/±7 or
    contradicted by a fitting bass/sample pair is `info`. The skill's `ears.md`/`workflow.md`
    were updated to the new behaviour and to `render` returning `metrics_summary`.
    Thresholds unchanged. Details: docs/ears.md.

## 2026-11-17 — WP12: polish pass after the first real usage sessions

All contract changes are additive unless marked **(breaking for readers of
that field)**; schemas regenerated (`uv run python -m muestra.schemas.export`).

1. **Beat-grid correction lives in the analysis.** `Analysis.grid:
   GridCorrection` keeps the tracker output (`raw`) and the correction
   (`tempo_factor` 0.5/1/2, `beat_phase`, `downbeat_shift_beats`,
   `beats_per_bar`, origin, confidences, `ask_human`, Spanish `reason`,
   `alternatives`, `history`); `beats_s`/`downbeats_s`/`bpm_global` stay the
   effective grid, so chop timing, warp, previews, ears and validation did not
   change. Parameters are absolute w.r.t. the tracker (idempotent, reversible).
   Rejected: a per-chop `source_beats_per_beat` (skill finding 5) — "the
   tracker counted eighths" is a property of the source, and per-chop factors
   would leave find_chops, previews and ears on the wrong grid.
2. **`source.analyze`** gains `min_bpm`/`max_bpm` (default 70-160) and picks
   the octave inside the range. Its result's `analysis` is now an
   `AnalysisSummary` (no `beats_s`/`downbeats_s`, adds `n_beats`/`n_downbeats`)
   **(breaking for readers of `data.analysis.beats_s`: read the file)**, plus
   `grid: GridReport` and `hint`. Pre-WP12 caches are upgraded without moving
   the chops of an existing song (report only).
3. **New tools** `source.regrid` (rewrites the source's song chops to cut the
   same audio, reports every move, retires candidate sets to
   `cache/chops/<src>/stale/`) and `source.preview_grid` (click previews under
   `cache/previews/<src>/grid/`, `index.json`). `GET /project` gains
   `grid_previews`; new WsEvent `grid.changed {source_id}`; the UI's Chops tab
   shows each source's grid and an "Escuchar la grilla" player. The summary no
   longer reports `csNNN.meta.json` sidecars as invalid candidate sets.
4. **Phrases.** Top-level `phrases` + `tracks[].phrases` placements
   (`phrase`, `at_bar?`, `repeat?` = loop to the window end when omitted,
   `transpose_semitones`); `notes` may be combined with phrases, `midi` not.
   `Song.track_notes()` is the single expansion used by renderer and ears.
   Diff paths `phrases.<id>...`, `tracks.<id>.phrases[i]`; Spanish sentences
   for placements and for "rewrote as phrases (suena igual)". A 12-bar bass in
   the Tiger Rag example: 53 lines / 3023 bytes -> 15 lines / 483 bytes.
5. **Compact results.** `plugins.list` default = aliases + plugins + preset
   counts/categories; presets only when browsing (`query`, `plugin`,
   `category`, `limit` 30, hard cap 100, `offset`/`next_offset`, `detail`);
   `PresetEntry.path` optional, `category` added, `PluginEntry.n_parameters`
   added. `versions.get` / `ears.analyze` take `detail` and return the summary
   + `metrics_path` by default; `VersionsGetResult.song_yaml` and
   `EarsAnalyzeResult.metrics` are now optional **(breaking for readers that
   expected them without `detail=true`; the UI passes it)**. The envelope
   mirrors a successful result's own `hint`.
6. **Starter kits** gain named `patterns` (`KitPattern{description, bars,
   steps}`); `library.kits` takes `pattern=<name>` and lists `patterns`. The
   fallback steps no longer include crash/percussion, and `steps_per_bar`
   other than 16 keeps musical time (32 used to double the pattern's speed).
   The owner's `MuestraLibrary/starter-kits.json` was updated to match the
   bundled template.
7. **ears.** Clip-track timing self-tests (±`timing_probe_ms` = 20 ms, max
   miss `timing_max_window_spread_ms` = 5 ms) and, when it fails or the
   cluster is too scattered, raises `info grid_unverified` instead of
   `grid_offset`/`grid_drift`/`clip_offset`; `TrackAlignment` gains `verified`,
   `confidence`, `onsets_per_beat`, `window_spread_ms`. Legato note starts are
   scanned once and compared with the track's own attacks
   (`click_attack_margin_db` = 6): slap-bass retriggers are no longer clicks.
8. **Wording.** Lane nouns no longer repeat their track's noun ("Bajé el bombo
   2 dB"); track `sample` reads "el sample".
9. **Startup.** Tool modules import heavy dependencies lazily; contract models
   use `defer_build`; module TypeAdapters are lazy (`LazyAdapter`); the CLI sets
   `PYDANTIC_DISABLE_PLUGINS` unless already set; `load_song` parses with
   libyaml (YAML 1.2 core scalars, duplicate keys rejected, any error falls
   back to ruamel for the message); ruamel round-trip edits keep long flow
   lines (`width` 4096); `git rev-parse` answers are cached per `.git` identity.

## 2026-11-17 — WP13: audit fixes (backend)

1. **Atomic versions.** `render` renders audio into `renders/.staging/<token>/`
   first and only then allocates the id, commits, tags, writes `meta.json`
   and renames the folder to `renders/vNNN` (under
   `renders/.staging/version.lock`). Any failure rolls back commit + tag;
   killed renders are rolled back by `staging.recover` at the next render
   and at `muestra serve` start (rule in docs/versions.md "Atomic
   versions"). A rolled-back id may be reused (no audio of it ever
   existed); published ids never are. Error hints end with "No version was
   created"; unexpected exceptions are `render_failed`. Why: the auditor's
   `plugin: funky-bass` left a tagged ghost v00N with no audio that became
   the next render's parent.
2. **`song.validate` checks instruments** (`check_plugins`, default =
   `check_files`): id-based paths `tracks.<id>.instrument.plugin|preset`,
   hint lists the available aliases inline (never "run plugins.list").
3. **Manual chop choice is never lost.** `chops.choose` writes the
   `chop_choice` event first (spec 3 step 4). A length change keeps each
   clip's span by rescaling `repeat`; when that is not a whole number the
   choice becomes a new chop `<id>-N` and the result's `agent_todo` says
   what to wire in (rewriting the arrangement for the human was judged
   riskier than one explicit agent step). Messages for the human are
   Spanish.
4. **Seams crossfade; a full render is a loop.** Each chop/sample
   placement carries a tail of the record past its end; contiguous
   placements are joined by an equal-power crossfade starting at the
   boundary (so the grid position and the downbeat stay where they were,
   with the same `fade_ms` fade-in the old code had), fades to silence only
   at run edges. `fade_ms` now means crossfade length. A full render's end
   is not faded; wrapping clip tracks crossfade into bar 1 and get their
   effects warmed 100 ms past the end. Why: the old per-repeat fade to zero
   measured −30 to −88 dB dips at every repeat (an audible hiccup every 1–2
   bars) and the UI loops renders. `CACHE_ABI` bumped (`render-4`).
5. **Chops by time.** `source.find_chops` gains `window_s` and `at_s`
   ("m:ss" accepted); candidates carry `bar`, `start_mmss`, `length_s`;
   the result carries the record's `downbeats_s` bar map. `at_s` skips the
   grid-regularity and section filters: the human asked for that spot.
6. **LAN mode needs a token and is listen-only.** A non-loopback bind
   prints a per-start random token in the phone URL; non-loopback clients
   need it (query once → HttpOnly cookie, or `X-Muestra-Token`) and may
   only use read-only tools + `feedback.append` + `chops.choose`;
   loopback clients are unchanged. A loopback bind checks the Host header
   (DNS rebinding). The UI needs no change (same-origin cookie). Why: any
   LAN client could render, restore, import any local file and write index
   files anywhere.
7. **Loudness is iterated, bounded by a limiter budget.** ≤ 4 passes,
   0.3 LU tolerance, ≤ 3 LU removed by the safety limiter (beyond that the
   mix is being crushed: stop and say so in the render hint rather than
   hit the number). `render_audio`/`render_song` take an optional `report`
   dict (additive) that `master()` fills.
8. **One ffmpeg resolver** (`muestra.media`): override, `MUESTRA_FFMPEG`,
   PATH, known install locations; `decode_error` vs `ffmpeg_missing` vs
   `ffmpeg_unusable`. Render's `audio_decode` code became `decode_error`.
9. **Feedback appends lock across processes** (`cache/locks/feedback-append.lock`,
   separate from the transports' `feedback.lock` so an in-process tool call holding
   that one can still append); readers ignore an unterminated last line.
10. **`versions.prune` + cache GC; stem format unchanged.** Pruning deletes
    whole `renders/vNNN/` folders (history keeps the version; re-render is
    bit-exact); "unread feedback" = feedback newer than the newest version.
    Stems stay 32-bit float for now: 24-bit PCM with a stored headroom gain or
    FLAC would cut ~25–60 % but the UI decodes and sums stems itself, so the
    format change needs UI work (and drum-lane stems, half of each render, are
    only read by ears: they could move to the cache). Not done here.
11. **Curated aliases are level-matched** to −24 LUFS on a reference phrase by
    a per-alias trim (default patch only). Existing songs using them change
    level once (finger-bass +10.3 dB, rhodes −8.8 dB, …).
12. **Formant preservation only for vocals by default** (`warp.formants`
    overrides). Measured: it keeps the long-term envelope but adds 0.5–1.9 dB
    of non-reversible colouring on instruments/drums (0.3–0.6 dB without).
    `CACHE_ABI` → `render-5`. Pitch-shifted non-vocal chops in existing songs
    sound slightly different after this change.

## 2026-11-17 — WP15: partial grid reliability and mix-first analysis

- Meter inconsistency no longer rejects a recording. `Analysis.reliability`
  adds per-bar evidence, contiguous time regions, an overall reliable-bar
  fraction (heuristic, not musical beat accuracy), and an actionable `hint`.
  Native irregular downbeats are preserved. Rebuilt half/double grids require
  support in raw tracker bars; local intervals may vary by at most a factor
  of 1.4 within a reliable bar. Intro/outro fragments remain unverified.
- `source.find_chops(allow_unreliable=false)` filters the whole candidate,
  including with `at_s`. Without stems the default is `mix`. Time-window
  operations reuse the full analysis. Standalone imported files have no
  inferred parent; use `window_s`/`at_s` on the original instead of cutting
  and retracking an excerpt.
- `source.preview_grid(window_s=[start,end])` auditions an exact window.
  `chops.choose` and `song.validate` return nonblocking reliability warnings.
  Choice adds `requested_chop_id`, `rescaled`, `recorded`, and `notes` for UI
  compatibility alongside the WP13 fields.
- Analyze the unseparated mix first. Separation attaches stems to that cached
  analysis without changing its grid or needing another inference pass.

### WP15 — drafts and transport reconciliation

`render(draft=true)` uses the same staged renderer and a random `d-<12 hex>`
render id, but never allocates a version or commits/tags. Its `version` result
is null; `draft_id` identifies the audio and snapshot. Ears is opt-in for drafts
(`analyze=true`, or later `ears.analyze(version=<draft_id>)`). Existing version
renders retain automatic ears. Metadata/metrics accept either id; history
contracts remain version-only. `/project` lists drafts and ordinary previews;
disk watching emits `draft.created`/`drafts.changed`, including deletions.
Public static UI assets and Bearer-header acceptance reconcile the WP14 client
with LAN authentication. API/audio/write restrictions remain authenticated.
Heavy render/ears imports are lazy again; shared MCP conventions appear once
in initialization instructions, and tool descriptions contain specific purposes.

### WP15 — no-pulse audio and honest metrics

Valid decoded audio whose tracker yields no usable grid still produces analysis
descriptors: `bpm_global=null`, empty beat/downbeat arrays, `grid=null` in the
tool result, zero reliability. This relaxes the old nonempty grid contract;
readers must handle absent pulse. No timestamps are invented. A window preview
then plays explicitly labelled audio without clicks. Summary reliability omits
the per-bar array; the complete map remains in `analysis.json`.

`DetuneCheck.confidence` and optional `Alarm.confidence` expose tuning strength
(heuristic, not a probability). Weak/predicted detune becomes `info` with no
automatic fix. Possibly intentional fourth/fifth transpositions also omit a
destructive fix. Ears summary lists skipped checks/reasons; instrument ducking
reports the lack of a dry instrument reference. Relative file references now
accept backslashes and normalize to `/`; traversal/absolute-path checks apply
after normalization. UTF-8 input errors include the byte offset. CLI doctor
retains its documented `{ok,checks}` diagnostic exception. Generated help escapes
percent signs. Drum sample swaps precede configured-gain notes, which explicitly
do not claim an isolated audible level change.

### WP15 — inherited prune reproduction gap found on real demo

An old demo render (v003, created before subsequent engine/grid changes) was
prunable under WP13's curated-alias test but regenerated with different PCM.
`RenderMeta.render_signature` now fingerprints renderer ABI/dependency versions,
song, effective source grids and referenced audio/MIDI/explicit preset content.
Pruning keeps absent/mismatched provenance as well as existing protections;
older versions are protected conservatively. A current render can be pruned and
regenerated with matching PCM. The renderer also passes the version snapshot's
seed to SFZ hosting (optional Python keyword), rather than reading the live
song's seed during re-render. Candidate fingerprints ignore reporting/history
changes, so no-op regrids leave retained candidates choosable.

### 2026-11-17 — WP16 arrangement editor: per-event control, song.edit, pieces

The owner wants to see and touch the arrangement ("control atómico", "sacá esto",
quitar un golpe, estirar, cortar). The data model was atomic but a single drum hit or
one pass of a repeated clip could only be changed by unrolling the pattern/clip.
Contract changes, all **additive** (old songs parse and render bit-identically,
`tests/render/test_wp16_events.py::test_defaults_render_bit_identically_through_the_new_paths`):

- `PatternLane.overrides: {<song bar>: <one bar of steps>}` replaces single bars of a
  lane (mute/add/replace/velocity are all "write that bar"). Chosen over per-hit
  exception lists because it reads like the steps an LLM and a human already know.
  `PatternLane.steps` may be omitted (all rests). Override bars must lie in the track
  window and be exactly `steps_per_bar` long (`out_of_range`, `pattern_length`).
- `Pattern.hits: [{beat, lane, velocity?, sample?}]`: free-timed hits (1-indexed song
  beats like notes, no swing, optional per-hit sound). The performance-pads follow-up
  (WP17) records into it; `song.edit {op: add_hits, quantize: off|step|beat}` is its API.
- `Clip.instances: {<1-based pass>: {mute, gain_db, pitch_semitones, reverse}}` overrides
  one pass of a repeated clip; `Clip.length_beats` is now allowed on chop clips (trim),
  plus `trim_start_beats`, `stretch` (keeps pitch), `gain_db`, `reverse`,
  `fade_in_ms`/`fade_out_ms`. Material outside the chop continues the record at the same
  warp rate (right) or re-warps the chop extended to the left. An "unroll" op was
  rejected: 8 near-identical clips are harder to read than one `instances` line.
- One expansion (`schemas.song.pattern_hits`, `clip_passes`) is used by the renderer,
  the ears timeline, the musical diff and (mirrored in TypeScript) the browser editor.
  Diff paths: `tracks.<t>.pattern.lanes.<l>.overrides.<bar>`, `tracks.<t>.pattern.hits[i]`
  (aligned like notes), `tracks.<t>.clips[i].instances.<n>.<field>`. Spanish:
  "Saqué el bombo de la batería del tercer tiempo del compás 7", "Muteé la repetición 3
  del clip del chop c1 del sample".
- Tools `song.edit`, `song.state` (readonly), `song.pieces` (package `muestra.editor`,
  registered in `registry._TOOL_PACKAGES`). `song.edit` is atomic and validated (only
  issues the song did not already have block), writes through the ruamel round trip,
  returns `applied` + `inverse` primitive ops, and journals before/after bytes under
  `cache/edits/` so undo/redo restore song.yaml byte-for-byte. Concurrency rule: ops
  are id-addressed and carry `expect` (the value they replace); when the file changed
  since `base_revision` they are re-applied and succeed iff every expect holds
  (`rebased: true`), else `edit_conflict` and nothing is written. Revision = first 16
  hex of sha256(song.yaml). A write that would not re-read as the intended song is
  refused (ruamel bug: emptying a commented block list).
- `song.pieces`: chop materials (the renderer's own `chop_audio`), one-shots, instrument
  dry stems and per-track WET stems rendered at gain 0 dB / pan centre / unmuted (the
  browser fader applies gain and the pan law relative to centre; measured equal to the
  render stem within 1e-5). Files under `cache/previews/pieces/` (served by `/files`),
  named by content, pruned to 400 files / 2.5 GB. A track whose audio fails reports
  `error` and does not block the others.
- `CommentEvent.selection` + `song_revision` (additive): what the human pointed at
  (track, lane, beat range, clip/pass, hits, notes, automation points, chop/source
  region, `paths`, `excerpt`). `feedback.read` adds `digest` (one line per event; the
  MCP text shows it) with the `song.edit` op that acts on a selection.
- LAN: `song.edit` and `song.pieces` join the remote-allowed write tools (no filesystem
  paths; library refs resolve only inside library roots).
- `render_signature` and file validation include per-hit samples.
- Editing is not versioning: `song.edit` never commits. "Guardar versión" is `render`
  with `summary = song.state.summary` ("Edición manual: ...").
