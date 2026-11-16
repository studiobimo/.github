// Run by self-lint with `node --test`. Covers every rule of the decision table, their order, and
// how the pull request is resolved, against a stand-in for the GitHub API (a replaced global
// fetch, which both this action and the changed-paths helper it reuses call). self-ci-policy
// makes the real calls on a pull request here.
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, test } from "node:test";
import { decide, inspect, report } from "./ci-policy.mjs";

const REPO = "studiobimo/example";
const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

const rawPr = ({
  number = 7,
  title = "feat: a thing",
  head = "feat/b",
  base = "main",
  draft = false,
  labels = [],
  headRepo = REPO,
} = {}) => ({
  number,
  title,
  user: { login: "octo" },
  draft,
  labels: labels.map((name) => ({ name })),
  head: {
    ref: head,
    sha: "h".repeat(40),
    repo: headRepo === null ? null : { full_name: headRepo },
  },
  base: { ref: base, sha: "b".repeat(40) },
});

// Writes the event payload and returns the env the action would see.
const eventEnv = ({ event = "pull_request", pr, ref = "feat/b", ...extra } = {}) => {
  const path = join(mkdtempSync(join(tmpdir(), "ci-policy-")), "event.json");
  writeFileSync(path, JSON.stringify({ pull_request: pr, repository: { default_branch: "main" } }));
  return {
    GITHUB_EVENT_NAME: event,
    GITHUB_EVENT_PATH: path,
    GITHUB_REPOSITORY: REPO,
    GITHUB_REF_NAME: ref,
    GITHUB_API_URL: "https://api.example.test",
    TOKEN: "t",
    PATHS: "",
    INTEGRATION_BRANCHES: "main\ndevelop",
    FULL_BRANCHES: "main",
    FULL_LABEL: "ci:full",
    FULL: "false",
    ...extra,
  };
};

// A stand-in for the API. `open` holds open pull requests as raw objects, `files` the compare
// result, and `fail` a substring of a path that answers HTTP 500. Records every path called.
const fakeApi = ({ open = [], files = [], fail, calls = [] } = {}) => {
  globalThis.fetch = async (input) => {
    const url = new URL(input);
    calls.push(`${url.pathname}${url.search}`);
    const respond = (body, status = 200) => new Response(JSON.stringify(body), { status });
    if (fail && url.pathname.includes(fail)) return respond({}, 500);
    if (url.pathname.includes("/compare/"))
      return respond({ files: files.map((filename) => ({ filename })) });
    const { searchParams } = url;
    const head = searchParams.get("head");
    const base = searchParams.get("base");
    return respond(
      open.filter((pr) => (head ? `studiobimo:${pr.head.ref}` === head : pr.base.ref === base)),
    );
  };
  return calls;
};

const run = async (env, api = {}) => {
  fakeApi(api);
  const inspected = await inspect({ env });
  return { inspected, ...decide(inspected), ...report(inspected) };
};

// A pull request summary for the pure decision table.
const pr = (over = {}) => ({
  number: 1,
  title: "t",
  author: "octo",
  base_ref: "main",
  base_sha: "b",
  head_ref: "feat/x",
  head_sha: "h",
  draft: false,
  labels: [],
  ...over,
});
const middle = { position: "middle", intermediate: true };

test("rule 1: no pull request validates the full suite", () => {
  assert.deepEqual(decide({ pr: null, fullBranches: ["main"] }), {
    validate: true,
    affectedBase: "",
    reason: "not a pull request, so it validates the full suite",
  });
});

test("rule 2: full forces the full suite over draft, middle and paths", () => {
  const result = decide({
    pr: pr({ draft: true, base_ref: "develop" }),
    forceFull: true,
    patterns: ["src/**"],
    files: ["README.md"],
    stack: middle,
  });
  assert.deepEqual([result.validate, result.affectedBase], [true, ""]);
  assert.match(result.reason, /full run was requested/);
});

test("rule 3: a fork validates the full suite, even a draft with no matching path", () => {
  const result = decide({
    pr: pr({ draft: true }),
    fork: true,
    patterns: ["src/**"],
    files: ["README.md"],
  });
  assert.deepEqual([result.validate, result.affectedBase], [true, ""]);
  assert.match(result.reason, /fork/);
});

test("rule 4: a pull request that touches none of the paths is not applicable", () => {
  const result = decide({ pr: pr(), patterns: ["src/**"], files: ["README.md"] });
  assert.equal(result.validate, false);
  assert.match(result.reason, /^not applicable/);
});

test("rule 4: a touched path, an empty list, a failed or truncated compare all continue", () => {
  const base = { pr: pr({ base_ref: "develop" }), patterns: ["src/**"] };
  assert.equal(decide({ ...base, files: ["src/a.go"] }).validate, true);
  assert.equal(decide({ pr: pr(), files: ["README.md"] }).validate, true);
  assert.equal(decide({ ...base, files: [], compareError: "HTTP 500" }).validate, true);
  assert.equal(decide({ ...base, files: [], truncated: true }).validate, true);
});

test("rule 5: the label forces the full suite over a draft and the middle of a stack", () => {
  const result = decide({
    pr: pr({ draft: true, labels: ["ci:full"], base_ref: "develop" }),
    fullLabel: "ci:full",
    stack: middle,
  });
  assert.deepEqual([result.validate, result.affectedBase], [true, ""]);
  assert.match(result.reason, /ci:full/);
});

test("rule 4 beats rule 5: not applicable wins over the label", () => {
  const result = decide({
    pr: pr({ labels: ["ci:full"] }),
    fullLabel: "ci:full",
    patterns: ["src/**"],
    files: ["README.md"],
  });
  assert.equal(result.validate, false);
  assert.match(result.reason, /^not applicable/);
});

test("an empty label name never matches", () => {
  const result = decide({ pr: pr({ labels: [""], base_ref: "develop" }), fullLabel: "" });
  assert.equal(result.affectedBase, "develop");
});

test("rule 6: a draft is deferred", () => {
  const result = decide({ pr: pr({ draft: true }), fullBranches: ["main"] });
  assert.deepEqual([result.validate, result.reason], [false, "deferred: draft"]);
});

test("rule 6 beats rule 7: a draft in the middle reports the draft", () => {
  const result = decide({ pr: pr({ draft: true }), stack: middle });
  assert.equal(result.reason, "deferred: draft");
});

test("rule 7: the middle of a stack is deferred, other positions are not", () => {
  const deferred = decide({ pr: pr({ base_ref: "feat/a" }), stack: middle });
  assert.deepEqual([deferred.validate, deferred.reason], [false, "deferred: middle of a stack"]);
  for (const position of ["single", "bottom", "top", "none", "unknown"]) {
    const result = decide({
      pr: pr({ base_ref: "feat/a" }),
      stack: { position, intermediate: false },
    });
    assert.equal(result.validate, true, position);
  }
});

test("rule 7 beats rule 8: the middle of a stack into a full branch is deferred", () => {
  const result = decide({ pr: pr(), stack: middle, fullBranches: ["main"] });
  assert.equal(result.validate, false);
});

test("rule 8: a pull request into a full branch validates the full suite", () => {
  const result = decide({ pr: pr({ base_ref: "main" }), fullBranches: ["main"] });
  assert.deepEqual([result.validate, result.affectedBase], [true, ""]);
});

test("rule 9: otherwise it narrows to the base branch", () => {
  const result = decide({ pr: pr({ base_ref: "develop" }), fullBranches: ["main"] });
  assert.deepEqual([result.validate, result.affectedBase], [true, "develop"]);
});

test("a pull_request event reads the payload, labels included", async () => {
  const labels = ["ci:full", "other"];
  const out = await run(eventEnv({ pr: rawPr({ base: "develop", labels }) }));
  assert.deepEqual([out.validate, out.affectedBase], [true, ""]);
  assert.deepEqual(JSON.parse(out.outputs["pull-request"]), {
    number: 7,
    title: "feat: a thing",
    author: "octo",
    base_ref: "develop",
    base_sha: "b".repeat(40),
    head_ref: "feat/b",
    head_sha: "h".repeat(40),
    draft: false,
    labels,
  });
});

test("a ready pull request into develop narrows to develop", async () => {
  const out = await run(eventEnv({ pr: rawPr({ base: "develop" }) }));
  assert.equal(out.outputs.validate, "true");
  assert.equal(out.outputs["affected-base"], "develop");
  assert.equal(out.outputs.position, "single");
});

test("a pull request into main runs the full suite, and into develop is a bottom", async () => {
  const out = await run(eventEnv({ pr: rawPr({ base: "main" }) }));
  assert.equal(out.outputs["affected-base"], "");
  const open = [
    rawPr({ head: "develop", base: "main" }),
    rawPr({ head: "feat/c", base: "feat/b" }),
  ];
  const stacked = await run(eventEnv({ pr: rawPr({ base: "develop" }) }), { open });
  assert.equal(stacked.outputs.position, "bottom");
  assert.equal(stacked.outputs.validate, "true");
});

test("the middle of a stack is deferred end to end", async () => {
  const open = [rawPr({ head: "feat/a", base: "main" }), rawPr({ head: "feat/c", base: "feat/b" })];
  const out = await run(eventEnv({ pr: rawPr({ base: "feat/a" }) }), { open });
  assert.equal(out.outputs.position, "middle");
  assert.equal(out.outputs.validate, "false");
  assert.equal(out.outputs["affected-base"], "");
});

test("paths: an untouched list defers, a touched one validates", async () => {
  const env = (PATHS) => eventEnv({ pr: rawPr({ base: "develop" }), PATHS });
  const miss = await run(env("src/**"), { files: ["README.md"] });
  assert.equal(miss.outputs.validate, "false");
  const hit = await run(env("src/**"), { files: ["src/a.go"] });
  assert.equal(hit.outputs.validate, "true");
});

test("a fork validates the full suite without any API call", async () => {
  for (const headRepo of ["someone/example", null]) {
    const calls = [];
    const env = eventEnv({ pr: rawPr({ headRepo, draft: true }), PATHS: "src/**" });
    const out = await run(env, { calls });
    assert.deepEqual([out.outputs.validate, out.outputs["affected-base"]], ["true", ""]);
    assert.equal(out.outputs.position, "none");
    assert.equal(calls.length, 0);
  }
});

test("full=true skips the lookups and runs the full suite", async () => {
  const calls = [];
  const env = eventEnv({ pr: rawPr({ draft: true }), FULL: "true", PATHS: "src/**" });
  const out = await run(env, { calls });
  assert.deepEqual([out.outputs.validate, out.outputs["affected-base"]], ["true", ""]);
  assert.equal(calls.length, 0);
});

test("a push has no pull request", async () => {
  const calls = [];
  const out = await run(eventEnv({ event: "push" }), { calls });
  assert.deepEqual(
    [out.outputs.validate, out.outputs["affected-base"], out.outputs["pull-request"]],
    ["true", "", ""],
  );
  assert.equal(out.outputs.position, "none");
  assert.equal(calls.length, 0);
});

test("workflow_dispatch resolves the open pull request for the branch", async () => {
  const calls = [];
  const open = [rawPr({ number: 42, head: "feat/b", base: "develop", labels: ["x"] })];
  const env = eventEnv({ event: "workflow_dispatch", ref: "feat/b" });
  const out = await run(env, { open, calls });
  assert.ok(
    calls.some((call) => call.includes("head=studiobimo%3Afeat%2Fb")),
    calls.join("\n"),
  );
  assert.equal(JSON.parse(out.outputs["pull-request"]).number, 42);
  assert.deepEqual([out.outputs.validate, out.outputs["affected-base"]], ["true", "develop"]);
});

test("workflow_dispatch with a draft pull request defers", async () => {
  const open = [rawPr({ head: "feat/b", base: "develop", draft: true })];
  const out = await run(eventEnv({ event: "workflow_dispatch", ref: "feat/b" }), { open });
  assert.equal(out.outputs.validate, "false");
});

test("workflow_dispatch with no pull request behaves like a push", async () => {
  const out = await run(eventEnv({ event: "workflow_dispatch", ref: "feat/none" }), { open: [] });
  assert.deepEqual(
    [out.outputs.validate, out.outputs["affected-base"], out.outputs["pull-request"]],
    ["true", "", ""],
  );
  assert.equal(out.level, "notice");
});

test("a failed pull request lookup on dispatch fails open with a warning", async () => {
  const out = await run(eventEnv({ event: "workflow_dispatch" }), { fail: "/pulls" });
  assert.deepEqual([out.outputs.validate, out.outputs["affected-base"]], ["true", ""]);
  assert.equal(out.level, "warning");
  assert.match(out.outputs.reason, /HTTP 500/);
});

test("an unreadable event fails open", async () => {
  const env = { ...eventEnv({ pr: rawPr() }), GITHUB_EVENT_PATH: "/does/not/exist.json" };
  const out = await run(env);
  assert.deepEqual([out.outputs.validate, out.outputs["affected-base"]], ["true", ""]);
  assert.equal(out.level, "warning");
});

test("a failed compare fails open to applicable", async () => {
  const env = eventEnv({ pr: rawPr({ base: "develop" }), PATHS: "src/**" });
  const out = await run(env, { fail: "/compare/", files: [] });
  assert.equal(out.outputs.validate, "true");
  assert.equal(out.outputs["affected-base"], "develop");
  assert.equal(out.level, "warning");
});

test("a failed stack lookup is not the middle of a stack", async () => {
  const out = await run(eventEnv({ pr: rawPr({ base: "feat/a" }) }), { fail: "/pulls" });
  assert.equal(out.outputs.validate, "true");
  assert.equal(out.outputs.position, "unknown");
  assert.equal(out.level, "warning");
});

test("a title or branch with a newline cannot reach the reason or the notice", async () => {
  const evil = "x\n::error::injected\r\nsecond";
  const env = eventEnv({
    pr: rawPr({ title: evil, base: evil, head: evil, labels: [evil] }),
    FULL_LABEL: evil,
    FULL_BRANCHES: "main",
  });
  for (const [label, { outputs, message }] of [
    ["label", report(await inspectWith(env))],
    ["base", report(await inspectWith({ ...env, FULL_LABEL: "ci:full" }))],
  ]) {
    assert.doesNotMatch(outputs.reason, /[\r\n]/, label);
    assert.doesNotMatch(message, /[\r\n]/, label);
  }
});

// The reason names the base ref and the label, so cover each as the user-controlled string.
async function inspectWith(env) {
  fakeApi();
  return inspect({ env });
}

test("the reason line also survives a newline in a fork-free full branch match", () => {
  const result = decide({
    pr: pr({ base_ref: "a\nb" }),
    fullBranches: ["a\nb"],
  });
  assert.doesNotMatch(result.reason, /[\r\n]/);
});

test("the pull-request output is one line of JSON even for a title with a newline", async () => {
  const out = await run(eventEnv({ pr: rawPr({ title: "a\nb" }) }));
  const text = out.outputs["pull-request"];
  assert.doesNotMatch(text, /[\r\n]/);
  assert.equal(JSON.parse(text).title, "a\nb");
});

test("the script writes outputs in the delimiter form and a clean notice line", async () => {
  const dir = mkdtempSync(join(tmpdir(), "ci-policy-run-"));
  const outputFile = join(dir, "output");
  writeFileSync(outputFile, "");
  const env = eventEnv({ pr: rawPr({ title: "a\nb", base: "develop" }), FULL: "true" });
  const { spawnSync } = await import("node:child_process");
  const script = new URL("./ci-policy.mjs", import.meta.url).pathname;
  const child = spawnSync(process.execPath, [script], {
    env: { ...env, GITHUB_OUTPUT: outputFile, PATH: process.env.PATH },
    encoding: "utf8",
  });
  // FULL=true skips every lookup, so the script never needs the network.
  assert.equal(child.status, 0, child.stderr);
  assert.match(child.stdout, /^::(notice|warning) title=ci-policy::validate=true/);
  const written = readFileSync(outputFile, "utf8");
  assert.match(written, /^validate<<ghadelim_[0-9a-f]+\ntrue\nghadelim_[0-9a-f]+\n/);
  assert.match(written, /pull-request<<ghadelim_[0-9a-f]+\n\{"number":7/);
});
