# What the UI uses from muestra-api (for WP7)

Every HTTP call the UI makes lives in one file, `ui/src/api/client.ts`.
This list says, for each one, whether it is in the contract
(`docs/contracts.md`, `src/muestra/schemas/api.py`, `schemas/`) or is an
**assumption / request**. If the real API differs, the fix goes in
`client.ts` only.

Legend: **[contrato]** = in the contract as written. **[supuesto]** =
the contract is silent or ambiguous; the UI assumes this. **[pedido]** =
not in the contract; please add it (the UI already handles it being missing).

## Conventions

| What | Status | Notes |
|---|---|---|
| Same origin in production: the API serves `ui/dist` at `/` | **[supuesto]** | Build uses `base: "./"`, so `ui/dist` can also live under a sub-path. The SPA has no client-side routes: serving `index.html` at `/` is enough. |
| Base URL override `?api=http://host:port` or `VITE_MUESTRA_API` | UI only | For a UI served from somewhere else. That needs CORS on the API (see below). |
| Project-scoped tool params are sent **without** `project` (and `project.open` without `path`) | **[contrato]** R11 + **[supuesto]** | R11: one project per server process, routes never carry it. WP7's adapter injects `project` when it is omitted (checked in its worktree). If a client sends a *different* path, WP7 answers `project_locked`; the UI never does. |
| Tool calls are always `POST /tools/<mcp_name>` with a JSON body | **[contrato]** | The UI never uses the `GET` variant. |
| Response of a non-long-running tool = the `ToolResult` envelope `{tool, ok, data, files, errors, hint}`, HTTP 200 also when `ok: false` | **[supuesto]** | The UI also copes with non-2xx and non-JSON bodies (shown as an error with the HTTP status). It never calls a `long_running` tool, so it never receives `{job_id}`. |
| CORS: only needed when the UI is not same-origin (e.g. Vite dev without proxy) | **[pedido, opcional]** | Dev uses Vite's proxy, so not required. |

## Tools

| Tool (route) | Status | Used for | Fields read |
|---|---|---|---|
| `versions.list` (`POST /tools/versions_list`) | **[contrato]** | version history | `versions[].{version, parent, created_at, summary}`. `liked` is ignored: the UI derives ratings from `feedback.read` so they update live. **Fallback:** if it fails (still a WP5 stub returning `not_implemented`), the UI uses `project.open.versions` + each `meta.json`. |
| `project.open` (`POST /tools/project_open`, body `{}`) | **[contrato]** | title fallback (folder name) and versions fallback | `project_path`, `versions` |
| `song.get` (`POST /tools/song_get`) | **[contrato]** | title (`song.meta.title`), chop ids (`song.chops[].{id, source}`) to suggest the `chop_id` for `chops.choose` | `song.meta.title`, `song.chops` |
| `feedback.read` (`POST /tools/feedback_read`, body `{}`) | **[contrato]** | all feedback, newest last | `events` (feedback union). `next_cursor`, `skipped` not used yet (the websocket delivers appends). |
| `feedback.append` (`POST /tools/feedback_append`, `{event}`) | **[contrato]** | likes, comments (general and timestamped), `ab_choice` | sends a full event **including `ts`** (aware UTC, `...Z`). Every builder is tested against `schemas/feedback.schema.json`. Expects `ok: false` + `errors` on a rejected event. |
| `chops.list` (`POST /tools/chops_list`, body `{}`) | **[contrato]** | candidate sets | `sets[]` = `ChopCandidateSet` |
| `chops.choose` (`POST /tools/chops_choose`) | **[contrato]** | manual override | sends `{candidate_id: "csNNN/candN", chop_id, mode: "manual", adjust?: {start_beats, length_beats}}`; `adjust` only when non-zero. Validated against `schemas/tools/chops.choose.params.schema.json` (minus `project`). **[supuesto]** the tool itself appends the `chop_choice` feedback event in manual mode (decisions WP0-20), so the UI does **not** append it too. |
| `versions.diff` | **[contrato]**, wired but unused | client method exists | the UI shows `meta.diff` (vs the parent) directly. |

**Contradiction to resolve (not the UI's to fix):** `docs/contracts.md`
(R5) says `ChopCandidate.id` is the *global* `csNNN/candN`; the pydantic
model and `schemas/chop-candidates.schema.json` say *local* `candN`
(pattern `^cand\d+$`), and the fixture uses local ids. The UI accepts both
and always sends the global id to `chops.choose`.

## Files (`GET /files/<project-relative path>`, Range)

| Path | Status | Notes |
|---|---|---|
| `renders/vNNN/meta.json` | **[contrato]** (under `renders/`) | Needs `version, parent, created_at, request, summary, diff, duration_s, sample_rate, bpm, range_bars, mix, tracks[{id, kind, stem, lanes[{id, stem}]}]`. `duration_s` missing → computed from `range_bars` and `bpm`. 404 → the version is not listed (not playable). |
| `renders/vNNN/metrics.json` | **[contrato]** | 404 → "sin métricas". Uses `loudness.*`, `spectral.mix`, `low_end_masking.*`, `alignment.max_abs_offset_ms`, `tonal.*`, `clicks.count`, `style_similarity?`, `alarms[{code, severity, message, track?, at_s?}]`. Alarm `message` is shown as-is: **[pedido]** WP6, write it in Spanish (the UI adds a Spanish title per `code`). `at_s` makes an alarm clickable (jump). |
| `renders/vNNN/mix.wav`, `stems/<track>.wav`, `stems/<track>.<lane>.wav` | **[contrato]** R7 | Fetched whole (decoded audio is needed). 24-bit PCM mix and 32-bit float stems both decode natively in Chromium (verified); a built-in WAV decoder is the fallback. |
| `cache/previews/<src>/<set>/<cand>.wav` | **[contrato]** | chop previews (looped audition + mini waveform). |
| `cache/analysis/<src>.json` | **[pedido]** | NOT in the `/files` whitelist (only `renders/` and `cache/previews/`), and `source.analyze` is a long-running *compute* tool, so the UI cannot read a source's sections/downbeats to draw "where the candidate sits in the source". Please either whitelist `cache/analysis/` for read, or include analyses in `/summary` (below). Without it the UI draws the candidates on a bare timeline. |
| Caching headers | **[pedido, opcional]** | `ETag`/`Last-Modified` + `Cache-Control: no-cache` let the browser revalidate instead of re-downloading WAVs. Renders never change once written (except a re-render of the same version), so long caching is fine. |

## Jobs and live events

| What | Status | Notes |
|---|---|---|
| `WS /events` → `WsEvent{type, ts, data}` | **[contrato]** | URL derived from the base (`ws://` / `wss://`). Unknown types ignored. Reconnects with backoff (0.8 s → 10 s) and **re-reads everything** after a reconnect, since events may have been missed. |
| `job.updated` → `data` = `Job{id, tool, status, progress, message, result}` | **[contrato]** | Shown with progress (`progress: null` = indeterminate bar). `done` hides after 5 s; `failed` stays with `result.errors[0].message`. **[pedido]** on connect, re-send the current state of running jobs (the mock does), so a page opened mid-separation shows it. |
| `version.created` → `data` = `{version, meta}` | **[contrato]** | If `meta` is missing the UI fetches `renders/<v>/meta.json`. **[supuesto]** emitted after `meta.json` exists; `metrics.json` may come later (the UI tolerates a 404 and shows "sin métricas"). **[pedido]** a `metrics` update event (or a second `version.created`) when `metrics.json` lands later, so the Oídos panel refreshes without a reload. |
| `feedback.appended` → `data` = the event line | **[contrato]** | Also accepts `{event: ...}`. De-duplicated against the UI's own optimistic writes by content. |
| `candidates.created` → `data` = `{set_id, source_id}` | **[contrato]** | The UI re-reads `chops.list`. |
| `song.changed` → `{}` | **[contrato]** | The UI re-reads the title. |
| `GET /jobs/<id>` | **[contrato]**, not used | The UI never starts long-running jobs; it only displays them from the websocket. |

## Optional accelerators (asked of WP7; the UI works without them)

Both are read through `client.ts`; a 404 is remembered and never retried.

### `GET /summary` **[pedido]**

One round-trip at boot instead of 2 + 2×N requests. Assumed shape
(`ProjectSummary` in `src/api/types.ts`), every field optional:

```json
{
  "project_path": "...", "title": "fixture-loop", "has_song": true,
  "versions": [{"version": "v001", "parent": null, "created_at": "...", "summary": "..."}],
  "metas":    {"v001": {"...RenderMeta": "..."}},
  "metrics":  {"v001": {"...Metrics": "..."}, "v002": null},
  "analyses": {"src1": {"...Analysis": "..."}}
}
```

### `GET /peaks/<project-relative wav>?buckets=N` **[pedido]**

Lets the waveform appear before the audio is decoded (big renders, slow
phones). Shape: `{"peaks": [[[min, max], ...], ...], "duration_s": 32.0}` —
one array per channel, `N` buckets, values in −1..1. Only for paths the
`/files` whitelist allows. Without it the UI computes peaks from the decoded mix.

## Not needed from the API

- Mute/solo/volume, loudness matching, A/B and blind mode are pure client
  side (stems are played directly). Nothing is re-rendered.
- The "pendiente de leer por el agente" badge is derived: an event is
  pending until a version with a later `created_at` exists. **[pedido,
  opcional]** if WP9 wants an explicit read marker, a per-project
  "last read cursor" (`feedback.read` already returns `next_cursor`) exposed
  to the UI would make this exact.
