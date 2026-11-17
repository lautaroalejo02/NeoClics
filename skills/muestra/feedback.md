# Traducir el feedback del humano

`feedback.read(cursor=N)` devuelve `events` y `next_cursor`. Tipos:

- `rating` `{version, value: like|dislike}`: el último rating de esa versión vale.
- `comment` `{version?, at_s?, track?, text}`: `at_s` = segundos dentro del mix de esa
  versión; `track` = el track que tenía en solo/seleccionado (puede no ser el culpable).
- `ab_choice` `{a, b, winner}`: comparó en la misma posición y eligió.
- `chop_choice` `{candidate_id, adjust?}`: eligió otro chop en la UI; ya está en `song.yaml`.

## Ubicar un comentario

`beat = at_s · bpm / 60` (desde 0); compás = `floor(beat / 4) + 1`, tiempo =
`beat mod 4 + 1`. Ejemplo: 12 s a 126.32 → beat 25.3 → compás 7, tiempo 2.3. Mirá qué suena
ahí (ventanas `at_bar`, automatizaciones) y las alarmas con ese `bar`. Para escuchar-medir
solo esa zona: `render(draft=true, range_bars=[7, 9], analyze=true)` o
`preview(target="track:sample", bars=4)`; ambos aparecen en la UI sin crear versión.

Si el comentario es sobre la grilla de la fuente ("el uno está corrido", "eso va al doble",
"los clicks no caen en los golpes"), no es un cambio del tema: es `source.regrid`
([troubleshooting.md](troubleshooting.md#la-grilla-está-mal-doblemitad-de-tempo-el-uno-corrido)).

## Principios

- Cambiá **pocas cosas por versión** (1-3, del mismo tema). Si en un mensaje pide tres
  cosas que no se pisan (intro, bajo, tono), podés hacerlas juntas y decir cuál es cuál; si
  dos tocan lo mismo (bombo y sidechain), una versión cada una.
- Usá su vocabulario en la respuesta, no el tuyo: "el bajo ahora tiene más cuerpo", no
  "agregué saturation drive_db 6".
- Un `dislike` sin comentario: preguntá qué no le gustó antes de cambiar a ciegas, o
  proponé volver a la anterior.
- Un `ab_choice` por una versión vieja: probablemente restaurar
  (`versions.restore`) y seguir desde ahí.
- Lo que dijo antes sigue valiendo: si pidió "más filtrado en la intro" no lo deshagas al
  arreglar otra cosa.

## Cuándo preguntar en vez de adivinar

Preguntá (una pregunta, con 2-3 opciones concretas) cuando:

- es vago: "más onda", "más future funk", "no me convence", "le falta algo";
- no se sabe qué parte: "eso del medio", "el sonido ese";
- hay dos lecturas que llevan a cambios opuestos ("más rápido": ¿tempo o que el sample
  corte más seguido?);
- contradice algo que pidió antes o lo que mide ears;
- es grande (cambiar el chop, el kit entero, la estructura).

Ejemplo: "¿'más onda' por el lado del groove (hats con swing, bajo con más movimiento) o del
color (más filtro y bombeo, tipo Macross)? Si querés hago las dos y comparás A/B." Dos
versiones baratas valen más que una pregunta larga.

## Frases y qué hacer

| Dice | Probablemente | Cambio concreto (empezá por el primero) |
|---|---|---|
| "el bajo más gordo" | más cuerpo/sostén abajo | notas más largas (0.4 → 0.6 de negra), `saturation` 4-6 dB, +2 dB; si sigue flaco `synth-bass`. Sidechain al bombo si las notas largas lo pisan |
| "no se escucha el bajo" | nivel o enmascarado | bajo +3 dB; si `low_end_collision`, sidechain; en parlantes chicos ayuda `saturation` |
| "más filtrado en la intro" | barrido más cerrado o más largo | low-pass arranca más abajo (500 → 300 Hz) y/o abre en 8 compases en vez de 4 |
| "el bombo se come al sample" | el bombeo o el bombo tapan el riff | sidechain `depth` -0.15 y `release_ms` 100; bombo -2 dB. Si ears marca `low_end_collision` con el sample: high-pass del sample más alto |
| "bombea mucho" / "sube y baja" | sidechain exagerado | `depth` 0.5 → 0.35, `release_ms` más corto |
| "no respira" / "le falta el bombeo" | sidechain débil | `depth` +0.15, `release_ms` 160-200 |
| "suena apagado" / "opaco" | faltan agudos | abrir el low-pass (¿quedó automatizado cerrado?), hats +2 dB o un hat más `bright` de la librería. En un disco viejo la falta de agudos es su color: decilo y ofrecé compensarlo con la batería |
| "muy chillón" / "pincha" | agudos de más | hats -3 dB, low-pass fijo del sample en 12-14 kHz |
| "está muy acelerado" | tempo | bajar a la próxima nota entera: `meta.bpm · 2^(-1/12)` y bajo -1 semitono (el sample baja medio tono con resample). Si quiere el tono igual: `stretch` |
| "más rápido" / "más energía" | tempo o densidad | preguntá; tempo: `meta.bpm · 2^(1/12)` y bajo +1; densidad: hats 16avos, chop más corto |
| "medio tono más arriba" (el sample) | tono sin tempo | `chops.<id>.warp.pitch_semitones: +1` y transponé el bajo +1 (o subir el tempo un semitono: más vinilo) |
| "que se escuche más Charly" (el artista del disco) | reconocer el original | hacia Night Tempo: chop más largo (4 compases), high-pass más bajo (80-100 Hz), sidechain 0.25-0.35, sample +2 dB, menos efectos; si la voz es el gancho, chop de `vocals` o `mix` |
| "muy repetitivo" | falta arreglo | variaciones por ventanas: batería que entra en el 5, break sin bombo en 13-14 (`until_bar`), fill con `bars: 2`, stutter al final de cada 4 |
| "le falta groove" / "está duro" | rígido | swing 0.15-0.25 en hats, fantasmas `o`, bajo con una nota de paso |
| "la batería suena de juguete" / "plana" | samples | `library.search similar_to=<uri> query="punchy"` o otro kit (`city-pop-80s`, `disco-808`); explicá qué cambia en las medidas |
| "más crudo" / "más sucio" | color Macross | `saturation` 6-10 dB en el sample, menos chorus/reverb |
| "más limpio" / "más pulido" | color Yung Bae | sacá saturación, `chorus` sutil, master -12 LUFS sin apretar |
| "se nota el corte" / "hace click" | borde del chop | `fade_ms` 10-15; si ears marca `loop_seam_click`, otro candidato que cierre mejor |
| "se desfasa" / "se va de tiempo" | grilla | mirá `grid_offset`/`grid_drift` y aplicá su `fix`; si ears no ve nada, preguntá en qué segundo |
| "suena saturado" / "distorsiona" | clipping o drive | ¿`clipping`? bajá el track más fuerte; si no, menos `drive_db` |
| "que dure más" | largo | `meta.length_bars` 16 → 32, más `repeat`, y revisá que las automatizaciones sigan teniendo sentido |
| "me gusta" (rating like) | conservá | no toques lo que no te pidió; anotá qué tiene esa versión |

## Responder

Después de renderizar: qué cambiaste (en sus términos), qué mediste, y una cosa concreta
para escuchar con segundo. Si una corrección técnica generó una versión intermedia, decí
cuál escuchar. Si no pudiste hacer algo (no hay herramienta, falla un plugin), decilo.
