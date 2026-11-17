import { describe, expect, it, vi } from "vitest";
import { ApiError, MuestraClient, encodePath } from "../src/api/client";

type Call = { url: string; init?: RequestInit };

function fakeFetch(routes: Record<string, (init?: RequestInit) => Response>) {
  const calls: Call[] = [];
  const f = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const u = String(url);
    calls.push({ url: u, init });
    const path = u.replace(/^https?:\/\/[^/]+/, "");
    const key = Object.keys(routes).find((k) => path.split("?")[0] === k);
    return key ? routes[key](init) : new Response(JSON.stringify({ detail: "nope" }), { status: 404 });
  });
  return { f: f as unknown as typeof fetch, calls };
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const env = (tool: string, data: unknown) => json({ tool, ok: true, data, files: [], errors: [], hint: null });

describe("MuestraClient", () => {
  it("POSTs tool params and unwraps the ToolResult envelope", async () => {
    const { f, calls } = fakeFetch({ "/tools/versions_list": () => env("versions.list", { files: [], versions: [{ version: "v001" }] }) });
    const c = new MuestraClient({ baseUrl: "http://api:1/", fetchImpl: f });
    const r = await c.listVersions();
    expect(r.versions[0].version).toBe("v001");
    expect(calls[0].url).toBe("http://api:1/tools/versions_list");
    expect(calls[0].init?.method).toBe("POST");
    // the HTTP API is bound to one project and injects it: never sent
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({});
  });

  it("sends `project` only when explicitly configured", async () => {
    const { f, calls } = fakeFetch({ "/tools/feedback_append": () => env("feedback.append", { appended: 1, files: [] }) });
    const c = new MuestraClient({ fetchImpl: f, project: "/p" });
    await c.appendFeedback({ ts: "2026-01-01T00:00:00Z", type: "comment", text: "hola" });
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ project: "/p", event: { ts: "2026-01-01T00:00:00Z", type: "comment", text: "hola" } });
  });

  it("turns ok:false into an ApiError carrying the issues", async () => {
    const issues = [{ path: "event.text", code: "too_small", message: "String should have at least 1 character", hint: "Write something" }];
    const { f } = fakeFetch({ "/tools/feedback_append": () => json({ tool: "feedback.append", ok: false, data: null, files: [], errors: issues, hint: "bad" }) });
    const c = new MuestraClient({ fetchImpl: f });
    const err = await c.appendFeedback({ ts: "2026-01-01T00:00:00Z", type: "comment", text: "" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).issues).toEqual(issues);
    expect((err as ApiError).message).toContain("at least 1 character");
  });

  it("reports HTTP errors and network failures as ApiError", async () => {
    const { f } = fakeFetch({ "/tools/chops_list": () => new Response("boom", { status: 500 }) });
    const c = new MuestraClient({ fetchImpl: f });
    await expect(c.listChopSets()).rejects.toMatchObject({ status: 500 });
    const failing = (async () => {
      throw new TypeError("fetch failed");
    }) as unknown as typeof fetch;
    const down = new MuestraClient({ fetchImpl: failing });
    await expect(down.listVersions()).rejects.toMatchObject({ status: null });
  });

  it("chooses a chop in manual mode, adjust only when non-zero", async () => {
    const { f, calls } = fakeFetch({ "/tools/chops_choose": () => env("chops.choose", { set_id: "cs001", candidate_id: "cs001/cand2", chop: {}, files: [] }) });
    const c = new MuestraClient({ fetchImpl: f });
    await c.chooseChop("cs001/cand2", "c1", { start_beats: 0, length_beats: 0 });
    await c.chooseChop("cs001/cand2", "c1", { start_beats: -1, length_beats: 2 });
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ candidate_id: "cs001/cand2", chop_id: "c1", mode: "manual" });
    expect(JSON.parse(String(calls[1].init?.body)).adjust).toEqual({ start_beats: -1, length_beats: 2 });
  });

  it("reads meta/metrics through /files and treats 404 as missing", async () => {
    const { f, calls } = fakeFetch({ "/files/renders/v001/meta.json": () => json({ version: "v001" }) });
    const c = new MuestraClient({ fetchImpl: f });
    expect((await c.getMeta("v001"))?.version).toBe("v001");
    expect(await c.getMetrics("v001")).toBeNull();
    expect(calls.map((x) => x.url)).toEqual(["/files/renders/v001/meta.json", "/files/renders/v001/metrics.json"]);
  });

  it("optional accelerators: null on 404 and not retried", async () => {
    const { f, calls } = fakeFetch({});
    const c = new MuestraClient({ fetchImpl: f });
    expect(await c.getSummary()).toBeNull();
    expect(await c.getSummary()).toBeNull();
    expect(await c.getPeaks("renders/v001/mix.wav", 100)).toBeNull();
    expect(await c.getPeaks("renders/v002/mix.wav", 100)).toBeNull();
    expect(calls.filter((x) => x.url.startsWith("/summary"))).toHaveLength(1);
    expect(calls.filter((x) => x.url.startsWith("/peaks"))).toHaveLength(1);
  });

  it("uses the peaks accelerator when present", async () => {
    const { f, calls } = fakeFetch({ "/peaks/renders/v001/mix.wav": () => json({ peaks: [[[-0.5, 0.5]]] }) });
    const c = new MuestraClient({ fetchImpl: f });
    expect((await c.getPeaks("renders/v001/mix.wav", 64))?.peaks[0][0]).toEqual([-0.5, 0.5]);
    expect(calls[0].url).toBe("/peaks/renders/v001/mix.wav?buckets=64");
  });

  it("encodes file paths per segment and derives the websocket URL", () => {
    expect(encodePath("renders/v001/stems/my track.wav")).toBe("renders/v001/stems/my%20track.wav");
    const c = new MuestraClient({ baseUrl: "http://h:9" });
    expect(c.fileUrl("cache/previews/src1/cs001/cand1.wav")).toBe("http://h:9/files/cache/previews/src1/cs001/cand1.wav");
    expect(c.eventsUrl()).toBe("ws://h:9/events");
    expect(new MuestraClient({ baseUrl: "https://x.y" }).eventsUrl()).toBe("wss://x.y/events");
  });

  it("parses websocket events strictly", () => {
    expect(MuestraClient.parseWsEvent('{"type":"version.created","ts":"t","data":{"version":"v002"}}')).toEqual({
      type: "version.created",
      ts: "t",
      data: { version: "v002" },
    });
    expect(MuestraClient.parseWsEvent('{"type":"nope","ts":"t"}')).toBeNull();
    expect(MuestraClient.parseWsEvent("not json")).toBeNull();
    expect(MuestraClient.parseWsEvent(42)).toBeNull();
    const ev = { ts: "2026-01-01T00:00:00Z", type: "rating", version: "v001", value: "like" };
    expect(MuestraClient.feedbackFromWs(ev)).toEqual(ev);
    expect(MuestraClient.feedbackFromWs({ event: ev })).toEqual(ev);
    expect(MuestraClient.feedbackFromWs({})).toBeNull();
  });
});
