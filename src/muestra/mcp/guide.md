# Muestra: guía corta para agentes

Shared conventions: use the current project unless `project` is supplied.
Song bars/beats and source downbeat indices are 1-based; `range_bars` is
`[start,end)` (end exclusive); clip preview indices are 0-based. Suffixes:
`_s` seconds, `_ms` milliseconds, `_hz` Hertz, `_db` decibels. Returned paths
are project-relative. Fix errors using their hints. Long tools report progress;
wait for completion. Windows paths accept both separators; prefer forward slashes.

Muestra es un estudio local: vos editás `song.yaml` y llamás estas herramientas; el humano
(Lautaro, castellano rioplatense, no usa DAWs) escucha en el browser y comenta. Muestra no
llama a ningún LLM. **No podés oír**: tus oídos son las métricas de ears (alarmas) y su
feedback. La skill completa (receta future funk, referencia de `song.yaml`, alarmas,
traducción de comentarios) está en `skills/muestra/SKILL.md` del repo de Muestra.

1. **Arranque, siempre:** `project_open` (ruta absoluta) → `feedback_read(cursor=<último
   next_cursor>)` → `versions_list` → `versions_get(<última>)` y leé `metrics_summary` →
   `song_get`. Recién ahí cambiás algo. La UI: `muestra serve --project <p> --open`.
2. **Fuente:** `source_import(path)` → primero `source_analyze` sobre el mix (~20–30 s,
   sin separar): bpm, key y `reliability.regions` fiables/dudosas; no falla por compás
   irregular. Sin pulso, BPM y grid son null y los beats quedan vacíos.
   Después `source_separate` si necesitás stems (4–6,5 min por tema de 3–4 min en esta
   laptop; avisale al humano). Adjunta stems sin mover la grilla ni reanalizar.
   **Grilla:** si `grid.ask_human`, `source_preview_grid(alternatives=true)` y que el humano
   escuche si el click agudo cae en el uno; `window_s=[inicio,fin]` apunta una región.
   Corregí con `source_regrid` (`tempo_factor`
   0.5/1/2, `downbeat_shift_beats`; absolutos y reversibles). Nunca dupliques `meta.bpm`
   para compensar una grilla mal leída.
3. **Chops:** fijá `meta.bpm` primero (los previews usan ese tempo). `source_find_chops`
   (`stem: other`, `length_beats` 8 o 16) → elegí con `chops_choose(mode=auto)` y explicá
   por qué en términos musicales. Una elección manual del humano (`chop_choice`) manda.
   "El riff del 0:15": `source_find_chops(at_s="0:15", length_beats=8)` en una llamada.
   Usa regiones fiables por defecto (`allow_unreliable=true` para explorar otras).
   Las ventanas usan la grilla del archivo completo: no recortes/reimportes para verificar.
4. **Arreglo:** editá `song.yaml` conservando comentarios. Compases/beats 1-indexados,
   `start_downbeat` 1-based, `range_bars` [inicio, fin), `clip:<track>:<i>` 0-based;
   unidades por sufijo (`_ms`, `_hz`, `_db`, `_beats`). Bajo y teclas como frases que se
   repiten (`phrases: [{id, length_bars, notes}]` + en el track `phrases: [{phrase: id}]`).
   Instrumentos: `finger-bass`,
   `slap-bass`, `synth-bass`, `rhodes`, `brass-stab`, `pad`. Batería: `library_kits`
   (`disco-01`...) y `library_search` por descriptores medidos.
5. **Tono:** con `warp.mode: resample`, semitonos = 12·log2(meta.bpm/local_bpm). Elegí
   `meta.bpm` para caer en un semitono entero y poné el bajo en esa tonalidad.
6. `song_validate` y arreglá cada issue (`path`, `code`, `hint`). Probá con
   `render(draft=true, range_bars=[1,5], analyze=true)`: sin versión/commit/tag, se escucha
   en la UI. Ears después: `ears_analyze(version=<draft_id>)`. Cuando esté listo,
   `render(request=<lo que
   dijo el humano>, summary=<qué cambió, en castellano de productor>, agent=<vos>)`.
7. **Ears antes de mostrar:** corregí clipping, clicks, choques bombo/bajo, sidechain que no
   agacha, chop fuera de grilla, bajo fuera de tono; re-renderizá. Lo dudoso (balance
   espectral, desafinación con confianza baja/info) se pregunta; no corrijas automáticamente.
   Leé también los checks omitidos y sus motivos. Ninguna alarma no significa que suene
   bien.
8. **Presentar:** qué cambiaste, qué mediste, qué tiene que escuchar y en qué segundo.
   Nunca digas que suena bien.
9. **Iterar:** `feedback_read`, 1-3 cambios del mismo tema por versión, `ears_compare` y
   `versions_diff` para explicar; `versions_restore` si prefiere una anterior.
   `versions_prune` propone limpieza (dry-run); `dry_run=false` la ejecuta sin perder historia.
   Celular: `muestra serve --host 0.0.0.0 --project <p>` y el link completo con `?token=...`.

Todo devuelve `{tool, ok, data, files, errors, hint}`; ante `ok: false` seguí los hints;
El diagnóstico CLI `muestra doctor` es la excepción: `{ok,checks}` cuando se redirige.
`not_implemented` no es un éxito. Errores inesperados: `cache/logs/muestra.log`. Nunca
compres, suscribas ni te registres en nada.
