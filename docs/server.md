# Muestra local server

From the source checkout, install once with `uv sync`, then:

```sh
uv run muestra serve --project /absolute/path/to/my-song --open
```

The server prints its URL and holds the selected socket until uvicorn takes over. The default
is `http://127.0.0.1:8765`; if that port is occupied it binds an available port instead. There
is one project per HTTP process. CLI and MCP can run in separate processes against that same
project. Start another HTTP process to listen to a different project.

`/` serves `ui/dist` when built, including SPA navigation. Without a build, `/` explains how
to build it. Unknown API paths and missing assets return 404. `--host 0.0.0.0` explicitly
enables LAN access and prints the phone URL **with its access token** (WP13, see below).

## API reference

Use **`/docs`** for interactive OpenAPI documentation, **`/openapi.json`** for its schema.
All registry tools are generated at application construction, including tools added later to
another package's `TOOLS` list. No hand-maintained list of HTTP routes is needed.

| Route | Behavior |
|---|---|
| `POST /tools/<mcp_name>` | Tool params as a JSON object; server injects its project |
| `GET /tools/<mcp_name>` | Available only for readonly tools; params as query fields |
| `GET /jobs/<id>` | Contract `Job` state and final `ToolResult` |
| `DELETE /jobs/<id>` | Cancel a queued job; 409 once running or finished |
| `GET /project` | Song, config, versions with meta/metrics/feedback, candidates and feedback cursor |
| `GET /waveform?path=renders/v001/mix.wav&points=1024` | Downsampled min/max peaks per audio channel |
| `GET /files/<project-relative-audio-path>` | Audio with Range, Content-Range and MIME headers |
| `HEAD /files/<project-relative-audio-path>` | Audio headers without a body |
| `WS /events` | Contract `WsEvent` stream |

`/project/summary` and `/waveform/peaks` are aliases. Query values for nested objects and lists
are JSON strings; boolean queries accept `true`/`false`. POST bodies and tool outputs preserve
the contract envelope `{tool, ok, data, files, errors, hint}`. `X-Muestra-Project` names the
bound project. Tool params named `project` are absent from the HTTP documentation: a supplied
different project is rejected. `project.open` can report this process's own project;
`project.create` returns `project_locked` over HTTP because it cannot switch this process.
Use CLI or MCP to create/open another project.

Success is 200; long work is 202 with `{job_id}`. Invalid parameters/JSON and expected tool
failures are 422; another project is 409; an unimplemented tool is 501; unexpected failures
are 500; a full queue is 503. Tool failures always include issues and hints. Unknown routes
and expired job IDs are 404. Framework file/query errors use HTTP `detail` responses.

### Jobs and progress

Registry `long_running` tools enter a **single worker**, with at most 32 pending requests.
Two HTTP renders cannot run simultaneously. Parameters are validated before queueing.
GET/job events show `queued → running → done|failed`. Final `result` is the exact tool
envelope. History is process-local, retaining at most 256 jobs; restarting loses it.

Running native/audio calls cannot safely be killed in a Python thread. Queued cancellation
uses contract status `failed` and an issue with code `cancelled`; running cancellation is
409. Shutdown cancels pending work and lets the running operation finish. This does not
serialize renders launched by a separate MCP process; the render package owns cross-process
version allocation and render locking.

Optional progress convention for tool authors:

```python
def my_tool(params, *, progress=None):
    if progress:
        progress(0.25, "Analyzing beats")  # fraction 0..1, optional message
    return MyResult(...)
```

The adapter detects the declared keyword; existing `fn(params)` tools are unchanged. CLI and
MCP calls block until completion. HTTP updates the Job and broadcasts `job.updated`; MCP
emits official progress notifications when the client requested them. Existing stubs do not
report intermediate progress. This callback is an additive adapter convention, not a change
to `ToolSpec` or persisted schemas.

### Disk events

The API watches the disk independently of tool calls. A portable 100 ms polling watcher
checks `song.yaml`, render meta/metrics, `cache/chops/*/*.json`, and `feedback.jsonl`. Song,
render and candidate changes debounce for 200 ms. It retries incomplete JSON and validates
render metadata before announcing a version. Baseline files are not replayed on startup.
Feedback emits only after a complete newline-terminated, valid event is present.

| Event | `data` |
|---|---|
| `job.updated` | Contract Job |
| `version.created` | `{version, meta}` with fully validated metadata |
| `candidates.created` | `{set_id, source_id}` |
| `feedback.appended` | The validated feedback event |
| `song.changed` | `{}` |
| `grid.changed` | `{source_id}` — `source.regrid` rewrote `cache/analysis/<src>.json` (stale candidate sets were retired) or `source.preview_grid` wrote `cache/previews/<src>/grid/index.json` (WP12); refresh `/project` (`analyses`, `grid_previews`, `candidate_sets`) |

Treat version/candidate events as **upserts**, keyed by ID: metadata and later metrics writes
can announce the same version again, and a manual chop selection changes an existing set.
Refresh `/project` to get the complete latest state. Each connection buffers 256 events and
drops its oldest on overflow; there is no replay cursor. Refresh the summary on reconnect.
Watch events include changes from other processes, not just HTTP-triggered work.

### Project summary and waveforms

`/project` returns:

```text
{project_path, config, song, versions: [{version, meta, metrics, feedback}],
 candidate_sets, feedback, feedback_cursor, feedback_skipped, issues}
```

Versions are newest first; incomplete/invalid metadata and candidates are skipped and
reported in `issues`. Missing/invalid metrics are null. Feedback aggregates contain
`likes`, `dislikes`, `comments`, and `events`; these count recorded rating events rather
than assigning persistent user identities. `feedback_skipped` preserves bad-line diagnostics.

Waveforms return `{path, sample_rate, frames, duration_s, points, channels}`. Each channel
contains parallel `min`/`max` arrays with `points` buckets over the entire file. The last
bucket covers the end of the file. `points` is 1..8192, default 1024, capped to frame count.
WAV and other libsndfile-supported audio is read in bounded blocks, with at most 32 channels;
the cache keys include modification time and file size. Peaks are audio amplitudes, not dB.

## Security and concurrency

- Localhost binding by default, no accounts, telemetry or LLM calls. A loopback bind only
  answers requests whose `Host` is `127.0.0.1`, `localhost` or `::1` (blocks DNS
  rebinding: a web page whose name resolves to 127.0.0.1 sends a matching Origin and Host).
- **LAN mode (WP13).** A non-loopback `--host` (e.g. `0.0.0.0`) generates a random token
  (`secrets.token_urlsafe(16)`, new at every start) and prints
  `http://<LAN-IP>:<port>/?token=<token>`. Clients **not on this computer**:
  - must present the token: `?token=` on any request (the response then sets an
    HttpOnly, SameSite=Strict cookie `muestra_token`, so the UI's own requests, audio and
    websocket work unchanged), or `Authorization: Bearer`, `X-Muestra-Token`, or that cookie. Without
    it: API/audio return `401` (`unauthorized`); the public UI shell shows its
    friendly Spanish screen asking for the complete link;
    the `/events` websocket closes with 1008;
  - may only call **read-only tools plus `feedback.append` and `chops.choose`** (what the
    listening UI writes), `GET /project`, `/files`, `/waveform`, `GET /jobs/<id>` and the UI.
    Everything else (render, restore, `source.import`, `library.index`, `plugins.scan`,
    `DELETE /jobs/<id>`, …) is `403 local_only`. No allowed tool takes a filesystem path:
    `project`/`project.open.path` must equal the served project (`project_locked`), and
    `library.search.similar_to` only looks up the index.
  - Loopback clients (the agent, the laptop's browser) need no token and keep every tool.
  - `?token=` is stripped from GET tool query parameters (it is not a tool param).
- `muestra mcp --transport streamable-http` refuses a non-loopback `--host` (exit 2): MCP
  exposes every tool with no authentication, and agents run on this computer.
- Before WP13, LAN access granted project-edit access (and arbitrary local file reads via
  `source.import`, index writes via `library.index`) to anyone who could connect.
- CORS allows only the Vite development origins `http://localhost:5173` and
  `http://127.0.0.1:5173`. HTTP and websocket requests carrying other cross-site Origins
  are rejected; the listening UI's own origin is accepted.
- Audio is restricted to `renders/` and `cache/previews/`, and known audio extensions.
  Canonical path containment checks reject symlink escapes. Portable path validation rejects
  `..`, absolute paths, drive letters, ADS colons, empty/dot segments, Windows
  reserved filenames, invalid characters and trailing dots/spaces. Sources, original stems,
  song/config/log files and arbitrary local files are not served through `/files`.
  Backslashes are normalized to `/` before these checks. The UI shell (`/`,
  `/assets/*`) is public so it can read `?token=` and show its Spanish auth
  screen; API/audio endpoints and remote write restrictions still require auth.
- Registry tools intentionally retain local filesystem capabilities (e.g. source import and
  library indexing). The file-serving restriction is not a sandbox for tool execution.
- All CLI/MCP/HTTP feedback reads and appends take the same OS-backed
  `cache/locks/feedback.lock` through portalocker, including on Windows. `chops.choose` also
  participates because it can record a manual choice. This protects whole
  Unicode JSONL records and cursor reads across these transports. **WP13:** `append_event`
  itself is now atomic across processes too (an OS lock on
  `cache/locks/feedback-append.lock` around a single `os.write` of the whole line on an
  `O_APPEND` descriptor), so direct callers are safe; readers only count lines that end in
  `
` (a line being written is read next time, not skipped) and split on `
` only.
  Measured with the auditor's script (two processes × 400 appends, Windows): 782 lines
  before, 800 after.
- Unexpected tool exceptions return a generic `internal_error` and log a traceback privately
  to `cache/logs/muestra.log` in the acted-on project. An unavailable project cannot provide
  a project log. `NotImplementedError` becomes a clean `not_implemented` on every surface.

## Verified implementation baseline

On Windows, this package was built against FastAPI 0.163.1, uvicorn 0.54.0, official MCP
Python SDK 2.3.0, portalocker 4.4.2 and Python 3.12. The CLI uses the standard-library
argparse framework. Since WP11 its dependencies are in the base install (`uv sync`); the integrator owns
`uv.lock`. macOS uses the same Python code and portalocker backend but has not been run here.
