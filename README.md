# Muestra

Un estudio de música para hacer canciones **como se programa**: vos decís lo
que querés en tu idioma, un agente de código (Claude Code, Codex o el que
uses) edita un `song.yaml`, renderiza versiones y te explica qué cambió; vos
escuchás en el navegador, comparás versiones, comentás en el segundo justo y
el agente sigue. Muestra no llama a ningún modelo de lenguaje propio: el
cerebro es el agente que ya pagás.

Qué hace: importa un disco, separa stems (batería, bajo, voz, resto), detecta
tempo, compases y secciones, propone loops cortados en downbeats, arma la
canción con samples de tu librería, instrumentos (Surge XT, SFZ) y efectos,
renderiza mix + stems, versiona todo en git con un diff musical en castellano,
y le da "oídos" al agente: métricas que avisan de clicks, sidechain que no
bombea, bombo que se pisa con el bajo, desafinación, loudness.

La especificación completa está en [docs/spec.md](docs/spec.md).

## Instalación (una vez)

Funciona en Windows y macOS. Necesitás [uv](https://docs.astral.sh/uv/),
git, ffmpeg y Node.js 20 o más nuevo.

1. **Herramientas.** Windows (PowerShell):
   `winget install astral-sh.uv Git.Git Gyan.FFmpeg OpenJS.NodeJS.LTS`.
   macOS: `brew install uv git ffmpeg node`. Abrí una terminal nueva después.
2. **El código.** `git clone <url-de-este-repo> Muestra` y `cd Muestra`.
3. **Python y dependencias.** `uv sync --extra separation`
   (sin `--extra separation` se instala todo menos la separación de stems,
   que es lo pesado; los pesos de los modelos se bajan solos la primera vez).
4. **La interfaz.** `cd ui`, `npm ci`, `npm run build`, `cd ..`.
5. **Tu primer proyecto.** `uv run muestra project create --name mi-tema --dir <carpeta-de-tus-temas>`.
6. **Chequeo.** `uv run muestra doctor --project <carpeta-de-tus-temas>/mi-tema`
   (ver abajo).
7. **Conectar tu agente.** `uv run muestra agent-setup --project <...>/mi-tema --write <carpeta-donde-corre-tu-agente>`
   (ver "Conectar un agente").
8. **Abrir el estudio.** `uv run muestra serve --project <...>/mi-tema --open`.

La librería de samples vive en `Documentos/MuestraLibrary` (se detecta sola;
otra carpeta: variable `MUESTRA_LIBRARY` o `[library] roots` en
`muestra.toml`). Después de agregar samples, indexala una vez:
`uv run muestra library index --project <...>/mi-tema`.

## `muestra doctor`

Revisa todo sin bajar nada ni abrir plugins, y para cada cosa que falta dice
**exactamente qué falta y el comando que lo arregla**:

```text
OK       git: C:\Program Files\Git\cmd\git.exe
OPTIONAL ffmpeg: ffmpeg está instalado en ...\ffmpeg.exe pero no está en el PATH de esta terminal ...
         Fix: Open a new terminal, or set MUESTRA_FFMPEG="...\ffmpeg.exe".
OK       python-core: Core packages installed (server, MCP, effects, beat tracking).
OPTIONAL python-separation: Stem separation is not installed ...
         Fix: uv sync --extra separation   (run in the Muestra checkout: ...)
OK       model:htdemucs: C:\Users\vos\.cache\demucs-infer\955717e8-8726e21a.th
MISSING  ui: The listening UI is not built.
         Fix: cd "...\Muestra\ui" && npm ci && npm run build
OK       library:C:\Users\vos\Documents\MuestraLibrary: ... (indexed)
OK       plugin:slap-bass: ...\Surge XT.vst3
```

`MISSING` = algo central no anda; `OPTIONAL` = una parte opcional (por
ejemplo, sin separación de stems todo lo demás funciona). Sale con código 1 si
hay algún `MISSING`.

## Conectar un agente

`uv run muestra agent-setup --project <tema>` imprime la configuración exacta
para tu máquina (rutas absolutas, el `uv` que corresponde). Con
`--write <carpeta>` además deja escritos `.mcp.json` (Claude Code) y
`.codex/config.toml` (Codex) **en esa carpeta**, sin tocar tu configuración
global. Detalle en [docs/agents.md](docs/agents.md).

- **Claude Code.** Abrí `claude` en la carpeta donde escribió `.mcp.json`,
  aprobá el servidor `muestra` y mirá `/mcp`. Separar un disco entero tarda
  minutos (unos 4-5 en una notebook con CPU): el servidor manda progreso, y si
  tu Claude Code corta herramientas largas, subí su límite con la variable
  `MCP_TOOL_TIMEOUT` (milisegundos, por ejemplo `1800000`) antes de abrirlo.
- **Codex.** Usá el bloque `[mcp_servers.muestra]` que imprime
  `agent-setup` (en `~/.codex/config.toml` o en el `.codex/config.toml` del
  workspace de confianza). Ya trae `tool_timeout_sec = 1800`: sin eso, Codex
  corta a los 60 s y la separación no llega a terminar.
- **Cualquier cliente MCP.** Servidor stdio: comando `uv`, argumentos
  `run --directory <Muestra> muestra mcp --project <tema>`. Pedile al agente
  que llame primero a `muestra_guide`.
- **Sin MCP, solo terminal.** Las mismas herramientas son comandos:
  `uv run --directory <Muestra> muestra song validate --project <tema>`,
  `... muestra render --project <tema> --summary "..."`,
  `... muestra feedback read --project <tema>`. La salida es JSON cuando se
  redirige; el progreso de lo largo va por stderr. `muestra tools` lista todo.

## Abrir el estudio (y desde el celular)

`uv run muestra serve --project <tema> --open` abre `http://127.0.0.1:8765`
(si el puerto está ocupado elige otro y lo imprime). Dejala abierta: cuando el
agente renderiza desde otra terminal, la versión nueva aparece sola.

Para escuchar desde el **celular** en la misma red Wi-Fi:
`uv run muestra serve --project <tema> --host 0.0.0.0` y en el celular abrí
`http://<IP-de-tu-compu>:8765` (la IP la ves con `ipconfig` en Windows o en
Ajustes > Red en macOS; Windows puede pedir permiso en el firewall la primera
vez). No hay contraseña: hacelo solo en una red de confianza.

En la página: barra espaciadora para play, `S` para saltar entre A y B en la
misma posición, `M` para comentar en el punto que suena, mute/solo por pista
(usa los stems, no re-renderiza), me gusta / no me gusta, y la pestaña Oídos
con las alarmas que el agente va a leer.

## Primera sesión

1. Poné tu disco en cualquier carpeta y decile al agente:
   *"Importá `C:\Música\nos-siguen-pegando-abajo.wav` en mi-tema, separalo y analizalo"*.
   Llama a `source.import`, `source.separate` (minutos la primera vez, después
   queda en caché) y `source.analyze` (tempo, compases, tonalidad, secciones).
2. *"Armame un loop future funk de 16 compases con el riff del inicio"*. El
   agente busca candidatos de chop (`source.find_chops`: cada uno con un
   preview al tempo del tema, en la pestaña Chops), elige uno y explica por
   qué (podés elegir otro vos), escribe `song.yaml` (chop con filtros y
   sidechain al bombo, batería de un kit de la librería con `library.kits`,
   un bajo de `plugins.list`), lo valida y renderiza **v001**.
3. Escuchá. Comentá en el segundo justo (`M`), marcá me gusta / no me gusta.
4. *"Más filtrado en la intro, el bajo más gordo y el sample medio tono más arriba"*.
   El agente lee tu feedback, edita, renderiza **v002** y te cuenta el diff
   musical ("Subí el chop c1 un semitono…"), que también ves en Versiones.
5. Hacé A/B entre v001 y v002 con `S`. Si preferís volver atrás:
   *"volvé a la v001"* (`versions.restore`); lo próximo que renderice sale de ahí.

Todo vive en la carpeta del tema: `song.yaml` (la canción), `renders/vNNN/`
(mix, stems, metadatos, métricas), `feedback.jsonl` (lo que dijiste) y un repo
git con una versión = un commit. Borrar `renders/` no pierde nada:
`muestra render --version v001` la vuelve a generar igual.

Hay un tema de ejemplo armado de punta a punta (un loop sobre "Tiger Rag",
grabación de 1928 de dominio público) en
`Documentos/MuestraProjects/demo-e2e`: abrilo con
`uv run muestra serve --project <...>/demo-e2e --open`.

## Tiempos de referencia (notebook Windows, CPU)

| paso | tiempo |
|---|---|
| separar un tema de 3:14 (htdemucs) | ~4-5 min la primera vez; instantáneo después (caché) |
| analizar (tempo, compases, secciones) | ~20-30 s |
| buscar candidatos de chop + previews | ~5 s |
| re-render de 16 compases (validar + commit + audio + metadatos + oídos) | ~6.5 s por MCP; ~11 s con `muestra render` desde la terminal (incluye arrancar Python) |

## Para desarrollar

```sh
uv sync --all-extras
uv run pytest                    # todo el repo (las pruebas lentas reales: MUESTRA_RUN_SLOW=1)
uv run ruff check && uv run ruff format --check
cd ui && npm run typecheck && npm run lint && npm test && npm run test:e2e && npm run test:e2e:real
uv run python -m muestra.schemas.export   # regenerar schemas/ después de cambiar un modelo
```

Contratos: [docs/contracts.md](docs/contracts.md). Decisiones:
[docs/decisions.md](docs/decisions.md). Por paquete: `docs/ingest.md`,
`docs/render.md`, `docs/instruments.md`, `docs/library.md`, `docs/versions.md`,
`docs/ears.md`, `docs/server.md`, `docs/agents.md`, `ui/README.md`.
