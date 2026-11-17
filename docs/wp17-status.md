# WP17 performance pads: running status

Branch `wp17-pads` (from `muestra/e1`).

## Done
- Song schema (additive): slice lanes (`PatternLane.slice`, `LaneSlice`), lane
  `pitch_semitones`/`reverse`/`choke`, hit `length_beats` (gate); shared expansion
  computes `DrumHit.cut0` (gate/choke); renderer `lane_sound`/`slice_audio`/`cut_sound`;
  validator (slice refs + analysis/grid checks), render signature, Spanish diff.
- `muestra.pads`: schema `schemas/pads.py` (banks, takes), storage under `pads/`,
  quantise/clean-up, commit ops, slicing (beat/half/transients), tools
  `pads.get/save_banks/take_save/take_get/take_delete/commit/slice/from_track`;
  song.pieces `voices`/`note_voices`; feedback selection `take`/`pad`; tests `tests/pads`.
- UI: song.ts mirror (slice lanes, cut0, voices), engine pads output + editorClock +
  playEditorAt, `src/pads/*`, Pads panel (grid, banks, recorder, pad editor, takes,
  calibration), "Cortar en pads" in the source view; unit tests `ui/test/pads.test.ts`;
  real e2e `ui/e2e-real/pads.spec.ts`; probe `ui/scripts/pads-probe.mjs`.
- Docs: decisions, contracts, render, ui/README, skill `pads.md`.

## In progress
- Full check runs (pytest, ruff, UI typecheck/lint/test/e2e/e2e:real/build).

## Not done / partial
- Instrument note pads play pre-rendered single notes (no live synth): latency fine, but
  a note longer than the 4 s render is cut; per-note velocity is gain only.
- No per-pad keyboard remapping (fixed physical layout).
