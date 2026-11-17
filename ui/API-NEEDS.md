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

---

# WP14/WP15 additions — reconciled with the backend

The transport mapping lives in `ui/src/api/client.ts` (+ `api/token.ts`).
WP15 implements the fields/events below and verifies token links, longer
manual choices and CLI draft playback against the real server. Optional
readers remain tolerant of older servers.

## 1. `chops.choose` (finding 2)

Backend: a valid manual candidate is recorded before validation. The call
can still fail for a stale candidate, invalid adjustment or audio overrun;
the UI confirms that the choice was recorded even in those cases.
The UI reads, **all optional** (`ChopsChooseResult` in `types.ts`):

| field | meaning | UI |
|---|---|---|
| `chop.id` or `chop_id` | chop id actually written | if it differs from the requested one: "Quedó guardado como chop «c1_2» (no pisé «c1»)" |
| `requested_chop_id` | what was asked | informational |
| `rescaled` (`true` or `{clip, repeat_from, repeat_to}`) | a clip's `repeat` was rescaled to keep its span | "Ajusté la repetición del clip (repetición 8 → 5)…" |
| `recorded` | the `chop_choice` event is on disk | informational |
| `message`, `notes[]`, `warnings[]` | backend text, any language | shown as secondary lines |

Errors: `ApiError.issues[].code` is mapped to Spanish in `text/es.ts`
(`clip_overrun`, `clip_position`, `chop_overrun`, `stale_candidates`,
`ref_not_found`, `grid_mismatch`, `invalid_value`, `out_of_range`,
`duplicate_id`, `file_not_found`, `invalid_audio`, `project_locked`,
`not_implemented`, `internal_error`, …). The backend's English text is kept
as "Detalle técnico". If the call fails the UI reloads `feedback.read`; if a
`chop_choice` for that candidate appeared it says the choice is not lost,
else it offers one click "Dejárselo al agente como comentario" (a normal
`comment` event through `feedback.append`).

## 2. Versions without audio (finding 3)

Uses `versions.list` (already real, WP5) in addition to `GET /project`:
`rendered`, `rerenderable`, `request`, `summary`, `parent`, `created_at`,
`liked`. A version is playable iff it is in `/project` (has `meta.json`) and
`rendered !== false`; every other listed version is shown greyed out with
"Sin audio: pedile al agente que la regenere (render de vNNN)" and a copy
button. If `versions.list` fails the UI behaves as before.

## 3. Drafts and previews (finding 4) — real WP15 shape

Optional fields of `GET /project` (and `/project/summary`):

```jsonc
"drafts":   [ { "id": "d-0123456789ab",             // stable key (upsert)
                "path": "renders/d-0123456789ab/mix.wav", // served by /files
                "created_at": "2026-11-17T10:00:00Z",
                "label": "Probando el bajo",         // free text
                "range_bars": [5, 9],                // [start, end) 1-indexed, if partial
                "duration_s": 7.5 } ],
"previews": [ { "id": "grid-src1", "path": "cache/previews/src1/grid.wav",
                "kind": "grid" | "chop" | "preview", "source_id": "src1" } ]
```

Only `path` is required per entry; malformed entries are ignored. Live
updates: websocket event `draft.created` (`data` = one draft object, an
upsert by `id`) and `drafts.changed` (`data` = `{}`, the UI re-reads
`/project`). Playback is one-shot through the same engine
(`engine.playPreview(key, path, null, false)`), never as a version, never in
A/B. The area is hidden when the server exposes nothing.

## 4. Access token for non-loopback use (finding 6)

* The page takes `?token=…` from its URL on first load, keeps it in
  `sessionStorage` (survives reloads of the tab), and removes it from the
  address bar with `history.replaceState`.
* **HTTP**: every request (tools, `/project`, `/waveform`, `/files`, `/jobs`)
  carries `Authorization: Bearer <token>` **and** `X-Muestra-Token: <token>`
  (`TOKEN_HEADERS` in `client.ts`; drop either one there if the backend only
  wants one).
* **WebSocket**: `/events?token=<token>` (browsers cannot set headers on a
  WebSocket).
* **401 or 403** from any request → a Spanish screen: open the link that
  `muestra serve` prints. A stale stored token is forgotten.
* No token in the URL and none stored → nothing is sent and nothing changes
  (loopback keeps working as before). The static UI (`/`, assets) should be
  servable without the token, otherwise the page itself cannot load to read it
  from the URL.
* Verified: both headers, query and cookie are accepted; WebSocket uses
  query/cookie. `/` and `/assets/*` load without auth. LAN API/audio stay
  authenticated and remote writes stay restricted to feedback/choice.

## 5. Alarm codes (finding 5)

Spanish titles + explanations exist for every code in `src/muestra/ears/*.py`
(list in `ui/test/es.test.ts`, `EARS_CODES`). A new ears code shows the generic
"Aviso de los oídos del agente" + the backend's own `message` until a row is
added to `ALARMS` in `ui/src/text/es.ts`. The backend's `message` (Spanish)
is always shown first.

## 6. Stem mode (finding 1) — nothing needed from the API

Client side only. AudioWorklet needs a **secure context**: from a phone over
plain `http://192.168.x.y:8765` the worklet does not exist, and the app uses
the native fallback (DynamicsCompressor + hard-bounded WaveShaper). Serving
over HTTPS (or `localhost`) gives the better limiter.
