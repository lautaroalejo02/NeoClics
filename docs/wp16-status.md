# WP16 arrangement editor: running status

Branch `wp16-editor` (from `muestra/e1`). Updated with every commit.

## Done
- Schema per-event control (lane `overrides`, `pattern.hits`, clip `instances`, trims,
  stretch, gain, reverse, fades), shared expansion, renderer, ears timeline, diff + Spanish.
- Backend `song.edit` / `song.state` / `song.pieces` (+ journal, byte-identical undo,
  conflicts), selection-anchored feedback + digest, skill docs, contracts, decisions.
- UI: Arreglo view (canvas timeline, step grid, piano roll, automation lanes, inspector,
  sound browser, ask box, shortcuts), arrangement player in the engine (look-ahead
  scheduler, dry/wet, one-shot path, offline render), optimistic edits + undo/redo,
  save version / discard / draft "render final".
- Real e2e (`e2e-real/editor.spec.ts`) passes: kick removal measured in the browser mix and
  in a draft render, undo/redo byte-identical, change sound, move clip, trim, add note,
  ask the agent (feedback.jsonl), save version with Spanish summary.

## In progress
- UI unit tests, mock e2e fallback, perf/memory and fidelity measurements.

## Next
- Source view (chop regions on the source waveform), docs (ui/README, API-NEEDS).
