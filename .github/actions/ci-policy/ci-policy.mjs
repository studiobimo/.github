// Decides whether a pull request runs the heavy validation job, and how much of it, for the
// ci-policy composite action. A cheap baseline (secret scan, workflow lint, title and size checks)
// runs on every push; this guard gates only the expensive job, at job level, so a deferred run
// allocates no runner and bills nothing. It composes two guards: pr-stack (is this the middle of
// a stack?) and changed-paths (does the pull request touch anything validation cares about?).
//
// It needs `contents: read` and `pull-requests: read`, with no checkout. Every uncertain case (no
// pull request, a failed lookup, a truncated file list) answers "validate, full suite", so the
// guard can only skip work once it has seen the pull request and proven the skip is safe.
import { randomBytes } from "node:crypto";
import { appendFileSync, readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import {
  compareFiles,
  decide as decidePaths,
  parsePatterns,
} from "../changed-paths/changed-paths.mjs";
import { decide as decideStack, findNeighbours } from "../pr-stack/pr-stack.mjs";

const PULL_REQUEST_EVENTS = ["pull_request", "pull_request_target"];
const API_VERSION = "2022-11-28";

// Quotes a user-controlled string (title, branch, label) so a newline cannot end the line it is
// printed on, and so it cannot inject a workflow command.
const quote = (text) => JSON.stringify(String(text));

/** Reduces a pull request from the event payload or the pulls API to what the policy reports. */
export const summarise = (pr) => ({
  number: pr.number,
  title: pr.title ?? "",
  author: pr.user?.login ?? "",
  base_ref: pr.base.ref,
  base_sha: pr.base.sha,
  head_ref: pr.head.ref,
  head_sha: pr.head.sha,
  draft: Boolean(pr.draft),
  labels: (pr.labels ?? []).map((label) => label.name),
});

const full = (reason) => ({ validate: true, affectedBase: "", reason });
const skip = (reason) => ({ validate: false, affectedBase: "", reason });

// What the path rule found, or null when it does not apply. A failed compare is not "nothing
// changed": it falls through as applicable.
const pathsVerdict = ({ patterns = [], files = [], truncated = false, compareError }) =>
  patterns.length && !compareError
    ? decidePaths({ event: "pull_request", patterns, files, truncated })
    : null;

// Ordered: the first rule whose `when` holds decides, so an earlier rule outranks a later one.
const RULES = [
  {
    when: ({ pr }) => !pr,
    result: () => full("not a pull request, so it validates the full suite"),
  },
  { when: ({ forceFull }) => forceFull, result: () => full("a full run was requested") },
  {
    when: ({ fork }) => fork,
    result: () => full("a pull request from a fork validates the full suite"),
  },
  {
    when: (input) => pathsVerdict(input)?.changed === false,
    result: (input) => skip(`not applicable: ${pathsVerdict(input).reason}`),
  },
  {
    when: ({ pr, fullLabel }) => fullLabel && pr.labels.includes(fullLabel),
    result: ({ fullLabel }) => full(`the ${quote(fullLabel)} label requests the full suite`),
  },
  { when: ({ pr }) => pr.draft, result: () => skip("deferred: draft") },
  {
    when: ({ stack }) => stack?.intermediate,
    result: () => skip("deferred: middle of a stack"),
  },
  {
    when: ({ pr, fullBranches = [] }) => fullBranches.includes(pr.base_ref),
    result: ({ pr }) => full(`it targets ${quote(pr.base_ref)}, which validates the full suite`),
  },
  {
    when: () => true,
    result: ({ pr }) => ({
      validate: true,
      affectedBase: pr.base_ref,
      reason: `it validates what changed against ${quote(pr.base_ref)}`,
    }),
  },
];

/**
 * Returns `{ validate, affectedBase, reason }`; the first matching rule wins. `pr` is a
 * `summarise`d pull request or null. `patterns`, `files`, `truncated` and `compareError` feed the
 * path rule, and `stack` is the pr-stack decision (`{ position, intermediate }`) or null.
 */
export const decide = (input) => RULES.find(({ when }) => when(input)).result(input);

const defaultFetchJson = async ({ api, token, path, query }) => {
  const url = `${api}${path}${query ? `?${new URLSearchParams(query)}` : ""}`;
  const response = await fetch(url, {
    headers: {
      accept: "application/vnd.github+json",
      authorization: `Bearer ${token}`,
      "x-github-api-version": API_VERSION,
    },
  });
  if (!response.ok) throw new Error(`GET ${path} failed: HTTP ${response.status}`);
  return response.json();
};

/**
 * Resolves the pull request and gathers what `decide` needs. Never throws: a failed lookup is
 * recorded in `notes` and fails open. `fetchJson({ api, token, path, query })` is injectable so
 * tests need no network.
 */
export const inspect = async ({ env, fetchJson = defaultFetchJson }) => {
  const event = env.GITHUB_EVENT_NAME ?? "";
  const repository = env.GITHUB_REPOSITORY;
  const api = env.GITHUB_API_URL;
  const token = env.TOKEN;
  const get = (path, query) => fetchJson({ api, token, path, query });
  const openPulls = async (query) => {
    const pulls = await get(`/repos/${repository}/pulls`, {
      ...query,
      state: "open",
      per_page: "1",
    });
    if (!Array.isArray(pulls)) throw new Error("listing pull requests returned no list");
    return pulls;
  };
  const input = {
    patterns: parsePatterns(env.PATHS),
    fullBranches: parsePatterns(env.FULL_BRANCHES),
    fullLabel: (env.FULL_LABEL ?? "").trim(),
    forceFull: env.FULL === "true",
    notes: [],
  };

  let raw;
  let defaultBranch;
  try {
    if (PULL_REQUEST_EVENTS.includes(event) || event === "workflow_dispatch") {
      const payload = JSON.parse(readFileSync(env.GITHUB_EVENT_PATH, "utf8"));
      defaultBranch = payload.repository?.default_branch;
      if (event === "workflow_dispatch") {
        const owner = repository.split("/")[0];
        [raw] = await openPulls({ head: `${owner}:${env.GITHUB_REF_NAME}` });
      } else {
        raw = payload.pull_request;
      }
    }
  } catch (error) {
    input.notes.push(`the pull request lookup failed: ${error.message}`);
  }
  if (!raw) return { ...input, pr: null, stack: null };

  const pr = summarise(raw);
  // `head.repo` is null once a fork is deleted; that is still not this repository.
  const fork = raw.head.repo?.full_name !== repository;
  const result = { ...input, pr, fork };
  if (fork || input.forceFull) return { ...result, stack: null };

  if (input.patterns.length) {
    try {
      Object.assign(
        result,
        await compareFiles({ api, repository, base: pr.base_sha, head: pr.head_sha, token }),
      );
    } catch (error) {
      result.compareError = error.message;
      result.notes.push(`the compare failed, so validation applies: ${error.message}`);
    }
  }

  try {
    const neighbours = await findNeighbours({
      pr: { headRef: pr.head_ref, baseRef: pr.base_ref },
      repository,
      defaultBranch,
      integrationBranches: parsePatterns(env.INTEGRATION_BRANCHES),
      lookup: async ({ query }) => (await openPulls(query)).length > 0,
    });
    result.stack = decideStack({ event: "pull_request", ...neighbours });
  } catch (error) {
    result.stack = decideStack({ event: "pull_request", error: error.message });
    result.notes.push(`the stack lookup failed: ${error.message}`);
  }
  return result;
};

// Workflow commands end at a newline and treat `%` as an escape, so neutralise both.
const escapeCommand = (text) =>
  text.replace(/%/g, "%25").replace(/\r/g, "%0D").replace(/\n/g, "%0A");

// Appends each output in the delimiter form, so a value can never forge another output.
const writeOutputs = (file, outputs) => {
  for (const [name, value] of Object.entries(outputs)) {
    const delimiter = `ghadelim_${randomBytes(8).toString("hex")}`;
    appendFileSync(file, `${name}<<${delimiter}\n${value}\n${delimiter}\n`);
  }
};

/** The outputs and the notice line for an inspected pull request. */
export const report = (inspected) => {
  const { validate, affectedBase, reason } = decide(inspected);
  const notes = inspected.notes ?? [];
  const line = [reason, ...notes].join("; ").replace(/\s*[\r\n]+\s*/g, " ");
  return {
    outputs: {
      validate: String(validate),
      "affected-base": affectedBase,
      reason: line,
      position: inspected.stack?.position ?? "none",
      "pull-request": inspected.pr ? JSON.stringify(inspected.pr) : "",
    },
    level: notes.length ? "warning" : "notice",
    message: `validate=${validate} affected-base=${quote(affectedBase)} (${line})`,
  };
};

const main = async () => {
  const { env } = process;
  const { outputs, level, message } = report(await inspect({ env }));
  console.log(`::${level} title=ci-policy::${escapeCommand(message)}`);
  writeOutputs(env.GITHUB_OUTPUT, outputs);
};

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  main().catch((error) => {
    // Unreachable in practice, since inspect never throws; fail open all the same.
    console.log(`::warning title=ci-policy::${escapeCommand(error.message)}`);
    writeOutputs(process.env.GITHUB_OUTPUT, {
      validate: "true",
      "affected-base": "",
      reason: "the policy failed, so it validates the full suite",
      position: "unknown",
      "pull-request": "",
    });
  });
}
