import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { EngineSnapshot } from "../src/audio/engine";
import type { MixerState } from "../src/audio/mixer";
import type { PlaybackEngine } from "../src/state/store";

export interface MockHandle {
  url: string;
  proc: ChildProcess;
  dir: string;
  stop: () => Promise<void>;
  received: () => Promise<{ event: Record<string, unknown>; valid: boolean; errors: unknown[] }[]>;
  reset: () => Promise<void>;
}

/** Start ui/mock/server.mjs on an ephemeral port with its own project copy. */
export async function startMock(env: Record<string, string> = {}): Promise<MockHandle> {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "muestra-mock-"));
  const proc = spawn(process.execPath, [path.resolve(__dirname, "../mock/server.mjs")], {
    env: { ...process.env, PORT: "0", MOCK_DEMO: "0", MOCK_PROJECT_DIR: path.join(dir, "project"), ...env },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const url = await new Promise<string>((resolve, reject) => {
    let out = "";
    const t = setTimeout(() => reject(new Error(`mock did not start: ${out}`)), 30_000);
    proc.stdout!.on("data", (d) => {
      out += String(d);
      const m = /http:\/\/localhost:(\d+)/.exec(out);
      if (m) {
        clearTimeout(t);
        resolve(`http://127.0.0.1:${m[1]}`);
      }
    });
    proc.stderr!.on("data", (d) => (out += String(d)));
    proc.on("exit", (c) => reject(new Error(`mock exited ${c}: ${out}`)));
  });
  return {
    url,
    proc,
    dir,
    stop: async () => {
      proc.kill();
      await new Promise((r) => setTimeout(r, 100));
      fs.rmSync(dir, { recursive: true, force: true });
    },
    received: async () => (await fetch(`${url}/debug/received`)).json(),
    reset: async () => {
      await fetch(`${url}/debug/reset`, { method: "POST" });
    },
  };
}

/** Engine double: records calls, keeps a position, emits snapshots. */
export class FakeEngine implements PlaybackEngine {
  calls: [string, ...unknown[]][] = [];
  position = 3.25;
  mixer: MixerState | null = null;
  private listeners = new Set<() => void>();
  private snap: EngineSnapshot = {
    audio: "running",
    source: "decks",
    playing: false,
    active: null,
    partner: null,
    listening: "mix",
    stemBus: "native",
    stemsLoading: false,
    ready: [],
    loading: [],
    preview: null,
    error: null,
  };
  private set(p: Partial<EngineSnapshot>) {
    this.snap = { ...this.snap, ...p };
    this.listeners.forEach((f) => f());
  }
  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };
  getSnapshot = () => this.snap;
  register(spec: { version: string }) {
    this.calls.push(["register", spec.version]);
  }
  async prepare(v: string) {
    this.calls.push(["prepare", v]);
    if (!this.snap.ready.includes(v)) this.set({ ready: [...this.snap.ready, v].sort() });
  }
  async select(v: string) {
    this.calls.push(["select", v]);
    await this.prepare(v);
    this.set({ active: v });
  }
  setPartner(v: string | null) {
    this.calls.push(["setPartner", v]);
    this.set({ partner: v });
  }
  setInitial(v: string) {
    this.set({ active: v });
  }
  play() {
    this.calls.push(["play"]);
    this.set({ playing: true });
  }
  pause() {
    this.calls.push(["pause"]);
    this.set({ playing: false });
  }
  toggle() {
    if (this.snap.playing) this.pause();
    else this.play();
  }
  seek(s: number) {
    this.calls.push(["seek", s]);
    this.position = s;
  }
  getPosition = () => this.position;
  getDuration = () => 16;
  isPlaying = () => this.snap.playing;
  setLoop(l: unknown) {
    this.calls.push(["setLoop", l]);
  }
  setVolume(v: number) {
    this.calls.push(["setVolume", v]);
  }
  setLevels(m: Map<string, number>) {
    this.calls.push(["setLevels", Object.fromEntries(m)]);
  }
  setMixer(m: MixerState) {
    this.calls.push(["setMixer", m]);
    this.mixer = m;
    const touched = Object.values(m.tracks).some((t) => t.mute || t.solo || t.gainDb !== 0);
    this.set({ listening: touched ? "stems" : "mix" });
  }
  async playPreview(key: string) {
    this.calls.push(["playPreview", key]);
    this.set({ preview: key });
  }
  stopPreview() {
    if (this.snap.preview) this.set({ preview: null });
  }
  getPreviewPosition = () => null;
  unlock() {}
  clearError() {}
  last(name: string) {
    return [...this.calls].reverse().find((c) => c[0] === name);
  }
}
