#!/usr/bin/env node
/**
 * Gaia ecological intelligence layer — MCP server.
 *
 * The primary consumer of this layer is an agent, so this is the primary interface. It
 * speaks stdio and binds its tools to `@gaia/service`; the REST API in `../api` binds the
 * same functions over HTTP.
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListResourcesRequestSchema,
  ListToolsRequestSchema,
  ReadResourceRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import {
  EII_TOOL_NAMES,
  RESOURCE_DEFINITIONS,
  ServiceError,
  archiveDir,
  lakePath,
  readResource,
} from "@gaia/service";
import { TOOL_DEFINITIONS, callTool } from "./tools.js";
import { EII_TOOL_DEFINITIONS, callEii } from "./eii-tools.js";

const server = new Server(
  { name: "gaia-ecological-layer", version: "0.2.0" },
  { capabilities: { tools: {}, resources: {} } },
);

// Two surfaces, one server. v0.1's tools measure a 20 m projected grid over the coastal
// pilot; the EII tools measure H3 hexes over the interior. They are listed together because
// an agent should be able to see both and choose, and they are dispatched apart because
// nothing in either should be able to answer for the other.
server.setRequestHandler(ListToolsRequestSchema, () => ({
  tools: [...TOOL_DEFINITIONS, ...EII_TOOL_DEFINITIONS],
}));

server.setRequestHandler(ListResourcesRequestSchema, () => ({
  resources: RESOURCE_DEFINITIONS.map((resource) => ({ ...resource })),
}));

server.setRequestHandler(ReadResourceRequestSchema, async (request) => {
  const resource = await readResource(request.params.uri);
  return {
    contents: [{ uri: resource.uri, mimeType: resource.mimeType, text: resource.text }],
  };
});

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  try {
    const isEii = (EII_TOOL_NAMES as readonly string[]).includes(name);
    const result = isEii ? await callEii(name, args ?? {}) : await callTool(name, args ?? {});
    return {
      content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }],
    };
  } catch (error) {
    const payload =
      error instanceof ServiceError
        ? error.toResponse()
        : {
            error: "internal",
            message: error instanceof Error ? error.message : String(error),
            retryable: true,
            generated_at: new Date().toISOString(),
          };
    return {
      content: [{ type: "text" as const, text: JSON.stringify(payload, null, 2) }],
      isError: true,
    };
  }
});

async function main(): Promise<void> {
  // stdout belongs to the protocol; diagnostics go to stderr.
  console.error(`[gaia-mcp] lake: ${lakePath()}`);
  console.error(`[gaia-mcp] eii archive: ${archiveDir()}`);
  await server.connect(new StdioServerTransport());
  console.error("[gaia-mcp] ready");
}

await main();
