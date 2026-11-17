// Mock muestra-api server (WP8 dev/test double for the real FastAPI in
// src/muestra/api/). Implements the contract surface from docs/contracts.md:
//   POST /tools/<mcp_name>   (GET allowed for readonly tools)
//   GET  /files/<path>       with Range, restricted to renders/ + cache/previews/
//   GET  /jobs/<id>
//   WS   /events             job.updated | version.created | candidates.created
//                            | feedback.appended | song.changed
// It serves a COPY of fixtures/example-project (mock/.project) seeded with
// extra derived versions (v002, v003) at startup. Extra debug routes
// (/debug/*) exist for tests only and are prefixed so nobody confuses them
// with the contract surface.
//
//   node mock/server.mjs            # port 8777
//   PORT=9000 node mock/server.mjs
//   MOCK_AUTORENDER=0 …             # disable the demo auto-render job

import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { WebSocketServer } from "ws";
import YAML from "yaml";
import { seedVersions, renderNext } from "./versions.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const UI_DIR = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(UI_DIR, "..");
const FIXTURE = path.join(REPO_ROOT, "fixtures", "example-project");
const PROJECT = path.join(__dirname, ".project");
const DIST = path.join(UI_DIR, "dist");

const PORT = Number(process.env.PORT ?? process.env.MUESTRA_MOCK_PORT ?? 8777);
const AUTORENDER = process.env.MOCK_AUTORENDER !== "0";
const JOB_RENDER_MS = Number(process.env.MOCK_RENDER_MS ?? 7000);

// ---------------------------------------------------------------- project
function freshProject() {
  fs.rmSync(PROJECT, { recursive: true, force: true });
  fs.cpSync(FIXTURE, PROJECT, { recursive: true });
  seedVersions(PROJECT);
  // Extra feedback entries beyond the fixture's 4 lines: several entries so
  // the feedback panel has content, incl. one pinned comment on v002.
  const extra = [
    { ts: "2026-11-17T20:20:00Z", version: "v002", type: "rating", value: "dislike" },
    {
      ts: "2026-11-17T20:21:05Z", version: "v002", type: "comment", at_s: 1.0,
      track: "bass", text: "el bajo tapa todo acá, bajalo 2 dB",
    },
    { ts: "2026-11-17T20:24:30Z", version: "v003", type: "rating", value: "like" },
    {
      ts: "2026-11-17T20:25:00Z", type: "comment",
      text: "la v003 va mejor, pero el final queda medio vacío",
    },
  ];
  fs.appendFileSync(
    path.join(PROJECT, "feedback.jsonl"),
    extra.map((e) => JSON.stringify(e)).join("\n") + "\n",
  );
}

// ---------------------------------------------------------------- ws bus
const clients = new Set();
function broadcast(type, data) {
  const msg = JSON.stringify({ type, ts: new Date().toISOString(), data });
  for (const ws of clients) if (ws.readyState === 1) ws.send(msg);
}

// ---------------------------------------------------------------- jobs
const jobs = new Map(); // id -> Job
let jobSeq = 0;

function updateJob(job, patch) {
  Object.assign(job, patch);
  broadcast("job.updated", job);
}

function startRenderJob() {
  const id = `job-${++jobSeq}`;
  const job = { id, tool: "render", status: "queued", progress: 0, message: null, result: null };
  jobs.set(id, job);
  broadcast("job.updated", job);
  const t0 = Date.now();
  updateJob(job, { status: "running", message: "renderizando…" });
  const tick = setInterval(() => {
    const p = Math.min(0.98, (Date.now() - t0) / JOB_RENDER_MS);
    updateJob(job, { progress: p });
  }, 350);
  setTimeout(() => {
    clearInterval(tick);
    try {
      const meta = renderNext(PROJECT);
      updateJob(job, {
        status: "done", progress: 1, message: "listo",
        result: { tool: "render", ok: true, data: { version: meta.version }, files: [], errors: [], hint: null },
      });
      broadcast("version.created", { version: meta.version, meta });
    } catch (e) {
      updateJob(job, { status: "failed", message: String(e), result: null });
    }
  }, JOB_RENDER_MS);
  return { job_id: id };
}

function startSeparateJob(sourceId) {
  const id = `job-${++jobSeq}`;
  const job = { id, tool: "source.separate", status: "queued", progress: 0, message: null, result: null };
  jobs.set(id, job);
  broadcast("job.updated", job);
  const t0 = Date.now();
  const dur = 12000;
  updateJob(job, { status: "running", message: `separando stems de ${sourceId}…` });
  const tick = setInterval(() => {
    const p = Math.min(0.99, (Date.now() - t0) / dur);
    updateJob(job, { progress: p });
  }, 500);
  setTimeout(() => {
    clearInterval(tick);
    updateJob(job, { status: "done", progress: 1, message: "stems listos", result: { tool: "source.separate", ok: true, data: {}, files: [], errors: [], hint: null } });
  }, dur);
  return { job_id: id };
}

// ---------------------------------------------------------------- feedback
const FEEDBACK_TYPES = new Set(["rating", "comment", "ab_choice", "chop_choice"]);

function readFeedbackFile() {
  const file = path.join(PROJECT, "feedback.jsonl");
  const events = [];
  const skipped = [];
  if (!fs.existsSync(file)) return { events, skipped, lines: 0 };
  const lines = fs.readFileSync(file, "utf8").split("\n");
  let n = 0;
  for (const line of lines) {
    n++;
    if (!line.trim()) continue;
    try {
      const e = JSON.parse(line);
      if (!FEEDBACK_TYPES.has(e.type)) {
        skipped.push({ path: "feedback.jsonl", line: n, message: `unknown type ${e.type}`, hint: "" });
        continue;
      }
      events.push(e);
    } catch {
      skipped.push({ path: "feedback.jsonl", line: n, message: "invalid JSON", hint: "" });
    }
  }
  return { events, skipped, lines: lines.length };
}

function appendFeedback(event) {
  if (!event.ts) event.ts = new Date().toISOString();
  fs.appendFileSync(path.join(PROJECT, "feedback.jsonl"), JSON.stringify(event) + "\n");
  broadcast("feedback.appended", { event });
  return { files: ["feedback.jsonl"], appended: 1, path: "feedback.jsonl" };
}

// ---------------------------------------------------------------- tools
const READONLY = new Set([
  "project_open", "song_get", "song_validate", "feedback_read",
  "versions_list", "versions_get", "versions_diff", "chops_list",
  "library_search", "source_analyze", "preview",
]);

function globChopSets() {
  const root = path.join(PROJECT, "cache", "chops");
  const sets = [];
  if (!fs.existsSync(root)) return sets;
  for (const src of fs.readdirSync(root)) {
    for (const f of fs.readdirSync(path.join(root, src))) {
      if (f.endsWith(".json")) {
        sets.push(JSON.parse(fs.readFileSync(path.join(root, src, f), "utf8")));
      }
    }
  }
  return sets;
}

function latestRatings() {
  const { events } = readFeedbackFile();
  const ratings = {};
  for (const e of events) if (e.type === "rating") ratings[e.version] = e.value === "like";
  return ratings;
}

function listVersions() {
  const dir = path.join(PROJECT, "renders");
  const ratings = latestRatings();
  const versions = fs.existsSync(dir)
    ? fs.readdirSync(dir).filter((d) => /^v\d+$/.test(d)).sort()
    : [];
  return {
    files: [],
    versions: versions.map((v) => {
      let meta = {};
      try { meta = JSON.parse(fs.readFileSync(path.join(dir, v, "meta.json"), "utf8")); } catch { /* partial */ }
      return {
        version: v,
        parent: meta.parent ?? null,
        created_at: meta.created_at ?? null,
        git_commit: meta.git_commit ?? null,
        summary: meta.summary ?? null,
        liked: ratings[v] ?? null,
      };
    }),
  };
}

function runTool(name, params) {
  const tool = name.replace(/_/g, ".");
  switch (name) {
    case "project_open": {
      const toml = fs.readFileSync(path.join(PROJECT, "muestra.toml"), "utf8");
      const cfg = YAML.parse(toml); // muestra.toml is TOML; YAML parses this file's simple shape
      return {
        files: ["muestra.toml"],
        project_path: PROJECT,
        config: cfg,
        has_song: fs.existsSync(path.join(PROJECT, "song.yaml")),
        sources: ["src1"],
        versions: listVersions().versions.map((v) => v.version),
      };
    }
    case "song_get": {
      const text = fs.readFileSync(path.join(PROJECT, "song.yaml"), "utf8");
      return { files: ["song.yaml"], path: "song.yaml", song: YAML.parse(text) };
    }
    case "versions_list":
      return listVersions();
    case "versions_get": {
      const v = params.version;
      const dir = path.join(PROJECT, "renders", v);
      if (!fs.existsSync(dir)) throw toolError(`no existe la versión ${v}`, "file_not_found");
      const meta = JSON.parse(fs.readFileSync(path.join(dir, "meta.json"), "utf8"));
      const songYaml = fs.existsSync(path.join(dir, "song.yaml"))
        ? fs.readFileSync(path.join(dir, "song.yaml"), "utf8")
        : "";
      return { files: [`renders/${v}/meta.json`], version: v, meta, song_yaml: songYaml };
    }
    case "feedback_read": {
      const { events, skipped } = readFeedbackFile();
      const cursor = params.cursor ?? 0;
      let slice = events.slice(cursor);
      if (params.since) slice = slice.filter((e) => e.ts >= params.since);
      return {
        files: ["feedback.jsonl"],
        events: slice,
        next_cursor: cursor + slice.length,
        latest_ts: slice.length ? slice[slice.length - 1].ts : null,
        count: slice.length,
        skipped,
      };
    }
    case "feedback_append": {
      const event = params.event;
      if (!event || !FEEDBACK_TYPES.has(event.type)) {
        throw toolError(`evento inválido: type ${event?.type}`, "invalid_value");
      }
      return appendFeedback(event);
    }
    case "chops_list": {
      let sets = globChopSets();
      if (params.source_id) sets = sets.filter((s) => s.source_id === params.source_id);
      return { files: [], sets };
    }
    case "chops_choose": {
      const cid = params.candidate_id; // "cs001/candN"
      const [setId] = cid.split("/");
      const setPath = path.join(PROJECT, "cache", "chops", "src1", `${setId}.json`);
      if (!fs.existsSync(setPath)) throw toolError(`set ${setId} no encontrado`, "file_not_found");
      const set = JSON.parse(fs.readFileSync(setPath, "utf8"));
      const cand = set.candidates.find((c) => `cs001/${c.id}` === cid || c.id === cid || `${setId}/${c.id}` === cid);
      if (!cand) throw toolError(`candidato ${cid} no encontrado`, "ref_not_found");
      set.chosen = {
        candidate_id: `${setId}/${cand.id}`,
        mode: params.mode ?? "manual",
        adjust: params.adjust ?? { start_beats: 0, length_beats: 0 },
        at: new Date().toISOString(),
      };
      fs.writeFileSync(setPath, JSON.stringify(set, null, 2));
      const chop = {
        id: params.chop_id,
        source: cand.source_id,
        stem: cand.stem,
        start_downbeat: cand.start_downbeat,
        length_beats: cand.length_beats + (params.adjust?.length_beats ?? 0),
        warp: { mode: "resample", pitch_semitones: 0 },
        fade_ms: 5,
      };
      if ((params.mode ?? "manual") === "manual") {
        appendFeedback({
          ts: new Date().toISOString(), type: "chop_choice",
          candidate_id: `${setId}/${cand.id}`,
          adjust: params.adjust ?? undefined,
        });
      }
      return { files: [`cache/chops/src1/${setId}.json`, "song.yaml", "feedback.jsonl"], set_id: setId, candidate_id: `${setId}/${cand.id}`, chop };
    }
    case "source_analyze": {
      const p = path.join(PROJECT, "cache", "analysis", `${params.source_id}.json`);
      if (!fs.existsSync(p)) throw toolError(`sin análisis para ${params.source_id}`, "file_not_found");
      return {
        files: [`cache/analysis/${params.source_id}.json`],
        source_id: params.source_id,
        analysis_path: `cache/analysis/${params.source_id}.json`,
        analysis: JSON.parse(fs.readFileSync(p, "utf8")),
      };
    }
    case "render":
      return startRenderJob();
    case "source_separate":
      return startSeparateJob(params.source_id ?? "src1");
    case "preview":
      return startRenderJob(); // reuse job plumbing for demo purposes
    default:
      throw toolError(`herramienta desconocida: ${tool}`, "unknown_tool");
  }
}

function toolError(message, code) {
  const err = new Error(message);
  err.issues = [{ path: "", code, message, hint: null }];
  return err;
}

// ---------------------------------------------------------------- http
const MIME = {
  ".wav": "audio/wav", ".json": "application/json", ".yaml": "text/yaml",
  ".yml": "text/yaml", ".html": "text/html", ".js": "text/javascript",
  ".css": "text/css", ".map": "application/json", ".svg": "image/svg+xml",
  ".woff2": "font/woff2", ".mid": "audio/midi", ".png": "image/png",
};

function send(res, status, body, headers = {}) {
  const isObj = body != null && typeof body === "object" && !Buffer.isBuffer(body);
  const payload = isObj ? JSON.stringify(body) : body;
  res.writeHead(status, {
    "content-type": isObj ? "application/json" : (headers["content-type"] ?? "application/octet-stream"),
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "content-type, range",
    "access-control-allow-methods": "GET, POST, OPTIONS",
    ...headers,
  });
  res.end(payload);
}

function serveFile(res, absPath, req) {
  const stat = fs.statSync(absPath);
  const mime = MIME[path.extname(absPath)] ?? "application/octet-stream";
  const range = req.headers.range;
  if (range) {
    const m = /^bytes=(\d+)-(\d*)$/.exec(range);
    if (m) {
      const start = Number(m[1]);
      const end = m[2] ? Math.min(Number(m[2]), stat.size - 1) : stat.size - 1;
      if (start >= stat.size || start > end) {
        send(res, 416, null, { "content-range": `bytes */${stat.size}` });
        return;
      }
      res.writeHead(206, {
        "content-type": mime,
        "content-length": end - start + 1,
        "content-range": `bytes ${start}-${end}/${stat.size}`,
        "accept-ranges": "bytes",
        "access-control-allow-origin": "*",
      });
      fs.createReadStream(absPath, { start, end }).pipe(res);
      return;
    }
  }
  res.writeHead(200, {
    "content-type": mime,
    "content-length": stat.size,
    "accept-ranges": "bytes",
    "access-control-allow-origin": "*",
  });
  fs.createReadStream(absPath).pipe(res);
}

function serveStatic(req, res, pathname) {
  // production mode: the API serves ui/dist at the same origin
  let rel = pathname === "/" ? "/index.html" : pathname;
  let abs = path.join(DIST, rel);
  if (!abs.startsWith(DIST) || !fs.existsSync(abs) || fs.statSync(abs).isDirectory()) {
    abs = path.join(DIST, "index.html"); // SPA fallback
  }
  if (!fs.existsSync(abs)) {
    send(res, 200, `<html><body style="font-family:monospace;background:#0d0f1e;color:#ddd">
      <h2>muestra mock</h2><p>proyecto: ${PROJECT}</p>
      <p>Sin <code>ui/dist</code> — corré <code>npm run build</code> o usá <code>npm run dev</code> (Vite).</p>
      <ul><li>POST /tools/&lt;name&gt;</li><li>/files/renders/v001/mix.wav</li>
      <li>/jobs/&lt;id&gt;</li><li>ws /events</li></ul></body></html>`,
      { "content-type": "text/html" });
    return;
  }
  serveFile(res, abs, req);
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://x");
  const pathname = decodeURIComponent(url.pathname);

  if (req.method === "OPTIONS") { send(res, 204, null); return; }

  // ---- debug helpers (NOT part of the contract) ----
  if (pathname === "/debug/feedback") {
    send(res, 200, readFeedbackFile()); return;
  }
  if (pathname === "/debug/render" && req.method === "POST") {
    send(res, 200, startRenderJob()); return;
  }
  if (pathname === "/debug/reset" && req.method === "POST") {
    freshProject(); send(res, 200, { ok: true }); return;
  }

  // ---- contract routes ----
  if (pathname.startsWith("/tools/")) {
    const name = pathname.slice("/tools/".length);
    if (req.method === "GET" && !READONLY.has(name)) {
      send(res, 405, { detail: `${name} is not readonly` }); return;
    }
    const done = (params) => {
      try {
        const data = runTool(name, params);
        send(res, 200, { tool: name.replace(/_/g, "."), ok: true, data, files: data?.files ?? [], errors: [], hint: null });
      } catch (e) {
        send(res, 200, {
          tool: name.replace(/_/g, "."), ok: false, data: null, files: [],
          errors: e.issues ?? [{ path: "", code: "internal_error", message: String(e), hint: null }],
          hint: String(e.message ?? e),
        });
      }
    };
    if (req.method === "POST") {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        let params = {};
        try { params = body ? JSON.parse(body) : {}; } catch { /* empty */ }
        done(params);
      });
    } else {
      const params = {};
      for (const [k, v] of url.searchParams) { try { params[k] = JSON.parse(v); } catch { params[k] = v; } }
      done(params);
    }
    return;
  }

  if (pathname.startsWith("/files/")) {
    const rel = pathname.slice("/files/".length);
    // Contract: restricted to renders/ and cache/previews/
    if (!/^renders\/|^cache\/previews\//.test(rel) || rel.includes("..")) {
      send(res, 403, { detail: "path fuera de renders/ o cache/previews/" }); return;
    }
    const abs = path.join(PROJECT, rel);
    if (!fs.existsSync(abs) || !fs.statSync(abs).isFile()) {
      send(res, 404, { detail: "no encontrado" }); return;
    }
    serveFile(res, abs, req);
    return;
  }

  if (pathname.startsWith("/jobs/")) {
    const job = jobs.get(pathname.slice("/jobs/".length));
    if (!job) { send(res, 404, { detail: "job no encontrado" }); return; }
    send(res, 200, job); return;
  }

  serveStatic(req, res, pathname);
});

// ---------------------------------------------------------------- ws
const wss = new WebSocketServer({ server, path: "/events" });
wss.on("connection", (ws) => {
  clients.add(ws);
  ws.on("close", () => clients.delete(ws));
  // Replay in-flight jobs so a fresh client sees progress.
  for (const job of jobs.values()) {
    if (job.status === "queued" || job.status === "running") {
      ws.send(JSON.stringify({ type: "job.updated", ts: new Date().toISOString(), data: job }));
    }
  }
});

// ---------------------------------------------------------------- boot
freshProject();
server.listen(PORT, () => {
  console.log(`[muestra-mock] proyecto: ${PROJECT}`);
  console.log(`[muestra-mock] http://localhost:${PORT}  (ws: /events)`);
  if (AUTORENDER) {
    // Demo: after a bit, simulate the agent rendering a new version so the
    // UI shows the live-update flow without anyone touching a terminal.
    setTimeout(() => {
      console.log("[muestra-mock] auto-render demo → nuevo job render");
      startRenderJob();
    }, Number(process.env.MOCK_AUTORENDER_DELAY ?? 20000));
  }
});
