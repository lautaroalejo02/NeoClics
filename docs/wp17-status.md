# WP17 performance pads: running status

Branch `wp17-pads` (from `muestra/e1`).

## Done
- Song schema (additive): slice lanes (`PatternLane.slice`, `LaneSlice`), lane
  `pitch_semitones`/`reverse`/`choke`, hit `length_beats` (gate); shared expansion
  computes `DrumHit.cut0` (gate/choke); renderer `lane_sound`/`slice_audio`/`cut_sound`;
  validator (slice refs + analysis/grid checks), render signature, Spanish diff.
- `muestra.pads`: schema `schemas/pads.py` (banks, takes), storage under `pads/`, quantise/clean-up, commit ops, slicing (beat/half/transients), tools `pads.get/save_banks/take_save/take_get/take_delete/commit/slice/from_track`; song.pieces `voices`/`note_voices`; tests `tests/pads`.

## In progress
- UI: done: song.ts mirror (slice lanes, choke/gate cut0, voices), engine pads output + editorClock + playEditorAt, pads/{keymap,model,player,store,react}. PadsView done (desktop+phone screenshots OK). UI unit tests `ui/test/pads.test.ts` (31).

## Next
- real e2e (record / commit / render timing / slices / reload), latency + burst measurements, docs (decisions, contracts, render, ui README, skill).
