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

### La grilla está mal (doble/mitad de tempo, el uno corrido)

Es una propiedad del análisis, no de cada chop: se corrige **una vez** en la fuente y todo lo
demás (chops, warp, previews, ears) la sigue. `source.analyze` ya elige la octava que cae en
70-160 BPM (Tiger Rag: el tracker lee 250, queda en 125) y dice en `grid.reason` qué hizo.
Si igual está mal:

- "va al doble" / "los clicks corren": `source.regrid tempo_factor=0.5`; "va a la mitad":
  `tempo_factor=2` (o `1` si ya estaba en 0.5). Los parámetros son **absolutos** respecto
  del tracker: repetir la llamada no cambia nada; `reset=true` vuelve a la lectura cruda.
- "el uno está corrido" / "el click agudo cae en el 3": `downbeat_shift_beats` (+1 = un
  tiempo más tarde). Con `tempo_factor: 0.5` el uno es una apuesta 50/50: la alternativa
  `downbeat_shift_beats: 2` viene en `grid.alternatives`.
- "los clicks caen entre los golpes": `beat_phase` (la otra mitad de los beats).
- Antes de aplicar, se puede escuchar sin cambiar nada: `source.preview_grid` con los mismos
  parámetros (o `alternatives=true`).
- `regrid` reescribe los chops de `song.yaml` de esa fuente para que corten **el mismo audio**
  y lo informa chop por chop (`chops[].note`); los sets de candidatos viejos se archivan
  (corré `source.find_chops` de nuevo). Si cambió el tempo, `suggested_bpm` dice qué
  `meta.bpm` mantiene la velocidad; mejor rehacer el tema en la grilla nueva.

**Nunca** dupliques `meta.bpm` ni uses `steps_per_bar: 8` + `bars: 2` para compensar una
grilla mal leída: la grilla del proyecto tiene que ser la musical.

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
  las silencia; las `notes` sueltas no se repiten solas (para eso, `phrases`); una frase con
  `repeat` termina después de esas pasadas.

## Métricas que se contradicen

- `sample_detuned` previsto vs `tuning_cents` ≈ 0: ver [ears.md](ears.md#sample_detuned).
- `track_transposed` en el sample mientras `bass_vs_sample` dice que el bajo encaja: suele
  ser una relación de quinta (pedal de dominante). No transpongas: preguntá.
- `key_mismatch` cambia entre versiones sin que toques el tono: estimación con confianza
  baja; ignorala si `bass_vs_sample` encaja.

## Contexto del agente

- `plugins.list` sin filtros da los alias, los plugins y cuántos presets hay por categoría
  (~3 KB); para ver presets, `plugin`/`category`/`query` (30 por página, `next_offset`).
- La respuesta de `render`, `versions.get` y `ears.analyze` trae solo el resumen de ears
  (~1 KB) y `metrics_path`; el modelo completo (12-20 KB) es `versions.get detail=true`:
  pedilo solo si hace falta ver `alarms` en detalle.
- `library.search` es compacto por defecto; `detail="full"` solo para pocos resultados.
