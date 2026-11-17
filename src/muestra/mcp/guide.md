# Muestra: guía corta para agentes

Muestra es un estudio local: vos editás `song.yaml` y llamás estas herramientas; el humano
(Lautaro, castellano rioplatense, no usa DAWs) escucha en el browser y comenta. Muestra no
llama a ningún LLM. **No podés oír**: tus oídos son las métricas de ears (alarmas) y su
feedback. La skill completa (receta future funk, referencia de `song.yaml`, alarmas,
traducción de comentarios) está en `skills/muestra/SKILL.md` del repo de Muestra.

1. **Arranque, siempre:** `project_open` (ruta absoluta) → `feedback_read(cursor=<último
   next_cursor>)` → `versions_list` → `versions_get(<última>)` y leé `metrics_summary` →
   `song_get`. Recién ahí cambiás algo. La UI: `muestra serve --project <p> --open`.
2. **Fuente:** `source_import(path)` → `source_separate` (minutos en CPU: avisale al humano
   antes; queda en caché) → `source_analyze` (bpm, downbeats, key con confianza, y `grid`:
   qué leyó el tracker y qué octava se eligió en 70-160 BPM).
   **Grilla:** si `grid.ask_human`, `source_preview_grid(alternatives=true)` y que el humano
   escuche si el click agudo cae en el uno; corregí con `source_regrid` (`tempo_factor`
   0.5/1/2, `downbeat_shift_beats`; absolutos y reversibles). Nunca dupliques `meta.bpm`
   para compensar una grilla mal leída.
3. **Chops:** fijá `meta.bpm` primero (los previews usan ese tempo). `source_find_chops`
   (`stem: other`, `length_beats` 8 o 16) → elegí con `chops_choose(mode=auto)` y explicá
   por qué en términos musicales. Una elección manual del humano (`chop_choice`) manda.
4. **Arreglo:** editá `song.yaml` conservando comentarios. Compases/beats 1-indexados,
   `start_downbeat` 1-based, `range_bars` [inicio, fin), `clip:<track>:<i>` 0-based;
   unidades por sufijo (`_ms`, `_hz`, `_db`, `_beats`). Bajo y teclas como frases que se
   repiten (`phrases: [{id, length_bars, notes}]` + en el track `phrases: [{phrase: id}]`).
   Instrumentos: `finger-bass`,
   `slap-bass`, `synth-bass`, `rhodes`, `brass-stab`, `pad`. Batería: `library_kits`
   (`disco-01`...) y `library_search` por descriptores medidos.
5. **Tono:** con `warp.mode: resample`, semitonos = 12·log2(meta.bpm/local_bpm). Elegí
   `meta.bpm` para caer en un semitono entero y poné el bajo en esa tonalidad.
6. `song_validate` y arreglá cada issue (`path`, `code`, `hint`). `render(request=<lo que
   dijo el humano>, summary=<qué cambió, en castellano de productor>, agent=<vos>)`.
7. **Ears antes de mostrar:** corregí clipping, clicks, choques bombo/bajo, sidechain que no
   agacha, chop fuera de grilla, bajo fuera de tono; re-renderizá. Lo dudoso (balance
   espectral, desafinación "prevista") se pregunta. Ninguna alarma no significa que suene
   bien.
8. **Presentar:** qué cambiaste, qué mediste, qué tiene que escuchar y en qué segundo.
   Nunca digas que suena bien.
9. **Iterar:** `feedback_read`, 1-3 cambios del mismo tema por versión, `ears_compare` y
   `versions_diff` para explicar; `versions_restore` si prefiere una anterior.

Todo devuelve `{tool, ok, data, files, errors, hint}`; ante `ok: false` seguí los hints;
`not_implemented` no es un éxito. Errores inesperados: `cache/logs/muestra.log`. Nunca
compres, suscribas ni te registres en nada.
