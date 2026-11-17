# Versions (WP5)

Every render is a **version**: cheap to create, compare, discard and restore.
This is how it works and what the four tools (`versions.list`, `versions.get`,
`versions.diff`, `versions.restore`) promise. Binding contract text lives in
[`contracts.md`](contracts.md) ("Project = its own git repo"); decisions in
[`decisions.md`](decisions.md) (WP5 entry).

## Model

* A Muestra project is **its own git repository** (`project.create` runs
  `git init`). Muestra refuses to version unless the project folder is the
  *top level* of that repo — it never commits into a parent repository.
* **One version = one commit + one annotated tag `vNNN`.** Version ids are
  never reused: `next_version_id` = highest number seen in git tags, version
  commits in history, `renders/` folders and the `version`/`a`/`b`/`winner`
  fields of `feedback.jsonl` (free text such as a comment saying "v2026" does
  not count), plus one.
* A version commit contains only the **versioned files**: `song.yaml`,
  `midi/`, `muestra.toml`, `feedback.jsonl`, `docs/`, `.gitignore`,
  `.gitattributes`. Imported audio (`sources/`), `library/`, `renders/` and
  `cache/` are never committed by Muestra.
* `renders/vNNN/` (audio, `meta.json`, `metrics.json`, `song.yaml` snapshot) is
  **derived data**, git-ignored. Delete it freely: versions stay listed (marked
  `rerenderable`) and their song is recovered from git.

### Commit message format

```
v007: Subí el chop c1 un semitono          <- subject: vNNN: <summary, one line>

Muestra-Version: v007                      <- marker; makes a commit a version commit
Muestra-Request: más tono en el sample
Muestra-Summary: ...                       <- only when the one-line subject isn't verbatim
Muestra-Agent: claude-code
Muestra-Parent: v005
```

Free text (`request`, `summary`, `agent`) goes through a reversible one-line
encoding in the trailers: backslash → `\\`, newline → `\n`, CR → `\r`; NUL and
other control characters are dropped; leading/trailing whitespace is trimmed.
Quotes, emoji, accents and leading dashes are stored as they are — messages
travel on stdin to git, never through a shell or argv, so there is nothing to
inject into or mangle. A summary that is not a single line (or is longer than
200 characters) is cut in the subject and kept in full in `Muestra-Summary`.
With no summary the subject is just `vNNN`.

### `record_version(project, version, request, summary, agent, parent) -> sha`

Called by the `render` tool **after** the audio exists in a staging folder
and **before** that folder is published as `renders/vNNN` (WP13, see
[Atomic versions](#atomic-versions)).

* **Always creates a commit** (empty if nothing changed). Re-rendering an
  unchanged song, or changing only the request text, is still a distinct
  version with its own id, tag and metadata.
* `parent=None` (what `render` should pass) means *derive it*: see
  [Parent tree](#parent-tree). Pass an id only to force a parent.
* The commit is built with a **private temporary index** and plumbing
  (`read-tree` → `add` → `write-tree` → `commit-tree` → `update-ref`), so:
  the user's own staging area is never touched (anything they staged stays
  staged, outside the version); no hook, no `commit.gpgsign` setting can block
  or hang it; the branch moves with a compare-and-swap (a concurrent commit
  makes it fail instead of being overwritten). If tagging fails, the commit is
  rolled back.
* Identity: the repo's/user's `user.name`/`user.email` when configured (also
  honours `GIT_AUTHOR_*`/`GIT_COMMITTER_*`); otherwise `Muestra
  <muestra@localhost>` for that commit only — the config is never modified.
* Works in a brand-new project (the scaffold commit exists, versions start at
  `v001`) and even in a repo with no commits yet.
* Every git call pins `core.autocrlf=false` and drops `GIT_DIR`,
  `GIT_WORK_TREE`, `GIT_INDEX_FILE`… from the environment.

### Line endings (Windows)

`project.create` writes its files in text mode, so on Windows `song.yaml` is
**CRLF**; Git for Windows defaults to `core.autocrlf=true`, which would store LF
and make a `git show`-based restore differ from the file the user had. So
Muestra stores every byte verbatim: all its git calls pin `core.autocrlf=false`,
and new projects get a `.gitattributes` with `* -text` (so a manual `git
checkout`/`git status` doesn't convert or flag line endings either). Tests
cover restore from the `renders/` snapshot **and** from git with a global
`core.autocrlf=true`.

## Reading versions

### `versions.list`

Newest first, one coherent record per version, merging three sources:

| field | from |
|---|---|
| `version`, `git_commit`, `created_at`* | git tag/commit (`created_at`: meta.json when present) |
| `request`, `summary`, `agent`, `parent` | meta.json when present, else the commit trailers |
| `rendered`, `has_meta`, `has_metrics`, `has_stems` | what exists in `renders/vNNN/` |
| `rerenderable` | no audio on disk, but the song is in git: `render` with `version=<v>` regenerates it |
| `feedback{rating, rated_at, comments, ab_wins, ab_losses}`, `liked` | `feedback.jsonl` (latest rating wins) |
| `parent`, `children`, `is_base`, plus top-level `roots`, `base` | the version tree |
| `warnings` | corrupt `meta.json`, tag pointing at an unreadable commit, meta.json commit ≠ tag commit (history edited by hand), parent that no longer exists… |

A version known only from a `renders/vNNN/` folder (no tag) is listed too. A
version whose tag **and** renders were deleted by hand is gone from the list
(its commit is still in history, and its number is never reused).

### `versions.get(version)`

`meta` (parsed `meta.json`, `diff[]` entries serialised with the contract name
`from`), `song_yaml` (text, line endings preserved) + `song_source`
(`renders`/`git`), `info` (the record above), `metrics`, `mix`, `stems`.

### `versions.diff(a, b)`

Compares the two versions' **parsed** songs (so comments, key order,
formatting and explicitly-written defaults never show up as changes) and
returns:

* `diff` — structured entries `{path, from, to}`;
* `changes` — one Spanish sentence per musical change;
* `musical_summary` — those sentences as a paragraph (+ metrics delta);
* `metrics_delta` — Spanish phrases when **both** versions have a
  `metrics.json`; `identical` — no structural difference.

#### Path grammar

ID-based, the automation-target grammar of the contract:

```
meta.bpm                                   chops.c1.warp.pitch_semitones
sources.src2                               tracks.drums.pattern.lanes.kick.steps
tracks.sample.fx.lp.cutoff_hz              tracks.bass.instrument.preset
tracks.sample.clips[2].at_bar              tracks.bass.notes[0].pitch
tracks.bass.fx[1].ratio   (fx w/o id)      master.fx.limiter.ceiling_db
tracks.drums.fx.hp2       (added fx)       master.fx[0].ratio
automation.tracks.sample.fx.lp.cutoff_hz.points   (automation keyed by target)
tracks    /  tracks.<id>.fx   (reorder)
phrases.riff.length_bars                   phrases.riff.notes[3].pitch      (WP12)
tracks.bass.phrases[0].transpose_semitones (placements aligned like clips)
```

Phrases (WP12) are matched by id; their notes are aligned like inline notes;
a track's phrase placements are aligned by phrase id or start bar. The Spanish
description says "El bajo ahora repite la frase riff de 2 compases desde el
compás 5", and rewriting inline notes as phrases (or back) is ONE sentence that
ends in "(suena igual)" when the expanded notes are identical.

* Elements are matched by id (sources, chops, tracks, lanes, fx with an id,
  automations by target), so inserting/deleting/reordering never shifts
  anyone's address.
* Clips, inline notes and fx without id have no id: they are **aligned** —
  identical items first, then the most alike leftovers (same material or same
  spot for clips; same start, or same pitch+length for notes; same type for
  fx). A moved clip or a transposed note is a *modification*
  (`clips[1].at_bar`, `notes[3].pitch`), not a remove + add. Pairing is
  blocked by keys, so a 800-note melody diffs in milliseconds.
* An added element is one entry with `from: null` and `to` = the whole
  element (`tracks.perc`, `tracks.sample.clips[0]`); removed: `to: null`.
  Indices are 0-based and refer to the **new** version, except for removed
  elements (index in the old one). `fx[<i>]` is the position in the chain.
* Reordering is reported only where it is audible/visible: the order of an fx
  chain (`tracks.<id>.fx`, `master.fx`: `from`/`to` are the ordered ids, or
  `fx[i]` for id-less) and of the tracks (`tracks`, mixer order). Reordering
  clips, notes, chops, lanes or automations changes nothing, so it isn't a
  change. A track *inserted* before others does not count as a reorder.

#### The Spanish description

Written the way a producer would say it, in rioplatense Spanish, first person
past: "Subí el chop c1 un semitono", "Bajé el bajo 2 dB", "Cambié el preset
del bajo de slap-bass-01 a slap-bass-02", "Sidechain más profundo (0.6 a 0.8)
en el sample", "Agregué un filtro pasa-bajos automatizado de 800 Hz a
18 kHz en los compases 1 a 4 del sample", "El bombo pasa de negras a un
patrón con síncopa en el compás 4", "El delay de la batería ahora es de
corcheas (antes negras)".

* **Grouped.** A new track is one sentence (kind, instrument/lanes, fx names,
  how many automations — not thirty); an fx added together with its automation
  is one sentence; removing an fx mentions its automation; automation of an
  added/removed track or fx is folded into that sentence.
* **Rhythm.** Drum-lane changes are classified per bar (negras, corcheas,
  semicorcheas, contratiempos, tresillos, los tiempos 2 y 4, un patrón con
  síncopa…). A class change reads "pasa de X a Y"; inside the same class it
  lists the hits added/removed ("agregué un golpe en el 'y' del 3") or softer/
  harder. Only the changed bars are named ("en el compás 4").
* **Notes.** A constant shift is "Subí el bajo 2 semitonos" (or "Subí 2
  semitonos 8 notas del bajo"); otherwise "Agregué N notas al bajo en el
  compás 3", "Saqué…", "Moví…", "Cambié la dinámica de…".
* **Vocabulary.** Known track ids and drum-lane names get Spanish nouns (`bass`
  → "el bajo", `kick` → "el bombo", `ohat` → "el hi-hat abierto"); others are
  "el track <id>" / "el lane <id>". Filters "abrí/cerré", delays in note
  values, compressor "más/menos compresión", sidechain "más/menos profundo".
* **Total.** Every field family of the song schema has a phrase (meta,
  sources, chops, tracks, clips, patterns/lanes, instruments, midi/notes, all
  twelve fx types and their params, plugin params, automation, master); a field
  nobody anticipated falls back to "Cambié <field> de <a> a <b>". If the
  structured diff is non-empty the description is never empty.

The **metrics delta** ("el mix quedó 1.2 LU más fuerte; menos energía en
graves") uses `muestra.ears.core.compare(a, b)` when that function exists and
returns Spanish phrases; otherwise it is computed from the two `metrics.json`
files (loudness ≥ 0.5 LU, true peak ≥ 1 dB, clipping, band balance ≥ 1 dB,
low-end owner, clicks, grid offset ≥ 5 ms, key coherence, new alarms).

## `versions.restore(version)`

Puts `song.yaml` (and `midi/`) back to that version, **byte for byte** — from
`renders/vNNN/song.yaml` when present, else the tag's blob (`song_source`
says which). It never loses work and is undoable:

1. If the versioned files have uncommitted changes, they are first committed
   as an untagged **snapshot commit** (`muestra: snapshot before restoring
   vNNN`). The user's staging area is untouched.
2. The files are written (`song.yaml` atomically); `midi/` is made to match the
   tag's `midi/` — files not in that version are removed only if some commit
   holds them (git-ignored files are never deleted).
3. The result is committed as `muestra: restore vNNN` (trailer
   `Muestra-Restored-From`). No tag, no render: the next `render` creates the
   new version.

The result carries `snapshot_commit`, `previous_commit`, `restore_commit`,
`changed`, `warnings`, and a ready-made `undo`:

```
git -C "<project>" restore --source=<snapshot-or-previous-commit> -- song.yaml [midi]
```

plus a `hint` that explains it in a sentence. `renders/`, `feedback.jsonl` and
every other file are left alone.

### Parent tree

History is a tree, not a line. `suggest_parent` walks HEAD's first-parent
history for the newest *version commit* (`Muestra-Version`) or *restore commit*
(`Muestra-Restored-From`):

```
render v001, v002, v003   ->  parents: -, v001, v002
versions.restore v001
render                    ->  v004, Muestra-Parent: v001
```

`versions.list` exposes it as `parent`/`children` per version, `roots`, and
`base` (the version the next render will descend from; `is_base` on that
record) so the UI can draw it. It also survives a manual `git reset --hard
v001` (the walk finds the v001 commit).

## Errors (structured, with hints for an agent)

All failures are `MuestraError`s → `ok: false` with `Issue`s whose `hint` says
how to fix them:

| code | when |
|---|---|
| `unknown_version` | no tag and no `renders/` folder (hint lists the known versions) |
| `not_git_repo` | not a repo, or only *inside* another repo (never committed into) |
| `git_not_installed` | `git` not on PATH |
| `repo_locked` | `.git/index.lock` exists |
| `repo_busy` | rebase/merge/cherry-pick/revert in progress |
| `detached_head` | HEAD is not on a branch (commits would be orphaned) |
| `version_exists` | the tag already exists (ids are never reused) |
| `git_unsafe_repo` | git's "dubious ownership" protection |
| `git_error` | any other git failure (stderr included) |
| `file_not_found` | no `song.yaml` to version |
| `parse_error` / `duplicate_key` / `missing_field`… | a version's `song.yaml` can't be parsed |

Read tools (`get`, `diff`) work from the `renders/` snapshot without git;
`list` and everything that writes need the repo.

## For the `render` tool (WP2)

```python
with staging.stage(project) as stage:          # renders/.staging/<token>/ + its lock
    render audio + song.yaml into stage.dir
    with staging.version_lock(project):        # renders/.staging/version.lock
        version = next_version_id(project)     # never reused for a published version
        stage.mark(version)                    # pending.json, before the commit
        sha = record_version(project, version, request, summary, agent, None)
        stage.mark(version, sha)
        write meta.json into stage.dir          # parent = commit_info(sha).parent
        stage.publish_new(renders/<version>)    # one rename
```

## Disk use and `versions.prune` (WP13)

A 30 s render is ~95–140 MB (measured on skill-check v018, 30.4 s at 48 kHz:
mix 8.8 MB, five track stems 58 MB, six drum-lane stems 70 MB; stems are
32-bit float). `versions.list` gives each record `size_bytes` and a top-level
`disk {renders_bytes, cache_render_bytes, previews_bytes, stems_bytes,
total_bytes, hint}`; the `hint` appears above 1 GB of renders + caches and
suggests `versions.prune`. `GET /project` carries the same `disk`.

`versions.prune(dry_run=true, keep_latest=5, cache=true)` deletes whole
`renders/vNNN/` folders; commits and tags stay, so the version is still
listed (`rerenderable`, `size_bytes: 0`) and `render version=vNNN`
regenerates it **bit-exact** (tested: prune → re-render → identical
`mix.wav` bytes). Kept, with reasons in the result: the newest
`keep_latest`; liked (latest rating); **unread feedback** = any event naming
the version (rating, comment, chop choice, either side of an A/B) with a
timestamp after the newest version's creation (the agent hasn't answered it
with a new version yet; Muestra can't know which `feedback.read` was the
agent's); both sides of the last 10 A/B choices; the base (parent of the
next render); versions that can't be re-rendered bit-exactly (no tag, or
plugin presets outside the curated aliases/SFZ). Cache GC: `cache/render`
entries not used by the newest render (cache hits now touch their file),
interrupted `*.tmp`, and all but the newest 5 loose previews / draft
renders. Never touched: `sources/`, `cache/stems/`, `cache/analysis/`,
`cache/chops/`, candidate previews. On a copy of skill-check (18 versions,
2.0 GB): the dry run proposed 12 versions + 36 cache files = 1.3 GB.

## Atomic versions

(WP13.) A version the human can see always has its audio. The `render` tool
renders into `renders/.staging/<token>/` while holding
`renders/.staging/<token>.lock`, and only then (under the project-wide
`renders/.staging/version.lock`) allocates the id, commits + tags, writes
`meta.json` and renames the folder to `renders/vNNN`. `.staging` never
matches `vNNN`, so it never counts as a version, is never listed and never
bumps the id counter.

* **Failure or interruption in-process** (an exception, Ctrl-C): the tag is
  deleted, the branch is moved back if our commit is still its tip, the
  staging folder is removed. The tool result says "No version was created".
* **Hard kill** (power loss, `kill -9`): the next `render` and `muestra
  serve` run `staging.recover`. Rule: a staging folder whose lock nobody
  holds belongs to a dead process. If its `pending.json` names a version
  `vNNN` and `renders/vNNN` does not exist, that version never had audio:
  its tag is deleted when it points at the commit recorded in
  `pending.json` (or, when the kill came before that was written, at a
  commit whose `Muestra-Version` trailer is `vNNN`), the branch is moved
  back when that commit is its tip, and the folder is deleted. A tag made
  by hand, or a version whose `renders/` folder exists, is never touched.
  Recovery runs under the version lock, so it can't race a live render.
* **Id reuse.** A rolled-back id can be allocated again: no render of it
  ever existed, so nobody can have heard, rated or commented it. Ids of
  published versions are never reused (unchanged rule).
* A re-render (`version=`) also renders into staging; the old audio stays
  in place until the new files exist, then each file is replaced (the UI may
  be streaming the old one; Windows can't rename a folder with open files).

Re-rendering an existing version (`RenderParams.version`) must **not** call
`record_version`: the commit and tag already exist; read the song with
`song_at_raw`/`snapshot_bytes`.
