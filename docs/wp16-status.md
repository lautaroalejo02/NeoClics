# WP16 arrangement editor: running status

Branch `wp16-editor` (from `muestra/e1`).

## Done
- Schema per-event control (lane `overrides`, `pattern.hits`, clip `instances`, trims,
  stretch, gain, reverse, fades), shared expansion, renderer, ears timeline, diff + Spanish.
- Backend `song.edit` / `song.state` / `song.pieces` (journal, byte-identical undo,
  conflicts, wet stems, one-shots, source export), selection-anchored feedback + digest,
  skill docs, contracts, decisions.
- UI Arreglo view: canvas timeline (clips, step grid, piano roll, automation), inspector,
  sound browser, source view, ask box, shortcuts; arrangement player in the engine;
  optimistic edits, batched persistence, undo/redo; save version / discard / render final.
- Tests: backend (editor, render events, feedback selection), UI unit (editor model),
  real e2e `e2e-real/editor.spec.ts`; measurements in the final report.

## Not done / partial
- Velocity lane editing of notes is via inspector only; automation points can't be
  multi-selected by marquee; no per-hit sample override from the UI (lane-level only).
- Performance pads (WP17).
