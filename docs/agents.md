# Connect a coding agent to Muestra

Muestra works with Claude Code, Codex CLI, or any MCP client. It runs locally using your
existing coding agent; the studio makes no LLM/API calls. No global Python installation of
Muestra is needed. Use absolute paths, particularly when the agent workspace differs from
the music project.

## Prepare once

1. Install Python tooling [uv](https://docs.astral.sh/uv/), git and ffmpeg. On Windows,
   `winget install astral-sh.uv`, `winget install Git.Git`, and
   `winget install Gyan.FFmpeg` are options. On macOS use `brew install uv git ffmpeg`.
2. In the Muestra checkout run `uv sync --extra server`.
3. Create a project: `uv run muestra project create --name my-song --dir /path/to/music`.
   To use an existing project, point the next commands at its folder containing `muestra.toml`.
4. Run `uv run muestra doctor --project /path/to/music/my-song`. It reports missing tools,
   library roots/indexes, configured plugin files, cached separation models and port conflicts,
   with concrete fixes. It does not load native plugins, validate model weights, or download
   anything. Native audio packages/models implemented by other work packages need their
   respective extras/setup; the server extra alone does not install separation models.
5. Run `uv run muestra serve --project /path/to/music/my-song --open` in a second terminal.
   Keep it running so the human can listen. If the UI is not built, the page explains how.

## Claude Code

Generate exact machine-specific configuration from the Muestra checkout:

```sh
uv run muestra agent-setup --project /path/to/music/my-song
```

For one-step project-local installation, pass the **agent workspace** directory:

```sh
uv run muestra agent-setup --project /path/to/music/my-song --write /path/to/agent-workspace
```

This merges a `muestra` server into `.mcp.json`, preserves other server entries, and also
writes a project-local Codex block. It never selects a user's global configuration itself.
The generated Claude entry is equivalent to:

```json
{
  "mcpServers": {
    "muestra": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/Muestra", "--extra", "server",
               "muestra", "mcp", "--project", "/absolute/path/to/my-song"]
    }
  }
}
```

The generator resolves the absolute uv executable when available, which avoids GUI agents
having a different PATH. It also prints the corresponding command:

```sh
claude mcp add --transport stdio --scope project muestra -- uv run --directory /absolute/path/to/Muestra --extra server muestra mcp --project /absolute/path/to/my-song
```

Start Claude Code in that agent workspace, approve the project MCP server, and open `/mcp`.
`claude mcp get muestra` checks connection health without running a model turn. An unapproved
project server is shown as pending approval, not started. Ask the agent to call
`muestra_guide`, then `song_get` and `feedback_read` before changing anything.

Configuration format verified against the [official Claude Code MCP documentation](https://code.claude.com/docs/en/mcp)
on 2026-11-17. On this machine, Claude Code **2.1.326** returned **Connected** using the
generated `.mcp.json` and an isolated temporary `CLAUDE_CONFIG_DIR`; no global config was
modified and no model turn ran. Its management CLI does not list individual tool names;
full discovery/calls are verified using the official MCP SDK client in automated tests.

## Codex CLI

Run the same `agent-setup` command. Copy its TOML block into `~/.codex/config.toml`, or use
the trusted agent workspace's `.codex/config.toml` produced by `--write`:

```toml
[mcp_servers.muestra]
command = "uv"
args = ["run", "--directory", "/absolute/path/to/Muestra", "--extra", "server", "muestra", "mcp", "--project", "/absolute/path/to/my-song"]
startup_timeout_sec = 60
tool_timeout_sec = 1800
```

The generated block substitutes the absolute uv/repository/project paths. A long timeout
lets CPU separation finish. Trust the workspace so its project configuration can load;
restart Codex after editing configuration. Use `codex mcp list` / `codex mcp get muestra` to
inspect registration, then `/mcp` in an interactive session to inspect available tools.
Ask it to call `muestra_guide` first. The global config snippet is printed for you to apply;
verification here did not edit it.

Format verified against [official OpenAI Codex MCP documentation](https://developers.openai.com/codex/mcp)
on 2026-11-17, including `[mcp_servers.<name>]`, command/args and the timeout fields.
**Codex CLI was not available on PATH on this machine**, so actual Codex-side registration,
tool discovery and model-turn use were not verified. The identical launch command was
verified with the SDK's stdio client from a directory outside the repository.

## Any MCP client

Configure a stdio server executable and arguments exactly as in the generated entry:

```text
command: uv
args: run --directory <Muestra-repo> --extra server muestra mcp --project <music-project>
```

Arguments are an array in your client's configuration; keep paths containing spaces as one
array element. Stdio stdout contains only MCP messages; don't wrap the command in a shell
that prints a banner. `muestra mcp` starts the server; it doesn't return a prompt. Your
client owns its process lifecycle.

All registry tools appear automatically using their `mcp_name` (e.g. `source_find_chops`),
with param schemas, descriptions/units and readonly annotations. Project-scoped tools may
omit `project` to use the current project. Selection starts with `--project`, then
`MUESTRA_PROJECT`, then the nearest parent `muestra.toml`. `project_create` and `project_open`
change the current selection. Explicit per-call `project` overrides only that call.
Each MCP result includes the acted-on project in text and `_meta.project`; its structured
content remains the contract `ToolResult` envelope. Errors set `isError=true` and include
issues/hints. Long work blocks and reports progress if the tool supports it and the client
requests progress.

The extra `muestra_guide` tool and `muestra.guide` prompt load the bundled workflow guide.
Resources expose `muestra://song`, `muestra://metrics/latest` and
`muestra://feedback/unread`. Reading the feedback resource advances that server's resource
cursor; explicit `feedback_read` cursors are independent. Switching project resets the
resource cursor. Prefer `feedback_read(cursor=...)` for reproducible agent iterations.

For clients that prefer Streamable HTTP:

```sh
uv run muestra mcp --project /absolute/path/to/my-song --transport streamable-http --port 8766
```

Connect to `http://127.0.0.1:8766/mcp`. This uses the official SDK transport with its default
host/origin protection. It is a separate server from the UI HTTP API. Stdio is the simplest
local setup; both transports expose the same generated tools.

## Agents without MCP: CLI only

```sh
uv run --directory /absolute/path/to/Muestra --extra server muestra tools --json
uv run --directory /absolute/path/to/Muestra --extra server muestra song get --project /absolute/path/to/my-song
uv run --directory /absolute/path/to/Muestra --extra server muestra song validate --project /absolute/path/to/my-song
uv run --directory /absolute/path/to/Muestra --extra server muestra feedback read --project /absolute/path/to/my-song --cursor 0
uv run --directory /absolute/path/to/Muestra --extra server muestra render --project /absolute/path/to/my-song --range-bars '[1,5]' --summary 'Filtered intro, stronger bass'
```

The CLI generates `muestra <group> <action>` from the registry, with top-level `render` and
`preview`. Every field has a flag, snake_case becomes kebab-case, booleans have `--no-...`,
and nested values accept JSON. Dotted flags can build nested objects:

```sh
uv run muestra feedback append --project /path/to/my-song --event.type comment --event.version v001 --event.at-s 12 --event.text 'The kick covers the sample here'
```

`--params-json '{...}'` accepts a complete params object; `--params-json -` reads it from
stdin. Individual flags override that JSON. `--project` or `MUESTRA_PROJECT` selects a
project; running directly inside one also discovers its nearest containing folder. Note
that `uv run --directory <repo>` changes cwd, so include `--project` for external projects.
Piped stdout is JSON automatically; `--json` forces JSON on a terminal. TTY output is
readable. Tool failures print structured issues and exit nonzero. CLI long work blocks.

## Troubleshooting

- **No tools / cannot start:** run the generated command directly. An idle stdio server
  waiting for MCP input is expected. Check `uv sync --extra server`, absolute paths, and
  whether the agent sees the uv executable. Run `muestra doctor`.
- **Wrong song:** check `_meta.project` and the printed path, then call `project_open` with
  an absolute path. HTTP project selection stays fixed until its process is restarted.
- **Tool not implemented:** the server works, but that package is still a stub in this
  branch. Integrate the corresponding work package; don't mistake the structured error
  for a completed audio operation.
- **Unexpected error:** inspect `<project>/cache/logs/muestra.log`; clients get a generic
  issue without exception details. Fix the listed validation fields before retrying.
- **Busy port:** `serve` chooses a free one and prints it. MCP HTTP uses the requested port;
  choose another with `--port` if it is occupied.

See [server.md](server.md) for jobs, watched events, summaries, waveform format and security.
The automated tests spawn the actual stdio server, discover every tool, create a project,
validate a song, append/read feedback, exercise progress/errors, and check the generated
external launch command. This transport verification is not an audio-quality test or a
substitute for the later real-agent music session.
