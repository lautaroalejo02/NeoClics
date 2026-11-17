# Working with Muestra

Muestra is a local music studio. You edit a project's declarative song.yaml and use tools
to prepare audio and render versions; the human listens and gives feedback in a browser.
Muestra never calls a language model. Your existing agent supplies the musical reasoning.

1. Open a project with project_open(path), or project_create(name, dir). The server remembers
   that project. Use absolute paths when importing files or switching projects. Each result
   identifies the project; confirm it before editing. Run `muestra serve --project <path>`
   in a second terminal and give the human its printed URL.
2. Read song_get and feedback_read before every iteration. Save next_cursor and pass it as
   cursor on the next feedback_read, so comments with identical timestamps are not lost.
   Feedback is the human's listening evidence. Ask about ambiguous musical intent.
3. Import a local WAV, FLAC, MP3 or M4A with source_import. Then source_separate and
   source_analyze once per source; reuse cached stems, beats, downbeats, key and sections.
   Long tools may take minutes. Wait for their result/progress; do not duplicate the request.
4. Use source_find_chops to propose three candidates from the requested section, stem and
   length_beats. Inspect previews and scores. Normally choose one with chops_choose(mode=auto)
   and explain why. Candidates remain visible in the UI; a human manual choice overrides
   your preference. Read the resulting chop_choice feedback before making another change.
5. Edit song.yaml on disk, preserving comments and unrelated changes. Use actual source/chop
   IDs, library samples and installed plugin aliases. Song bars and beats, and start_downbeat,
   are 1-indexed; seconds appear only in audio/analysis/feedback. range_bars=[start,end) ends
   EXCLUSIVELY. `clip:<track>:<index>` previews use a 0-based clip index. Do not guess units:
   _s=seconds, _ms=milliseconds, _hz=Hertz, _db=decibels, pitch_semitones=semitones.
6. Run song_validate. Fix all issues using path, code, message and hint. check_files=false is
   useful for a structural draft; use the default true before rendering. Preview the selected
   chop/track or mix, then render with a short musical summary, request and agent identity.
   Render creates a version, git commit/tag, mix, aligned stems, metadata and metrics.
7. Read the render's `metrics_summary` (headline + the worst alarms, each with a song.yaml
   fix); the full report is `renders/vNNN/metrics.json` (ears_analyze / versions_get) and
   covers clipping/true peak, loudness, low-end masking + sidechain, grid alignment, tonal
   coherence + detune and clicks. These are alarms, not proof that music sounds good. Never
   claim to have heard the result. Tell the human what changed and where to listen. If the
   render hint says a track's reproducibility is unverified (a non-curated plugin preset),
   prefer the curated aliases from plugins_list. A sample `grid_offset` alarm on a busy live
   record is unreliable (docs/ears.md); trust drum/instrument timing.
   If the chop's source tempo is about double the project tempo (old records often track
   at 200-260 BPM), use `warp.mode: stretch`: resample would drop it an octave.
8. Let the human compare versions at the same playback position, mute/solo stems, rate and
   comment with timestamps. Read new feedback, make a focused edit, validate and render
   again. Use versions_diff/get/list to explain the musical changes; versions_restore can
   recover a chosen song snapshot. Do not overwrite existing rendered versions accidentally.

For a first future-funk loop, try 16 bars, a downbeat-aligned riff, vinyl-style resample warp,
four-on-the-floor drums, offbeat hats, a controlled bass, gentle sidechain and a filter sweep.
Treat this as a starting palette: the human's taste and feedback take precedence.

Every tool returns {tool,ok,data,files,errors,hint}. Paths in files are project-relative.
On errors, follow the hints. `not_implemented` means this build needs the corresponding work
package; do not simulate success. Unexpected failures have diagnostics in cache/logs/muestra.log.
No purchases, subscriptions, automatic LLM calls or telemetry are needed.
