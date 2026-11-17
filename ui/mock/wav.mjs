// Minimal WAV decode/encode for the mock server.
// Supports fmt 1 (PCM 16/24/32-bit int) and fmt 3 (float32/float64),
// any channel count. Samples are returned/accepted as planar Float32Array
// per channel in [-1, 1].

/**
 * @param {Buffer} buf
 * @returns {{sampleRate:number, channels:number, frames:number, data:Float32Array[]}}
 */
export function decodeWav(buf) {
  if (buf.toString("ascii", 0, 4) !== "RIFF" || buf.toString("ascii", 8, 12) !== "WAVE") {
    throw new Error("not a RIFF/WAVE file");
  }
  let pos = 12;
  let fmt = null;
  let data = null;
  while (pos + 8 <= buf.length) {
    const id = buf.toString("ascii", pos, pos + 4);
    const size = buf.readUInt32LE(pos + 4);
    const body = pos + 8;
    if (id === "fmt ") {
      fmt = {
        audioFormat: buf.readUInt16LE(body),
        channels: buf.readUInt16LE(body + 2),
        sampleRate: buf.readUInt32LE(body + 4),
        bits: buf.readUInt16LE(body + 14),
      };
    } else if (id === "data") {
      data = buf.subarray(body, body + size);
    }
    pos = body + size + (size % 2);
  }
  if (!fmt || !data) throw new Error("missing fmt or data chunk");

  const bytesPer = fmt.bits / 8;
  const frames = Math.floor(data.length / (bytesPer * fmt.channels));
  const channels = Array.from({ length: fmt.channels }, () => new Float32Array(frames));

  for (let f = 0; f < frames; f++) {
    for (let c = 0; c < fmt.channels; c++) {
      const off = (f * fmt.channels + c) * bytesPer;
      let v;
      if (fmt.audioFormat === 3) {
        v = fmt.bits === 64 ? data.readDoubleLE(off) : data.readFloatLE(off);
      } else if (fmt.bits === 16) {
        v = data.readInt16LE(off) / 32768;
      } else if (fmt.bits === 24) {
        const b0 = data[off], b1 = data[off + 1], b2 = data[off + 2];
        let n = b0 | (b1 << 8) | (b2 << 16);
        if (n & 0x800000) n -= 0x1000000;
        v = n / 8388608;
      } else if (fmt.bits === 32) {
        v = data.readInt32LE(off) / 2147483648;
      } else {
        throw new Error(`unsupported fmt ${fmt.audioFormat}/${fmt.bits}bit`);
      }
      channels[c][f] = v;
    }
  }
  return { sampleRate: fmt.sampleRate, channels: fmt.channels, frames, data: channels };
}

/**
 * @param {{sampleRate:number, data:Float32Array[]}} wav
 * @param {"pcm24"|"float32"} format
 */
export function encodeWav(wav, format = "float32") {
  const channels = wav.data.length;
  const frames = wav.data[0]?.length ?? 0;
  const isFloat = format === "float32";
  const bits = isFloat ? 32 : 24;
  const bytesPer = bits / 8;
  const blockAlign = channels * bytesPer;
  const dataSize = frames * blockAlign;

  const buf = Buffer.alloc(44 + dataSize);
  buf.write("RIFF", 0);
  buf.writeUInt32LE(36 + dataSize, 4);
  buf.write("WAVE", 8);
  buf.write("fmt ", 12);
  buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(isFloat ? 3 : 1, 20);
  buf.writeUInt16LE(channels, 22);
  buf.writeUInt32LE(wav.sampleRate, 24);
  buf.writeUInt32LE(wav.sampleRate * blockAlign, 28);
  buf.writeUInt16LE(blockAlign, 32);
  buf.writeUInt16LE(bits, 34);
  buf.write("data", 36);
  buf.writeUInt32LE(dataSize, 40);

  for (let f = 0; f < frames; f++) {
    for (let c = 0; c < channels; c++) {
      const v = Math.max(-1, Math.min(1, wav.data[c][f]));
      const off = 44 + (f * channels + c) * bytesPer;
      if (isFloat) {
        buf.writeFloatLE(v, off);
      } else {
        let n = Math.round(v * 8388608);
        if (n < 0) n += 0x1000000;
        buf[off] = n & 0xff;
        buf[off + 1] = (n >> 8) & 0xff;
        buf[off + 2] = (n >> 16) & 0xff;
      }
    }
  }
  return buf;
}

// ---------- simple DSP used to derive mock versions ----------

export function gain(wav, db) {
  const g = 10 ** (db / 20);
  for (const ch of wav.data) for (let i = 0; i < ch.length; i++) ch[i] *= g;
  return wav;
}

/** One-pole lowpass per channel (in place). cutoffHz at wav.sampleRate. */
export function lowpass(wav, cutoffHz) {
  const rc = 1 / (2 * Math.PI * cutoffHz);
  const dt = 1 / wav.sampleRate;
  const a = dt / (rc + dt);
  for (const ch of wav.data) {
    let prev = 0;
    for (let i = 0; i < ch.length; i++) {
      prev += a * (ch[i] - prev);
      ch[i] = prev;
    }
  }
  return wav;
}

/** One-pole highpass (in place). */
export function highpass(wav, cutoffHz) {
  const lp = wav.data.map((ch) => Float32Array.from(ch));
  lowpass({ sampleRate: wav.sampleRate, data: lp }, cutoffHz);
  for (let c = 0; c < wav.data.length; c++) {
    const ch = wav.data[c], l = lp[c];
    for (let i = 0; i < ch.length; i++) ch[i] = ch[i] - l[i];
  }
  return wav;
}

/** Soft saturation (in place). drive 1 = none. */
export function saturate(wav, drive) {
  const k = Math.max(1, drive);
  const norm = Math.tanh(k);
  for (const ch of wav.data) for (let i = 0; i < ch.length; i++) ch[i] = Math.tanh(ch[i] * k) / norm;
  return wav;
}

/** Sum several wavs channel-wise into the first (all same length assumed). */
export function sumInto(dst, src, srcGain = 1) {
  for (let c = 0; c < dst.data.length; c++) {
    const d = dst.data[c], s = src.data[c];
    for (let i = 0; i < d.length; i++) d[i] += s[i] * srcGain;
  }
  return dst;
}

export function clone(wav) {
  return { sampleRate: wav.sampleRate, channels: wav.channels, frames: wav.frames, data: wav.data.map((c) => Float32Array.from(c)) };
}

/** Compute min/max peaks: peaks[ch][i] = [min,max] for bucket i. */
export function peaks(wav, buckets = 800) {
  const per = Math.max(1, Math.floor(wav.frames / buckets));
  const n = Math.ceil(wav.frames / per);
  return wav.data.map((ch) => {
    const out = new Array(n);
    for (let i = 0; i < n; i++) {
      let mn = Infinity, mx = -Infinity;
      const start = i * per, end = Math.min(ch.length, start + per);
      for (let j = start; j < end; j++) {
        const v = ch[j];
        if (v < mn) mn = v;
        if (v > mx) mx = v;
      }
      out[i] = [mn === Infinity ? 0 : mn, mx === -Infinity ? 0 : mx];
    }
    return out;
  });
}
