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
