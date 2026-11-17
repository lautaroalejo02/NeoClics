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

  describe("manual chop choice (WP14 finding 2)", () => {
    const setChoose = (mode: string) => fetch(`${mock.url}/debug/choose?mode=${mode}`, { method: "POST" });
    async function openChops() {
      const b = await boot();
      await b.user.click(screen.getByTestId("tab-chops"));
      await b.user.click(await screen.findByTestId("cand-cand2"));
      return b;
    }

    it("success says what happened, in Spanish, and shows it in Chops and in the feedback list", async () => {
      const { user } = await openChops();
      await user.click(screen.getByTestId("confirm-chop"));
      const out = await screen.findByTestId("chop-outcome");
      expect(out.dataset.status).toBe("ok");
      expect(out.textContent).toContain("Elegiste el candidato 2; el agente lo va a usar en la próxima versión.");
      expect(await screen.findByText(/elegido: cand2 \(vos\)/)).toBeTruthy();
      await user.click(screen.getByRole("button", { name: "Todo" }));
      expect(within(screen.getByTestId("feedback-list")).getAllByText(/cs001\/cand2/).length).toBeGreaterThan(0);
    });

    it("a chop written under a new id says so; a rescaled clip is explained", async () => {
      await setChoose("newid");
      const { user } = await openChops();
      await user.click(screen.getByTestId("confirm-chop"));
      expect((await screen.findByTestId("chop-outcome")).textContent).toMatch(/Quedó guardado como chop «c1_2» \(no pisé «c1»\)/);
      await setChoose("rescale");
      await user.click(screen.getByTestId("confirm-chop"));
      await waitFor(() => expect(screen.getByTestId("chop-outcome").textContent).toContain("Ajusté la repetición del clip (repetición 8 → 5)"));
    });

    it("an English backend error is never the primary message; the one-click comment keeps the choice", async () => {
      await setChoose("fail");
      const { user } = await openChops();
      await user.click(screen.getByTestId("confirm-chop"));
      const out = await screen.findByTestId("chop-outcome");
      expect(out.dataset.status).toBe("error");
      const primary = out.querySelector("strong")!.textContent!;
      expect(primary).toMatch(/Ese corte es más largo/);
      expect(primary).not.toContain("Clip ends");
      expect(out.textContent).toContain("Clip ends at beat 128"); // kept as secondary detail
      // nothing was recorded yet...
      expect((await mock.received()).some((r) => r.event.type === "chop_choice")).toBe(false);
      // ...so one click leaves it for the agent as a comment
      await user.click(screen.getByTestId("chop-as-comment"));
      await waitFor(async () => {
        const r = await lastReceived();
        expect(r.valid).toBe(true);
        expect(r.event).toMatchObject({ type: "comment" });
        expect(String(r.event.text)).toContain("cs001/cand2");
        expect(String(r.event.text)).toContain("«c1»");
      });
      expect(screen.getByTestId("chop-outcome").dataset.status).toBe("commented");
      expect(screen.queryByTestId("chop-as-comment")).toBeNull();
    });

    it("if the backend recorded the choice despite failing, we say it is not lost", async () => {
      await setChoose("fail-recorded");
      const { user } = await openChops();
      await user.click(screen.getByTestId("confirm-chop"));
      const out = await screen.findByTestId("chop-outcome");
      await waitFor(() => expect(out.dataset.status).toBe("recorded"));
      expect(screen.queryByTestId("chop-as-comment")).toBeNull();
    });
  });

  describe("versions without audio (WP14 finding 3)", () => {
    const del = (vs: string) => fetch(`${mock.url}/debug/delete-renders?versions=${vs}`, { method: "POST" });

    it("lists them greyed out, with summary, feedback and a copyable regeneration request; no play/A-B", async () => {
      await del("v001,v003");
      const { store, engine, user } = await boot();
      expect(store.state.versions.map((v) => v.version)).toEqual(["v002", "v004"]);
      expect(store.state.noAudio.map((v) => v.version)).toEqual(["v001", "v003"]);
      const row = screen.getByTestId("version-v003");
      expect(row.dataset.noaudio).toBe("true");
      expect(row.className).toContain("ghost");
      // summary + the human's feedback survive
      expect(row.textContent).toContain("Abrí el filtro del sample");
      expect(within(screen.getByTestId("version-v001")).getByText(/me gustó/)).toBeTruthy();
      // clear message, no play / A-B affordances
      expect(screen.getByTestId("noaudio-v003").textContent).toContain("Sin audio: pedile al agente que la regenere (render de v003)");
      expect(within(row).queryByRole("button", { name: /Escuchar v003/ })).toBeNull();
      expect(within(row).queryByRole("button", { name: /v003 como A/ })).toBeNull();
      expect(within(row).queryByRole("button", { name: /v003 como B/ })).toBeNull();
      await user.click(row);
      expect(engine.calls.some((c) => c[0] === "select" && c[1] === "v003")).toBe(false);
      // the copy button puts the request on the clipboard
      const written: string[] = [];
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (t: string) => void written.push(t) } });
      await user.click(screen.getByTestId("copy-regen-v003"));
      expect(written[0]).toContain("render de v003");
      expect(written[0]).toContain("v003");
      // number keys still map to versions with audio only
      expect(store.versionAt(2)).toBe("v004");
    });

    it("when NO version has audio the app says so instead of 'no versions yet'", async () => {
      await del("v001,v002,v003,v004");
      const { store } = await boot().catch(async () => {
        throw new Error("boot failed");
      });
      void store;
      expect(screen.queryByTestId("empty")).toBeNull();
      const card = await screen.findByTestId("no-audio");
      expect(card.textContent).toContain("render de v004");
      expect(screen.getAllByTestId(/^version-v00[1-4]$/)).toHaveLength(4);
    });
  });

  describe("drafts and previews (WP14 finding 4)", () => {
    it("no drafts exposed → no area (today's server)", async () => {
      await boot();
      expect(screen.queryByTestId("drafts")).toBeNull();
    });

    it("lists a draft from /project, marked as not a version, playable with the same engine", async () => {
      await fetch(`${mock.url}/debug/draft`, { method: "POST" });
      const { engine, user } = await boot();
      const area = await screen.findByTestId("drafts");
      expect(area.textContent).toContain("Borradores y previews");
      expect(area.textContent).toContain("no es una versión");
      expect(area.textContent).toContain("compases 1–2");
      // it is not in the version list
      expect(screen.getByTestId("versions").querySelectorAll("[data-testid^='version-']")).toHaveLength(4);
      await user.click(screen.getByTestId("play-draft-d001"));
      const call = engine.calls.find((c) => c[0] === "playPreview");
      expect(call?.[1]).toBe("draft:d001");
    });

    it("a draft announced live (draft.created) appears without reloading", async () => {
      const { store } = await boot({ ws: true });
      await waitFor(() => expect(store.state.ws).toBe("open"), { timeout: 5000 });
      expect(screen.queryByTestId("drafts")).toBeNull();
      await fetch(`${mock.url}/debug/draft?label=Probando%20el%20bombo`, { method: "POST" });
      expect((await screen.findByTestId("drafts", {}, { timeout: 4000 })).textContent).toContain("Probando el bombo");
      store.destroy();
    });
  });

  it("chops: 'escuchar la grilla' shows the corrected grid and plays its click previews", async () => {
    const fs = await import("node:fs");
    const path = await import("node:path");
    const project = path.join(mock.dir, "project");
    const apath = path.join(project, "cache", "analysis", "src1.json");
    const analysis = JSON.parse(fs.readFileSync(apath, "utf8"));
    analysis.grid = {
      origin: "auto", tempo_factor: 0.5, beat_phase: 0, downbeat_shift_beats: 0, beats_per_bar: 4,
      tempo_range_bpm: [70, 160], raw: { bpm_global: 250, beats_s: analysis.beats_s, downbeats_s: analysis.downbeats_s },
      tempo_confidence: 0.7, bar_phase_confidence: 0.5, ask_human: true,
      reason: "El tracker leyó 250 BPM (contó corcheas); se tomó un beat de cada dos: 125 BPM.", alternatives: [],
    };
    fs.writeFileSync(apath, JSON.stringify(analysis));
    const gdir = path.join(project, "cache", "previews", "src1", "grid");
    fs.mkdirSync(gdir, { recursive: true });
    const preview = { path: "cache/previews/src1/grid/mix-abc.wav", label: "grilla actual", applied: true, tempo_factor: 0.5, beat_phase: 0, downbeat_shift_beats: 0, beats_per_bar: 4, bpm: 125, start_downbeat: 1, start_s: 0, end_s: 8, bars: 4 };
    const alt = { ...preview, path: "cache/previews/src1/grid/mix-def.wav", label: "el uno dos tiempos más tarde", applied: false, downbeat_shift_beats: 2 };
    fs.writeFileSync(path.join(gdir, "index.json"), JSON.stringify({ source_id: "src1", created_at: "2026-11-17T00:00:00Z", bpm: 125, raw_bpm: 250, previews: [preview, alt] }));
    const { engine, user } = await boot();
    await user.click(screen.getByTestId("tab-chops"));
    const panel = await screen.findByTestId("grid-src1");
    expect(panel.textContent).toContain("el detector leyó 250");
    expect(panel.textContent).toContain("confirmala de oído");
    const buttons = within(panel).getAllByTestId("grid-preview");
    expect(buttons.map((b) => b.textContent?.trim())).toEqual(["Actual", "el uno dos tiempos más tarde"]);
    await user.click(buttons[1]);
    expect(engine.calls).toContainEqual(["playPreview", `grid:src1:${alt.path}`]);
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
