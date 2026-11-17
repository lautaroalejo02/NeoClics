# What the UI uses from muestra-api

Every HTTP call the UI makes lives in one file, `ui/src/api/client.ts`.
WP8 wrote this list against the contract before the real API existed;
WP11 (integration, 2026-11-17) reconciled every item with what
`src/muestra/api/` really serves. **Each row now describes the real
behaviour**, verified by `npm run test:e2e:real` (Playwright against the
real server) and `tests/api/`. The mock (`ui/mock/server.mjs`) mirrors the
same shapes.

## Conventions

| What | Status |
|---|---|
| Same origin in production: `muestra serve` serves `ui/dist` at `/` (SPA fallback) | **real** (`docs/server.md`) |
| Base URL override `?api=http://host:port` or `VITE_MUESTRA_API` | UI only; the API allows CORS from the Vite dev origins only |
| Project-scoped tool params are sent **without** `project` | **real**: the server injects its project; a different one is `project_locked` (409) |
| Tool calls are `POST /tools/<mcp_name>` with a JSON body | **real** |
| Tool response = the `ToolResult` envelope `{tool, ok, data, files, errors, hint}` | **real, with HTTP status codes**: 200 ok, 422 expected tool failure, 409 other project, 501 not implemented, 500 internal. The body is always the envelope, so the client reads `ok`/`errors`, never the status (resolved: the UI already parsed the envelope on any status) |
| `long_running` tools answer 202 `{job_id}` | real; the UI never starts one, it only shows jobs from the websocket |

## Read endpoints (the UI's boot path)

| Endpoint | Real shape | Used for |
|---|---|---|
| `GET /project` (alias `/project/summary`) | `{project_path, config, song, versions: [{version, meta, metrics, feedback}], candidate_sets, analyses: {src: Analysis}, feedback, feedback_cursor, feedback_skipped, issues}`; versions newest first, only those with a valid `meta.json` | one round-trip at boot; mapped to the UI summary by `summaryFromProjectView()` in `client.ts`. **Resolved:** the UI's guessed `GET /summary` was replaced by this; `analyses` was added server-side for the chop panel |
| `GET /waveform?path=<audio>&points=N` | `{path, sample_rate, frames, duration_s, points, channels: [{min, max}]}` | waveform before the audio decodes. **Resolved:** replaces the guessed `GET /peaks/...`; converted to `[min, max]` pairs in `client.ts` |
| `POST /tools/versions_get {version}` | `{version, meta, metrics, song_yaml, ...}` | one version's meta + metrics (e.g. after a `version.created` without meta); getMeta/getMetrics share one request |
| `GET /files/<project-relative path>` | audio only (wav/flac/mp3/m4a/ogg/aiff) under `renders/` and `cache/previews/`, HTTP Range, `ETag` + `Cache-Control: no-cache` | mix, stems, lane stems, chop previews. **Resolved:** JSON (`meta.json`, `metrics.json`, `cache/analysis/*.json`) is NOT served here — it comes from `/project` and `versions.get` — so the file whitelist stays audio-only |

## Tools

| Tool | Used for | Fields read |
|---|---|---|
| `versions.list` | fallback when `/project` is missing | `versions[].{version, parent, created_at, summary}` |
| `project.open` | title/versions fallback | `project_path`, `versions` |
| `song.get` | fallback; normally the song comes in `/project` | `song.meta.title`, `song.chops` |
| `feedback.read` / `feedback.append` | ratings, timestamped comments, A/B choices (`ts` sent, aware UTC) | `events` |
| `chops.list` / `chops.choose` | candidate sets, manual override (the tool logs `chop_choice` itself) | `sets[]`; candidate ids are global `csNNN/candN` everywhere (model, schema, fixture, ingest, API, UI) |
| `versions.diff` | **the versions panel** shows its Spanish musical description (`changes`, `musical_summary`) and the ears delta (`metrics_delta`) when a row is expanded | `changes`, `musical_summary`, `metrics_delta` |

## Live events (`WS /events`)

| Event | Real behaviour |
|---|---|
| `job.updated` → `Job` | **Resolved:** a websocket that connects is first sent the state of every queued/running job, so a page opened mid-separation shows it |
| `version.created` → `{version, meta, metrics}` | **Resolved:** emitted when `meta.json` is valid and **re-emitted (upsert) when `metrics.json` lands** or a version is re-rendered; the UI updates meta/metrics of a known version in place (the Oídos panel refreshes without a reload) |
| `feedback.appended` → the event | real; de-duplicated against optimistic writes |
| `candidates.created` → `{set_id, source_id}` | real; the UI re-reads `chops.list` |
| `song.changed` → `{}` | real |

Events come from the server **watching the project folder**, so renders
triggered by a CLI or MCP process in another terminal appear live
(verified by `e2e-real`: a `muestra render` subprocess shows up in the page).

## Metrics (`metrics.json`, WP6 ears)

Alarm `message`, `fix` and `metrics.summary` are written by ears in
Spanish. Alarms with `at_s` are drawn on the waveform and are clickable.

## Not needed from the API

- Mute/solo/volume, loudness matching, A/B and blind mode are client side
  (stems play directly). Nothing is re-rendered.
- The "pendiente de leer por el agente" badge is derived: an event is
  pending until a version with a later `created_at` exists.
