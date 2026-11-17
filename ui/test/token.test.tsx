// Access token for non-loopback use (WP14 finding 6).
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { MuestraClient, isAuthError } from "../src/api/client";
import { captureToken, forgetToken, type TokenEnv } from "../src/api/token";
import { StoreProvider } from "../src/state/react";
import { Store } from "../src/state/store";
import { App } from "../src/ui/App";
import WS from "ws";
import { FakeEngine, startMock, type MockHandle } from "./helpers";

function fakeEnv(url: string, stored: Record<string, string> = {}) {
  const u = new URL(url);
  const store = { ...stored };
  const replaced: string[] = [];
  const env: TokenEnv = {
    location: { href: u.href, search: u.search, pathname: u.pathname, hash: u.hash },
    history: { state: null, replaceState: (_s: unknown, _t: string, link?: string | URL | null) => void replaced.push(String(link)) },
    storage: {
      getItem: (k: string) => store[k] ?? null,
      setItem: (k: string, v: string) => void (store[k] = v),
      removeItem: (k: string) => void delete store[k],
    },
  };
  return { env, store, replaced };
}

describe("captureToken", () => {
  it("takes the token from the URL on first load, strips it, and keeps it for the session", () => {
    const { env, store, replaced } = fakeEnv("http://192.168.1.20:8765/?token=abc123");
    expect(captureToken(env)).toBe("abc123");
    expect(replaced).toEqual(["/"]); // gone from the visible URL
    expect(Object.values(store)).toContain("abc123");
    // after the reload the URL has no token: it comes from storage
    const again = fakeEnv("http://192.168.1.20:8765/", store);
    expect(captureToken(again.env)).toBe("abc123");
    expect(again.replaced).toEqual([]);
  });
  it("keeps other query params and the hash when stripping", () => {
    const { env, replaced } = fakeEnv("http://h:1/app?api=http://x:2&token=t0k#frag");
    expect(captureToken(env)).toBe("t0k");
    expect(replaced[0]).toBe("/app?api=http%3A%2F%2Fx%3A2#frag");
  });
  it("a new link wins over a stale stored token; no token anywhere → null (loopback works unchanged)", () => {
    const { env } = fakeEnv("http://h:1/?token=new", { "muestra.token.v1": "old" });
    expect(captureToken(env)).toBe("new");
    expect(captureToken(fakeEnv("http://127.0.0.1:8765/").env)).toBeNull();
    expect(captureToken(null)).toBeNull();
  });
  it("blocked storage still returns the URL token", () => {
    const { env } = fakeEnv("http://h:1/?token=abc");
    env.storage = null;
    expect(captureToken(env)).toBe("abc");
  });
  it("forgetToken drops the stored one", () => {
    const { env, store } = fakeEnv("http://h:1/?token=abc");
    captureToken(env);
    forgetToken(env);
    expect(Object.keys(store)).toHaveLength(0);
  });
});

describe("client with a token", () => {
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

  it("sends it on every HTTP request (tools, /project, files, GET json) and on the websocket", async () => {
    const seen: Record<string, string>[] = [];
    const f = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      seen.push({ ...(init?.headers as Record<string, string>), _url: String(url) });
      if (String(url).includes("/tools/")) return json({ tool: "t", ok: true, data: {}, files: [], errors: [], hint: null });
      if (String(url).includes("/files/")) return new Response(new ArrayBuffer(4));
      return json({ project_path: "/p", versions: [] });
    }) as unknown as typeof fetch;
    const c = new MuestraClient({ baseUrl: "http://h:1", token: "tok 1", fetchImpl: f });
    await c.listVersions();
    await c.getSummary();
    await c.fetchFile("renders/v001/mix.wav");
    await c.getJob("j1");
    expect(seen).toHaveLength(4);
    for (const h of seen) {
      expect(h.authorization).toBe("Bearer tok 1");
      expect(h["x-muestra-token"]).toBe("tok 1");
    }
    expect(c.eventsUrl()).toBe("ws://h:1/events?token=tok%201");
  });

  it("without a token nothing extra is sent (loopback unchanged)", async () => {
    const seen: (HeadersInit | undefined)[] = [];
    const f = vi.fn(async (_u: string | URL | Request, init?: RequestInit) => {
      seen.push(init?.headers);
      return json({ tool: "t", ok: true, data: {}, files: [], errors: [], hint: null });
    }) as unknown as typeof fetch;
    const c = new MuestraClient({ baseUrl: "http://h:1", fetchImpl: f });
    await c.listVersions();
    expect(seen[0]).toEqual({ "content-type": "application/json" });
    expect(c.eventsUrl()).toBe("ws://h:1/events");
  });

  it("401/403 → onAuthError + an ApiError the app can recognise", async () => {
    const seen: number[] = [];
    const f = (async () => json({ detail: "token requerido" }, 401)) as unknown as typeof fetch;
    const c = new MuestraClient({ fetchImpl: f, onAuthError: (s) => seen.push(s) });
    const err = await c.listVersions().catch((e: unknown) => e);
    expect(isAuthError(err)).toBe(true);
    await c.getSummary();
    await expect(c.fetchFile("renders/v001/mix.wav")).rejects.toSatisfy(isAuthError);
    expect(seen.length).toBeGreaterThanOrEqual(3);
  });
});

describe("a server that requires a token (mock with MOCK_TOKEN)", () => {
  let mock: MockHandle;
  beforeAll(async () => {
    mock = await startMock({ MOCK_TOKEN: "s3cret" });
  }, 60_000);
  afterAll(async () => {
    await mock?.stop();
  });

  async function boot(token: string | null) {
    const engine = new FakeEngine();
    let store: Store | null = null;
    const client = new MuestraClient({ baseUrl: mock.url, token, onAuthError: (s) => store?.authRefused(s) });
    store = new Store({ client, engine, WebSocketImpl: null });
    render(
      <StoreProvider store={store}>
        <App />
      </StoreProvider>,
    );
    await act(() => store!.init());
    return store;
  }

  it("without the token: a friendly Spanish screen that says to open the link from the terminal", async () => {
    await boot(null);
    const s = await screen.findByTestId("unauthorized");
    expect(s.textContent).toContain("muestra serve");
    expect(s.textContent).toContain("?token=");
    expect(screen.queryByTestId("boot-error")).toBeNull();
  });

  it("with a wrong token: the same screen", async () => {
    const store = await boot("nope");
    expect(store.state.boot).toBe("unauthorized");
  });

  it("the websocket carries the token too (and is refused without it)", async () => {
    const open = (url: string) =>
      new Promise<string>((resolve) => {
        const ws = new WS(url);
        ws.on("open", () => (ws.close(), resolve("open")));
        ws.on("unexpected-response", (_r, res) => resolve(String(res.statusCode)));
        ws.on("error", () => resolve("error"));
      });
    expect(await open(new MuestraClient({ baseUrl: mock.url, token: "s3cret" }).eventsUrl())).toBe("open");
    expect(await open(new MuestraClient({ baseUrl: mock.url }).eventsUrl())).toBe("401");
  });

  it("with the right token: the app boots normally", async () => {
    const store = await boot("s3cret");
    expect(store.state.boot).toBe("ready");
    expect(store.state.versions.length).toBeGreaterThan(0);
  });
});
