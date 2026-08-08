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
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { ServiceError, lakePath } from "@gaia/service";
import { TOOL_DEFINITIONS, callTool } from "./tools.js";

const server = new Server(
  { name: "gaia-ecological-layer", version: "0.1.0" },
  { capabilities: { tools: {} } },
);

server.setRequestHandler(ListToolsRequestSchema, () => ({ tools: TOOL_DEFINITIONS }));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  try {
    const result = await callTool(name, args ?? {});
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
  await server.connect(new StdioServerTransport());
  console.error("[gaia-mcp] ready");
}

await main();
