# `song.yaml`: referencia práctica

Fuente de verdad del arreglo. Campos desconocidos son error. Todo lo de acá valida contra
el schema real (`tests/skill` lo comprueba). Los fragmentos asumen un tema con `src1`, el
chop `c1` y tracks `sample`, `drums` (con lane `kick`) y `bass`.

## Esqueleto

```yaml song
schema_version: 0
meta:
  title: borrador-01
  bpm: 124.0          # tempo del proyecto (no del disco)
  key: Am             # canónico: F# (mayor) / F#m (menor); referencia para ears
  length_bars: 16
  seed: 42            # toda aleatoriedad sale de acá
sources:
- id: src1
  file: sources/src1.wav     # lo agrega source.import
chops: []
tracks: []
automation: []
master:
  fx: []
  target_lufs: -12
```

Posiciones: compás `n` empieza en el beat `(n-1)*4 + 1`. Solo 4/4.

## Chops

```yaml fragment
chops:
- id: c1
  source: src1
  stem: other            # drums | bass | vocals | other | mix (mix = disco original)
  start_downbeat: 17     # índice 1-based en analysis.downbeats_s
  start_offset_beats: 0  # corre el inicio por la grilla del disco (puede ser -0.5, 2...)
  length_beats: 8        # beats de la fuente que se toman = beats que ocupa en el proyecto
  warp: {mode: resample, pitch_semitones: 0}
  fade_ms: 8             # 5 por defecto; subilo si ears marca click
```

- `warp.mode: resample` (vinilo): velocidad = `meta.bpm / local_bpm`; el tono sube o baja
  con el tempo: `semitonos = 12·log2(meta.bpm/local_bpm)`. Es el sonido del género.
- `warp.mode: stretch`: Rubber Band beat a beat, conserva el tono. Usalo si ears marca
  `grid_drift` (disco que se acelera) o si el cambio de tempo es grande (> ~3 semitonos).
- `pitch_semitones` (-24..24, decimales ok): corrimiento extra que **no** cambia la duración.
- `local_bpm = 60·length_beats/(end_s-start_s)` depende del largo: dos chops del mismo lugar
  con distinto largo pueden tener distinto `local_bpm` (y distinto tono en resample).
- `start_downbeat` y `length_beats` cuentan en la grilla **corregida** del análisis (la de
  `source.analyze`/`source.regrid`). Si un chop de 8 beats dura la mitad de lo esperado, la
  grilla está al doble: se arregla con `source.regrid`, que además reescribe estos campos.

Stutter (re-disparar un pedacito):

```yaml fragment
chops:
- id: hit
  source: src1
  stem: other
  start_downbeat: 17
  length_beats: 1        # una negra
  warp: {mode: resample, pitch_semitones: 0}
  fade_ms: 4
tracks:
- id: sample
  clips:
  - {chop: c1, at_bar: 1, repeat: 3}       # compases 1-6
  - {chop: hit, at_bar: 7, repeat: 8}      # 8 negras: compases 7-8
  - {chop: c1, at_bar: 9, repeat: 4}
```

## Clips

`{chop | sample, at_bar, offset_beats, repeat}`. Posición = `(at_bar-1)*4 + offset_beats`.
Cada repetición dura `length_beats` del chop. Pasarse de `length_bars` es error
(`clip_overrun`). Clips superpuestos se suman.

```yaml fragment
tracks:
- id: sample
  clips:
  - {chop: c1, at_bar: 1, repeat: 4}
  - {chop: c1, at_bar: 9, offset_beats: -0.5, repeat: 4}   # entra una corchea antes
```

Sample de librería: necesita `length_beats` (se corta, no se repite: usá `repeat`). `warp`
solo si la librería tiene su BPM (loops); un one-shot suena a su velocidad.

```yaml fragment
tracks:
- id: fx-crash
  clips:
  - {sample: "library://kits/tr8-hardware-machines/roland-tr-8/crash-tr-8-crash-02.wav", length_beats: 4, at_bar: 5}
  gain_db: -10
```

### Cortar, recortar y editar una sola repetición (WP16)

Para cambiar UNA pasada de un clip repetido no lo desarmes: `instances` (claves 1-based =
número de repetición) silencia, baja, sube de tono o da vuelta solo esa pasada. En un clip
de chop, `length_beats` lo recorta (cada pasada dura eso) y `trim_start_beats` saltea el
comienzo del material; `stretch` lo estira sin cambiar el tono (una pasada usa
`length_beats / stretch` beats de material). También `gain_db`, `reverse` y
`fade_in_ms`/`fade_out_ms` por clip.

```yaml fragment
tracks:
- id: sample
  clips:
  - {chop: c1, at_bar: 1, repeat: 8, instances: {3: {mute: true}, 6: {pitch_semitones: 12}}}
- id: stutter
  clips:
  - {chop: c1, at_bar: 13, repeat: 4, length_beats: 1, trim_start_beats: 2, gain_db: -3}
  - {chop: c1, at_bar: 15, length_beats: 8, stretch: 2, reverse: true, fade_out_ms: 400}
```

El editor del browser escribe esto mismo (y `song.edit` desde un agente): son las formas
canónicas, mantenelas así en vez de desarmar el clip en muchos.

## Patrones de batería

```yaml fragment
tracks:
- id: drums
  at_bar: 5              # entra en el compás 5 (ventana: at_bar..until_bar, until exclusivo)
  pattern:
    steps_per_bar: 16
    bars: 1              # el patrón se repite cada `bars` compases
    swing: 0.0
    lanes:
      kick: {sample: "library://kits/tr8-hardware-machines/roland-tr-8/kick-tr-8-kick-02.wav", steps: "x...x...x...x...", gain_db: -4}
      clap: {sample: "library://kits/essential-kits-musicradar/tr-909-kit/clap-tr-909-clap.wav", steps: "....x.......x...", gain_db: -5}
      chat: {sample: "library://kits/tr8-hardware-machines/roland-tr-8/chat-tr-8-closed-hat-01.wav", steps: "x.o.x.o.x.o.x.o.", gain_db: -12, pan: -0.2}
      ohat: {sample: "library://kits/tr8-hardware-machines/roland-tr-8/ohat-tr-8-open-hat-01.wav", steps: "..x...x...x...x.", gain_db: -10}
```

Gramática de `steps`: `x`/`X` golpe (1.0), `o` fantasma (0.6), `1`-`9` velocidad n/9,
`.`/`-`/`_` silencio, espacios ignorados. Largo exacto = `steps_per_bar * bars`.

- `swing` 0..1 atrasa los pasos impares `swing·paso/3`: 0.1-0.25 = groove sutil, 0.66 ≈
  shuffle, 1 = tresillo. Se aplica a todos los lanes del track.
- Variación cada 2 compases (fill al final), con `bars: 2`:

```yaml fragment
tracks:
- id: drums
  pattern:
    steps_per_bar: 16
    bars: 2
    lanes:
      kick: {sample: "library://kits/tr8-hardware-machines/roland-tr-8/kick-tr-8-kick-02.wav", steps: "x...x...x...x... x...x...x...x.x."}
      clap: {sample: "library://kits/essential-kits-musicradar/tr-909-kit/clap-tr-909-clap.wav", steps: "....x.......x... ....x.......x.xx"}
```

Patrones útiles (16 pasos): four-on-the-floor `x...x...x...x...`; clap 2 y 4
`....x.......x...`; hat abierto a contratiempo `..x...x...x...x.`; hats 16avos con acento
`x.o.x.o.x.o.x.o.` / `xoxoxoxoxoxoxoxo`; bombo disco con empuje `x...x...x...x..x`.
Para que un lane suene en otra ventana que el resto, ponelo en otro track (ej. `kick` solo
desde el compás 1 y `drums` desde el 5): el sidechain puede apuntar a ese track.

### Una excepción en un compás y golpes sueltos (WP16)

`overrides` cambia UN compás de un lane sin tocar el patrón: la clave es el compás de la
CANCIÓN (1-indexado) y el valor los pasos de ese compás (`steps_per_bar` caracteres; todo
`.` = ese lane calla en ese compás). `hits` son golpes fuera de la grilla:
`{beat, lane, velocity?, sample?}` con `beat` 1-indexado desde el inicio del tema (como las
notas); el swing no los mueve. Un lane puede no tener `steps` si solo tiene overrides/hits.

```yaml fragment
tracks:
- id: drums
  pattern:
    steps_per_bar: 16
    lanes:
      kick: {sample: "library://kits/tr8-hardware-machines/roland-tr-8/kick-tr-8-kick-02.wav", steps: "x...x...x...x...", overrides: {7: "x...x.......x...", 16: "x...x...x...x.xx"}}
      clap: {sample: "library://kits/essential-kits-musicradar/tr-909-kit/clap-tr-909-clap.wav", steps: "....x.......x...", overrides: {8: "................"}}
    hits:
    - {beat: 30.5, lane: clap, velocity: 0.6}
    - {beat: 63.75, lane: kick, sample: "library://kits/essential-kits-musicradar/tr-909-kit/clap-tr-909-clap.wav"}
```

Lanes que suenan un pedazo de chop (`slice`), afinación de sampler (`pitch_semitones`),
`reverse`, grupos de corte (`choke`) y golpes con duración (`length_beats`): lo que escriben
los pads, ver [pads.md](pads.md).

"Sacá el bombo del tercer tiempo del compás 7" es exactamente el override de arriba (o
`song.edit` con `{op: hit, track: drums, lane: kick, bar: 7, step: 8, velocity: null}`,
paso 0-based dentro del compás). El diff lo describe así: "Saqué el bombo de la batería
del tercer tiempo del compás 7".

## Instrumentos: frases que se repiten (lo normal)

Una línea de bajo de 2 compases que suena 12 compases se escribe una vez, como frase:

```yaml fragment
phrases:
- id: bajo                     # el nombre que usan los tracks
  length_bars: 2               # se repite cada 2 compases
  notes:                       # beat 1 = primer tiempo DE LA FRASE
  - {beat: 1.5, pitch: A1, length_beats: 0.4, velocity: 100}   # "y" del 1
  - {beat: 2.5, pitch: A1, length_beats: 0.4, velocity: 92}
  - {beat: 3.5, pitch: A2, length_beats: 0.4, velocity: 100}
  - {beat: 4.5, pitch: A1, length_beats: 0.4, velocity: 92}
  - {beat: 5.5, pitch: C2, length_beats: 0.4, velocity: 100}   # compás 2 de la frase
  - {beat: 7.5, pitch: E2, length_beats: 0.9, velocity: 96}
- id: remate
  length_bars: 1
  notes:
  - {beat: 1.5, pitch: A1, length_beats: 0.4}
  - {beat: 3, pitch: G2, length_beats: 0.25}
  - {beat: 3.5, pitch: E2, length_beats: 0.25}
  - {beat: 4, pitch: C2, length_beats: 0.25}
tracks:
- id: bass
  at_bar: 5
  instrument: {plugin: finger-bass}     # alias: finger-bass, slap-bass, synth-bass, rhodes, brass-stab, pad
  phrases:
  - {phrase: bajo, repeat: 4}                                       # compases 5-12
  - {phrase: bajo, at_bar: 13, repeat: 1, transpose_semitones: 5}   # 13-14, una cuarta arriba
  - {phrase: remate, at_bar: 15}                                    # sin repeat: 15 y 16 (hasta el final)
  gain_db: 6
```

- `phrases[].notes[].beat` cuenta **dentro de la frase** (1 = su primer tiempo; tiene que ser
  < `length_bars`·4 + 1). `pitch` número MIDI o nombre (`C4` = 60). Bajo eléctrico: E1-G3.
- En el track: `phrase` (id), `at_bar` (default: el `at_bar` del track), `repeat` (pasadas;
  **sin `repeat` se repite hasta el `until_bar` del track o el final**, la última cortada) y
  `transpose_semitones` (-24..24). Una pasada que se pasa de la ventana es error.
- Variar: otra frase en otro compás (fill, remate), una frase transpuesta para seguir un
  cambio de acorde, o `notes` sueltas en el mismo track encima de las frases.
- `versions.diff` lo cuenta así: "El bajo ahora repite la frase bajo de 2 compases desde el
  compás 5"; cambiar una nota de la frase es un cambio, no 6.

## Instrumentos: notas sueltas y MIDI

```yaml fragment
tracks:
- id: bass
  at_bar: 5
  instrument: {plugin: finger-bass}
  notes:
  - {beat: 17.5, pitch: A1, length_beats: 0.4, velocity: 100}   # compás 5, "y" del 1
  - {beat: 18.5, pitch: A1, length_beats: 0.4, velocity: 92}
  gain_db: 6
```

- En `notes`, `beat` es absoluto desde el inicio del tema (compás 5 = beat 17) y **no se
  repite**: sirve para algo que pasa una vez. Para loops, `phrases`.
- `midi: midi/bass.mid` (ticks = beats del proyecto; ignora tempo del archivo) va solo: no
  se combina con `notes` ni `phrases`.
- La ventana `at_bar`/`until_bar` silencia notas fuera de ella (no las mueve).
- Preset de Surge directo: `instrument: {plugin: surge-xt, preset: "Basses/FM Slap", params:
  {global_volume: -12}}`. Para buscar presets: `plugins.list plugin=surge-xt category=Basses` o `query="slap bass"` (paginado; sin filtros solo da alias y conteos).

Acordes de Rhodes (notas simultáneas = mismo `beat`):

```yaml fragment
tracks:
- id: keys
  at_bar: 9
  instrument: {plugin: rhodes}
  notes:
  - {beat: 34.5, pitch: A3, length_beats: 1.5, velocity: 80}
  - {beat: 34.5, pitch: C4, length_beats: 1.5, velocity: 80}
  - {beat: 34.5, pitch: E4, length_beats: 1.5, velocity: 80}
  - {beat: 34.5, pitch: G4, length_beats: 1.5, velocity: 80}
  fx:
  - {type: chorus, rate_hz: 0.6, depth: 0.3, mix: 0.3}
  gain_db: -9
```

## Efectos

Cadena en orden. `id` es obligatorio si lo vas a automatizar (único por track).

```yaml fragment
tracks:
- id: sample
  clips:
  - {chop: c1, at_bar: 1, repeat: 8}
  fx:
  - {id: hp, type: highpass, cutoff_hz: 150, order: 4}       # order 2|4|6|8 (pendiente 12-48 dB/oct)
  - {id: lp, type: lowpass, cutoff_hz: 18000}
  - {id: sc, type: sidechain, key_track: drums, key_lane: kick, depth: 0.5, attack_ms: 1, release_ms: 140}
  - {id: comp, type: compressor, threshold_db: -18, ratio: 3, attack_ms: 10, release_ms: 100, knee_db: 6, makeup_gain_db: 0}
  - {id: sat, type: saturation, drive_db: 4}
  - {id: ph, type: phaser, rate_hz: 0.3, depth: 0.5, centre_frequency_hz: 1300, feedback: 0.2, mix: 0.3}
  - {id: ch, type: chorus, rate_hz: 0.5, depth: 0.25, centre_delay_ms: 7, feedback: 0, mix: 0.25}
  - {id: dly, type: delay, time_beats: 0.75, feedback: 0.3, mix: 0.2}   # corchea con puntillo
  - {id: rev, type: reverb, room_size: 0.4, damping: 0.5, wet_level: 0.15, dry_level: 0.8, width: 1.0}
  - {id: g, type: gain, gain_db: -2}
  - {id: lim, type: limiter, ceiling_db: -1, release_ms: 50}
  gain_db: 0
  pan: 0
```

Plugin de efecto (parámetros en unidades nativas):

```yaml fragment
tracks:
- id: bass
  instrument: {plugin: finger-bass}
  notes:
  - {beat: 1.5, pitch: A1, length_beats: 0.4}
  fx:
  - {id: surgefx, type: plugin, plugin: Surge XT Effects, params: {output_mix: 0.3}}
```

### Sidechain

- `key_track` = track que dispara (no el mismo). `key_lane` restringe a un lane de un
  pattern (`kick`): sin él, cualquier golpe del track (clap, hats) también agacha.
- Disparo por eventos: `ganancia = 1 - depth·env`. `depth` 0.5 ≈ 6 dB de bajada nominal (ears
  mide ~75 % de eso). `release_ms` 100-180 a 115-130 BPM; más largo = bombeo más marcado.
- Si el bombo está en un track aparte (`kick`), `key_track: kick` sin `key_lane`.
- Ciclos (A agacha con B y B con A) son error.

## Automatización

```yaml fragment
automation:
- {target: tracks.sample.fx.lp.cutoff_hz, curve: exp, points: [[1, 500], [5, 18000]]}       # abre en 4 compases
- {target: tracks.sample.fx.hp.cutoff_hz, curve: exp, points: [[13, 150], [17, 1200]]}      # vacía graves antes del final
- {target: tracks.sample.gain_db, curve: lin, points: [[1, -6], [3, 0]]}
- {target: tracks.bass.pan, curve: step, points: [[1, 0], [9, -0.1]]}
- {target: tracks.sample.fx.sc.depth, curve: lin, points: [[1, 0.3], [9, 0.6]]}       # el bombeo crece
- {target: master.gain_db, curve: lin, points: [[15, 0], [17, -3]]}
```

- Targets: `tracks.<id>.gain_db | .pan | .fx.<fxid>.<param>`, `tracks.<id>.fx.<fxid>.params.<nombre>`
  (plugins), `master.gain_db | master.fx.<fxid>.<param>`.
- `points` = `[compás, valor]`, compás decimal permitido, entre 1 y `length_bars + 1`
  (= final). Dos puntos en el mismo compás = salto instantáneo.
- `curve`: `exp` para frecuencias (suena parejo al oído; valores > 0), `lin` para dB/pan/depth,
  `step` para cambios de golpe.
- La automatización **reemplaza** el valor fijo: antes del primer punto vale el primero,
  después del último vale el último. Un target, una automatización.

## Master

```yaml fragment
master:
  fx:
  - {type: compressor, threshold_db: -18, ratio: 3}
  - {type: limiter, ceiling_db: -1.0}
  gain_db: 0
  target_lufs: -12
```

Orden real: fx del master → `gain_db` → ganancia hacia `target_lufs` → limitador de
seguridad (techo = el `ceiling_db` más bajo). Consecuencias: con `target_lufs`, subir
`master.gain_db` no cambia el volumen final; y si el mix tiene mucho pico (PLR > ~10 dB,
típicamente el bombo), el limitador final se come 1-3 LU y no llega al target. Arrancá en
-12 y ver [ears.md](ears.md#loudness_off_target).

## Errores de validación frecuentes

| code | causa típica | arreglo |
|---|---|---|
| `unknown_field` | campo mal escrito (`cutoff` en vez de `cutoff_hz`) | usá el nombre exacto |
| `ref_not_found` | chop/track/source/key_lane que no existe | revisá ids |
| `clip_overrun` | `at_bar + repeat·largo` pasa `length_bars` | menos `repeat` o más compases |
| `chop_overrun` | el chop se sale del audio o de la grilla analizada | otro `start_downbeat` o menos `length_beats` |
| `pattern_length` | `steps` no tiene `steps_per_bar·bars` pasos | contá los caracteres |
| `automation_target` | target sin `id` en el fx o param no numérico | agregá `id` al fx |
| `duplicate_key` | clave repetida en el YAML | dejá una |
| `track_kind` | track con dos contenidos o instrumento sin `notes`/`midi` | uno solo |
