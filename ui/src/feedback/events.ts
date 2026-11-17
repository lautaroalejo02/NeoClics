// Builders for feedback.jsonl events (schemas/feedback.schema.json).
// Every builder returns a schema-valid object (tested against the JSON
// Schema with ajv) or throws — the UI never sends an event the API would
// reject. `ts` is always timezone-aware ISO-8601 UTC ("...Z").

import type {
  ABChoiceEvent,
  ChopAdjust,
  ChopChoiceEvent,
  CommentEvent,
  FeedbackEvent,
  RatingEvent,
  VersionId,
} from "../api/types";

const VERSION_RE = /^v\d{3,}$/;
const ID_RE = /^[a-z][a-z0-9_-]{0,63}$/;
const CANDIDATE_RE = /^cs\d{3,}\/cand\d+$/;

export function nowIso(now: Date = new Date()): string {
  return now.toISOString();
}

function assertVersion(v: string, field: string): void {
  if (!VERSION_RE.test(v)) throw new Error(`${field}: versión inválida "${v}"`);
}

export function rating(version: VersionId, value: "like" | "dislike", now?: Date): RatingEvent {
  assertVersion(version, "version");
  return { ts: nowIso(now), type: "rating", version, value };
}

export function comment(
  opts: { text: string; version?: VersionId | null; atS?: number | null; track?: string | null },
  now?: Date,
): CommentEvent {
  const text = opts.text.trim();
  if (!text) throw new Error("El comentario está vacío");
  const e: CommentEvent = { ts: nowIso(now), type: "comment", text };
  if (opts.version != null) {
    assertVersion(opts.version, "version");
    e.version = opts.version;
  }
  if (opts.atS != null && Number.isFinite(opts.atS)) {
    e.at_s = Math.round(Math.max(0, opts.atS) * 1000) / 1000;
  }
  if (opts.track != null) {
    if (!ID_RE.test(opts.track)) throw new Error(`track inválido "${opts.track}"`);
    e.track = opts.track;
  }
  return e;
}

export function abChoice(a: VersionId, b: VersionId, winner: VersionId, now?: Date): ABChoiceEvent {
  assertVersion(a, "a");
  assertVersion(b, "b");
  if (winner !== a && winner !== b) throw new Error("winner tiene que ser a o b");
  return { ts: nowIso(now), type: "ab_choice", a, b, winner };
}

export function chopChoice(
  opts: { candidateId: string; version?: VersionId | null; adjust?: ChopAdjust | null },
  now?: Date,
): ChopChoiceEvent {
  if (!CANDIDATE_RE.test(opts.candidateId)) {
    throw new Error(`candidate_id inválido "${opts.candidateId}" (esperaba csNNN/candN)`);
  }
  const e: ChopChoiceEvent = { ts: nowIso(now), type: "chop_choice", candidate_id: opts.candidateId };
  if (opts.version != null) {
    assertVersion(opts.version, "version");
    e.version = opts.version;
  }
  if (opts.adjust != null) e.adjust = { start_beats: opts.adjust.start_beats, length_beats: opts.adjust.length_beats };
  return e;
}

/** Stable identity for de-duplicating events echoed back over the websocket. */
export function eventKey(e: FeedbackEvent): string {
  switch (e.type) {
    case "rating":
      return `rating|${e.ts}|${e.version}|${e.value}`;
    case "comment":
      return `comment|${e.ts}|${e.version ?? ""}|${e.at_s ?? ""}|${e.text}`;
    case "ab_choice":
      return `ab|${e.ts}|${e.a}|${e.b}|${e.winner}`;
    case "chop_choice":
      return `chop|${e.ts}|${e.candidate_id}`;
  }
}

/** Global candidate id from a set + a candidate (local or global id). */
export function globalCandidateId(setId: string, candId: string): string {
  return candId.includes("/") ? candId : `${setId}/${candId}`;
}
