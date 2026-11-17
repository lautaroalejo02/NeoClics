# WP17 performance pads: running status

Branch `wp17-pads` (from `muestra/e1`).

## Done
- Song schema (additive): slice lanes (`PatternLane.slice`, `LaneSlice`), lane
  `pitch_semitones`/`reverse`/`choke`, hit `length_beats` (gate); shared expansion
  computes `DrumHit.cut0` (gate/choke); renderer `lane_sound`/`slice_audio`/`cut_sound`;
  validator (slice refs + analysis/grid checks), render signature, Spanish diff.

## In progress
- `muestra.pads` package: schema (`schemas/pads.py`), storage, processing, commit ops.

## Next
- pads tools, song.pieces voices, tests; UI pads view, recording, latency; docs.
