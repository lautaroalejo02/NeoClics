// Mixer state (per-track mute/solo/volume, per-lane mute/solo) and the pure
// rules that turn it into gains. Global by track id, so a muted "drums"
// stays muted when you flip A/B — you compare like with like.

export interface MixerTrack {
  mute: boolean;
  solo: boolean;
  gainDb: number;
}

export interface MixerLane {
  mute: boolean;
  solo: boolean;
}

export interface MixerState {
  tracks: Record<string, MixerTrack>;
  /** trackId -> laneId -> control. Lane solo is scoped to its track. */
  lanes: Record<string, Record<string, MixerLane>>;
}

export const EMPTY_MIXER: MixerState = { tracks: {}, lanes: {} };

export const DEFAULT_TRACK: MixerTrack = { mute: false, solo: false, gainDb: 0 };
export const DEFAULT_LANE: MixerLane = { mute: false, solo: false };

export function trackCtl(m: MixerState, id: string): MixerTrack {
  return m.tracks[id] ?? DEFAULT_TRACK;
}

export function laneCtl(m: MixerState, trackId: string, laneId: string): MixerLane {
  return m.lanes[trackId]?.[laneId] ?? DEFAULT_LANE;
}

function trackIsDefault(t: MixerTrack): boolean {
  return !t.mute && !t.solo && Math.abs(t.gainDb) < 0.05;
}

/** Any lane of this track muted/soloed → the track plays its lane stems. */
export function trackUsesLanes(m: MixerState, trackId: string): boolean {
  const lanes = m.lanes[trackId];
  if (!lanes) return false;
  return Object.values(lanes).some((l) => l.mute || l.solo);
}

/** Nothing touched → play the post-master mix.wav (what the agent rendered). */
export function isMixerDefault(m: MixerState): boolean {
  return (
    Object.values(m.tracks).every(trackIsDefault) &&
    Object.keys(m.lanes).every((t) => !trackUsesLanes(m, t))
  );
}

/** Linear fader gain for a track after mute/solo rules. */
export function trackGain(m: MixerState, trackId: string): number {
  const anySolo = Object.values(m.tracks).some((t) => t.solo);
  const t = trackCtl(m, trackId);
  const audible = !t.mute && (!anySolo || t.solo);
  return audible ? 10 ** (t.gainDb / 20) : 0;
}

/** 0/1 gate for a lane after lane mute/solo rules within its track. */
export function laneGain(m: MixerState, trackId: string, laneId: string): number {
  const lanes = m.lanes[trackId] ?? {};
  const anySolo = Object.values(lanes).some((l) => l.solo);
  const l = laneCtl(m, trackId, laneId);
  return !l.mute && (!anySolo || l.solo) ? 1 : 0;
}

export function patchTrack(m: MixerState, id: string, patch: Partial<MixerTrack>): MixerState {
  return { ...m, tracks: { ...m.tracks, [id]: { ...trackCtl(m, id), ...patch } } };
}

export function patchLane(m: MixerState, trackId: string, laneId: string, patch: Partial<MixerLane>): MixerState {
  return {
    ...m,
    lanes: {
      ...m.lanes,
      [trackId]: { ...(m.lanes[trackId] ?? {}), [laneId]: { ...laneCtl(m, trackId, laneId), ...patch } },
    },
  };
}
