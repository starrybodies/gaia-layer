/**
 * @gaia/core — the shared contract.
 *
 * Every type crossing the language boundary is generated from the Pydantic schemas in
 * `pipeline/src/gaia_pipeline/schemas`. Nothing in this package redefines a data shape by
 * hand; if a shape is wrong, fix it in Python and run `make schema`.
 */

export * from "./generated/index.js";
export * from "./guards.js";
export * from "./constants.js";
