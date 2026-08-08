import type { NextConfig } from "next";
import { resolve } from "node:path";

const config: NextConfig = {
  // The workspace root holds lockfiles for sibling projects; pin it so Turbopack does not
  // walk up and pick the wrong one.
  turbopack: { root: resolve(import.meta.dirname, "..") },
  transpilePackages: ["@gaia/core"],
};

export default config;
