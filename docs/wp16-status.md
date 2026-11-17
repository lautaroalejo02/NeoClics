# WP16 arrangement editor: running status

Branch `wp16-editor` (from `muestra/e1`). Updated with every commit.

## Done
- Schema: per-bar lane `overrides`, free `pattern.hits`, optional lane `steps`,
  clip `instances` (per-pass mute/gain/pitch/reverse), clip `trim_start_beats`,
  `length_beats` on chop clips, `stretch`, `gain_db`, `reverse`, `fade_in_ms`/`fade_out_ms`.
  Shared expansion: `pattern_hits`, `clip_passes`, `lane_bar_steps` (schemas/song.py).
- Renderer uses the shared expansion; defaults stay bit-identical
  (tests/render/test_wp16_events.py).

## In progress
- versions diff/describe + ears timeline for the new forms.

## Next
- `song.edit` (ops, inverse, journal, revision), `song.pieces`, feedback selection,
  docs/contracts, UI Arreglo view.
