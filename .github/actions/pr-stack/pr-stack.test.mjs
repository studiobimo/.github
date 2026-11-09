// Run by self-lint with `node --test`. Covers the decision logic and how the event is read, with
// a stand-in for the pulls API; self-pr-stack makes the real calls on a pull request here.
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { decide, inspect } from "./pr-stack.mjs";

const REPO = "studiobimo/example";

// Writes a pull_request event payload and returns the env the action would see.
const eventEnv = ({
  head = "feat/b",
  base = "feat/a",
  headRepo = REPO,
  event = "pull_request",
}) => {
  const path = join(mkdtempSync(join(tmpdir(), "pr-stack-")), "event.json");
  const pull_request = {
    head: { ref: head, repo: headRepo === null ? null : { full_name: headRepo } },
    base: { ref: base },
  };
  writeFileSync(path, JSON.stringify({ pull_request, repository: { default_branch: "main" } }));
  return {
    GITHUB_EVENT_NAME: event,
    GITHUB_EVENT_PATH: path,
    GITHUB_REPOSITORY: REPO,
    GITHUB_API_URL: "https://api.example.test",
    TOKEN: "t",
  };
};

// A stand-in for the pulls API: `open` holds the open pull requests as { head, base }.
const fakeLookup =
  (open, calls = []) =>
  async ({ query }) => {
    calls.push(query);
    return open.some((pr) =>
      query.head ? `studiobimo:${pr.head}` === query.head : pr.base === query.base,
    );
  };

test("only the middle of a stack is intermediate", () => {
  const cases = [
    [{ hasParent: false, hasChild: false }, "single", false],
    [{ hasParent: false, hasChild: true }, "bottom", false],
    [{ hasParent: true, hasChild: true }, "middle", true],
    [{ hasParent: true, hasChild: false }, "top", false],
  ];
  for (const [neighbours, position, intermediate] of cases) {
    const result = decide({ event: "pull_request", ...neighbours });
    assert.equal(result.position, position);
    assert.equal(result.intermediate, intermediate, position);
  }
});

test("non-pull-request events always run", () => {
  for (const event of ["push", "workflow_dispatch", "schedule", "merge_group"]) {
    const result = decide({ event, hasParent: true, hasChild: true });
    assert.deepEqual([result.position, result.intermediate], ["none", false], event);
  }
});

test("pull_request_target is judged like pull_request", () => {
  const result = decide({ event: "pull_request_target", hasParent: true, hasChild: true });
  assert.equal(result.intermediate, true);
});

test("a fork always runs", () => {
  const result = decide({ event: "pull_request", fork: true, hasParent: true, hasChild: true });
  assert.deepEqual([result.position, result.intermediate], ["none", false]);
});

test("a failed lookup fails open", () => {
  const result = decide({
    event: "pull_request",
    error: "HTTP 403",
    hasParent: true,
    hasChild: true,
  });
  assert.deepEqual([result.position, result.intermediate], ["unknown", false]);
  assert.match(result.reason, /HTTP 403/);
});

test("inspect finds both neighbours of a middle pull request", async () => {
  const open = [
    { head: "feat/a", base: "main" },
    { head: "feat/b", base: "feat/a" },
    { head: "feat/c", base: "feat/b" },
  ];
  const calls = [];
  const found = await inspect({ env: eventEnv({}), lookup: fakeLookup(open, calls) });
  assert.deepEqual(found, { event: "pull_request", hasParent: true, hasChild: true });
  assert.deepEqual(calls, [{ head: "studiobimo:feat/a" }, { base: "feat/b" }]);
  assert.equal(decide(found).intermediate, true);
});

test("a pull request into the default branch has no parent, even with main as a head", async () => {
  const open = [
    { head: "main", base: "release" },
    { head: "feat/a", base: "main" },
    { head: "feat/b", base: "feat/a" },
  ];
  const calls = [];
  const env = eventEnv({ head: "feat/a", base: "main" });
  const found = await inspect({ env, lookup: fakeLookup(open, calls) });
  assert.deepEqual([found.hasParent, found.hasChild], [false, true]);
  assert.deepEqual(calls, [{ base: "feat/a" }]);
  assert.equal(decide(found).position, "bottom");
});

test("inspect skips the lookup for a fork, including a deleted one", async () => {
  for (const headRepo of ["someone/example", null]) {
    const calls = [];
    const found = await inspect({ env: eventEnv({ headRepo }), lookup: fakeLookup([], calls) });
    assert.deepEqual(found, { event: "pull_request", fork: true });
    assert.equal(calls.length, 0);
  }
});

test("inspect skips the lookup for a push", async () => {
  const calls = [];
  const found = await inspect({ env: eventEnv({ event: "push" }), lookup: fakeLookup([], calls) });
  assert.deepEqual(found, { event: "push" });
  assert.equal(calls.length, 0);
});

test("inspect reports an API failure instead of throwing", async () => {
  const lookup = async () => {
    throw new Error("listing pull requests failed: HTTP 500");
  };
  const found = await inspect({ env: eventEnv({}), lookup });
  assert.match(found.error, /HTTP 500/);
  assert.equal(decide(found).intermediate, false);
});

test("inspect reports an unreadable event instead of throwing", async () => {
  const env = { ...eventEnv({}), GITHUB_EVENT_PATH: "/does/not/exist.json" };
  const found = await inspect({ env, lookup: fakeLookup([]) });
  assert.ok(found.error);
  assert.equal(decide(found).position, "unknown");
});
