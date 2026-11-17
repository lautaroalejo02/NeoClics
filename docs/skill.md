# La skill del agente (WP9)

Muestra no tiene LLM propio: el "cerebro" es el agente que el usuario ya usa. La skill en
[`skills/muestra/`](../skills/muestra/) le enseña a usar las herramientas reales, la receta
future funk, a leer las métricas y a traducir el feedback. Formato
[Agent Skills](https://code.claude.com/docs/en/skills): una carpeta con `SKILL.md`
(frontmatter `name` + `description`) y archivos de referencia que el agente carga cuando
los necesita.

| Archivo | Para qué | Tamaño |
|---|---|---|
| `SKILL.md` | cuándo usarla, arranque de sesión, ciclo, reglas duras, mapa de archivos | ~4 KB (se carga al activarse; la `description` está siempre en contexto, ~430 caracteres) |
| `workflow.md` | la sesión paso a paso con tiempos medidos, summary/request, batería sin oír, restore, honestidad | ~9 KB |
| `song-yaml.md` | referencia práctica de cada construcción, con fragmentos validados | ~11 KB |
| `recipes/future-funk.md` | confirmar la grilla con el humano, receta del primer loop, cuentas de tempo/tono, frases, ejemplo real e iteraciones | ~15 KB |
| `styles.md` | perfiles de 9.1 como movimientos concretos | ~5 KB |
| `ears.md` | cada alarma, qué cambiar, qué no mide | ~9 KB |
| `feedback.md` | frases del humano → cambios; cuándo preguntar | ~6 KB |
| `pads.md` | tomas de los pads (WP17): leerlas, ponerlas en la canción, limpiarlas, slices | ~5 KB |
| `troubleshooting.md`, `paid-tools.md` | problemas frecuentes; política de pagos y A/B ciego | ~7 KB |
| `examples/*.yaml` | canciones completas validadas y renderizadas | 2-6.5 KB c/u |
| `AGENTS.fragment.md` | bloque para pegar en AGENTS.md / CLAUDE.md | 1 KB |

La versión corta para clientes que solo tienen el servidor MCP es
[`src/muestra/mcp/guide.md`](../src/muestra/mcp/guide.md): la devuelven la herramienta
`muestra_guide` y el prompt `muestra.guide`, y es también el campo `instructions` del
servidor MCP (por eso es de una pantalla).

## Instalar en Claude Code

Claude Code busca skills en `.claude/skills/<nombre>/SKILL.md` del proyecto (el workspace
donde abrís `claude`) y en `~/.claude/skills/<nombre>/` del usuario
([docs oficiales](https://code.claude.com/docs/en/skills)).

Nivel proyecto (recomendado: el workspace del agente, que puede ser la carpeta del tema):

```sh
# macOS / Linux: enlace, se actualiza con el repo
mkdir -p <workspace>/.claude/skills
ln -s <MUESTRA_REPO>/skills/muestra <workspace>/.claude/skills/muestra
```

```powershell
# Windows: junction (no requiere admin) o copia
New-Item -ItemType Directory -Force <workspace>\.claude\skills
cmd /c mklink /J <workspace>\.claude\skills\muestra <MUESTRA_REPO>\skills\muestra
# o: Copy-Item -Recurse <MUESTRA_REPO>\skills\muestra <workspace>\.claude\skills\
```

Nivel usuario: lo mismo en `~/.claude/skills/muestra` (queda disponible en todos los
proyectos; no lo hace ningún script de Muestra). Además conectá el servidor MCP con
`muestra agent-setup` ([agents.md](agents.md)).

Verificado (2026-11-17, Claude Code 2.1.326, sin turnos de modelo y con un
`CLAUDE_CONFIG_DIR` temporal): copiada a `<tmp>/ws/.claude/skills/muestra`,
`claude plugin validate .claude/skills` → "Validation passed". No se corrió una sesión con
modelo para ver la activación automática.

## Instalar en Codex

Codex lee skills con el mismo formato en `.agents/skills/` desde el directorio actual hasta
la raíz del repo, y en `$HOME/.agents/skills/` para el usuario
([docs oficiales](https://developers.openai.com/codex/skills), consultadas 2026-11-17).

```sh
mkdir -p <workspace>/.agents/skills
ln -s <MUESTRA_REPO>/skills/muestra <workspace>/.agents/skills/muestra   # Windows: mklink /J
```

Si tu versión de Codex no tiene skills, pegá
[`skills/muestra/AGENTS.fragment.md`](../skills/muestra/AGENTS.fragment.md) en el
`AGENTS.md` del workspace (Codex lo lee siempre) con la ruta absoluta al repo. El servidor
MCP se registra con el bloque TOML que imprime `muestra agent-setup`. **Codex no está
instalado en esta máquina: nada de esto se verificó con Codex.**

## Cualquier otro agente

- Con MCP: la herramienta `muestra_guide` (o el prompt `muestra.guide`) da lo esencial;
  pedile al agente que la llame al empezar. Si puede leer archivos, que lea
  `skills/muestra/SKILL.md` y siga los enlaces.
- Sin MCP: adjuntá o pegá `SKILL.md` (y `workflow.md`, `song-yaml.md` si entran) y que use
  el CLI (`muestra tools --json`, `muestra <grupo> <acción> --project <p>`).
- Con soporte de `AGENTS.md`/`CLAUDE.md`: pegá `AGENTS.fragment.md`.

## Cómo se verificó el contenido

- `tests/skill/test_skill_examples.py` (corrida normal, < 2 s): extrae los bloques
  ```` ```yaml song ```` / ```` ```yaml fragment ```` de todos los `.md` de la skill, de
  `guide.md` y de este archivo, y los valida con el validador real
  (`validate_song(check_files=False)`); valida `examples/*.yaml`; aplica en cadena los
  ```` ```diff tiger-rag ```` de la receta y valida cada resultado; rechaza bloques `yaml`
  sin etiqueta; comprueba los enlaces relativos y el frontmatter; y que el validador
  rechace errores típicos.
- `tests/skill/test_skill_render.py` (opt-in, `MUESTRA_RUN_SLOW=1`; 5 passed en 94 s en esta máquina, mismos LUFS que en el proyecto de prueba): importa Tiger
  Rag de la librería de prueba, separa, analiza, valida con `check_files=True` y renderiza
  cada ejemplo con la librería y los instrumentos reales.
- A mano, en `C:\Users\lauti\Documents\MuestraProjects\skill-check` (fuera del repo):
  v001-v017 (ver la tabla en [skill-findings.md](skill-findings.md#renders)).
- `examples/build_examples.py` regenera los ejemplos (genera las listas de notas).

Nadie escuchó ningún render. Las decisiones musicales de la skill vienen de la receta del
spec, de los perfiles y de lo que midió ears; Lautaro tiene que juzgarlas de oído.
