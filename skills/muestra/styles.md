# Perfiles de estilo: una paleta, no una plantilla

Los perfiles calibran; no se clonan. Se mezclan por dimensión ("más Yung Bae en la
batería, más Macross en los cortes") y se puede salir de ellos: el objetivo es que suene
a Lautaro (rock nacional de los 80 cruzado con city pop y fusión japonesa). Cada fila es
un movimiento concreto en `song.yaml`. Tempos en BPM musicales (`meta.bpm`), con la grilla
de la fuente ya corregida (`source.analyze`/`source.regrid`).

## Tabla de movimientos

| Dimensión | Macross 82-99 | Yung Bae | Night Tempo | Saint Pepsi | Moe Shop | Raíz french house |
|---|---|---|---|---|---|---|
| Tempo | 125-135 | 115-124 | el del disco ±3 % | 110-122 | 120-130 | 120-126 |
| Resample `n` | +1 a +3 st | +0 a +1 | 0 (o `stretch`) | +1 a +2 | indistinto | +0 a +1 |
| Largo del chop | 1 compás (4 beats), a veces 2 | 2-4 compases | 4-8 compases | 2-4 compases | 1-2 compases, como condimento | 1-2 compases que loopean sin fin |
| Repetición | alta + stutters (`length_beats` 1-2) y re-disparos | media, con variaciones por sección | baja: la frase entera | alta | baja | altísima |
| `stem` | `other` | `other` | `mix` (o `other` + su bajo) | `other` / `vocals` como gancho | `other` muy filtrado | `other` |
| High-pass del sample | 180-250 Hz | 140-170 Hz | 60-90 Hz | 120-160 Hz | 200-400 Hz | 100-150 Hz |
| Barrido low-pass | 400 Hz → abierto en 4 compases; cortes de golpe (`step`) | intro + break, 600 Hz → abierto | casi nada | intro 4 compases | sutil | largo: 8-16 compases, 300 Hz → 18 kHz |
| Sidechain depth / release | 0.65-0.8 / 150-200 ms | 0.4-0.5 / 100-130 ms | 0.2-0.3 / 100 ms | 0.5-0.6 / 140 ms | 0.35-0.5 / 110 ms | 0.6-0.75 / 160-220 ms ("respira") |
| Batería | 909, clap fuerte, sin hats cerrados, crudo | 16avos con fantasmas, swing 0.15-0.25, percusión suave | bombo suave entrando tarde, clap bajo | disco con hat abierto marcado, swing 0.1 | programada, limpia, fills | four-on-the-floor sin variación, hat abierto protagonista |
| Bajo | synth-bass, octavas cortas en contratiempo | slap/finger con notas de paso y octavas | ninguno (el del disco) o refuerzo de tónica | finger, octavas disco | synth propio, línea compuesta | synth/finger simple, contratiempos |
| Color | `saturation` 6-10 dB en el sample | `chorus` 0.2-0.3, `phaser` sutil | nada; volumen del sample adelante | `phaser` 0.3 mix | `rhodes`/`brass-stab`/`pad` propios | `phaser` lento, `delay` 0.75 beat |
| Cuánto se reconoce el original | parcial: un gesto repetido | bastante, recortado | totalmente | bastante, con la voz como gancho | poco | el loop entero, transformado por el filtro |
| Arreglo | loop + cortes; drops con filtro | intro, groove, break, vuelta (ventanas `at_bar`/`until_bar`) | edit del tema con batería encima | loop de 2-4 compases | partes compuestas | tensión por automatización, no por acordes |

## Ejemplos renderizados (Tiger Rag, grilla corregida a 125)

Validados y renderizados en el proyecto de prueba; nadie los escuchó.

- [examples/tiger-rag-macross.yaml](examples/tiger-rag-macross.yaml): c1 de 1 compás x3 +
  stutter c2 de una negra x4 cada 4 compases (con `pitch_semitones: -0.35` para igualar su
  tempo local), `meta.bpm` 133.15 (+1.45 st), saturación 8 dB, sidechain 0.7/170 ms, sin hats
  cerrados, `synth-bass`, low-pass que abre de golpe en el drop y high-pass antes de la última
  vuelta.
- [examples/tiger-rag-yungbae.yaml](examples/tiger-rag-yungbae.yaml): mismo chop de 2
  compases, swing 0.2, hats en 16avos con fantasmas, cencerro bajito, bajo con octava y
  notas de paso (Sol-Re-Fa), chorus, break filtrado sin bombo ni bajo en los compases 13-14 y
  vuelta en el 15. Con `slap-bass` ears marcaba clicks en notas ligadas: era el ataque del
  propio preset (el mismo transitorio que en cada nota, medido muestra a muestra) y ya no
  se marca; el ejemplo usa finger por sonido, no por clicks. Si Lautaro quiere slap,
  probalo y preguntale si oye clicks.
- [examples/tiger-rag-nighttempo.yaml](examples/tiger-rag-nighttempo.yaml): frase de 4
  compases del `mix`, `stretch` + `pitch_semitones: 0.45` (la frase larga derivaba: ears
  `grid_drift`), high-pass 70 Hz, sidechain 0.25, bombo desde el compás 5 y clap desde
  el 9, sin bajo propio.

## Cómo mezclar perfiles

Tomá cada dimensión de un perfil distinto y cambiá solo esa en la versión:

- "Más Yung Bae en la batería": swing 0.2, hats `xoxoxoxoxoxoxoxo` a -14 dB, un lane de
  percusión en `...........o..o.`. No toques el chop.
- "Más Macross en los cortes": chop de 1 compás, stutter al final de cada 4 compases,
  sidechain 0.7. No toques la batería.
- "Que se escuche más Charly" (o el artista del disco): hacia Night Tempo en esa dimensión.
  Ver [feedback.md](feedback.md).

## El ángulo propio

Rock nacional de los 80 (Clics Modernos, Spinetta, Soda) tiene guitarras con chorus,
baterías con reverb gated y bajos con mucho ataque; el city pop y la fusión japonesa
(Casiopea, T-Square) traen acordes con séptimas y novenas, slap y bronces. Ideas que no son
de ningún perfil: chop de la guitarra del disco argentino sobre un bajo slap de fusión;
`city-pop-80s` (snare gated) en vez del 909; `brass-stab` en los cortes. Proponelas como
opción, una por versión.
