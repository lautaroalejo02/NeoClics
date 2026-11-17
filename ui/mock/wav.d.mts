export interface MockWav { sampleRate: number; channels: number; frames: number; data: Float32Array[] }
export function decodeWav(buf: Uint8Array): MockWav;
export function encodeWav(wav: { sampleRate: number; data: Float32Array[] }, format?: "pcm24" | "float32"): Uint8Array;
