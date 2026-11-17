# Leer ears

`render` corre ears y devuelve `data.metrics`; `metrics.summary` es lo que leés primero:

```
v004: -13.1 LUFS (target -12), pico -1.0 dBTP, PLR 12.2 | 0 problema(s), 2 aviso(s)
- aviso sample_detuned: ... -> poné `chops.c1.warp.pitch_semitones: -0.45` ...
```

Cada alarma (`metrics.alarms[]`) trae `severity` (`error` = problema, `warn` = aviso,
`info`), `code`, dónde (`bar`/`beat` del proyecto, `at_s`, `track`, `lane`, `chop`,
`where[]`), `value` vs `threshold` con `unit`, `message` en castellano y `fix` (un cambio
concreto en `song.yaml`). `metrics.skipped` dice qué no se pudo medir y por qué: si algo
está ahí, **no** está verificado.

Comparar dos versiones: `ears.compare(version_a, version_b)` → `lufs_delta`, `brightness`
(brighter/darker), `low_end` (heavier/lighter), `track_level_delta_db`, `alarms_fixed`,
`alarms_introduced`, `alarms_kept`, `notes`. Usalo después de cada iteración: un cambio
puede arreglar una cosa y romper otra (pasó en la receta: bajar el bombo hizo que el bajo
lo tapara).

## Regla de uso

1. `error` y los avisos técnicos claros: corregí antes de mostrar.
2. Avisos de gusto o con medición débil: mostrá y preguntá.
3. Ninguna alarma ≠ suena bien. Ver [Lo que ears no puede decirte](#lo-que-ears-no-puede-decirte).

## Volumen y dinámica

| code | qué significa | qué cambiar |
|---|---|---|
| `clipping` (error) | picos planos a 0 dBFS en el mix | bajá el track que más pega (`technical.tracks` peak), menos `drive_db`; nunca subas el master para compensar |
| `true_peak_hot` | true peak > -0.5 dBTP | `ceiling_db: -1` en el limitador del master |
| `loudness_off_target` | LUFS lejos de `target_lufs` | ver abajo |
| `over_compressed` | PLR < 6 dB (warn) / < 4 (error): aplastado, el bombo pierde golpe | menos `ratio`/threshold más alto en el master, `target_lufs` más bajo |
| `mix_silent`, `no_loudness_target` | nada suena / no hay target | revisá `mute` y ventanas; agregá `target_lufs: -12` |

### loudness_off_target

El master aplica la ganancia hacia `target_lufs` **después** de sus efectos y después un
limitador de seguridad. Si el mix tiene mucho pico relativo (PLR > ~10 dB, casi siempre el
bombo), ese limitador se come 1-3 LU y no llega. Medido en Tiger Rag: target -10 → -12.0,
-11 → -12.5, -12 → -13.0. **Subir `master.gain_db` no cambia nada** (el `fix` de ears lo
sugiere, pero la normalización lo anula: verificado). Qué sí sirve:

- aceptar un target realista (-12 para loops con sample viejo y bombo punchy);
- bajar el pico: bombo -2 dB, compresor en el master (`threshold_db: -18, ratio: 3`);
- subir el cuerpo: sample y bajo (`gain_db`) para que el bombo pese menos en los picos.

Lo segundo y lo tercero cambian el sonido: hacelo de a uno y contalo.

## Balance espectral (`spectral_<banda>_excess|deficit`)

Bandas: `sub` 20-60, `bass` 60-250, `low_mid` 250-500, `mid` 500-2k, `high_mid` 2-4k,
`presence` 4-8k, `air` 8-20k Hz, comparadas con una referencia "dance" que es un **modelo,
no medida en discos de future funk**. Son las alarmas menos confiables.

- `sub_deficit`: falta peso abajo. Opciones: `synth-bass` en vez de `finger-bass`, bajo +3
  dB, bombo más largo (`library.search query="kick long boomy"`). Preguntá antes: con un
  finger-bass siempre aparece (medido -8/-9 dB).
- `low_mid_excess` ("barro"): menos `saturation` en el bajo, high-pass del sample más alto
  (+30-50 Hz), bajo -2 dB.
- `presence_deficit`/`air_deficit` ("apagado"): si hay un low-pass automatizado es a
  propósito (ears lo baja a `info`). En un disco viejo es el color del disco: decilo. Si no,
  hats +2 dB o abrir el low-pass.
- Excesos en una banda nombran el track culpable: tocá ese.

Es un promedio del loop: 4 compases oscuros y 12 brillantes se compensan.

## Graves: bombo, bajo, sample, sidechain

| code | qué significa | qué cambiar |
|---|---|---|
| `low_end_collision` | en ≥ 25 % de los bombos otro track suena a < 6 dB del bombo en 40-150 Hz | sample: `highpass` 150-200 Hz o sidechain; bajo: sidechain (`depth` 0.5-0.6, `release_ms` 80-100) o notas a contratiempo |
| `sidechain_missing` (error) | el sample no se agacha (< 1.5 dB) | `key_track`/`key_lane` correctos, `depth` ≥ 0.4, el key track no muteado |
| `sidechain_weak` | se agacha < 40 % de lo nominal | `depth` +0.15 |
| `sidechain_no_recovery` | no se recupera antes del próximo golpe ("siempre agachado") | `release_ms` más corto (100-140 a 120-130 BPM) |
| `no_sidechain` (info) | hay sample y bombo pero no hay sidechain | agregalo (es la firma del género) |

Medido (corpus de ears): `depth` 0.6 → ~6 dB de dip; 0.45 → 4.1; 0.3 → 2.4 (débil). En
Tiger Rag: 0.5 → 5.6 dB, recupera en 178 ms. `low_end_masking.sidechain[]` trae
`measured_dip_db` y `recovery_ms` para comparar versiones. Sin stems de la fuente en caché
(samples de librería) el sidechain no se verifica: mirá `skipped`.

## Grilla

| code | qué significa | qué cambiar |
|---|---|---|
| `grid_offset` | todo el sample llega tarde/temprano (> 15 ms, mediana) | `chops.<id>.start_offset_beats` con el valor del `fix` (N ms = N·bpm/60000 beats) |
| `grid_drift` | el loop se va corriendo dentro del chop (el disco acelera/frena) | `warp.mode: stretch` en ese chop (+ `pitch_semitones` para mantener el tono que daba el resample) o un chop más corto |
| `clip_offset` | una repetición sola está corrida | revisá ese clip (`offset_beats`) |
| `lane_offset` | un lane de batería llega tarde (one-shot con silencio al principio) | cambiá el `sample` de ese lane |
| `notes_offset` | notas de un instrumento fuera de tiempo (presets de ataque lento) | info; cambiá preset si molesta |
| `swing_mismatch` (info) | las semicorcheas del sample swinguean más que la batería | `pattern.swing` al valor del `fix` |

Corrimientos > 1/8 de beat se leen con signo cambiado. Material sin ataques (pads) no se
mide.

## Tonalidad y afinación

| code | qué significa | qué cambiar |
|---|---|---|
| `bass_sample_clash` | el bajo encajaría mejor transpuesto (`best_shift_semitones`) | transponé el bajo esa cantidad (todas las notas) |
| `track_transposed` | un track encaja con `meta.key` corrido k semitonos | si k es ±5/±7 (cuarta/quinta) y `bass_vs_sample` encaja: probablemente es un pedal de dominante, no un error; ajustá `meta.key` o preguntá |
| `key_mismatch` | el mix suena en otra tonalidad que `meta.key` | con confianza < 0.4, no persigas el número; confiá en `bass_vs_sample` |
| `sample_detuned` | el sample cae entre dos notas | ver abajo |

### sample_detuned

Dos variantes, decí cuál es:

- **medido** (`tonal.detune[].measured_cents` con valor): la afinación del stem se midió con
  fuerza suficiente. Corregí: el `fix` da `pitch_semitones` o un `meta.bpm` que cae en
  semitono entero.
- **previsto por el resample (sin medir)**: ears calculó `12·log2(bpm/local_bpm)` y asume que
  el disco está en La 440. Si vos ya compensaste un disco corrido (Tiger Rag: -45 cents en
  `tonal.tracks.sample.tuning_cents` de la fuente, el stem y el render), la alarma es un falso
  positivo y su `fix` lo desafinaría. Verificalo: `tonal.tracks.<sample>.tuning_cents`
  cerca de 0 después de tu corrección = bien. Contáselo al humano y pedile que confirme de
  oído ("¿el bajo y el sample suenan afinados juntos o baten?").

Las estimaciones de tonalidad en discos viejos saltan entre versiones (Tiger Rag: C#, F,
F#, B con confianzas 0.07-0.54). Usá `bass_vs_sample.fit` (0..1, ≥ 0.45 = encaja) como guía
principal.

## Clicks

| code | qué significa | qué cambiar |
|---|---|---|
| `click` | salto en el borde de un clip o nota | chop: `fade_ms` 10-15; nota: otro preset o notas que no se pisen (`slap-bass` da clicks en ataques de notas cortas: medido) |
| `loop_seam_click` | salto en la vuelta del loop | `fade_ms` del chop, o un chop que cierre mejor (otro candidato) |

No busca clicks fuera de bordes conocidos (automatización, ataque de sidechain).

## Técnicos

| code | qué cambiar |
|---|---|
| `dc_offset` | `highpass` 20 Hz en el track del `fix` o en el master |
| `low_end_phase` | bajo y bombo centrados (`pan: 0`), sin chorus/reverb en ellos |
| `track_silent` | track que no suena: `mute`, ventana `at_bar`/`until_bar`, notas fuera de la ventana, gain |
| `track_inaudible` | suena ≥ 20 dB debajo del resto: subí `gain_db` o sacalo |

## Lo que ears no puede decirte

- Si el loop es lindo, aburrido, si el chop corta una palabra o una frase a la mitad, si el
  groove "camina", si es el sample correcto para el estilo.
- Artefactos de stretch/pitch (warble), de separación (stems "con agua"), aliasing,
  distorsión fea, reverb que embarra.
- Problemas de un beat: tonalidad y espectro son promedios del loop.
- Enmascaramiento fuera de 40-150 Hz (un clap que tapa la voz en 1-4 kHz).
- Si el bombeo del sidechain tiene la velocidad musical correcta.
- Micro-timing < 15 ms (el "atrás" del groove).
- El estilo: `style_similarity` no está implementado (siempre `null`).

Por eso el humano escucha. Vos decís qué mediste y qué tiene que juzgar él.
