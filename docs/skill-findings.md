# WP9: hallazgos para el integrador

Encontrados mientras se verificaba la skill contra el producto real (branch `muestra/e1`
en 2ee9f61, Windows 11, 2026-11-17). No se tocó código del producto. `P` =
`C:\Users\lauti\Documents\MuestraProjects\skill-check` (proyecto de prueba con los primeros
30 s de Tiger Rag, 1928, CC0, ya separado y analizado). Comandos con `uv run` desde el repo.

## Bugs

### 1. ears sugiere `master.gain_db` para `loudness_off_target`, pero con `target_lufs` no tiene efecto

- Repro: con `examples/tiger-rag.yaml` en `P`, `preview target=mix` con `master.gain_db`
  0, +6 y -6 (o render).
- Esperado: el `fix` de `loudness_off_target` propone algo que cambie el volumen final.
- Real: las tres miden -13.13 LUFS / -0.96 dBTP. `render/dsp.py::master` aplica
  `master.gain_db` antes de normalizar a `target_lufs`, así que se anula; el fix dice
  "revisá `master.gain_db` y los `gain_db` de los tracks". Lo que acerca al target es
  bajar el pico (bombo) o el target. Además, un `type: gain` antes del limitador del master
  tampoco ayuda salvo que lleve los picos por encima del techo (+4 dB: sin cambio; +10:
  -11.3; +14: -10.5 con target -10).

### 2. `sample_detuned` "previsto" contradice la medición y su fix desafina

- Repro: `P`, v001-v009. La fuente mide `tuning_cents` -45 (stem other -45.0, original
  -44.9, render v001 -43.4; fuerza 0.05-0.08, debajo de `tuning_min_strength` 0.15).
  Compensando (`meta.bpm` 252.64 = +0.45 st) el sample mide +2.1 cents.
- Esperado: sin alarma, o una que use la medición (consistente aunque débil) o permita
  declarar el corrimiento del disco.
- Real: `sample_detuned` warn "+45 cents ... (previsto por el resample (sin medir))" en cada
  versión, con fix `pitch_semitones: -0.45` / `meta.bpm: 246.15`, que volvería a desafinarlo.
  Tampoco se puede declarar en `ears.toml` (solo umbrales).

### 3. `track_transposed` marca relaciones de quinta que `bass_vs_sample` aprueba

- Repro: `P` v003 (bajo en G, `meta.key: C`): `track_transposed` "bass suena 5 semitonos
  abajo de C" mientras `bass_vs_sample` da `best_shift_semitones: 0`, fit 0.575. v014
  (`examples/tiger-rag-macross.yaml`): `track_transposed` del sample "+5 de G#" con el bajo
  encajando (fit 0.578).
- Esperado: un pedal de dominante (±5/±7 semitonos) es normal; info, o no alarmar si
  `bass_vs_sample` encaja.
- Real: warn con un fix de transponer 5 semitonos. No existe `meta.key` que silencie las dos.

### 4. `versions.diff` dice "Bajé el bombo del bombo 2 dB"

- Repro: `versions.diff a=v001 b=v004` en `P` (track `kick` con lane `kick`).
- Esperado: "Bajé el bombo 2 dB". Real: "Bajé el bombo del bombo 2 dB". Cosmético.

## Limitaciones de diseño (no son bugs del código)

### 5. No hay forma de mapear una fuente a doble tempo

Beat This mide Tiger Rag a 250 BPM (sensación ~125). `chop.length_beats` es a la vez beats
de la fuente y del proyecto, así que a 125 BPM un chop en resample suena una octava abajo y
a la mitad de velocidad (o 2x stretch). Workaround documentado en la skill: "doble grilla"
(`meta.bpm` 2x, `length_bars` 2x, `steps_per_bar: 8, bars: 2`). Sugerencia: un factor
`source_beats_per_beat` (0.5/1/2) en el chop o en el análisis.

### 6. Las notas inline no se repiten

Un bajo de 2 compases que se repite 12 compases son 48-128 líneas `notes`. Los patterns
tile-an; las notas no. Sugerencia: `notes_loop_beats` o `repeat` en instrumentos.

### 7. `plugins.list` devuelve ~541 KB (3.028 presets) sin filtro

- Repro: `muestra plugins list --project P > out.json` → 541.147 bytes.
- Para un agente por MCP es una llamada que llena el contexto. Sugerencia: parámetros
  `query`/`plugin`/`limit`, o por defecto solo alias + plugins.

### 8. El pattern listo de `library.kits name=disco-01` trae crash en todos los compases

`crash: "x..............."` y cencerro `.x.x..x..x.x..x.` con `bars: 1` se repiten en cada
compás; un crash por compás no es un default musical. Sugerencia: crash con `bars: 4` o
fuera del pattern. La skill indica copiar solo kick/clap/hats.

### 9. Chops del mismo lugar con distinto largo tienen distinto tono en resample

Consecuencia correcta del contrato (`local_bpm` por chop), pero fácil de pasar por alto:
desde el downbeat 17 de Tiger Rag, 2 beats → 240.0 BPM, 8 → 244.9, 16 → 246.15, 32 → 246.8;
un stutter de 2 beats queda 35 cents distinto de un chop de 8. Documentado en la skill
(compensar con `pitch_semitones`). ears no lo marca como alarma propia.

### 10. `slap-bass` genera avisos `click` en notas cortas

`examples/tiger-rag-yungbae.yaml` con `slap-bass`: 7 `click` a -40/-53 dBFS en ataques
(v015); con `finger-bass`, ninguno (v017). Puede ser el ataque del preset FM o un click
real: no se puede saber sin escuchar.

## Entorno

- `muestra doctor`: ffmpeg no está en el PATH de esta máquina (solo afecta mp3/m4a).
- `render` devuelve el modelo completo de métricas (12-21 KB por render); WP6 sugería solo
  `metrics.summary` en línea. La skill le dice al agente que lea `summary` primero.

## Renders

Todos en `P`, feedback humano **simulado** (3 eventos agregados con `feedback.append` para
probar la lectura). Tiempos = `meta.render_time_s`. Nadie escuchó nada.

| Versión | Qué | Render | ears (summary) |
|---|---|---|---|
| v001 | receta inicial, 260.8 BPM, bajo F# | 3.9 s | -13.2 LUFS (target -10), 1 aviso |
| v002 | 252.64, bajo C, ganancias | 5.4 s | -12.0, 4 avisos (clash -5, detune previsto, sub, loudness) |
| v003 | bajo G | 4.4 s | -12.1, 4 avisos (track_transposed, ...) |
| v004 | meta.key G, target -12 (= `examples/tiger-rag.yaml`) | 5.8 s | -13.1, 2 avisos |
| v005 | iteración 1 "bajo más gordo" | 3.3 s | -13.5, 2 avisos + 1 info |
| v006 | iteración 2 "el bombo se come al sample" | 6.0 s | -13.4, 4 avisos (low_end_collision nuevo) |
| v007 | autocorrección de v006 | 6.1 s | -13.2, 2 avisos |
| v008 | iteración 3 "está muy acelerado" | 9.7 s | -13.2, 2 avisos |
| v009 | `examples/tiger-rag.yaml` tal cual | 4.8 s | -13.1, 2 avisos |
| v010 | Macross, primera versión | 6.5 s | -12.1, 3 avisos (bpm calculado con el chop equivocado) |
| v011 | Yung Bae con slap | 5.6 s | -12.7, 2 avisos (click) |
| v012 | Night Tempo con resample | 5.0 s | -12.0, 5 avisos (grid_drift) |
| v013 | `examples/template.yaml` (solo mecánica) | 5.1 s | -14.6, 3 avisos |
| v014 | `examples/tiger-rag-macross.yaml` | 5.9 s | -12.1, 3 avisos |
| v015 | Yung Bae con slap, notas corregidas | 5.5 s | -12.7, 2 avisos (click) |
| v016 | `examples/tiger-rag-nighttempo.yaml` | 7.3 s | -12.2, 4 avisos (espectrales, detune previsto) |
| v017 | `examples/tiger-rag-yungbae.yaml` (difiere solo en un comentario) | 5.4 s | -13.2, 2 avisos |

Ninguno tuvo alarmas de severidad `error`.
