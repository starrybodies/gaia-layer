/**
 * @gaia/service — the one place query logic lives.
 *
 * The MCP server and the REST API are both thin adapters over this module. If a behaviour
 * exists in one transport and not the other, it is in the wrong place.
 */

export * from "./errors.js";
export * from "./geometry.js";
export * from "./stats.js";
export * from "./rows.js";
export * from "./trends.js";
export * from "./summary.js";
export { claimIdFor } from "./claims.js";
export { close, isPopulated, lakePath } from "./db.js";
export * from "./tools.js";
