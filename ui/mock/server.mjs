// Mock muestra-api (WP8 dev/test double for the real FastAPI server, WP7).
// Implements the contract surface from docs/contracts.md "HTTP API model":
//
//   POST /tools/<mcp_name>   ToolResult envelope (GET allowed for readonly tools)
//   GET  /files/<path>       HTTP Range; restricted to renders/ + cache/previews/
//   GET  /jobs/<id>          Job
//   WS   /events             WsEvent {type, ts, data}
//
// plus the two OPTIONAL accelerators the UI can use when the API offers them
// (NOT contract; see ui/API-NEEDS.md), on by default, MOCK_ACCEL=0 disables:
//
//   GET  /project            muestra-api summary (song, versions+meta+metrics, analyses)
//   GET  /waveform?path=&points=N   muestra-api waveform min/max
//
// It serves a COPY of fixtures/example-project (ui/mock/.project), seeded
// with derived versions (see versions.mjs). /debug/* routes exist for tests
// and demos only.
//
//   node mock/server.mjs                 # port 8777
//   PORT=9000 node mock/server.mjs
//   MOCK_DEMO=0      no demo jobs (auto-render after 25 s, stem separation)
//   MOCK_ACCEL=0     tools only (no /project, no /waveform)
//   MOCK_BARS=8      render length used for the derived versions

import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { WebSocketServer } from "ws";
import YAML from "yaml";
import { seedVersions, renderNext } from "./versions.mjs";
import { decodeWav } from "./wav.mjs";
import { makeAjv, compile } from "./schema.mjs";


const __dirname = path.dirname(fileURLToPath(import.meta.url));
const UI_DIR = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(UI_DIR, "..");
const FIXTURE = path.join(REPO_ROOT, "fixtures", "example-project");
const PROJECT = path.resolve(process.env.MOCK_PROJECT_DIR ?? path.join(__dirname, ".project"));
const DIST = path.join(UI_DIR, "dist");

const PORT = Number(process.env.PORT ?? 8777);
const DEMO = process.env.MOCK_DEMO !== "0";
const ACCEL = process.env.MOCK_ACCEL !== "0";
const RENDER_MS = Number(process.env.MOCK_RENDER_MS ?? 6000);
const LATENCY_MS = Number(process.env.MOCK_LATENCY_MS ?? 0);

// ---------------------------------------------------------------- schema
const ajv = makeAjv();
const validateFeedback = compile(ajv, "feedback.schema.json");
// `project` is injected by the HTTP server (one project per process).
const validateChoose = compile(ajv, "tools/chops.choose.params.schema.json", ["project"]);

/** Every feedback event the mock received, with its schema verdict (tests). */
const received = [];

// ---------------------------------------------------------------- project
function freshProject() {
  fs.rmSync(PROJECT, { recursive: true, force: true });
  fs.cpSync(FIXTURE, PROJECT, { recursive: true });
  const { root, durationS } = seedVersions(PROJECT);
  const at = (min) => new Date(root + min * 60_000).toISOString();
  // The fixture's feedback lines carry fictional 2026-11-17 timestamps;
  // rewrite the file on the seeded timeline so "pendiente" reads right.
  const lines = [
    { ts: at(2), version: "v001", type: "rating", value: "like" },
    { ts: at(3), version: "v001", type: "comment", at_s: Math.min(12, durationS - 1), track: "drums", text: "acá el bombo se come al sample" },
    { ts: at(4), version: "v001", type: "chop_choice", candidate_id: "cs001/cand1" },
    { ts: at(11), version: "v002", type: "rating", value: "dislike" },
    { ts: at(12), version: "v002", type: "comment", at_s: 2.0, track: "bass", text: "el bajo tapa todo acá, bajalo un poco" },
    { ts: at(13), type: "ab_choice", a: "v001", b: "v002", winner: "v001" },
    { ts: at(20), version: "v003", type: "rating", value: "like" },
    { ts: at(21), version: "v003", type: "comment", at_s: 6.0, text: "este corte suena re bien, que se repita" },
    { ts: at(22), type: "comment", text: "en general va mejor, pero el final queda medio vacío" },
    // after v004 (26 min): still unread by the agent
    { ts: at(40), version: "v004", type: "comment", at_s: 9.5, track: "sample", text: "el sample quedó muy atrás en el compás 5" },
  ];
  fs.writeFileSync(path.join(PROJECT, "feedback.jsonl"), lines.map((e) => JSON.stringify(e)).join("\n") + "\n");
  received.length = 0;
}

// ---------------------------------------------------------------- ws bus
const clients = new Set();
function broadcast(type, data) {
  const msg = JSON.stringify({ type, ts: new Date().toISOString(), data });
  for (const ws of clients) if (ws.readyState === 1) ws.send(msg);
}

// ---------------------------------------------------------------- jobs
const jobs = new Map();
let jobSeq = 0;

function newJob(tool, message) {
  const job = { id: `job-${++jobSeq}`, tool, status: "queued", progress: 0, message, result: null };
  jobs.set(job.id, job);
  broadcast("job.updated", job);
  return job;
}

function updateJob(job, patch) {
  Object.assign(job, patch);
  broadcast("job.updated", { ...job });
}

function runJob(job, ms, finish) {
  const t0 = Date.now();
  setTimeout(() => updateJob(job, { status: "running" }), 200);
  const tick = setInterval(() => updateJob(job, { status: "running", progress: Math.min(0.98, (Date.now() - t0) / ms) }), 400);
  setTimeout(() => {
    clearInterval(tick);
    try {
      const data = finish();
      updateJob(job, {
        status: "done",
        progress: 1,
        message: "listo",
        result: { tool: job.tool, ok: true, data, files: data?.files ?? [], errors: [], hint: null },
      });
    } catch (e) {
      updateJob(job, {
        status: "failed",
        message: String(e?.message ?? e),
        result: { tool: job.tool, ok: false, data: null, files: [], errors: [{ path: "", code: "internal_error", message: String(e), hint: null }], hint: null },
      });
    }
  }, ms);
  return { job_id: job.id };
}

function startRender(ms = RENDER_MS) {
  const job = newJob("render", "renderizando el loop…");
  return runJob(job, ms, () => {
    const meta = renderNext(PROJECT);
    broadcast("version.created", { version: meta.version, meta, metrics: readMetrics(meta.version) });
    return { version: meta.version, files: [meta.mix] };
  });
}

function startSeparate(sourceId = "src1", ms = 45_000) {
  const job = newJob("source.separate", `separando stems de ${sourceId} (BS-RoFormer)…`);
  return runJob(job, ms, () => ({ source_id: sourceId, files: [] }));
}

// ---------------------------------------------------------------- feedback
function readFeedbackFile() {
  const file = path.join(PROJECT, "feedback.jsonl");
  const events = [];
  const skipped = [];
  if (!fs.existsSync(file)) return { events, skipped, lines: 0 };
  const lines = fs.readFileSync(file, "utf8").split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  lines.forEach((line, i) => {
    if (!line.trim()) return;
    try {
      const e = JSON.parse(line);
      if (!validateFeedback(e)) {
        skipped.push({ path: "feedback.jsonl", line: i + 1, message: ajv.errorsText(validateFeedback.errors), hint: "Corregí o borrá la línea." });
        return;
      }
      events.push({ line: i + 1, e });
    } catch {
      skipped.push({ path: "feedback.jsonl", line: i + 1, message: "JSON inválido", hint: "Corregí o borrá la línea." });
    }
  });
  return { events, skipped, lines: lines.length };
}

function appendFeedback(event) {
  const e = { ...event };
  if (!e.ts) e.ts = new Date().toISOString();
  const ok = validateFeedback(e);
  received.push({ event: e, valid: !!ok, errors: ok ? [] : validateFeedback.errors });
  if (!ok) throw toolError(`evento inválido: ${ajv.errorsText(validateFeedback.errors)}`, "invalid_value", "event");
  fs.appendFileSync(path.join(PROJECT, "feedback.jsonl"), JSON.stringify(e) + "\n");
  broadcast("feedback.appended", e); // data = the event line (schemas/api.py)
  return { files: ["feedback.jsonl"], appended: 1, path: "feedback.jsonl" };
}

// ---------------------------------------------------------------- tools
const READONLY = new Set(["project_open", "song_get", "song_validate", "feedback_read", "versions_list", "versions_get", "versions_diff", "chops_list", "library_search"]);

function chopSetPaths() {
  const root = path.join(PROJECT, "cache", "chops");
  const out = [];
  if (!fs.existsSync(root)) return out;
  for (const src of fs.readdirSync(root)) {
    for (const f of fs.readdirSync(path.join(root, src))) if (/^cs\d{3,}\.json$/.test(f)) out.push(path.join(root, src, f));
  }
  return out.sort();
}

function versionDirs() {
  const dir = path.join(PROJECT, "renders");
  return fs.existsSync(dir) ? fs.readdirSync(dir).filter((d) => /^v\d{3,}$/.test(d)).sort() : [];
}

function readMeta(v) {
  try {
    return JSON.parse(fs.readFileSync(path.join(PROJECT, "renders", v, "meta.json"), "utf8"));
  } catch {
    return null;
  }
}

function readMetrics(v) {
  try {
    return JSON.parse(fs.readFileSync(path.join(PROJECT, "renders", v, "metrics.json"), "utf8"));
  } catch {
    return null;
  }
}

function listVersions() {
  const liked = {};
  for (const { e } of readFeedbackFile().events) if (e.type === "rating") liked[e.version] = e.value === "like";
  return {
    files: [],
    versions: versionDirs().map((v) => {
      const m = readMeta(v) ?? {};
      return { version: v, parent: m.parent ?? null, created_at: m.created_at ?? null, git_commit: m.git_commit ?? null, summary: m.summary ?? null, liked: liked[v] ?? null };
    }),
  };
}

function songTitle() {
  try {
    return YAML.parse(fs.readFileSync(path.join(PROJECT, "song.yaml"), "utf8"))?.meta?.title ?? null;
  } catch {
    return null;
  }
}

function runTool(name, params) {
  switch (name) {
    case "project_open":
      return {
        files: [],
        project_path: PROJECT,
        config: { schema_version: 0, sample_rate: 22050, export_bit_depth: 24, library: { roots: ["../library"] }, plugins: { search_dirs: [], aliases: {} }, separation: { backend: "audio-separator", model: null } },
        has_song: fs.existsSync(path.join(PROJECT, "song.yaml")),
        sources: ["src1"],
        versions: versionDirs(),
      };
    case "song_get":
      return { files: ["song.yaml"], path: "song.yaml", song: YAML.parse(fs.readFileSync(path.join(PROJECT, "song.yaml"), "utf8")) };
    case "versions_list":
      return listVersions();
    case "versions_get": {
      const meta = readMeta(params.version);
      if (!meta) throw toolError(`no existe la versión ${params.version}`, "file_not_found", "version");
      const songPath = path.join(PROJECT, "renders", params.version, "song.yaml");
      return { files: [], version: params.version, meta, metrics: readMetrics(params.version), song_yaml: fs.existsSync(songPath) ? fs.readFileSync(songPath, "utf8") : "" };
    }
    case "versions_diff": {
      const b = readMeta(params.b);
      if (!b || !readMeta(params.a)) throw toolError("versión inexistente", "file_not_found", "a");
      const same = b.parent === params.a;
      return { files: [], a: params.a, b: params.b, diff: same ? b.diff : [], musical_summary: same ? b.summary : null, changes: same && b.summary ? [b.summary] : [], metrics_delta: [], identical: !same };
    }
    case "feedback_read": {
      const { events, skipped, lines } = readFeedbackFile();
      const cursor = params.cursor ?? 0;
      let slice = events.filter((x) => x.line > cursor).map((x) => x.e);
      if (params.since) slice = slice.filter((e) => Date.parse(e.ts) >= Date.parse(params.since));
      return { files: ["feedback.jsonl"], events: slice, next_cursor: lines, latest_ts: slice.length ? slice[slice.length - 1].ts : null, count: slice.length, skipped };
    }
    case "feedback_append": {
      if (!params.event || typeof params.event !== "object") throw toolError("falta 'event'", "missing_field", "event");
      return appendFeedback(params.event);
    }
    case "chops_list": {
      let sets = chopSetPaths().map((p) => JSON.parse(fs.readFileSync(p, "utf8")));
      if (params.source_id) sets = sets.filter((s) => s.source_id === params.source_id);
      return { files: [], sets };
    }
    case "chops_choose": {
      if (!validateChoose(params)) throw toolError(ajv.errorsText(validateChoose.errors), "invalid_value", "");
      if (chooseMode === "fail" || chooseMode === "fail-recorded") {
        if (chooseMode === "fail-recorded") appendFeedback({ ts: new Date().toISOString(), type: "chop_choice", candidate_id: params.candidate_id });
        throw toolError("Clip ends at beat 128 but the song ends at beat 64 (bar 32).", "clip_overrun", "tracks[0].clips[0].repeat");
      }
      const [setId, local] = params.candidate_id.split("/");
      const setPath = chopSetPaths().find((p) => path.basename(p) === `${setId}.json`);
      if (!setPath) throw toolError(`set ${setId} no encontrado`, "ref_not_found", "candidate_id");
      const set = JSON.parse(fs.readFileSync(setPath, "utf8"));
      const cand = set.candidates.find((c) => c.id === local || c.id === params.candidate_id);
      if (!cand) throw toolError(`candidato ${params.candidate_id} no encontrado`, "ref_not_found", "candidate_id");
      const mode = params.mode ?? "manual";
      const adjust = params.adjust ?? { start_beats: 0, length_beats: 0 };
      set.chosen = { candidate_id: params.candidate_id, mode, adjust: { start_beats: adjust.start_beats ?? 0, length_beats: adjust.length_beats ?? 0 }, at: new Date().toISOString() };
      fs.writeFileSync(setPath, JSON.stringify(set, null, 2) + "\n");
      if (mode === "manual") {
        const ev = { ts: new Date().toISOString(), type: "chop_choice", candidate_id: params.candidate_id };
        if (params.adjust) ev.adjust = set.chosen.adjust;
        appendFeedback(ev);
      }
      const chop = {
        id: params.chop_id,
        source: cand.source_id,
        stem: cand.stem,
        start_downbeat: cand.start_downbeat,
        start_offset_beats: adjust.start_beats ?? 0,
        length_beats: cand.length_beats + (adjust.length_beats ?? 0),
        warp: { mode: "resample", pitch_semitones: 0 },
        fade_ms: 5,
      };
      const out = { files: [path.relative(PROJECT, setPath).split(path.sep).join("/"), "song.yaml", "feedback.jsonl"], set_id: setId, candidate_id: params.candidate_id, chop };
      if (chooseMode === "newid") Object.assign(out, { chop_id: `${params.chop_id}_2`, requested_chop_id: params.chop_id, chop: { ...chop, id: `${params.chop_id}_2` }, recorded: true });
      if (chooseMode === "rescale") Object.assign(out, { rescaled: { clip: "intro", repeat_from: 8, repeat_to: 5 }, notes: ["Clip 'intro': repeat 8 -> 5 to keep its span."], recorded: true });
      return out;
    }
    case "render":
      return startRender();
    case "source_separate":
      return startSeparate(params.source_id);
    default:
      throw toolError(`herramienta desconocida: ${name}`, "unknown_tool", "");
  }
}

let chooseMode = "ok";

function toolError(message, code, p) {
  const err = new Error(message);
  err.issues = [{ path: p ?? "", code, message, hint: null, value: null }];
  return err;
}

// ---------------------------------------------------------------- accelerators
function projectView() {
  // muestra-api GET /project (src/muestra/api/summary.py): versions newest
  // first, only those with a readable meta.json.
  const versions = [];
  for (const v of listVersions().versions) {
    const meta = readMeta(v.version);
    if (meta) versions.push({ version: v.version, meta, metrics: readMetrics(v.version), feedback: null });
  }
  versions.sort((x, y) => Number(y.version.slice(1)) - Number(x.version.slice(1)));
  const analyses = {};
  const adir = path.join(PROJECT, "cache", "analysis");
  if (fs.existsSync(adir))
    for (const f of fs.readdirSync(adir))
      if (f.endsWith(".json") && !f.endsWith(".meta.json")) analyses[f.slice(0, -5)] = JSON.parse(fs.readFileSync(path.join(adir, f), "utf8"));
  let song = null;
  try {
    song = YAML.parse(fs.readFileSync(path.join(PROJECT, "song.yaml"), "utf8"));
  } catch {
    /* no song */
  }
  return { project_path: PROJECT, config: {}, song, versions, candidate_sets: [], analyses, feedback: {}, feedback_cursor: 0, feedback_skipped: [], issues: [] };
}

const peaksCache = new Map();
function waveformFor(rel, points) {
  // muestra-api GET /waveform (src/muestra/api/files.py)
  const key = `${rel}|${points}`;
  if (peaksCache.has(key)) return peaksCache.get(key);
  const w = decodeWav(fs.readFileSync(path.join(PROJECT, rel)));
  const count = Math.min(points, w.frames);
  const channels = w.data.map((ch) => {
    const min = [];
    const max = [];
    for (let b = 0; b < count; b++) {
      const s0 = Math.floor((b * w.frames) / count);
      const e0 = Math.max(s0 + 1, Math.floor(((b + 1) * w.frames) / count));
      let mn = Infinity;
      let mx = -Infinity;
      for (let i = s0; i < e0 && i < ch.length; i++) {
        if (ch[i] < mn) mn = ch[i];
        if (ch[i] > mx) mx = ch[i];
      }
      min.push(mn);
      max.push(mx);
    }
    return { min, max };
  });
  const res = { path: rel, sample_rate: w.sampleRate, frames: w.frames, duration_s: w.frames / w.sampleRate, points: count, channels };
  peaksCache.set(key, res);
  return res;
}

// ---------------------------------------------------------------- http
const MIME = {
  ".wav": "audio/wav", ".json": "application/json", ".yaml": "text/yaml", ".html": "text/html; charset=utf-8",
  ".js": "text/javascript", ".css": "text/css", ".map": "application/json", ".svg": "image/svg+xml",
  ".woff2": "font/woff2", ".woff": "font/woff", ".png": "image/png", ".ico": "image/x-icon", ".webmanifest": "application/manifest+json",
};

function send(res, status, body, headers = {}) {
  const isObj = body != null && typeof body === "object" && !Buffer.isBuffer(body);
  res.writeHead(status, { "content-type": isObj ? "application/json" : "text/plain; charset=utf-8", ...headers });
  res.end(isObj ? JSON.stringify(body) : body ?? "");
}

function serveFile(req, res, abs) {
  const stat = fs.statSync(abs);
  const mime = MIME[path.extname(abs)] ?? "application/octet-stream";
  const base = { "content-type": mime, "accept-ranges": "bytes", "cache-control": "no-cache", "last-modified": stat.mtime.toUTCString() };
  const range = req.headers.range;
  if (range) {
    const m = /^bytes=(\d*)-(\d*)$/.exec(range);
    if (m && (m[1] || m[2])) {
      let start = m[1] ? Number(m[1]) : stat.size - Number(m[2]);
      let end = m[1] && m[2] ? Math.min(Number(m[2]), stat.size - 1) : stat.size - 1;
      start = Math.max(0, start);
      if (start >= stat.size || start > end) {
        send(res, 416, "", { "content-range": `bytes */${stat.size}` });
        return;
      }
      res.writeHead(206, { ...base, "content-length": end - start + 1, "content-range": `bytes ${start}-${end}/${stat.size}` });
      if (req.method === "HEAD") return res.end();
      fs.createReadStream(abs, { start, end }).pipe(res);
      return;
    }
  }
  res.writeHead(200, { ...base, "content-length": stat.size });
  if (req.method === "HEAD") return res.end();
  fs.createReadStream(abs).pipe(res);
}

function serveStatic(req, res, pathname) {
  // production-like: the API serves ui/dist at the same origin
  const rel = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
  let abs = path.resolve(DIST, rel);
  if (!abs.startsWith(DIST) || !fs.existsSync(abs) || fs.statSync(abs).isDirectory()) abs = path.join(DIST, "index.html");
  if (!fs.existsSync(abs)) {
    send(res, 200, `muestra mock\nproyecto: ${PROJECT}\nSin ui/dist: corré "npm run build", o "npm run dev" (Vite, puerto 5173).\n`);
    return;
  }
  serveFile(req, res, abs);
}

function readBody(req) {
  return new Promise((resolve) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => resolve(body));
  });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", "http://x");
  let pathname;
  try {
    pathname = decodeURIComponent(url.pathname);
  } catch {
    send(res, 400, { detail: "bad path" });
    return;
  }
  if (LATENCY_MS) await new Promise((r) => setTimeout(r, LATENCY_MS));

  // ---- debug helpers (NOT part of the contract) ----
  if (pathname === "/debug/received") return send(res, 200, received);
  if (pathname === "/debug/render" && req.method === "POST") return send(res, 200, startRender(Number(url.searchParams.get("ms") ?? RENDER_MS)));
  if (pathname === "/debug/separate" && req.method === "POST") return send(res, 200, startSeparate("src1", Number(url.searchParams.get("ms") ?? 45_000)));
  if (pathname === "/debug/choose" && req.method === "POST") {
    // what the next chops.choose does: ok | fail (the real English clip_overrun) | newid | rescale | fail-recorded
    chooseMode = url.searchParams.get("mode") ?? "ok";
    return send(res, 200, { ok: true, mode: chooseMode });
  }
  if (pathname === "/debug/reset" && req.method === "POST") {
    chooseMode = "ok";
    freshProject();
    peaksCache.clear();
    return send(res, 200, { ok: true });
  }

  // ---- contract: tools ----
  if (pathname.startsWith("/tools/")) {
    const name = pathname.slice("/tools/".length);
    if (req.method !== "POST" && !(req.method === "GET" && READONLY.has(name))) {
      return send(res, 405, { detail: `${name}: method not allowed` });
    }
    let params = {};
    if (req.method === "POST") {
      const body = await readBody(req);
      try {
        params = body ? JSON.parse(body) : {};
      } catch {
        return send(res, 422, { detail: "JSON inválido" });
      }
    } else {
      for (const [k, v] of url.searchParams) {
        try {
          params[k] = JSON.parse(v);
        } catch {
          params[k] = v;
        }
      }
    }
    const tool = name.replace(/_/g, ".");
    try {
      const data = runTool(name, params);
      if (data && "job_id" in data) return send(res, 200, data); // long_running → {job_id}
      return send(res, 200, { tool, ok: true, data, files: data?.files ?? [], errors: [], hint: null });
    } catch (e) {
      return send(res, 200, {
        tool,
        ok: false,
        data: null,
        files: [],
        errors: e.issues ?? [{ path: "", code: "internal_error", message: String(e), hint: null, value: null }],
        hint: String(e.message ?? e),
      });
    }
  }

  // ---- contract: files ----
  if (pathname.startsWith("/files/")) {
    const rel = pathname.slice("/files/".length);
    // like muestra-api: audio only, under renders/ and cache/previews/
    if (!/^(renders|cache\/previews)\/.+\.(wav|flac|mp3|m4a|ogg|aif|aiff)$/i.test(rel) || rel.split("/").includes("..")) {
      return send(res, 403, { detail: "Use an audio path inside renders/ or cache/previews/." });
    }
    const abs = path.join(PROJECT, rel);
    if (!fs.existsSync(abs) || !fs.statSync(abs).isFile()) return send(res, 404, { detail: "no encontrado" });
    return serveFile(req, res, abs);
  }

  // ---- contract: jobs ----
  if (pathname.startsWith("/jobs/")) {
    const job = jobs.get(pathname.slice("/jobs/".length));
    return job ? send(res, 200, job) : send(res, 404, { detail: "job no encontrado" });
  }

  // ---- optional accelerators ----
  if (ACCEL && (pathname === "/project" || pathname === "/project/summary")) return send(res, 200, projectView());
  if (ACCEL && (pathname === "/waveform" || pathname === "/waveform/peaks")) {
    const rel = url.searchParams.get("path") ?? "";
    if (!/^(renders|cache\/previews)\/.+\.wav$/.test(rel) || rel.split("/").includes("..")) return send(res, 403, { detail: "path inválido" });
    if (!fs.existsSync(path.join(PROJECT, rel))) return send(res, 404, { detail: "no encontrado" });
    const points = Math.min(8192, Math.max(1, Number(url.searchParams.get("points") ?? 1024)));
    return send(res, 200, waveformFor(rel, points));
  }

  serveStatic(req, res, pathname);
});

// ---------------------------------------------------------------- ws
const wss = new WebSocketServer({ server, path: "/events" });
wss.on("connection", (ws) => {
  clients.add(ws);
  ws.on("close", () => clients.delete(ws));
  for (const job of jobs.values()) {
    if (job.status === "queued" || job.status === "running") ws.send(JSON.stringify({ type: "job.updated", ts: new Date().toISOString(), data: job }));
  }
});

// ---------------------------------------------------------------- boot
freshProject();
server.listen(PORT, () => {
  const port = server.address().port;
  console.log(`[muestra-mock] proyecto: ${PROJECT}`);
  console.log(`[muestra-mock] http://localhost:${port}  (ws /events, accel ${ACCEL ? "on" : "off"}, demo ${DEMO ? "on" : "off"})`);
  if (DEMO) {
    // Live-update demo: a long stem separation and, later, the "agent"
    // rendering a new version — without touching a terminal.
    setTimeout(() => startSeparate("src1", 60_000), 3000);
    setTimeout(() => startRender(), Number(process.env.MOCK_DEMO_RENDER_AFTER ?? 25_000));
  }
});
