import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { decodeWav as mockDecode, encodeWav } from "../mock/wav.mjs";
import { decodeWav, isWav } from "../src/audio/wav";

const FIX = path.resolve(__dirname, "../../fixtures/example-project/renders/v001");

function ab(buf: Uint8Array): ArrayBuffer {
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;
}

function header(v: DataView, at: number, s: string) {
  for (let i = 0; i < s.length; i++) v.setUint8(at + i, s.charCodeAt(i));
}

describe("WAV fallback decoder", () => {
  it("decodes the fixture's 32-bit float stems exactly, unclamped (contract R7)", () => {
    const raw = fs.readFileSync(path.join(FIX, "stems/drums.wav"));
    const d = decodeWav(ab(raw));
    const ref = mockDecode(raw);
    expect(d.format).toBe("float");
    expect(d.bitsPerSample).toBe(32);
    expect(d.sampleRate).toBe(ref.sampleRate);
    expect(d.channels).toHaveLength(2);
    expect(d.frames).toBe(ref.frames);
    let peak = 0;
    let same = true;
    for (let i = 0; i < d.frames; i++) {
      if (d.channels[0][i] !== ref.data[0][i]) same = false;
      peak = Math.max(peak, Math.abs(d.channels[0][i]));
    }
    expect(same).toBe(true);
    expect(peak).toBeGreaterThan(1); // pre-master stems exceed full scale
  });

  it("decodes the fixture's 24-bit PCM mix", () => {
    const raw = fs.readFileSync(path.join(FIX, "mix.wav"));
    const d = decodeWav(ab(raw));
    const ref = mockDecode(raw);
    expect(d.format).toBe("pcm");
    expect(d.bitsPerSample).toBe(24);
    for (let i = 0; i < d.frames; i += 97) expect(d.channels[1][i]).toBeCloseTo(ref.data[1][i], 6);
  });

  it("decodes 16-bit PCM", () => {
    const buf = new ArrayBuffer(44 + 8);
    const v = new DataView(buf);
    header(v, 0, "RIFF");
    v.setUint32(4, 44, true);
    header(v, 8, "WAVE");
    header(v, 12, "fmt ");
    v.setUint32(16, 16, true);
    v.setUint16(20, 1, true);
    v.setUint16(22, 1, true);
    v.setUint32(24, 8000, true);
    v.setUint32(28, 16000, true);
    v.setUint16(32, 2, true);
    v.setUint16(34, 16, true);
    header(v, 36, "data");
    v.setUint32(40, 8, true);
    [0, 16384, -32768, 32767].forEach((s, i) => v.setInt16(44 + i * 2, s, true));
    expect(Array.from(decodeWav(buf).channels[0])).toEqual([0, 0.5, -1, 32767 / 32768]);
  });

  it("decodes WAVE_FORMAT_EXTENSIBLE float after an odd-sized chunk", () => {
    const fmtBody = 40;
    const buf = new ArrayBuffer(12 + (8 + 4) + (8 + fmtBody) + (8 + 16));
    const v = new DataView(buf);
    header(v, 0, "RIFF");
    v.setUint32(4, buf.byteLength - 8, true);
    header(v, 8, "WAVE");
    let o = 12;
    header(v, o, "LIST");
    v.setUint32(o + 4, 3, true); // odd size → one pad byte
    o += 8 + 4;
    header(v, o, "fmt ");
    v.setUint32(o + 4, fmtBody, true);
    v.setUint16(o + 8, 0xfffe, true);
    v.setUint16(o + 10, 2, true);
    v.setUint32(o + 12, 48000, true);
    v.setUint32(o + 16, 48000 * 8, true);
    v.setUint16(o + 20, 8, true);
    v.setUint16(o + 22, 32, true);
    v.setUint16(o + 24, 22, true);
    v.setUint16(o + 26, 32, true);
    v.setUint32(o + 28, 3, true);
    v.setUint16(o + 32, 3, true); // SubFormat = IEEE float
    o += 8 + fmtBody;
    header(v, o, "data");
    v.setUint32(o + 4, 16, true);
    [0.25, -0.25, 1.5, -2].forEach((s, i) => v.setFloat32(o + 8 + i * 4, s, true));
    const x = decodeWav(buf);
    expect(x.format).toBe("float");
    expect(x.sampleRate).toBe(48000);
    expect(Array.from(x.channels[0])).toEqual([0.25, 1.5]);
    expect(Array.from(x.channels[1])).toEqual([-0.25, -2]);
  });

  it("round-trips the mock encoder (float32 + pcm24)", () => {
    const src = { sampleRate: 22050, data: [Float32Array.from([0, 0.5, -0.75, 1.25]), Float32Array.from([1, -1, 0.1, -0.1])] };
    const f = decodeWav(ab(encodeWav(src, "float32")));
    expect(Array.from(f.channels[0])).toEqual(Array.from(src.data[0]));
    const p = decodeWav(ab(encodeWav(src, "pcm24")));
    expect(p.channels[0][3]).toBeCloseTo(8388607 / 8388608, 6); // PCM clamps
    expect(p.channels[1][2]).toBeCloseTo(0.1, 6);
  });

  it("rejects non-WAV data", () => {
    expect(isWav(new ArrayBuffer(4))).toBe(false);
    expect(() => decodeWav(new TextEncoder().encode("ID3 not a wav file").buffer as ArrayBuffer)).toThrow();
  });
});
