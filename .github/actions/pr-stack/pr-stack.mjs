// Decides where a pull request sits in a stack, for the pr-stack composite action. A stack is a
// chain of pull requests where each one's base is the head branch of the one below it. Only the
// middle of a stack is reported as intermediate: the top holds the cumulative tree, and the
// bottom is the one the rulesets gate and the one that merges first, so both keep running.
//
// It reads two lists from the pulls API and needs `pull-requests: read`, with no checkout. Every
// uncertain case (a non-pull-request event, a fork, a failed lookup) answers
// `intermediate=false`, so the guard can only skip work once it has seen both neighbours.
import { appendFileSync, readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const PULL_REQUEST_EVENTS = ["pull_request", "pull_request_target"];
const API_VERSION = "2022-11-28";

/**
 * Returns `{ position, intermediate, reason }`. `position` is `single`, `bottom`, `middle` or
 * `top` for a pull request in this repository, and `none` or `unknown` when the guard does not
 * apply or could not tell. `hasParent` means an open pull request's head is this one's base;
 * `hasChild` means an open pull request's base is this one's head.
 */
export const decide = ({ event, fork = false, error, hasParent = false, hasChild = false }) => {
  const runs = (position, reason) => ({ position, intermediate: false, reason });
  if (!PULL_REQUEST_EVENTS.includes(event)) return runs("none", `${event} always runs`);
  // A fork's head branch lives in another repository, so no pull request here can be based on it.
  if (fork) return runs("none", "a pull request from a fork always runs");
  if (error) return runs("unknown", `the stack lookup failed, so it runs: ${error}`);
  if (hasParent && hasChild)
    return { position: "middle", intermediate: true, reason: "a pull request is stacked on it" };
  if (hasChild) return runs("bottom", "the bottom of a stack always runs");
  if (hasParent) return runs("top", "the top of a stack always runs");
  return runs("single", "not part of a stack");
};

// Whether any open pull request matches the query. One result is enough to answer.
const anyOpen = async ({ api, repository, token, query }) => {
  const params = new URLSearchParams({ ...query, state: "open", per_page: "1" });
  const response = await fetch(`${api}/repos/${repository}/pulls?${params}`, {
    headers: {
      accept: "application/vnd.github+json",
      authorization: `Bearer ${token}`,
      "x-github-api-version": API_VERSION,
    },
  });
  if (!response.ok) throw new Error(`listing pull requests failed: HTTP ${response.status}`);
  const pulls = await response.json();
  if (!Array.isArray(pulls)) throw new Error("listing pull requests returned no list");
  return pulls.length > 0;
};

/** Reads the event and looks up both neighbours. Never throws: a failure is reported in `error`. */
export const inspect = async ({ env, lookup = anyOpen }) => {
  const event = env.GITHUB_EVENT_NAME ?? "";
  if (!PULL_REQUEST_EVENTS.includes(event)) return { event };
  try {
    const payload = JSON.parse(readFileSync(env.GITHUB_EVENT_PATH, "utf8"));
    const pr = payload.pull_request;
    const repository = env.GITHUB_REPOSITORY;
    // `head.repo` is null once a fork is deleted; that is still not this repository.
    if (pr.head.repo?.full_name !== repository) return { event, fork: true };
    const owner = repository.split("/")[0];
    const common = { api: env.GITHUB_API_URL, repository, token: env.TOKEN };
    // A pull request into the default branch is the bottom by definition, since that is the
    // branch the rulesets gate; an open `develop` -> `main` pull request does not make it a
    // middle one.
    const intoDefault = pr.base.ref === payload.repository?.default_branch;
    const [hasParent, hasChild] = await Promise.all([
      intoDefault ? false : lookup({ ...common, query: { head: `${owner}:${pr.base.ref}` } }),
      lookup({ ...common, query: { base: pr.head.ref } }),
    ]);
    return { event, hasParent, hasChild };
  } catch (error) {
    return { event, error: error.message };
  }
};

const main = async () => {
  const { env } = process;
  const { position, intermediate, reason } = decide(await inspect({ env }));
  const level = position === "unknown" ? "warning" : "notice";
  console.log(`::${level} title=pr-stack::position=${position} (${reason})`);
  appendFileSync(env.GITHUB_OUTPUT, `position=${position}\nintermediate=${intermediate}\n`);
};

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  main().catch((error) => {
    // Unreachable in practice, since inspect never throws; fail open all the same.
    console.log(`::warning title=pr-stack::${error.message}`);
    appendFileSync(process.env.GITHUB_OUTPUT, "position=unknown\nintermediate=false\n");
  });
}
