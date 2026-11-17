// JSON Schema validators over the repo's generated contracts (schemas/),
// shared by the mock server and the unit tests. The pydantic-exported
// schemas use draft 2020-12 `$defs`; their OpenAPI-style `discriminator`
// (with `mapping`) is not supported by ajv, but the `oneOf` beside it with
// `const` type tags already validates the same thing, so it is ignored.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const Ajv2020 = require("ajv/dist/2020").default;
const addFormats = require("ajv-formats");

export const SCHEMAS_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "schemas");

export function loadSchema(rel) {
  return JSON.parse(fs.readFileSync(path.join(SCHEMAS_DIR, rel), "utf8"));
}

export function makeAjv() {
  const ajv = new Ajv2020({ allErrors: true, strict: false });
  ajv.addKeyword("discriminator");
  addFormats(ajv);
  return ajv;
}

/** Compile a schema file; `omitRequired` drops fields the HTTP API injects. */
export function compile(ajv, rel, omitRequired = []) {
  const s = loadSchema(rel);
  if (omitRequired.length && Array.isArray(s.required)) s.required = s.required.filter((k) => !omitRequired.includes(k));
  return ajv.compile(s);
}
