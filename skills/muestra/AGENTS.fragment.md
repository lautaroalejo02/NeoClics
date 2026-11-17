<!-- Pegá este bloque en el AGENTS.md (Codex, otros agentes) o CLAUDE.md del workspace
     del agente. Reemplazá <MUESTRA_REPO> por la ruta absoluta al repo de Muestra. -->

## Música con Muestra

Este workspace hace música con Muestra (estudio local; el humano escucha en el browser y
comenta en castellano). Antes de tocar `song.yaml` o llamar herramientas `muestra_*` / el
CLI `muestra`, leé `<MUESTRA_REPO>/skills/muestra/SKILL.md` y seguí sus reglas; cargá los
demás archivos de esa carpeta solo cuando los necesites (receta, referencia de
`song.yaml`, alarmas de ears, feedback). Si no podés leer archivos fuera del workspace,
llamá a la herramienta MCP `muestra_guide`.

Siempre: arrancá con `feedback_read` y la métrica de la última versión; validá antes de
renderizar; pocos cambios por versión; nunca digas que algo "suena bien" (no podés oír);
nunca compres ni te registres en nada.
