// Pure-TS WAV decoder: the fallback when a browser's decodeAudioData
// rejects a file (render stems are 32-bit float WAV, mix is 24-bit PCM;
// contracts.md R7). Handles RIFF/WAVE, WAVE_FORMAT_PCM (8/16/24/32-bit
// int), WAVE_FORMAT_IEEE_FLOAT (32/64-bit) and WAVE_FORMAT_EXTENSIBLE.
// Output is planar Float32Array per channel, ready for createBuffer().

export interface DecodedWav {
  sampleRate: number;
  channels: Float32Array[];
  frames: number;
  format: "pcm" | "float";
  bitsPerSample: number;
}

const FMT_PCM = 1;
const FMT_FLOAT = 3;
const FMT_EXTENSIBLE = 0xfffe;

function tag(v: DataView, at: number): string {
  return String.fromCharCode(v.getUint8(at), v.getUint8(at + 1), v.getUint8(at + 2), v.getUint8(at + 3));
}

export function isWav(buf: ArrayBuffer): boolean {
  if (buf.byteLength < 12) return false;
  const v = new DataView(buf);
  return tag(v, 0) === "RIFF" && tag(v, 8) === "WAVE";
}

export function decodeWav(buf: ArrayBuffer): DecodedWav {
  const v = new DataView(buf);
  if (!isWav(buf)) throw new Error("No es un archivo RIFF/WAVE");
  let pos = 12;
  let fmt: { format: number; channels: number; sampleRate: number; bits: number; blockAlign: number } | null = null;
  let dataStart = -1;
  let dataLen = 0;
  while (pos + 8 <= v.byteLength) {
    const id = tag(v, pos);
    const size = v.getUint32(pos + 4, true);
    const body = pos + 8;
    if (id === "fmt ") {
      let format = v.getUint16(body, true);
      const channels = v.getUint16(body + 2, true);
      const sampleRate = v.getUint32(body + 4, true);
      const blockAlign = v.getUint16(body + 12, true);
      const bits = v.getUint16(body + 14, true);
      if (format === FMT_EXTENSIBLE && size >= 40) {
        // SubFormat GUID: first two bytes carry the real format code.
        format = v.getUint16(body + 24, true);
      }
      fmt = { format, channels, sampleRate, bits, blockAlign };
    } else if (id === "data") {
      dataStart = body;
      // Some writers leave size 0/0xFFFFFFFF when streaming: clamp to file.
      dataLen = Math.min(size, v.byteLength - body);
      if (size === 0 || size === 0xffffffff) dataLen = v.byteLength - body;
      break;
    }
    pos = body + size + (size % 2);
  }
  if (!fmt) throw new Error("WAV sin chunk fmt");
  if (dataStart < 0) throw new Error("WAV sin chunk data");
  const { format, channels: nch, sampleRate, bits } = fmt;
  if (nch < 1) throw new Error("WAV sin canales");
  const bytes = bits / 8;
  const blockAlign = fmt.blockAlign || nch * bytes;
  const frames = Math.floor(dataLen / blockAlign);
  const out = Array.from({ length: nch }, () => new Float32Array(frames));

  if (format === FMT_FLOAT && bits === 32) {
    if (dataStart % 4 === 0 && blockAlign === nch * 4) {
      // fast path: aligned interleaved float32 view
      const inter = new Float32Array(buf, dataStart, frames * nch);
      for (let c = 0; c < nch; c++) {
        const ch = out[c];
        for (let i = 0, j = c; i < frames; i++, j += nch) ch[i] = inter[j];
      }
    } else {
      for (let i = 0; i < frames; i++)
        for (let c = 0; c < nch; c++) out[c][i] = v.getFloat32(dataStart + i * blockAlign + c * 4, true);
    }
  } else if (format === FMT_FLOAT && bits === 64) {
    for (let i = 0; i < frames; i++)
      for (let c = 0; c < nch; c++) out[c][i] = v.getFloat64(dataStart + i * blockAlign + c * 8, true);
  } else if (format === FMT_PCM) {
    const u8 = new Uint8Array(buf, dataStart, frames * blockAlign);
    for (let c = 0; c < nch; c++) {
      const ch = out[c];
      let o = c * bytes;
      if (bits === 16) {
        for (let i = 0; i < frames; i++, o += blockAlign) {
          const n = u8[o] | (u8[o + 1] << 8);
          ch[i] = (n > 32767 ? n - 65536 : n) / 32768;
        }
      } else if (bits === 24) {
        for (let i = 0; i < frames; i++, o += blockAlign) {
          const n = u8[o] | (u8[o + 1] << 8) | (u8[o + 2] << 16);
          ch[i] = (n & 0x800000 ? n - 0x1000000 : n) / 8388608;
        }
      } else if (bits === 32) {
        for (let i = 0; i < frames; i++, o += blockAlign) {
          ch[i] = v.getInt32(dataStart + o, true) / 2147483648;
        }
      } else if (bits === 8) {
        for (let i = 0; i < frames; i++, o += blockAlign) ch[i] = (u8[o] - 128) / 128;
      } else {
        throw new Error(`WAV PCM de ${bits} bits no soportado`);
      }
    }
  } else {
    throw new Error(`Formato WAV ${format}/${bits} bits no soportado`);
  }
  return { sampleRate, channels: out, frames, format: format === FMT_FLOAT ? "float" : "pcm", bitsPerSample: bits };
}
