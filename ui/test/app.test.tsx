// Component tests: the real <App/> against the real mock server (contract
// routes, schema-validating feedback.append), with a fake audio engine.

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import WS from "ws";
import { MuestraClient } from "../src/api/client";
import { StoreProvider } from "../src/state/react";
import { Store } from "../src/state/store";
import { App } from "../src/ui/App";
import { FakeEngine, startMock, type MockHandle } from "./helpers";

let mock: MockHandle;

beforeAll(async () => {
  mock = await startMock();
}, 60_000);

afterAll(async () => {
  await mock?.stop();
});

beforeEach(async () => {
  await mock.reset();
  localStorage.clear();
  // wide desktop layout: versions | player | feedback all visible
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 1440 });
});

async function boot(opts: { ws?: boolean } = {}) {
  const engine = new FakeEngine();
  const client = new MuestraClient({ baseUrl: mock.url });
  const store = new Store({ client, engine, // `ws` (Node) — jsdom's WebSocket can't mix with Node's event classes
    WebSocketImpl: opts.ws ? (WS as unknown as typeof WebSocket) : null });
  const user = userEvent.setup();
  render(
    <StoreProvider store={store}>
      <App />
    </StoreProvider>,
  );
  await act(() => store.init());
  await screen.findByTestId("versions");
  return { store, engine, user };
}

async function lastReceived() {
  const r = await mock.received();
  return r[r.length - 1];
}

describe("App against the mock", () => {
  it("boots on the newest version with its parent as B", async () => {
    const { store } = await boot();
    expect(store.state.versions.map((v) => v.version)).toEqual(["v001", "v002", "v003", "v004"]);
    expect(screen.getByTestId("now-version").textContent).toBe("v004");
    expect(store.state.b).toBe("v002"); // v004's parent
    expect(screen.getByTestId("hear-b").textContent).toContain("v002");
    // loudness match is ON by default and says what it does
    expect((screen.getByTestId("loudness-match") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByTestId("loudness-note").textContent).toMatch(/baja .* dB/);
  });

  it("A/B: S flips to the other side of the pair", async () => {
    const { engine, user } = await boot();
    await user.keyboard("s");
    await waitFor(() => expect(engine.last("select")).toEqual(["select", "v002"]));
    expect(screen.getByTestId("now-version").textContent).toBe("v002");
    await user.keyboard("a");
    await waitFor(() => expect(engine.last("select")).toEqual(["select", "v004"]));
  });

  it("number keys jump to versions; picking B in the list sets the pair", async () => {
    const { store, user } = await boot();
    await user.keyboard("1");
    await waitFor(() => expect(store.state.active).toBe("v001"));
    // v001 replaced the side we were hearing (A); B stays
    expect(store.state.a).toBe("v001");
    expect(store.state.b).toBe("v002");
    await user.click(screen.getByTestId("set-b-v003"));
    expect(store.state.b).toBe("v003");
  });

  it("M → type → Enter writes a schema-valid comment at the playhead", async () => {
    const { engine, user } = await boot();
    engine.position = 9.4321;
    await user.keyboard("m");
    const input = await screen.findByTestId("quick-comment-input");
    expect(document.activeElement).toBe(input);
    await user.keyboard("el bombo se come al sample{Enter}");
    await waitFor(async () => {
      const r = await lastReceived();
      expect(r?.event).toMatchObject({ type: "comment", version: "v004", at_s: 9.432, text: "el bombo se come al sample" });
      expect(r.valid).toBe(true);
    });
    expect(screen.queryByTestId("quick-comment")).toBeNull();
    // shows up immediately as pending for the agent
    const list = screen.getAllByTestId("feedback-list")[0];
    expect(within(list).getAllByText("el bombo se come al sample").length).toBeGreaterThan(0);
  });

  it("comment pinned to a track", async () => {
    const { user } = await boot();
    await user.click(screen.getByTestId("comment-here"));
    await user.click(within(screen.getByTestId("quick-comment")).getByRole("button", { name: "drums" }));
    await user.type(screen.getByTestId("quick-comment-input"), "flojo el clap{Enter}");
    await waitFor(async () => expect((await lastReceived())?.event).toMatchObject({ track: "drums", text: "flojo el clap" }));
  });

  it("Escape cancels the quick comment without sending", async () => {
    const { user } = await boot();
    const before = (await mock.received()).length;
    await user.keyboard("m");
    await screen.findByTestId("quick-comment-input");
    await user.keyboard("borrador{Escape}");
    expect(screen.queryByTestId("quick-comment")).toBeNull();
    expect((await mock.received()).length).toBe(before);
  });

  it("like/dislike writes rating events", async () => {
    const { user } = await boot();
    await user.click(screen.getByTestId("like-v003"));
    await waitFor(async () => {
      const r = await lastReceived();
      expect(r.event).toMatchObject({ type: "rating", version: "v003", value: "like" });
      expect(r.valid).toBe(true);
    });
  });

  it("general comment box (Enter sends, attached to the version)", async () => {
    const { user } = await boot();
    const box = screen.getAllByTestId("general-comment")[0];
    await user.type(box, "el final queda vacío{Enter}");
    await waitFor(async () => expect((await lastReceived())?.event).toMatchObject({ type: "comment", version: "v004", text: "el final queda vacío" }));
    expect((box as HTMLTextAreaElement).value).toBe("");
  });

  it("mute switches to stems and shows the 'sin master' indicator", async () => {
    const { engine, user } = await boot();
    await user.click(screen.getByTestId("mute-drums"));
    expect(engine.mixer?.tracks.drums.mute).toBe(true);
    expect(await screen.findByTestId("stems-badge")).toBeTruthy();
    expect(screen.getByTestId("stems-badge").textContent).toContain("stems sin master");
    await user.click(screen.getByTestId("mute-drums"));
    expect(screen.queryByTestId("stems-badge")).toBeNull();
  });

  it("blind A/B hides identities and records ab_choice", async () => {
    const { store, user } = await boot();
    store.startBlind(() => 0.2); // X = A (v004)
    await screen.findByTestId("blind-x");
    expect(screen.getByTestId("now-version").textContent).toBe("X");
    expect(screen.getByTestId("versions").textContent).toContain("Modo ciego");
    expect(screen.getByTestId("blind-x").textContent).not.toContain("v00");
    await user.click(screen.getByTestId("pick-y"));
    await waitFor(async () => {
      const r = await lastReceived();
      expect(r.event).toMatchObject({ type: "ab_choice", a: "v004", b: "v002", winner: "v002" });
      expect(r.valid).toBe(true);
    });
    expect((await screen.findByTestId("blind-reveal")).textContent).toContain("v002");
  });

  it("chop candidates: nudge and confirm calls chops.choose (manual)", async () => {
    const { user } = await boot();
    await user.click(screen.getByTestId("tab-chops"));
    const card = await screen.findByTestId("cand-cand2");
    await user.click(card);
    await user.click(within(card).getByRole("button", { name: "Inicio de cand2: un beat más" }));
    await user.click(within(card).getByRole("button", { name: "Largo de cand2: un beat menos" }));
    await user.click(screen.getByTestId("confirm-chop"));
    await waitFor(async () => {
      const r = await lastReceived();
      expect(r.event).toMatchObject({ type: "chop_choice", candidate_id: "cs001/cand2", adjust: { start_beats: 1, length_beats: -1 } });
      expect(r.valid).toBe(true);
    });
    expect(await screen.findByText(/elegido: cand2 \(vos\)/)).toBeTruthy();
  });

  it("oídos: alarms in Spanish, clickable to jump", async () => {
    const { engine, user } = await boot();
    await user.keyboard("2"); // v002 has positioned alarms
    await user.click(screen.getByTestId("tab-ears"));
    const btn = await screen.findByRole("button", { name: /Graves que se pisan/ });
    await user.click(btn);
    expect(engine.last("seek")).toEqual(["seek", 2]);
    expect(screen.getByTestId("lufs-delta").textContent).toMatch(/dB (más fuerte|más bajo) que v00\d/);
  });

  it("live: a render job shows progress and the new version is offered as B", async () => {
    const { store } = await boot({ ws: true });
    await waitFor(() => expect(store.state.ws).toBe("open"), { timeout: 5000 });
    await fetch(`${mock.url}/debug/render?ms=1200`, { method: "POST" });
    expect(await screen.findByText("Renderizando versión", {}, { timeout: 4000 })).toBeTruthy();
    const offer = await screen.findByTestId("offer", {}, { timeout: 8000 });
    expect(offer.textContent).toContain("v005");
    fireEvent.click(screen.getByTestId("offer-compare"));
    await waitFor(() => expect(store.state.active).toBe("v005"));
    expect(store.state.b).toBe("v004");
    store.destroy();
  });

  it("feedback written elsewhere arrives over the websocket", async () => {
    const { store } = await boot({ ws: true });
    await waitFor(() => expect(store.state.ws).toBe("open"), { timeout: 5000 });
    await new MuestraClient({ baseUrl: mock.url }).appendFeedback({ ts: new Date().toISOString(), type: "comment", text: "desde otra pestaña" });
    expect((await screen.findAllByText("desde otra pestaña", {}, { timeout: 4000 })).length).toBe(1);
    store.destroy();
  });

  it("empty project teaches what to say to the agent", async () => {
    const engine = new FakeEngine();
    const client = new MuestraClient({ baseUrl: mock.url });
    client.listVersions = async () => ({ files: [], versions: [] });
    client.getSummary = async () => null;
    const store = new Store({ client, engine, WebSocketImpl: null });
    render(
      <StoreProvider store={store}>
        <App />
      </StoreProvider>,
    );
    await act(() => store.init());
    const empty = await screen.findByTestId("empty");
    expect(empty.textContent).toContain("Armame un loop future funk");
  });

  it("API down → explains how to start it", async () => {
    const engine = new FakeEngine();
    const store = new Store({ client: new MuestraClient({ baseUrl: "http://127.0.0.1:9" }), engine, WebSocketImpl: null });
    render(
      <StoreProvider store={store}>
        <App />
      </StoreProvider>,
    );
    await act(() => store.init());
    expect((await screen.findByTestId("boot-error")).textContent).toContain("muestra serve --project");
  });
});
