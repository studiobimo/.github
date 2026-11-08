// Run by self-lint with `node --test`. Covers the decision logic only; the compare API call is
// what a pull request in a consumer exercises.
import assert from "node:assert/strict";
import { test } from "node:test";
import { decide, matches, parsePatterns, toRegExp } from "./changed-paths.mjs";

test("parsePatterns drops blank lines and comments", () => {
  assert.deepEqual(parsePatterns("a/**\n\n# note\n  b/*.go  \n"), ["a/**", "b/*.go"]);
  assert.deepEqual(parsePatterns(""), []);
});

test("* stops at a slash, ** crosses directories", () => {
  assert.equal(matches("docs/a.md", ["docs/*"]), true);
  assert.equal(matches("docs/nested/a.md", ["docs/*"]), false);
  assert.equal(matches("docs/nested/a.md", ["docs/**"]), true);
});

test("a leading **/ also matches files at the root", () => {
  assert.equal(matches("main.go", ["**/*.go"]), true);
  assert.equal(matches("cmd/main.go", ["**/*.go"]), true);
  assert.equal(matches("main.rs", ["**/*.go"]), false);
});

test("? makes the preceding character optional, as in GitHub's cheat sheet", () => {
  assert.equal(matches("app.js", ["*.jsx?"]), true);
  assert.equal(matches("app.jsx", ["*.jsx?"]), true);
  assert.equal(matches("app.jsxx", ["*.jsx?"]), false);
  assert.equal(toRegExp("v?.txt").test("v1.txt"), false);
});

test("+ repeats the preceding character", () => {
  assert.equal(matches("ve.txt", ["ve+.txt"]), true);
  assert.equal(matches("vee.txt", ["ve+.txt"]), true);
  assert.equal(matches("v.txt", ["ve+.txt"]), false);
});

test("[] is a character class", () => {
  assert.equal(matches("v1.txt", ["v[0-9].txt"]), true);
  assert.equal(matches("vx.txt", ["v[0-9].txt"]), false);
  assert.equal(matches("b.md", ["[abc].md"]), true);
});

test("a quantifier with nothing to apply to is literal", () => {
  assert.equal(matches("+x", ["+x"]), true);
  assert.equal(matches("a?", ["*?"]), true);
  assert.equal(matches("a", ["*?"]), false);
});

test("other regex metacharacters in a glob are literal", () => {
  assert.equal(matches("a.b(1)$.txt", ["a.b(1)$.txt"]), true);
  assert.equal(matches("aab1.txt", ["a.b(1).txt"]), false);
});

test("a later ! pattern excludes an earlier match", () => {
  const patterns = ["src/**", "!src/generated/**"];
  assert.equal(matches("src/app.ts", patterns), true);
  assert.equal(matches("src/generated/api.ts", patterns), false);
});

test("a pull request that touches a matching path is changed", () => {
  const result = decide({
    event: "pull_request",
    patterns: ["**/*.go"],
    files: ["README.md", "cmd/main.go"],
  });
  assert.equal(result.changed, true);
  assert.match(result.reason, /"cmd\/main.go" matches/);
});

test("a pull request that touches no matching path is not changed", () => {
  const result = decide({
    event: "pull_request",
    patterns: ["**/*.go"],
    files: ["README.md", "docs/guide.md"],
  });
  assert.deepEqual(result, { changed: false, reason: "none of 2 changed files match" });
});

test("an empty pattern list always answers changed", () => {
  assert.equal(decide({ event: "pull_request", patterns: [], files: [] }).changed, true);
});

test("non-pull-request events always answer changed", () => {
  for (const event of ["push", "workflow_dispatch", "schedule", "merge_group"]) {
    assert.equal(decide({ event, patterns: ["**/*.go"], files: [] }).changed, true, event);
  }
});

test("pull_request_target is compared like pull_request", () => {
  assert.equal(
    decide({ event: "pull_request_target", patterns: ["a/**"], files: ["b/x"] }).changed,
    false,
  );
});

test("a truncated file list answers changed, since the match may be missing", () => {
  const result = decide({
    event: "pull_request",
    patterns: ["**/*.go"],
    files: ["README.md"],
    truncated: true,
  });
  assert.equal(result.changed, true);
});

test("a file name is quoted in the reason, so a newline cannot start a new log command", () => {
  const { reason } = decide({
    event: "pull_request",
    patterns: ["*.go"],
    files: ["evil\n::set-output name=x::y.go"],
  });
  assert.equal(reason.includes("\n"), false);
});
