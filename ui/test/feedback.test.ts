import { describe, expect, it } from "vitest";
import { compile, makeAjv } from "../mock/schema.mjs";
import * as fb from "../src/feedback/events";

// The repo's generated JSON Schemas (schemas/) are the contract.
const ajv = makeAjv();
const validate = compile(ajv, "feedback.schema.json");
const appendParams = compile(ajv, "tools/feedback.append.params.schema.json", ["project"]);
const chooseParams = compile(ajv, "tools/chops.choose.params.schema.json", ["project"]);

function expectValid(e: unknown) {
  const ok = validate(e);
  expect(ok, ajv.errorsText(validate.errors)).toBe(true);
  expect(appendParams({ event: e }), ajv.errorsText(appendParams.errors)).toBe(true);
}

describe("feedback event builders", () => {
  const now = new Date("2026-11-18T21:06:10.123Z");

  it("rating", () => {
    const e = fb.rating("v002", "like", now);
    expect(e).toEqual({ ts: "2026-11-18T21:06:10.123Z", type: "rating", version: "v002", value: "like" });
    expectValid(e);
    expectValid(fb.rating("v1000", "dislike"));
  });

  it("comment pinned to a time and a track", () => {
    const e = fb.comment({ text: "  acá el bombo se come al sample ", version: "v002", atS: 12.00049, track: "drums" }, now);
    expect(e).toEqual({ ts: now.toISOString(), type: "comment", text: "acá el bombo se come al sample", version: "v002", at_s: 12, track: "drums" });
    expectValid(e);
  });

  it("general comment omits optional fields instead of sending nulls", () => {
    const e = fb.comment({ text: "en general va mejor" });
    expect(Object.keys(e).sort()).toEqual(["text", "ts", "type"]);
    expectValid(e);
  });

  it("rejects what the schema would reject", () => {
    expect(() => fb.comment({ text: "   " })).toThrow();
    expect(() => fb.comment({ text: "x", track: "Drums!" })).toThrow();
    expect(() => fb.rating("2", "like")).toThrow();
    expect(() => fb.abChoice("v001", "v002", "v003")).toThrow();
    expect(() => fb.chopChoice({ candidateId: "cand2" })).toThrow();
    // negative times clamp to 0 (schema: at_s >= 0)
    expectValid(fb.comment({ text: "x", atS: -0.3 }));
  });

  it("ab_choice", () => {
    const e = fb.abChoice("v001", "v002", "v002", now);
    expectValid(e);
    expect(e.winner).toBe("v002");
  });

  it("chop_choice with an adjust", () => {
    const e = fb.chopChoice({ candidateId: "cs001/cand2", version: "v003", adjust: { start_beats: -1, length_beats: 2 } }, now);
    expectValid(e);
  });

  it("timestamps are timezone-aware (contract R6)", () => {
    expect(fb.rating("v001", "like").ts).toMatch(/Z$/);
  });

  it("global candidate ids accept local and global forms", () => {
    expect(fb.globalCandidateId("cs001", "cand2")).toBe("cs001/cand2");
    expect(fb.globalCandidateId("cs001", "cs001/cand2")).toBe("cs001/cand2");
  });

  it("the client's chops.choose params match the tool schema", () => {
    const params = { candidate_id: "cs001/cand2", chop_id: "c1", mode: "manual", adjust: { start_beats: 1, length_beats: 0 } };
    expect(chooseParams(params), ajv.errorsText(chooseParams.errors)).toBe(true);
  });

  it("event keys de-duplicate websocket echoes", () => {
    const e = fb.comment({ text: "hola", version: "v001", atS: 1 }, now);
    expect(fb.eventKey(e)).toBe(fb.eventKey(JSON.parse(JSON.stringify(e))));
    expect(fb.eventKey(e)).not.toBe(fb.eventKey({ ...e, text: "chau" }));
  });
});
