/**
 * Compile the exported JSON Schemas into Zod schemas.
 *
 * This script is the second half of the one-schema-source rule. Pydantic is the source;
 * `make schema` exports it to `docs/schema`; this turns that into Zod under
 * `src/generated`. Nothing in `src/generated` is ever hand-edited — CI regenerates and
 * fails on drift.
 */
import { mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import $RefParser from "@apidevtools/json-schema-ref-parser";
import { jsonSchemaToZod } from "json-schema-to-zod";
import prettier from "prettier";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..");
const schemaDir = join(repoRoot, "docs", "schema");
const outDir = join(here, "..", "src", "generated");

const HEADER = `// GENERATED FILE — DO NOT EDIT.
// Source: pipeline/src/gaia_pipeline/schemas (Pydantic) -> docs/schema (JSON Schema) -> here.
// Regenerate with \`make schema\`.
`;

/** `EcologicalStateResponse` -> `ecological-state-response` */
function toKebab(name: string): string {
  return name.replace(/([a-z0-9])([A-Z])/g, "$1-$2").toLowerCase();
}

async function main(): Promise<void> {
  const indexRaw = await readFile(join(schemaDir, "index.json"), "utf8");
  const index = JSON.parse(indexRaw) as Record<string, string>;
  const modelNames = Object.keys(index).sort();

  await rm(outDir, { recursive: true, force: true });
  await mkdir(outDir, { recursive: true });

  const prettierOptions = (await prettier.resolveConfig(outDir)) ?? {};

  for (const model of modelNames) {
    const fileName = index[model];
    if (fileName === undefined) continue;
    const raw = JSON.parse(await readFile(join(schemaDir, fileName), "utf8")) as object;

    // json-schema-to-zod emits `z.any()` for any `$ref` it is handed, which would erase
    // every nested type — provenance steps, methods, validation status. Dereferencing
    // first inlines the `$defs` so the compiler sees real shapes. The schemas are acyclic,
    // so full inlining terminates.
    const schema = (await $RefParser.dereference(structuredClone(raw))) as object;
    delete (schema as { $defs?: unknown }).$defs;

    const body = jsonSchemaToZod(schema, {
      module: "esm",
      name: `${model}Schema`,
      type: model,
    });

    const source = await prettier.format(`${HEADER}\n${body}`, {
      ...prettierOptions,
      parser: "typescript",
    });
    await writeFile(join(outDir, `${toKebab(model)}.ts`), source, "utf8");
  }

  const barrel = [
    HEADER,
    ...modelNames.map((model) => `export * from "./${toKebab(model)}.js";`),
    "",
  ].join("\n");
  await writeFile(
    join(outDir, "index.ts"),
    await prettier.format(barrel, { ...prettierOptions, parser: "typescript" }),
    "utf8",
  );

  const written = await readdir(outDir);
  console.log(`generated ${written.length} files in core/src/generated`);
}

await main();
