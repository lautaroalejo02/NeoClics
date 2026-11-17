# Muestra: estudio de música AI-native (spec v0, entregable 1)

> Nombre provisorio. Documento vivo: se refina en sesiones de Q&A con Lautaro.
> Versión: v0.1 · Fecha: 2026-11-17 · Destinatarios: Orca, GrokBot y cualquier agente que implemente.
> Cambios v0.1: canciones de prueba, referencias de estilo abiertas, modo dual de elección de chops, política de herramientas pagas (sección 16).

## 0. Cómo usar este documento (para agentes)

1. Leé todo antes de escribir código. Las secciones 7 (contratos) y 8 (herramientas) son la interfaz entre work packages: no las cambies sin dejarlo anotado en `docs/decisions.md` y avisar.
2. Todo lo marcado como **[SUPUESTO]** todavía no lo confirmó Lautaro. Implementalo de forma que sea fácil de cambiar.
3. Las elecciones de librerías son sugerencias. Si encontrás algo claramente mejor, documentá por qué y seguí. Verificá versiones y licencias actuales antes de fijarlas.
4. Prioridad absoluta: **que suene bien** y **que iterar sea rápido**. Si hay que elegir entre una feature más y calidad de sonido, gana el sonido.

## 1. Visión

Lautaro quiere hacer música (future funk estilo Macross 82-99, sampleando rock y pop argentino viejo y mezclándolo con city pop y jazz fusión japonés) **sin aprender un DAW**. En sus palabras: un proceso "así como se programa ahora pero en música", con un agente "generando, cortando, haciendo".

El modelo mental es el de un agente de código:
- Lautaro trae una canción y dice qué quiere en lenguaje natural.
- El agente (Claude Code, Codex u otro, usando las suscripciones que ya paga) edita un proyecto declarativo, renderiza y explica qué cambió.
- Lautaro escucha, compara versiones, comenta, y el agente vuelve a iterar.

## 2. Principios de diseño

1. **Música como código.** Un archivo `song.yaml` es la fuente de verdad del arreglo. Todo render se reproduce exactamente a partir de `song.yaml` + fuentes + librería. Cada versión es un commit de git.
2. **El humano escucha, el agente hace.** Los agentes no oyen. Compensamos con (a) métricas objetivas de audio ("oídos", sección 10) y (b) un circuito de feedback rápido desde la UI.
3. **Calidad de sonido primero.** Samples reales, separación de stems con modelos de última generación, time-stretch y pitch-shift de alta calidad, efectos y plugins buenos. Nada de síntesis "de juguete" como sonido principal.
4. **Usar las suscripciones existentes.** En v1 la herramienta **no llama LLMs propios**. El cerebro es el agente que Lautaro ya usa, conectado por MCP. Así no hay costo de API extra.
5. **Lista para pasar a web.** Núcleo (motor + API) separado de la interfaz. Hoy: reproductor local en el browser + agente en terminal. Mañana: la misma API detrás de una web app. Nada atado a la consola ni al sistema operativo.
6. **Iteración en segundos.** Separar y analizar una canción es lento y se hace una vez (con caché). Re-renderizar un loop tiene que ser rápido.

## 3. Flujo principal (escenario objetivo del entregable 1)

1. Lautaro: "Importá `nos-siguen-pegando-abajo.wav`".
2. Muestra separa stems (batería, bajo, voz, resto), detecta BPM, tonalidad, beats, downbeats y secciones, y lo guarda en caché.
3. Lautaro: "Armame un loop future funk de 16 compases con el riff del inicio".
4. El agente busca candidatos de chop en la sección pedida, genera 3 previews cortos y los muestra en la UI. **Modo dual (decidido):** por defecto el agente elige el que mejor le parece y explica por qué (modo `auto`), pero los candidatos quedan visibles y Lautaro puede elegir otro, ajustar el inicio o el largo, o pedir más candidatos (modo `manual`). Cualquier elección manual pisa a la del agente y queda registrada en `feedback.jsonl`.
5. El agente arma `song.yaml` aplicando la receta future funk (sección 9) y renderiza **v001**.
6. En la UI: forma de onda, play, loop, stems del render con mute/solo, y el resumen del agente de "qué hice".
7. Lautaro: "Más filtrado en la intro, el bajo más gordo y el sample medio tono más arriba".
8. El agente edita `song.yaml`, renderiza **v002**, escribe el diff musical ("subí 1 semitono el chop c1, agregué un filtro low-pass automatizado en los compases 1 a 4, cambié el preset del bajo").
9. Lautaro hace A/B entre v001 y v002 en la misma posición, marca "me gusta / no me gusta", deja un comentario en el segundo 0:12 ("acá el bombo se come al sample").
10. El agente lee el feedback y sigue. Repetir.

## 4. Alcance

### Entra en el entregable 1
- Importar archivos de audio locales (wav, flac, mp3, m4a).
- Separación de stems + análisis (BPM, tonalidad, beats, downbeats, secciones, loudness) con caché.
- Búsqueda de candidatos de chop y previews.
- Librería local de samples (kits de batería, one-shots, loops) indexada y buscable.
- `song.yaml` con tracks, clips de chops y samples, patrones de batería, MIDI, efectos y automatización básica.
- Render determinista a WAV (mix + stems por track) con metadatos.
- Versionado (git) + diff musical legible.
- Servidor MCP + CLI equivalente.
- Reproductor local en el browser con A/B, loop, mute/solo por track, rating y comentarios con timestamp.
- "Oídos" del agente: métricas de audio por render.
- Skill/instrucciones para el agente con la receta future funk y cómo usar las herramientas.

### Fuera del entregable 1
- Web pública, cuentas de usuario, deploy.
- Descarga desde URLs (YouTube, etc.). Solo archivos locales.
- Generación de audio con modelos generativos (queda la interfaz preparada, ver sección 15).
- Tema completo con estructura de canción y export final para YouTube (eso es el entregable 2).
- Tocar en vivo, controladores MIDI, tiempo real.
- Video o visualizer.

## 5. Arquitectura

```
 Lautaro
   |  (lenguaje natural)
   v
 Agente (Claude Code / Codex / orquestado por Orca)  <-- usa las suscripciones
   |  MCP (o CLI)
   v
 +----------------------- muestra-core (Python) ------------------------+
 |  ingest -> separate -> analyze -> chops                              |
 |  library (índice de samples)                                         |
 |  song.yaml  --validate-->  renderer  --> renders/vNNN/ (mix + stems) |
 |  ears (métricas)    versions (git + diff musical)    feedback        |
 +----------------------------------------------------------------------+
   |  HTTP API local (FastAPI) + websocket de eventos
   v
 UI local en el browser (reproductor, A/B, comentarios)  --> futura web app
```

- **muestra-core**: librería Python con toda la lógica. Sin estado global; todo vive en la carpeta del proyecto.
- **muestra-mcp**: servidor MCP que expone las herramientas de la sección 8 llamando a core.
- **muestra-cli**: CLI que expone lo mismo (para scripts, debugging y agentes sin MCP).
- **muestra-api**: API HTTP local que usa la UI. Es la que después se despliega para la versión web.
- **muestra-ui**: frontend web (React + Vite sugerido). Solo habla con muestra-api.

## 6. Tecnología sugerida

| Pieza | Sugerencia | Notas |
|---|---|---|
| Lenguaje núcleo | Python 3.11+ | El ecosistema de audio/ML está ahí |
| Separación de stems | `audio-separator` con modelos BS-RoFormer / Mel-Band RoFormer | Comparar contra htdemucs en 3 canciones viejas y elegir. Grabaciones de los 80 en cinta pueden separar peor |
| Beats y downbeats | `beat_this` (CPJKU) | Debe soportar tempo variable: muchos discos viejos no tienen click |
| Estructura (secciones) | `allin1` o similar | Intro, verso, estribillo para elegir de dónde chopear |
| Tonalidad | Essentia (key extractor) | Guardar también la confianza |
| Loudness | `pyloudnorm` (LUFS) | |
| Time-stretch / pitch-shift | Rubber Band (motor R3) vía `pyrubberband` o CLI | Calidad alta. Además modo "resample" (vinilo) sin Rubber Band |
| Efectos y plugins | Spotify `pedalboard` | Efectos nativos de calidad y carga de plugins VST3/AU, incluidos instrumentos con MIDI |
| Instrumentos | Surge XT (VST3, libre), sfizz (sampler SFZ), presets propios | Verificar renderizado headless en Windows |
| MIDI | `mido` / `pretty_midi` | |
| Mezcla y render | numpy + pedalboard, a 48 kHz / 32-bit float interno | Export WAV 24-bit |
| API local | FastAPI + websocket | |
| UI | React + Vite + wavesurfer.js | A/B sincronizado por posición |
| MCP | SDK oficial de MCP en Python | Probar con Claude Code y con Codex |
| Versionado | git (texto) + carpeta de renders (audio, ignorado o con LFS) | |
| Similitud de estilo | LAION-CLAP o MERT (opcional en v1) | Para "¿suena a future funk?" contra referencias |

**[SUPUESTO] Máquina:** arranca en la Zenbook personal (Windows, con WSL2 disponible). Todo tiene que funcionar también en macOS (posible MacBook nueva). Lo pesado (separación) corre en CPU con caché; dejar preparado un backend opcional en GPU serverless (Modal) detrás de la misma interfaz.

## 7. Contratos de datos

### 7.1 Estructura de un proyecto

```
mi-proyecto/
  muestra.toml            # config del proyecto (sample rate, rutas de librería, backend de separación)
  song.yaml               # fuente de verdad del arreglo (versionado en git)
  sources/                # audio original importado (no se modifica nunca)
  cache/
    stems/<source_id>/    # drums.wav, bass.wav, vocals.wav, other.wav
    analysis/<source_id>.json
    previews/             # renders cortos de candidatos
  midi/                   # .mid generados o editados por el agente
  renders/
    v001/
      mix.wav
      stems/<track_id>.wav
      meta.json           # ver 7.4
      metrics.json        # ver sección 10
  feedback.jsonl          # ver 7.5
  docs/decisions.md       # decisiones de diseño y cambios de contrato
```

### 7.2 `analysis.json` (por fuente)

```json
{
  "source_id": "src1",
  "file": "sources/nos-siguen-pegando-abajo.wav",
  "duration_s": 251.3,
  "sample_rate": 44100,
  "bpm_global": 126.4,
  "tempo_variable": true,
  "beats_s": [0.41, 0.89, 1.36],
  "downbeats_s": [0.41, 2.31],
  "key": {"tonic": "A", "mode": "minor", "confidence": 0.71},
  "sections": [{"label": "intro", "start_s": 0.0, "end_s": 15.2}],
  "loudness_lufs": -11.8,
  "stems": {"drums": "cache/stems/src1/drums.wav", "bass": "...", "vocals": "...", "other": "..."},
  "separation_model": "nombre-y-version-del-modelo"
}
```

### 7.3 `song.yaml` (borrador de schema, v0)

Posiciones musicales en compases y beats (1-indexados), no en segundos. El renderer convierte usando el tempo del proyecto.

```yaml
schema_version: 0
meta:
  title: borrador-01
  bpm: 120
  key: F#m
  length_bars: 16
  seed: 42                # toda aleatoriedad usa este seed

sources:
  - id: src1
    file: sources/nos-siguen-pegando-abajo.wav

chops:
  - id: c1
    source: src1
    stem: other           # drums | bass | vocals | other | mix
    start_downbeat: 3     # índice de downbeat en analysis.json (se alinea a la grilla real del disco)
    length_beats: 8
    warp:
      mode: resample      # resample = estilo vinilo, el pitch sube con el tempo (típico future funk)
                          # stretch  = Rubber Band, conserva el pitch
      pitch_semitones: 0  # ajuste extra sobre el warp
    fade_ms: 5

tracks:
  - id: sample
    clips:
      - {chop: c1, at_bar: 1, repeat: 8}
    fx:
      - {id: hp, type: highpass, cutoff_hz: 150}
      - {id: lp, type: lowpass, cutoff_hz: 18000}
      - {id: sc, type: sidechain, key_track: drums, depth: 0.6, release_ms: 120}
    gain_db: -2

  - id: drums
    pattern:
      steps_per_bar: 16
      lanes:
        kick:  {sample: library://kits/disco-01/kick.wav,  steps: "x...x...x...x..."}
        clap:  {sample: library://kits/disco-01/clap.wav,  steps: "....x.......x..."}
        hat:   {sample: library://kits/disco-01/ohat.wav,  steps: "..x...x...x...x."}
      swing: 0.0
    fx:
      - {id: comp, type: compressor, threshold_db: -18, ratio: 4}

  - id: bass
    instrument: {plugin: surge-xt, preset: presets/slap-bass-01.fxp}
    midi: midi/bass.mid
    fx: []

automation:
  - {target: tracks.sample.fx.lp.cutoff_hz, curve: exp, points: [[1, 800], [5, 18000]]}

master:
  fx:
    - {type: compressor, threshold_db: -12, ratio: 2}
    - {type: limiter, ceiling_db: -1.0}
  target_lufs: -10
```

Reglas:
- `song.validate` falla con mensajes claros (qué campo, qué valor, cómo arreglarlo): los mensajes de error son para un agente.
- Rutas `library://` resuelven contra la librería configurada en `muestra.toml`.
- Campos desconocidos: error, no se ignoran en silencio.

### 7.4 `renders/vNNN/meta.json`

```json
{
  "version": "v002",
  "parent": "v001",
  "git_commit": "abc123",
  "created_at": "2026-11-18T21:04:00Z",
  "request": "más filtrado en la intro, bajo más gordo, sample medio tono arriba",
  "agent": "claude-code",
  "summary": "Subí c1 un semitono; filtro LP automatizado compases 1-4; preset de bajo slap-bass-02",
  "diff": [{"path": "chops.c1.warp.pitch_semitones", "from": 0, "to": 1}],
  "render_time_s": 6.2,
  "duration_s": 32.0
}
```

### 7.5 `feedback.jsonl` (lo escribe la UI, lo lee el agente)

```json
{"ts":"2026-11-18T21:06:10Z","version":"v002","type":"rating","value":"like"}
{"ts":"2026-11-18T21:06:40Z","version":"v002","type":"comment","at_s":12.0,"track":"drums","text":"acá el bombo se come al sample"}
{"ts":"2026-11-18T21:07:00Z","type":"ab_choice","a":"v001","b":"v002","winner":"v002"}
```

## 8. Herramientas (MCP y CLI)

Mismos nombres en MCP y CLI (`muestra <grupo> <acción>`). Todas devuelven JSON con rutas a los archivos generados y un campo `hint` corto para el agente cuando algo sale raro.

| Herramienta | Qué hace |
|---|---|
| `project.create(name)` / `project.open(path)` | Crea o abre un proyecto |
| `source.import(path)` | Copia el audio a `sources/` y le asigna un id |
| `source.separate(source_id, model?)` | Separa stems (con caché) |
| `source.analyze(source_id)` | Genera `analysis.json` |
| `source.find_chops(source_id, section?, stem?, length_beats?, n=3)` | Propone candidatos de loop alineados a downbeats, renderiza previews y devuelve el elegido por el agente con su justificación |
| `chops.choose(candidate_id, adjust?)` | Fija un candidato (lo usa el agente en modo `auto` y la UI en modo `manual`); `adjust` permite correr inicio o largo en beats |
| `preview(target)` | Render corto de un chop, clip o track |
| `library.index(path)` / `library.search(query, bpm?, key?, type?)` | Indexa y busca samples (con BPM, tonalidad y tags) |
| `song.get()` / `song.validate()` | Lee y valida `song.yaml` (el agente edita el archivo directamente) |
| `render(range_bars?, stems=true)` | Renderiza, crea `vNNN`, hace commit, corre `ears` y devuelve métricas |
| `versions.list()` / `versions.diff(a, b)` / `versions.restore(v)` | Historial y comparación |
| `feedback.read(since?)` | Feedback nuevo de la UI |
| `ears.analyze(version)` | Métricas de audio (sección 10) |

## 9. Receta future funk inicial (conocimiento para la skill del agente)

Punto de partida para que el agente tome decisiones razonables. Es una guía de estilo, no reglas; Lautaro la va a ajustar.

- **Tempo:** rango típico de unos 110 a 130 BPM. Muchas veces se logra acelerando el sample (modo `resample`), lo que también lo sube de tono.
- **Batería:** four-on-the-floor (bombo en negras), clap o snare en 2 y 4, hi-hat abierto a contratiempo. Sonido punchy y limpio.
- **Sidechain:** el sample "respira" con el bombo. Es una de las firmas del género.
- **Filtros:** barridos de low-pass y high-pass estilo french house, sobre todo en intros y transiciones.
- **Chops:** loops de 1, 2 o 4 compases, cortados en downbeats; a veces stutters y re-triggers de frases cortas.
- **Bajo:** reforzar o reemplazar el bajo del sample con uno propio (slap, synth bass), en la misma tonalidad.
- **Color:** algo de phaser, chorus, saturación y un poco de "vinilo" opcional.
- **Fuentes del sample:** Lautaro mencionó Clics Modernos (Charly García), Spinetta, Soda Stereo, Bajofondo, y mezclar con city pop y fusión japonesa (Casiopea, T-Square).
- **Canciones de prueba (decidido):** 1) "Nos siguen pegando abajo" (Charly García, Clics Modernos), el inicio es el candidato que él marcó; 2) "Lunes por la madrugada" (Los Abuelos de la Nada). Sumar una tercera bien distinta (por ejemplo algo de fusión japonesa) para probar robustez.

### 9.1 Referencias de estilo: una paleta, no una plantilla

Lautaro quiere que el sistema sea **abierto**: las referencias sirven para calibrar, no para clonar un sub-estilo. La skill debe tratarlas como "perfiles" que se pueden mezclar ("más Yung Bae en la batería, más Macross en los cortes") y también salir de ellas.

| Perfil | Qué aporta | Rasgos que el agente puede tomar |
|---|---|---|
| **Macross 82-99** (productor mexicano, de los fundadores del género; serie *Sailorwave*) | El future funk "clásico": samples de anime y city pop japonés muy chopeados | Cortes cortos y repetidos, filtro y sidechain marcados, sonido algo crudo, tono subido por acelerar el sample |
| **Yung Bae** (productor de Portland, EE.UU.) | La versión más producida y bailable, cerca del nu-disco | Mezcla pulida, bajo y batería con mucho groove, arreglos con más partes y menos loop crudo |
| **Night Tempo** (productor coreano, conocido por sus re-ediciones de city pop, como la de "Plastic Love" de Mariya Takeuchi) | Respeto por el tema original | Edits más largos y fieles al sample, el original se reconoce; útil para que Charly o Spinetta "se escuchen" |
| **Saint Pepsi / Skylar Spence** (*Hit Vibes*, 2013; *Prom King*, 2015) | El origen del género con samples de disco y R&B estadounidense | Loops de 2-4 compases con mucho funk, voz chopeada como gancho |
| **Moe Shop** (productor francés con vocalistas japoneses) | La salida "más original": menos sample, más composición propia | Synths y bajos propios, el sample como condimento y no como base |
| **Raíz french house**: Stardust "Music Sounds Better With You" (1998), Modjo "Lady" (2000), Daft Punk *Discovery* (2001) | De dónde salen el filtro y el sidechain del future funk | Barridos de filtro largos, loop disco que "respira", tensión por automatización más que por cambios de acordes |

- La pregunta de qué es "Horsey" queda abierta: Lautaro lo mencionó como referencia, pero la banda de Londres con ese nombre es art rock/post punk. Confirmar qué artista quiso decir.
- Lautaro debe elegir 1 o 2 temas concretos por perfil para `references/` (los usa la métrica de similitud de la sección 10 y la skill como ejemplos).
- Ángulo propio: el cruce de rock nacional de los 80 con city pop y fusión japonesa no es un perfil existente; los perfiles son puntos de partida, el objetivo es que suene a Lautaro.

La skill también tiene que enseñarle al agente: el flujo de la sección 3, cómo escribir el `summary` de cada versión en términos musicales (no técnicos), y a leer `feedback.jsonl` antes de cada iteración.

## 10. "Oídos" del agente (métricas por render)

`metrics.json` por versión, para que el agente detecte problemas que no puede oír:

- Loudness integrado (LUFS) y true peak; clipping.
- Balance espectral por bandas (graves, medios, agudos) del mix y por track.
- Enmascaramiento graves: energía de bombo vs bajo vs sample en 40-150 Hz (el caso "el bombo se come al sample").
- Alineación: desfase de los transientes del sample respecto de la grilla.
- Coherencia tonal: tonalidad estimada del mix vs `meta.key`.
- Clicks en los bordes de los chops.
- (Opcional) Similitud de estilo con CLAP/MERT contra 3 a 5 temas de referencia que elija Lautaro.

Las métricas no reemplazan el oído de Lautaro: son alarmas.

## 11. Criterios de aceptación del entregable 1

1. Importar una canción de 4 minutos y tener stems + análisis en caché, en la Zenbook con CPU, en un tiempo razonable (medirlo y reportarlo; objetivo orientativo: menos de 10 minutos la primera vez, instantáneo después).
2. Desde Claude Code, con un solo pedido ("loop future funk de 16 compases con el riff del inicio"), se obtiene un v001 que suena coherente: chop alineado, sin clicks, batería en grilla, sidechain audible.
3. Re-render de un loop de 16 compases en menos de 15 segundos en la Zenbook.
4. Diez iteraciones seguidas por lenguaje natural funcionan, cada una con versión, commit, diff musical y métricas.
5. A/B en la UI salta entre versiones manteniendo la posición de reproducción.
6. Mute/solo por track en la UI sin re-renderizar (usa los stems del render).
7. Comentarios con timestamp llegan a `feedback.jsonl` y el agente los usa en la siguiente iteración.
8. El mismo flujo funciona desde Codex con el mismo servidor MCP.
9. Reproducibilidad: borrar `renders/` y re-renderizar una versión da el mismo audio (bit a bit o con diferencia despreciable).
10. Instalación documentada en un README de menos de 10 pasos.

## 12. Plan de trabajo en paralelo

Orden: primero los contratos (WP0), después todo lo demás en paralelo contra esos contratos y con datos de prueba.

| WP | Qué | Depende de |
|---|---|---|
| WP0 | Schemas: `song.yaml`, `analysis.json`, `meta.json`, `feedback.jsonl` + validador + fixtures de ejemplo | Nada |
| WP1 | Ingest + separación + análisis (con benchmark de 2-3 modelos en canciones viejas) | WP0 |
| WP2 | Renderer: chops, warp, patrones de batería, mezcla, automatización, master | WP0 |
| WP3 | Hosting de plugins e instrumentos (pedalboard + Surge XT/sfizz) + render de MIDI | WP0 |
| WP4 | Librería de samples: indexado (BPM, tonalidad, tipo, tags) y búsqueda | WP0 |
| WP5 | Versionado (git), diff musical, `meta.json` | WP0, WP2 |
| WP6 | Oídos: métricas | WP0 |
| WP7 | API local + servidor MCP + CLI | WP0 (stubs), después todos |
| WP8 | UI local | WP0, WP7 (con mocks al principio) |
| WP9 | Skill del agente: receta, perfiles de estilo (9.1), flujo, ejemplos de iteración; prueba de punta a punta con Claude Code y Codex | WP7 |
| WP10 | Evaluación de sonido: comparar opciones gratis vs pagas según la sección 16 y entregar el informe para que Lautaro decida | WP2, WP3 |

Cada WP entrega: código, tests (incluidos tests de audio con fixtures cortos), y una nota en `docs/decisions.md` si cambió algo del contrato.

## 13. Riesgos

- **Agentes sordos:** pueden "arreglar" algo que suena peor. Mitigación: métricas, feedback rápido, versiones baratas de descartar.
- **Discos viejos:** tempo que varía, grabación en cinta, mezclas mono o raras. La separación y el beat tracking pueden fallar. Mitigación: grilla por downbeats reales y benchmark de modelos con este tipo de material.
- **Calidad de plugins en Windows y en modo headless:** algunos VST3 se portan mal fuera de un DAW. Mitigación: probar temprano (WP3) y tener plan B con sampler SFZ.
- **Derechos de autor:** samplear discos comerciales para subir a YouTube probablemente dispare reclamos de Content ID (monetización para los titulares, bloqueos). No es un problema técnico del entregable 1, pero condiciona qué se publica después.
- **Scope creep:** la tentación de sumar generativos, tiempo real o video antes de que el loop suene bien.

## 14. Supuestos y preguntas abiertas (para el próximo Q&A)

### Decisiones de Lautaro (Q&A del 2026-11-17)
- **Interfaz:** agente + reproductor local en el browser, porque es lo más rápido; diseñado para poder pasar a web después.
- **Canciones de prueba:** "Nos siguen pegando abajo" primero, después "Lunes por la madrugada".
- **Referencias de estilo:** abiertas (ver 9.1); mencionó Macross 82-99, Yung Bae y "Horsey" (a confirmar), y pidió recomendaciones más originales.
- **Elección de chops:** las dos cosas, modo dual (sección 3, paso 4).
- **Herramientas pagas:** sí, pero solo después de un análisis exhaustivo (sección 16).

### Supuestos que siguen sin confirmar
- **[SUPUESTO] Sonidos:** en orden de prioridad, (1) canciones que trae él, (2) librería local de kits y samples, (3) instrumentos virtuales con MIDI. Modelos generativos quedan para después.
- **[SUPUESTO] Entregable 1:** canción → loop future funk de 16 compases iterable por lenguaje natural.
- **[SUPUESTO] Máquina:** Zenbook personal (Windows/WSL2), compatible con macOS.

### Preguntas abiertas
1. ¿Qué artista es "Horsey"?
2. ¿Qué 1 o 2 temas concretos por perfil de 9.1 van a `references/`?
3. ¿Tenés kits de batería o un sonido de batería de referencia ("quiero que suene como X")?
4. ¿Te interesa tararear o cantar una idea como entrada (melodía o ritmo) en una próxima versión?
5. Generativos: ¿como fuente de texturas y loops, o todo desde samples y síntesis?
6. ¿Evaluás con auriculares (AirPods, Bose, Sennheiser HD599) o con monitores? Afecta el target de mezcla.
7. ¿Confirmás los tres supuestos de arriba?

## 15. Después del entregable 1 (no implementar ahora)

- **Entregable 2:** tema completo (estructura intro, partes, drop, outro), export para YouTube, portada.
- **Adaptadores generativos** detrás de una interfaz `generator`: ElevenLabs Music, Stable Audio Open (local o Modal), Lyria RealTime; siempre con su licencia anotada en `meta.json`.
- **Versión web:** desplegar `muestra-api` + `muestra-ui`, con "Sign in with ChatGPT" u otro esquema para que el usuario use su propio plan.
- **Visualizer:** video sincronizado a partir de `song.yaml` (los eventos de cada track ya están en el arreglo, así que el visual puede reaccionar exacto a cada bombo y cada chop).
- **Tiempo real:** tocar y modificar en vivo.

## 16. Política de herramientas pagas

Lautaro acepta pagar plugins, librerías de samples o servicios **solo después de un análisis exhaustivo**. Regla para los agentes: nunca comprar ni suscribir nada; preparar la evidencia para que él decida.

1. **Gratis primero.** Toda la v1 tiene que funcionar con opciones gratis u open source.
2. **Candidatos pagos solo con hipótesis clara:** qué problema de sonido resolvería (por ejemplo "la batería del kit gratis suena plana en el bombo") y cuánto cuesta.
3. **Comparación a ciegas (WP10):** el mismo `song.yaml` renderizado con la opción gratis y con la paga (demo o trial), con nombres ocultos (A/B), en la UI. Lautaro elige sin saber cuál es cuál.
4. **Informe por candidato** en `docs/evaluaciones/<nombre>.md`: qué resuelve, resultado del A/B a ciegas, precio (único o suscripción), licencia (uso comercial, YouTube), compatibilidad (Windows, macOS, headless con pedalboard), y alternativas gratis probadas.
5. **Decisión de Lautaro** anotada en `docs/decisions.md`.

Categorías donde más probablemente valga la pena evaluar: kits de batería y one-shots, instrumentos (bajos, Rhodes, brass), y separación de stems en la nube si la de CPU queda corta en discos viejos.
