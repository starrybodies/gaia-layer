import type { NextConfig } from "next";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");

const config: NextConfig = {
  // The workspace root holds lockfiles for sibling projects; pin it so Turbopack does not
  // walk up and pick the wrong one.
  turbopack: { root },

  // The workspace packages are already compiled to plain ESM by tsc, so they need no
  // transpiling — and listing them here would pull DuckDB's native binding into the bundle
  // graph regardless of it being marked external.

  // DuckDB ships a native binding, and its loader references every platform package by
  // name so the bundler tries to resolve all of them. They stay external and are required
  // at runtime from node_modules; only the one matching the host is actually installed.
  serverExternalPackages: [
    "@duckdb/node-api",
    "@duckdb/node-bindings",
    "@duckdb/node-bindings-darwin-arm64",
    "@duckdb/node-bindings-darwin-x64",
    "@duckdb/node-bindings-linux-arm64",
    "@duckdb/node-bindings-linux-arm64-musl",
    "@duckdb/node-bindings-linux-x64",
    "@duckdb/node-bindings-linux-x64-musl",
    "@duckdb/node-bindings-win32-arm64",
    "@duckdb/node-bindings-win32-x64",
  ],

  /**
   * Keep every DuckDB package out of the server bundle.
   *
   * `serverExternalPackages` above covers imports that arrive through node_modules, but the
   * service reaches DuckDB through a workspace symlink, which the bundler treats as first-
   * party source and follows. DuckDB's loader then names all nine platform bindings, only
   * one of which is installed, and the build fails on the other eight.
   *
   * Externalising by prefix leaves the loader to require the right one at runtime, which is
   * what it was written to do.
   */
  webpack: (config, { isServer }) => {
    if (isServer) {
      const externals = Array.isArray(config.externals) ? config.externals : [config.externals];
      config.externals = [
        ...externals.filter(Boolean),
        (
          { request }: { request?: string },
          callback: (error?: unknown, result?: string) => void,
        ) => {
          if (request !== undefined && request.startsWith("@duckdb/")) {
            callback(undefined, `commonjs ${request}`);
            return;
          }
          callback();
        },
      ];
    }
    return config;
  },

  // The data lake travels with the deployment. It is read-only at serve time and 47 MB, so
  // shipping it inside the function is simpler and faster than fronting it with storage.
  outputFileTracingRoot: root,
  outputFileTracingIncludes: {
    "/api/v1/**": ["../data/gaia.duckdb"],
    "/report": ["../data/gaia.duckdb"],
    "/": ["../data/gaia.duckdb"],
  },
};

export default config;
