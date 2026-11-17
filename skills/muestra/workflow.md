# Flujo de trabajo

Nombres de herramienta en forma de registro (`source.find_chops`); por MCP es
`source_find_chops`, por CLI `muestra source find-chops --project <p> --source-id src1`.
Los tiempos son los medidos en la laptop de Lautaro (Ryzen 5 7530U, CPU, 2026-11-17).

## 0. Arranque

1. `muestra doctor --project <p>`. Si falta algo, decilo con el `fix` que trae. `ffmpeg`
   solo hace falta para importar mp3/m4a (WAV y FLAC andan sin él).
2. `feedback.read(cursor=<último next_cursor>)`. Guardá el `next_cursor` nuevo (anotalo en
   tu respuesta o en `docs/` del proyecto) y usalo en la próxima lectura.
3. `versions.list`: cuál es la última, cuál le gustó (`liked`, `feedback.rating`), cuál es la
   base (`is_base`: de esa desciende el próximo render).
4. `versions.get(<última>)` → `metrics.summary` (y `metrics.alarms` si hace falta detalle).
5. `song.get`. Recién ahora planificá.

Si hay comentarios nuevos, respondé a eso primero. Si hay una `chop_choice` manual, esa
elección manda (ver paso 4).

## 1. Importar

`source.import(path=<ruta absoluta>)` copia el audio a `sources/` y lo agrega a `song.yaml`
como `src1`, `src2`... Instantáneo. Mismo contenido dos veces = mismo id.

## 2. Separar (lento)

`source.separate(source_id)`: HTDemucs en CPU. 30 s de audio ≈ 1 min; un tema de 4 min
≈ 9 min la primera vez; después es caché (< 0.1 s). **Antes de llamarla, avisale al humano**:
"Separo los stems; tarda unos minutos, te aviso". Mientras corre no lances otra separación ni
renders del mismo proyecto. Podés, en paralelo y sin tocar el proyecto: leer la librería
(`library.kits`, `library.search`), mirar los alias de instrumentos (abajo) y proponer en
texto el plan del loop.

## 3. Analizar

`source.analyze(source_id)` (≈ 15 s para 30 s de audio) → `cache/analysis/<src>.json` con
`bpm_global`, `tempo_variable`, `beats_s`, `downbeats_s`, `key {tonic, mode, confidence}`,
`sections`, `loudness_lufs`. La respuesta trae un resumen (sin las listas de beats) y
`grid`: qué leyó el tracker (`raw_bpm`), qué se aplicó (`bpm`, `tempo_factor`), confianza y
`alternatives`. Leé:

- `grid.corrected` / `grid.reason`: el tracker suele contar corcheas en discos viejos (Tiger
  Rag: 250); `source.analyze` elige la octava que cae en 70-160 BPM (`min_bpm`/`max_bpm` lo
  cambian) y guarda la lectura cruda. **Nunca dupliques el tempo del proyecto para
  compensar**: si la grilla está mal, se corrige la grilla (`source.regrid`).
- `grid.ask_human: true` → antes de armar el tema, que el humano escuche la grilla
  ([recipes/future-funk.md](recipes/future-funk.md#la-grilla-de-la-fuente-antes-que-nada)).
- `key.confidence` < 0.4 → la tonalidad es una suposición; la confirmás después con ears.
- `sections`: etiquetas `intro`, `section_02`... (por novedad, no "verso/estribillo").
- Si el análisis falla por compás (4/4 no consistente), no lo fuerces: decile al humano.

## 4. Chops (modo dual)

1. Fijá antes `meta.bpm` aproximado: los previews se renderizan al tempo del proyecto.
2. `source.find_chops(source_id, section?, stem?, length_beats?, n=3)`. Sin `length_beats`
   compara 1/2/4/8 compases. Para future funk empezá con `stem: other` (sin batería ni bajo
   del disco) y `length_beats` de 2 o 4 compases (8 o 16 beats de la fuente).
3. Devuelve candidatos `csNNN/candN` (start_downbeat, local_bpm, score con razones en
   castellano, preview) y un `pick` con justificación. La UI muestra los previews.
4. Elegí con `chops.choose(candidate_id, chop_id="c1", mode="auto")` y explicale al humano
   en términos musicales: "Tomé los 2 compases que arrancan a los 15 s: es donde mejor
   cierra la vuelta del loop y el pulso es más parejo; hay otros dos en la UI si preferís".
   No le leas números de score.
5. Si el humano elige otro en la UI (o te dice "el segundo"), eso aparece en
   `feedback.read` como `chop_choice` y ya está escrito en `song.yaml`. No lo pises. Si te
   lo pide por chat: `chops.choose(..., mode="manual")`; `adjust: {start_beats: 1}` corre el
   inicio un beat, `{length_beats: 4}` alarga.
6. ¿Pide más candidatos? Otra llamada con otra `section`, `stem` o `length_beats`.

## 5. Armar el loop

Partí de la receta: [recipes/future-funk.md](recipes/future-funk.md). Batería desde la
librería ([más abajo](#batería-sin-oír)), bajo con alias de instrumento. Escribí
`song.yaml` y corré `song.validate` (con `check_files` por defecto). Arreglá **todos** los
issues que devuelve (cada uno trae `path`, `code`, `hint`).

Alias de instrumento listos (no hace falta `plugins.list`, que devuelve ~540 KB):
`finger-bass` (bajo eléctrico real, E1-G3), `slap-bass` (FM slap), `synth-bass`
(square, fundamental fuerte), `rhodes`, `brass-stab`, `pad`.

## 6. Renderizar

`render(request=..., summary=..., agent=<tu nombre>)`. 16 compases ≈ 4-10 s. Crea `vNNN`
(commit + tag), `mix.wav`, stems, `meta.json`, `metrics.json`; la respuesta trae
`data.metrics_summary` (leé eso primero; el detalle está en `metrics.json`, vía `versions.get`). `range_bars:
[1, 5]` renderiza solo los compases 1-4. `version: v003` re-renderiza esa versión sin
crear otra. `preview(target="track:bass", bars=4)` escucha una parte sin crear versión
(pero sin métricas).

## 7. Leer ears y corregir antes de mostrar

Leé `metrics.summary` y [ears.md](ears.md). Corregí antes de mostrar todo lo que es un
defecto técnico claro: `clipping`, `click`/`loop_seam_click`, `low_end_collision`,
`sidechain_missing`, `grid_offset`/`grid_drift`/`clip_offset`, `sample_detuned` **medido**,
`bass_sample_clash`, `track_silent`/`track_inaudible`, `lane_offset`. Re-renderizá (es
barato) y mostrá esa versión; decile "v001 fue un borrador técnico, escuchá v002".
Dejá para el humano lo que es gusto o donde la métrica es dudosa (avisos `spectral_*`,
`sample_detuned` previsto, `key_mismatch` con confianza baja): mencionalos como pregunta.

## 8. Presentar

Tres partes, corto:

1. **Qué hice**, en lenguaje de productor (el `summary`).
2. **Qué medí**: lo relevante de ears en una línea ("volumen -13 LUFS, el sample se agacha
   ~5.6 dB con cada bombo, el bajo encaja con el sample, sin clicks").
3. **Qué escuchar**, con tiempos: "fijate a 0:08 cuando entra la batería si el bajo se
   siente o queda flaco; ears dice que falta sub-grave pero esa referencia es aproximada".

Nunca "suena bien/genial". Sí: "medí que...", "no puedo saber si...; decime".

## 9. Iterar

Leé el feedback nuevo, traducilo con [feedback.md](feedback.md), cambiá 1-3 cosas del
mismo tema, validá, renderizá, comparalo con `ears.compare(version_a, version_b)` y
`versions.diff(a, b)` (te da `changes` en castellano). Si pidió varias cosas distintas,
hacé una versión por cosa o agrupá solo lo que no se pisa, y decí cuál es cuál.

## Escribir `request` y `summary`

- `request`: las palabras del humano, sin corregir ("más filtrado en la intro, el bajo más
  gordo"). Si la versión es una autocorrección tuya: "(autocorrección: bajo fuera de tono)".
- `summary`: 1-2 frases, primera persona, qué se oye distinto. Sí: "Bajé medio tono el
  sample acelerándolo menos y filtré la intro 4 compases". No: "chops.c1.warp
  pitch_semitones -1; tracks.sample.fx.lp automation".
- Va al commit (`vNNN: <summary>`) y a la UI: es lo que Lautaro lee.

## Tempo y tonalidad desde el análisis

Regla: con `resample`, `semitonos = 12·log2(meta.bpm / local_bpm)`. Elegí el tempo para que
caiga en un semitono entero (o compense el desafinado del disco) y después poné el bajo en
la tonalidad resultante. Cuentas y ejemplo resuelto en
[recipes/future-funk.md](recipes/future-funk.md#tempo-y-tono).

## Batería sin oír

1. `library.kits` → kits curados (`disco-01` 909, `disco-808`, `city-pop-80s`,
   `acoustic-disco`); `library.kits name=disco-01` trae URIs, descripción medida y un
   `pattern:` para pegar. Copiá solo los lanes que usás: el pattern de ejemplo pone un crash
   en **todos** los compases y cencerro todo el tiempo.
2. Para cambiar una pieza, buscá por descriptores medidos: `library.search query="bombo
   corto punchy" type=kick clean_only=true`, o `similar_to=<uri>` + palabras ("menos boomy").
   Leé el `summary` de cada hit: `kick | 0.26s | pitch B1 (62 Hz) | decay 96 ms | ...`.
3. Criterios: bombo `punchy`/`short` con decay 80-200 ms para four-on-the-floor; fundamental
   del bombo cerca de la tónica o la quinta del tema; clap/snare `attack` corto; hats `bright`.
   Evitá flags `clipping`, `type-mismatch`, `low-level` salvo que lo busques.
4. Los kits **no fueron escuchados por nadie**: decilo cuando los propongas. Los niveles
   varían entre packs: ajustá `gain_db` por lane (los TR-8 vienen calientes: -4 a -12).

## Restaurar una versión

Cuando el humano dice "volvé a la v3", "la anterior era mejor" o el A/B (`ab_choice`) eligió
una vieja: `versions.restore(version="v003")`. Copia byte a byte su `song.yaml` (y `midi/`),
guarda antes tu trabajo sin commitear y devuelve un `undo`. No crea versión: el próximo
`render` cuelga de v003 (la historia es un árbol). También restaurá si dos iteraciones
seguidas empeoraron lo que él marcó. Para comparar sin restaurar: `versions.diff`.

## Honestidad

- Decí siempre qué es medido, qué es estimado y qué no se puede medir.
- Si una métrica contradice al humano, gana el humano; contale qué medía la métrica.
- Si no entendiste el pedido, preguntá antes de renderizar (ver [feedback.md](feedback.md)).
- Si una herramienta devuelve `not_implemented` o falla, decilo; no simules el resultado.
