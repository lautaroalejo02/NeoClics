# Receta: primer loop future funk de 16 compases

Objetivo del v001: el riff del disco en loop, acelerado (más agudo), bombo en negras con el
sample "respirando", clap en 2 y 4, hats a contratiempo, bajo propio en la tonalidad del
sample, intro filtrada que se abre. Sin clicks, en grilla, sin que el bajo choque.
Es un punto de partida: el gusto de Lautaro manda ([styles.md](../styles.md)).

## La grilla de la fuente (antes que nada)

Todo se cuelga de la grilla del análisis: dónde empieza cada chop, su `local_bpm`, el warp,
los previews y ears. En discos viejos (tocados sin click, 2/4 rápidos, transfers de 78 rpm) el
tracker suele contar **corcheas** (doble tempo), a veces **blancas** (mitad), o pone **el uno
en otro tiempo**. Eso se arregla en la fuente, una vez, nunca en el tema:

1. `source.analyze` elige solo la octava que cae en 70-160 BPM (`min_bpm`/`max_bpm` para
   otro rango; el future funk anda en 90-130) y guarda la lectura cruda del tracker.
   Mirá `grid` en la respuesta: `raw_bpm`, `bpm`, `reason`, `tempo_confidence`,
   `bar_phase_confidence`, `ask_human` y `alternatives` (listas para pasarle a `regrid`).
2. Si `ask_human` es `true` (siempre que se corrigió la octava: con el tempo a la mitad, cuál
   de los dos "unos" del tracker es el real es 50/50), **que el humano lo escuche**:
   `source.preview_grid(source_id, start_downbeat=<donde vas a cortar>, alternatives=true)`.
   Deja WAVs cortos del disco con un click suave en cada negra y uno agudo y fuerte en cada
   uno; la pestaña Chops de la UI los muestra en "Escuchar la grilla".
3. Explicale qué escuchar, sin jerga, y ofrecele las alternativas por nombre. Por ejemplo:
   > "El programa que detecta el tempo leyó el disco a 250 BPM, el doble de lo que se siente
   > (contó corcheas). Lo dejé en 125. En Chops → Escuchar la grilla tenés el disco con
   > clicks: el suave tiene que caer donde marcarías el pie, y el agudo en el 'uno' de cada
   > compás. ¿Coincide? Si el agudo te cae en el 3, escuchá la segunda opción ('el uno dos
   > tiempos más tarde'); si los clicks van al doble de rápido que tu pie, la de 250."
4. Aplicá lo que diga con `source.regrid` (parámetros **absolutos** respecto del tracker:
   repetir la llamada no cambia nada; `reset=true` vuelve atrás):

   | Él dice | Llamada |
   |---|---|
   | "sí, cae bien" | nada |
   | "los clicks van al doble" | `tempo_factor: 0.5` |
   | "van a la mitad", "muy lentos" | `tempo_factor: 2` (o `1` si estaba en 0.5) |
   | "el agudo cae en el 3" (o "en el 2", "en el 4") | la alternativa con `downbeat_shift_beats` (±1, 2) |
   | "caen entre los golpes" | la alternativa con el otro `beat_phase` |

5. Si ya había un tema armado, `regrid` reescribe sus chops para que corten **el mismo
   audio** y lo cuenta chop por chop (`chops[].note`); los candidatos viejos se archivan (corré
   `source.find_chops` de nuevo). Si cambió el tempo de la fuente, el resultado trae
   `suggested_bpm` (mantiene la velocidad de los chops), pero lo sano es rehacer `meta.bpm`,
   patterns y compases en la grilla nueva con la cuenta de [Tempo y tono](#tempo-y-tono).

Si él no puede escuchar ahora, seguí con la grilla de `source.analyze` y decilo en la
presentación del v001 ("asumí que el uno es X; si se siente corrido, decime").

**Nunca** dupliques `meta.bpm`, ni uses `steps_per_bar: 8` + `bars: 2`, para compensar
una grilla mal leída: la grilla del proyecto es siempre la musical (16 pasos = 1 compás).

## Decisiones por defecto

| Qué | Valor de partida | Por qué / cuándo cambiarlo |
|---|---|---|
| Chop | `stem: other`, 2 compases (8 beats), `source.find_chops` + pick auto | 1 compás = más Macross; 4 = más Night Tempo |
| Warp | `resample` | la firma del género; `stretch` solo si hay `grid_drift` o el salto es > ~3 st |
| Tempo | 118-130 BPM, cayendo en semitono entero | ver [Tempo y tono](#tempo-y-tono) |
| Sample | `highpass` 150 Hz (order 4), `lowpass` automatizado, `sidechain` depth 0.5 / release 140 | hp 120-200 según cuánto grave tenga el disco |
| Intro | low-pass `exp` de 500 Hz a 18 kHz en los primeros 4 compases | 8 compases = más french house |
| Batería | kit `disco-01`, pattern `basic`: bombo en track `kick` desde el 1, clap/hats en `drums` desde el 5 | `kick` aparte para que el sidechain no reaccione a los hats |
| Hats | cerrado `x.o.x.o.x.o.x.o.` -12 dB, abierto `..x...x...x...x.` -10 dB | swing 0.1-0.25 para más groove |
| Bajo | `finger-bass`, una frase de 1-2 compases en loop (`phrases`), contratiempos: tónica, tónica, octava, tónica; entra en el 5 | `synth-bass` si falta peso; `slap-bass` para funk |
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
depende del largo del chop y de que la grilla esté bien (con la grilla al doble, `local_bpm`
sale al doble y el sample sonaría una octava abajo). Los instrumentos (bajo, Rhodes) están
afinados en La 440 y no se pueden desafinar, así que **el sample tiene que caer en un
semitono entero**:

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
  tocado en vivo (Tiger Rag desde el downbeat 9: 1 beat 120.0, 4 beats 122.45, 8 beats
  123.08, 16 beats 123.39). Calculá con el chop que realmente usás. Para un stutter de otro
  largo, igualalo al principal con `pitch_semitones = 12·log2(local_stutter/local_principal)`
  (ej. 12·log2(120.0/122.45) = -0.35).
- **Discos que no están en La 440** (cinta, 78 rpm): ears lo mide en `tonal.tracks.<sample>.
  tuning_cents`. Si da -40 en la fuente, en el stem y en el render, el disco está 40 cents
  bajo: `d = -40`.

### Ejemplo resuelto (Tiger Rag, real)

- Grilla: el tracker lee 250 BPM; `source.analyze` la deja en 125 (`tempo_factor` 0.5).
- c1 (8 beats desde el downbeat 9): `local_bpm` 123.08. Análisis: F mayor, confianza 0.20.
- Ingenuo: 128 → S = 12·log2(128/123.08) = +0.68 st: 32 cents entre dos notas. El bajo
  batiría.
- v001: n = 1 → 123.08 · 2^(1/12) = **130.40**. ears: el sample mide **-43 cents** (la fuente
  sola, -45): el disco está 45 cents bajo, así que suena a +0.55.
- v002: d = -45, n = 0 → 123.08 · 2^(0.45/12) = **126.32**. El sample ahora mide +2 cents.
  Otras opciones afinadas: 119.23 o 133.83.
- Alternativa sin cambiar el tempo de v001: `pitch_semitones: 0.45`.

### El bajo en la tonalidad del sample

La tonalidad del análisis es una pista, no un dato (en discos viejos la confianza suele ser
< 0.4 y la estimación cambia entre versiones). Proceso:

1. Primer bajo: tónica estimada + `n`, frase simple de contratiempos (pedal).
2. Render → mirá `tonal.bass_vs_sample`: `fit` (0..1) y `best_shift_semitones`. Si
   `best_shift` ≠ 0 y `best_fit` es claramente mayor, transponé el bajo esa cantidad
   (`transpose_semitones` en la frase, o las notas de la frase).
3. Cuando el bajo encaja (`best_shift: 0`), poné `meta.key` en la tónica del bajo para que
   `track_transposed` no lo marque. Si el bajo está en la quinta del sample (pedal de
   dominante) ears lo da como info: es normal, contalo y dejá que el humano decida.

En Tiger Rag: F# (v001) → `best_shift +2`; C (v002) → `-5`; G (v003) → encaja (fit 0.58).
`meta.key: G` en v004.

### Frases que se repiten

El bajo (y cualquier instrumento) se escribe como una frase de 1-4 compases que se repite
sola: `phrases` arriba con `{id, length_bars, notes}` (los `beat` cuentan desde el inicio de
la frase) y en el track `phrases: [{phrase: bajo}]` (sin `repeat` se repite hasta el
`until_bar` del track). Variaciones: otra frase en otro compás (`{phrase: fill, at_bar: 16}`),
`transpose_semitones` para seguir un cambio de acorde, o `notes` sueltas encima. Un bajo de
12 compases son ~15 líneas en vez de ~50, y el diff dice "el bajo repite la frase bajo de 1
compás desde el compás 5".

## Ejemplo completo verificado (Tiger Rag)

[examples/tiger-rag.yaml](../examples/tiger-rag.yaml): fuente "Tiger Rag" (The Home
Towners, 1928, CC0), primeros 30 s, grilla corregida a 125 por `source.analyze`.
Renderizado en un proyecto de prueba (v020): 5.3 s de render, -13.1 LUFS (target -12), pico
-1.0 dBTP, PLR 12.2, sin clicks ni choques de graves; el sample cae en la grilla con
desvío mediano -8.5 ms (p90 16 ms). Quedan `spectral_sub_deficit` (pregunta para el humano)
y la info `swing_mismatch` (el disco swinguea las corcheas; la batería no). Nadie lo escuchó.

Para otro disco, la misma estructura: [examples/template.yaml](../examples/template.yaml)
(valida y renderiza).

Cómo se llegó a la versión presentada (autocorrecciones antes de mostrar; medido en la
grilla doble original, mismo audio):

| Versión | Cambio | Qué mostró ears |
|---|---|---|
| v001 | receta a 130.40, bajo en F# | -13.2 LUFS vs -10; sample a -43 cents (medición débil); bajo encajaría +2 |
| v002 | 126.32, bajo en C, más sample y bajo | bajo encajaría -5; falta sub |
| v003 | bajo en G | `bass_sample_clash` resuelto; `track_transposed` (meta.key C) |
| v004 | `meta.key: G`, target -12, master sin empuje | 2 avisos, ningún problema: se presenta |

## Tres iteraciones resueltas

Los diffs se aplican en orden sobre `examples/tiger-rag.yaml`; `~ a => b` = reemplazar en
todas las notas. Los renders son del proyecto de prueba (grilla corregida). Los comentarios
del humano fueron **simulados** para la prueba.

### 1. "me gusta pero el bajo más gordo"

Razonamiento: "gordo" = más cuerpo y sostén, no necesariamente más volumen. ears ya decía
que faltaba sub. Cambio solo el bajo: notas más largas (0.4 → 0.6 de negra) y un poco más
fuertes, saturación suave para armónicos, y un sidechain para que las notas largas no pisen
el bombo.

```diff tiger-rag
~ length_beats: 0.4, velocity: 100} => length_beats: 0.6, velocity: 104}
~ length_beats: 0.4, velocity: 92} => length_beats: 0.6, velocity: 96}
-  gain_db: 13
+  fx:
+  - {id: sat, type: saturation, drive_db: 6}
+  - {id: sc, type: sidechain, key_track: kick, depth: 0.4, release_ms: 90}
+  gain_db: 13
```

v026 (en el proyecto de prueba), `summary`: "Bajo más gordo: notas más largas y un poco más
fuertes, saturación suave para que tenga cuerpo en parlantes chicos, y se agacha con el bombo
para no pisarlo". ears: bajo +6.9 dB RMS, 6 de 64 bombos se tocan con el bajo (bajo el
umbral), mix 2.5 dB más oscuro arriba, sigue `spectral_sub_deficit`. A Lautaro: "Si todavía
lo sentís flaco abajo, el próximo paso es cambiarlo por un bajo de synth, que tiene más
fundamental".

### 2. "acá el bombo se come al sample" (comentario en v004, `at_s: 12`)

Ubicación: 12 s × 126.32/60 = beat 25.3 → compás 7, tiempo 2.3, con la batería completa.
ears no marca choque de graves entre bombo y sample (el sample tiene high-pass en 150 Hz),
así que lo que se oye es el bombeo y el nivel del bombo. Cambio dos cosas del mismo tema:
sidechain menos profundo y más rápido, bombo 2 dB abajo.

```diff tiger-rag
-  - {id: sc, type: sidechain, key_track: kick, depth: 0.5, release_ms: 140}
+  - {id: sc, type: sidechain, key_track: kick, depth: 0.35, release_ms: 100}
-      kick: {sample: "library://kits/tr8-hardware-machines/roland-tr-8/kick-tr-8-kick-02.wav", steps: "x...x...x...x...", gain_db: -4}
+      kick: {sample: "library://kits/tr8-hardware-machines/roland-tr-8/kick-tr-8-kick-02.wav", steps: "x...x...x...x...", gain_db: -6}
```

v027: el sample se agacha 3.6 dB (antes 5.7) y vuelve en 104 ms. Pero ears marcó un problema
nuevo: con el bombo más bajo, el bajo largo de la iteración 1 ahora lo tapa
(`low_end_collision`, 35 de 64 golpes) y hay barro en 250-500 Hz. No se muestra; se corrige:

```diff tiger-rag
-  - {id: sat, type: saturation, drive_db: 6}
-  - {id: sc, type: sidechain, key_track: kick, depth: 0.4, release_ms: 90}
+  - {id: sat, type: saturation, drive_db: 4}
+  - {id: sc, type: sidechain, key_track: kick, depth: 0.6, release_ms: 90}
```

v028: 0 choques, vuelve el único aviso de siempre (`spectral_sub_deficit`). A Lautaro: "En
v028 el sample respira menos con el bombo y el bombo está un poco más abajo. Fijate a 0:12 si
el riff ahora queda adelante. (v027 fue un paso intermedio que tapaba el bajo; no hace falta
escucharla)".

### 3. "está muy acelerado"

Razonamiento: bajar el tempo con resample también baja el tono. Para seguir afinado bajo
exactamente un semitono: 123.08 · 2^(-0.55/12) ≈ 119.23 (126.32 → 119.23) y transpongo el
bajo de G a F#. `meta.key` acompaña.

```diff tiger-rag
-  bpm: 126.32  # 123.08 (tempo local de c1) x 2^(0.45/12): el disco esta 45 cents bajo y +0.45 st lo afina
-  key: G  # donde encaja el bajo con el sample (ears: bass_vs_sample)
+  bpm: 119.23  # 123.08 x 2^(-0.55/12): un semitono menos que 126.32, sigue afinado
+  key: F#  # bajo un semitono con el tempo
~ pitch: G1, => pitch: F#1,
~ pitch: G2, => pitch: F#2,
```

v029, `summary`: "Más lento: de 126 a 119 BPM. Como el sample va a velocidad de vinilo,
también baja medio tono (de Sol a Fa#), así que bajé el bajo igual para que sigan juntos".
ears: el sample mide +2.0 cents, bajo con el sample fit 0.46 (`best_shift` +5: relación de
quinta, no un error), sin alarmas nuevas. A Lautaro: "Al bajar el tempo el sample también
quedó medio tono más grave, como un disco más lento. Si querés más lento pero con el tono de
antes, lo hago con stretch (pierde un poco del color de vinilo)".

## Variaciones de estilo (todas renderizadas)

| Archivo | Perfil | Render | ears |
|---|---|---|---|
| [tiger-rag-macross.yaml](../examples/tiger-rag-macross.yaml) | Macross 82-99: corte de 1 compás + stutter, +1.45 st (133.15), saturación, sidechain 0.7 | v021, 8.3 s | -12.1 LUFS; `sample_detuned` +33 cents medido (señal débil), falta sub, info `track_transposed` (quinta, ver ears.md) |
| [tiger-rag-yungbae.yaml](../examples/tiger-rag-yungbae.yaml) | Yung Bae: swing 0.2, bajo con notas de paso, chorus, break filtrado, más partes | v022, 5.2 s | -13.2 LUFS; falta sub |
| [tiger-rag-nighttempo.yaml](../examples/tiger-rag-nighttempo.yaml) | Night Tempo: frase de 4 compases del disco entero (`mix`), `stretch`, sidechain 0.25 | v023, 8.4 s | -12.1 LUFS; oscuro arriba (es el disco de 1928), falta sub |

Los tres: sin clicks, sin choques, sin problemas de grilla. Qué cambia en cada perfil y por
qué: [styles.md](../styles.md).
