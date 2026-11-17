# Problemas frecuentes

## Entorno

- **`muestra doctor` dice ffmpeg missing**: solo afecta mp3/m4a (WAV/FLAC andan). Instalarlo
  (`winget install Gyan.FFmpeg` / `brew install ffmpeg`) o `MUESTRA_FFMPEG=<ruta al exe>`;
  reiniciar la terminal. Mientras tanto pedile al humano el WAV.
- **`library_not_indexed` / `no_library`**: `library.index` (incremental; ~37 s la primera
  vez para 4.661 archivos, ~1 s después). Raíces: `[library] roots` en `muestra.toml`,
  `MUESTRA_LIBRARY`, `~/Documents/MuestraLibrary`.
- **`not_implemented`**: esa parte no está en este build. Decilo; no simules el resultado.
- **Error inesperado (`internal_error`)**: el detalle está en `<proyecto>/cache/logs/muestra.log`.
- **Proyecto equivocado**: el resultado MCP trae `_meta.project`; `project.open` con ruta
  absoluta. Por CLI siempre `--project`.
- **`repo_locked` / `repo_busy` / `detached_head`**: git del proyecto en un estado raro
  (otro proceso, rebase a medias). No borres `.git/index.lock` si hay un render corriendo.
- **Separación lenta**: normal en CPU (~9 min un tema de 4 min). No la repitas: es caché.
  `.muestra-ingest.lock` colgado después de un crash: borralo solo si no corre nada.

## Análisis

### El tracker midió doble tempo

`bpm_global` 200-260 en un tema que se siente a 100-130 (pasa con 2/4 rápidos y discos
de los 20-30): cada beat es una corchea. Usá doble grilla
([recipes/future-funk.md](recipes/future-funk.md#fuente-a-doble-tempo)). Al revés
(`bpm_global` 60-75 en algo bailable): el tracker contó blancas. Mismo truco invertido:
`meta.bpm` a la mitad y `steps_per_bar: 32` (32 caracteres = 1 compás musical). Avisale al
humano que la grilla es rara.

- **El análisis falla por compás** (menos de 75 % de compases de 4 beats): 3/4, swing
  fuerte, intro sin pulso. El análisis es del archivo entero: avisá y proponé otro tema, o
  que el humano recorte en WAV la parte que quiere y la importás como fuente nueva.
- **`tempo_variable: true`**: normal en discos sin click. Por eso cada chop tiene su
  `local_bpm`. Chops largos pueden derivar: `grid_drift` → `stretch` o chop más corto.
- **Una sola sección**: el tema es corto o parejo. Pasá `section: null` y elegí por tiempo
  (`start_s` de cada candidato).

## Chops

- **`find_chops` devuelve menos de `n`**: no hay más candidatos distintos (descarta los que
  se pisan más de 50 %). Probá otro `stem`, `length_beats` o `section`.
- **`chops.choose` rechaza el candidato**: cambió la fuente, los stems o el análisis desde
  que se generó (el set queda viejo). Volvé a correr `find_chops`.
- **El preview suena muy agudo/grave**: los previews usan `meta.bpm`; fijalo antes.
- **`chop_overrun`**: el chop se pasa del final del audio o de la grilla analizada.
- **El corte agarra la mitad de una palabra/frase**: ears no lo ve. `start_offset_beats`
  -1 o +1, o pedile al humano que elija en la UI.

## Render

- **Tarda más de 15 s**: primera vez decodifica y warpea (después hay caché); instrumentos
  Surge arrancan un proceso (~4 s). `range_bars` para probar una parte.
- **`plugin_crashed` / `plugin_timeout`**: probá con `plugins.scan names=[...]`; usá un alias
  SFZ (`finger-bass`, `rhodes`) que no carga plugin nativo.
- **Render OK pero `metrics` es null**: leé `data.hint`; `ears.analyze(version)` lo reintenta.
- **No llega al volumen objetivo**: [ears.md](ears.md#loudness_off_target).
- **Las notas del bajo no suenan en una parte**: la ventana `at_bar`/`until_bar` del track
  las silencia; o las notas no están escritas ahí (no se repiten solas).

## Métricas que se contradicen

- `sample_detuned` previsto vs `tuning_cents` ≈ 0: ver [ears.md](ears.md#sample_detuned).
- `track_transposed` en el sample mientras `bass_vs_sample` dice que el bajo encaja: suele
  ser una relación de quinta (pedal de dominante). No transpongas: preguntá.
- `key_mismatch` cambia entre versiones sin que toques el tono: estimación con confianza
  baja; ignorala si `bass_vs_sample` encaja.

## Contexto del agente

- `plugins.list` devuelve ~540 KB (3.000 presets): no lo llames si alcanzan los alias.
- La respuesta de `render` trae solo `metrics_summary` (~1 KB); el modelo completo de métricas
  (12-20 KB) está en `versions.get` → `metrics`: pedilo solo si hace falta ver `alarms`.
- `library.search` es compacto por defecto; `detail="full"` solo para pocos resultados.
