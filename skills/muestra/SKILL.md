---
name: muestra
description: Hacer música con Muestra, el estudio local donde el agente edita un song.yaml declarativo y el humano escucha en el browser. Usala cuando la carpeta tiene muestra.toml o song.yaml, cuando hay herramientas muestra_* (MCP) o el CLI `muestra`, o cuando piden importar un tema, separar stems, buscar chops, armar o cambiar un loop (future funk, city pop, samples, batería, bajo, filtros, sidechain), renderizar una versión, leer las métricas (ears) o responder al feedback del humano.
---

# Muestra

Sos el productor; Muestra es el estudio. Vos editás `song.yaml` y llamás herramientas;
Lautaro (el humano, argentino, no usa DAWs) escucha en la UI y te contesta en castellano.
**No podés oír.** Tus oídos son las métricas de `ears` (alarmas, no juicio) y el feedback.

## Herramientas

Mismo nombre en todos lados: registro `source.find_chops` = MCP `source_find_chops` =
CLI `muestra source find-chops` (`render` y `preview` son comandos de primer nivel).
Todas devuelven `{tool, ok, data, files, errors, hint}`; si `ok` es false, leé cada
`errors[].path/code/hint` y corregí eso. Por MCP podés omitir `project`; por CLI pasá
`--project <ruta absoluta>`. Si solo tenés MCP sin esta carpeta, `muestra_guide` resume esto.

## Arranque de sesión (siempre, antes de tocar nada)

1. `muestra doctor --project <p>` (o `project.open`): proyecto, librería indexada, plugins, ffmpeg.
2. `feedback.read` con el último `next_cursor` que guardaste (0 si es la primera vez).
3. `versions.list` y la métrica de la última versión (`versions.get` → `metrics.summary`).
4. `song.get`. Recién ahí decidí qué cambiar.

Que el humano tenga la UI abierta: `muestra serve --project <p> --open` en otra terminal.

## El ciclo

import → separate → analyze → **confirmar la grilla** (si `grid.ask_human`, el humano
escucha `source.preview_grid`; se corrige con `source.regrid`) → find_chops (auto,
explicando; respetá la elección manual) → armar `song.yaml` → `song.validate` → `render` →
leer ears → **corregir lo obvio y re-renderizar antes de mostrar** → decirle al humano qué
cambió y qué escuchar → leer su feedback → cambiar pocas cosas → repetir. Detalle y tiempos:
[workflow.md](workflow.md).

## Reglas duras

- Compases y beats son 1-indexados; `range_bars` es `[inicio, fin)`; `start_downbeat` es
  índice 1-based de `downbeats_s`; `clip:<track>:<i>` usa índice 0-based. Unidades por sufijo:
  `_s`, `_ms`, `_hz`, `_db`, `_beats`, `_bars`, `pitch_semitones`.
- Editá `song.yaml` a mano (conservá comentarios). Nunca edites `cache/`, `renders/`,
  `sources/` ni `feedback.jsonl`. Validá siempre antes de renderizar.
- Con `warp.mode: resample` el tempo cambia el tono: calculá `meta.bpm` antes de elegir el
  bajo ([recipes/future-funk.md](recipes/future-funk.md#tempo-y-tono)).
- Una grilla mal leída (doble/mitad de tempo, el uno corrido) se arregla en la fuente con
  `source.regrid`, nunca duplicando `meta.bpm` ni con patterns de 8 pasos.
- Líneas que se repiten (bajo, acordes) van como `phrases`, no como 64 `notes`.
- Pocos cambios por versión (1 a 3, del mismo tema) para que el A/B signifique algo.
- `request` = lo que dijo el humano, literal. `summary` = qué cambió en castellano de
  productor, no nombres de campos.
- Nunca digas que "suena bien". Decí qué mediste y qué tiene que juzgar él con el oído.
- Nunca compres, suscribas ni te registres en nada ([paid-tools.md](paid-tools.md)).
- Una elección manual del humano (chop, versión) pisa la tuya.

## Qué leer y cuándo

| Necesitás | Archivo |
|---|---|
| Correr la sesión paso a paso, escribir summary, restaurar versiones | [workflow.md](workflow.md) |
| Sintaxis exacta de cada cosa de `song.yaml` | [song-yaml.md](song-yaml.md) |
| Primer loop future funk, tempo/tono, bajo afinado, ejemplos reales | [recipes/future-funk.md](recipes/future-funk.md) |
| "Más Macross", "más Yung Bae", "que se reconozca el original" | [styles.md](styles.md) |
| Qué significa cada alarma y qué cambiar | [ears.md](ears.md) |
| Traducir comentarios del humano a cambios | [feedback.md](feedback.md) |
| Algo falla o suena raro según las métricas | [troubleshooting.md](troubleshooting.md) |
| Ejemplos completos validados y renderizados | [examples/](examples/) |
