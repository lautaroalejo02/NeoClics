# Receta: primer loop future funk de 16 compases

Objetivo del v001: el riff del disco en loop, acelerado (más agudo), bombo en negras con el
sample "respirando", clap en 2 y 4, hats a contratiempo, bajo propio en la tonalidad del
sample, intro filtrada que se abre. Sin clicks, en grilla, sin que el bajo choque.
Es un punto de partida: el gusto de Lautaro manda ([styles.md](../styles.md)).

## Decisiones por defecto

| Qué | Valor de partida | Por qué / cuándo cambiarlo |
|---|---|---|
| Chop | `stem: other`, 2 compases (8 beats), `source.find_chops` + pick auto | 1 compás = más Macross; 4 = más Night Tempo |
| Warp | `resample` | la firma del género; `stretch` solo si hay `grid_drift` o el salto es > ~3 st |
| Tempo | sensación 118-130 BPM, cayendo en semitono entero | ver [Tempo y tono](#tempo-y-tono) |
| Sample | `highpass` 150 Hz (order 4), `lowpass` automatizado, `sidechain` depth 0.5 / release 140 | hp 120-200 según cuánto grave tenga el disco |
| Intro | low-pass `exp` de 500 Hz a 18 kHz en los primeros 4 compases | 8 compases = más french house |
| Batería | kit `disco-01`: bombo en track `kick` desde el 1, clap/hats en `drums` desde el 5 | `kick` aparte para que el sidechain no reaccione a los hats |
| Hats | cerrado `x.o.x.o.x.o.x.o.` -12 dB, abierto `..x...x...x...x.` -10 dB | swing 0.1-0.25 para más groove |
| Bajo | `finger-bass`, contratiempos: tónica, tónica, octava, tónica; entra en el 5 | `synth-bass` si falta peso; `slap-bass` para funk (ojo clicks) |
| Master | compresor -18 dB 3:1, limitador -1 dB, `target_lufs: -12` | -10 solo si ears muestra PLR < 9 dB |

Niveles: los discos viejos vienen bajos (Tiger Rag: -27 LUFS) y los TR-8 calientes. Empezá
con sample `gain_db` +6..+10, bombo -4, bajo +6..+13 (el alias `finger-bass` trae 6 dB de
headroom) y ajustá con lo que diga ears (`technical.tracks.<id>.rms_dbfs`).

## Tempo y tono

Con `resample`, el chop se reproduce a velocidad `meta.bpm / local_bpm`:

```
semitonos S = 12 · log2(meta.bpm / local_bpm)
```

`local_bpm` lo da `source.find_chops` (por candidato) o ears (`tonal.detune[].local_bpm`);
depende del largo del chop. Los instrumentos (bajo, Rhodes) están afinados en La 440 y no
se pueden desafinar, así que **el sample tiene que caer en un semitono entero**:

1. Elegí `n` = semitonos enteros que querés subir (+1 a +2 es lo típico; 0 si ya está en
   tempo).
2. `meta.bpm = local_bpm · 2^((n - d/100)/12)`, donde `d` = cents que el disco está corrido
   de La 440 (0 si no lo sabés; lo mide ears: ver abajo).
3. Tonalidad nueva = tonalidad del análisis + `n`. Ahí va el bajo.
4. Si no querés tocar el tempo: dejá `meta.bpm` y corregí con
   `warp.pitch_semitones = n - (S + d/100)` (no cambia la duración, pero procesa el audio).

Bajar o subir el tempo después mueve todo: `ΔS = 12·log2(bpm_nuevo/bpm_viejo)`; con un
semitono menos (`bpm · 2^(-1/12)`, -5.6 %) el sample baja medio tono y transponés el bajo -1.

Dos trampas medidas:

- **Chops del mismo lugar con distinto largo tienen distinto `local_bpm`** en un disco
  tocado en vivo (Tiger Rag desde el downbeat 17: 2 beats 240.0, 8 beats 244.9, 16 beats
  246.15, 32 beats 246.8). Calculá con el chop que realmente usás. Para un stutter de otro
  largo, igualalo al principal con `pitch_semitones = 12·log2(local_stutter/local_principal)`
  (ej. 12·log2(240.0/244.9) = -0.35).
- **Discos que no están en La 440** (cinta, 78 rpm): ears lo mide en `tonal.tracks.<sample>.
  tuning_cents`. Si da -40 en la fuente, en el stem y en el render, el disco está 40 cents
  bajo: `d = -40`.

### Ejemplo resuelto (Tiger Rag, real)

- c1 (16 beats desde el downbeat 17): `local_bpm` 246.15. Análisis: F mayor, confianza 0.20.
- Ingenuo: sensación 128 → `meta.bpm` 256 → S = 12·log2(256/246.15) = +0.68 st: 32 cents
  entre dos notas. El bajo batiría.
- v001: n = 1 → 246.15 · 2^(1/12) = **260.79** (sensación 130.4). ears: el sample mide
  **-43 cents** (la fuente sola, -45): el disco está 45 cents bajo, así que suena a +0.55.
- v002: d = -45, n = 0 → 246.15 · 2^(0.45/12) = **252.64** (sensación 126.3). El sample
  ahora mide +2 cents. Otras opciones afinadas: 238.46 (119.2) o 267.66 (133.8).
- Alternativa sin cambiar el tempo de v001: `pitch_semitones: 0.45`.

### El bajo en la tonalidad del sample

La tonalidad del análisis es una pista, no un dato (en discos viejos la confianza suele ser
< 0.4 y la estimación cambia entre versiones). Proceso:

1. Primer bajo: tónica estimada + `n`, patrón simple de contratiempos (pedal).
2. Render → mirá `tonal.bass_vs_sample`: `fit` (0..1) y `best_shift_semitones`. Si
   `best_shift` ≠ 0 y `best_fit` es claramente mayor, transponé el bajo esa cantidad.
3. Cuando el bajo encaja (`best_shift: 0`), poné `meta.key` en la tónica del bajo para que
   `track_transposed` no lo marque. Si el bajo está en la quinta del sample (pedal de
   dominante) ears lo marca igual: es normal, contalo y dejá que el humano decida.

En Tiger Rag: F# (v001) → `best_shift +2`; C (v002) → `-5`; G (v003) → encaja (fit 0.58).
`meta.key: G` en v004.

## Fuente a doble tempo

Si `bpm_global` da ~200-260 en un tema que se siente a 100-130, el tracker contó
corcheas. Muestra no puede mapear 2 beats del disco a 1 del proyecto (anotado en
`docs/skill-findings.md`), así que trabajá en **doble grilla**:

| Musical | En `song.yaml` |
|---|---|
| sensación 126 BPM | `meta.bpm: 252.64` |
| 16 compases | `length_bars: 32` |
| compás musical m | compás del proyecto `2m - 1` |
| negra musical k (desde el inicio) | beat del proyecto `2k - 1` |
| una corchea | 1 beat |
| patrón de 16 semicorcheas | `steps_per_bar: 8`, `bars: 2` (mismas cadenas de 16 caracteres) |
| chop de 2 compases musicales | `length_beats: 16` |

`release_ms`, `fade_ms` y efectos en ms no cambian. `delay.time_beats` sí (1 = corchea).
Decile al humano los compases musicales, no los del proyecto (los segundos coinciden).

## Ejemplo completo verificado (Tiger Rag)

[examples/tiger-rag.yaml](../examples/tiger-rag.yaml): fuente "Tiger Rag" (The Home
Towners, 1928, CC0), primeros 30 s, doble grilla. Renderizado en un proyecto de prueba
(v004 y de nuevo v009, idénticos): 4.8-5.8 s de render, -13.1 LUFS (target -12), pico
-1.0 dBTP, PLR 12.2, sidechain medido 5.6 dB con 178 ms de recuperación, sin clicks ni
choques de graves. Avisos que quedan: `sample_detuned` *previsto* (falso: ver
[ears.md](../ears.md#sample_detuned)) y `spectral_sub_deficit` (pregunta para el humano).
Nadie lo escuchó.

Para un disco que el tracker mide bien, la misma receta en grilla normal:
[examples/template.yaml](../examples/template.yaml) (valida y renderiza; sobre Tiger Rag
suena una octava abajo, solo sirve como estructura). Lo único que cambia es la tabla de
arriba.

Cómo se llegó a la versión presentada (autocorrecciones antes de mostrar):

| Versión | Cambio | Qué mostró ears |
|---|---|---|
| v001 | receta a 260.79, bajo en F# | -13.2 LUFS vs -10; sample a -43 cents (medición débil); bajo encajaría +2 |
| v002 | 252.64, bajo en C, más sample y bajo | bajo encajaría -5; `sample_detuned` previsto; falta sub |
| v003 | bajo en G | `bass_sample_clash` resuelto; `track_transposed` (meta.key C) |
| v004 | `meta.key: G`, target -12, master sin empuje | 2 avisos, ningún problema: se presenta |

## Tres iteraciones resueltas

Los diffs se aplican en orden sobre `examples/tiger-rag.yaml`; `~ a => b` = reemplazar en
todas las notas. Los renders son v005-v008 del proyecto de prueba. Los comentarios del
humano fueron **simulados** para la prueba.

### 1. "me gusta pero el bajo más gordo"

Razonamiento: "gordo" = más cuerpo y sostén, no necesariamente más volumen. ears ya decía
que faltaba sub. Cambio solo el bajo: notas más largas (0.4 → 0.6 de negra) y un poco más
fuertes, saturación suave para armónicos, y un sidechain para que las notas largas no pisen
el bombo.

```diff tiger-rag
~ length_beats: 0.8, velocity: 100} => length_beats: 1.2, velocity: 104}
~ length_beats: 0.8, velocity: 92} => length_beats: 1.2, velocity: 96}
-  gain_db: 13
+  fx:
+  - {id: sat, type: saturation, drive_db: 6}
+  - {id: sc, type: sidechain, key_track: kick, depth: 0.4, release_ms: 90}
+  gain_db: 13
```

v005, `summary`: "Bajo más gordo: notas más largas y un poco más fuertes, saturación suave
para que tenga cuerpo en parlantes chicos, y se agacha con el bombo para no pisarlo".
ears: bajo +6.9 dB RMS, 6 de 64 bombos se tocan con el bajo (bajo el umbral), mix 2.5 dB
más oscuro arriba, sigue `spectral_sub_deficit`. A Lautaro: "Si todavía lo sentís flaco abajo,
el próximo paso es cambiarlo por un bajo de synth, que tiene más fundamental".

### 2. "acá el bombo se come al sample" (comentario en v004, `at_s: 12`)

Ubicación: 12 s × 252.64/60 = beat 50.5 → compás 13 del proyecto = compás musical 7, con la
batería completa. ears no marca choque de graves entre bombo y sample (el sample tiene
high-pass en 150 Hz), así que lo que se oye es el bombeo y el nivel del bombo. Cambio dos
cosas del mismo tema: sidechain menos profundo y más rápido, bombo 2 dB abajo.

```diff tiger-rag
-  - {id: sc, type: sidechain, key_track: kick, depth: 0.5, release_ms: 140}
+  - {id: sc, type: sidechain, key_track: kick, depth: 0.35, release_ms: 100}
-      kick: {sample: "library://kits/tr8-hardware-machines/roland-tr-8/kick-tr-8-kick-02.wav", steps: "x...x...x...x...", gain_db: -4}
+      kick: {sample: "library://kits/tr8-hardware-machines/roland-tr-8/kick-tr-8-kick-02.wav", steps: "x...x...x...x...", gain_db: -6}
```

v006: el sample se agacha 3.6 dB (antes 5.6) y vuelve en 104 ms. Pero ears marcó un problema
nuevo: con el bombo más bajo, el bajo largo de la iteración 1 ahora lo tapa
(`low_end_collision`, 35 de 64 golpes) y hay barro en 250-500 Hz. No se muestra; se corrige:

```diff tiger-rag
-  - {id: sat, type: saturation, drive_db: 6}
-  - {id: sc, type: sidechain, key_track: kick, depth: 0.4, release_ms: 90}
+  - {id: sat, type: saturation, drive_db: 4}
+  - {id: sc, type: sidechain, key_track: kick, depth: 0.6, release_ms: 90}
```

v007: 0 choques, vuelven los 2 avisos de siempre. A Lautaro: "En v007 el sample respira
menos con el bombo y el bombo está un poco más abajo. Fijate a 0:12 si el riff ahora queda
adelante. (v006 fue un paso intermedio que tapaba el bajo; no hace falta escucharla)".

### 3. "está muy acelerado"

Razonamiento: bajar el tempo con resample también baja el tono. Para seguir afinado bajo
exactamente un semitono: 246.15 · 2^(-0.55/12) ≈ 238.45 (sensación 126.3 → 119.2) y
transpongo el bajo de G a F#. `meta.key` acompaña.

```diff tiger-rag
-  bpm: 252.64  # 246.15 (tempo local de c1) x 2^(0.45/12): el disco esta 45 cents bajo y +0.45 st lo afina; sensacion 126.3
-  key: G  # donde encaja el bajo con el sample (ears: bass_vs_sample)
+  bpm: 238.45  # 246.15 x 2^(-0.55/12): un semitono menos que 252.64, sigue afinado; sensacion 119.2
+  key: F#  # bajo un semitono con el tempo
~ pitch: G1, => pitch: F#1,
~ pitch: G2, => pitch: F#2,
```

v008, `summary`: "Más lento: de 126 a 119 de sensación. Como el sample va a velocidad de
vinilo, también baja medio tono (de Sol a Fa#), así que bajé el bajo igual para que sigan
juntos". ears: el sample mide +1.8 cents, el bajo encaja (fit 0.47), sin alarmas nuevas.
A Lautaro: "Al bajar el tempo el sample también quedó medio tono más grave, como un disco
más lento. Si querés más lento pero con el tono de antes, lo hago con stretch (pierde un
poco del color de vinilo)".

## Variaciones de estilo (todas renderizadas)

| Archivo | Perfil | Render | ears |
|---|---|---|---|
| [tiger-rag-macross.yaml](../examples/tiger-rag-macross.yaml) | Macross 82-99: corte de 1 compás + stutter, +1.45 st (133 de sensación), saturación, sidechain 0.7 | v014, 5.9 s | -12.1 LUFS; `track_transposed` del sample (relación de quinta, ver ears.md), detune previsto, falta sub |
| [tiger-rag-yungbae.yaml](../examples/tiger-rag-yungbae.yaml) | Yung Bae: swing 0.2, bajo con notas de paso, chorus, break filtrado, más partes | v017, 5.4 s | -13.2 LUFS; detune previsto, falta sub |
| [tiger-rag-nighttempo.yaml](../examples/tiger-rag-nighttempo.yaml) | Night Tempo: frase de 4 compases del disco entero (`mix`), `stretch`, sidechain 0.25 | v016, 7.3 s | -12.2 LUFS; oscuro arriba (es el disco de 1928), falta sub, detune previsto |

Los tres: sin clicks, sin choques, sin problemas de grilla. Qué cambia en cada perfil y por
qué: [styles.md](../styles.md).
