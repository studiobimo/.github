#!/usr/bin/env node
// Lints one message with commitlint, against @commitlint/config-conventional plus the
// rules of the repository it runs in.
//
// Usage, from the root of the repository being checked:
//   lint.mjs [--label <what>] <message>
//
// Only the `rules` of the repository's .commitlintrc.yaml are read. Its `extends`,
// `plugins` and `parserPreset` can each name a JavaScript file, and on a pull request
// that file is the author's: commitlint is given a config built here and run from an
// empty directory, so nothing from the pull request is ever executed. A repository
// that extends config-conventional itself loses nothing, since that is the base here.
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "yaml";

const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
let label = "commit message";
if (args[0] === "--label") {
  label = args[1];
  args.splice(0, 2);
}
const message = args[0] ?? "";

// By path, because commitlint runs from an empty directory with nothing to resolve from.
const config = {
  extends: [fileURLToPath(import.meta.resolve("@commitlint/config-conventional"))],
  rules: {},
};
const own = [".commitlintrc.yaml", ".commitlintrc.yml"].find((file) => existsSync(file));
if (own) {
  const rules = parse(readFileSync(own, "utf8"))?.rules ?? {};
  if (typeof rules !== "object" || Array.isArray(rules)) {
    console.error(`✖ ${own}: \`rules\` has to be a mapping of rule name to setting`);
    process.exit(2);
  }
  config.rules = rules;
}

const work = mkdtempSync(join(tmpdir(), "commitlint-"));
let result;
try {
  const file = join(work, "commitlint.json");
  writeFileSync(file, JSON.stringify(config));
  result = spawnSync(
    process.execPath,
    [join(here, "node_modules/@commitlint/cli/cli.js"), "--config", file],
    { cwd: work, input: `${message}\n`, encoding: "utf8" },
  );
} finally {
  rmSync(work, { recursive: true, force: true });
}

if (result.error) {
  console.error(`✖ cannot run commitlint: ${result.error.message}. Was \`npm ci\` run in ${here}?`);
  process.exit(2);
}
if (result.status !== 0) {
  console.error(`✖ Not a valid ${label}${own ? ` (rules: ${own})` : ""}:\n`);
  console.error(`${result.stdout}${result.stderr}`.trimEnd());
  process.exit(result.status === 1 ? 1 : 2);
}
