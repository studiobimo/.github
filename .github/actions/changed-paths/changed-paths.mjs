// Decides whether a pull request touches any of a list of path globs, for the changed-paths
// composite action. It reads the changed files from the compare API (base...head, so only the
// pull request's own changes count) and needs nothing but `contents: read`, with no checkout.
// Every uncertain case (a non-pull-request event, no patterns, a truncated file list) answers
// `changed=true`, so the guard can only skip work once it has proven the pull request does not
// touch the paths.
import { appendFileSync, readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

// Events whose payload carries a base and head to compare. Everything else always runs.
const PULL_REQUEST_EVENTS = ["pull_request", "pull_request_target"];
// The compare API lists at most this many files per response; a list this long may be incomplete.
const MAX_COMPARE_FILES = 300;
const API_VERSION = "2022-11-28";

/** Newline-separated globs; blank lines and `#` comments are ignored. */
export const parsePatterns = (text = "") =>
  text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"));

// Converts a GitHub `paths:`-style glob to an anchored RegExp: `*` and `?` stop at `/`, `**`
// crosses directories, and a `**/` prefix also matches files at the root.
export const toRegExp = (glob) => {
  let source = "";
  for (let i = 0; i < glob.length; i++) {
    const char = glob[i];
    if (char === "*" && glob[i + 1] === "*") {
      if (glob[i + 2] === "/") {
        source += "(?:.*/)?";
        i += 2;
      } else {
        source += ".*";
        i += 1;
      }
    } else if (char === "*") source += "[^/]*";
    else if (char === "?") source += "[^/]";
    else source += char.replace(/[.+^${}()|[\]\\]/g, "\\$&");
  }
  return new RegExp(`^${source}$`);
};

/** Like GitHub `paths:`: patterns apply in order, and a later `!pattern` excludes a match. */
export const matches = (file, patterns) => {
  let included = false;
  for (const pattern of patterns) {
    const negated = pattern.startsWith("!");
    if (toRegExp(negated ? pattern.slice(1) : pattern).test(file)) included = !negated;
  }
  return included;
};

/** Returns `{ changed, reason }`. `files` holds the pull request's changed paths, old and new names. */
export const decide = ({ event, patterns, files = [], truncated = false }) => {
  if (!patterns.length) return { changed: true, reason: "no paths configured" };
  if (!PULL_REQUEST_EVENTS.includes(event))
    return { changed: true, reason: `${event} always runs` };
  if (truncated) return { changed: true, reason: "the changed-file list may be incomplete" };
  const hit = files.find((file) => matches(file, patterns));
  return hit
    ? { changed: true, reason: `${JSON.stringify(hit)} matches` }
    : { changed: false, reason: `none of ${files.length} changed files match` };
};

const compareFiles = async ({ api, repository, base, head, token }) => {
  const response = await fetch(`${api}/repos/${repository}/compare/${base}...${head}`, {
    headers: {
      accept: "application/vnd.github+json",
      authorization: `Bearer ${token}`,
      "x-github-api-version": API_VERSION,
    },
  });
  if (!response.ok) {
    throw new Error(`compare ${base}...${head} failed: HTTP ${response.status}`);
  }
  const { files = [] } = await response.json();
  return {
    files: files.flatMap((file) => [file.filename, file.previous_filename].filter(Boolean)),
    truncated: files.length >= MAX_COMPARE_FILES,
  };
};

const main = async () => {
  const { env } = process;
  const event = env.GITHUB_EVENT_NAME ?? "";
  const patterns = parsePatterns(env.PATHS);
  let compared = { files: [], truncated: false };
  if (patterns.length && PULL_REQUEST_EVENTS.includes(event)) {
    const { pull_request: pr } = JSON.parse(readFileSync(env.GITHUB_EVENT_PATH, "utf8"));
    compared = await compareFiles({
      api: env.GITHUB_API_URL,
      repository: env.GITHUB_REPOSITORY,
      base: pr.base.sha,
      head: pr.head.sha,
      token: env.TOKEN,
    });
  }
  const { changed, reason } = decide({ event, patterns, ...compared });
  console.log(`::notice title=changed-paths::changed=${changed} (${reason})`);
  appendFileSync(env.GITHUB_OUTPUT, `changed=${changed}\n`);
};

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  main().catch((error) => {
    // Failing the step fails the job, so a broken guard can never report a skipped success.
    console.log(`::error title=changed-paths::${error.message}`);
    process.exit(1);
  });
}
