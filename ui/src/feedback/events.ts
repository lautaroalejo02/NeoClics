// Builders for feedback.jsonl events (schemas/feedback.schema.json).
// `ts` is always a timezone-aware ISO-8601 string (UTC "Z").

import type {
  ABChoiceEvent,
  ChopAdjust,
  ChopChoiceEvent,
  CommentEvent,
  RatingEvent,
  VersionId,
} from "../api/types";

export function nowIso(): string {
  return new Date().toISOString(); // always UTC, always offset-tagged
}

export function rating(version: VersionId, value: "like" | "dislike"): RatingEvent {
  return { ts: nowIso(), type: "rating", version, value };
}

export function comment(opts: {
  text: string;
  version?: VersionId | null;
  atS?: number | null;
  track?: string | null;
}): CommentEvent {
  const e: CommentEvent = { ts: nowIso(), type: "comment", text: opts.text };
  if (opts.version != null) e.version = opts.version;
  if (opts.atS != null) e.at_s = Math.max(0, opts.atS);
  if (opts.track != null) e.track = opts.track;
  return e;
}

export function abChoice(a: VersionId, b: VersionId, winner: VersionId): ABChoiceEvent {
  if (winner !== a && winner !== b) {
    throw new Error("winner must equal a or b");
  }
  return { ts: nowIso(), type: "ab_choice", a, b, winner };
}

export function chopChoice(opts: {
  candidateId: string; // global "csNNN/candN"
  version?: VersionId | null;
  adjust?: ChopAdjust | null;
}): ChopChoiceEvent {
  const e: ChopChoiceEvent = {
    ts: nowIso(),
    type: "chop_choice",
    candidate_id: opts.candidateId,
  };
  if (opts.version != null) e.version = opts.version;
  if (opts.adjust != null) e.adjust = opts.adjust;
  return e;
}
