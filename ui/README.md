# muestra-ui (WP8)

The local listening room for Muestra: listen to each version the agent
renders, compare any two **instantly at the same position**, mute/solo the
render's stems without re-rendering, pick sample chops, and leave feedback
(likes, timestamped comments, A/B choices) that the agent reads from
`feedback.jsonl` before its next iteration. UI language: Spanish (rioplatense).

React 19 + Vite 8 + TypeScript 6, Web Audio for playback. No CDN, no
telemetry, no external requests: fonts are bundled, the app works offline.

## Quick start

```sh
cd ui
npm ci
npm run dev:all        # mock API on :8777 + Vite on http://localhost:5173
```

Or in two terminals: `npm run mock` and `npm run dev`. Vite proxies `/tools`,
`/files`, `/jobs`, `/events`, `/project` and `/waveform` to `MUESTRA_API`
(default `http://localhost:8777`), so dev is same-origin like production.

Against the real API instead of the mock:

```sh
muestra serve --project path/to/project        # prints its URL, default :8765
MUESTRA_API=http://localhost:8765 npm run dev
```

## Scripts

| | |
|---|---|
| `npm run dev` | Vite dev server (proxy to `MUESTRA_API`) |
| `npm run mock` | mock API (see below) |
| `npm run dev:all` | both |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run lint` | ESLint (typescript-eslint, react-hooks) |
| `npm test` | unit + engine + component tests (Vitest, jsdom). Component tests spawn the mock server on a free port. |
| `npm run build` | typecheck + production build to `ui/dist` |
| `npm run test:e2e` | build, then Playwright (headless Chromium, desktop + phone) against the mock serving `ui/dist` |
| `npm run test:e2e:real` | build, then Playwright against the REAL muestra-api serving `ui/dist` for a real project built by `tests/integration/e2e_server.py` (needs `uv` on PATH or `MUESTRA_UV`): play, A/B, mute, a comment reaching `feedback.jsonl`, a CLI render showing up live |
| `npm run e2e:install` | download Playwright's Chromium once (`npx playwright install chromium`) |

## How it is served in production

`npm run build` writes static files to `ui/dist` (relative asset paths,
`base: "./"`). The API (`muestra serve`, WP7) serves `ui/dist/index.html`
at `/` and the assets beside it, **on the same origin** as the contract
routes, so the app talks to `""` (same origin) with no configuration. There
are no client-side routes. To point a build at another origin, open it with
`?api=http://host:port` (the API then needs CORS) or build with
`VITE_MUESTRA_API=...`.

The mock does the same: with a build present, `npm run mock` and open
<http://localhost:8777>.

## Keyboard

| Key | |
|---|---|
| `Espacio` | play / pause |
| `S` | switch A ⇄ B at the same position (gapless) |
| `A` / `B` | hear A / hear B |
| `X` / `Y` | blind mode: hear X / Y |
| `L` | loop on/off (drag on the waveform to choose bars; drag the edges or the region to adjust) |
| `←` / `→` | one beat back / forward (`⇧` = one bar) |
| `M` | comment at the playhead: type, `Enter` sends, `Esc` cancels; playback keeps going |
| `1`…`9` | jump to version 1…9 (keeps the position) |
| `↑` / `↓` | volume |
| `?` | shortcuts help |

Shortcuts are ignored while typing; sliders keep their own arrow keys.

## The mock (`ui/mock/`)

A Node server implementing the contract surface from `docs/contracts.md`
against a **copy** of `fixtures/example-project` (written to
`ui/mock/.project`, git-ignored; the fixture itself is never modified):

- `POST /tools/<mcp_name>` with the `ToolResult` envelope: `project_open`,
  `song_get`, `versions_list|get|diff`, `feedback_read|append`,
  `chops_list|choose`, and `render`/`source_separate` as jobs.
  `feedback.append` and `chops.choose` **validate against `schemas/`** (ajv)
  and reject what the real API would reject.
- `GET /files/...` with HTTP Range, audio only, restricted to `renders/` and `cache/previews/` (like muestra-api).
- `GET /jobs/<id>`, `WS /events` (`job.updated`, `version.created`,
  `feedback.appended`, `candidates.created`).
- `GET /project` and `GET /waveform?path=&points=` in muestra-api's real shapes
  (`MOCK_ACCEL=0` turns them off to exercise the tools-only fallback).

At startup it derives a meaningful history from the single fixture render:
v001 is extended to 8 bars (the fixture render is 1 bar) with a synthesized
bass line (the fixture's bass stem is silent), and v002–v004 are made with
real DSP on the stems (filter sweep, gain, saturation, high-pass), each mix
rebuilt as master(sum of stems). v004 branches from v002, so the history is
a tree. Loudness/spectral/masking metrics are **measured** from that audio
(RMS-based approximations), so loudness matching has real work to do. It
also writes ten feedback lines on that timeline (one still "pendiente").

With `MOCK_DEMO=1` (default) it starts a 60 s stem separation job after 3 s
and "the agent" renders a new version after 25 s, to show live updates.

| env | default | |
|---|---|---|
| `PORT` | 8777 | `0` = any free port (printed) |
| `MOCK_DEMO` | 1 | demo jobs |
| `MOCK_ACCEL` | 1 | `/project` and `/waveform` |
| `MOCK_BARS` | 8 | render length |
| `MOCK_PROJECT_DIR` | `ui/mock/.project` | where the copy lives |
| `MOCK_RENDER_MS` | 6000 | simulated render time |

Test-only routes: `GET /debug/received` (every feedback event received, with
its schema verdict), `POST /debug/render?ms=`, `POST /debug/separate`,
`POST /debug/reset`.

## Layout of the code

```
src/api/        types.ts (contract types), client.ts (the ONLY place that knows routes)
src/audio/      engine.ts (Web Audio), mixer.ts (mute/solo rules), wav.ts (decoder), signal.ts
src/music/      time.ts (bars/beats math, loops, A/B mapping)
src/feedback/   events.ts (schema-valid feedback builders)
src/state/      store.ts (state + actions), derive.ts (pure derivations), react.tsx (hooks)
src/ui/         components (Player, Waveform, Versions, Mixer, Feedback, Chops, Ears, Chrome, keys)
mock/           mock API + WAV/DSP helpers + schema validators
test/           Vitest; e2e/ Playwright
```

## Audio engine

React never drives audio: the store calls engine methods; the playhead and
time readout are painted from `requestAnimationFrame` straight to the DOM.

- **One deck per version.** All of its sources — `mix.wav`, every track stem
  and (on demand) the drum-lane stems — are started with the *same*
  `start(when, offset)`, so they are sample-synchronous for the whole run.
- **Mix vs stems.** Per contract the stems are pre-master and the mix is
  post-master. With the mixer untouched you hear `mix.wav`. Mute, solo, a
  fader move or a lane control crossfades (15 ms) to the summed stems, level
  matched to the mix (RMS ratio, clamped ±18 dB), and the player shows
  "escuchando stems sin master". Nothing is restarted or re-rendered: it is a
  gain change on the running graph. Lane mute/solo swaps that track's stem
  for its level-matched lane stems.
- **A/B.** The incoming version is started at context time `T = now + 12 ms`
  at the position the outgoing version will have at `T`, mapped by bar/beat
  (identity for equal timing; tempo or range differences map musically), and
  a 20 ms equal-power crossfade runs at `T`. Both positions come from the
  same clock arithmetic, so the switch is gapless and sample-accurate
  (verified by the engine tests and the e2e test: `toOffset === fromPos`).
- **Loudness match** (default on): during A/B the louder version is turned
  down to the quieter one's integrated LUFS (from `metrics.json`); never up,
  so it cannot clip. The UI says by how much.
- **Seeks and loop edits** restart the run behind a 6 ms fade (no clicks).
  Loops are kept in absolute song beats, so they mean the same bars in every version.
- **Decoding.** The AudioContext is created at the render's sample rate so
  nothing is resampled. `decodeAudioData` first; if a browser rejects a WAV
  (e.g. 32-bit float), `src/audio/wav.ts` decodes it (PCM 8/16/24/32, float
  32/64, `WAVE_FORMAT_EXTENSIBLE`).
- **Memory.** Decoded audio is held in an LRU over a byte budget (640 MB,
  220 MB on phones / low-memory devices); the A/B pair is pinned, lane stems
  load only when used. Waveform peaks are kept separately (tiny).
- **Autoplay policy.** The first click/tap/key anywhere unlocks the context;
  until then the transport says "Tocá play para activar el audio".

See `API-NEEDS.md` for exactly what the UI expects from the API.
