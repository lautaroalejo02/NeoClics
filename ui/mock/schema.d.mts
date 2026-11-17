import type { ValidateFunction } from "ajv";
export const SCHEMAS_DIR: string;
export function loadSchema(rel: string): Record<string, unknown>;
export function makeAjv(): import("ajv").default;
export function compile(ajv: import("ajv").default, rel: string, omitRequired?: string[]): ValidateFunction;
